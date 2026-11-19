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


def amazon_page_without_ean(title="FPD 40 Inch Smart TV, Full HD Google TV with HDR10, Dolby Audio (CG40)"):
    """The shape of the Amazon page of a seller who gave no GTIN (seen 2026-11-19, B0DJR1RWS3)."""
    return (
        f"<html><head><title>{title} : Amazon.nl: Electronics &amp; Photo</title></head><body><table>"
        "<tr><th>ASIN</th><td>B0DJR1RWS3</td></tr><tr><th>Item model number</th><td>CG40-P3</td></tr>"
        "</table></body></html>"
    )


class NameSource:
    """A source that searches on a name: records how it was called."""

    def __init__(self):
        self.calls = []

    def __call__(self, ean, steps, page_name=None):
        self.calls.append((ean, page_name))
        steps.append("bron: gezocht")
        return lookup.Found(
            (Offer("Shop.nl", 20.0, 0.0, "Morgen in huis", True, "https://shop.example/", "Echte titel"),),
            "Bron", title="Echte titel", query=page_name,
        )


def test_amazon_page_without_an_ean_is_searched_on_its_title_when_the_source_can_do_that():
    net = Net({AMAZON_URL: (200, amazon_page_without_ean())})
    source = NameSource()
    result = net.lookup(AMAZON_URL, source=source)
    name = "FPD 40 Inch Smart TV, Full HD Google TV with HDR10, Dolby Audio (CG40)"
    assert source.calls == [(None, name)]
    assert result.status == lookup.OK and result.ean is None and result.query == name
    assert lookup.NAME_FROM_AMAZON_NOTE in result.notes  # and says so, with the source's own notes after it
    assert any("Geen EAN op de Amazon-pagina; productnaam van de pagina" in step for step in result.steps)
    assert net.calls == [AMAZON_URL]  # one request to Amazon, none to Beslist


def test_a_source_that_asks_for_a_choice_also_gets_the_note():
    net = Net({AMAZON_URL: (200, amazon_page_without_ean())})

    def source(ean, steps, page_name=None):
        return lookup.Choose(("a", "b"), "Bron", lambda c, s: None, notes=("let op",))

    result = net.lookup(AMAZON_URL, source=source)
    assert result.status == lookup.CHOOSE and result.ean is None
    assert result.notes == (lookup.NAME_FROM_AMAZON_NOTE, "let op")


def test_beslist_cannot_search_on_a_name_so_the_owner_is_asked_for_the_ean():
    net = Net({AMAZON_URL: (200, amazon_page_without_ean())})
    result = net.lookup(AMAZON_URL)
    assert result.status == lookup.NEEDS_EAN
    assert "Amazon toont bij dit product geen EAN" in result.message and "Productnaam" in result.message
    assert net.calls == [AMAZON_URL]


def test_an_amazon_page_without_ean_or_usable_title_passes_no_name():
    net = Net({AMAZON_URL: (200, "<html>geen barcode</html>")})
    source = NameSource()
    result = net.lookup(AMAZON_URL, source=source)
    assert source.calls == [(None, None)]
    assert any("ook geen bruikbare titel" in step for step in result.steps)


@pytest.mark.parametrize("title", ["Amazon.nl", "Sorry! Er is iets misgegaan", "Dyson", "Pagina niet gevonden"])
def test_titles_that_say_too_little_are_not_used_as_a_name(title):
    assert lookup._name_from_amazon_page(AMAZON_URL, amazon_page_without_ean(title)) is None


def test_the_title_is_cut_at_the_feature_list_and_at_a_word():
    page = amazon_page_without_ean("Dyson V8 Cyclone Fluffy | Steelstofzuiger met 40 minuten gebruiksduur, HEPA")
    assert lookup._name_from_amazon_page(AMAZON_URL, page) == "Dyson V8 Cyclone Fluffy"
    long = " ".join(["woord"] * 60)
    cut = lookup._name_from_amazon_page(AMAZON_URL, amazon_page_without_ean(long))
    assert len(cut) <= lookup.MAX_PAGE_NAME_CHARS and cut.endswith("woord")


def test_a_blocked_amazon_page_never_gives_a_name():
    net = Net({AMAZON_URL: (403, "nee")})
    source = NameSource()
    result = net.lookup(AMAZON_URL, source=source)
    assert result.status == lookup.NEEDS_EAN and source.calls == []


# ------------------------------------------------------------------ the words of the link as a name

KRUIDVAT_URL = "https://www.kruidvat.nl/biodermal-p-cl-e-creme/p/130611"
LINK_NAME = "biodermal p cl e creme"


def test_a_shop_that_is_not_known_is_searched_on_the_words_of_its_link_without_reading_the_page():
    net = Net({})
    source = NameSource()
    result = net.lookup(KRUIDVAT_URL, source=source)
    assert source.calls == [(None, LINK_NAME)]
    assert result.status == lookup.OK and result.ean is None and result.query == LINK_NAME
    assert lookup.NAME_FROM_LINK_NOTE in result.notes and lookup.NAME_FROM_AMAZON_NOTE not in result.notes
    assert f"Productnaam uit de link: {LINK_NAME}" in result.steps
    assert net.calls == [] and net.sleeps == []  # the shop is never asked for its page


