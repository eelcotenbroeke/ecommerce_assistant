import json

import pytest

import pageprobe as pp

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
