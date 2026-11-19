import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from streamlit.testing.v1 import AppTest

import fetching
import lookup
import ean_page
import pageprobe
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
            json.dumps(
                {
                    # the first result names the product, so Celini (default title) comes first and
                    # Flaconi's own, different title is shown in its block
                    "shopping_results": [
                        item("Celini.nl", 35.28),
                        item(
                            "Flaconi.nl", 38.63, link="https://www.flaconi.nl/kerastase-gloss",
                            title="Flaconi: Kérastase Gloss Absolu shampoo 500 ml", rating=4.5, reviews=120,
                        ),
                    ]
                }
            ),
        ),
        "google_immersive_product": (
            200,
            json.dumps(
                {
                    "product_results": {
                        "stores": [
                            store("Celini.nl", 35.28, shipping_extracted=3.95),
                            store("Flaconi.nl", 38.63, shipping="Free"),
                            store(
                                "bol.com", 40.0, shipping="Free", link="https://www.bol.com/nl/p/x/9200000001/",
                                extensions=["Op voorraad"],
                            ),
                        ]
                    }
                }
            ),
        ),
    }

    # SerpApi's account: usage grows with every search made, like the real thing; the answer
    # also holds the key and an e-mail address, as the real one does
    account = {"before": 12, "plan": 250, "fails": False}

    def fake_fetch(url, timeout=20, accept="text/html"):
        calls.append(url)
        if url.startswith(fetching.SERPAPI_URL):
            return serp_answers[parse_qs(urlparse(url).query)["engine"][0]]
        if url.startswith(fetching.SERPAPI_ACCOUNT_URL):
            if account["fails"]:
                return 503, "kapot"
            used = account["before"] + sum(c.startswith(fetching.SERPAPI_URL) for c in calls)
            body = {
                "api_key": parse_qs(urlparse(url).query)["api_key"][0],
                "account_email": "eigenaar@example.test",
                "this_month_usage": used,
                "total_searches_left": account["plan"] - used,
            }
            return 200, json.dumps(body)
        return answers[url]

    monkeypatch.setattr(fetching, "fetch_url", fake_fetch)
    monkeypatch.setattr(lookup, "PAUSE_SECONDS", 0)
    fake_fetch.answers, fake_fetch.calls = answers, calls
    fake_fetch.serp_answers, fake_fetch.account = serp_answers, account
    return fake_fetch


KEY = "sekrit-key-123"


def start(key=None, limit="100"):
    """The screen with a key; the monthly limit is set explicitly (None: the default)."""
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    if key:
        at.secrets["SERPAPI_KEY"] = key
        if limit:
            at.secrets["SERPAPI_MONTHLY_LIMIT"] = limit
    return at.run()


def go_button(at):
    """The search button (inside a form, so it is found by its label)."""
    return next(b for b in at.button if b.label == "Zoek beste aanbieding")


def search(at, text=EAN):
    at.text_input(key="ean_text").set_value(text)
    return go_button(at).click().run()


def listed_shops(at):
    """Shop names of the blocks in the offer list (each starts with the bold shop name)."""
    return [m.value.split("**")[1] for m in at.markdown if m.value.startswith("**") and " \u00b7 " in m.value]


def show_all(at):
    """Switch 'only Thuiswinkel Waarborg shops' off: the fake shops (Celini, Flaconi) are not members."""
    return at.toggle(key="ean_members_only").set_value(False).run()


def link_buttons(at):
    return {b.proto.label: b.proto.url for b in at.get("link_button")}


def serp_calls(network):
    """The searches (they cost credits); the free account lookups are not in here."""
    return [url for url in network.calls if url.startswith(fetching.SERPAPI_URL)]


def account_calls(network):
    return [url for url in network.calls if url.startswith(fetching.SERPAPI_ACCOUNT_URL)]


def texts(elements):
    return " ".join(element.value for element in elements)


def test_phone_screen_is_the_default_and_makes_no_request(network):
    at = start()
    assert not at.exception
    assert [t.value for t in at.title] == ["\U0001f3af Prijscheck via EAN"]
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test verbinding met Beslist", "Test pagina's"]
    assert network.calls == []


def test_old_search_is_still_available(network):
    at = start()
    at.radio[0].set_value("Zoeken (oud)").run()
    assert not at.exception
    assert "Snipe Prijzen" in [b.label for b in at.button]


def test_paste_an_ean_and_search(network):
    at = start()
    at.text_input(key="ean_text").set_value(EAN)
    go_button(at).click().run()
    assert not at.exception
    assert "Flaconi.nl: € 38,63" in texts(at.success)
    assert network.calls == [SEARCH_URL, PRODUCT_URL]
    assert "Flaconi.nl" in listed_shops(at)
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
    go_button(at).click().run()
    assert "niet omzeilen" in texts(at.error)
    assert listed_shops(at) == []


def test_invalid_input_is_a_warning(network):
    at = start()
    at.text_input(key="ean_text").set_value("https://www.kruidvat.nl/p/shampoo/123")  # a shop that is not known
    go_button(at).click().run()
    assert "bol- of Amazon-productlink of een EAN" in texts(at.warning)
    assert network.calls == []


AMAZON_URL = "https://www.amazon.nl/dp/B01M635J8I"


def amazon_page():
    return (
        "<html><body><table><tr><td><span>Global Trade Identification Number</span></td>"
        f"<td><span>0{EAN}</span></td></tr></table></body></html>"
    )


