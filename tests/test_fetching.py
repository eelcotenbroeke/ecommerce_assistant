import gzip
import io
import json
import urllib.error

import fetching


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self.headers = status, body, headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_url_sends_honest_user_agent_and_unzips(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["agent"] = request.get_header("User-agent")
        seen["encoding"] = request.get_header("Accept-encoding")
        return FakeResponse(200, gzip.compress("café".encode()), {"Content-Encoding": "gzip"})

    monkeypatch.setattr(fetching.urllib.request, "urlopen", fake_urlopen)
    assert fetching.fetch_url("https://example.test/") == (200, "café")
    assert seen["agent"] == fetching.USER_AGENT
    assert "ecommerce-assistant" in seen["agent"]
    assert seen["encoding"] == "gzip"


def test_fetch_url_plain_body(monkeypatch):
    monkeypatch.setattr(
        fetching.urllib.request, "urlopen", lambda request, timeout: FakeResponse(200, b"hallo")
    )
    assert fetching.fetch_url("https://example.test/") == (200, "hallo")


def test_fetch_url_http_error_returns_status(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"nee"))

    monkeypatch.setattr(fetching.urllib.request, "urlopen", fake_urlopen)
    assert fetching.fetch_url("https://example.test/") == (403, "nee")


def test_fetch_url_connection_error_returns_none_and_message(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("geen verbinding")

    monkeypatch.setattr(fetching.urllib.request, "urlopen", fake_urlopen)
    status, text = fetching.fetch_url("https://example.test/")
    assert status is None
    assert "geen verbinding" in text


# ------------------------------------------------------------------ serp_fetcher

KEY = "sekrit-key-123"


def serp_with(status, body, recorder=None):
    seen = []

    def fake_fetch(url, timeout=20, accept="text/html"):
        seen.append((url, timeout, accept))
        return status, body

    return fetching.serp_fetcher(KEY, recorder=recorder, fetch=fake_fetch), seen


def test_serp_fetcher_puts_the_key_in_the_request_only():
    fetch_json, seen = serp_with(200, json.dumps({"shopping_results": []}))
    data, error = fetch_json({"engine": "google_shopping", "q": "3474637283414", "hl": "nl"})
    assert (data, error) == ({"shopping_results": []}, None)
    url, timeout, accept = seen[0]
    assert url.startswith(fetching.SERPAPI_URL + "?")
    assert "api_key=" + KEY in url
    assert "engine=google_shopping" in url and "q=3474637283414" in url
    assert accept == "application/json"
    assert timeout == 60


def test_serp_fetcher_error_answers_have_no_key_and_no_data():
    body = json.dumps({"error": f"Invalid API key {KEY}."})
    fetch_json, _ = serp_with(401, body)
    data, error = fetch_json({"engine": "google_shopping"})
    assert data is None
    assert error == "Invalid API key ***."


def test_serp_fetcher_ok_answer_that_carries_an_error_keeps_the_data():
    fetch_json, _ = serp_with(200, json.dumps({"error": "Google hasn't returned any results.", "x": 1}))
    data, error = fetch_json({"engine": "google_shopping"})
    assert data == {"error": "Google hasn't returned any results.", "x": 1}
    assert error == "Google hasn't returned any results."


def test_serp_fetcher_unreadable_answer_and_network_error():
    fetch_json, _ = serp_with(502, "<html>bad gateway</html>")
    assert fetch_json({}) == (None, "SerpApi gaf HTTP 502 zonder leesbaar antwoord.")
    fetch_json, _ = serp_with(200, json.dumps([1, 2]))
    assert fetch_json({})[0] is None
    fetch_json, _ = serp_with(None, f"timed out at https://serpapi.com/search.json?api_key={KEY}")
    data, error = fetch_json({})
    assert data is None
    assert error.startswith("Netwerkfout: ") and KEY not in error


def test_serp_fetcher_recorder_gets_params_without_key_and_redacted_answers():
    recorded = []
    fetch_json, _ = serp_with(200, json.dumps({"search_metadata": {"note": f"key {KEY}"}}), lambda *args: recorded.append(args))
    fetch_json({"engine": "google_shopping", "q": "1"})
    [(params, data, error)] = recorded
    assert params == {"engine": "google_shopping", "q": "1"}
    assert data == {"search_metadata": {"note": "key ***"}}
    assert error is None
    assert KEY not in json.dumps(recorded)


def test_serp_fetcher_makes_one_attempt_only():
    fetch_json, seen = serp_with(500, json.dumps({"error": "boom"}))
    fetch_json({"engine": "google_shopping"})
    assert len(seen) == 1


def test_fetch_url_accept_header(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["accept"] = request.get_header("Accept")
        return FakeResponse(200, b"{}")

    monkeypatch.setattr(fetching.urllib.request, "urlopen", fake_urlopen)
    fetching.fetch_url("https://example.test/", accept="application/json")
    assert seen["accept"] == "application/json"
    fetching.fetch_url("https://example.test/")
    assert seen["accept"] == "text/html"


# ------------------------------------------------------------------ serp_account


def account_with(status, body):
    seen = []

    def fake_fetch(url, timeout=20, accept="text/html"):
        seen.append(url)
        return status, body

    return (lambda: fetching.serp_account(KEY, fetch=fake_fetch)), seen


def test_serp_account_asks_the_free_account_endpoint_and_drops_the_key():
    body = json.dumps({"api_key": KEY, "this_month_usage": 12, "account_email": "me@example.test"})
    call, seen = account_with(200, body)
    data, error = call()
    assert error is None
    assert data == {"this_month_usage": 12, "account_email": "me@example.test"}
    assert seen == [f"{fetching.SERPAPI_ACCOUNT_URL}?api_key={KEY}"]


def test_serp_account_failures_have_no_key_and_no_data():
    call, _ = account_with(401, json.dumps({"error": f"Invalid API key: {KEY}"}))
    assert call() == (None, "Invalid API key: ***")
    call, _ = account_with(200, "<html>")
    assert call() == (None, "SerpApi gaf HTTP 200 zonder leesbaar antwoord.")
    call, _ = account_with(503, json.dumps({"x": 1}))
    assert call() == (None, "SerpApi gaf HTTP 503.")
    call, _ = account_with(None, f"timeout {KEY}")
    data, error = call()
    assert data is None and KEY not in error
