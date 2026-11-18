import copy
import json

import pytest

from beslist import (
    Offer,
    best_offer,
    find_product_url,
    parse_offers,
    rank_offers,
    search_url,
)

EAN = "3474637283414"

# (shop, price, shipping, delivery text, availability) as read from the live Beslist product page
# on 2026-11-18. The offer links in the fixture are shortened stand-ins, not the real redirects.
LIVE_OFFERS = [
    ("Celini.nl", 35.28, 3.95, "1-2 dagen", "InStock"),
    ("Flaconi.nl", 38.63, 0, "2-3 dagen", "InStock"),
    ("Easycosmetic.nl", 39.99, 4.99, "2 dagen", "InStock"),
    ("Haibu.nl", 43.45, 0, "1 dag", "InStock"),
    ("Beautyplaza.com", 44.18, 0, "1 dag", "InStock"),
    ("bol.com", 44.18, 0, "1 dag", "InStock"),
    ("Notino.nl", 47.18, 4.25, "2 dagen", "InStock"),
    ("Basler-beauty.nl", 50.23, 5.9, "4-6 dagen", "InStock"),
    ("Iciparisxl.nl", 58.9, 0, "1-2 dagen", "InStock"),
    ("Lookfantastic.nl", 59.8, 0, "4 dagen", "InStock"),
    ("Kaufland.nl", 62.01, 0, "8+ dagen", "InStock"),
    ("Celebrestore.com", 36.3, 5.99, "Niet leverbaar", "InStock"),
]


def make_offer(shop, price, shipping, delivery, availability, n=0):
    return {
        "@type": "Offer",
        "availability": f"https://schema.org/{availability}",
        "price": price,
        "priceCurrency": "EUR",
        "url": f"/p/redirect/{n}",
        "seller": {"@type": "Organization", "name": shop},
        "shippingDetails": {
            "@type": "OfferShippingDetails",
            "shippingRate": {"@type": "MonetaryAmount", "value": shipping, "currency": "EUR"},
            "deliveryTime": {"@type": "ShippingDeliveryTime", "description": delivery},
        },
    }


def make_product(rows=LIVE_OFFERS):
    offers = [make_offer(*row, n=i) for i, row in enumerate(rows)]
    return {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Kerastase Gloss Absolu shampoo 500ml",
        "offers": {
            "@type": "AggregateOffer",
            "lowPrice": min(r[1] for r in rows),
            "highPrice": max(r[1] for r in rows),
            "priceCurrency": "EUR",
            "offerCount": len(rows),
            "offers": offers,
        },
    }


def page(*blocks, extra=""):
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in blocks
    )
    return f"<html><head>{scripts}</head><body><h1>Product</h1>{extra}</body></html>"


@pytest.fixture
def live_page():
    return page(make_product())


# ------------------------------------------------------------------ parse_offers


def test_parses_all_twelve_offers_in_page_order(live_page):
    offers = parse_offers(live_page)
    assert [o.shop for o in offers] == [row[0] for row in LIVE_OFFERS]


def test_offer_fields(live_page):
    celini = parse_offers(live_page)[0]
    assert celini == Offer(
        shop="Celini.nl",
        price=35.28,
        shipping=3.95,
        delivery="1-2 dagen",
        deliverable=True,
        url="https://www.beslist.nl/p/redirect/0",
    )
    assert celini.total == 39.23
    assert celini.shipping_known


def test_free_shipping_is_zero_not_unknown(live_page):
    flaconi = parse_offers(live_page)[1]
    assert flaconi.shipping == 0.0
    assert flaconi.shipping_known
    assert flaconi.total == 38.63


def test_in_stock_but_niet_leverbaar_is_not_deliverable(live_page):
    celebrestore = parse_offers(live_page)[-1]
    assert celebrestore.shop == "Celebrestore.com"
    assert not celebrestore.deliverable
    assert celebrestore.delivery == "Niet leverbaar"


@pytest.mark.parametrize(
    "text", ["Niet leverbaar", "niet op voorraad", "Uitverkocht", "Niet meer beschikbaar"]
)
def test_undeliverable_phrases(text):
    html = page(make_product([("Shop", 10.0, 0, text, "InStock")]))
    assert not parse_offers(html)[0].deliverable


@pytest.mark.parametrize("availability", ["OutOfStock", "SoldOut", "Discontinued", "PreOrder"])
def test_not_in_stock_availability_is_not_deliverable(availability):
    html = page(make_product([("Shop", 10.0, 0, "1 dag", availability)]))
    assert not parse_offers(html)[0].deliverable


def test_missing_availability_is_not_deliverable():
    product = make_product([("Shop", 10.0, 0, "1 dag", "InStock")])
    del product["offers"]["offers"][0]["availability"]
    assert not parse_offers(page(product))[0].deliverable


