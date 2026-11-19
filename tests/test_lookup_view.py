from datetime import datetime

import pytest

import lookup
import lookup_view as view
from beslist import Offer
from choice import recommend


def offer(shop, price, shipping=0.0, deliverable=True, delivery="1 dag"):
    return Offer(shop, price, shipping, delivery, deliverable, None)


def result(status=lookup.OK, offers=(), message="msg", **fields):
    fields.setdefault("recommendation", recommend(offers) if offers else None)
    fields.setdefault("read_at", datetime(2026, 11, 18, 12, 30))
    return lookup.LookupResult(status, message, offers=tuple(offers), **fields)


@pytest.mark.parametrize(
    "value, expected",
    [(38.63, "€ 38,63"), (0, "€ 0,00"), (1234.5, "€ 1.234,50"), (5, "€ 5,00")],
)
def test_eur(value, expected):
    assert view.eur(value) == expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (lookup.OK, "success"),
        (lookup.CHOOSE, "info"),
        (lookup.UNAVAILABLE, "warning"),
        (lookup.NEEDS_EAN, "warning"),
        (lookup.NOT_FOUND, "warning"),
        (lookup.NO_OFFERS, "warning"),
        (lookup.INVALID_INPUT, "warning"),
        (lookup.BLOCKED, "error"),
        (lookup.MISMATCH, "error"),
        (lookup.SOURCE_ERROR, "error"),
    ],
)
def test_severity(status, expected):
    assert view.severity(result(status)) == expected


def test_headline_and_explanation_for_the_cheapest():
    res = result(offers=[offer("Flaconi.nl", 38.63), offer("Celini.nl", 35.28, 3.95)])
    assert view.headline(res) == "Flaconi.nl: € 38,63"
    assert view.explanation(res) == "De goedkoopste leverbare optie, inclusief verzending."


def test_explanation_for_a_preferred_shop_names_the_extra_cost():
    res = result(offers=[offer("Flaconi.nl", 38.63), offer("bol.com", 42.0)])
    assert view.headline(res) == "bol.com: € 42,00"
    text = view.explanation(res)
    assert "€ 3,37 (8,7%) duurder dan de goedkoopste (Flaconi.nl, € 38,63)" in text
    assert "gratis" in text


def test_percentage_uses_a_decimal_comma():
    res = result(offers=[offer("Shop", 40.0), offer("bol.com", 43.0)])
    assert "(7,5%)" in view.explanation(res)


def test_no_recommendation_gives_no_headline():
    res = result(lookup.UNAVAILABLE, offers=[offer("Ghost", 5.0, deliverable=False)])
    assert view.headline(res) is None and view.explanation(res) is None


def test_read_note_says_snapshot_and_when():
    note = view.read_note(result())
    assert "18-11-2026 om 12:30" in note and "momentopname" in note
    assert view.read_note(result(read_at=None)) is None


def test_hint_for_multipack_only_when_the_lookup_failed():
    failed = result(lookup.NOT_FOUND, child_gtins=("5410091779108",))
    assert "5410091779108" in view.hint(failed)
    worked = result(offers=[offer("Shop", 10.0)], child_gtins=("5410091779108",))
    assert view.hint(worked) is None
    assert view.hint(result(lookup.NOT_FOUND)) is None


def test_offer_cards_order_badges_and_lines():
    res = result(
        offers=[
            offer("Ghost.nl", 20.0, deliverable=False, delivery="Niet leverbaar"),
            offer("Celini.nl", 35.28, 3.95, delivery="1-2 dagen"),
            offer("Flaconi.nl", 38.63, 0.0, delivery="2-3 dagen"),
            offer("bol.com", 40.0, 3.95),
        ]
    )
    cards = view.offer_cards(res)
    # bol (40,00) is under 10% above Celini (39,23): it is recommended and listed first
    assert [c.shop for c in cards] == ["bol.com", "Flaconi.nl", "Celini.nl", "Ghost.nl"]
    assert [c.badge for c in cards] == ["Aanbevolen", "Goedkoopste", None, None]
    assert cards[0].total == "\u20ac 40,00"
    # the page says 3,95 shipping for bol, but delivery is free for the owner
    assert cards[0].line == "Prijs \u20ac 40,00 \u00b7 verzending gratis (voor jou) \u00b7 levering 1 dag"
    assert cards[1].line == "Prijs \u20ac 38,63 \u00b7 verzending gratis \u00b7 levering 2-3 dagen"
    assert cards[2].line == "Prijs \u20ac 35,28 \u00b7 verzending \u20ac 3,95 \u00b7 levering 1-2 dagen"
    assert cards[2].total == "\u20ac 39,23"
    assert cards[3].line.startswith("Niet leverbaar \u00b7 Prijs \u20ac 20,00")
    assert [c.deliverable for c in cards] == [True, True, True, False]