def test_amazon_link_is_read_and_the_lookup_goes_on(network):
    network.answers[AMAZON_URL] = (200, amazon_page())
    at = start()
    at.text_input(key="ean_text").set_value("https://www.amazon.nl/-/en/HG/dp/B01M635J8I/?ref_=x&pd_rd_w=y")
    go_button(at).click().run()
    assert not at.exception
    assert network.calls[0] == AMAZON_URL  # the product's own page, no tracking
    assert SEARCH_URL in network.calls
    assert f"Gezocht op EAN {EAN}" in texts(at.caption)
    assert at.success  # a recommendation, not a request for the EAN


def test_amazon_block_asks_for_the_ean_and_stops(network):
    network.answers[AMAZON_URL] = (503, "Service Unavailable")
    at = start()
    at.text_input(key="ean_text").set_value(AMAZON_URL)
    go_button(at).click().run()
    assert "Amazon liet de pagina niet lezen" in texts(at.warning)
    assert "Global Trade Identification Number" in texts(at.warning)
    assert network.calls == [AMAZON_URL]


AMAZON_NO_EAN_URL = "https://www.amazon.nl/dp/B0DJR1RWS3"


def amazon_page_without_ean():
    return (
        "<html><head><title>Kérastase Gloss Absolu shampoo : Amazon.nl: Beauty</title></head>"
        "<body><table><tr><th>ASIN</th><td>B0DJR1RWS3</td></tr></table></body></html>"
    )


def test_amazon_page_without_an_ean_is_searched_on_its_title(network):
    network.answers[AMAZON_NO_EAN_URL] = (200, amazon_page_without_ean())
    at = start(KEY)
    search(at, AMAZON_NO_EAN_URL)
    assert not at.exception
    assert serp_queries(network) == ["Kérastase Gloss Absolu shampoo"]
    assert SEARCH_URL not in network.calls  # no Beslist
    caption = texts(at.caption)
    assert "Zoekterm bij Google: Kérastase Gloss Absolu shampoo" in caption and "Gezocht op EAN" not in caption
    assert "Amazon toont bij dit product geen EAN" in everything_on_screen(at)
    show_all(at)
    assert "Celini.nl" in listed_shops(at)


def test_amazon_page_without_an_ean_and_without_a_key_asks_for_the_ean_or_the_name(network):
    network.answers[AMAZON_NO_EAN_URL] = (200, amazon_page_without_ean())
    at = start()
    search(at, AMAZON_NO_EAN_URL)
    assert "Amazon toont bij dit product geen EAN" in texts(at.warning) and "Productnaam" in texts(at.warning)
    assert network.calls == [AMAZON_NO_EAN_URL]


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
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test verbinding met Beslist", "Test pagina's"]


def test_with_a_key_the_offers_come_from_google_shopping(network):
    at = start(KEY)
    assert not at.exception
    assert not at.warning
    assert [b.label for b in at.button] == ["Zoek beste aanbieding", "Test SerpApi", "Test pagina's", "Start testrun"]
    assert serp_calls(network) == []
    assert "SerpApi deze maand: 12 van 100 gebruikt, nog 88 over" in texts(at.caption)
    search(at)
    assert not at.exception
    assert len(serp_calls(network)) == 2
    assert SEARCH_URL not in network.calls
    show_all(at)
    # bol (40,00) is 3,5% above Flaconi (38,63): the owner's rule picks bol
    assert "bol.com: € 40,00" in texts(at.success)
    assert "duurder dan de goedkoopste" in texts(at.markdown)
    # the list: recommended shop first, then the others by total; every shop with its own details
    assert listed_shops(at) == ["bol.com", "Flaconi.nl", "Celini.nl"]
    markdown = texts(at.markdown)
    assert "**bol.com** \u00b7 \u20ac 40,00 \u00b7 _Aanbevolen_" in markdown
    assert "**Flaconi.nl** \u00b7 \u20ac 38,63 \u00b7 _Goedkoopste_" in markdown
    assert "**Celini.nl** \u00b7 \u20ac 39,23" in markdown
    captions = texts(at.caption)
    assert "Prijs \u20ac 40,00 \u00b7 verzending gratis" in captions
    assert "Prijs \u20ac 35,28 \u00b7 verzending \u20ac 3,95" in captions
    assert "\u2605 4,5 (120 reviews)" in captions and "Op voorraad" in captions
    assert "Flaconi: Kérastase Gloss Absolu shampoo 500 ml" in texts(at.markdown)  # the shop's own title
    assert link_buttons(at) == {
        "Open bol.com": "https://www.bol.com/nl/p/x/9200000001/",
        "Open Flaconi.nl": "https://www.flaconi.nl/kerastase-gloss",
        "Bekijk op Google Shopping": "https://www.google.com/shopping/product/111",
    }
    assert "Geen directe link" in captions  # Celini gave none
    assert f"Product volgens Google Shopping: **{TITLE}**" in texts(at.markdown)
    assert f"Gezocht op EAN {EAN} \u00b7 Bron: Google Shopping" in texts(at.caption)
    assert "controleer of de titel bij je product past" in texts(at.info)
    # the usage line is refreshed after the lookup: 12 before + this lookup's 2 searches
    assert "SerpApi deze maand: 14 van 100 gebruikt, nog 86 over" in texts(at.caption)
    assert "Deze sessie: 2 aanroepen." in texts(at.caption)
    assert [b.label for b in at.get("download_button")] == ["Download antwoorden (JSON)"]


