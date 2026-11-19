import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

import pageprobe as pp
from ean import normalize_gtin

EAN = "3474637283414"


def page(ld=None, body="", head="", title="Een pagina"):
    blocks = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in (ld or []))
    return f"<html><head><title>{title}</title>{head}{blocks}</head><body>{body}</body></html>"


def read(html, status=200, url="https://www.shop.nl/p/1"):
    return pp.read_page(url, status, html)


# ------------------------------------------------------------------ links


@pytest.mark.parametrize(
    "line, expected",
    [
        ("https://www.coolblue.nl/product/123/x.html", "https://www.coolblue.nl/product/123/x.html"),
        ("  Kijk dit eens https://www.zalando.nl/nike-x.html?utm=1  ", "https://www.zalando.nl/nike-x.html?utm=1"),
        ("www.wehkamp.nl/p/abc/", "https://www.wehkamp.nl/p/abc/"),
    ],
)
def test_clean_url_accepts_shop_links(line, expected):
    assert pp.clean_url(line) == (expected, None)


@pytest.mark.parametrize(
    "line",
    ["", "shampoo", "ftp://shop.nl/x", "http://localhost/x", "http://127.0.0.1/x", "https://192.168.1.1/x",
     "https://user:pw@shop.nl/x", "https://shop.nl:8080/x", "https://printer.local/x", "https://intranet/x"],
)
def test_clean_url_rejects_everything_else(line):
    url, reason = pp.clean_url(line)
    assert url is None and reason


# ------------------------------------------------------------------ reading pages


def test_a_page_with_full_product_data_feeds_every_step():
    html = page(
        ld=[{
            "@context": "https://schema.org", "@type": "Product", "name": "Sony WH-1000XM5", "brand": {"@type": "Brand", "name": "Sony"},
            "mpn": "WH1000XM5B.CE7", "sku": "984321", "gtin13": EAN,
            "offers": {"@type": "Offer", "price": "329.00", "priceCurrency": "EUR"},
            "review": [{"reviewBody": "x" * 500}],
        }]
    )
    report = read(html)
    assert report.verdict == pp.READABLE
    assert (report.ean, report.ean_source) == (EAN, "json-ld")
    assert report.mpns == ("WH1000XM5B.CE7",) and report.skus == ("984321",)
    assert (report.brand, report.name, report.name_source) == ("Sony", "Sony WH-1000XM5", "json-ld")
    assert report.rungs == ("EAN", "merk + MPN", "productnaam")
    assert "Gevonden: EAN, MPN, merk, naam" in report.message and "Ontbreekt" not in report.message
    assert "review" not in json.dumps(report.raw)  # reviews are not part of the download
    assert report.raw["json_ld_products"][0]["offers"] == {"price": "329.00", "priceCurrency": "EUR"}


def test_a_fashion_page_with_only_the_shops_own_code_has_no_ean_or_mpn():
    html = page(
        ld=[{"@type": "Product", "name": "Air Max 90", "brand": "Nike", "sku": "NI112O0XX-A11"}],
    )
    report = read(html)
    assert report.verdict == pp.READABLE
    assert report.ean is None and report.mpns == () and report.skus == ("NI112O0XX-A11",)
    assert report.rungs == ("productnaam",)
    assert "Ontbreekt: EAN, MPN" in report.message


def test_spec_table_rows_are_found_and_listed_for_the_download():
    body = (
        "<table><tr><th>Fabrikantcode</th><td>ABC-123</td></tr>"
        "<tr><th>EAN</th><td>" + EAN + "</td></tr><tr><th>Merk</th><td>Philips</td></tr></table>"
    )
    report = read(page(body=body))
    assert (report.ean, report.ean_source) == (EAN, "spec-table")
    assert ["Fabrikantcode", "ABC-123"] in report.raw["spec_pairs"]
    assert ["Merk", "Philips"] in report.raw["spec_pairs"]


def test_name_falls_back_to_og_title_then_to_the_page_title():
    with_og = read(page(head='<meta property="og:title" content="Philips Series 5000 scheerapparaat">', body="<table><tr><td>Merk</td><td>x</td></tr></table>"))
    assert (with_og.name, with_og.name_source) == ("Philips Series 5000 scheerapparaat", "og:title")
    with_title = read(page(title="Scheerapparaat | Shop", body="<table><tr><td>Merk</td><td>x</td></tr></table>"))
    assert (with_title.name, with_title.name_source) == ("Scheerapparaat | Shop", "title")


def test_a_product_group_with_variants_does_not_pick_an_ean():
    group = {
        "@type": "ProductGroup", "name": "Trui", "brand": "Wehkamp",
        "hasVariant": [
            {"@type": "Product", "sku": "A1", "gtin13": EAN},
            {"@type": "Product", "sku": "A2", "gtin13": "4006381333931"},
        ],
    }
    report = read(page(ld=[group]))
    assert report.variants == 3  # the group and its two variants
    assert report.ean is None  # two barcodes: ambiguous, so none
    assert report.skus == ("A1", "A2")
    assert report.raw["json_ld_products"][0]["hasVariant"] == "2 varianten"


