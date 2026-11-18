"""Live check: can this machine read Beslist without a browser, and does the chain work?

Run it on the machine where the app will run:

    python probe_beslist.py [EAN]

It fetches the Beslist search page for the barcode, finds the product page, reads the offers
and prints the offers plus the recommendation from choice.py. Two requests, two seconds apart,
with an honest User-Agent. If Beslist refuses (403, a captcha page, an empty answer) the script
reports that and stops: it does not try to get around a block.

This file is not a pure module: `fetch_url` does network I/O. `run` takes the fetch function as
a parameter so the tests can drive it without a network.
"""
from __future__ import annotations

import gzip
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from beslist import best_offer, find_product_url, parse_offers, product_ean, search_url
from choice import effective_offer, recommend
from ean import normalize_gtin

USER_AGENT = (
    "ecommerce-assistant/0.1 (personal price check; "
    "+https://github.com/eelcotenbroeke/ecommerce_assistant)"
)
DEFAULT_EAN = "3474637283414"
PAUSE_SECONDS = 2.0


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


def _eur(amount):
    return "EUR " + f"{amount:.2f}".replace(".", ",")


def _failed(out, step, status, text):
    if status is None:
        out(f"  Mislukt bij {step}: geen antwoord ({text}).")
    else:
        out(f"  Mislukt bij {step}: HTTP {status}. Beslist weigert dit verzoek; niet omzeilen.")
    return 1


def run(ean, fetch=fetch_url, sleep=time.sleep, out=print, now=datetime.now):
    """Run the probe. Returns 0 when the whole chain worked, 1 when it did not, 2 on bad input."""
    gtin = normalize_gtin(ean)
    if not gtin:
        out(f"Ongeldige EAN: {ean!r}")
        return 2

    out(f"Beslist-test voor EAN {gtin}")
    status, html = fetch(search_url(gtin))
    out(f"1. Zoekpagina: HTTP {status}, {len(html) if html else 0} tekens")
    if status != 200 or not html:
        return _failed(out, "de zoekpagina", status, html)
    product_url = find_product_url(html, gtin)
    if not product_url:
        out("  Geen productpagina voor deze EAN gevonden op de zoekpagina.")
        return 1
    out("2. Productpagina gevonden voor exact deze EAN")

    sleep(PAUSE_SECONDS)
    status, html = fetch(product_url)
    out(f"3. Productpagina: HTTP {status}, {len(html) if html else 0} tekens")
    if status != 200 or not html:
        return _failed(out, "de productpagina", status, html)
    offers = parse_offers(html)
    page_ean = product_ean(html)
    out(f"4. {len(offers)} aanbiedingen gelezen; EAN op de pagina: {page_ean or 'onbekend'}")
    if not offers:
        out("  Geen aanbiedingen in de pagina (andere opmaak, of een blokkadepagina).")
        return 1
    if page_ean and page_ean != gtin:
        out(f"  De pagina hoort bij een andere EAN ({page_ean}); resultaat niet gebruiken.")
        return 1

    out("")
    for offer in sorted(offers, key=lambda o: effective_offer(o).total):
        shown = effective_offer(offer)
        status_text = offer.delivery or "levertijd onbekend"
        if not offer.deliverable:
            status_text = f"NIET LEVERBAAR ({status_text})"
        out(f"  {offer.shop:<22} {_eur(shown.total):>11}  {status_text}")

    cheapest = best_offer([effective_offer(offer) for offer in offers])
    advice = recommend(offers)
    out("")
    if advice is None:
        out("Geen leverbaar aanbod.")
    else:
        line = f"Advies: {advice.pick.shop}, {_eur(advice.pick.total)}"
        if advice.reason == "preferred":
            line += (
                f" ({_eur(advice.extra)} / {advice.extra_pct}% boven de goedkoopste, "
                f"{cheapest.shop})"
            )
        out(line)
    out(f"Gelezen op {now():%Y-%m-%d %H:%M}: momentopname, geen actuele prijs.")
    out("Resultaat: Beslist is zonder browser bereikbaar vanaf deze computer.")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EAN))