def test_the_api_key_never_reaches_the_page_or_the_download(network):
    at = start(KEY)
    search(at)
    assert KEY in serp_calls(network)[0]  # it does go to SerpApi ...
    assert KEY not in everything_on_screen(at)  # ... and nowhere else
    assert "example.test" not in everything_on_screen(at)  # the account's e-mail neither
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
    assert "Deze sessie: 2 aanroepen." in texts(at.caption)


def test_refresh_asks_again(network):
    at = start(KEY)
    search(at)
    search(at)
    at.button(key="ean_refresh").click().run()
    assert len(serp_calls(network)) == 4
    assert "Uit het geheugen" not in texts(at.caption)
    assert "Deze sessie: 4 aanroepen." in texts(at.caption)


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
    assert listed_shops(at) == []
    search(at)  # a failure is not cached: the second try asks again
    assert len(serp_calls(network)) == 2


def test_connection_test_with_a_key_uses_serpapi(network):
    at = start(KEY)
    at.button(key="ean_conn_test").click().run()
    assert "SerpApi werkt" in texts(at.success)
    assert len(serp_calls(network)) == 2
    assert "Deze sessie: 2 aanroepen." in texts(at.caption)  # test calls are counted


def test_read_time_is_in_the_owners_time_zone():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zoneinfo.ZoneInfo(ean_page.TIME_ZONE)
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database here")
    assert ean_page._now().tzinfo is not None
    assert ean_page._now().utcoffset().total_seconds() in (3600, 7200)


# ------------------------------------------------------------------ the monthly budget


def test_free_account_lookups_are_not_searches(network):
    at = start(KEY)
    search(at)
    assert len(serp_calls(network)) == 2
    assert len(account_calls(network)) == 3  # on opening, before the search, after the search


def test_no_search_when_the_month_is_used_up(network):
    network.account["before"] = 99  # 99 + 2 > 100
    at = start(KEY)
    search(at)
    assert not at.exception
    assert "Maandbudget bereikt: 99 van 100" in texts(at.error)
    assert serp_calls(network) == []
    assert listed_shops(at) == []
    assert "SerpApi deze maand: 99 van 100 gebruikt, nog 1 over" in texts(at.caption)


def test_the_last_lookup_that_still_fits_may_run(network):
    network.account["before"] = 98  # 98 + 2 = 100
    at = start(KEY)
    search(at)
    assert len(serp_calls(network)) == 2
    assert "SerpApi deze maand: 100 van 100 gebruikt, nog 0 over" in texts(at.caption)
    search(at, "3600523298136")  # another product: the budget is now gone
    assert len(serp_calls(network)) == 2
    assert "Maandbudget bereikt: 100 van 100" in texts(at.error)


def test_the_limit_can_be_raised_in_secrets(network):
    network.account["before"] = 120
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.secrets["SERPAPI_KEY"] = KEY
    at.secrets["SERPAPI_MONTHLY_LIMIT"] = "150"
    at.run()
    search(at)
    assert len(serp_calls(network)) == 2
    assert "SerpApi deze maand: 122 van 150 gebruikt" in texts(at.caption)


def test_a_finished_plan_stops_the_search_even_under_the_limit(network):
    network.account["plan"] = 12 + 1  # one search left
    at = start(KEY)
    search(at)
    assert "geen zoekopdrachten meer over" in texts(at.error)
    assert serp_calls(network) == []


def test_cached_result_needs_no_budget(network):
    at = start(KEY)
    search(at)
    network.account["before"] = 500  # budget gone, but the cached lookup is free
    search(at)
    assert "Uit het geheugen van deze sessie" in texts(at.caption)
    assert not at.error


def test_unknown_usage_does_not_block_and_is_said(network):
    network.account["fails"] = True
    at = start(KEY)
    assert not at.exception
    assert "kon niet worden opgehaald" in texts(at.caption)
    search(at)
    assert len(serp_calls(network)) == 2
    assert "Google Shopping" in texts(at.caption)


def test_without_a_limit_secret_the_default_of_250_applies(network):
    network.account["before"] = 128  # what the owner's account showed on 2026-11-18
    at = start(KEY, limit=None)
    assert "SerpApi deze maand: 128 van 250 gebruikt, nog 122 over" in texts(at.caption)
    search(at)
    assert len(serp_calls(network)) == 2
    assert not at.error


# ------------------------------------------------------------------ which input a result belongs to


def test_the_result_names_the_ean_that_was_searched(network):
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"shopping_results": [item("Celini.nl", 35.28, title="KitchenAid Artisan mixer", product_id="9")]}),
    )
    at = start(KEY)
    search(at, "8710103000000")  # invalid check digit: a warning, nothing searched
    assert serp_calls(network) == []
    search(at, EAN)
    assert f"Gezocht op EAN {EAN}" in texts(at.caption)
    assert "Product volgens Google Shopping: **KitchenAid Artisan mixer**" in texts(at.markdown)


def test_a_new_input_replaces_the_old_result(network):
    at = start(KEY)
    search(at, EAN)
    assert f"Gezocht op EAN {EAN}" in texts(at.caption)
    other = "3600523298136"
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"shopping_results": [item("Celini.nl", 12.0, title="Heel ander product", product_id="7")]}),
    )
    search(at, other)
    assert f"Gezocht op EAN {other}" in texts(at.caption)
    assert f"Gezocht op EAN {EAN}" not in texts(at.caption)
    assert "Product volgens Google Shopping: **Heel ander product**" in texts(at.markdown)
    assert len(serp_calls(network)) == 4  # two lookups of two searches each


# ------------------------------------------------------------------ several products: the owner picks

OTHER_TITLE = "Kérastase Gloss Absolu Masque 200 ml"


