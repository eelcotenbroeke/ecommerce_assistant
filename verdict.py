"""Is there a better deal than the shop the link comes from? Pure: no network, no Streamlit.

The consumer stands on a shop's product page and asks one thing: buy here, or somewhere else?
The offers say what every shop charges; this module finds the shop of the pasted link among them
(the *reference*) and compares its total with the cheapest offer (and the app's recommendation).

- The reference is the cheapest deliverable offer of the link's shop. A shop is recognised by the
  host of the link: "www.kruidvat.nl" is the offer named "Kruidvat.nl" or "Kruidvat", "www.bol.com"
  is "bol.com", "www.amazon.nl" is "Amazon.nl - Retail". Google's own links say nothing and are
  not used. The owner's free delivery from bol and Amazon is applied (`choice.effective_offer`).
- When the shop is not among the offers (or its price is not known), the person can type the total
  he sees on the page (`parse_total`); that price then is the reference.
- The comparison is made on totals, shipping included. A saving under `MIN_SAVING` counts as
  "you are already at the best price": a few cents is not a reason to go elsewhere.

Prices are snapshots (the offers were read at one moment): the screen says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from choice import effective_offer
from members import ALIASES, host_of, squash
from linkname import looks_like_link

MIN_SAVING = 0.50  # EUR: less than this is not worth a different shop

BEST = "best"  # the link's shop is as cheap as the recommended offer (or cheaper)
CHEAPER = "cheaper"  # the recommended offer is cheaper than the link's shop
GONE = "gone"  # the link's shop is in the offers but cannot deliver
UNKNOWN = "unknown"  # the link's shop is not in the offers: the price is needed


def link_host(text):
    """'www.Kruidvat.nl/p/shampoo/123' -> 'kruidvat.nl'; None when the text is not a link."""
    text = (text or "").strip()
    if not looks_like_link(text):
        return None
    if "://" not in text:
        text = "https://" + text
    host = host_of(text)
    return host[4:] if host.startswith("www.") else host or None


def _registered(host):
    """'shop.bol.com' -> 'bol.com' (the last two labels; enough for .nl, .com, .de, ...)."""
    labels = (host or "").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host or ""


def same_shop(offer, host):
    """True when `offer` comes from the shop whose host is `host`."""
    if not host:
        return False
    domain = _registered(host)
    key = squash(domain)  # "bol.com" -> "bolcom"
    label = squash(domain.split(".")[0])  # "bol"
    offer_host = host_of(offer.url)  # Google's own links give ''
    if offer_host and _registered(offer_host) == domain:
        return True
    name = squash(offer.shop)
    # "Kruidvat.nl", "bol.com", "Amazon.nl - Retail" start with the whole domain; "Kruidvat",
    # "Wehkamp" are the first label alone.
    if ALIASES.get(name) == domain:  # "Albert Heijn" is ah.nl
        return True
    return bool(name) and (name == label or name == key or name.startswith(key))


def find_reference(offers, link):
    """The cheapest deliverable offer of the link's shop, or None. A shop that is only found with
    offers that cannot be had gives the cheapest of those (see `verdict`: kind GONE)."""
    host = link_host(link)
    mine = [offer for offer in offers if same_shop(offer, host)]
    if not mine:
        return None
    pool = [offer for offer in mine if offer.deliverable] or mine
    return min(pool, key=lambda offer: effective_offer(offer).total)


@dataclass(frozen=True)
class Verdict:
    kind: str  # BEST, CHEAPER, GONE or UNKNOWN
    shop: str  # the link's shop (its host when it is not among the offers)
    reference_total: float | None  # what it costs there, shipping included; None when unknown
    shipping_known: bool = True  # False: the reference total has no shipping in it
    typed: bool = False  # the reference total was typed by the person
    best_shop: str | None = None  # the cheapest offer by total
    best_total: float | None = None
    pick_shop: str | None = None  # the app's recommendation (a preferred shop may be a bit dearer)
    pick_total: float | None = None
    saving: float = 0.0  # EUR the cheapest offer is below the reference (0 unless CHEAPER)
    caution: str | None = None  # why the saving is not certain (the cheapest offer's variant is unconfirmed)

    @property
    def saving_pct(self):
        if not self.reference_total or self.saving <= 0:
            return 0.0
        return round(100 * self.saving / self.reference_total, 1)

    @property
    def pick_saving(self):
        """EUR the recommended offer is below the reference (can be 0 or negative)."""
        if self.reference_total is None or self.pick_total is None:
            return 0.0
        return round(self.reference_total - self.pick_total, 2)


def verdict(link, offers, recommendation, typed_total=None, min_saving=MIN_SAVING):
    """The verdict for a pasted link, or None when the text is not a link or nothing is
    recommended.

    `offers` are all the offers read (before any trust filter: the link's shop may not be on the
    member list and still is the one the person stands in); `recommendation` is the app's pick
    (`choice.Recommendation`, after the filter). `typed_total` is the total the person typed.
    The question is "is there a better deal?", so the reference is compared with the cheapest
    offer, not with a preferred shop that costs a little more.
    """
    host = link_host(link)
    if host is None or recommendation is None:
        return None
    best, pick = recommendation.cheapest, recommendation.pick
    reference = find_reference(offers, link)
    shop = reference.shop if reference is not None else host
    others = dict(best_shop=best.shop, best_total=best.total, pick_shop=pick.shop, pick_total=pick.total)

    if typed_total is not None and typed_total > 0:
        reference_total, shipping_known, typed = round(typed_total, 2), True, True
    elif reference is not None:
        shown = effective_offer(reference)
        reference_total, shipping_known, typed = shown.total, shown.shipping is not None, False
    else:
        return Verdict(UNKNOWN, shop, None, **others)

    common = dict(shop=shop, reference_total=reference_total, shipping_known=shipping_known, typed=typed, **others)
    if reference is not None and not typed and not reference.deliverable:
        return Verdict(GONE, **common)
    saving = round(reference_total - best.total, 2)
    if saving >= min_saving and not (reference is not None and same_shop(best, host)):
        return Verdict(CHEAPER, saving=saving, **common)
    return Verdict(BEST, **common)


_TOTAL_RE = re.compile(r"(?:€|eur)?\s*(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:[.,](\d{1,2}))?\s*(?:€|eur)?", re.IGNORECASE)


def parse_total(text):
    """What a person types for a price: '13,99', '€ 13.99', '1.299,00' -> float; None otherwise."""
    text = (text or "").strip()
    match = _TOTAL_RE.fullmatch(text)
    if not match:
        return None
    whole = re.sub(r"[.\s]", "", match.group(1))
    cents = match.group(2) or "0"
    value = float(f"{whole}.{cents.ljust(2, '0')[:2]}" if match.group(2) else whole)
    return value if value > 0 else None
