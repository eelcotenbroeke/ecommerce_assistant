import pytest

from beslist import Offer
from choice import TOLERANCE, effective_offer, is_preferred, recommend


def offer(shop, price, shipping=0.0, deliverable=True):
    return Offer(shop, price, shipping, "1 dag", deliverable, None)


# Totals from the Kerastase page read on 2026-11-18 (Flaconi 38,63 free shipping, Celini 35,28 +
# 3,95, bol 44,18); the bol/Amazon variants below are made up to test the 10% boundary.
LIVE = [
    offer("Celini.nl", 35.28, 3.95),
    offer("Flaconi.nl", 38.63),
    offer("Beautyplaza.com", 44.18),
    offer("bol.com", 44.18),
]


@pytest.mark.parametrize(
    "shop", ["bol.com", "Bol.com", "bol", "BOL", " bol.com ", "www.bol.com", "Amazon.nl", "amazon", "Amazon"]
)
def test_preferred_shops(shop):
    assert is_preferred(shop)


@pytest.mark.parametrize(
    "shop",
    ["Flaconi.nl", "Beautyplaza.com", "bol.com partner", "Amazon.de", "Amazon Warehouse", "", None],
)
def test_other_shops_are_not_preferred(shop):
    assert not is_preferred(shop)


def test_effective_offer_gives_free_delivery_to_preferred_shops_only():
    assert effective_offer(offer("bol.com", 20.0, 3.95)).total == 20.0
    assert effective_offer(offer("Amazon.nl", 20.0, None)).shipping == 0.0
    assert effective_offer(offer("Flaconi.nl", 20.0, 3.95)).total == 23.95


def test_live_example_bol_is_too_expensive_so_cheapest_wins():
    rec = recommend(LIVE)
    assert (rec.pick.shop, rec.reason, rec.extra) == ("Flaconi.nl", "cheapest", 0.0)
    assert rec.pick == rec.cheapest


def test_preferred_shop_within_tolerance_wins():
    offers = [offer("Flaconi.nl", 38.63), offer("bol.com", 42.00)]  # 8,7% above
    rec = recommend(offers)
    assert (rec.pick.shop, rec.reason) == ("bol.com", "preferred")
    assert rec.cheapest.shop == "Flaconi.nl"
    assert rec.extra == 3.37
    assert rec.extra_pct == 8.7


def test_exactly_ten_percent_does_not_qualify_but_just_below_does():
    assert recommend([offer("Shop", 100.0), offer("bol.com", 110.0)]).pick.shop == "Shop"
    assert recommend([offer("Shop", 100.0), offer("bol.com", 109.99)]).pick.shop == "bol.com"


def test_free_delivery_makes_bol_win_over_a_shop_with_shipping():
    # bol shows 3,95 shipping on the comparison page but is free for the owner: 40,00 vs 39,95.
    offers = [offer("Shop", 35.0, 4.95), offer("bol.com", 40.0, 3.95)]
    rec = recommend(offers)
    assert rec.pick.shop == "bol.com"
    assert rec.pick.total == 40.0
    assert rec.cheapest.shop == "Shop"
    assert rec.reason == "preferred"


def test_preferred_shop_that_is_cheapest_has_no_extra():
    rec = recommend([offer("Shop", 50.0), offer("Amazon.nl", 45.0)])
    assert (rec.pick.shop, rec.reason, rec.extra) == ("Amazon.nl", "cheapest", 0.0)
    assert rec.extra_pct == 0.0


def test_cheapest_of_several_preferred_shops_is_chosen():
    offers = [offer("Shop", 100.0), offer("bol.com", 108.0), offer("Amazon.nl", 105.0)]
    assert recommend(offers).pick.shop == "Amazon.nl"


def test_preferred_shop_that_cannot_deliver_is_ignored():
    offers = [offer("Shop", 100.0), offer("bol.com", 101.0, deliverable=False)]
    rec = recommend(offers)
    assert (rec.pick.shop, rec.reason) == ("Shop", "cheapest")


def test_tolerance_can_be_changed():
    offers = [offer("Shop", 100.0), offer("bol.com", 104.0)]
    assert recommend(offers, tolerance=0.03).pick.shop == "Shop"
    assert recommend(offers, tolerance=0.05).pick.shop == "bol.com"
    assert TOLERANCE == 0.10


def test_unknown_shipping_is_counted_as_zero_for_other_shops():
    # Documented limit: Offer.total treats unknown shipping as 0, shipping_known tells the caller.
    rec = recommend([Offer("Shop", 30.0, None, None, True, None)])
    assert rec.pick.total == 30.0
    assert not rec.pick.shipping_known


def test_nothing_deliverable_or_empty_gives_none():
    assert recommend([]) is None
    assert recommend([offer("bol.com", 10.0, deliverable=False)]) is None