def several_products(network):
    """Google answers with two different products, like it does for most real EANs."""
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps(
            {
                "shopping_results": [
                    item("Cosmetica.nl", 15.0, product_id="222", title=OTHER_TITLE, token="other",
                         thumbnail="https://encrypted-tbn0.gstatic.com/images?q=1"),
                    item("Celini.nl", 35.28),
                    item("Flaconi.nl", 38.63, link="https://www.flaconi.nl/kerastase-gloss"),
                ]
            }
        ),
    )


def pick_buttons(at):
    return sorted(b.key for b in at.button if b.key and b.key.startswith("ean_pick_"))


def test_several_products_are_listed_after_one_search(network):
    several_products(network)
    at = start(KEY)
    search(at)
    assert not at.exception
    assert len(serp_calls(network)) == 1
    assert "Tik op het product" in texts(at.info)
    assert pick_buttons(at) == ["ean_pick_0", "ean_pick_1"]
    markdown = texts(at.markdown)
    assert f"**{OTHER_TITLE}**" in markdown and f"**{TITLE}**" in markdown
    captions = texts(at.caption)
    assert "1 winkel · € 15,00" in captions
    assert "2 winkels · € 35,28 – € 38,63" in captions
    assert listed_shops(at) == []  # no offers yet
    assert not at.success
    assert "SerpApi deze maand: 13 van 100 gebruikt" in captions  # one search so far


def test_tapping_a_product_fetches_its_shops_for_one_more_search(network):
    several_products(network)
    at = start(KEY)
    search(at)
    at.button(key="ean_pick_1").click().run()
    assert not at.exception
    assert len(serp_calls(network)) == 2
    show_all(at)
    assert "Celini.nl" in listed_shops(at) and "bol.com" in listed_shops(at)
    assert f"Product volgens Google Shopping: **{TITLE}**" in texts(at.markdown)
    assert "controleer of de titel" not in texts(at.info)  # he chose it himself
    assert "SerpApi deze maand: 14 van 100 gebruikt" in texts(at.caption)
    assert pick_buttons(at) == []


def test_another_product_goes_back_to_the_list_without_searching(network):
    several_products(network)
    at = start(KEY)
    search(at)
    at.button(key="ean_pick_1").click().run()
    at.button(key="ean_back").click().run()
    assert len(serp_calls(network)) == 2
    assert pick_buttons(at) == ["ean_pick_0", "ean_pick_1"]
    assert listed_shops(at) == []
    at.button(key="ean_pick_1").click().run()  # the same product again: from the session's memory
    assert len(serp_calls(network)) == 2
    show_all(at)
    assert "Celini.nl" in listed_shops(at)
    at.button(key="ean_back").click().run()
    at.button(key="ean_pick_0").click().run()  # a different product costs one more search
    assert len(serp_calls(network)) == 3
    assert f"Product volgens Google Shopping: **{OTHER_TITLE}**" in texts(at.markdown)


def test_the_same_list_is_remembered_for_fifteen_minutes(network):
    several_products(network)
    at = start(KEY)
    search(at)
    search(at)
    assert len(serp_calls(network)) == 1
    assert pick_buttons(at) == ["ean_pick_0", "ean_pick_1"]


def test_a_product_name_settles_the_choice_without_a_list(network):
    several_products(network)
    at = start(KEY)
    at.text_input(key="ean_hint").set_value("kerastase gloss absolu bain hydra-glaze shampoo")
    search(at)
    assert len(serp_calls(network)) == 2
    assert pick_buttons(at) == []
    show_all(at)
    assert "Celini.nl" in listed_shops(at)


def test_a_different_product_name_is_a_different_lookup(network):
    several_products(network)
    at = start(KEY)
    search(at)
    at.text_input(key="ean_hint").set_value("kerastase gloss absolu masque 200 ml")
    search(at)
    assert len(serp_calls(network)) == 3  # the first list is not reused for another name
    assert f"Product volgens Google Shopping: **{OTHER_TITLE}**" in texts(at.markdown)


def test_the_budget_is_checked_again_before_the_second_step(network):
    several_products(network)
    at = start(KEY)
    search(at)
    network.account["before"] = 500
    at.button(key="ean_pick_1").click().run()
    assert not at.exception
    assert "Maandbudget bereikt" in texts(at.error)
    assert len(serp_calls(network)) == 1
    assert listed_shops(at) == []


def test_the_connection_test_accepts_a_list_of_products(network):
    several_products(network)
    at = start(KEY)
    at.button(key="ean_conn_test").click().run()
    assert "SerpApi werkt" in texts(at.success)
    assert len(serp_calls(network)) == 1


# ------------------------------------------------------------------ searching on the product name


def serp_queries(network):
    return [parse_qs(urlparse(u).query)["q"][0] for u in serp_calls(network) if "google_shopping" in u]


def test_without_a_product_name_google_is_asked_for_the_ean(network):
    at = start(KEY)
    search(at)
    assert serp_queries(network) == [EAN]
    assert "Zoekterm bij Google" not in texts(at.caption)


def test_with_a_product_name_google_is_asked_for_the_name_and_the_screen_says_so(network):
    at = start(KEY)
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu shampoo")
    search(at)
    assert serp_queries(network) == ["Kérastase Gloss Absolu shampoo"]
    assert f"Gezocht op EAN {EAN} \u00b7 Zoekterm bij Google: Kérastase Gloss Absolu shampoo" in texts(at.caption)


