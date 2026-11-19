"""Google Shopping through SerpApi as an offers source: EAN in, candidate products and offers out.

Pure: the SerpApi call is passed in as `fetch_json(params) -> (data, error)`, which also holds
the API key, so nothing here ever sees (or can leak) the key.

Two calls, field names checked against a real answer for an EAN on 2026-11-19:

1. `engine=google_shopping`, `q=<EAN>`: `shopping_results[]` with title, product_id, source (the
   shop), extracted_price, thumbnail, immersive_product_page_token. No direct shop link.
2. `engine=google_immersive_product` with that page token and `more_stores=true`:
   `product_results.stores[]` with name, link, title, extracted_price, shipping_extracted, total,
   rating, tag, details_and_offers. This is the list of shops for ONE product.

Google Shopping does not match a barcode exactly: the real answer for an EAN held 40 loosely
related shampoos, and the product asked for was number 10. So step 1 groups the results into
candidate products (`candidates`), ranks them against the owner's product name when he gave one
(`rank`), and either picks one when that is unambiguous (`auto_pick`) or hands the candidates
back as a `Choose` so the owner taps the right one. Only then step 2 fetches the shops.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime

from beslist import Offer, is_undeliverable_text
from lookup import NEEDS_EAN, NO_EAN_ON_AMAZON, NOT_FOUND, NO_OFFERS, SOURCE_ERROR, Choose, Failure, Found

SOURCE_NAME = "Google Shopping"
GOOGLE = "https://www.google.com"
CHECK_TITLE_NOTE = (
    "Google Shopping geeft de EAN niet terug: controleer of de titel bij je product past."
)
SEARCH_COST = 1  # one SerpApi search per step
MAX_QUERY_CHARS = 120  # of the owner's product name, as sent to Google
NOT_FOUND_BY_EAN = (
    "Google Shopping vond niets voor deze EAN. Vul bij \u201cProductnaam\u201d de naam van het product in "
    "(merk en productnaam) en zoek opnieuw: dan zoekt Google op de naam."
)
NOT_FOUND_BY_NAME = "Google Shopping vond niets voor \u201c{}\u201d. Probeer een andere naam, bijvoorbeeld merk en productnaam."
MAX_CANDIDATES = 12  # shown to the owner
MIN_AUTO_WORDS = 4  # a shorter name ("segafredo bonen") names a brand and a kind, not one product
MIN_AUTO_COVERAGE = 1.0  # share of the product-name words a title needs for a pick: all of them
MIN_AUTO_MARGIN = 0.25  # how far the best title must lead the second one

_DELIVERY_WORDS_RE = re.compile(r"levering|bezorg|verzend|delivery|shipping", re.IGNORECASE)
_CONDITIONAL_RE = re.compile(
    r"\b(?:vanaf|boven|minimaal|from|over|orders?)\b|bij\s+(?:een\s+)?bestelling", re.IGNORECASE
)
_AMOUNT_RE = re.compile(
    r"(?:€|eur)\s*(\d[\d.,]*)|(\d[\d.,]*)\s*(?:€|eur)", re.IGNORECASE
)
_PROMO_RES = [
    re.compile(r"\b\d+\s*\+\s*\d+\s*gratis\b", re.IGNORECASE),  # 1+1 gratis, 2+3 gratis
    re.compile(r"\b\d+\s*voor\s*\d+(?:[.,]\d+)?\b", re.IGNORECASE),  # 2 voor 10.00
    re.compile(r"\bbonus\b", re.IGNORECASE),  # Bonus 2e gratis
    re.compile(r"\b\d+\s*%\s*korting\b", re.IGNORECASE),  # 47% korting
    re.compile(r"\b\d+e\s+(?:halve\s+prijs|gratis)\b", re.IGNORECASE),  # 2e halve prijs
]


# --------------------------------------------------------------------------- numbers and text


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def money(text):
    """'€ 1.234,56' -> 1234.56, '4,95' -> 4.95; None when there is no amount."""
    cleaned = re.sub(r"[^\d.,]", "", str(text or ""))
    if not cleaned or not re.search(r"\d", cleaned):
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):  # 1.234 is a thousand separator
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_shipping(text):
    """Shipping cost in EUR from a delivery text; None when it cannot be told.

    "Gratis verzending" -> 0.0, "+ € 4,95 verzending" -> 4.95. Anything conditional ("gratis
    vanaf € 50") or without a euro amount ("Levering 1-2 dagen") is unknown, not guessed.
    """
    if not text:
        return None
    text = str(text)
    if _CONDITIONAL_RE.search(text):
        return None
    if re.search(r"\b(?:gratis|free)\b", text, re.IGNORECASE):
        return 0.0
    match = _AMOUNT_RE.search(text)
    if match and (_DELIVERY_WORDS_RE.search(text) or "+" in text):
        return money(match.group(1) or match.group(2))
    return None


def _price(entry):
    """Price in EUR of a result, or None (also for dollar or pound prices)."""
    shown = str(entry.get("price") or "")
    if "$" in shown or "£" in shown:
        return None
    value = _finite(entry.get("extracted_price"))
    if value is None:
        value = money(shown)
    return round(value, 2) if value is not None and value > 0 else None


def _https(link):
    return link if isinstance(link, str) and link.startswith("https://") else None


def _link(value):
    """A web link for the owner to open: https, or one of Google's own relative redirects."""
    if not isinstance(value, str):
        return None
    if value.startswith("https://"):
        return value
    if value.startswith(("/goto?", "/url?", "/shopping/")):
        return GOOGLE + value
    return None


def _texts(value):
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return [str(value)] if value else []


def _words(text):
    """Lower-case words without accents or punctuation: 'Kérastase Hydra-Glaze' -> 3 words."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", plain.lower())


def split_promo(title):
    """('1+1 gratis', "L'Oréal ...") for "1+1 gratis: L'Oréal ..."; (None, title) otherwise."""
    title = str(title or "")
    match = re.match(r"^\s*([^:|]{2,45}?)\s*[:|]\s*(.+)$", title)
    if match and any(rx.search(match.group(1)) for rx in _PROMO_RES):
        return match.group(1).strip(), match.group(2).strip()
    return None, title


# --------------------------------------------------------------------------- offers


MAX_DETAILS = 6
MAX_BOOK_DETAILS = 3  # of the shop's own conditions (stock, delivery, returns)
MAX_DETAIL_CHARS = 160


def _clip(text, limit=MAX_DETAIL_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _details(entry, promo=None, skip=()):
    """Short facts about an offer for the list, deduplicated: promo, rating, tag, conditions."""
    facts = []
    if promo:
        facts.append(f"Actie: {promo}")
    rating = _finite(entry.get("rating"))
    if rating is not None and 0 < rating <= 5:
        text = "★ " + f"{rating:.1f}".replace(".", ",")
        reviews = _finite(entry.get("reviews"))
        if reviews is not None and reviews >= 1:
            text += f" ({int(reviews)} reviews)"
        facts.append(text)
    if entry.get("tag"):
        facts.append(str(entry["tag"]))
    facts.extend(_texts(entry.get("details_and_offers"))[:MAX_BOOK_DETAILS])
    facts.extend(_texts(entry.get("extensions")))
    if entry.get("snippet"):
        facts.append(str(entry["snippet"]))
    seen = {str(text).strip().lower() for text in skip if text}
    result = []
    for fact in facts:
        fact = _clip(fact)
        if fact and fact.lower() not in seen:
            seen.add(fact.lower())
            result.append(fact)
    return tuple(result[:MAX_DETAILS])


def _title_and_promo(entry):
    if not entry.get("title"):
        return None, None
    promo, title = split_promo(entry["title"])
    return _clip(title, 200), promo


def offer_from_item(item):
    """An Offer from one `shopping_results` entry, or None."""
    shop = str(item.get("source") or "").strip()
    price = _price(item)
    if not shop or price is None:
        return None
    delivery = str(item.get("delivery") or "").strip() or None
    extensions = _texts(item.get("extensions"))
    deliverable = not any(is_undeliverable_text(text) for text in [delivery, *extensions])
    title, promo = _title_and_promo(item)
    return Offer(
        shop,
        price,
        parse_shipping(delivery),
        delivery,
        deliverable,
        _link(item.get("link")),
        title,
        _details(item, promo, skip=[delivery]),
    )


def offer_from_store(store):
    """An Offer from one immersive-product `stores` entry, or None."""
    shop = str(store.get("name") or "").strip()
    price = _price(store)
    if not shop or price is None:
        return None
    shipping = _finite(store.get("shipping_extracted"))
    if shipping is None or shipping < 0:
        shipping = parse_shipping(store.get("shipping"))
    conditions = _texts(store.get("details_and_offers")) + _texts(store.get("extensions"))
    delivery = next((text for text in _texts(store.get("extensions")) if _DELIVERY_WORDS_RE.search(text)), None)
    deliverable = not any(is_undeliverable_text(text) for text in conditions)
    title, promo = _title_and_promo(store)
    return Offer(
        shop,
        price,
        None if shipping is None else round(shipping, 2),
        delivery,
        deliverable,
        _link(store.get("link")),
        title,
        _details(store, promo, skip=[delivery]),
    )


def _add(offers, offer):
    """Keep one offer per shop: the cheapest total."""
    if offer is None:
        return
    key = offer.shop.lower()
    if key not in offers or offer.total < offers[key].total:
        offers[key] = offer


def _fill_from_items(offers, items):
    """A store offer without title, link or details borrows them from the same shop's result."""
    by_shop = {}
    for item in items:
        offer = offer_from_item(item)
        if offer is not None:
            by_shop.setdefault(offer.shop.lower(), offer)
    for key, offer in list(offers.items()):
        other = by_shop.get(key)
        if other is None:
            continue
        offers[key] = replace(
            offer,
            url=offer.url or other.url,
            title=offer.title or other.title,
            details=offer.details or other.details,
        )


# --------------------------------------------------------------------------- candidate products


@dataclass(frozen=True)
class Candidate:
    """One product Google Shopping found for the query: what the owner picks from."""

    key: str  # product_id, or the normalised title when there is none
    title: str
    token: str | None  # immersive_product_page_token: what step 2 needs
    thumbnail: str | None
    shops: tuple[str, ...]  # shops that listed this product in step 1
    price_min: float | None
    price_max: float | None
    product_link: str | None
    position: int  # Google's order (0 = first)
    score: float = 0.0  # how many of the owner's product-name words the title has (0..1)
    items: tuple = ()  # the raw step-1 results of this product


def candidates(items):
    """Group `shopping_results` into products, in Google's order."""
    groups = {}
    for position, item in enumerate(items):
        key = str(item.get("product_id") or "") or " ".join(_words(item.get("title")))
        groups.setdefault(key, []).append((position, item))
    result = []
    for key, members in groups.items():
        first = members[0][1]
        prices = [p for p in (_price(item) for _, item in members) if p is not None]
        shops = []
        for _, item in members:
            shop = str(item.get("source") or "").strip()
            if shop and shop.lower() not in [s.lower() for s in shops]:
                shops.append(shop)
        result.append(
            Candidate(
                key=key,
                title=_clip(first.get("title") or "(zonder titel)", 200),
                token=next(
                    (i["immersive_product_page_token"] for _, i in members if i.get("immersive_product_page_token")),
                    None,
                ),
                thumbnail=next((_https(i.get("thumbnail")) for _, i in members if i.get("thumbnail")), None),
                shops=tuple(shops),
                price_min=min(prices) if prices else None,
                price_max=max(prices) if prices else None,
                product_link=next((_https(i.get("product_link")) for _, i in members if i.get("product_link")), None),
                position=members[0][0],
                items=tuple(item for _, item in members),
            )
        )
    return result


def similarity(hint, title):
    """Share of the words of `hint` that also occur in `title` (0 when there is no hint)."""
    wanted = set(_words(hint))
    if not wanted:
        return 0.0
    return len(wanted & set(_words(title))) / len(wanted)


def rank(cands, hint=None):
    """Best match with the owner's product name first, otherwise Google's own order."""
    scored = [replace(c, score=similarity(hint, c.title)) for c in cands]
    return sorted(scored, key=lambda c: (-c.score, c.position))


def auto_pick(ranked, hint=None):
    """The candidate when there is no real choice: one product, or one that a specific name fits
    word for word while the next one trails clearly. A short or vague name never settles it: a wrong
    silent pick makes every price after it worthless (2 of 2 picks on 3-word names were wrong in the
    keyword Testrun of 2026-11-19)."""
    if len(ranked) == 1:
        return ranked[0]
    if not hint or len(ranked) < 2 or len(_words(hint)) < MIN_AUTO_WORDS:
        return None
    if ranked[0].score >= MIN_AUTO_COVERAGE and ranked[0].score - ranked[1].score >= MIN_AUTO_MARGIN:
        return ranked[0]
    return None


# --------------------------------------------------------------------------- the source


def _finish(candidate, fetch_json, now, guard, steps, check_note, query=None):
    """Step 2: the shops for one product."""
    notes = [CHECK_TITLE_NOTE] if check_note else []
    offers = {}
    if candidate.token:
        reason = guard(SEARCH_COST) if guard else None
        if reason:
            steps.append("Winkellijst: niet opgehaald (budget)")
            return Failure(SOURCE_ERROR, reason, SOURCE_NAME, candidate.product_link, query)
        stores_data, stores_error = fetch_json(
            {"engine": "google_immersive_product", "page_token": candidate.token, "more_stores": "true"}
        )
        if stores_data is None:
            steps.append("Winkellijst: geen antwoord")
            notes.append(
                f"De winkellijst met verzendkosten kon niet worden opgehaald ({stores_error}); "
                "alleen de directe resultaten zijn gebruikt."
            )
        else:
            product = stores_data.get("product_results")
            stores = product.get("stores") if isinstance(product, dict) else None
            stores = [s for s in stores or [] if isinstance(s, dict)]
            steps.append(f"Winkellijst: {len(stores)} winkels")
            for store in stores:
                _add(offers, offer_from_store(store))
    items = [item for item in candidate.items if isinstance(item, dict)]
    _fill_from_items(offers, items)
    stores_seen = set(offers)
    for item in items:
        offer = offer_from_item(item)
        if offer is not None and offer.shop.lower() not in stores_seen:
            _add(offers, offer)

    if not offers:
        return Failure(
            NO_OFFERS,
            "Google Shopping vond het product, maar geen bruikbare prijzen.",
            SOURCE_NAME,
            candidate.product_link,
            query,
        )
    return Found(
        tuple(offers.values()),
        SOURCE_NAME,
        product_url=candidate.product_link,
        read_at=now(),
        title=candidate.title,
        notes=tuple(notes),
        query=query,
    )


def serp_source(fetch_json, now=datetime.now, guard=None, hint=None):
    """Offers source for lookup.lookup: Google Shopping by EAN through SerpApi.

    `guard(cost)` runs before each search: it returns None to go ahead, or a Dutch reason (for
    instance "monthly budget reached") and then nothing is searched. `hint` is the owner's
    product name, if he gave one: it ranks the candidates and can settle the choice. Without an
    EAN (`ean` is None: an Amazon page that shows none) Google is asked for the owner's name, or
    else for `page_name`, the product name read from that page; with neither there is nothing
    to search for and the owner is asked for the EAN.
    """

    name = _clip(hint, MAX_QUERY_CHARS) if hint and hint.strip() else None

    def source(ean, steps, page_name=None):
        rank_hint = hint
        if not name and ean is None:
            if not page_name:
                return Failure(NEEDS_EAN, NO_EAN_ON_AMAZON)
            rank_hint = page_name
        # With the owner's product name Google is asked for that name: Google does not search
        # on the barcode itself (for one real EAN it gave 40 loosely related products, for
        # another nothing at all), so the name is the more reliable question.
        query = name or (_clip(page_name, MAX_QUERY_CHARS) if ean is None else None)
        # step 1 plus step 2 of the product that gets picked: the guard looks at both
        reason = guard(2 * SEARCH_COST) if guard else None
        if reason:
            steps.append("Google Shopping: niet gezocht (budget)")
            return Failure(SOURCE_ERROR, reason, SOURCE_NAME, query=query)
        data, error = fetch_json({"engine": "google_shopping", "q": query or ean, "hl": "nl", "gl": "nl"})
        if data is None:
            steps.append("Google Shopping: geen antwoord")
            return Failure(
                SOURCE_ERROR, f"Google Shopping (SerpApi) gaf geen antwoord: {error}", SOURCE_NAME, query=query
            )
        items = [item for item in data.get("shopping_results") or [] if isinstance(item, dict)]
        if not items:
            steps.append("Google Shopping: 0 resultaten")
            if error and "any results" not in str(error).lower():
                return Failure(SOURCE_ERROR, f"SerpApi meldt: {error}", SOURCE_NAME, query=query)
            return Failure(NOT_FOUND, NOT_FOUND_BY_NAME.format(query) if query else NOT_FOUND_BY_EAN, SOURCE_NAME, query=query)

        ranked = rank(candidates(items), rank_hint)
        steps.append(f"Google Shopping: {len(items)} resultaten, {len(ranked)} producten")
        picked = auto_pick(ranked, rank_hint)
        if picked is not None:
            if len(ranked) > 1:
                steps.append("Product gekozen op je productnaam")
            # the guard above already looked at both steps: no second look at the account
            return _finish(picked, fetch_json, now, None, steps, check_note=True, query=query)

        metadata = data.get("search_metadata")
        see_all = _https(metadata.get("google_shopping_url")) if isinstance(metadata, dict) else None
        return Choose(
            tuple(ranked[:MAX_CANDIDATES]),
            SOURCE_NAME,
            lambda candidate, steps2: _finish(
                candidate, fetch_json, now, guard, steps2, check_note=False, query=query
            ),
            product_url=see_all,
            query=query,
        )

    return source
