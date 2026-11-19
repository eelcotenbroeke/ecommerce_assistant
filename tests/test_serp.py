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


OTHER = "Kérastase Gloss Absolu Masque 200 ml"


def two_products():
    return FakeSerp(
        shopping=[
            item("C.nl", 15.0, product_id="222", title=OTHER, token="other"),
            item("A.nl", 38.0, product_id="111"),
            item("B.nl", 40.0, product_id="111"),
        ],
        stores=[],
    )


def test_different_products_are_offered_to_choose_from_in_googles_order():
    fake = two_products()
    result = run(fake)
    assert result.status == lookup.CHOOSE
    assert [c.title for c in result.candidates] == [OTHER, TITLE]
    assert [c.shops for c in result.candidates] == [("C.nl",), ("A.nl", "B.nl")]
    assert (result.candidates[1].price_min, result.candidates[1].price_max) == (38.0, 40.0)
    assert [call["engine"] for call in fake.calls] == ["google_shopping"]  # nothing fetched yet
    assert result.offers == ()


def test_choosing_a_product_fetches_only_its_shops():
    fake = two_products()
    chosen = lookup.finish_choice(run(fake), run(fake).candidates[1])
    assert chosen.status == lookup.OK
    assert {o.shop for o in chosen.offers} == {"A.nl", "B.nl"}
    assert fake.calls[-1]["page_token"] == TOKEN
    assert not any("controleer de titel" in note.lower() for note in chosen.notes)  # he chose it himself


def test_a_failing_second_step_after_choosing_is_a_failure_of_that_lookup():
    fake = two_products()
    other = run(fake).candidates[0]
    result = lookup.finish_choice(run(fake), other)  # C.nl has an item but no store list
    assert result.status == lookup.OK and {o.shop for o in result.offers} == {"C.nl"}


def test_results_without_product_id_are_grouped_by_title():
    fake = FakeSerp(
        shopping=[
            item("A.nl", 38.0, product_id=None, token=None),
            item("B.nl", 40.0, product_id=None, token=None, title=TITLE.upper()),
            item("C.nl", 15.0, product_id=None, token=None, title="Iets anders"),
        ]
    )
    result = run(fake)
    assert result.status == lookup.CHOOSE
    assert [c.shops for c in result.candidates] == [("A.nl", "B.nl"), ("C.nl",)]


def test_a_single_product_is_used_straight_away_with_the_check_title_note():
    result = run(FakeSerp(shopping=[item("A.nl", 38.0), item("B.nl", 40.0)], stores=[]))
    assert result.status == lookup.OK
    assert {o.shop for o in result.offers} == {"A.nl", "B.nl"}
    assert any("titel" in note.lower() for note in result.notes)


def test_the_product_name_ranks_candidates_and_a_clear_match_settles_the_choice():
    fake = two_products()
    source = serp.serp_source(fake, now=lambda: "NOW", hint="kerastase GLOSS absolu bain hydra-glaze shampoo")
    result = lookup.lookup(EAN, None, source=source)
    assert result.status == lookup.OK
    assert {o.shop for o in result.offers} == {"A.nl", "B.nl"}
    assert any("productnaam" in step for step in result.steps)


def test_a_name_that_fits_several_products_equally_still_shows_the_list():
    source = serp.serp_source(two_products(), now=lambda: "NOW", hint="kerastase gloss absolu")
    result = lookup.lookup(EAN, None, source=source)
    assert result.status == lookup.CHOOSE  # both titles have all three words: no clear winner
    assert [c.title for c in result.candidates] == [OTHER, TITLE]  # a tie keeps Google's order


