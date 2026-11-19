import csv
import json
from collections import Counter

import pytest

import categorize_members
import categories
import members


def test_fold_removes_accents_and_case():
    assert categories.fold("Prénatal CAFÉ") == "prenatal cafe"
    assert categories.fold(None) == ""


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Webwinkel voor fietsen, e-bikes en fietsaccessoires", "auto_fiets_motor"),
        ("Specialist in tuinmeubelen, parasols en barbecues", "tuin_klussen"),
        ("Gespecialiseerd in laptops, tablets en printers", "elektronica"),
        ("Dames- en herenmode en sneakers van bekende merken", "mode"),
        ("Verkoop van wijn, champagne en andere dranken", "eten_drinken"),
        ("Alles voor je hond en kat: voer, manden en speelgoed voor huisdieren", "dieren"),
        ("Online bank- en gordijnenzaak met meubelen en dekbedden", "wonen"),
        ("Vergelijkingssite voor energie en een online datingsite voor singles", "diensten"),
        ("Gouden en zilveren sieraden en horloges", "sieraden"),
    ],
)
def test_a_clear_description_gets_its_category_first(description, expected):
    assert categories.classify(description=description)[0] == expected


def test_a_compound_ending_in_mode_is_fashion_but_modern_is_not():
    assert categories.classify(description="Onze mannenmode en damesmode voor elk seizoen")[0] == "mode"
    assert "mode" not in categories.classify(description="Een modern en strak model voor in de woonkamer")


def test_gouda_cheese_is_not_gold_and_a_fitting_insurance_is_not_an_insurer():
    assert categories.classify(description="Wij zijn gek op Goudse kaas") == ("eten_drinken",)
    assert categories.classify(description="Gordijnen op maat, met gratis inmeetverzekering")[0] == "wonen"
    assert categories.classify(slug="combi-motors-verzekering") == ("diensten",)


def test_the_slug_and_name_count_when_the_description_is_empty():
    assert categories.classify(slug="voetbalshop-nl") == ("sport_outdoor",)
    assert categories.classify(name="Voipshop.nl") == ("elektronica",)
    assert categories.classify(name="Prénatal", description="") == ("baby_speelgoed",)


def test_a_description_that_says_nothing_is_overig():
    assert categories.classify(description="Altijd iets bijzonders") == (categories.OVERIG,)
    assert categories.classify() == (categories.OVERIG,)
    assert categories.classify(None, None, None) == (categories.OVERIG,)


def test_a_second_category_needs_two_keywords_and_there_are_never_three():
    two = categories.classify(description="Kinderkleding en kinderschoenen, speelgoed en puzzels")
    assert set(two) == {"mode", "baby_speelgoed"}
    one = categories.classify(description="Babykleding en speelgoed voor kinderen")
    assert one[0] == "baby_speelgoed" and len(one) == 1  # fashion has only one keyword here
    many = categories.classify(
        description="Laptops, printers, tuinmeubelen, parasols, kleding, schoenen, wijn, champagne, honden, katten"
    )
    assert len(many) == categories.MAX_CATEGORIES


def test_every_rule_has_a_label_and_a_valid_pattern():
    assert [code for code, _label, _patterns in categories.RULES] == [
        code for code in categories.LABELS if code != categories.OVERIG
    ]
    assert categories.LABELS[categories.OVERIG] == "Overig"
    for _code, _label, patterns in categories.RULES:
        assert patterns  # compiled at import: an invalid regex would have failed there


def test_categorize_rows_adds_a_column_and_uses_the_slug_when_there_is_no_description():
    rows = [
        {"webshop_slug": "bakker-fietsen", "domain": "bakkerfietsen.nl"},
        {"webshop_slug": "voetbalshop-nl", "domain": "voetbalshop.nl"},
    ]
    done = categories.categorize_rows(rows, {"bakker-fietsen": ["Bakker", "Fietsen en onderdelen"]})
    assert [r["categorie"] for r in done] == ["auto_fiets_motor", "sport_outdoor"]
    assert rows[0].keys() == {"webshop_slug", "domain"}  # the input is not changed


def test_the_script_rewrites_the_csv_with_a_categorie_column(tmp_path):
    path = tmp_path / "leden.csv"
    path.write_text("webshop_slug,domain\nvoetbalshop-nl,voetbalshop.nl\nx-b-v,x.nl\n", encoding="utf-8")
    data = tmp_path / "omschrijvingen.json"
    data.write_text(json.dumps({"leden": {"x-b-v": ["X", "Wij verkopen koffie en thee"]}}), encoding="utf-8")
    assert categorize_members.rewrite(path, data) == 2
    assert path.read_text(encoding="utf-8").splitlines() == [
        "webshop_slug,domain,categorie",
        "voetbalshop-nl,voetbalshop.nl,sport_outdoor",
        "x-b-v,x.nl,eten_drinken",
    ]
    assert categorize_members.main(["categorize_members.py"]) == 2  # needs the JSON file


# ------------------------------------------------------------------ the committed list


@pytest.fixture(scope="module")
def committed():
    with open(members.LIST_PATH, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_every_member_has_valid_categories_and_few_are_overig(committed):
    assert len(committed) == 1169
    valid = set(categories.LABELS)
    for row in committed:
        codes = row["categorie"].split("|")
        assert 1 <= len(codes) <= categories.MAX_CATEGORIES and set(codes) <= valid, row
    first = Counter(row["categorie"].split("|")[0] for row in committed)
    assert first[categories.OVERIG] / len(committed) < 0.15
    assert all(first[code] >= 5 for code, _label, _p in categories.RULES)  # no rule is dead


def test_well_known_shops_land_where_you_expect(committed):
    by_slug = {row["webshop_slug"]: row["categorie"].split("|") for row in committed}
    assert by_slug["ah-nl"][0] == "eten_drinken"
    assert by_slug["jumbo-com"][0] == "eten_drinken"
    assert by_slug["mediamarkt-nl"][0] == "elektronica"
    assert by_slug["zalando-nl"][0] == "mode"
    assert by_slug["etos"][0] == "beauty_gezondheid"
    assert by_slug["bol"] == ["algemeen"]
    assert by_slug["wehkamp-retail-group-b-v"] == ["algemeen"]
    assert by_slug["centraal-beheer"] == ["diensten"]


def test_the_member_list_knows_the_categories_of_a_shop():
    listed = members.load()
    assert listed.categories_of("Albert Heijn") == ("eten_drinken",)
    assert listed.categories_of("Celini.nl") == ()  # not a member: nothing to say
    assert "ah.nl" in listed.in_category("eten_drinken")
    assert "zalando.nl" not in listed.in_category("eten_drinken")


def test_build_reads_the_category_column_and_keeps_the_first_of_a_duplicate_domain():
    built = members.build(
        [
            ("a", "a.nl", "mode|baby_speelgoed"),
            ("b", "b.nl", ""),
            ("c", "c.nl"),
            ("a2", "a.nl", "elektronica"),
        ]
    )
    assert built.categories == {"a.nl": ("mode", "baby_speelgoed")}
    assert built.categories_of("a.nl") == ("mode", "baby_speelgoed")
    assert built.categories_of("b.nl") == () and built.categories_of("c.nl") == ()
    assert built.in_category("mode") == {"a.nl"}
