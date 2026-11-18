import pytest

import lookup
import serp
from serp_pages import TITLE, TOKEN, in_dollars, item, store

EAN = "3474637283414"
class FakeSerp:
    """Fake SerpApi: answers by engine, records the parameters of every call."""

    def __init__(self, shopping=None, stores=None, shopping_error=None, stores_answer="ok"):
        self.shopping = {"shopping_results": shopping or []} if shopping_error is None else {"error": shopping_error}
        self.stores = {"product_results": {"stores": stores or []}}
        self.stores_answer = stores_answer
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        if params["engine"] == "google_shopping":
            return self.shopping, self.shopping.get("error")
        if self.stores_answer is None:
            return None, "SerpApi timeout"
        return self.stores, None


def run(fake, text=EAN, **kwargs):
    return lookup.lookup(text, None, source=serp.serp_source(fake, now=lambda: "NOW"), **kwargs)


# ------------------------------------------------------------------ small parsers


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Gratis verzending", 0.0),
        ("Free shipping", 0.0),
        ("Free", 0.0),
        ("+ € 4,95 verzending", 4.95),
        ("€ 3,95 verzendkosten", 3.95),
        ("4,95 € bezorging", 4.95),
        ("+ €12", 12.0),
        ("Gratis verzending vanaf € 50", None),
        ("Gratis bezorging bij bestellingen boven € 20", None),
        ("Free delivery on orders over $59", None),
        ("Levering in 1-2 dagen", None),
        ("Morgen in huis", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_shipping(text, expected):
    assert serp.parse_shipping(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("€ 38,63", 38.63), ("1.234,56", 1234.56), ("1.234", 1234.0), ("4.95", 4.95), ("EUR 5", 5.0), ("gratis", None), (None, None)],
)
def test_money(text, expected):
    assert serp.money(text) == expected


# ------------------------------------------------------------------ the chain


def test_stores_list_gives_the_offers_with_shipping():
    fake = FakeSerp(
        shopping=[item("Flaconi.nl", 38.63), item("Celini.nl", 35.28)],
        stores=[
            store("Celini.nl", 35.28, shipping_extracted=3.95, link="https://celini.nl/x"),
            store("Flaconi.nl", 38.63, shipping="Free", extensions=["In stock online", "Gratis levering morgen"]),
            store("bol.com", 42.0, shipping="Free"),
        ],
    )
    result = run(fake)
    assert result.status == lookup.OK
    assert result.source == "Google Shopping"
    assert result.product_title == TITLE
    assert result.read_at == "NOW"
    assert result.product_url == "https://www.google.com/shopping/product/111"
    shops = {o.shop: o for o in result.offers}
    assert set(shops) == {"Celini.nl", "Flaconi.nl", "bol.com"}
    assert shops["Celini.nl"].shipping == 3.95 and shops["Celini.nl"].url == "https://celini.nl/x"
    assert shops["Flaconi.nl"].shipping == 0.0 and shops["Flaconi.nl"].delivery == "Gratis levering morgen"
    # bol.com at 42,00 is 8,7% above Flaconi (38,63): the preferred shop wins
    assert result.recommendation.pick.shop == "bol.com"
    assert result.recommendation.cheapest.shop == "Flaconi.nl"
    assert serp.CHECK_TITLE_NOTE in result.notes


def test_calls_use_the_documented_parameters():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[store("Flaconi.nl", 38.63)])
    run(fake)
    assert fake.calls == [
        {"engine": "google_shopping", "q": EAN, "hl": "nl", "gl": "nl"},
        {"engine": "google_immersive_product", "page_token": TOKEN, "more_stores": "true"},
    ]
    assert not any("api_key" in call for call in fake.calls)


def test_direct_results_fill_in_shops_missing_from_the_store_list():
    fake = FakeSerp(
        shopping=[item("Flaconi.nl", 99.0), item("Haibu.nl", 43.45, delivery="Gratis verzending")],
        stores=[store("Flaconi.nl", 38.63, shipping="Free")],
    )
    offers = {o.shop: o for o in run(fake).offers}
    assert offers["Flaconi.nl"].price == 38.63  # the store list wins over the direct result
    assert offers["Haibu.nl"].shipping == 0.0 and offers["Haibu.nl"].delivery == "Gratis verzending"


def test_without_a_token_only_direct_results_are_used():
    fake = FakeSerp(shopping=[item("Haibu.nl", 43.45, token=None), item("bol.com", 44.18, "+ € 3,95 verzending", token=None)])
    result = run(fake)
    assert [c["engine"] for c in fake.calls] == ["google_shopping"]
    offers = {o.shop: o for o in result.offers}
    assert offers["bol.com"].shipping == 3.95
    assert offers["Haibu.nl"].shipping is None  # no delivery text: unknown, not guessed
    # bol is free for the owner whatever Google says
    assert result.recommendation.pick.shop == "bol.com" and result.recommendation.pick.total == 44.18


