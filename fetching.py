"""The one place that talks to shop websites over HTTP (stdlib only).

Not a pure module: `fetch_url` does network I/O. Everything that decides what to do with a page
lives in pure modules that receive a fetch function, so tests never need a network.

The User-Agent is honest on purpose. If a site refuses the request, callers report that and stop;
nothing here retries under another identity.
"""
from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "ecommerce-assistant/0.1 (personal price check; "
    "+https://github.com/eelcotenbroeke/ecommerce_assistant)"
)


def fetch_url(url, timeout=20, accept="text/html"):
    """(HTTP status, text) for a GET; (None, error message) when there was no HTTP answer."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "nl-NL,nl;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        try:
            status, headers, body = error.code, error.headers, error.read()
        except OSError:
            return error.code, ""
    except OSError as error:  # URLError, timeouts, connection resets
        return None, str(error)
    if "gzip" in (headers.get("Content-Encoding") or "").lower():
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            pass
    return status, body.decode("utf-8", errors="replace")


SERPAPI_URL = "https://serpapi.com/search.json"


def _redact(text, secret):
    """`text` with the secret (and its URL-encoded form) replaced by ***."""
    text = str(text)
    if not secret:
        return text
    for form in {secret, urllib.parse.quote_plus(secret), urllib.parse.quote(secret)}:
        text = text.replace(form, "***")
    return text


def serp_fetcher(api_key, recorder=None, timeout=60, fetch=None):
    """A `fetch_json(params) -> (data, error)` for SerpApi that holds the API key.

    The key goes into the request only. It is removed from error texts and from what the
    recorder gets, so it cannot end up on screen, in a download or in a log. `data` is None when
    there was no usable answer; `error` is SerpApi's own message when it gave one (a 200 answer
    can carry an error too, for example "no results"). One attempt only: every search costs a
    credit, so nothing is retried here.

    `recorder(params, data, error)` is called after every call, for the raw-response download.
    """

    def fetch_json(params):
        query = urllib.parse.urlencode({**params, "api_key": api_key})
        status, text = (fetch or fetch_url)(
            f"{SERPAPI_URL}?{query}", timeout=timeout, accept="application/json"
        )
        data, error = None, None
        if status is None:
            error = f"Netwerkfout: {text}"
        else:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                error = f"SerpApi gaf HTTP {status} zonder leesbaar antwoord."
            elif status == 200:
                data, error = parsed, parsed.get("error")
            else:
                error = parsed.get("error") or f"SerpApi gaf HTTP {status}."
        if error:
            error = _redact(error, api_key)
        if recorder is not None:
            clean = json.loads(_redact(json.dumps(data), api_key)) if data is not None else None
            recorder(dict(params), clean, error)
        return data, error

    return fetch_json


SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"


def serp_account(api_key, timeout=15, fetch=None):
    """SerpApi's Account API: `(data, error)` with this month's usage. Free, not a search.

    The answer repeats the API key and the owner's e-mail address; the key is dropped here and
    callers keep only the numbers they need (budget.parse_account).
    """
    query = urllib.parse.urlencode({"api_key": api_key})
    status, text = (fetch or fetch_url)(
        f"{SERPAPI_ACCOUNT_URL}?{query}", timeout=timeout, accept="application/json"
    )
    if status is None:
        return None, _redact(f"Netwerkfout: {text}", api_key)
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return None, f"SerpApi gaf HTTP {status} zonder leesbaar antwoord."
    if status != 200 or parsed.get("error"):
        return None, _redact(parsed.get("error") or f"SerpApi gaf HTTP {status}.", api_key)
    parsed.pop("api_key", None)
    return parsed, None
