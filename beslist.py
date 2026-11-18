"""Beslist.nl helpers: read the offers of one product from its Beslist product page, and pick the
cheapest one that can actually be delivered.

Pure functions only (no network, no Streamlit). The page shape was checked against a live
Beslist product page on 2026-11-18 (EAN 3474637283414, 12 offers): the page embeds one JSON-LD
"Product" whose "offers" is an AggregateOffer listing every shop, while the visible page only
shows three of them. Each offer carries availability, price, priceCurrency, a relative Beslist
redirect url, seller.name, and shippingDetails with shippingRate.value and
deliveryTime.description.

Two traps seen on that page, both handled here:

- An offer can say availability InStock while its delivery text is "Niet leverbaar". Such an
  offer is not deliverable and is never chosen as best.
- The cheapest sticker price is not always the cheapest total: shipping decides (Celini was
  cheapest by price, Flaconi with free shipping was cheapest by total).

A wrong match is worse than no match, so a page with more than one product that has offers
returns no offers instead of a mix.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from ean import normalize_gtin

BASE_URL = "https://www.beslist.nl"

# --------------------------------------------------------------------------- URLs

# /p/<slug>/<category id>/<ean>/ was observed on a live EAN search page on 2026-11-18.
_PRODUCT_PATH_RE = re.compile(r"^/p/[^/?#]+/\d+/(\d{8,14})/?$")
_BESLIST_HOSTS = {"beslist.nl", "www.beslist.nl"}


def search_url(ean):
    """The Beslist search page for a barcode, or None when the barcode is not valid."""
    gtin = normalize_gtin(ean)
    return f"{BASE_URL}/products/r/{gtin}/" if gtin else None


class _LinkScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href.strip())


def find_product_url(search_html, ean):
    """Link to the Beslist product page for exactly this barcode, from an EAN search page.

    Only a link whose path ends in the same (normalised) barcode counts, so a search page that
    also lists other products cannot hand back the wrong one. Returns an absolute URL or None.
    """
    wanted = normalize_gtin(ean)
    if not wanted or not search_html:
        return None
    scan = _LinkScan()
    try:
        scan.feed(search_html)
        scan.close()
    except Exception:  # noqa: BLE001 - malformed markup must never crash a price lookup
        pass
    for href in scan.hrefs:
        parts = urlparse(urljoin(BASE_URL + "/", href))
        if parts.hostname not in _BESLIST_HOSTS:
            continue
        match = _PRODUCT_PATH_RE.match(parts.path)
        if match and normalize_gtin(match.group(1)) == wanted:
            return f"{BASE_URL}{parts.path}"
    return None


# --------------------------------------------------------------------------- offers


@dataclass(frozen=True)
class Offer:
    shop: str
    price: float  # EUR, item price
    shipping: float | None  # EUR; None when the page does not say
    delivery: str | None  # delivery text as shown, e.g. "1-2 dagen"
    deliverable: bool  # in stock AND not marked as undeliverable
    url: str | None  # link to the shop's page for this product, when the source gives one
    title: str | None = None  # the product as this shop lists it, when the source says
    details: tuple[str, ...] = ()  # short facts for the list: rating, stock, remarks

    @property
    def total(self):
        """Item price plus shipping (shipping counted as 0 when unknown, see shipping_known)."""
        return round(self.price + (self.shipping or 0.0), 2)

    @property
    def shipping_known(self):
        return self.shipping is not None


_AVAILABLE = {"InStock", "LimitedAvailability", "OnlineOnly"}
# Only "Niet leverbaar" was seen live; the other phrases are guesses in the safe direction
# (treat as undeliverable).
_UNDELIVERABLE_RE = re.compile(
    r"niet\s+(?:meer\s+)?(?:leverbaar|beschikbaar|op\s+voorraad)|uitverkocht|out\s+of\s+stock",
    re.IGNORECASE,
)


def is_undeliverable_text(text):
    """True when a delivery/stock text says the item cannot be had ("Niet leverbaar", ...)."""
    return bool(text) and _UNDELIVERABLE_RE.search(str(text)) is not None


class _LdScan(HTMLParser):
    """Collects the text of every <script type="application/ld+json"> block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and "ld+json" in (dict(attrs).get("type") or "").lower():
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._buf is not None:
            self.blocks.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


# JSON-LD keys that point at OTHER products; their offers must not be taken as this page's.
_RELATION_KEYS = {
    "isSimilarTo",
    "isRelatedTo",
    "isAccessoryOrSparePartFor",
    "isConsumableFor",
    "itemListElement",
}