def test_a_page_without_any_product_data_is_said_so():
    report = read(page(body="<div id='app'></div>", title="Laden..."), status=200)
    assert report.verdict == pp.NO_PRODUCT
    assert "JavaScript" in report.message
    assert report.rungs == ("productnaam",)  # only the page title, which is weak but there


# ------------------------------------------------------------------ blocks and errors


@pytest.mark.parametrize("status", [202, 401, 403, 429, 503])
def test_these_statuses_are_blocks_and_are_not_worked_around(status):
    report = read("<html>nope</html>", status=status)
    assert report.verdict == pp.BLOCKED
    assert "niet omzeild" in report.message and f"HTTP {status}" in report.message
    assert report.rungs == ()


def test_a_small_200_page_with_a_bot_check_is_a_block():
    report = read("<html><body>Just a moment... verify you are human</body></html>")
    assert report.verdict == pp.BLOCKED


def test_a_big_product_page_that_mentions_captcha_is_not_a_block():
    html = page(ld=[{"@type": "Product", "name": "X"}], body="<p>captcha</p>" + "<p>tekst</p>" * 2000)
    assert read(html).verdict == pp.READABLE


def test_not_found_other_status_and_no_answer():
    assert read("x", status=404).verdict == pp.NOT_FOUND
    other = read("x", status=500)
    assert other.verdict == pp.ERROR and "HTTP 500" in other.message
    none = pp.read_page("https://www.shop.nl/p/1", None, "timed out")
    assert none.verdict == pp.NO_ANSWER and "timed out" in none.message


# ------------------------------------------------------------------ probing several links


def test_probe_makes_one_request_per_distinct_link_with_a_pause_and_a_cap():
    calls, sleeps = [], []

    def fetch(url):
        calls.append(url)
        return 200, page(ld=[{"@type": "Product", "name": url}])

    urls = [f"https://s{i}.nl/p" for i in range(12)]
    reports = pp.probe([urls[0], urls[0], *urls[1:]], fetch, sleep=sleeps.append, pause=0.5)
    assert calls == urls[: pp.MAX_URLS]
    assert [r.name for r in reports] == urls[: pp.MAX_URLS]
    assert sleeps == [0.5] * (pp.MAX_URLS - 1)


def test_download_data_has_verdict_findings_and_page_data_but_no_html():
    html = page(ld=[{"@type": "Product", "name": "X", "brand": "Y", "mpn": "M1", "gtin13": EAN}], body="SECRET-BODY-TEXT")
    data = pp.download_data([read(html), read("x", status=403)])
    assert data[0]["gevonden"]["ean"] == EAN and data[0]["gevonden"]["mpn"] == ["M1"]
    assert data[0]["pagina"]["json_ld_products"][0]["name"] == "X"
    assert data[1]["oordeel"] == pp.BLOCKED and data[1]["pagina"] is None
    assert "SECRET-BODY-TEXT" not in json.dumps(data)


# ------------------------------------------------------------------ what a real Coolblue page showed (2026-11-19)

from pathlib import Path  # noqa: E402


def coolblue_page():
    """The page rebuilt from the owner's real probe result: JSON-LD product + spec rows, no barcode anywhere."""
    real = json.loads((Path(__file__).parent / "data" / "probe_real_coolblue_dyson.json").read_text(encoding="utf-8"))
    product = {k: v for k, v in real["pagina"]["json_ld_products"][0].items()}
    rows = "".join(f"<tr><th>{a}</th><td>{b}</td></tr>" for a, b in real["pagina"]["spec_pairs"])
    return real["link"], page(ld=[product], body=f"<table>{rows}</table>", title=real["pagina"]["title"])


def test_the_manufacturers_code_comes_from_the_spec_row_when_json_ld_has_no_mpn():
    url, html = coolblue_page()
    report = pp.read_page(url, 200, html)
    assert report.verdict == pp.READABLE
    assert report.mpns == ("476575-01",)  # "Fabrikantcode", not the shop's own "Artikelnummer"
    assert report.skus == ("937057",)
    assert (report.brand, report.name) == ("Dyson", "Dyson V15 Detect Fluffy")
    assert report.ean is None and report.barcode_hints == ()
    assert report.rungs == ("merk + MPN", "productnaam")
    assert "Gevonden: MPN, merk, naam" in report.message and "Ontbreekt: EAN" in report.message


def test_the_brand_comes_from_the_merk_row_when_json_ld_has_none():
    body = "<table><tr><th>Merk</th><td>Philips</td></tr><tr><th>Fabrikantcode</th><td>S5000/20</td></tr></table>"
    report = read(page(body=body))
    assert (report.brand, report.mpns) == ("Philips", ("S5000/20",))


def test_json_ld_mpn_and_row_mpn_are_both_kept_without_repeats():
    html = page(ld=[{"@type": "Product", "name": "X", "mpn": "AB-1"}], body="<table><tr><th>MPN</th><td>AB-1</td></tr><tr><th>Fabrikantcode</th><td>AB-2</td></tr></table>")
    assert read(html).mpns == ("AB-1", "AB-2")