def test_rank_puts_the_best_name_match_first_but_only_a_good_match_is_picked_automatically():
    cands = serp.candidates(two_products().shopping["shopping_results"])
    ranked = serp.rank(cands, "shampoo hydra glaze foo bar baz")  # 3 of 6 words: TITLE
    assert [c.key for c in ranked] == ["111", "222"]
    assert ranked[0].score == 0.5 and ranked[1].score == 0.0
    assert serp.auto_pick(ranked, "shampoo hydra glaze foo bar baz") is None  # 0.5 < 1.0
    assert serp.auto_pick(serp.rank(cands, "masque"), "masque") is None  # one word: too vague
    assert serp.auto_pick(serp.rank(cands, None), None) is None
    specific = "kerastase gloss absolu masque 200 ml"  # every word in one title, the other trails
    assert serp.auto_pick(serp.rank(cands, specific), specific).key == "222"


def test_a_short_name_never_settles_the_choice_even_when_one_title_has_all_its_words():
    ranked = serp.rank(serp.candidates(two_products().shopping["shopping_results"]), "masque")
    assert ranked[0].score == 1.0 and ranked[1].score == 0.0
    assert serp.auto_pick(ranked, "masque") is None
    assert serp.auto_pick(ranked[:1], "masque").key == ranked[0].key  # a single candidate is no choice


def test_a_specific_name_needs_a_clear_lead_over_the_second_title():
    cands = serp.candidates(
        [
            item("A.nl", 10.0, product_id="1", title="Segafredo Intermezzo koffiebonen 1 kg bio"),
            item("B.nl", 11.0, product_id="2", title="Segafredo Intermezzo koffiebonen 1 kg"),
        ]
    )
    name = "segafredo intermezzo koffiebonen 1 kg bio"  # 6 of 6 words against 5 of 6: a lead of 0.17
    assert [round(c.score, 2) for c in serp.rank(cands, name)] == [1.0, 0.83]
    assert serp.auto_pick(serp.rank(cands, name), name) is None


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


def test_guard_reason_stops_before_any_search():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)])
    source = serp.serp_source(fake, now=lambda: "NOW", guard=lambda cost: "Maandbudget bereikt: 100 van 100")
    result = lookup.lookup(EAN, None, source=source)
    assert result.status == lookup.SOURCE_ERROR
    assert result.message == "Maandbudget bereikt: 100 van 100"
    assert result.source == "Google Shopping"
    assert fake.calls == []
    assert result.offers == ()


def test_guard_that_says_none_lets_the_search_run():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[store("Flaconi.nl", 38.63, shipping="Free")])
    source = serp.serp_source(fake, now=lambda: "NOW", guard=lambda cost: None)
    assert lookup.lookup(EAN, None, source=source).status == lookup.OK
    assert [call["engine"] for call in fake.calls] == ["google_shopping", "google_immersive_product"]


# ------------------------------------------------------------------ title, details and links per offer


def test_offers_carry_the_shops_title_details_and_link():
    fake = FakeSerp(
        shopping=[
            item(
                "Flaconi.nl", 38.63, delivery="Gratis levering morgen", link="https://flaconi.nl/p/1",
                title="Flaconi: K\u00e9rastase Gloss 500 ml", rating=4.5, reviews=120,
                extensions=["Op voorraad", "Gratis levering morgen"], snippet="Voor glans en zachtheid.",
            )
        ],
        stores=[],
    )
    offer = run(fake).offers[0]
    assert offer.title == "Flaconi: K\u00e9rastase Gloss 500 ml"
    assert offer.url == "https://flaconi.nl/p/1"
    # the delivery text is shown on its own line, so it is not repeated in the details
    assert offer.details == ("\u2605 4,5 (120 reviews)", "Op voorraad", "Voor glans en zachtheid.")


def test_store_offer_borrows_what_it_lacks_from_the_same_shops_result():
    fake = FakeSerp(
        shopping=[item("Flaconi.nl", 38.63, link="https://flaconi.nl/p/1", title="Flaconi titel", rating=4.0)],
        stores=[store("Flaconi.nl", 38.63, shipping="Free")],
    )
    offer = run(fake).offers[0]
    assert offer.shipping == 0.0  # the store list still decides shipping and price
    assert (offer.url, offer.title, offer.details) == ("https://flaconi.nl/p/1", "Flaconi titel", ("\u2605 4,0",))