def test_an_empty_answer_for_the_ean_points_to_the_product_name_field(network):
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"error": "Google hasn't returned any results for this query."}),
    )
    at = start(KEY)
    search(at)
    assert not at.exception
    assert "Productnaam" in texts(at.warning)
    assert listed_shops(at) == []
    # filling in the name and searching again asks Google for the name
    network.serp_answers["google_shopping"] = (200, json.dumps({"shopping_results": [item("Celini.nl", 35.28)]}))
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu")
    search(at)
    assert serp_queries(network) == [EAN, "Kérastase Gloss Absolu"]
    show_all(at)
    assert "Celini.nl" in listed_shops(at)


# ------------------------------------------------------------------ test of shop pages (no SerpApi)

PAGE_OK = (
    '<html><head><title>Sony koptelefoon</title><script type="application/ld+json">'
    + json.dumps({"@type": "Product", "name": "Sony WH-1000XM5", "brand": "Sony", "mpn": "WH1000XM5B", "gtin13": EAN})
    + "</script></head><body></body></html>"
)


def probe_links(at, text):
    at.text_area(key="probe_links").set_value(text)
    return next(b for b in at.button if b.label == "Test pagina's").click().run()


def test_the_page_probe_reports_readable_and_blocked_shops_and_uses_no_searches(network):
    network.answers["https://www.coolblue.nl/product/1/x.html"] = (200, PAGE_OK)
    network.answers["https://www.zalando.nl/nike-air.html"] = (403, "Forbidden")
    at = start(KEY)
    probe_links(at, "https://www.coolblue.nl/product/1/x.html\nhttps://www.zalando.nl/nike-air.html")
    assert not at.exception
    assert serp_calls(network) == []
    assert network.calls[-2:] == ["https://www.coolblue.nl/product/1/x.html", "https://www.zalando.nl/nike-air.html"]
    assert "Pagina gelezen. Gevonden: EAN, MPN, merk, naam" in texts(at.success)
    assert "niet omzeild" in texts(at.error) and "HTTP 403" in texts(at.error)
    captions = texts(at.caption)
    assert f"EAN {EAN} (uit json-ld)" in captions and "MPN (fabrikantcode): WH1000XM5B" in captions
    assert "Bruikbaar voor: EAN, merk + MPN, productnaam" in captions
    assert "**www.coolblue.nl** \u00b7 Leesbaar" in texts(at.markdown)
    assert "**www.zalando.nl** \u00b7 Geweigerd door de winkel" in texts(at.markdown)
    assert "Download paginagegevens (JSON)" in [b.label for b in at.get("download_button")]


def test_the_page_probe_names_lines_that_are_not_links(network):
    network.answers["https://www.wehkamp.nl/p/1/"] = (200, PAGE_OK)
    at = start()
    probe_links(at, "shampoo\n\nhttps://www.wehkamp.nl/p/1/\nhttp://localhost/x")
    assert "Regel 1: Dit lijkt geen link." in texts(at.warning)
    assert "Regel 4:" in texts(at.warning)
    assert network.calls == ["https://www.wehkamp.nl/p/1/"]


def test_the_page_probe_download_holds_findings_but_not_the_page(network):
    network.answers["https://www.coolblue.nl/product/1/x.html"] = (200, PAGE_OK.replace("</body>", "SECRET-BODY</body>"))
    at = start()
    probe_links(at, "https://www.coolblue.nl/product/1/x.html")
    data = pageprobe.download_data(at.session_state["probe_reports"])
    assert data[0]["gevonden"]["ean"] == EAN
    assert "SECRET-BODY" not in json.dumps(data)


def test_an_empty_probe_does_nothing(network):
    at = start()
    probe_links(at, "   ")
    assert not at.exception
    assert network.calls == []
    assert not at.get("download_button")


def test_the_page_probe_shows_the_manufacturers_code_and_barcode_hints(network):
    html = PAGE_OK.replace("</body>", '<script>d={"ean":"%s"}</script><table><tr><th>Fabrikantcode</th><td>476575-01</td></tr></table></body>' % EAN)
    network.answers["https://www.coolblue.nl/product/2/y.html"] = (200, html)
    at = start()
    probe_links(at, "https://www.coolblue.nl/product/2/y.html")
    captions = texts(at.caption)
    assert "476575-01" in captions
    assert f"Barcode in de paginacode (niet zeker dat het dit product is): {EAN}" in captions


# ------------------------------------------------------------------ Thuiswinkel Waarborg members


def test_shops_that_are_not_members_are_hidden_by_default_and_the_switch_shows_them(network):
    at = start(KEY)
    search(at)
    assert not at.exception
    # the fake shops: Celini and Flaconi are not on the list, bol always stays
    assert listed_shops(at) == ["bol.com"]
    assert "bol.com: \u20ac 40,00" in texts(at.success)
    assert "2 winkels verborgen: niet op de ledenlijst van Thuiswinkel Waarborg" in texts(at.caption)
    toggle = at.toggle(key="ean_members_only")
    assert toggle.label == "Alleen Thuiswinkel Waarborg-winkels" and toggle.value is True
    assert len(serp_calls(network)) == 2
    show_all(at)
    assert listed_shops(at) == ["bol.com", "Flaconi.nl", "Celini.nl"]
    assert "verborgen" not in texts(at.caption)
    assert len(serp_calls(network)) == 2  # the switch is only a view: no new search


def test_the_switch_keeps_its_value_for_the_next_search(network):
    at = start(KEY)
    search(at)
    show_all(at)
    search(at, "https://www.kruidvat.nl/p/shampoo/123")  # a warning without offers: no switch on the screen for a moment
    assert not at.toggle
    search(at)  # the same EAN again, from the session's memory
    assert at.toggle(key="ean_members_only").value is False
    assert "Flaconi.nl" in listed_shops(at)


