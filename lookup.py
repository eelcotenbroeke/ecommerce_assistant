"""The whole lookup chain: bol link or EAN in, recommendation out.

Pure: the network is passed in, so this module never touches the network or Streamlit and is
tested with fake fetches. Steps, as seen by the owner:

1. A barcode is used as is. A bol or Amazon product link is fetched (without its tracking
   parameters) to read its EAN; when the shop refuses (a block is never worked around) the owner
   is asked for the EAN instead. An Amazon page that is readable but shows no EAN gives its title
   as a product name instead: the offers source then searches on that name (`source(None, steps,
   name=...)`).
2. An *offers source* turns the EAN into a list of offers. Two exist: Beslist (`beslist_source`,
   pages fetched with `fetch`) and Google Shopping through SerpApi (`serp.serp_source`). A source
   returns `Found`, `Failure` or `Choose`: Google Shopping cannot match a barcode exactly, so it
   hands back candidate products and the owner picks one (`finish_choice` then fetches the
   shops for that product).
3. `choice.recommend` picks the offer to buy.

Every failure ends in a status and a Dutch message that says what to do. A block (403, 429, a
bot-check page...) is reported and the chain stops: it never retries under another identity.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from beslist import Offer, find_product_url, parse_offers, product_ean, search_url
from choice import TOLERANCE, Recommendation, recommend
from ean import extract_ids, normalize_gtin, parse_product_url
from linkname import looks_like_link, name_from_link
from pageprobe import looks_blocked, read_page

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
CHOOSE = "choose"  # the source found several products: the owner picks the one he means


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
    query: str | None = None  # the search text when it was not the barcode (the owner's product name)


@dataclass(frozen=True)
class Failure:
    """What an offers source returns when it could not deliver offers."""

    status: str
    message: str
    source: str = ""
    product_url: str | None = None
    query: str | None = None  # the search text when it was not the barcode


@dataclass(frozen=True)
class Choose:
    """What an offers source returns when it found several products and cannot tell which one.

    `finish(candidate, steps)` fetches the offers for the chosen candidate and returns `Found`
    or `Failure`; it appends to `steps`.
    """

    candidates: tuple
    source: str
    finish: Callable
    notes: tuple[str, ...] = ()
    product_url: str | None = None  # where the owner can see all results at the source
    query: str | None = None  # the search text when it was not the barcode


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
    candidates: tuple = ()  # status CHOOSE: the products to pick from
    pending: Callable | None = None  # status CHOOSE: what `finish_choice` calls
    query: str | None = None  # what the source searched for when that was not the barcode

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


_AMAZON_EAN_HELP = (
    "Plak de EAN zelf (als Amazon die toont, staat hij onder Productinformatie bij "
    "'Global Trade Identification Number')."
)
NO_EAN_ON_AMAZON = (
    "Amazon toont bij dit product geen EAN. Plak de EAN zelf als je die weet, of vul bij "
    "\u201cProductnaam\u201d de naam van het product in."
)
NAME_FROM_AMAZON_NOTE = (
    "Amazon toont bij dit product geen EAN: er is gezocht op de productnaam in plaats van op de EAN. "
    "Controleer of het gevonden product klopt."
)

NAME_FROM_LINK_NOTE = (
    "De winkelpagina is niet gelezen: er is gezocht op de woorden uit de link, niet op een EAN. "
    "Controleer of het gevonden product klopt (type, formaat, aantal)."
)
NO_NAME_IN_LINK = (
    "Uit deze link kan ik geen productnaam halen. Vul bij \u201cProductnaam\u201d de naam van het "
    "product in, of plak een EAN."
)
NAME_NEEDS_GOOGLE = (
    "Zoeken op productnaam kan alleen via Google Shopping (SerpApi): zet SERPAPI_KEY onder "
    "Settings \u2192 Secrets. Of plak een bol-link, een Amazon-link of een EAN."
)
MAX_PAGE_NAME_CHARS = 120
_UNUSABLE_NAME_RE = re.compile(r"^(amazon\b|sorry\b|oeps\b|page not found|pagina niet gevonden)", re.IGNORECASE)


def _amazon_page_url(text, ref):
    """The Amazon page to fetch: the product's own /dp/ page on the host that was pasted."""
    text = text if "://" in text else "https://" + text
    return f"https://{urlparse(text).hostname}/dp/{ref.product_id}"


