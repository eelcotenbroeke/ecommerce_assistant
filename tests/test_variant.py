import pytest

import variant


@pytest.mark.parametrize(
    "text, sizes, count",
    [
        ("Weleda Baby Shampoo 200ml", (("ml", 200.0),), None),
        ("shampoo 400 ml", (("ml", 400.0),), None),
        ("Brita waterfilterkan 2,4l", (("ml", 2400.0),), None),
        ("Omer bier 24 flesjes 33cl", (("ml", 330.0),), None),
        ("Segafredo bonen 1kg", (("g", 1000.0),), None),
        ("Renske vers vlees 10x395gr", (("g", 395.0),), 10),
        ("Renske 10 X 395 Gram", (("g", 395.0),), 10),
        ("Axolotl knijpdier 11cm", (), None),  # centimetres are not a size we compare
        ("Brita 3x maxtra", (), 3),
        ("Björn Borg slips (x3) - multipack", (), 3),
        ("Walkie talkie 2 stuks", (), 2),
        ("Rimmel duopack mascara", (), 2),
        ("LEGO Classic (484st) opbergdoos", (), None),  # pieces, not a pack
        ("1+1 Gratis Rimmel mascara", (), None),  # a promo, not a pack
        ("Kerastase 250ml + fondant 250ml + drops 45ml", (("ml", 250.0), ("ml", 45.0)), None),
        ("", (), None),
        (None, (), None),
    ],
)
def test_read(text, sizes, count):
    spec = variant.read(text)
    assert (spec.sizes, spec.count) == (sizes, count)


def test_another_size_is_a_mismatch():
    warning = variant.mismatch("weleda baby shampoo calendula 400ml", "Weleda Baby Shampoo Calendula 200ml")
    assert warning.startswith("Let op: je zocht 400 ml, het gevonden product noemt 200 ml.")


def test_the_same_size_written_differently_is_no_mismatch():
    assert variant.mismatch("segafredo bonen 1kg", "Segafredo Intermezzo bonen 1000 gram") is None
    assert variant.mismatch("bier 33cl", "Omer bier 330 ml") is None


def test_big_sizes_are_said_in_litres_and_kilos():
    assert "je zocht 2,4 l, het gevonden product noemt 1,5 l" in variant.mismatch("waterkan 2,4l", "waterkan 1,5 liter")
    assert "je zocht 1 kg, het gevonden product noemt 500 g" in variant.mismatch("bonen 1kg", "bonen 500g")


def test_a_title_that_states_no_size_cannot_be_checked():
    assert variant.mismatch("weleda shampoo 400ml", "Weleda Baby Shampoo Calendula") is None
    assert variant.mismatch("", "Weleda 200ml") is None
    assert variant.mismatch("weleda shampoo", "Weleda 200ml") is None


def test_a_set_that_holds_the_size_asked_for_is_not_flagged():
    assert variant.mismatch("kerastase drops 45ml", "Kerastase Bain 250ml + Fondant 250ml + Drops 45ml") is None


def test_another_pack_is_a_mismatch():
    warning = variant.mismatch("renske vers vlees 10x 395gr", "10x Renske vers vlees maaltijd hond 95gr")
    assert "noemt 95 g" in warning  # the size is the first thing that differs
    warning = variant.mismatch("brita maxtra 3x", "4x Brita Maxtra Pro")
    assert "je zocht een verpakking van 3, het gevonden product noemt een verpakking van 4" in warning


def test_a_multipack_asked_for_and_a_single_item_found():
    warning = variant.mismatch("bjorn borg 7x boxershorts", "Bjorn Borg BOXER Boxershort (MP002) M")
    assert "je zocht een verpakking van 7, het gevonden product noemt geen verpakking van meerdere" in warning


def test_a_single_item_asked_for_is_never_a_mismatch_on_count():
    assert variant.mismatch("bjorn borg boxershort", "Bjorn Borg Boxershort 3-pack") is None
    assert variant.mismatch("slip 1x", "slip 3-pack") is None


# ------------------------------------------------------------------ shade, colour and code

WANTED = ["Maybelline Sculpting Stix eyeliner", "maybelline new york sculpting stix 011 rosy sculpt 2 in 1 eyeliner"]
GENERIC = "Maybelline Sculpting Stix Eyeliner"
AMAZON = (
    "Maybelline New York Sculpting Stix 2-in-1 eyeliner, gemakkelijk mengbare looks, tot 24 uur houvast "
    "dankzij contourkleur, kleur: Black Blur"
)


def test_a_shade_code_in_the_link_that_the_found_title_lacks_is_a_requirement():
    assert variant.requirements(WANTED, GENERIC) == ("011",)
    assert variant.requirements(["lego classic 10696"], "10696 LEGO Classic") == ()  # the title has it
    assert variant.requirements(["weleda shampoo 400 ml"], "Weleda shampoo") == ()  # sizes are mismatch's job
    assert variant.requirements(["jas zwart"], "Jas") == ("black",)  # Dutch colour, canonical word
    assert variant.requirements(["magimix keukenmachine"], "Magimix keukenmachine") == ()


def test_another_colour_with_a_label_is_a_different_variant():
    fit = variant.check(WANTED, GENERIC, AMAZON)
    assert fit.state == variant.DIFFERS and fit.note == "Andere variant: kleur Black Blur."


def test_the_variant_that_was_asked_for_is_fine():
    assert variant.check(WANTED, GENERIC, "Maybelline New York Sculpting Stix 011 Rosy Sculpt").state == variant.OK
    assert variant.check(WANTED, GENERIC, "Sculpting Stix eyeliner - kleur: Rosy Sculpt 011").state == variant.OK


def test_a_title_that_does_not_say_the_variant_is_unconfirmed_not_wrong():
    fit = variant.check(WANTED, GENERIC, "Maybelline Sculpting Stix Eyeliner")
    assert fit.state == variant.UNCONFIRMED and fit.note == "Variant niet bevestigd: de titel noemt 011 niet."
    assert variant.check(WANTED, GENERIC, None).state == variant.UNCONFIRMED


def test_nothing_asked_for_means_nothing_to_check():
    assert variant.check(["maybelline sculpting stix eyeliner"], GENERIC, AMAZON).state == variant.OK


def test_dutch_and_english_colours_are_the_same_colour():
    assert variant.check(["shirt zwart"], "Shirt", "Shirt - kleur: Black").state == variant.OK
    assert variant.check(["shirt black"], "Shirt", "Shirt - kleur: Zwart").state == variant.OK
    assert variant.check(["shirt zwart"], "Shirt", "Shirt - kleur: Wit").state == variant.DIFFERS
    assert variant.check(["shirt zwart"], "Shirt", "Shirt rood").note == "Variant niet bevestigd: de titel noemt zwart niet."


def test_another_size_with_a_label_is_a_different_variant_when_a_variant_was_asked_for():
    fit = variant.check(["jas zwart"], "Jas", "Jas, maat: XL")
    assert fit.state == variant.DIFFERS and fit.note == "Andere variant: maat XL."