def test_store_offer_keeps_its_own_link_title_and_details():
    fake = FakeSerp(
        shopping=[item("Flaconi.nl", 38.63, link="https://flaconi.nl/oud", title="oud", rating=1.0)],
        stores=[
            store(
                "Flaconi.nl", 38.63, shipping="Free", link="https://flaconi.nl/nieuw",
                title="nieuw", extensions=["Op voorraad"],
            )
        ],
    )
    offer = run(fake).offers[0]
    assert (offer.url, offer.title, offer.details) == ("https://flaconi.nl/nieuw", "nieuw", ("Op voorraad",))


def test_details_are_short_deduplicated_and_capped():
    long_text = "x" * 400
    fake = FakeSerp(
        shopping=[
            item(
                "Flaconi.nl", 38.63, rating=9, extensions=["A", "a", "B", "C", "D", "E", "F", "G"], snippet=long_text,
            )
        ],
        stores=[],
    )
    offer = run(fake).offers[0]
    assert "\u2605" not in "".join(offer.details)  # 9 is not a star rating
    assert offer.details == ("A", "B", "C", "D", "E", "F")  # "a" repeats "A"; capped at six
    only_long = run(FakeSerp(shopping=[item("Flaconi.nl", 38.63, snippet=long_text)], stores=[])).offers[0]
    assert len(only_long.details[0]) == 160 and only_long.details[0].endswith("\u2026")


def test_the_guard_is_asked_for_the_whole_lookup_once_when_the_product_is_clear():
    costs = []
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[store("Flaconi.nl", 38.63, shipping="Free")])
    source = serp.serp_source(fake, now=lambda: "NOW", guard=lambda cost: costs.append(cost))
    assert lookup.lookup(EAN, None, source=source).status == lookup.OK
    assert costs == [2]


def test_the_guard_is_asked_again_for_the_second_step_after_choosing():
    costs = []
    fake = two_products()
    source = serp.serp_source(fake, now=lambda: "NOW", guard=lambda cost: costs.append(cost))
    result = lookup.lookup(EAN, None, source=source)
    assert costs == [2]
    lookup.finish_choice(result, result.candidates[1])
    assert costs == [2, 1]


def test_a_guard_stopping_the_second_step_keeps_the_first_search_in_the_steps():
    fake = two_products()
    stop = {"now": False}
    source = serp.serp_source(fake, now=lambda: "NOW", guard=lambda cost: "Op" if stop["now"] else None)
    result = lookup.lookup(EAN, None, source=source)
    stop["now"] = True
    failed = lookup.finish_choice(result, result.candidates[1])
    assert failed.status == lookup.SOURCE_ERROR and failed.message == "Op"
    assert len(fake.calls) == 1  # the stores were not fetched


def test_javascript_or_http_links_are_not_kept():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63, link="javascript:alert(1)")], stores=[])
    assert run(fake).offers[0].url is None


# ------------------------------------------------------------------ searching on the owner's product name

NAME = "Kérastase Gloss Absolu shampoo"


def test_the_bare_ean_is_the_query_without_a_product_name():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run(fake)
    assert fake.calls[0]["q"] == EAN
    assert result.query is None


def test_a_product_name_is_the_query_instead_of_the_ean():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    source = serp.serp_source(fake, now=lambda: "NOW", hint=f"  {NAME} ")
    result = lookup.lookup(EAN, None, source=source)
    assert fake.calls[0]["q"] == NAME  # trimmed
    assert result.ean == EAN and result.query == NAME
    assert result.status == lookup.OK


def test_a_very_long_product_name_is_cut_before_it_is_sent():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    source = serp.serp_source(fake, now=lambda: "NOW", hint="woord " * 100)
    lookup.lookup(EAN, None, source=source)
    assert len(fake.calls[0]["q"]) <= serp.MAX_QUERY_CHARS