def _name_from_amazon_page(url, html):
    """The product name for a search, from the page's own title; None when it says too little.

    Amazon titles often continue after " | " with a string of features: only the first part is
    kept, cut at a word to at most MAX_PAGE_NAME_CHARS.
    """
    name = read_page(url, 200, html).name or ""
    name = " ".join(name.split(" | ")[0].split())
    if name.endswith("\u2026"):  # read_page already cut a very long title: drop the half word
        name = name[:-1].rsplit(" ", 1)[0]
    if len(name) > MAX_PAGE_NAME_CHARS:
        name = name[:MAX_PAGE_NAME_CHARS].rsplit(" ", 1)[0]
    if len(name.split()) < 2 or _UNUSABLE_NAME_RE.match(name):
        return None
    return name


def _ean_from_amazon(text, ref, fetch, steps, stop):
    """(ean, child_gtins, early_result, page_name) like `_ean_from_input`.

    A readable page without an EAN is not a failure here: it returns no EAN and, when the title
    is usable, the product name; `lookup` then asks the offers source to search on that name.
    """
    url = _amazon_page_url(text, ref)
    status, html = fetch(url)
    steps.append(_describe("Amazon-pagina", status, html))
    if status is not None and looks_blocked(status, html or ""):
        return stop(
            NEEDS_EAN,
            "Amazon liet de pagina niet lezen (waarschijnlijk een bot-controle). Dit wordt niet "
            f"omzeild. {_AMAZON_EAN_HELP}",
        )
    if status != 200 or not html:
        return stop(NEEDS_EAN, f"{_failure('Amazon', status, html)} {_AMAZON_EAN_HELP}")
    ids = extract_ids(html)
    if not ids.ean:
        name = _name_from_amazon_page(url, html)
        steps.append(
            "Geen EAN op de Amazon-pagina" + (f"; productnaam van de pagina: {name}" if name else "; ook geen bruikbare titel")
        )
        return None, (), None, name
    steps.append(f"EAN van de Amazon-pagina: {ids.ean}")
    return ids.ean, ids.child_gtins, None, None


def name_from_input(text, hint=None):
    """The product name to search on, when the owner gave a name instead of a link or an EAN.

    Nothing in the link field, or words in it (no link, no barcode), means a search on a name:
    the *Productnaam* field wins when it is filled in, else the words typed in the link field.
    A link or a barcode in the link field is never taken for a name: returns None.
    """
    cleaned = (text or "").strip()
    name = (hint or "").strip()
    if not cleaned:
        return name or None
    if re.fullmatch(r"[\d\s\-]+", cleaned) or "://" in cleaned or parse_product_url(cleaned):
        return None
    if not re.search(r"\s", cleaned) and re.search(r"[./]", cleaned):
        return None  # "kruidvat.nl/p/..." or "www.shop.nl": a link, even when the shop is not known
    return name or cleaned


def _ean_from_input(text, fetch, steps):
    """(ean, child_gtins, early_result, page_name).

    early_result is set when there is nothing to continue with. ean is None, with no early_result,
    when the page was read but shows no EAN: page_name is then the product name from the page (or None).
    """
    cleaned = (text or "").strip()

    def stop(status, message):
        return None, (), LookupResult(status, message, steps=tuple(steps)), None

    if not cleaned:
        return stop(INVALID_INPUT, "Plak de link van een product, een EAN of een productnaam.")
    if re.fullmatch(r"[\d\s\-]+", cleaned):
        gtin = normalize_gtin(cleaned)
        if gtin:
            return gtin, (), None, None
        return stop(INVALID_INPUT, "Dit is geen geldige EAN (lengte of controlecijfer klopt niet).")

    ref = parse_product_url(cleaned)
    if ref is None:
        return stop(
            INVALID_INPUT,
            "Plak een bol- of Amazon-productlink of een EAN. Andere winkels worden nog niet herkend.",
        )
    if ref.shop == "amazon":
        return _ean_from_amazon(cleaned, ref, fetch, steps, stop)

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
    return ids.ean, ids.child_gtins, None, None


def _beslist_failure(status, text):
    message = _failure("Beslist", status, text)
    if status in _BLOCK_STATUSES:
        return f"{message} Beslist weigert dit verzoek; niet omzeilen."
    if status is None:
        return message
    return f"{message} Probeer het later opnieuw."


def beslist_source(fetch, sleep=time.sleep, now=datetime.now):
    """Offers source: Beslist search page -> product page for exactly this EAN -> offers."""

    def source(ean, steps, page_name=None):
        if ean is None:  # Beslist is asked for a barcode, not for a name
            return Failure(NEEDS_EAN, NO_EAN_ON_AMAZON)
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


