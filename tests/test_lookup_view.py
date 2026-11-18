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
        (lookup.UNAVAILABLE, "warning"),
        (lookup.NEEDS_EAN, "warning"),
        (lookup.NOT_FOUND, "warning"),
        (lookup.NO_OFFERS, "warning"),
        (lookup.INVALID_INPUT, "warning"),
        (lookup.BLOCKED, "error"),
        (lookup.MISMATCH, "error"),
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


def test_offer_rows_order_and_columns():
    res = result(
        offers=[
            offer("Ghost.nl", 20.0, deliverable=False, delivery="Niet leverbaar"),
            offer("Celini.nl", 35.28, 3.95, delivery="1-2 dagen"),
            offer("Flaconi.nl", 38.63, 0.0, delivery="2-3 dagen"),
            offer("bol.com", 44.18, 3.95),
        ]
    )
    rows = view.offer_rows(res)
    assert [r["Winkel"] for r in rows] == ["Flaconi.nl", "Celini.nl", "bol.com", "Ghost.nl"]
    assert rows[0] == {
        "Winkel": "Flaconi.nl",
        "Totaal": "€ 38,63",
        "Prijs": "€ 38,63",
        "Verzending": "gratis",
        "Levering": "2-3 dagen",
        "Leverbaar": "ja",
    }
    assert rows[1]["Verzending"] == "€ 3,95" and rows[1]["Totaal"] == "€ 39,23"
    # bol shows 3,95 on the page but delivery is free for the owner
    assert rows[2]["Verzending"] == "gratis (voor jou)" and rows[2]["Totaal"] == "€ 44,18"
    assert rows[3]["Leverbaar"] == "nee" and rows[3]["Levering"] == "Niet leverbaar"


def test_unknown_shipping_is_shown_as_unknown():
    res = result(offers=[Offer("Shop", 30.0, None, None, True, None)])
    row = view.offer_rows(res)[0]
    assert row["Verzending"] == "onbekend"
    assert row["Totaal"] == "€ 30,00 + verzending?"
    assert row["Levering"] == "onbekend"