def test_limited_availability_is_deliverable():
    html = page(make_product([("Shop", 10.0, 0, "1 dag", "LimitedAvailability")]))
    assert parse_offers(html)[0].deliverable


def test_offers_as_plain_list_and_single_offer():
    product = make_product()
    as_list = copy.deepcopy(product)
    as_list["offers"] = as_list["offers"]["offers"]
    assert len(parse_offers(page(as_list))) == 12

    single = copy.deepcopy(product)
    single["offers"] = single["offers"]["offers"][0]
    assert [o.shop for o in parse_offers(page(single))] == ["Celini.nl"]


def test_product_inside_graph():
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "BreadcrumbList"}, make_product()]}
    assert len(parse_offers(page(graph))) == 12


def test_shipping_details_as_list_that_agrees():
    product = make_product([("Shop", 10.0, 2.5, "1 dag", "InStock")])
    detail = product["offers"]["offers"][0]["shippingDetails"]
    product["offers"]["offers"][0]["shippingDetails"] = [detail, copy.deepcopy(detail)]
    offer = parse_offers(page(product))[0]
    assert offer.shipping == 2.5
    assert offer.delivery == "1 dag"


def test_shipping_details_list_that_disagrees_is_unknown():
    product = make_product([("Shop", 10.0, 2.5, "1 dag", "InStock")])
    detail = product["offers"]["offers"][0]["shippingDetails"]
    other = copy.deepcopy(detail)
    other["shippingRate"]["value"] = 9.0
    product["offers"]["offers"][0]["shippingDetails"] = [detail, other]
    offer = parse_offers(page(product))[0]
    assert offer.shipping is None
    assert not offer.shipping_known
    assert offer.total == 10.0


def test_missing_or_foreign_currency_shipping_is_unknown():
    product = make_product([("A", 10.0, 2.0, "1 dag", "InStock"), ("B", 11.0, 2.0, "1 dag", "InStock")])
    del product["offers"]["offers"][0]["shippingDetails"]
    product["offers"]["offers"][1]["shippingDetails"]["shippingRate"]["currency"] = "GBP"
    offers = parse_offers(page(product))
    assert [o.shipping for o in offers] == [None, None]
    assert all(o.deliverable for o in offers)


def test_numeric_strings_are_accepted():
    product = make_product([("Shop", 10.0, 2.0, "1 dag", "InStock")])
    offer = product["offers"]["offers"][0]
    offer["price"] = "12.50"
    offer["shippingDetails"]["shippingRate"]["value"] = "1.95"
    parsed = parse_offers(page(product))[0]
    assert (parsed.price, parsed.shipping, parsed.total) == (12.5, 1.95, 14.45)


@pytest.mark.parametrize("bad", [None, "", "gratis", "NaN", "inf", 0, -5, True, [], {}])
def test_offers_without_a_usable_price_are_dropped(bad):
    product = make_product([("Bad", 10.0, 0, "1 dag", "InStock"), ("Good", 11.0, 0, "1 dag", "InStock")])
    product["offers"]["offers"][0]["price"] = bad
    assert [o.shop for o in parse_offers(page(product))] == ["Good"]


def test_offers_in_other_currency_are_dropped():
    product = make_product([("Pound", 10.0, 0, "1 dag", "InStock"), ("Euro", 11.0, 0, "1 dag", "InStock")])
    product["offers"]["offers"][0]["priceCurrency"] = "GBP"
    assert [o.shop for o in parse_offers(page(product))] == ["Euro"]


def test_offers_without_shop_name_are_dropped():
    product = make_product([("A", 10.0, 0, "1 dag", "InStock"), ("B", 11.0, 0, "1 dag", "InStock")])
    product["offers"]["offers"][0]["seller"] = {}
    product["offers"]["offers"][1]["seller"] = "  "
    assert parse_offers(page(product)) == []


def test_seller_as_plain_string_and_missing_url():
    product = make_product([("Shop", 10.0, 0, "1 dag", "InStock")])
    product["offers"]["offers"][0]["seller"] = "Plain Shop"
    del product["offers"]["offers"][0]["url"]
    offer = parse_offers(page(product))[0]
    assert offer.shop == "Plain Shop"
    assert offer.url is None


def test_absolute_offer_url_is_kept():
    product = make_product([("Shop", 10.0, 0, "1 dag", "InStock")])
    product["offers"]["offers"][0]["url"] = "https://www.beslist.nl/p/x/1/"
    assert parse_offers(page(product))[0].url == "https://www.beslist.nl/p/x/1/"


def test_two_products_with_offers_is_ambiguous():
    assert parse_offers(page(make_product(), make_product())) == []


