"""The whole lookup chain: bol link or EAN in, recommendation out.

Pure: the network is passed in, so this module never touches the network or Streamlit and is
tested with fake fetches. Steps, as seen by the owner:

1. A barcode is used as is. A bol product link is fetched to read its EAN. An Amazon link is not
   fetched (Amazon blocks automated access); the owner is asked for the EAN instead.
2. An *offers source* turns the EAN into a list of offers. Two exist: Beslist (`beslist_source`,
   pages fetched with `fetch`) and Google Shopping through SerpApi (`serp.serp_source`). A source
   returns `Found` or `Failure`.
3. `choice.recommend` picks the offer to buy.

Every failure ends in a status and a Dutch message that says what to do. A block (403, 429, a
bot-check page...) is reported and the chain stops: it never retries under another identity.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from beslist import Offer, find_product_url, parse_offers, product_ean, search_url
from choice import TOLERANCE, Recommendation, recommend
from ean import extract_ids, normalize_gtin, parse_product_url

PAUSE_SECONDS = 2.0  # between the two requests to Beslist
SAMPLE_EAN = "3474637283414"  # a product known to be on Beslist, for connection tests
# 202 is what AWS WAF sends for a bot check ("challenge"): a small page instead of the real one.
_BLOCK_STATUSES = {202, 401, 403, 429, 451}

# Statuses: "ok" and "unavailable" mean the chain worked; the rest say what went wrong.
OK = "ok"
UNAVAILABLE = "unavailable"  # offers found, but none can be delivered
INVALID_INPUT = "invalid_input"
NEEDS_EAN = "needs_ean"  # the owner has to paste the EAN himself
BLOCKED = "blocked"
SOURCE_ERROR = "source_error"  # a paid API said no (bad key, no credits, ...)
NOT_FOUND = "not_found"
NO_OFFERS = "no_offers"
MISMATCH = "mismatch"


@dataclass(frozen=True)
class Found:
    """What an offers source returns on success."""

    offers: tuple[Offer, ...]
    source: str  # shown to the owner, e.g. "Beslist" or "Google Shopping"
    product_url: str | None = None
    page_ean: str | None = None  # barcode the source itself states, when it states one
    read_at: datetime | None = None
    title: str | None = None  # product title as the source calls it
    notes: tuple[str, ...] = ()  # warnings that do not stop the lookup


@dataclass(frozen=True)
class Failure:
    """What an offers source returns when it could not deliver offers."""

    status: str
    message: str
    source: str = ""
    product_url: str | None = None


@dataclass(frozen=True)
class LookupResult:
    status: str
    message: str  # Dutch, shown to the owner
    steps: tuple[str, ...] = ()  # what happened, one line per request
    ean: str | None = None
    child_gtins: tuple[str, ...] = ()  # barcodes of items inside a multipack (from the bol page)
    source: str = ""  # which offers source answered
    product_title: str | None = None
    product_url: str | None = None
    page_ean: str | None = None  # barcode the source itself states
    notes: tuple[str, ...] = ()
    offers: tuple[Offer, ...] = ()
    recommendation: Recommendation | None = None
    read_at: datetime | None = None  # when the offers were read

    @property
    def worked(self):
        return self.status in (OK, UNAVAILABLE)


def _describe(label, status, text):
    """One log line for a request: what was asked, what came back."""
    if status is None:
        return f"{label}: geen antwoord"
    return f"{label}: HTTP {status}, {len(text) if text else 0} tekens"


def _failure(site, status, text):
    if status is None:
        return f"Geen antwoord van {site} ({text})."
    if status == 202:
        return f"{site} toont een controlepagina (HTTP 202) in plaats van de pagina: bot-controle."
    return f"{site} gaf HTTP {status}."


def _bol_page_url(text):
    """The bol page to fetch for an already recognised bol link (scheme, host and path only)."""
    text = text if "://" in text else "https://" + text
    parts = urlparse(text)
    return f"https://{parts.hostname}{parts.path}"


def _ean_from_input(text, fetch, steps):
    """(ean, child_gtins, early_result): early_result is set when there is no EAN to continue with."""
    cleaned = (text or "").strip()

    def stop(status, message):
        return None, (), LookupResult(status, message, steps=tuple(steps))

    if not cleaned:
        return stop(INVALID_INPUT, "Plak een bol-productlink of een EAN.")
    if re.fullmatch(r"[\d\s\-]+", cleaned):
        gtin = normalize_gtin(cleaned)
        if gtin:
            return gtin, (), None
        return stop(INVALID_INPUT, "Dit is geen geldige EAN (lengte of controlecijfer klopt niet).")

    ref = parse_product_url(cleaned)
    if ref is None:
        return stop(
            INVALID_INPUT,
            "Plak een bol-productlink of een EAN. Andere winkels worden nog niet herkend.",
        )
    if ref.shop == "amazon":
        return stop(
            NEEDS_EAN,
            "Amazon laat automatisch ophalen niet toe. Plak de EAN: die staat op de Amazon-pagina "
            "onder Productinformatie bij 'Global Trade Identification Number'.",
        )

    status, html = fetch(_bol_page_url(cleaned))
    steps.append(_describe("bol-pagina", status, html))
    if status != 200 or not html:
        return stop(
            NEEDS_EAN,
            f"{_failure('bol.com', status, html)} Plak de EAN zelf: die staat op de bolpagina "
            "bij Specificaties.",
        )
    ids = extract_ids(html)
    if not ids.ean:
        return stop(
            NEEDS_EAN,
            "Geen eenduidige EAN op de bolpagina gevonden. Plak de EAN zelf.",
        )
    steps.append(f"EAN van de bolpagina: {ids.ean}")
    return ids.ean, ids.child_gtins, None


def _beslist_failure(status, text):
    message = _failure("Beslist", status, text)
    if status in _BLOCK_STATUSES:
        return f"{message} Beslist weigert dit verzoek; niet omzeilen."
    if status is None:
        return message
    return f"{message} Probeer het later opnieuw."


def beslist_source(fetch, sleep=time.sleep, now=datetime.now):
    """Offers source: Beslist search page -> product page for exactly this EAN -> offers."""

    def source(ean, steps):
        name = "Beslist"
        status, html = fetch(search_url(ean))
        steps.append(_describe("Beslist zoekpagina", status, html))
        if status != 200 or not html:
            return Failure(BLOCKED, _beslist_failure(status, html), name)
        product_url = find_product_url(html, ean)
        if not product_url:
            return Failure(
                NOT_FOUND, "Geen productpagina voor deze EAN gevonden op de zoekpagina.", name
            )
        steps.append("Beslist productpagina gevonden voor exact deze EAN")

        sleep(PAUSE_SECONDS)
        status, html = fetch(product_url)
        read_at = now()
        steps.append(_describe("Beslist productpagina", status, html))
        if status != 200 or not html:
            return Failure(BLOCKED, _beslist_failure(status, html), name, product_url)
        offers = tuple(parse_offers(html))
        page_ean = product_ean(html)
        steps.append(f"{len(offers)} aanbiedingen gelezen; EAN op de pagina: {page_ean or 'onbekend'}")
        if not offers:
            return Failure(
                NO_OFFERS,
                "Geen aanbiedingen in de pagina (andere opmaak, of een blokkadepagina).",
                name,
                product_url,
            )
        if page_ean and page_ean != ean:
            return Failure(
                MISMATCH,
                f"De pagina hoort bij een andere EAN ({page_ean}); resultaat niet gebruiken.",
                name,
                product_url,
            )
        return Found(offers, name, product_url=product_url, page_ean=page_ean, read_at=read_at)

    return source


def lookup(text, fetch, sleep=time.sleep, now=datetime.now, tolerance=TOLERANCE, source=None):
    """Run the chain for a bol link or a barcode. Always returns a LookupResult.

    `fetch` reads the bol page (and the Beslist pages when no other `source` is given). A
    `source(ean, steps)` function returns `Found` or `Failure`; it defaults to Beslist.
    """
    steps = []
    ean, child_gtins, early = _ean_from_input(text, fetch, steps)
    if early is not None:
        return early
    source = source or beslist_source(fetch, sleep, now)

    def result(status, message, **fields):
        fields.setdefault("ean", ean)
        fields.setdefault("child_gtins", child_gtins)
        return LookupResult(status, message, steps=tuple(steps), **fields)

    found = source(ean, steps)
    if isinstance(found, Failure):
        return result(found.status, found.message, source=found.source, product_url=found.product_url)

    fields = {
        "source": found.source,
        "product_url": found.product_url,
        "page_ean": found.page_ean,
        "read_at": found.read_at,
        "product_title": found.title,
        "notes": found.notes,
    }
    if not found.offers:
        return result(NO_OFFERS, "Geen aanbiedingen gevonden.", **fields)

    advice = recommend(found.offers, tolerance)
    fields.update(offers=tuple(found.offers), recommendation=advice)
    if advice is None:
        return result(UNAVAILABLE, "Geen leverbaar aanbod.", **fields)
    return result(OK, "Aanbiedingen gelezen.", **fields)