def test_the_search_text_survives_the_choice_step():
    fake = two_products()
    source = serp.serp_source(fake, now=lambda: "NOW", hint="kerastase")
    result = lookup.lookup(EAN, None, source=source)
    assert result.status == lookup.CHOOSE and result.query == "kerastase"
    chosen = lookup.finish_choice(result, result.candidates[1])
    assert chosen.status == lookup.OK and chosen.query == "kerastase"


def test_nothing_found_for_the_ean_tells_the_owner_to_fill_in_the_product_name():
    fake = FakeSerp(shopping_error="Google hasn't returned any results for this query.")
    result = run(fake)
    assert result.status == lookup.NOT_FOUND
    assert "Productnaam" in result.message and "EAN" in result.message
    assert result.query is None
    assert len(fake.calls) == 1


def test_nothing_found_for_the_name_asks_for_another_name():
    fake = FakeSerp(shopping_error="Google hasn't returned any results for this query.")
    source = serp.serp_source(fake, now=lambda: "NOW", hint="onzinproduct")
    result = lookup.lookup(EAN, None, source=source)
    assert result.status == lookup.NOT_FOUND
    assert "“onzinproduct”" in result.message and "andere naam" in result.message
    assert result.query == "onzinproduct"


# ------------------------------------------------------------------ no EAN: the name of an Amazon page

AMAZON_URL = "https://www.amazon.nl/dp/B0DJR1RWS3"
PAGE_NAME = "Kérastase Gloss Absolu shampoo"


def amazon_fetch(title=PAGE_NAME):
    page = (
        f"<html><head><title>{title} : Amazon.nl: Beauty</title></head>"
        "<body><table><tr><th>ASIN</th><td>B0DJR1RWS3</td></tr></table></body></html>"
    )
    return lambda url: (200, page)


def run_amazon(fake, title=PAGE_NAME, **kwargs):
    source = serp.serp_source(fake, now=lambda: "NOW", **kwargs)
    return lookup.lookup(AMAZON_URL, amazon_fetch(title), source=source)


def test_without_an_ean_google_is_asked_for_the_name_of_the_amazon_page():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run_amazon(fake)
    assert fake.calls[0]["q"] == PAGE_NAME
    assert result.status == lookup.OK and result.ean is None and result.query == PAGE_NAME
    assert lookup.NAME_FROM_AMAZON_NOTE in result.notes


def test_the_owners_own_name_wins_over_the_name_of_the_page():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run_amazon(fake, hint="Kérastase masque")
    assert fake.calls[0]["q"] == "Kérastase masque" and result.query == "Kérastase masque"


def test_the_owners_name_is_used_when_the_page_has_no_usable_title():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run_amazon(fake, title="Amazon.nl", hint="Kérastase masque")
    assert fake.calls[0]["q"] == "Kérastase masque" and result.status == lookup.OK


def test_without_any_name_nothing_is_searched_and_the_owner_is_asked_for_the_ean():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run_amazon(fake, title="Amazon.nl")
    assert result.status == lookup.NEEDS_EAN and "Productnaam" in result.message
    assert fake.calls == []


def test_the_name_of_the_page_ranks_the_candidates_and_a_clear_match_is_picked():
    fake = FakeSerp(
        shopping=[
            item("C.nl", 15.0, product_id="222", title=OTHER, token="other"),
            item("A.nl", 38.0, product_id="111"),
        ],
        stores=[],
    )
    result = run_amazon(fake)
    assert result.status == lookup.OK and result.product_title == TITLE
    assert "Product gekozen op je productnaam" in result.steps