def test_a_member_gets_a_badge(network):
    network.serp_answers["google_immersive_product"] = (
        200,
        json.dumps(
            {
                "product_results": {
                    "stores": [
                        store("Kruidvat.nl", 33.0, shipping_extracted=0.0),
                        store("Celini.nl", 30.0, shipping_extracted=3.95),
                    ]
                }
            }
        ),
    )
    at = start(KEY)
    search(at)
    assert listed_shops(at) == ["Kruidvat.nl"]
    assert texts(at.caption).count(":green[\u2713 Thuiswinkel Waarborg]") == 1
    # the recommendation is made among the members: Celini (33,95 with shipping) is hidden anyway
    assert "Kruidvat.nl: \u20ac 33,00" in texts(at.success)


def test_when_no_member_has_an_offer_all_shops_are_shown_with_a_note(network):
    network.serp_answers["google_immersive_product"] = (
        200,
        json.dumps({"product_results": {"stores": [store("Celini.nl", 30.0, shipping_extracted=3.95)]}}),
    )
    at = start(KEY)
    search(at)
    assert not at.exception
    # no empty screen: Celini and Flaconi (from Google's first answer) are shown, with a warning
    assert "Celini.nl" in listed_shops(at)
    assert "Geen winkel van de ledenlijst van Thuiswinkel Waarborg heeft dit leverbaar" in texts(at.info)
    assert "verborgen" not in texts(at.caption)
    assert "Celini.nl: \u20ac 33,95" in texts(at.success)


# ------------------------------------------------------------------ a name alone is enough


def test_a_product_name_alone_is_searched_without_a_link_or_an_ean(network):
    at = start(KEY)
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu shampoo")
    go_button(at).click().run()
    assert not at.exception
    assert serp_queries(network) == ["Kérastase Gloss Absolu shampoo"]
    assert network.calls.count(SEARCH_URL) == 0  # no Beslist
    caption = texts(at.caption)
    assert "Zoekterm bij Google: Kérastase Gloss Absolu shampoo" in caption and "Gezocht op EAN" not in caption
    assert "Amazon toont bij dit product geen EAN" not in everything_on_screen(at)
    show_all(at)
    assert "Celini.nl" in listed_shops(at)


def test_words_in_the_link_field_are_a_name_too(network):
    at = start(KEY)
    search(at, "Kérastase Gloss Absolu shampoo")
    assert not at.exception
    assert serp_queries(network) == ["Kérastase Gloss Absolu shampoo"]
    assert not at.error and not at.warning


def test_a_name_alone_without_a_key_says_google_is_needed_and_requests_nothing(network):
    at = start()
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu shampoo")
    go_button(at).click().run()
    assert "SERPAPI_KEY" in texts(at.warning) and "productnaam" in texts(at.warning)
    assert network.calls == []


def test_the_candidate_list_for_a_name_does_not_talk_about_an_ean(network):
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"shopping_results": [
            item("A.nl", 38.0, product_id="111", title="Kérastase Gloss Absolu shampoo 250 ml"),
            item("B.nl", 15.0, product_id="222", title="Kérastase Gloss Absolu masque 200 ml"),
        ]}),
    )
    at = start(KEY)
    search(at, "Kérastase Gloss")
    assert not at.exception
    intro = texts(at.info)
    assert "je zoekterm" in intro and "EAN" not in intro


def test_a_link_in_the_link_field_is_still_a_link_when_the_name_is_filled_in(network):
    at = start(KEY)
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu shampoo")
    search(at, EAN)
    assert f"Gezocht op EAN {EAN}" in texts(at.caption)


def test_an_empty_form_asks_for_a_link_an_ean_or_a_name(network):
    at = start(KEY)
    search(at, "")
    assert "productnaam" in texts(at.warning) + texts(at.error) + texts(at.info)
    assert serp_calls(network) == []


def test_a_session_of_twenty_five_products_stays_in_the_raw_answers_download(network):
    at = start(KEY)
    for number in range(25):
        search(at, f"Merk Product {number} shampoo")
    assert not at.exception
    log = at.session_state["serp_log"]
    assert len(log) == 50 and ean_page.LOG_LIMIT >= 50


# ------------------------------------------------------------------ the testrun

TESTRUN_TABLE = (
    "nr\tcategorie\turl\tzoekwoorden\tproduct\n"
    "1\tbeauty_gezondheid\thttps://www.kruidvat.nl/biodermal/p/130611\tkerastase gloss absolu\tkerastase gloss absolu shampoo 500 ml\n"
    "2\tbeauty_gezondheid\thttps://www.haibu.nl/kerastase-drops.html\tkerastase drops\tkerastase gloss absolu masque 200 ml\n"
)


def run_testrun(at, table=TESTRUN_TABLE, subset=None):
    at.text_area(key="bench_text").set_value(table).run()  # the start button is enabled once the table is read
    if subset:
        at.radio(key="bench_subset").set_value(subset).run()
    return at.button(key="bench_start").click().run()


def test_the_testrun_is_only_there_with_a_key(network):
    assert "bench_text" not in [t.key for t in start().text_area]
    assert "bench_text" in [t.key for t in start(KEY).text_area]


