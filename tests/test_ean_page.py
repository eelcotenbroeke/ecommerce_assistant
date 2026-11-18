from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import fetching
import lookup
from beslist_pages import EAN, PRODUCT_URL, SEARCH_PAGE, SEARCH_URL, product_page

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def network(monkeypatch):
    """Fake network for the app: `answers` maps URL -> (status, text); `calls` records requests."""
    answers = {SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())}
    calls = []

    def fake_fetch(url, timeout=20):
        calls.append(url)
        return answers[url]

    monkeypatch.setattr(fetching, "fetch_url", fake_fetch)
    monkeypatch.setattr(lookup, "PAUSE_SECONDS", 0)
    fake_fetch.answers, fake_fetch.calls = answers, calls
    return fake_fetch


def start():
    return AppTest.from_file(APP_PATH, default_timeout=60).run()


def texts(elements):
    return " ".join(element.value for element in elements)


def test_phone_screen_is_the_default_and_makes_no_request(network):
    at = start()
    assert not at.exception
    assert [t.value for t in at.title] == ["\U0001f3af Prijscheck via EAN"]
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test verbinding met Beslist"]
    assert network.calls == []


def test_old_search_is_still_available(network):
    at = start()
    at.radio[0].set_value("Zoeken (oud)").run()
    assert not at.exception
    assert "Snipe Prijzen" in [b.label for b in at.button]


def test_paste_an_ean_and_search(network):
    at = start()
    at.text_input(key="ean_text").set_value(EAN)
    at.button(key="ean_go").click().run()
    assert not at.exception
    assert "Flaconi.nl: € 38,63" in texts(at.success)
    assert network.calls == [SEARCH_URL, PRODUCT_URL]
    assert len(at.dataframe) == 1
    assert "momentopname" in texts(at.caption)


def test_link_parameter_runs_the_lookup_once(network):
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.query_params["url"] = EAN
    at.run()
    assert not at.exception
    assert "Flaconi.nl" in texts(at.success)
    assert at.text_input(key="ean_text").value == EAN
    assert network.calls == [SEARCH_URL, PRODUCT_URL]
    at.run()  # any later interaction reruns the script; it must not fetch again
    assert network.calls == [SEARCH_URL, PRODUCT_URL]


def test_block_is_shown_as_an_error_without_a_table(network):
    network.answers[SEARCH_URL] = (403, "nee")
    at = start()
    at.text_input(key="ean_text").set_value(EAN)
    at.button(key="ean_go").click().run()
    assert "niet omzeilen" in texts(at.error)
    assert len(at.dataframe) == 0


def test_invalid_input_is_a_warning(network):
    at = start()
    at.text_input(key="ean_text").set_value("shampoo")
    at.button(key="ean_go").click().run()
    assert "bol-productlink of een EAN" in texts(at.warning)
    assert network.calls == []


def test_amazon_link_asks_for_the_ean(network):
    at = start()
    at.text_input(key="ean_text").set_value("https://www.amazon.nl/dp/B01M635J8I")
    at.button(key="ean_go").click().run()
    assert "Global Trade Identification Number" in texts(at.warning)
    assert network.calls == []


def test_connection_test_reports_success_and_failure(network):
    at = start()
    at.button(key="ean_conn_test").click().run()
    assert "bereikbaar" in texts(at.success)

    network.answers[SEARCH_URL] = (403, "nee")
    at = start()
    at.button(key="ean_conn_test").click().run()
    assert "niet bereikbaar" in texts(at.error)
