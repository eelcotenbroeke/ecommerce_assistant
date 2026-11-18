"""The one place that talks to shop websites over HTTP (stdlib only).

Not a pure module: `fetch_url` does network I/O. Everything that decides what to do with a page
lives in pure modules that receive a fetch function, so tests never need a network.

The User-Agent is honest on purpose. If a site refuses the request, callers report that and stop;
nothing here retries under another identity.
"""
from __future__ import annotations

import gzip
import urllib.error
import urllib.request

USER_AGENT = (
    "ecommerce-assistant/0.1 (personal price check; "
    "+https://github.com/eelcotenbroeke/ecommerce_assistant)"
)


def fetch_url(url, timeout=20):
    """(HTTP status, text) for a GET; (None, error message) when there was no HTTP answer."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
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
