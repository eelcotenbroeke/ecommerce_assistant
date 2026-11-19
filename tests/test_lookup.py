import json
from datetime import datetime

import pytest

import lookup
from beslist import Offer
from beslist_pages import EAN, PRODUCT_URL, ROWS, SEARCH_PAGE, SEARCH_URL, product_page

BOL_URL = "https://www.bol.com/nl/nl/p/kerastase-gloss-absolu/9300000012345678/"
BOL_LINK_FROM_PHONE = BOL_URL + "?bltgh=abc123&promo=1#reviews"
AMAZON_URL = "https://www.amazon.nl/dp/B01M635J8I"
AMAZON_LINK_FROM_PHONE = (
    "https://www.amazon.nl/-/en/HG-Gel-Unblocker/dp/B01M635J8I/?_encoding=UTF8&pd_rd_w=kx6Jz&ref_=pd_hp_d_atf"
)
NOW = datetime(2026, 11, 18, 12, 30)


def bol_page(ean=EAN, child=None):
    product = {"@type": "Product", "name": "Shampoo", "gtin13": ean}
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'
    if child:
        html += f"<table><tr><th>Child GTIN</th><td>{child}</td></tr></table>"
    return html


def amazon_page(gtin14="0" + EAN):
    """The shape of an Amazon product page: a product overview row holding a GTIN-14."""
    return (
        "<html><head><title>HG Gel Unblocker : Amazon.nl: Huishouden</title></head><body><table><tr>"
        '<td><span class="a-text-bold">Global Trade Identification Number</span></td>'
        f'<td><span class="po-break-word">{gtin14}</span></td></tr></table></body></html>'
    )


class Net:
    """Fake network: answers by URL, remembers calls and sleeps."""

    def __init__(self, answers):
        self.answers, self.calls, self.sleeps = answers, [], []

    def fetch(self, url):
        self.calls.append(url)
        return self.answers[url]

    def lookup(self, text, **kwargs):
        return lookup.lookup(text, self.fetch, sleep=self.sleeps.append, now=lambda: NOW, **kwargs)


def beslist_ok():
    return {SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())}


# ------------------------------------------------------------------ barcode input


@pytest.mark.parametrize("text", [EAN, f" {EAN} ", "0" + EAN, "3474 6372 83414", "3474637283414\n"])
def test_barcode_input_runs_the_chain(text):
    net = Net(beslist_ok())
    result = net.lookup(text)
    assert result.status == lookup.OK and result.worked
    assert result.ean == EAN
    assert net.calls == [SEARCH_URL, PRODUCT_URL]
    assert net.sleeps == [lookup.PAUSE_SECONDS]
    assert result.product_url == PRODUCT_URL
    assert result.page_ean == EAN
    assert result.read_at == NOW
    assert len(result.offers) == len(ROWS)
    assert result.recommendation.pick.shop == "Flaconi.nl"


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_input(text):
    net = Net({})
    result = net.lookup(text)
    assert result.status == lookup.INVALID_INPUT and not result.worked
    assert net.calls == []


@pytest.mark.parametrize("text", ["3474637283415", "12345", "0000000000000"])
def test_invalid_barcodes_make_no_request(text):
    net = Net({})
    result = net.lookup(text)
    assert result.status == lookup.INVALID_INPUT
    assert "geen geldige EAN" in result.message
    assert net.calls == []


@pytest.mark.parametrize("text", ["https://www.kruidvat.nl/shampoo/p/123", "shampoo kerastase", "bol"])
def test_other_input_is_not_recognised(text):
    net = Net({})
    result = net.lookup(text)
    assert result.status == lookup.INVALID_INPUT
    assert "bol- of Amazon-productlink of een EAN" in result.message
    assert net.calls == []


# ------------------------------------------------------------------ bol and Amazon links


def test_bol_link_gives_the_ean_then_the_chain():
    answers = {"https://www.bol.com/nl/nl/p/kerastase-gloss-absolu/9300000012345678/": (200, bol_page())}
    net = Net({**answers, **beslist_ok()})
    result = net.lookup(BOL_LINK_FROM_PHONE)
    assert result.status == lookup.OK
    assert result.ean == EAN
    # query string and fragment (tracking, promo codes) are never sent on
    assert net.calls == [BOL_URL, SEARCH_URL, PRODUCT_URL]
    assert net.sleeps == [lookup.PAUSE_SECONDS]  # pause only between the two Beslist requests
    assert any("EAN van de bolpagina" in step for step in result.steps)


def test_bol_link_without_scheme():
    net = Net({BOL_URL: (200, bol_page()), **beslist_ok()})
    assert net.lookup("www.bol.com/nl/nl/p/kerastase-gloss-absolu/9300000012345678/").status == lookup.OK