def test_several_products_for_the_name_of_the_page_are_a_list_with_the_note():
    fake = two_products()
    result = run_amazon(fake, title="Kérastase Gloss")
    assert result.status == lookup.CHOOSE and result.ean is None
    assert result.query == "Kérastase Gloss" and lookup.NAME_FROM_AMAZON_NOTE in result.notes
    chosen = lookup.finish_choice(result, result.candidates[1])
    assert chosen.status == lookup.OK and chosen.query == "Kérastase Gloss"


def test_nothing_found_for_the_name_of_the_page_says_which_name_was_tried():
    fake = FakeSerp(shopping_error="Google hasn't returned any results for this query.")
    result = run_amazon(fake)
    assert result.status == lookup.NOT_FOUND
    assert f"“{PAGE_NAME}”" in result.message and result.query == PAGE_NAME


def test_the_budget_guard_still_counts_both_steps_for_a_search_on_the_page_name():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    asked = []
    result = run_amazon(fake, guard=lambda cost: asked.append(cost))
    assert asked == [2] and result.status == lookup.OK


# ------------------------------------------------------------------ no link, no EAN: only a name


def run_by_name(fake, name=PAGE_NAME, **kwargs):
    source = serp.serp_source(fake, now=lambda: "NOW", hint=name, **kwargs)
    return lookup.lookup("", None, source=source, by_name=True)  # fetch is None: no page is read


def test_a_name_alone_is_what_google_is_asked_for():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    result = run_by_name(fake)
    assert fake.calls[0]["q"] == PAGE_NAME
    assert result.status == lookup.OK and result.ean is None and result.query == PAGE_NAME
    assert lookup.NAME_FROM_AMAZON_NOTE not in result.notes


def test_a_name_alone_that_matches_one_product_is_picked_at_once():
    fake = FakeSerp(
        shopping=[
            item("C.nl", 15.0, product_id="222", title=OTHER, token="other"),
            item("A.nl", 38.0, product_id="111"),
        ],
        stores=[],
    )
    result = run_by_name(fake, name=TITLE)
    assert result.status == lookup.OK and result.product_title == TITLE
    assert "Product gekozen op je productnaam" in result.steps


def test_a_vague_name_alone_gives_a_list_to_choose_from_and_the_choice_keeps_the_query():
    fake = two_products()
    result = run_by_name(fake, name="Kérastase Gloss")
    assert result.status == lookup.CHOOSE and result.ean is None and result.query == "Kérastase Gloss"
    assert result.notes == ()
    chosen = lookup.finish_choice(result, result.candidates[1])
    assert chosen.status == lookup.OK and chosen.query == "Kérastase Gloss"


def test_nothing_found_for_a_name_alone_asks_for_another_name():
    fake = FakeSerp(shopping_error="Google hasn't returned any results for this query.")
    result = run_by_name(fake, name="onzinproduct")
    assert result.status == lookup.NOT_FOUND and "“onzinproduct”" in result.message


def test_the_budget_guard_counts_both_steps_for_a_name_alone():
    fake = FakeSerp(shopping=[item("Flaconi.nl", 38.63)], stores=[])
    asked = []
    result = run_by_name(fake, guard=lambda cost: asked.append(cost))
    assert asked == [2] and result.status == lookup.OK


# ------------------------------------------------------------------ the next page of the store list


class PagedSerp(FakeSerp):
    """Fake SerpApi whose store list has pages: `pages` maps a next_page_token to the stores of that page."""

    def __init__(self, first, token, pages, **kwargs):
        super().__init__(shopping=[item("Shop0.nl", 40.0)], stores=first, **kwargs)
        if token:
            self.stores["product_results"]["stores_next_page_token"] = token
        self.pages = pages

    def __call__(self, params):
        next_token = params.get("next_page_token")
        if next_token is not None:
            self.calls.append(params)
            page = self.pages.get(next_token)
            if page is None:
                return None, "SerpApi timeout"
            stores, token = page
            product = {"stores": stores}
            if token:
                product["stores_next_page_token"] = token
            return {"product_results": product}, None
        return super().__call__(params)