# ------------------------------------------------------------------ barcodes anywhere in the page code


def test_a_barcode_next_to_an_ean_label_in_a_script_is_reported_as_a_hint_not_as_the_ean():
    html = page(ld=[{"@type": "Product", "name": "X", "brand": "Y"}], body='<script>window.dataLayer=[{"id":"937057","ean":"3474637283414"}]</script>')
    report = read(html)
    assert report.ean is None  # not trusted: the page could mention other products too
    assert [h[0] for h in report.barcode_hints] == [EAN]
    assert '"ean":"3474637283414"' in report.barcode_hints[0][1]
    assert "Wel een barcode in de paginacode gevonden" in report.message
    assert report.raw["barcode_hints"][0][0] == EAN


def test_valid_looking_numbers_without_a_label_are_ignored():
    body = "<p>bestelnummer 4006381333931</p><script>var t=1732017600000;</script>"
    assert pp.barcode_hints(page(body=body)) == ()


def test_barcode_hints_are_distinct_capped_and_need_a_valid_check_digit():
    valid = ["4006381333931", "3474637283414", "8711200123452", "5449000000996", "4003994155486", "8000500310427", "3017620422003"]
    bad = "ean 3474637283415"  # wrong check digit
    text = " ".join(f"gtin:{v} ean {v}" for v in valid) + " " + bad
    hints = pp.barcode_hints(text)
    assert len(hints) == pp.MAX_BARCODE_HINTS
    assert [h[0] for h in hints] == [v for v in valid if pp.ean.normalize_gtin(v)][: pp.MAX_BARCODE_HINTS]
    assert "3474637283415" not in [h[0] for h in pp.barcode_hints(bad)]


# ------------------------------------------------------------------ real answers of 2026-11-19 (eight big shops)


def _big_shops():
    path = Path(__file__).parent / "data" / "probe_real_big_shops.json"
    return {urlparse(e["link"]).hostname: e for e in json.loads(path.read_text(encoding="utf-8"))["probe"]}


def test_amazon_title_loses_its_site_tail():
    amazon = _big_shops()["www.amazon.nl"]
    assert amazon["gevonden"]["naam"].endswith(": Amazon.nl: Health & Personal Care")  # what the old parser showed
    cleaned = pp.clean_title(amazon["pagina"]["title"])
    assert cleaned == "HG Gel Unblocker | Removes Clogging in Sink & Toilet | 1L"
    assert pp.clean_title("Dyson V15 Detect Fluffy | Coolblue | Stofzuigers") == "Dyson V15 Detect Fluffy | Coolblue | Stofzuigers"
    assert pp.clean_title("Zoek : Amazon.de") == "Zoek"
    assert pp.clean_title(None) == ""


def test_read_page_gives_the_amazon_name_without_the_tail():
    html = (
        "<html><head><title>HG Gel Unblocker : Amazon.nl: Health &amp; Personal Care</title></head><body><table><tr>"
        "<td><span>Global Trade Identification Number</span></td><td><span>08711577266042</span></td></tr></table>"
        "</body></html>"
    )
    report = pp.read_page("https://www.amazon.nl/dp/B0CBQ252DS", 200, html)
    assert report.verdict == pp.READABLE
    assert report.ean == "8711577266042" and report.ean_source == "spec-table"
    assert report.name == "HG Gel Unblocker" and report.name_source == "title"


def test_the_big_shops_as_the_app_saw_them():
    shops = _big_shops()
    verdicts = {host: e["oordeel"] for host, e in shops.items()}
    assert verdicts == {
        "www.bol.com": "blocked",
        "www.amazon.nl": "readable",
        "www.kruidvat.nl": "blocked",
        "www.etos.nl": "no_answer",
        "www.ah.nl": "blocked",
        "www.jumbo.com": "no_answer",
        "www.mediamarkt.nl": "blocked",
        "www.hema.nl": "readable",
    }
    # the barcodes the readable shops gave are real, valid ones
    assert normalize_gtin(shops["www.amazon.nl"]["gevonden"]["ean"]) == "8711577266042"
    assert normalize_gtin(shops["www.hema.nl"]["gevonden"]["ean"]) == "8721245460115"
    assert shops["www.hema.nl"]["gevonden"]["varianten"] == 13  # every variant has its own barcode
    for host in ("www.bol.com", "www.kruidvat.nl", "www.ah.nl", "www.mediamarkt.nl"):
        assert shops[host]["http_status"] == 403


def test_looks_blocked_is_public_and_knows_bot_pages():
    assert pp.looks_blocked(403, "")
    assert pp.looks_blocked(200, "<html>Please solve the captcha</html>")
    assert not pp.looks_blocked(200, "<html>" + "x" * 9000 + "captcha</html>")  # a big real page
    assert not pp.looks_blocked(200, "<html>gewone pagina</html>")
