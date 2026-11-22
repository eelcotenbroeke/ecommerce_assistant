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


# ------------------------------------------------------------------ Thuiswinkel Waarborg members

import members  # noqa: E402

LIST = members.build(
    [("kruidvat-nl", "kruidvat.nl"), ("trekpleister", "trekpleister.nl"), ("bol", "bol.com"), ("amazon-nl", "amazon.nl")]
)


def shops(res):
    return [o.shop for o in res.offers]


def test_only_members_hides_the_other_shops_and_says_how_many():
    res = result(offers=[offer("Celini.nl", 30.0), offer("Kruidvat.nl", 33.0), offer("Trekpleister", 35.0)])
    shown, hidden = view.apply_member_filter(res, LIST)
    assert hidden == 1
    assert shops(shown) == ["Kruidvat.nl", "Trekpleister"]
    assert shown.recommendation.pick.shop == "Kruidvat.nl"  # not the hidden, cheaper Celini
    assert res.recommendation.pick.shop == "Celini.nl" and len(res.offers) == 3  # the original is untouched
    line = view.hidden_line(hidden)
    assert line.startswith("1 winkel verborgen: niet op de ledenlijst van Thuiswinkel Waarborg")
    assert "19-11-2026" in line
    assert view.hidden_line(3).startswith("3 winkels verborgen")
    assert view.hidden_line(0) is None


def test_the_filter_off_or_nothing_to_hide_changes_nothing():
    res = result(offers=[offer("Celini.nl", 30.0), offer("Kruidvat.nl", 33.0)])
    assert view.apply_member_filter(res, LIST, only_members=False) == (res, 0)
    assert view.apply_member_filter(res, None) == (res, 0)
    members_only = result(offers=[offer("Kruidvat.nl", 33.0), offer("Trekpleister", 35.0)])
    assert view.apply_member_filter(members_only, LIST) == (members_only, 0)
    assert view.apply_member_filter(result(), LIST) == (result(), 0)


def test_bol_and_amazon_stay_even_when_the_list_does_not_know_them():
    empty = members.build([])
    res = result(offers=[offer("bol.com", 40.0), offer("Amazon.nl", 41.0), offer("Celini.nl", 30.0)])
    shown, hidden = view.apply_member_filter(res, empty)
    assert shops(shown) == ["bol.com", "Amazon.nl"] and hidden == 1
    assert shown.recommendation.pick.shop == "bol.com"


def test_the_ten_percent_rule_works_among_the_visible_shops_only():
    res = result(offers=[offer("Celini.nl", 30.0), offer("Kruidvat.nl", 40.0), offer("bol.com", 43.0)])
    assert res.recommendation.pick.shop == "Celini.nl"  # 30,00, far below bol: the plain cheapest
    shown, hidden = view.apply_member_filter(res, LIST)
    # Kruidvat is the cheapest member (40,00); bol (43,00) is 7,5% above it: the owner picks bol
    assert shown.recommendation.pick.shop == "bol.com" and shown.recommendation.reason == "preferred"
    assert shown.recommendation.cheapest.shop == "Kruidvat.nl"


def test_no_member_with_a_deliverable_offer_shows_every_shop_with_a_note():
    res = result(offers=[offer("Celini.nl", 30.0), offer("Flaconi.nl", 31.0), offer("Kruidvat.nl", 33.0, deliverable=False)])
    shown, hidden = view.apply_member_filter(res, LIST)
    assert hidden == 0 and shops(shown) == ["Celini.nl", "Flaconi.nl", "Kruidvat.nl"]
    assert shown.recommendation == res.recommendation and shown.status == lookup.OK
    assert shown.notes[0] == view.NO_MEMBER_NOTE
    nothing, hidden = view.apply_member_filter(result(offers=[offer("Celini.nl", 30.0)]), LIST)
    assert shops(nothing) == ["Celini.nl"] and hidden == 0 and nothing.notes[0] == view.NO_MEMBER_NOTE


def test_a_result_that_was_already_undeliverable_keeps_its_own_message():
    res = result(lookup.UNAVAILABLE, offers=[offer("Celini.nl", 30.0, deliverable=False)], message="Geen leverbaar aanbod.")
    shown, hidden = view.apply_member_filter(res, LIST)
    assert hidden == 1 and shown.message == "Geen leverbaar aanbod."


def test_the_cards_mark_the_members():
    res = result(offers=[offer("Celini.nl", 30.0), offer("Kruidvat.nl", 33.0)])
    cards = {c.shop: c.member for c in view.offer_cards(res, LIST)}
    assert cards == {"Celini.nl": False, "Kruidvat.nl": True}
    assert {c.member for c in view.offer_cards(res)} == {False}  # no list: nothing is claimed
    assert view.MEMBER_BADGE == "Thuiswinkel Waarborg"


# ------------------------------------------------------------------ the variant that was asked for

LINK = "https://www.kruidvat.nl/maybelline-new-york-sculpting-stix-011-rosy-sculpt-2-in-1-eyeliner/p/6428063"


def titled(shop, price, title, shipping=0.0):
    return Offer(shop, price, shipping, None, True, None, title)