def first_page():
    return [store(f"Shop{n}.nl", 40.0 + n, shipping_extracted=0.0) for n in range(13)]


def test_the_next_page_of_stores_is_read_when_google_has_more():
    fake = PagedSerp(first_page(), "T2", {"T2": ([store("Goedkoop.nl", 30.0, shipping_extracted=2.0)], None)})
    result = run(fake)
    assert result.status == lookup.OK
    assert len(result.offers) == 14 and result.recommendation.cheapest.shop == "Goedkoop.nl"
    assert [c["engine"] for c in fake.calls] == ["google_shopping", "google_immersive_product", "google_immersive_product"]
    assert fake.calls[2] == {
        "engine": "google_immersive_product", "page_token": TOKEN, "more_stores": "true", "next_page_token": "T2",
    }
    assert "Winkellijst, pagina 2: 1 winkels" in result.steps
    assert serp.MORE_STORES_NOTE not in result.notes


def test_only_one_extra_page_is_followed_and_the_note_says_there_is_more():
    fake = PagedSerp(first_page(), "T2", {"T2": ([store("Goedkoop.nl", 30.0, shipping_extracted=2.0)], "T3")})
    result = run(fake)
    assert len(fake.calls) == 3  # no third page: each page costs a search
    assert len(result.offers) == 14
    assert serp.MORE_STORES_NOTE in result.notes


def test_a_page_that_costs_more_than_the_budget_allows_is_skipped_with_the_note():
    fake = PagedSerp(first_page(), "T2", {"T2": ([store("Goedkoop.nl", 30.0)], None)})
    asked = []

    def guard(cost):
        asked.append(cost)
        return "Maandbudget bereikt" if len(asked) >= 2 else None

    result = lookup.lookup(EAN, None, source=serp.serp_source(fake, now=lambda: "NOW", guard=guard))
    assert result.status == lookup.OK  # one product: no list, so the guard is asked for both steps at once
    assert len(result.offers) == 13 and "Volgende winkelpagina: niet opgehaald (budget)" in result.steps
    assert serp.MORE_STORES_NOTE in result.notes
    assert [c["engine"] for c in fake.calls] == ["google_shopping", "google_immersive_product"]


def test_a_failing_next_page_keeps_the_first_page_and_says_there_is_more():
    fake = PagedSerp(first_page(), "T2", {})  # the token is unknown: the fake answers a timeout
    result = run(fake)
    assert result.status == lookup.OK and len(result.offers) == 13
    assert any(step.startswith("Winkellijst, pagina 2: geen antwoord") for step in result.steps)
    assert serp.MORE_STORES_NOTE in result.notes


def test_a_shop_on_both_pages_keeps_its_cheapest_total():
    page2 = ([store("Shop0.nl", 35.0, shipping_extracted=0.0)], None)
    result = run(PagedSerp(first_page(), "T2", {"T2": page2}))
    assert len(result.offers) == 13
    assert next(o for o in result.offers if o.shop == "Shop0.nl").price == 35.0


def test_without_a_next_page_token_no_extra_search_is_made():
    fake = PagedSerp(first_page(), None, {})
    result = run(fake)
    assert len(fake.calls) == 2 and serp.MORE_STORES_NOTE not in result.notes


# ------------------------------------------------------------------ a second try on a shorter name

LONG_NAME = "maybelline new york sculpting stix 011 rosy sculpt 2 in 1 eyeliner"
SHORT_NAME = "maybelline new york sculpting stix"


class QuerySerp(FakeSerp):
    """Google answers nothing for the long name and products for the short one."""

    def __init__(self, short_items, **kwargs):
        super().__init__(shopping=[], stores=[], **kwargs)
        self.short_items = short_items

    def __call__(self, params):
        if params["engine"] == "google_shopping":
            self.calls.append(params)
            found = self.short_items if params["q"] == SHORT_NAME else []
            return {"shopping_results": found}, None
        return super().__call__(params)


