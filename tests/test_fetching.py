import gzip
import io
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