def test_multipack_child_barcodes_are_passed_on():
    net = Net({BOL_URL: (200, bol_page(child="5410091779108")), **beslist_ok()})
    result = net.lookup(BOL_URL)
    assert result.child_gtins == ("5410091779108",)


def test_bol_page_not_reachable_asks_for_the_ean():
    net = Net({BOL_URL: (403, "Forbidden")})
    result = net.lookup(BOL_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "bol.com gaf HTTP 403" in result.message and "Plak de EAN zelf" in result.message
    assert net.calls == [BOL_URL]


def test_bol_connection_error_asks_for_the_ean():
    net = Net({BOL_URL: (None, "timed out")})
    result = net.lookup(BOL_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Geen antwoord van bol.com (timed out)" in result.message


def test_bol_page_without_clear_ean_asks_for_the_ean():
    net = Net({BOL_URL: (200, "<html>geen barcode</html>")})
    result = net.lookup(BOL_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Geen eenduidige EAN" in result.message


def test_amazon_link_gives_the_ean_then_the_chain():
    net = Net({AMAZON_URL: (200, amazon_page()), **beslist_ok()})
    result = net.lookup(AMAZON_LINK_FROM_PHONE)
    assert result.status == lookup.OK
    assert result.ean == EAN  # the GTIN-14 with its leading zero is the same barcode
    # the locale, the query string and the tracking never go on: only the product's own page
    assert net.calls == [AMAZON_URL, SEARCH_URL, PRODUCT_URL]
    assert any("EAN van de Amazon-pagina" in step for step in result.steps)


def test_amazon_link_without_scheme_and_on_another_amazon_host():
    net = Net({"https://amazon.nl/dp/B01M635J8I": (200, amazon_page()), **beslist_ok()})
    assert net.lookup("amazon.nl/dp/B01M635J8I").status == lookup.OK
    net = Net({"https://www.amazon.de/dp/B01M635J8I": (200, amazon_page()), **beslist_ok()})
    assert net.lookup("https://www.amazon.de/gp/product/B01M635J8I?th=1").status == lookup.OK


@pytest.mark.parametrize("status", [202, 403, 429, 503])
def test_amazon_block_is_reported_and_not_worked_around(status):
    net = Net({AMAZON_URL: (status, "nee")})
    result = net.lookup(AMAZON_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Amazon liet de pagina niet lezen" in result.message and "niet omzeild" in result.message
    assert "Global Trade Identification Number" in result.message  # and says where the EAN is
    assert net.calls == [AMAZON_URL]  # one request, no retry


def test_amazon_bot_check_page_with_status_200_is_a_block_too():
    page = "<html><body>Enter the characters you see below (captcha)</body></html>"
    net = Net({AMAZON_URL: (200, page)})
    result = net.lookup(AMAZON_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Amazon liet de pagina niet lezen" in result.message
    assert net.calls == [AMAZON_URL]


def test_amazon_other_failures_ask_for_the_ean():
    net = Net({AMAZON_URL: (404, "weg")})
    assert "Amazon gaf HTTP 404." in net.lookup(AMAZON_URL).message
    net = Net({AMAZON_URL: (None, "timed out")})
    result = net.lookup(AMAZON_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Geen antwoord van Amazon (timed out)" in result.message and "Plak de EAN zelf" in result.message


def test_amazon_page_without_a_clear_ean_asks_for_the_ean():
    net = Net({AMAZON_URL: (200, "<html>geen barcode</html>")})
    result = net.lookup(AMAZON_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Geen eenduidige EAN op de Amazon-pagina" in result.message


# ------------------------------------------------------------------ Beslist failures


@pytest.mark.parametrize("status", [403, 429])
def test_block_is_reported_and_not_worked_around(status):
    net = Net({SEARCH_URL: (status, "nee")})
    result = net.lookup(EAN)
    assert result.status == lookup.BLOCKED
    assert f"HTTP {status}" in result.message and "niet omzeilen" in result.message
    assert net.calls == [SEARCH_URL]
    assert net.sleeps == []


def test_bot_check_page_is_a_block_not_a_retry():
    # HTTP 202 with a small page is how AWS WAF answers a bot check (seen from Community Cloud)
    net = Net({SEARCH_URL: (202, "<html>challenge</html>")})
    result = net.lookup(EAN)
    assert result.status == lookup.BLOCKED
    assert "controlepagina (HTTP 202)" in result.message
    assert "niet omzeilen" in result.message and "later opnieuw" not in result.message
    assert net.calls == [SEARCH_URL]


def test_bot_check_on_the_bol_page_asks_for_the_ean():
    net = Net({BOL_URL: (202, "<html>challenge</html>")})
    result = net.lookup(BOL_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "controlepagina" in result.message and "Plak de EAN zelf" in result.message


def test_server_error_says_try_later():
    result = Net({SEARCH_URL: (503, "")}).lookup(EAN)
    assert result.status == lookup.BLOCKED
    assert "later opnieuw" in result.message and "niet omzeilen" not in result.message


def test_no_answer_from_beslist():
    result = Net({SEARCH_URL: (None, "timed out")}).lookup(EAN)
    assert result.status == lookup.BLOCKED
    assert "Geen antwoord van Beslist (timed out)" in result.message
    assert result.steps == ("Beslist zoekpagina: geen antwoord",)


def test_product_page_blocked_keeps_the_product_url():
    result = Net({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (429, "")}).lookup(EAN)
    assert result.status == lookup.BLOCKED
    assert result.product_url == PRODUCT_URL


def test_barcode_not_on_beslist():
    net = Net({SEARCH_URL: (200, "<html><a href='/c/shampoo/'>x</a></html>")})
    result = net.lookup(EAN)
    assert result.status == lookup.NOT_FOUND
    assert len(net.calls) == 1


def test_page_without_offers():
    result = Net({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, "<html>captcha</html>")}).lookup(EAN)
    assert result.status == lookup.NO_OFFERS
    assert result.offers == ()


def test_page_for_another_barcode_is_rejected():
    page = product_page(gtin="3600523298136")
    result = Net({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, page)}).lookup(EAN)
    assert result.status == lookup.MISMATCH
    assert result.recommendation is None and result.offers == ()
    assert "3600523298136" in result.message


def test_page_without_barcode_is_accepted():
    page = product_page(gtin=None)
    result = Net({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, page)}).lookup(EAN)
    assert result.status == lookup.OK
    assert result.page_ean is None


def test_nothing_deliverable():
    page = product_page([("Ghost.nl", 20.0, 0, "Niet leverbaar", "InStock")])
    result = Net({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, page)}).lookup(EAN)
    assert result.status == lookup.UNAVAILABLE and result.worked
    assert result.recommendation is None
    assert len(result.offers) == 1


def test_tolerance_is_passed_to_the_recommendation():
    rows = [("Flaconi.nl", 40.0, 0, "1 dag", "InStock"), ("bol.com", 43.0, 3.95, "1 dag", "InStock")]
    answers = {SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page(rows))}
    assert Net(answers).lookup(EAN).recommendation.pick.shop == "bol.com"
    assert Net(answers).lookup(EAN, tolerance=0.05).recommendation.pick.shop == "Flaconi.nl"


# ------------------------------------------------------------------ a source that cannot tell which product


def choose_source(finish):
    def source(ean, steps):
        steps.append("bron: 3 producten")
        return lookup.Choose(("a", "b", "c"), "Bron", finish, notes=("let op",), product_url="https://bron.example/alles")

    return source


def test_a_source_that_cannot_decide_gives_a_choose_result_without_offers():
    result = lookup.lookup(EAN, None, source=choose_source(lambda c, steps: None))
    assert result.status == lookup.CHOOSE
    assert not result.worked  # nothing to recommend yet
    assert result.candidates == ("a", "b", "c")
    assert result.ean == EAN and result.source == "Bron"
    assert result.offers == () and result.recommendation is None
    assert result.steps[-1] == "bron: 3 producten"


def test_finish_choice_recommends_from_the_chosen_products_offers():
    seen = []

    def finish(candidate, steps):
        seen.append(candidate)
        steps.append("winkels gelezen")
        return lookup.Found(
            (Offer("Flaconi.nl", 38.63, 0.0, "1 dag", True, None), Offer("Celini.nl", 35.28, 3.95, None, True, None)),
            "Bron", read_at=NOW, title="Gekozen product",
        )

    chosen = lookup.finish_choice(lookup.lookup(EAN, None, source=choose_source(finish)), "b")
    assert seen == ["b"]
    assert chosen.status == lookup.OK and chosen.worked
    assert chosen.recommendation.pick.shop == "Flaconi.nl"
    assert chosen.product_title == "Gekozen product" and chosen.ean == EAN
    assert chosen.steps[-2:] == ("Gekozen: b", "winkels gelezen")
    assert chosen.candidates == ()


def test_finish_choice_passes_a_failure_on_with_its_message():
    failure = lookup.Failure(lookup.SOURCE_ERROR, "Maandbudget bereikt", "Bron")
    chosen = lookup.finish_choice(lookup.lookup(EAN, None, source=choose_source(lambda c, s: failure)), "a")
    assert (chosen.status, chosen.message, chosen.source) == (lookup.SOURCE_ERROR, "Maandbudget bereikt", "Bron")
    assert not chosen.worked and chosen.offers == ()
