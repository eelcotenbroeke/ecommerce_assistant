import json
from datetime import datetime

import pytest

import benchmark
import lookup
import serp
from beslist import Offer
from choice import recommend
from serp_pages import item

TABLE = (
    "nr\tcategorie\turl\tzoekwoorden\tproduct\n"
    "1\thuishouden,wonen\thttps://www.magimix.nl/x.html\tmagimix keukenmachine \tmagimix cs 3200\n"
    "2\tbeauty_gezondheid\thttps://www.kruidvat.nl/p/1\tweleda baby shmapoo\tweleda baby shampoo calendula 400ml\n"
    "3\telectronica\thttps://www.bol.com/nl/nl/p/logitech/9300000236202343/\t\tlogitech mx master\n"
)


# ------------------------------------------------------------------ the table


def test_a_table_pasted_from_a_spreadsheet_is_read_with_its_header():
    rows, problems = benchmark.parse_table(TABLE)
    assert problems == []
    assert [r.nr for r in rows] == [1, 2, 3]
    assert rows[0].categorie == "huishouden,wonen"  # the comma stays: the delimiter is the tab
    assert rows[0].zoekwoorden == "magimix keukenmachine"  # trailing spaces are dropped
    assert rows[2].zoekwoorden == "" and rows[2].url.startswith("https://www.bol.com")
    assert [r.holdout for r in rows] == [False, True, False]  # even numbers are the verdict set


def test_without_a_header_the_columns_are_taken_in_the_agreed_order():
    rows, problems = benchmark.parse_table("7\tdieren\thttps://www.huisdieren.nl/shop/x/\trenske vlees\trenske vers vlees 10x 395gr")
    assert problems == [] and rows[0].nr == 7 and rows[0].product == "renske vers vlees 10x 395gr"


def test_a_semicolon_table_works_too():
    rows, _ = benchmark.parse_table("nr;categorie;url;zoekwoorden;product\n4;sport;https://x.nl/p;adidas tas;adidas tas 30l")
    assert rows[0].nr == 4 and rows[0].zoekwoorden == "adidas tas"


def test_header_with_a_missing_column_is_refused():
    rows, problems = benchmark.parse_table("nr\turl\tzoekwoorden\n1\thttps://x.nl\tjas")
    assert rows == [] and "product" in problems[0]


def test_bad_rows_are_reported_not_hidden():
    text = "nr\tcategorie\turl\tzoekwoorden\tproduct\nx\tdieren\t\tkat\tkattenbak\n5\tdieren\t\t\tleeg\n6\tdieren\thttps://x.nl\t\tlink\n6\tdieren\thttps://y.nl\ty\ty\n"
    rows, problems = benchmark.parse_table(text)
    joined = " ".join(problems)
    assert [r.nr for r in rows] == [1, 6, 6]  # the row without a number takes its position
    assert "geen nummer" in joined and "Nummer 5: geen link en geen zoekwoorden" in joined and "Nummer 6 komt twee keer" in joined


def test_an_empty_table_says_so():
    assert benchmark.parse_table("  \n")[1] == ["De tabel is leeg."]
    assert benchmark.parse_table(None)[0] == []


# ------------------------------------------------------------------ the plan


def test_keyword_runs_come_first_and_empty_inputs_are_skipped():
    rows, _ = benchmark.parse_table(TABLE)
    plan = benchmark.tasks(rows)
    assert [(r.nr, mode) for r, mode in plan] == [
        (1, "zoekwoorden"), (2, "zoekwoorden"), (1, "link"), (2, "link"), (3, "link"),
    ]


def test_the_plan_can_be_limited_to_the_verdict_or_development_set_and_to_one_kind_of_run():
    rows, _ = benchmark.parse_table(TABLE)
    assert [(r.nr, m) for r, m in benchmark.tasks(rows, benchmark.EVEN)] == [(2, "zoekwoorden"), (2, "link")]
    assert [(r.nr, m) for r, m in benchmark.tasks(rows, benchmark.ODD, links=False)] == [(1, "zoekwoorden")]
    assert [(r.nr, m) for r, m in benchmark.tasks(rows, keywords=False)] == [(1, "link"), (2, "link"), (3, "link")]


def test_the_search_budget_is_counted_at_most():
    rows, _ = benchmark.parse_table(TABLE)
    # 2 keyword runs (2 each) + the bol link (2: its page might be readable); magimix and kruidvat links cost nothing
    assert benchmark.max_searches(benchmark.tasks(rows)) == 9
    assert benchmark.max_searches(benchmark.tasks(rows, links=False)) == 6


def test_a_link_with_a_product_name_in_its_words_costs_searches_too():
    rows, _ = benchmark.parse_table("1\tx\thttps://www.haibu.nl/kerastase-gloss-absolu-drops-45ml.html\t\tdrops")
    assert benchmark.max_searches(benchmark.tasks(rows)) == 3  # its name is searched


# ------------------------------------------------------------------ tapping


def candidates(*titles):
    items = [item("Shop.nl", 10.0 + i, product_id=str(i), title=title, token=f"t{i}") for i, title in enumerate(titles)]
    return tuple(serp.candidates(items))


def test_the_candidate_that_matches_the_product_is_tapped():
    cands = candidates("Kérastase Gloss Absolu Masque 200 ml", "Kérastase Gloss Absolu Drops 45 ml", "Kérastase Nutritive")
    pick = benchmark.pick_candidate(cands, "kerastase gloss absolu drops 45ml")
    assert pick.candidate.title.endswith("Drops 45 ml") and pick.place == 2 and not pick.doubtful


