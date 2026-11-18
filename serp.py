"""Google Shopping through SerpApi as an offers source: EAN in, offers out.

Pure: the SerpApi call is passed in as `fetch_json(params) -> (data, error)`, which also holds
the API key, so nothing here ever sees (or can leak) the key.

Two calls per product, both from SerpApi's documentation (no live Dutch response has been seen
yet, so the field names are the documented ones and the parsing is deliberately defensive):

1. `engine=google_shopping`, `q=<EAN>`: `shopping_results[]` with title, product_id, source (the
   shop), extracted_price, delivery, link, immersive_product_page_token.
2. `engine=google_immersive_product` with that page token and `more_stores=true`:
   `product_results.stores[]` with name, extracted_price, shipping / shipping_extracted, link,
   extensions. This is the list of shops with shipping; it is used when a token is present.

Google Shopping does not tell which barcode a result belongs to, so a search for an EAN can in
principle return a similar product. The owner is shown the product title and a note to check
it, and when results belong to several different products only the largest group is used.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from beslist import Offer, is_undeliverable_text
from lookup import NOT_FOUND, NO_OFFERS, SOURCE_ERROR, Failure, Found

SOURCE_NAME = "Google Shopping"
CHECK_TITLE_NOTE = (
    "Google Shopping geeft de EAN niet terug: controleer of de titel bij je product past."
)
_DELIVERY_WORDS_RE = re.compile(r"levering|bezorg|verzend|delivery|shipping", re.IGNORECASE)
_CONDITIONAL_RE = re.compile(
    r"\b(?:vanaf|boven|minimaal|from|over|orders?)\b|bij\s+(?:een\s+)?bestelling", re.IGNORECASE
)
_AMOUNT_RE = re.compile(
    r"(?:€|eur)\s*(\d[\d.,]*)|(\d[\d.,]*)\s*(?:€|eur)", re.IGNORECASE
)


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


def _texts(value):
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return [str(value)] if value else []


# --------------------------------------------------------------------------- offers


def offer_from_item(item):
    """An Offer from one `shopping_results` entry, or None."""
    shop = str(item.get("source") or "").strip()
    price = _price(item)
    if not shop or price is None:
        return None
    delivery = str(item.get("delivery") or "").strip() or None
    extensions = _texts(item.get("extensions"))
    deliverable = not any(is_undeliverable_text(text) for text in [delivery, *extensions])
    return Offer(shop, price, parse_shipping(delivery), delivery, deliverable, _https(item.get("link")))


def offer_from_store(store):
    """An Offer from one immersive-product `stores` entry, or None."""
    shop = str(store.get("name") or "").strip()
    price = _price(store)
    if not shop or price is None:
        return None
    shipping = _finite(store.get("shipping_extracted"))
    if shipping is None or shipping < 0:
        shipping = parse_shipping(store.get("shipping"))
    extensions = _texts(store.get("extensions"))
    delivery = next((text for text in extensions if _DELIVERY_WORDS_RE.search(text)), None)
    deliverable = not any(is_undeliverable_text(text) for text in extensions)
    return Offer(
        shop,
        price,
        None if shipping is None else round(shipping, 2),
        delivery,
        deliverable,
        _https(store.get("link")),
    )


def _normalise_title(title):
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()


def _main_group(items):
    """The results that belong to one product: the largest group by product_id (or title)."""
    groups = {}
    for item in items:
        key = str(item.get("product_id") or "") or _normalise_title(item.get("title"))
        groups.setdefault(key, []).append(item)
    best = max(groups.values(), key=len)  # max keeps the first of equal sizes
    return best, len(groups)


def _add(offers, offer):
    """Keep one offer per shop: the cheapest total."""
    if offer is None:
        return
    key = offer.shop.lower()
    if key not in offers or offer.total < offers[key].total:
        offers[key] = offer


# --------------------------------------------------------------------------- the source


def serp_source(fetch_json, now=datetime.now):
    """Offers source for lookup.lookup: Google Shopping by EAN through SerpApi."""

    def source(ean, steps):
        data, error = fetch_json({"engine": "google_shopping", "q": ean, "hl": "nl", "gl": "nl"})
        if data is None:
            steps.append("Google Shopping: geen antwoord")
            return Failure(
                SOURCE_ERROR, f"Google Shopping (SerpApi) gaf geen antwoord: {error}", SOURCE_NAME
            )
        items = [item for item in data.get("shopping_results") or [] if isinstance(item, dict)]
        steps.append(f"Google Shopping: {len(items)} resultaten")
        if not items:
            if error and "any results" not in str(error).lower():
                return Failure(SOURCE_ERROR, f"SerpApi meldt: {error}", SOURCE_NAME)
            return Failure(NOT_FOUND, "Google Shopping vond niets voor deze EAN.", SOURCE_NAME)

        chosen, group_count = _main_group(items)
        notes = [CHECK_TITLE_NOTE]
        if group_count > 1:
            notes.append(
                f"Google vond {group_count} verschillende producten voor deze EAN; "
                "alleen het product met de meeste resultaten is gebruikt."
            )
        title = next((str(i["title"]) for i in chosen if i.get("title")), None)
        product_link = next((_https(i.get("product_link")) for i in chosen if i.get("product_link")), None)

        offers = {}
        token = next(
            (i["immersive_product_page_token"] for i in chosen if i.get("immersive_product_page_token")),
            None,
        )
        if token:
            stores_data, stores_error = fetch_json(
                {"engine": "google_immersive_product", "page_token": token, "more_stores": "true"}
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
        stores_seen = set(offers)
        for item in chosen:
            offer = offer_from_item(item)
            if offer is not None and offer.shop.lower() not in stores_seen:
                _add(offers, offer)

        if not offers:
            return Failure(
                NO_OFFERS,
                "Google Shopping vond het product, maar geen bruikbare prijzen.",
                SOURCE_NAME,
                product_link,
            )
        return Found(
            tuple(offers.values()),
            SOURCE_NAME,
            product_url=product_link,
            read_at=now(),
            title=title,
            notes=tuple(notes),
        )

    return source