def _types(node):
    value = node.get("@type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _number(value):
    """A finite number from a JSON number or a plain numeric string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _products_with_offers(blocks):
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "Product" in _types(node) and node.get("offers"):
                found.append(node)
                return
            for key, value in node.items():
                if key not in _RELATION_KEYS and isinstance(value, (dict, list)):
                    walk(value)

    for block in blocks:
        try:
            walk(json.loads(block))
        except ValueError:
            continue
    return found


def _offer_nodes(offers):
    """Flatten an AggregateOffer / list of Offers / single Offer into plain Offer dicts."""
    if isinstance(offers, list):
        for item in offers:
            yield from _offer_nodes(item)
    elif isinstance(offers, dict):
        if isinstance(offers.get("offers"), (list, dict)):
            yield from _offer_nodes(offers["offers"])
        else:
            yield offers


def _shipping(details):
    """(shipping cost in EUR or None, delivery text or None) from a shippingDetails value.

    A list of several entries is only trusted when they agree; otherwise the cost is unknown.
    """
    entries = details if isinstance(details, list) else [details]
    costs, texts = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rate = entry.get("shippingRate")
        if isinstance(rate, dict) and str(rate.get("currency", "EUR")).upper() == "EUR":
            cost = _number(rate.get("value"))
            if cost is not None and cost >= 0:
                costs.append(round(cost, 2))
        delivery = entry.get("deliveryTime")
        text = delivery.get("description") if isinstance(delivery, dict) else None
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    cost = costs[0] if costs and all(c == costs[0] for c in costs) else None
    text = texts[0] if texts and all(t == texts[0] for t in texts) else None
    return cost, text


def _to_offer(node):
    price = _number(node.get("price"))
    if price is None or price <= 0:
        return None
    if str(node.get("priceCurrency", "EUR")).upper() != "EUR":
        return None
    seller = node.get("seller")
    shop = seller.get("name") if isinstance(seller, dict) else seller
    if not isinstance(shop, str) or not shop.strip():
        return None
    shipping, delivery = _shipping(node.get("shippingDetails"))
    availability = str(node.get("availability") or "").rstrip("/").rsplit("/", 1)[-1]
    deliverable = availability in _AVAILABLE and not is_undeliverable_text(delivery)
    link = node.get("url")
    url = urljoin(BASE_URL + "/", link) if isinstance(link, str) and link.strip() else None
    return Offer(
        shop=shop.strip(),
        price=round(price, 2),
        shipping=shipping,
        delivery=delivery,
        deliverable=deliverable,
        url=url,
    )


def _product_node(html):
    """The single Product (with offers) in the page's JSON-LD, or None when absent/ambiguous."""
    if not html:
        return None
    scan = _LdScan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:  # noqa: BLE001 - malformed markup must never crash a price lookup
        pass
    products = _products_with_offers(scan.blocks)
    return products[0] if len(products) == 1 else None


def parse_offers(html):
    """All offers on a Beslist product page, in page order. Empty list when nothing reliable.

    Offers without a usable price, shop name or EUR currency are dropped. An offer whose
    availability is missing or not an in-stock value is kept but marked not deliverable.
    """
    product = _product_node(html)
    if product is None:
        return []
    offers = (_to_offer(node) for node in _offer_nodes(product["offers"]))
    return [offer for offer in offers if offer is not None]


_GTIN_KEYS = ("gtin13", "gtin", "gtin14", "gtin12", "gtin8")


def product_ean(html):
    """The validated barcode the Beslist page itself states for its product, or None.

    Use it to check that the page really is the product you asked for: the live product page
    carries "gtin13" in its JSON-LD next to the offers.
    """
    product = _product_node(html)
    if product is None:
        return None
    for key in _GTIN_KEYS:
        value = product.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            gtin = normalize_gtin(value)
            if gtin:
                return gtin
    return None


def rank_offers(offers):
    """Deliverable offers, cheapest total first; known shipping beats unknown on equal totals.

    Offers that tie keep their page order.
    """
    usable = [offer for offer in offers if offer.deliverable]
    return sorted(usable, key=lambda offer: (offer.total, not offer.shipping_known))


def best_offer(offers):
    """The cheapest deliverable offer by total (price + shipping), or None."""
    ranked = rank_offers(offers)
    return ranked[0] if ranked else None
