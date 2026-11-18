from datetime import datetime

import pytest

import lookup
import probe_beslist
from beslist_pages import EAN, PRODUCT_URL, SEARCH_PAGE, SEARCH_URL, product_page

FIXED_NOW = lambda: datetime(2026, 11, 18, 12, 30)  # noqa: E731


class Session:
    """Fake fetch: answers by URL, remembers calls, sleeps and output lines."""

    def __init__(self, answers):
        self.answers = answers
        self.calls, self.sleeps, self.lines = [], [], []

    def fetch(self, url):
        self.calls.append(url)
        return self.answers[url]

    def run(self, ean=EAN):
        code = probe_beslist.run(
            ean, fetch=self.fetch, sleep=self.sleeps.append, out=self.lines.append, now=FIXED_NOW
        )
        return code, "\n".join(self.lines)


def test_full_chain_works():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())})
    code, text = session.run()
    assert code == 0
    assert session.calls == [SEARCH_URL, PRODUCT_URL]
    assert session.sleeps == [lookup.PAUSE_SECONDS]
    assert "4 aanbiedingen gelezen" in text
    assert f"EAN op de pagina: {EAN}" in text
    assert "NIET LEVERBAAR (Niet leverbaar)" in text
    assert "Gelezen op 2026-11-18 12:30: momentopname" in text
    assert "zonder browser bereikbaar" in text


def test_advice_is_cheapest_when_preferred_shop_is_too_expensive():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())})
    _, text = session.run()
    assert "Advies: Flaconi.nl, EUR 38,63" in text


def test_advice_prefers_bol_within_ten_percent():
    rows = [("Flaconi.nl", 40.0, 0, "1 dag", "InStock"), ("bol.com", 43.0, 3.95, "1 dag", "InStock")]
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page(rows))})
    _, text = session.run()
    assert "Advies: bol.com, EUR 43,00 (EUR 3,00 / 7.5% boven de goedkoopste, Flaconi.nl)" in text


def test_blocked_search_page_stops_without_a_second_request():
    session = Session({SEARCH_URL: (403, "Forbidden")})
    code, text = session.run()
    assert code == 1
    assert "HTTP 403" in text and "niet omzeilen" in text
    assert session.calls == [SEARCH_URL]
    assert session.sleeps == []


def test_no_http_answer_is_reported():
    session = Session({SEARCH_URL: (None, "timed out")})
    code, text = session.run()
    assert code == 1
    assert "Geen antwoord van Beslist (timed out)" in text


def test_blocked_product_page():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (429, "")})
    code, text = session.run()
    assert code == 1
    assert "HTTP 429" in text


def test_search_page_without_product_link():
    session = Session({SEARCH_URL: (200, "<html><a href='/c/shampoo/'>x</a></html>")})
    code, text = session.run()
    assert code == 1
    assert "Geen productpagina" in text
    assert len(session.calls) == 1


def test_product_page_without_offers_such_as_a_block_page():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, "<html>captcha</html>")})
    code, text = session.run()
    assert code == 1
    assert "Geen aanbiedingen" in text


def test_page_for_another_barcode_is_rejected():
    page = product_page(gtin="3600523298136")
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, page)})
    code, text = session.run()
    assert code == 1
    assert "andere EAN (3600523298136)" in text
    assert "Advies" not in text


def test_page_without_barcode_is_accepted_but_reported():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page(gtin=None))})
    code, text = session.run()
    assert code == 0
    assert "EAN op de pagina: onbekend" in text


def test_invalid_ean_makes_no_request():
    session = Session({})
    code, text = session.run("1234567890123")
    assert code == 2
    assert session.calls == []
    assert "geen geldige EAN" in text


def test_gtin14_input_is_normalised():
    session = Session({SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())})
    code, _ = session.run("0" + EAN)
    assert code == 0


@pytest.mark.parametrize("amount, expected", [(38.63, "EUR 38,63"), (0, "EUR 0,00"), (5, "EUR 5,00")])
def test_eur_format(amount, expected):
    assert probe_beslist._eur(amount) == expected