def maybelline_result(*offers):
    return result(
        offers=list(offers), product_title="Maybelline Sculpting Stix Eyeliner",
        query="Maybelline Sculpting Stix eyeliner", reference_link=LINK,
    )


def test_what_was_asked_for_is_the_search_term_and_the_words_of_the_link():
    res = maybelline_result(titled("A.nl", 10.0, "x"))
    texts = view.wanted_texts(res)
    assert texts[0] == "Maybelline Sculpting Stix eyeliner" and "011 rosy sculpt" in texts[1]
    assert view.wanted_texts(result()) == []


def test_only_offers_that_are_not_ok_get_a_fit():
    res = maybelline_result(
        titled("Kruidvat.nl", 10.99, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
        titled("Amazon.nl", 6.38, "Maybelline Sculpting Stix, kleur: Black Blur"),
        titled("Etos", 8.0, "Maybelline Sculpting Stix Eyeliner"),
    )
    fits = view.variant_fits(res)
    states = {key[0]: fit.state for key, fit in fits.items()}
    assert states == {"Amazon.nl": "differs", "Etos": "unconfirmed"}
    assert view.variant_fits(result(offers=[offer("A.nl", 10.0)])) == {}  # nothing asked for


def test_an_offer_of_another_variant_is_never_the_recommendation():
    res = maybelline_result(
        titled("Kruidvat.nl", 10.99, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
        titled("Amazon.nl", 6.38, "Maybelline Sculpting Stix, kleur: Black Blur"),
    )
    assert res.recommendation.pick.shop == "Amazon.nl"
    checked = view.apply_variant_check(res, view.variant_fits(res))
    assert checked.recommendation.pick.shop == "Kruidvat.nl"
    assert [o.shop for o in checked.offers] == ["Kruidvat.nl", "Amazon.nl"]  # still listed
    cards = view.offer_cards(checked, None, view.variant_fits(res))
    assert [c.shop for c in cards] == ["Kruidvat.nl", "Amazon.nl"]
    assert cards[1].excluded and cards[1].warning == "Andere variant: kleur Black Blur."
    assert not cards[0].excluded and cards[0].warning is None


def test_when_every_offer_is_another_variant_the_result_is_left_as_it_was():
    res = maybelline_result(titled("Amazon.nl", 6.38, "Maybelline Sculpting Stix, kleur: Black Blur"))
    assert view.apply_variant_check(res, view.variant_fits(res)) is res


def test_an_unconfirmed_cheapest_offer_makes_the_saving_a_maybe():
    res = maybelline_result(
        titled("Kruidvat.nl", 10.99, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
        titled("Etos", 8.0, "Maybelline Sculpting Stix Eyeliner"),
    )
    found = view.verdict_for(res, res, "", view.variant_fits(res))
    assert found.kind == "cheaper" and found.caution == "Variant niet bevestigd: de titel noemt 011 niet."
    assert view.verdict_headline(found) == "Mogelijk goedkoper elders: bespaar € 2,99 (27,2%) bij Etos"
    assert view.verdict_severity(found) == "info"
    assert "Controleer dat voordat je gelooft dat het goedkoper is." in view.verdict_detail(found)


def test_a_confirmed_saving_has_no_caution():
    res = maybelline_result(
        titled("Kruidvat.nl", 10.99, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
        titled("Etos", 8.0, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
    )
    found = view.verdict_for(res, res, "", view.variant_fits(res))
    assert found.caution is None and view.verdict_severity(found) == "warning"


def test_the_headline_box_is_redundant_only_when_the_verdict_names_the_same_shop():
    res = maybelline_result(
        titled("Kruidvat.nl", 10.99, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
        titled("Etos", 8.0, "Maybelline Sculpting Stix 011 Rosy Sculpt"),
    )
    found = view.verdict_for(res, res)
    assert view.headline_is_redundant(found)
    assert not view.headline_is_redundant(None)
    preferred = result(offers=[offer("Kruidvat.nl", 45.0), offer("Etos", 40.0), offer("bol.com", 42.0)], reference_link="https://www.kruidvat.nl/p/shampoo-test/1")
    assert not view.headline_is_redundant(view.verdict_for(preferred, preferred))  # the pick is bol, not the cheapest


# ------------------------------------------------------------------ only the close products are listed


class Scored:
    def __init__(self, score):
        self.score = score


@pytest.mark.parametrize(
    "scores, expected",
    [
        ([1.0, 0.9, 0.7, 0.3], 2),  # within 0.25 of the best: floor 0.75
        ([0.75, 0.5, 0.5, 0.25], 3),  # the floor is 0.5 however low the best is
        ([0.6, 0.5, 0.2], 2),
        ([0.4, 0.3, 0.2], 3),  # nothing reaches 0.5: the best few, never an empty list
        ([0.4, 0.35, 0.3, 0.2, 0.1, 0.05, 0.0], 5),
        ([0.0, 0.0, 0.0], 3),  # no name to compare with: everything is shown
        ([1.0], 1),
        ([], 0),
    ],
)
def test_close_count(scores, expected):
    assert view.close_count([Scored(s) for s in scores]) == expected


def test_a_score_a_hair_under_the_margin_still_counts_as_close():
    assert view.close_count([Scored(1.0), Scored(0.75 - 1e-12), Scored(0.2)]) == 2