def test_one_offer_per_shop_cheapest_total_wins():
    fake = FakeSerp(
        shopping=[item("A.nl", 10.0)],
        stores=[store("Shop", 20.0, shipping_extracted=5.0), store("shop", 22.0, shipping_extracted=0.0), store("Shop", 30.0)],
    )
    shop_offers = [o for o in run(fake).offers if o.shop.lower() == "shop"]
    assert len(shop_offers) == 1 and shop_offers[0].total == 22.0


def test_largest_product_group_is_used_and_the_others_are_reported():
    fake = FakeSerp(
        shopping=[
            item("A.nl", 38.0, product_id="111"),
            item("B.nl", 40.0, product_id="111"),
            item("C.nl", 15.0, product_id="222", title="Kérastase Gloss Absolu Masque 200 ml", token="other"),
        ],
        stores=[],
    )
    result = run(fake)
    assert {o.shop for o in result.offers} == {"A.nl", "B.nl"}
    assert any("2 verschillende producten" in note for note in result.notes)
    assert fake.calls[1]["page_token"] == TOKEN


def test_results_without_product_id_are_grouped_by_title():
    fake = FakeSerp(
        shopping=[
            item("A.nl", 38.0, product_id=None, token=None),
            item("B.nl", 40.0, product_id=None, token=None, title=TITLE.upper()),
            item("C.nl", 15.0, product_id=None, token=None, title="Iets anders"),
        ]
    )
    assert {o.shop for o in run(fake).offers} == {"A.nl", "B.nl"}


def test_dollar_prices_missing_prices_and_missing_shops_are_skipped():
    bad_shop = item("", 10.0)
    dollars = in_dollars(item("US.com", 10.0))
    no_price = item("NoPrice.nl", 10.0)
    no_price.pop("extracted_price")
    no_price["price"] = "op aanvraag"
    fake = FakeSerp(shopping=[bad_shop, dollars, no_price, item("Good.nl", 10.0)], stores=[])
    assert [o.shop for o in run(fake).offers] == ["Good.nl"]


def test_out_of_stock_stores_are_not_deliverable():
    fake = FakeSerp(
        shopping=[item("A.nl", 10.0)],
        stores=[
            store("Ghost.nl", 5.0, extensions=["Niet op voorraad"]),
            store("Real.nl", 12.0, extensions=["In stock online"]),
        ],
    )
    result = run(fake)
    assert {o.shop: o.deliverable for o in result.offers} == {"Ghost.nl": False, "Real.nl": True, "A.nl": True}
    assert result.recommendation.pick.shop in {"A.nl"}  # Ghost is never picked


def test_stores_call_failing_keeps_the_direct_results_and_says_so():
    fake = FakeSerp(shopping=[item("A.nl", 10.0, "Gratis verzending")], stores_answer=None)
    result = run(fake)
    assert result.status == lookup.OK
    assert [o.shop for o in result.offers] == ["A.nl"]
    assert any("winkellijst" in note.lower() and "SerpApi timeout" in note for note in result.notes)
    assert "Winkellijst: geen antwoord" in result.steps


# ------------------------------------------------------------------ failures


def test_no_results_means_not_found():
    result = run(FakeSerp(shopping_error="Google hasn't returned any results for this query."))
    assert result.status == lookup.NOT_FOUND
    result = run(FakeSerp(shopping=[]))
    assert result.status == lookup.NOT_FOUND
    assert result.product_title is None


def test_api_errors_are_source_errors():
    result = run(FakeSerp(shopping_error="Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"))
    assert result.status == lookup.SOURCE_ERROR
    assert "Invalid API key" in result.message
    assert not result.worked


def test_no_answer_from_serpapi():
    result = lookup.lookup(EAN, None, source=serp.serp_source(lambda params: (None, "Netwerkfout: timeout")))
    assert result.status == lookup.SOURCE_ERROR
    assert "Netwerkfout: timeout" in result.message
    assert result.steps == ("Google Shopping: geen antwoord",)


def test_product_without_usable_prices():
    fake = FakeSerp(shopping=[in_dollars(item("US.com", 10.0))], stores=[])
    result = run(fake)
    assert result.status == lookup.NO_OFFERS
    assert result.product_url == "https://www.google.com/shopping/product/111"


def test_nothing_deliverable():
    fake = FakeSerp(shopping=[item("A.nl", 10.0, "Uitverkocht")], stores=[])
    result = run(fake)
    assert result.status == lookup.UNAVAILABLE and result.worked
