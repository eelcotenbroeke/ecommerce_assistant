"""Tests for ean.py. HTML fixtures are small copies of the shapes seen on live bol.com and
amazon.nl product pages (checked 2026-11-18); the barcodes are real, public product identifiers."""
import pytest

import ean
from ean import ProductIds, ShopRef

# ----------------------------------------------------------------------------- fixtures

BOL_PERSIL_URL = (
    "https://www.bol.com/nl/nl/p/persil-sensitive-gel-vloeibaar-wasmiddel-alle-kleuren-"
    "was-allergie-vriendelijk-voordeelverpakking-6x-18-wasbeurten/9300000262822725/"
    "?cid=1794993459810-5160453650043"
)

BOL_JSONLD = """
<script type="application/ld+json">{"@context":"https://schema.org","@type":"ProductGroup",
"name":"Persil Sensitive","hasVariant":[{"@type":"Product","name":"Persil Sensitive Gel 6x18",
"brand":{"@type":"Brand","name":"Persil"},"gtin13":"5412530956163",
"weight":{"@type":"QuantitativeValue","value":5400,"unitCode":"GRM"}}]}</script>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
"""

BOL_SPECS = """
<dl>
 <div><dt>EAN</dt><dd><span>5412530956163</span></dd></div>
 <div><dt>Child GTIN</dt><dd><span>5410091779108</span></dd></div>
</dl>
"""

AMAZON_TABLE = """
<table><tr>
 <td class="a-span3"><span class="a-size-base a-text-bold">Global Trade Identification Number</span></td>
 <td class="a-span9" role="presentation"><span class="a-size-base po-break-word">08711577110086</span></td>
</tr></table>
"""

AMAZON_BULLET = (
    '<li><span class="a-text-bold">Global Trade Identification Number&rlm; : &lrm;</span>'
    "<span>08711577110086</span></li>"
)


# ----------------------------------------------------------------------------- normalize_gtin


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5412530956163", "5412530956163"),  # EAN-13
        ("5412 5309 56163", "5412530956163"),  # spaces ignored
        ("5412-5309-56163", "5412530956163"),  # hyphens ignored
        ("08711577110086", "8711577110086"),  # GTIN-14 with leading zero -> EAN-13
        ("036000291452", "0036000291452"),  # UPC-A -> padded to 13
        ("96385074", "96385074"),  # EAN-8 stays
        ("\u200e8711577110086\u200f", "8711577110086"),  # direction marks (seen on Amazon)
        (8711577110086, "8711577110086"),  # int input
    ],
)
def test_normalize_gtin_accepts(raw, expected):
    assert ean.normalize_gtin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "5412530956164",  # wrong check digit
        "541253095616",  # wrong length (12 digits but invalid check digit)
        "12345",  # too short
        "54125309561630000",  # too long
        "ABC1234567890",  # not digits
        "",
        None,
    ],
)
def test_normalize_gtin_rejects(raw):
    assert ean.normalize_gtin(raw) is None


# ----------------------------------------------------------------------------- parse_product_url


@pytest.mark.parametrize(
    "url, expected",
    [
        (BOL_PERSIL_URL, ShopRef("bol", "9300000262822725")),
        (
            "https://www.bol.com/nl/nl/p/weleda-calendula-babyshampoo-douchecreme/9200000011686582/",
            ShopRef("bol", "9200000011686582"),
        ),
        (
            "https://www.amazon.nl/dp/B01M635J8I?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1",
            ShopRef("amazon", "B01M635J8I"),
        ),
        (
            "https://www.amazon.nl/HG-spray-zilvervisjes/dp/B01M635J8I/ref=sr_1_1",
            ShopRef("amazon", "B01M635J8I"),
        ),
        ("https://www.amazon.de/gp/product/b01m635j8i", ShopRef("amazon", "B01M635J8I")),
        ("www.bol.com/nl/nl/p/x-y/9200000011686582/", ShopRef("bol", "9200000011686582")),
    ],
)
def test_parse_product_url_recognised(url, expected):
    assert ean.parse_product_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bol.com/nl/nl/account/bestellingen/details/C000HFM6NX/?order_item_id=1",
        "https://www.bol.com/nl/nl/s/?searchtext=persil",
        "https://www.amazon.nl/s?k=persil",
        "https://www.kruidvat.nl/persil-sensitive/p/12345",
        "https://notamazon.example.com/dp/B01M635J8I",
        "not a url",
        "",
        None,
    ],
)
def test_parse_product_url_not_a_product_page(url):
    assert ean.parse_product_url(url) is None