@pytest.mark.parametrize(
    "query, expected",
    [
        (LONG_NAME, SHORT_NAME),
        ("Kérastase Gloss Absolu Bain Hydra-Glaze Shampoo 500 ml", "Kérastase Gloss Absolu Bain Hydra-Glaze"),
        ("weleda baby shampoo", None),  # already short
        ("weleda baby shampoo 400", "weleda baby shampoo"),  # a bare number is dropped too
        ("a b c d e f", "a b c d e"),
        ("", None),
        (None, None),
    ],
)
def test_shorter(query, expected):
    assert serp.shorter(query) == expected


def test_nothing_found_for_the_long_name_gives_one_more_search_on_a_shorter_name():
    pink = item("Kruidvat.nl", 7.99, product_id="9", title="Maybelline Sculpting Stix 011 Rosy Sculpt eyeliner")
    fake = QuerySerp([item("Etos", 8.49, product_id="8", title="Maybelline Sculpting Stix 02 Mocha"), pink])
    result = run_by_name(fake, name=LONG_NAME)
    assert [c.get("q") for c in fake.calls if c["engine"] == "google_shopping"] == [LONG_NAME, SHORT_NAME]
    assert result.query == SHORT_NAME  # the screen shows what Google was really asked
    assert f"Opnieuw gezocht op een kortere naam: {SHORT_NAME}" in result.steps
    assert serp.SHORTER_QUERY_NOTE.format(SHORT_NAME) in result.notes
    assert result.status == lookup.CHOOSE  # two products; the full name ranks the shade first
    assert result.candidates[0].title.startswith("Maybelline Sculpting Stix 011")
    assert serp.SHORTER_QUERY_NOTE.format(SHORT_NAME) in result.notes


def test_a_single_product_on_the_shorter_name_goes_on_to_its_shops_with_the_note():
    fake = QuerySerp([item("Kruidvat.nl", 7.99, product_id="9", title="Maybelline Sculpting Stix eyeliner")])
    result = run_by_name(fake, name=LONG_NAME)
    assert result.status == lookup.OK and result.query == SHORT_NAME
    assert serp.SHORTER_QUERY_NOTE.format(SHORT_NAME) in result.notes
    assert [c["engine"] for c in fake.calls] == ["google_shopping", "google_shopping", "google_immersive_product"]


def test_nothing_on_the_shorter_name_either_says_both_terms_and_stops():
    fake = QuerySerp([])
    result = run_by_name(fake, name=LONG_NAME)
    assert result.status == lookup.NOT_FOUND and result.query == SHORT_NAME
    assert LONG_NAME in result.message and SHORT_NAME in result.message and "ook niet" in result.message
    assert len(fake.calls) == 2


def test_a_name_that_is_already_short_is_not_searched_twice():
    fake = FakeSerp(shopping=[], stores=[])
    result = run_by_name(fake, name="weleda baby shampoo")
    assert result.status == lookup.NOT_FOUND and len(fake.calls) == 1


def test_a_barcode_that_finds_nothing_is_not_retried():
    fake = FakeSerp(shopping=[], stores=[])
    result = run(fake)
    assert result.status == lookup.NOT_FOUND and len(fake.calls) == 1


def test_the_budget_is_asked_before_the_second_try():
    fake = QuerySerp([item("Kruidvat.nl", 7.99)])
    asked = []

    def guard(cost):
        asked.append(cost)
        return "Maandbudget bereikt" if len(asked) >= 2 else None

    source = serp.serp_source(fake, now=lambda: "NOW", guard=guard, hint=LONG_NAME)
    result = lookup.lookup("", None, source=source, by_name=True)
    assert asked == [2, 2] and result.status == lookup.SOURCE_ERROR and result.message == "Maandbudget bereikt"
    assert len(fake.calls) == 1 and "Kortere zoekterm: niet gezocht (budget)" in result.steps