def lookup(text, fetch, sleep=time.sleep, now=datetime.now, tolerance=TOLERANCE, source=None, by_name=False):
    """Run the chain for a bol link, an Amazon link or a barcode. Always returns a LookupResult.

    `fetch` reads the bol or Amazon page (and the Beslist pages when no other `source` is given).
    A `source(ean, steps)` function returns `Found`, `Failure` or `Choose`; it defaults to Beslist.
    When an Amazon page has no EAN the source is called as `source(None, steps, page_name=name)`
    (name: the page's own title, or None): a source that can search on a name does so, the others
    ask for the EAN.

    When the page cannot be used (bol or another shop refuses us, the shop is not known, or an Amazon
    page has neither EAN nor title) and the source can search on a name, the words of the link
    itself are the name (`linkname.name_from_link`, no request to the shop).

    With `by_name` there is no link or EAN: the owner gave a product name and the source (which
    holds that name) searches on it. Only a source that can do that works: Beslist cannot.
    """
    steps = []
    can_search_names = source is not None
    from_link = False
    if by_name:
        if source is None:
            return LookupResult(NEEDS_EAN, NAME_NEEDS_GOOGLE, steps=tuple(steps))
        ean, child_gtins, early, page_name = None, (), None, None
    else:
        ean, child_gtins, early, page_name = _ean_from_input(text, fetch, steps)
        # A page that cannot be used (a shop that refuses us, a shop we do not know, a page
        # without EAN or title): the words of the link itself are still a name to search on.
        if can_search_names and not ean and not page_name and looks_like_link(text):
            if early is None or early.status in (NEEDS_EAN, INVALID_INPUT):
                name = name_from_link(text)
                if name:
                    steps.append(f"Productnaam uit de link: {name}")
                    ean, child_gtins, early, page_name, from_link = None, (), None, name, True
                elif early is not None and early.status == INVALID_INPUT:
                    return LookupResult(INVALID_INPUT, NO_NAME_IN_LINK, steps=tuple(steps))
    if early is not None:
        return early
    source = source or beslist_source(fetch, sleep, now)

    found = source(ean, steps) if ean else source(None, steps, page_name=page_name)
    if not ean and not by_name and isinstance(found, (Found, Choose)):
        note = NAME_FROM_LINK_NOTE if from_link else NAME_FROM_AMAZON_NOTE
        found = replace(found, notes=(note, *found.notes))
    if isinstance(found, Failure):
        return _result(
            steps, ean, child_gtins, found.status, found.message,
            source=found.source, product_url=found.product_url, query=found.query,
        )
    if isinstance(found, Choose):
        return _result(
            steps, ean, child_gtins, CHOOSE, "Kies het product dat je bedoelt.",
            source=found.source, product_url=found.product_url, notes=found.notes, query=found.query,
            candidates=tuple(found.candidates), pending=found.finish,
        )
    return _from_found(found, steps, ean, child_gtins, tolerance)


def _result(steps, ean, child_gtins, status, message, **fields):
    fields.setdefault("ean", ean)
    fields.setdefault("child_gtins", child_gtins)
    return LookupResult(status, message, steps=tuple(steps), **fields)


def _from_found(found, steps, ean, child_gtins, tolerance):
    fields = {
        "source": found.source,
        "product_url": found.product_url,
        "page_ean": found.page_ean,
        "read_at": found.read_at,
        "product_title": found.title,
        "notes": found.notes,
        "query": found.query,
    }
    if not found.offers:
        return _result(steps, ean, child_gtins, NO_OFFERS, "Geen aanbiedingen gevonden.", **fields)

    advice = recommend(found.offers, tolerance)
    fields.update(offers=tuple(found.offers), recommendation=advice)
    if advice is None:
        return _result(steps, ean, child_gtins, UNAVAILABLE, "Geen leverbaar aanbod.", **fields)
    return _result(steps, ean, child_gtins, OK, "Aanbiedingen gelezen.", **fields)


def finish_choice(result, candidate, tolerance=TOLERANCE):
    """The owner picked `candidate` from a CHOOSE result: fetch its offers and recommend."""
    steps = list(result.steps)
    title = getattr(candidate, "title", None)
    steps.append(f"Gekozen: {title if isinstance(title, str) else candidate}")
    found = result.pending(candidate, steps)
    if isinstance(found, Failure):
        return _result(
            steps, result.ean, result.child_gtins, found.status, found.message,
            source=found.source or result.source, product_url=found.product_url,
            query=found.query or result.query,
        )
    return _from_found(found, steps, result.ean, result.child_gtins, tolerance)