def test_of_equal_matches_the_tightest_title_wins():
    cands = candidates("Logitech MX Master 3S draadloze muis grafiet zwart met usb ontvanger en extra", "Logitech MX Master 3S draadloze muis")
    pick = benchmark.pick_candidate(cands, "logitech mx master draadloze muis")
    assert pick.candidate.title == "Logitech MX Master 3S draadloze muis" and pick.place == 2


def test_a_weak_match_is_still_tapped_but_marked_doubtful():
    pick = benchmark.pick_candidate(candidates("Heel iets anders", "Nog iets"), "logitech mx master draadloze muis")
    assert pick is not None and pick.score < benchmark.MIN_PICK_SCORE and pick.doubtful
    assert benchmark.pick_candidate((), "x") is None


# ------------------------------------------------------------------ records

ROW = benchmark.Row(10, "huishouden", "https://www.kruidvat.nl/p/1", "brita waterfilter kan", "brita waterfilterkan essential 2,4l")
NOW = datetime(2026, 11, 19, 16, 0, 0)


def offers():
    return (
        Offer("Kruidvat.nl", 17.99, None, None, True, "https://www.kruidvat.nl/p", "Brita kan"),  # shipping unknown
        Offer("Brita-shop.nl", 19.95, 3.95, "morgen", True, None, None),
        Offer("bol.com", 19.0, 4.0, None, True, None, None),  # free for the owner: effective shipping 0
        Offer("Weg.nl", 9.0, 0.0, None, False, None, None),  # not deliverable: never the cheapest
    )


def worked_result():
    found = offers()
    return lookup.LookupResult(
        lookup.OK, "Aanbiedingen gelezen.", steps=("Google Shopping: 40 resultaten",), source="Google Shopping",
        product_title="Brita waterfilterkan Style Essential 2,4 l", offers=found, recommendation=recommend(found),
        read_at=NOW, query="brita waterfilter kan",
    )


def test_a_record_holds_both_cheapest_totals_and_the_recommendation():
    rec = benchmark.record(ROW, "zoekwoorden", worked_result(), searches=2, seconds=7.26)
    assert rec["holdout"] is True and rec["soort"] == "zoekwoorden" and rec["invoer"] == "brita waterfilter kan"
    assert rec["aantal_winkels"] == 4 and rec["onbekende_verzending"] == 1
    # unknown shipping counted as 0 makes Kruidvat the cheapest; with known shipping only it is bol (free for the owner)
    assert rec["goedkoopste_onbekend_nul"] == {"winkel": "Kruidvat.nl", "totaal": 17.99}
    assert rec["goedkoopste_bekend"] == {"winkel": "bol.com", "totaal": 19.0}
    bol = next(o for o in rec["winkels"] if o["winkel"] == "bol.com")
    assert bol["verzending"] == 4.0 and bol["verzending_effectief"] == 0.0 and bol["voorkeurswinkel"] is True
    kruidvat = next(o for o in rec["winkels"] if o["winkel"] == "Kruidvat.nl")
    assert kruidvat["verzending_bekend"] is False and kruidvat["totaal_bekend"] is None and kruidvat["totaal_onbekend_nul"] == 17.99
    assert rec["aanbevolen"]["winkel"] == "bol.com" and rec["aanbevolen"]["reden"] == "preferred" and rec["aanbevolen"]["extra"] == 1.01
    assert rec["gelezen"] == "2026-11-19T16:00:00" and rec["seconden"] == 7.3 and rec["zoekopdrachten"] == 2
    assert rec["overeenkomst_met_product"] == pytest.approx(0.8)
    json.dumps(rec)  # everything in it can be written to the file


def test_a_record_after_a_list_keeps_the_candidates_and_the_tap():
    cands = candidates("Brita waterfilterkan Style Essential 2,4 l 3x Maxtra", "Brita Marella 2,4 l")
    choose = lookup.LookupResult(lookup.CHOOSE, "Kies", candidates=cands, query="brita waterfilter kan")
    pick = benchmark.pick_candidate(cands, ROW.product)
    rec = benchmark.record(ROW, "zoekwoorden", worked_result(), choose=choose, pick=pick)
    assert rec["lijst_getoond"] is True and [c["positie_google"] for c in rec["lijst"]] == [1, 2]
    assert rec["gekozen"]["plaats_in_lijst"] == 1 and rec["gekozen"]["twijfel"] is False
    json.dumps(rec)


def test_a_failed_run_is_a_record_too():
    result = lookup.LookupResult(lookup.NEEDS_EAN, "bol.com gaf HTTP 403.", steps=("bol-pagina: HTTP 403, 12 tekens",))
    rec = benchmark.record(ROW, "link", result, raw=[{"aanroep": {}}])
    assert rec["status"] == "needs_ean" and rec["melding"].startswith("bol.com gaf HTTP 403")
    assert rec["aantal_winkels"] == 0 and rec["goedkoopste_onbekend_nul"] is None and rec["aanbevolen"] is None
    assert rec["gekozen"] is None and rec["lijst_getoond"] is False and rec["ruwe_antwoorden"] == [{"aanroep": {}}]
    assert rec["overeenkomst_met_product"] is None


def test_summary_rows_are_short_and_the_document_has_the_rules():
    rec = benchmark.record(ROW, "zoekwoorden", worked_result())
    row = benchmark.summary_rows([rec])[0]
    assert row["goedkoopste"] == "Kruidvat.nl 17,99" and row["aanbevolen"] == "bol.com 19,00" and row["winkels"] == 4
    document = benchmark.results_document([rec], {"gemaakt": "nu"})
    assert document["meta"] == {"gemaakt": "nu"} and document["regels"] == [rec]
