"""Which offer to buy: the owner's shop preference on top of the plain cheapest total.

Pure functions only (no network, no Streamlit). Rules, as stated by the owner on 2026-11-18:

- He buys online.
- Delivery from bol and Amazon is free for him, whatever shipping the comparison page shows
  for those shops, so their effective shipping is 0.
- He trusts bol and Amazon most: when a preferred shop is less than 10% above the cheapest
  total, he picks the preferred shop.

The 10% is measured against the cheapest deliverable total, after the free-delivery rule is
applied. "Below 10%" is strict: exactly 10% above does not qualify.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from beslist import Offer, rank_offers

TOLERANCE = 0.10
_EPSILON = 1e-9

# Matched against the whole, lower-cased shop name (a "www." prefix is ignored). Only the shops
# themselves: a third-party seller on bol or Amazon is not covered by the owner's free delivery.
_FREE_DELIVERY_RE = re.compile(r"(?:bol(?:\.com)?|amazon(?:\.nl)?)")


def is_preferred(shop):
    """True for the shops the owner trusts and gets free delivery from (bol, Amazon.nl)."""
    name = (shop or "").strip().lower()
    if name.startswith("www."):
        name = name[4:]
    return _FREE_DELIVERY_RE.fullmatch(name) is not None


def effective_offer(offer):
    """The offer as the owner pays it: preferred shops ship for free."""
    return replace(offer, shipping=0.0) if is_preferred(offer.shop) else offer


@dataclass(frozen=True)
class Recommendation:
    pick: Offer  # the offer to buy, with the owner's free delivery applied
    cheapest: Offer  # cheapest deliverable offer by effective total
    reason: str  # "cheapest" or "preferred"
    extra: float  # EUR the pick costs more than the cheapest (0 when they are the same)

    @property
    def extra_pct(self):
        return round(100 * self.extra / self.cheapest.total, 1) if self.cheapest.total else 0.0


def recommend(offers, tolerance=TOLERANCE):
    """Pick one offer to buy, or None when no offer is deliverable.

    The cheapest effective total wins, unless a preferred shop is less than `tolerance` above
    it: then the cheapest preferred shop wins and `extra` says what that costs.
    """
    ranked = rank_offers([effective_offer(offer) for offer in offers])
    if not ranked:
        return None
    cheapest = ranked[0]
    if is_preferred(cheapest.shop):
        return Recommendation(cheapest, cheapest, "cheapest", 0.0)
    allowed = tolerance * cheapest.total - _EPSILON  # strictly below, despite float rounding
    for offer in ranked:  # ranked by total, so the first preferred one is the cheapest
        extra = round(offer.total - cheapest.total, 2)
        if is_preferred(offer.shop) and extra < allowed:
            return Recommendation(offer, cheapest, "preferred", extra)
    return Recommendation(cheapest, cheapest, "cheapest", 0.0)