def test_related_products_are_ignored():
    product = make_product()
    product["isSimilarTo"] = [make_product([("Other", 1.0, 0, "1 dag", "InStock")])]
    product["offers"]["offers"] = product["offers"]["offers"][:2]
    assert [o.shop for o in parse_offers(page(product))] == ["Celini.nl", "Flaconi.nl"]


def test_item_list_pages_have_no_offers():
    listing = {"@type": "ItemList", "itemListElement": [{"item": make_product()}]}
    assert parse_offers(page(listing)) == []


def test_other_ld_blocks_and_broken_json_do_not_hide_the_product():
    html = (
        '<script type="application/ld+json">{not json</script>'
        + page({"@type": "BreadcrumbList"}, make_product())
    )
    assert len(parse_offers(html)) == 12


def test_plain_scripts_are_not_read_as_json_ld():
    html = f"<script>var x = {json.dumps(make_product())};</script>"
    assert parse_offers(html) == []


@pytest.mark.parametrize("html", [None, "", "<html><body>niets</body></html>", "<<<>>><script"])
def test_empty_or_unusable_pages(html):
    assert parse_offers(html) == []


# ------------------------------------------------------------------ best_offer / rank_offers


def test_best_offer_on_live_data_is_flaconi(live_page):
    best = best_offer(parse_offers(live_page))
    assert (best.shop, best.total) == ("Flaconi.nl", 38.63)


def test_cheapest_price_is_not_cheapest_total(live_page):
    offers = parse_offers(live_page)
    cheapest_price = min(offers, key=lambda o: o.price)
    assert cheapest_price.shop == "Celini.nl"
    assert best_offer(offers).shop != cheapest_price.shop


def test_undeliverable_offer_is_never_best_even_when_cheapest():
    rows = [("Ghost", 5.0, 0, "Niet leverbaar", "InStock"), ("Real", 20.0, 0, "1 dag", "InStock")]
    assert best_offer(parse_offers(page(make_product(rows)))).shop == "Real"


def test_ranking_order_and_tie_keeps_page_order(live_page):
    ranked = rank_offers(parse_offers(live_page))
    assert [o.total for o in ranked] == sorted(o.total for o in ranked)
    assert len(ranked) == 11  # Celebrestore excluded
    tied = [o.shop for o in ranked if o.total == 44.18]
    assert tied == ["Beautyplaza.com", "bol.com"]


def test_known_shipping_beats_unknown_on_equal_total():
    unknown = Offer("Unknown", 10.0, None, None, True, None)
    known = Offer("Known", 10.0, 0.0, "1 dag", True, None)
    assert best_offer([unknown, known]).shop == "Known"


def test_best_offer_none_when_nothing_deliverable():
    only_ghost = [Offer("Ghost", 5.0, 0.0, "Niet leverbaar", False, None)]
    assert best_offer(only_ghost) is None
    assert best_offer([]) is None


def test_total_rounds_cleanly():
    assert Offer("S", 0.1, 0.2, None, True, None).total == 0.3


# ------------------------------------------------------------------ URLs


SEARCH_PAGE = f"""
<html><body>
  <a href="/p/andere-shampoo-250ml/286/3600523298130/">Andere</a>
  <a href="/p/kerastase-gloss-absolu-bain-hydra-glaze-shampoo-500ml/286/{EAN}/">Kerastase</a>
  <a href="/c/shampoo/">Shampoo</a>
</body></html>
"""


def test_search_url():
    assert search_url(EAN) == f"https://www.beslist.nl/products/r/{EAN}/"
    assert search_url("0" + EAN) == search_url(EAN)
    assert search_url("1234567890123") is None
    assert search_url(None) is None


def test_find_product_url_matches_on_barcode():
    assert find_product_url(SEARCH_PAGE, EAN) == (
        f"https://www.beslist.nl/p/kerastase-gloss-absolu-bain-hydra-glaze-shampoo-500ml/286/{EAN}/"
    )


def test_find_product_url_accepts_absolute_beslist_links_and_gtin14():
    html = f'<a href="https://www.beslist.nl/p/x/286/{EAN}/?ref=1">x</a>'
    assert find_product_url(html, "0" + EAN) == f"https://www.beslist.nl/p/x/286/{EAN}/"


def test_find_product_url_never_returns_another_product():
    html = '<a href="/p/andere/286/3600523298130/">Andere</a>'
    assert find_product_url(html, EAN) is None


def test_find_product_url_ignores_other_hosts():
    html = f'<a href="https://evil.example/p/x/286/{EAN}/">x</a>'
    assert find_product_url(html, EAN) is None


@pytest.mark.parametrize("html", [None, "", "<a href", "<<<>>>"])
def test_find_product_url_empty_or_broken_pages(html):
    assert find_product_url(html, EAN) is None


def test_find_product_url_invalid_barcode():
    assert find_product_url(SEARCH_PAGE, "1234567890123") is None
    assert find_product_url(SEARCH_PAGE, None) is None