# ----------------------------------------------------------------------------- extract_ids


def test_bol_page_prefers_jsonld_and_finds_child_gtin():
    ids = ean.extract_ids(f"<html><head>{BOL_JSONLD}</head><body>{BOL_SPECS}</body></html>")
    assert ids == ProductIds("5412530956163", ("5410091779108",), "json-ld")


def test_bol_page_falls_back_to_spec_table_without_jsonld():
    ids = ean.extract_ids(f"<html><body>{BOL_SPECS}</body></html>")
    assert ids == ProductIds("5412530956163", ("5410091779108",), "spec-table")


@pytest.mark.parametrize("fragment", [AMAZON_TABLE, AMAZON_BULLET])
def test_amazon_gtin14_becomes_ean13(fragment):
    ids = ean.extract_ids(f"<html><body>{fragment}</body></html>")
    assert ids == ProductIds("8711577110086", (), "spec-table")


def test_microdata_meta_and_text():
    meta = '<meta itemprop="gtin13" content="4001638096515">'
    text = '<span itemprop="gtin">4001638096515</span>'
    assert ean.extract_ids(f"<body>{meta}</body>") == ProductIds("4001638096515", (), "microdata")
    assert ean.extract_ids(f"<body>{text}</body>") == ProductIds("4001638096515", (), "microdata")


def test_two_variants_in_jsonld_is_ambiguous_without_other_evidence():
    block = (
        '<script type="application/ld+json">{"@type":"ProductGroup","hasVariant":['
        '{"@type":"Product","gtin13":"5412530956163"},'
        '{"@type":"Product","gtin13":"4001638096515"}]}</script>'
    )
    assert ean.extract_ids(f"<html>{block}</html>") == ProductIds(None)


def test_ambiguous_jsonld_is_resolved_by_labelled_spec_row():
    block = (
        '<script type="application/ld+json">{"@type":"ProductGroup","hasVariant":['
        '{"@type":"Product","gtin13":"5412530956163"},'
        '{"@type":"Product","gtin13":"4001638096515"}]}</script>'
    )
    ids = ean.extract_ids(f"<html>{block}<dl><dt>EAN</dt><dd>5412530956163</dd></dl></html>")
    assert ids.ean == "5412530956163"
    assert ids.source == "spec-table"


def test_barcodes_of_related_products_are_ignored():
    block = (
        '<script type="application/ld+json">{"@type":"Product","gtin13":"5412530956163",'
        '"isSimilarTo":[{"@type":"Product","gtin13":"4001638096515"}]}</script>'
    )
    assert ean.extract_ids(f"<html>{block}</html>").ean == "5412530956163"


def test_invalid_barcodes_are_ignored():
    html = "<dl><dt>EAN</dt><dd>5412530956164</dd></dl>"  # wrong check digit
    assert ean.extract_ids(html) == ProductIds(None)


def test_label_without_a_barcode_next_to_it_is_ignored():
    html = "<p>EAN</p><p>Zie productomschrijving</p><p>Levertijd 1-2 dagen</p>"
    assert ean.extract_ids(html).ean is None


def test_scripts_and_styles_are_not_treated_as_page_text():
    html = "<style>EAN 5412530956163</style><script>var EAN = '5412530956163'</script><p>hallo</p>"
    assert ean.extract_ids(html) == ProductIds(None)


@pytest.mark.parametrize("html", ["", None, "   ", "<html><body><p>geen ean hier</p></body></html>"])
def test_pages_without_an_ean(html):
    assert ean.extract_ids(html) == ProductIds(None)


def test_malformed_html_does_not_raise():
    assert ean.extract_ids("<div><dt>EAN</dt><dd>5412530956163</dd><<<<>>").ean == "5412530956163"