def test_a_blocked_bol_page_falls_back_on_the_words_of_the_link():
    net = Net({BOL_URL: (403, "nee")})
    source = NameSource()
    result = net.lookup(BOL_URL, source=source)
    assert net.calls == [BOL_URL]  # asked once, honestly, and not again
    assert source.calls == [(None, "kerastase gloss absolu")]
    assert result.status == lookup.OK and lookup.NAME_FROM_LINK_NOTE in result.notes
    assert any("bol-pagina: HTTP 403" in step for step in result.steps)  # the block stays in the log


def test_a_bol_page_that_works_is_still_read_for_its_ean():
    net = Net({BOL_URL: (200, bol_page())})
    source = NameSource()
    net.lookup(BOL_URL, source=source)
    assert source.calls == [(EAN, None)]  # an EAN beats the words of the link


def test_a_link_without_a_name_in_its_words_is_refused_with_the_way_out():
    net = Net({})
    source = NameSource()
    result = net.lookup("https://www.my-origines.com/nl/calendula-P0300036.html", source=source)
    assert result.status == lookup.INVALID_INPUT and result.message == lookup.NO_NAME_IN_LINK
    assert source.calls == [] and net.calls == []


def test_without_a_source_that_can_search_names_the_old_answers_stay():
    net = Net({BOL_URL: (403, "nee")})
    assert net.lookup(BOL_URL).status == lookup.NEEDS_EAN  # Beslist cannot search on a name
    unknown = net.lookup(KRUIDVAT_URL)
    assert unknown.status == lookup.INVALID_INPUT and "Andere winkels worden nog niet herkend" in unknown.message


def test_an_amazon_page_without_ean_or_title_falls_back_on_the_slug_of_the_link():
    link = "https://www.amazon.nl/Anker-PowerWave-Wireless-Charger/dp/B07THHQMHM"
    net = Net({"https://www.amazon.nl/dp/B07THHQMHM": (200, "<html>geen barcode</html>")})
    source = NameSource()
    result = net.lookup(link, source=source)
    assert source.calls == [(None, "anker powerwave wireless charger")]
    assert lookup.NAME_FROM_LINK_NOTE in result.notes


def test_the_title_of_an_amazon_page_wins_over_the_slug_of_its_link():
    net = Net({"https://www.amazon.nl/dp/B01M635J8I": (200, amazon_page_without_ean())})
    source = NameSource()
    result = net.lookup(AMAZON_LINK_FROM_PHONE, source=source)
    assert source.calls[0][1].startswith("FPD 40 Inch Smart TV") and lookup.NAME_FROM_AMAZON_NOTE in result.notes


def test_an_invalid_barcode_never_becomes_a_name():
    net = Net({})
    source = NameSource()
    result = net.lookup("1234567890123", source=source)
    assert result.status == lookup.INVALID_INPUT and source.calls == []


# ------------------------------------------------------------------ a product name instead of a link


def test_a_name_in_the_link_field_or_in_the_name_field_is_a_name():
    assert lookup.name_from_input("", "Kérastase Gloss Absolu") == "Kérastase Gloss Absolu"
    assert lookup.name_from_input("   ", " Nutella ") == "Nutella"
    assert lookup.name_from_input("Kérastase Gloss Absolu shampoo", "") == "Kérastase Gloss Absolu shampoo"
    # the owner's *Productnaam* wins when both are filled in
    assert lookup.name_from_input("Kérastase", "Kérastase Gloss Absolu") == "Kérastase Gloss Absolu"
    assert lookup.name_from_input("Nutella") == "Nutella"


@pytest.mark.parametrize(
    "text",
    [
        EAN,
        "3474-6372 83414",
        BOL_URL,
        AMAZON_URL,
        "https://www.kruidvat.nl/p/shampoo/123",  # a shop that is not known yet: still a link
        "www.kruidvat.nl",
        "kruidvat.nl/p/shampoo/123",
        "Kijk eens https://www.kruidvat.nl/p/1",
    ],
)
def test_links_and_barcodes_are_never_taken_for_a_name(text):
    assert lookup.name_from_input(text, "Kérastase") is None
    assert lookup.name_from_input(text, "") is None


def test_nothing_at_all_is_no_name():
    assert lookup.name_from_input("", "") is None
    assert lookup.name_from_input(None, None) is None
    net = Net({})
    result = net.lookup("")
    assert result.status == lookup.INVALID_INPUT and "productnaam" in result.message


def test_by_name_calls_the_source_without_an_ean_or_a_page_and_reads_no_page():
    net = Net({})
    source = NameSource()
    result = net.lookup("", source=source, by_name=True)
    assert source.calls == [(None, None)]
    assert result.status == lookup.OK and result.ean is None
    assert lookup.NAME_FROM_AMAZON_NOTE not in result.notes  # that note is about an Amazon page
    assert net.calls == []


def test_a_name_search_through_a_choice_has_no_amazon_note_either():
    net = Net({})

    def source(ean, steps, page_name=None):
        return lookup.Choose(("a", "b"), "Bron", lambda c, s: None, notes=("let op",))

    result = net.lookup("", source=source, by_name=True)
    assert result.status == lookup.CHOOSE and result.ean is None and result.notes == ("let op",)


def test_beslist_cannot_search_on_a_name_and_nothing_is_requested():
    net = Net({})
    result = net.lookup("", by_name=True)
    assert result.status == lookup.NEEDS_EAN and result.message == lookup.NAME_NEEDS_GOOGLE
    assert "SERPAPI_KEY" in result.message
    assert net.calls == [] and net.sleeps == []


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