def test_the_testrun_shows_the_plan_and_what_it_costs_at_most(network):
    at = start(KEY)
    at.text_area(key="bench_text").set_value(TESTRUN_TABLE).run()
    # 2 keyword runs (2 searches each) and the haibu link, whose words hold a product name; kruidvat's holds none
    assert "4 runs, maximaal 6 SerpApi-zoekopdrachten" in texts(at.info)
    assert "nog 88 over" in texts(at.info)
    assert serp_calls(network) == []  # nothing runs before the button


def test_the_testrun_runs_every_row_on_the_keywords_and_on_the_link(network):
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"shopping_results": [
            item("Celini.nl", 35.28),
            item("Masque.nl", 15.0, product_id="222", title="Kérastase Gloss Absolu Masque 200 ml", token="other"),
        ]}),
    )
    at = start(KEY)
    run_testrun(at)
    assert not at.exception
    records = at.session_state["bench_records"]
    assert [(r["nr"], r["soort"]) for r in records] == [(1, "zoekwoorden"), (2, "zoekwoorden"), (1, "link"), (2, "link")]
    first, second, link, _ = records
    assert first["status"] == "ok" and "Shampoo" in first["gevonden_product"] and first["zoekterm"] == "kerastase gloss absolu"
    assert second["status"] == "ok" and "Masque" in second["gevonden_product"]
    assert first["lijst_getoond"] and first["gekozen"]["plaats_in_lijst"] == 1  # both products fit the keywords: a list
    assert second["lijst_getoond"] and second["gekozen"]["plaats_in_lijst"] == 2  # and the truth says the masque
    assert first["zoekopdrachten"] == 2 and len(first["ruwe_antwoorden"]) == 2 and first["holdout"] is False
    # a link without a product name in its words is refused without a request; one with a name is searched on it
    assert link["status"] == "invalid_input" and link["zoekopdrachten"] == 0 and link["ruwe_antwoorden"] == []
    assert "Productnaam" in link["melding"]
    named = records[3]
    assert named["soort"] == "link" and named["zoekterm"] == "kerastase drops" and named["zoekopdrachten"] == 2
    assert named["status"] == "ok" and named["lijst_getoond"] is True  # the name fits two products: a list, then the tap
    assert any("Productnaam uit de link: kerastase drops" in step for step in named["stappen"])
    assert len(serp_calls(network)) == 6
    assert at.session_state["bench_running"] is False
    meta = at.session_state["bench_meta"]
    assert meta["runs_gedraaid"] == 4 and meta["zoekopdrachten"] == 6 and meta["problemen_bij_het_lezen"] == []
    assert "budget_voor" in meta and "budget_na" in meta
    assert "4 runs klaar, 6 zoekopdrachten gebruikt." in texts(at.caption)
    assert len(at.get("download_button")) >= 1


def test_the_testrun_taps_the_product_that_matches_the_truth_when_a_list_is_shown(network):
    network.serp_answers["google_shopping"] = (
        200,
        json.dumps({"shopping_results": [
            item("A.nl", 38.0, product_id="111", title="Kérastase Gloss Absolu Bain shampoo 500 ml"),
            item("B.nl", 15.0, product_id="222", title="Kérastase Gloss Absolu Masque 200 ml", token="other"),
        ]}),
    )
    at = start(KEY)
    run_testrun(at, TESTRUN_TABLE.split("\n")[0] + "\n" + TESTRUN_TABLE.split("\n")[2] + "\n")  # only row 2
    record = next(r for r in at.session_state["bench_records"] if r["soort"] == "zoekwoorden")
    assert record["nr"] == 2 and record["lijst_getoond"] is True and len(record["lijst"]) == 2
    assert record["gekozen"]["plaats_in_lijst"] == 2 and record["gekozen"]["twijfel"] is False
    assert "Masque" in record["gevonden_product"] and record["zoekopdrachten"] == 2


def test_the_testrun_can_be_limited_to_the_verdict_set(network):
    at = start(KEY)
    run_testrun(at, subset="even")
    records = at.session_state["bench_records"]
    assert {r["nr"] for r in records} == {2} and len(records) == 2


def test_a_run_that_crashes_becomes_a_record_and_the_rest_goes_on(network, monkeypatch):
    real = ean_page._lookup

    def flaky(text, hint=""):
        if text == "kerastase drops":
            raise RuntimeError("kapot")
        return real(text, hint)

    monkeypatch.setattr(ean_page, "_lookup", flaky)
    at = start(KEY)
    run_testrun(at)
    assert not at.exception
    records = at.session_state["bench_records"]
    assert len(records) == 4
    broken = next(r for r in records if r["nr"] == 2 and r["soort"] == "zoekwoorden")
    assert broken["status"] == "error" and "RuntimeError: kapot" in broken["melding"]
    assert next(r for r in records if r["nr"] == 1 and r["soort"] == "zoekwoorden")["status"] == "ok"


def test_a_table_with_problems_says_so_and_a_missing_column_blocks_the_start(network):
    at = start(KEY)
    at.text_area(key="bench_text").set_value("nr\turl\tzoekwoorden\n1\thttps://x.nl\tjas").run()
    assert "product" in texts(at.warning)
    assert at.button(key="bench_start").disabled


# ------------------------------------------------------------------ a link of any shop


def test_a_link_of_a_shop_that_blocks_the_app_is_searched_on_the_words_of_the_link(network):
    network.answers[BOL_LINK] = (403, "nee")
    at = start(KEY)
    search(at, BOL_LINK)
    assert not at.exception
    assert serp_queries(network) == ["kerastase gloss absolu shampoo"]
    caption = texts(at.caption)
    assert "Zoekterm bij Google: kerastase gloss absolu shampoo" in caption and "Gezocht op EAN" not in caption
    assert network.calls.count(BOL_LINK) == 1 and SEARCH_URL not in network.calls  # bol asked once, no Beslist
    show_all(at)
    assert "Celini.nl" in listed_shops(at)


