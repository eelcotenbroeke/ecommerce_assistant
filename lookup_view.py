"""Text and table rows for showing a LookupResult (Dutch). Pure: no Streamlit, no network.

The Streamlit screen (ean_page.py) only places these strings and rows on the page, so what the
owner reads is covered by tests.
"""
from __future__ import annotations

import lookup
from choice import effective_offer, is_preferred


def eur(value):
    """12345.6 -> '€ 12.345,60' (same format as app.fmt_eur)."""
    text = f"{value:,.2f}"
    return "€ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


def _pct(value):
    return f"{value}".replace(".", ",") + "%"


def severity(result):
    """'success', 'warning' or 'error': how loudly to show the message."""
    if result.status == lookup.OK:
        return "success"
    if result.status in (lookup.BLOCKED, lookup.MISMATCH, lookup.SOURCE_ERROR):
        return "error"
    return "warning"


def headline(result):
    """What to buy and for how much, or None when there is no recommendation."""
    advice = result.recommendation
    if advice is None:
        return None
    return f"{advice.pick.shop}: {eur(advice.pick.total)}"


def explanation(result):
    """Why that shop, in one or two sentences. None when there is no recommendation."""
    advice = result.recommendation
    if advice is None:
        return None
    if advice.reason == "preferred":
        return (
            f"{eur(advice.extra)} ({_pct(advice.extra_pct)}) duurder dan de goedkoopste "
            f"({advice.cheapest.shop}, {eur(advice.cheapest.total)}), maar bol en Amazon "
            "vertrouw je het meest en zij bezorgen gratis."
        )
    return "De goedkoopste leverbare optie, inclusief verzending."


def source_line(result):
    """Where the offers came from and which product that is, for the owner to check."""
    parts = []
    if result.source:
        parts.append(f"Bron: {result.source}")
    if result.product_title:
        parts.append(f"Product: {result.product_title}")
    return " \u00b7 ".join(parts) or None


def pick_link(result):
    """(label, url) to open the recommended shop, or None when the source gave no usable link."""
    advice = result.recommendation
    if advice is None or not str(advice.pick.url or "").startswith(("https://", "http://")):
        return None
    return f"Open {advice.pick.shop}", advice.pick.url


def product_link_label(result):
    """Button text for result.product_url (the product page at the source)."""
    return f"Bekijk op {result.source}" if result.source else "Bekijk product"


def read_note(result):
    if result.read_at is None:
        return None
    return (
        f"Gelezen op {result.read_at:%d-%m-%Y om %H:%M}: een momentopname, geen actuele prijs. "
        "Controleer de prijs in de winkel voordat je koopt."
    )


def hint(result):
    """Extra tip when the lookup did not work: a bol multipack has a separate item barcode."""
    if result.child_gtins and not result.worked:
        return (
            "Deze bolpagina is een multipack; het losse artikel heeft EAN "
            + ", ".join(result.child_gtins)
            + ". Probeer die EAN."
        )
    return None


def _shipping_text(offer):
    shown = effective_offer(offer)
    if shown.shipping is None:
        return "onbekend"
    if shown.shipping == 0:
        return "gratis (voor jou)" if is_preferred(offer.shop) and offer.shipping != 0 else "gratis"
    return eur(shown.shipping)


def offer_rows(result):
    """One dict per offer, deliverable ones cheapest first, then the ones that cannot be had."""
    ordered = sorted(
        result.offers,
        key=lambda offer: (not offer.deliverable, effective_offer(offer).total),
    )
    rows = []
    for offer in ordered:
        shown = effective_offer(offer)
        total = eur(shown.total) if shown.shipping is not None else eur(shown.total) + " + verzending?"
        rows.append(
            {
                "Winkel": offer.shop,
                "Totaal": total,
                "Prijs": eur(offer.price),
                "Verzending": _shipping_text(offer),
                "Levering": offer.delivery or "onbekend",
                "Leverbaar": "ja" if offer.deliverable else "nee",
            }
        )
    return rows
