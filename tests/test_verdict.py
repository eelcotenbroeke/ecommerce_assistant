import pytest

import verdict
from beslist import Offer
from choice import recommend


def offer(shop, price, shipping=0.0, url=None, deliverable=True):
    return Offer(shop, price, shipping, None, deliverable, url)


def test_the_host_of_a_link_without_www_or_scheme():
    assert verdict.link_host("https://www.Kruidvat.nl/p/shampoo/123?x=1") == "kruidvat.nl"
    assert verdict.link_host("www.bol.com/nl/nl/p/x/9200000001/") == "bol.com"
    assert verdict.link_host("3474637283414") is None
    assert verdict.link_host("kerastase gloss absolu") is None
    assert verdict.link_host("") is None


@pytest.mark.parametrize(
    "shop, host",
    [
        ("Kruidvat.nl", "kruidvat.nl"),
        ("Kruidvat", "kruidvat.nl"),
        ("bol.com", "bol.com"),
        ("Amazon.nl - Retail", "amazon.nl"),
        ("Wehkamp", "wehkamp.nl"),
        ("Albert Heijn", "ah.nl"),
    ],
)
def test_a_shop_is_recognised_by_the_host_of_the_link(shop, host):
    found = verdict.same_shop(offer(shop, 10.0), host)
    assert found is True


def test_a_direct_offer_link_is_recognised_by_its_host():
    direct = offer("Albert Heijn", 3.5, url="https://www.ah.nl/producten/product/wi123/x")
    assert verdict.same_shop(direct, "ah.nl")


def test_google_links_and_other_shops_do_not_match():
    google = offer("Celini.nl", 10.0, url="https://www.google.com/goto?url=https%3A%2F%2Fwww.kruidvat.nl%2Fx")
    assert not verdict.same_shop(google, "kruidvat.nl")
    assert not verdict.same_shop(offer("bolderstore", 10.0), "bol.com")
    assert not verdict.same_shop(offer("Kruidvat.nl", 10.0), None)


def test_subdomains_count_as_the_same_shop():
    assert verdict.same_shop(offer("bol.com", 10.0), "shop.bol.com")


def test_the_reference_is_the_cheapest_deliverable_offer_of_that_shop():
    offers = [offer("Kruidvat.nl", 13.99, 0), offer("Kruidvat.nl", 12.0, 3.0), offer("Kruidvat.nl", 9.0, deliverable=False)]
    assert verdict.find_reference(offers, "https://www.kruidvat.nl/p/x/1").total == 13.99


def test_a_shop_that_cannot_deliver_still_is_found():
    offers = [offer("Kruidvat.nl", 9.0, deliverable=False)]
    assert verdict.find_reference(offers, "https://www.kruidvat.nl/p/x/1").price == 9.0


def make(offers, link):
    return verdict.verdict(link, offers, recommend(offers))


