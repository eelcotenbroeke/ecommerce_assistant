import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from streamlit.testing.v1 import AppTest

import fetching
import lookup
import ean_page
from beslist_pages import EAN, PRODUCT_URL, SEARCH_PAGE, SEARCH_URL, product_page
from serp_pages import TITLE, item, store

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def network(monkeypatch):
    """Fake network for the app: `answers` maps URL -> (status, text); `calls` records requests."""
    answers = {SEARCH_URL: (200, SEARCH_PAGE), PRODUCT_URL: (200, product_page())}
    calls = []

    serp_answers = {
        "google_shopping": (
            200,
            json.dumps({"shopping_results": [item("Flaconi.nl", 38.63), item("Celini.nl", 35.28)]}),
        ),
        "google_immersive_product": (
            200,
            json.dumps(
                {
                    "product_results": {
                        "stores": [
                            store("Celini.nl", 35.28, shipping_extracted=3.95),
                            store("Flaconi.nl", 38.63, shipping="Free"),
                            store("bol.com", 40.0, shipping="Free"),
                        ]
                    }
                }
            ),
        ),
    }

    def fake_fetch(url, timeout=20, accept="text/html"):
        calls.append(url)
        if url.startswith(fetching.SERPAPI_URL):
            return serp_answers[parse_qs(urlparse(url).query)["engine"][0]]
        return answers[url]

    monkeypatch.setattr(fetching, "fetch_url", fake_fetch)
    monkeypatch.setattr(lookup, "PAUSE_SECONDS", 0)
    fake_fetch.answers, fake_fetch.calls, fake_fetch.serp_answers = answers, calls, serp_answers
    return fake_fetch


KEY = "sekrit-key-123"


def start(key=None):
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    if key:
        at.secrets["SERPAPI_KEY"] = key
    return at.run()


def search(at, text=EAN):
    at.text_input(key="ean_text").set_value(text)
    return at.button(key="ean_go").click().run()


def serp_calls(network):
    return [url for url in network.calls if url.startswith(fetching.SERPAPI_URL)]


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


# ------------------------------------------------------------------ with a SerpApi key


def everything_on_screen(at):
    elements = [
        *at.markdown, *at.caption, *at.success, *at.error, *at.warning, *at.info, *at.text,
    ]
    return " ".join(str(element.value) for element in elements)


def test_without_a_key_the_screen_says_so_and_uses_beslist(network):
    at = start()
    assert "SERPAPI_KEY" in texts(at.warning)
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test verbinding met Beslist"]


def test_with_a_key_the_offers_come_from_google_shopping(network):
    at = start(KEY)
    assert not at.exception
    assert not at.warning
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test SerpApi"]
    assert network.calls == []
    search(at)
    assert not at.exception
    assert len(serp_calls(network)) == 2
    assert SEARCH_URL not in network.calls
    # bol (40,00) is 3,5% above Flaconi (38,63): the owner's rule picks bol
    assert "bol.com: € 40,00" in texts(at.success)
    assert "duurder dan de goedkoopste" in texts(at.markdown)
    assert len(at.dataframe) == 1
    assert f"Bron: Google Shopping \u00b7 Product: {TITLE}" in texts(at.caption)
    assert "controleer of de titel bij je product past" in texts(at.info)
    assert "SerpApi-aanroepen in deze sessie: 2" in texts(at.caption)
    assert [b.label for b in at.get("download_button")] == ["Download antwoorden (JSON)"]


def test_the_api_key_never_reaches_the_page_or_the_download(network):
    at = start(KEY)
    search(at)
    assert KEY in serp_calls(network)[0]  # it does go to SerpApi ...
    assert KEY not in everything_on_screen(at)  # ... and nowhere else
    assert KEY not in json.dumps(at.session_state["serp_log"], default=str)
    assert [entry["aanroep"]["engine"] for entry in at.session_state["serp_log"]] == [
        "google_shopping",
        "google_immersive_product",
    ]


def test_same_input_within_fifteen_minutes_costs_nothing_more(network):
    at = start(KEY)
    search(at)
    search(at)
    assert len(serp_calls(network)) == 2
    assert "Uit het geheugen van deze sessie" in texts(at.caption)
    assert "SerpApi-aanroepen in deze sessie: 2" in texts(at.caption)


def test_refresh_asks_again(network):
    at = start(KEY)
    search(at)
    search(at)
    at.button(key="ean_refresh").click().run()
    assert len(serp_calls(network)) == 4
    assert "Uit het geheugen" not in texts(at.caption)
    assert "SerpApi-aanroepen in deze sessie: 4" in texts(at.caption)


def test_cache_expires(network, monkeypatch):
    at = start(KEY)
    search(at)
    clock = ean_page.time.time()
    monkeypatch.setattr(ean_page.time, "time", lambda: clock + ean_page.CACHE_SECONDS + 1)
    search(at)
    assert len(serp_calls(network)) == 4


def test_a_rejected_key_is_an_error_without_the_key_and_is_not_remembered(network):
    network.serp_answers["google_shopping"] = (
        401,
        json.dumps({"error": f"Invalid API key. Your API key should be here: {KEY}"}),
    )
    at = start(KEY)
    search(at)
    assert not at.exception
    assert "Invalid API key" in texts(at.error)
    assert KEY not in everything_on_screen(at)
    assert KEY not in json.dumps(at.session_state["serp_log"], default=str)
    assert len(at.dataframe) == 0
    search(at)  # a failure is not cached: the second try asks again
    assert len(serp_calls(network)) == 2


def test_connection_test_with_a_key_uses_serpapi(network):
    at = start(KEY)
    at.button(key="ean_conn_test").click().run()
    assert "SerpApi werkt" in texts(at.success)
    assert len(serp_calls(network)) == 2
    assert "SerpApi-aanroepen in deze sessie: 2" in texts(at.caption)  # test calls are counted


def test_read_time_is_in_the_owners_time_zone():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zoneinfo.ZoneInfo(ean_page.TIME_ZONE)
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database here")
    assert ean_page._now().tzinfo is not None
    assert ean_page._now().utcoffset().total_seconds() in (3600, 7200)