def test_the_cheapest_is_first_and_marked_when_it_is_also_the_pick():
    res = result(offers=[offer("Flaconi.nl", 38.63), offer("Celini.nl", 35.28, 0.0)])
    cards = view.offer_cards(res)
    assert [c.shop for c in cards] == ["Celini.nl", "Flaconi.nl"]
    assert [c.badge for c in cards] == ["Aanbevolen", None]


def test_unknown_shipping_is_shown_as_unknown():
    res = result(offers=[Offer("Shop", 30.0, None, None, True, None)])
    card = view.offer_cards(res)[0]
    assert card.line == "Prijs \u20ac 30,00 \u00b7 verzending onbekend"
    assert card.total == "\u20ac 30,00 + verzending?"


def test_offer_card_carries_title_details_and_only_web_links():
    linked = Offer(
        "Flaconi.nl", 38.63, 0.0, "1 dag", True, "https://flaconi.nl/x",
        "Flaconi Kerastase Gloss 500 ml", ("\u2605 4,5 (12 reviews)", "Op voorraad"),
    )
    card = view.offer_cards(result(offers=[linked]))[0]
    assert card.url == "https://flaconi.nl/x"
    assert card.title == "Flaconi Kerastase Gloss 500 ml"
    assert card.details == ("\u2605 4,5 (12 reviews)", "Op voorraad")
    for url in (None, "", "javascript:alert(1)", "/relatief"):
        bad = Offer("Flaconi.nl", 38.63, 0.0, "1 dag", True, url)
        assert view.offer_cards(result(offers=[bad]))[0].url is None


def test_shop_title_is_left_out_when_it_is_the_product_title():
    same = Offer("A", 10.0, 0.0, None, True, None, "K\u00e9rastase  Gloss, 500 ml")
    other = Offer("B", 11.0, 0.0, None, True, None, "Gloss Absolu navulling")
    res = result(offers=[same, other], product_title="Kerastase Gloss 500 ML")
    # accents, case, spaces and punctuation do not make a title "different"
    assert [c.title for c in view.offer_cards(res)] == [None, "Gloss Absolu navulling"]
def test_source_line_names_the_searched_ean_and_the_source():
    res = result(source="Google Shopping", ean="3474637283414", product_title="Shampoo 500 ml")
    assert view.source_line(res) == "Gezocht op EAN 3474637283414 \u00b7 Bron: Google Shopping"
    assert view.source_line(result(source="Beslist")) == "Bron: Beslist"
    assert view.source_line(result(ean="3474637283414")) == "Gezocht op EAN 3474637283414"
    assert view.source_line(result()) is None


def test_product_line_names_the_found_product():
    res = result(source="Google Shopping", product_title="Shampoo 500 ml")
    assert view.product_line(res) == "Product volgens Google Shopping: **Shampoo 500 ml**"
    assert view.product_line(result(product_title="Shampoo")) == "Product: **Shampoo**"
    assert view.product_line(result(source="Google Shopping")) is None


def test_product_link_label_follows_the_source():
    assert view.product_link_label(result(source="Google Shopping")) == "Bekijk op Google Shopping"
    assert view.product_link_label(result(source="Beslist")) == "Bekijk op Beslist"
    assert view.product_link_label(result()) == "Bekijk product"


def test_candidate_cards_show_shops_and_price_range():
    import serp

    def cand(key, shops, low, high, thumbnail=None):
        return serp.Candidate(key, f"Product {key}", "tok", thumbnail, shops, low, high, None, 0)

    cands = (
        cand("1", ("A.nl",), 35.28, 35.28, "https://img.example/1.jpg"),
        cand("2", ("A.nl", "B.nl"), 4.69, 12.03, "javascript:alert(1)"),
        cand("3", (), None, None),
    )
    cards = view.candidate_cards(result(lookup.CHOOSE, candidates=cands))
    assert [c.title for c in cards] == ["Product 1", "Product 2", "Product 3"]
    assert [c.line for c in cards] == [
        "1 winkel \u00b7 \u20ac 35,28",
        "2 winkels \u00b7 \u20ac 4,69 \u2013 \u20ac 12,03",
        "",
    ]
    assert [c.thumbnail for c in cards] == ["https://img.example/1.jpg", None, None]


def test_source_line_names_the_search_text_only_when_it_was_not_the_ean():
    plain = result(ean="3474637283414", source="Google Shopping")
    assert view.source_line(plain) == "Gezocht op EAN 3474637283414 \u00b7 Bron: Google Shopping"
    named = result(ean="3474637283414", source="Google Shopping", query="K\u00e9rastase Gloss Absolu")
    assert view.source_line(named) == (
        "Gezocht op EAN 3474637283414 \u00b7 Zoekterm bij Google: K\u00e9rastase Gloss Absolu \u00b7 Bron: Google Shopping"
    )