def test_a_cheaper_shop_elsewhere_gives_the_saving():
    offers = [offer("Kruidvat.nl", 13.99, 0.0), offer("bol.com", 12.0, 2.99)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.CHEAPER
    assert (found.shop, found.pick_shop) == ("Kruidvat.nl", "bol.com")
    assert found.reference_total == 13.99 and found.pick_total == 12.0  # bol ships free for the owner
    assert found.saving == 1.99 and found.saving_pct == 14.2


def test_the_link_shop_is_the_best_when_nothing_beats_it():
    offers = [offer("Kruidvat.nl", 9.99, 0.0), offer("bol.com", 12.0)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.BEST and found.saving == 0.0


def test_a_few_cents_are_not_worth_another_shop():
    offers = [offer("Kruidvat.nl", 10.00, 0.0), offer("Etos", 9.60, 0.0)]
    assert make(offers, "https://www.kruidvat.nl/p/x/1").kind == verdict.BEST
    offers = [offer("Kruidvat.nl", 10.00, 0.0), offer("Etos", 9.50, 0.0)]
    assert make(offers, "https://www.kruidvat.nl/p/x/1").kind == verdict.CHEAPER


def test_the_owners_free_delivery_counts_for_the_link_shop_too():
    offers = [offer("bol.com", 10.0, 2.99), offer("Etos", 11.0, 0.0)]
    found = make(offers, "https://www.bol.com/nl/nl/p/x/9200000001/")
    assert found.kind == verdict.BEST and found.reference_total == 10.0


def test_a_shop_that_cannot_deliver_is_said_so_with_the_alternative():
    offers = [offer("Kruidvat.nl", 9.0, deliverable=False), offer("bol.com", 12.0)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.GONE and found.pick_shop == "bol.com"


def test_unknown_shipping_at_the_link_shop_is_flagged():
    offers = [offer("Kruidvat.nl", 13.99, None), offer("bol.com", 12.0)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.CHEAPER and found.shipping_known is False


def test_a_shop_that_is_not_in_the_offers_asks_for_the_price():
    offers = [offer("bol.com", 12.0), offer("Etos", 13.0, 0.0)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.UNKNOWN and found.reference_total is None
    assert found.shop == "kruidvat.nl" and found.pick_shop == "bol.com"


def test_a_typed_total_is_the_reference():
    offers = [offer("bol.com", 12.0), offer("Etos", 13.0, 0.0)]
    link = "https://www.kruidvat.nl/p/x/1"
    found = verdict.verdict(link, offers, recommend(offers), typed_total=14.5)
    assert found.kind == verdict.CHEAPER and found.typed and found.saving == 2.5
    found = verdict.verdict(link, offers, recommend(offers), typed_total=11.0)
    assert found.kind == verdict.BEST and found.typed


def test_a_typed_total_beats_the_list_price_of_the_link_shop():
    offers = [offer("Kruidvat.nl", 13.99, 0.0), offer("bol.com", 12.5)]
    link = "https://www.kruidvat.nl/p/x/1"
    found = verdict.verdict(link, offers, recommend(offers), typed_total=11.0)
    assert found.kind == verdict.BEST and found.reference_total == 11.0


def test_no_verdict_without_a_link_or_a_recommendation():
    offers = [offer("bol.com", 12.0)]
    assert verdict.verdict("3474637283414", offers, recommend(offers)) is None
    assert verdict.verdict("https://www.kruidvat.nl/p/x/1", offers, None) is None


def test_the_link_shop_can_be_the_pick_even_with_a_pricier_seller_of_the_same_shop():
    offers = [offer("bol.com", 12.0), offer("bol.com", 15.0)]
    assert make(offers, "https://www.bol.com/nl/nl/p/x/9200000001/").kind == verdict.BEST


@pytest.mark.parametrize(
    "text, value",
    [
        ("13,99", 13.99), ("13.99", 13.99), ("€ 13,99", 13.99), ("€13,99", 13.99), ("13,99 €", 13.99),
        ("1.299,00", 1299.0), ("12,5", 12.5), ("13", 13.0), (" 7,50 ", 7.5), ("EUR 8,00", 8.0),
        ("", None), ("abc", None), ("0", None), ("-5", None), ("13,999", None), ("twaalf euro", None),
    ],
)
def test_parse_total(text, value):
    assert verdict.parse_total(text) == value


def test_the_saving_is_against_the_cheapest_shop_and_the_preferred_pick_is_kept_apart():
    # Etos is cheapest (40), bol is within 10% (42) and so is the owner's pick
    offers = [offer("Kruidvat.nl", 45.0, 0.0), offer("Etos", 40.0, 0.0), offer("bol.com", 42.0)]
    found = make(offers, "https://www.kruidvat.nl/p/x/1")
    assert found.kind == verdict.CHEAPER
    assert (found.best_shop, found.best_total, found.saving) == ("Etos", 40.0, 5.0)
    assert (found.pick_shop, found.pick_total, found.pick_saving) == ("bol.com", 42.0, 3.0)


def test_staying_is_not_the_best_when_the_cheapest_is_cheaper_than_the_preferred_pick():
    # the owner's pick is bol (40.00), but the link's shop (39.23) is not the cheapest (38.63)
    offers = [offer("Celini.nl", 35.28, 3.95), offer("Flaconi.nl", 38.63, 0.0), offer("bol.com", 40.0)]
    found = make(offers, "https://www.celini.nl/p/x/1")
    assert found.kind == verdict.CHEAPER and found.best_shop == "Flaconi.nl" and found.saving == 0.6
    assert found.pick_shop == "bol.com" and found.pick_saving < 0