def test_a_link_of_a_shop_nobody_taught_the_app_is_searched_without_reading_its_page(network):
    link = "https://www.kruidvat.nl/kerastase-gloss-absolu-shampoo/p/130611"
    at = start(KEY)
    search(at, link)
    assert not at.exception
    assert serp_queries(network) == ["kerastase gloss absolu shampoo"]
    assert link not in network.calls  # the page itself is never requested


def test_a_link_without_a_name_in_its_words_says_what_to_do(network):
    at = start(KEY)
    search(at, "https://www.my-origines.com/nl/calendula-P0300036.html")
    assert "Productnaam" in texts(at.warning)
    assert serp_calls(network) == []


BOL_LINK = "https://www.bol.com/nl/nl/p/kerastase-gloss-absolu-shampoo/9300000012345678/"


# ------------------------------------------------------------------ buy here or elsewhere?

CELINI_LINK = "https://www.celini.nl/kerastase-gloss-absolu-shampoo/p/123"
KRUIDVAT_LINK = "https://www.kruidvat.nl/kerastase-gloss-absolu-shampoo/p/456"


def test_a_link_gets_a_verdict_against_the_shop_it_comes_from(network):
    at = start(KEY)
    search(at, CELINI_LINK)
    assert not at.exception
    show_all(at)
    # Celini costs 35,28 + 3,95 = 39,23; Flaconi is cheapest with 38,63; bol (40,00) is the owner's pick
    assert "Goedkoper elders: bespaar € 0,60 (1,5%) bij Flaconi.nl" in texts(at.warning)
    assert "Flaconi.nl: € 38,63 in totaal, tegen € 39,23 bij Celini.nl." in texts(at.markdown)
    assert len(serp_calls(network)) == 2  # the verdict is a view: no extra search


def test_the_link_shop_being_cheapest_is_said_so(network):
    at = start(KEY)
    search(at, "https://www.flaconi.nl/kerastase-gloss-absolu-shampoo/p/1")
    show_all(at)
    assert "Je zit al goed: Flaconi.nl is de goedkoopste optie die we vonden" in texts(at.success)


def test_a_shop_that_is_not_in_the_list_asks_for_the_total_and_compares_it(network):
    at = start(KEY)
    search(at, KRUIDVAT_LINK)
    assert "kruidvat.nl staat niet in de lijst" in texts(at.info)
    typed = at.text_input(key="ean_typed_total")
    assert typed.label == "Wat zie je als totaalprijs op de pagina (met verzending)?"
    typed.set_value("45,00").run()
    # members only (default): bol (40,00) is the only shop shown, so it is the cheapest shown
    assert "Goedkoper elders: bespaar € 5,00 (11,1%) bij bol.com" in texts(at.warning)
    assert "€ 40,00 in totaal, tegen € 45,00 bij jouw prijs." in texts(at.markdown)
    assert len(serp_calls(network)) == 2  # still no new search
    at.text_input(key="ean_typed_total").set_value("38,00").run()
    assert "Je zit al goed: kruidvat.nl is de goedkoopste optie die we vonden" in texts(at.success)


def test_a_total_that_is_not_a_number_is_said_so_and_changes_nothing(network):
    at = start(KEY)
    search(at, KRUIDVAT_LINK)
    at.text_input(key="ean_typed_total").set_value("veel").run()
    assert "Dat lees ik niet als bedrag" in texts(at.caption)
    assert "kruidvat.nl staat niet in de lijst" in texts(at.info)


def test_a_typed_total_is_forgotten_with_the_next_search(network):
    at = start(KEY)
    search(at, KRUIDVAT_LINK)
    at.text_input(key="ean_typed_total").set_value("45,00").run()
    search(at, CELINI_LINK)
    assert not at.exception
    assert "bij jouw prijs" not in texts(at.markdown)


def test_a_barcode_has_no_verdict_and_no_price_field(network):
    at = start(KEY)
    search(at)
    assert not at.exception
    assert not [m for m in at.markdown if "Goedkoper elders" in m.value or "Je zit al goed" in m.value]
    assert "Je zit al goed" not in texts(at.success) and "Goedkoper elders" not in texts(at.warning)
    assert not any(t.key == "ean_typed_total" for t in at.text_input)


# ------------------------------------------------------------------ is it the size he meant?


def test_a_size_in_the_link_that_the_product_does_not_have_is_warned_about(network):
    at = start(KEY)
    # the fake product is the 500 ml shampoo
    search(at, "https://www.celini.nl/kerastase-gloss-absolu-shampoo-250ml/p/123")
    assert not at.exception
    assert (
        "Let op: je zocht 250 ml, het gevonden product noemt 500 ml." in texts(at.warning)
    )


def test_the_same_size_gives_no_warning(network):
    at = start(KEY)
    search(at, "https://www.celini.nl/kerastase-gloss-absolu-shampoo-500ml/p/123")
    assert not at.exception
    assert "Let op: je zocht" not in texts(at.warning)


def test_a_product_name_with_another_size_is_warned_about_too(network):
    at = start(KEY)
    at.text_input(key="ean_hint").set_value("Kérastase Gloss Absolu shampoo 250 ml")
    go_button(at).click().run()
    assert "Let op: je zocht 250 ml" in texts(at.warning)
