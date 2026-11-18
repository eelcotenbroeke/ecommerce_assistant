"""Live check: can this machine read Beslist without a browser, and does the chain work?

Run it on the machine where the app will run:

    python probe_beslist.py [EAN]

It runs the same chain as the app (lookup.py): fetch the Beslist search page for the barcode,
find the product page, read the offers, and print them with the recommendation from choice.py.
Two requests to Beslist, two seconds apart, with an honest User-Agent. If Beslist refuses (403, a
captcha page, an empty answer) the script reports that and stops: it does not try to get around
a block.

`run` takes the fetch function as a parameter so the tests can drive it without a network.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

from choice import effective_offer
from fetching import fetch_url
from lookup import INVALID_INPUT, lookup

DEFAULT_EAN = "3474637283414"


def _eur(amount):
    return "EUR " + f"{amount:.2f}".replace(".", ",")


def run(ean, fetch=fetch_url, sleep=time.sleep, out=print, now=datetime.now):
    """Run the probe. Returns 0 when the whole chain worked, 1 when it did not, 2 on bad input."""
    result = lookup(ean, fetch, sleep=sleep, now=now)
    if result.status == INVALID_INPUT:
        out(result.message)
        return 2

    out(f"Beslist-test voor EAN {result.ean}")
    for number, step in enumerate(result.steps, start=1):
        out(f"{number}. {step}")
    if not result.worked:
        out(f"  Mislukt: {result.message}")
        return 1

    out("")
    for offer in sorted(result.offers, key=lambda o: effective_offer(o).total):
        shown = effective_offer(offer)
        status_text = offer.delivery or "levertijd onbekend"
        if not offer.deliverable:
            status_text = f"NIET LEVERBAAR ({status_text})"
        out(f"  {offer.shop:<22} {_eur(shown.total):>11}  {status_text}")

    advice = result.recommendation
    out("")
    if advice is None:
        out(result.message)
    else:
        line = f"Advies: {advice.pick.shop}, {_eur(advice.pick.total)}"
        if advice.reason == "preferred":
            line += (
                f" ({_eur(advice.extra)} / {advice.extra_pct}% boven de goedkoopste, "
                f"{advice.cheapest.shop})"
            )
        out(line)
    out(f"Gelezen op {result.read_at:%Y-%m-%d %H:%M}: momentopname, geen actuele prijs.")
    out("Resultaat: Beslist is zonder browser bereikbaar vanaf deze computer.")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EAN))
