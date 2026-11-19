"""Texts and offer blocks for showing a LookupResult (Dutch). Pure: no Streamlit, no network.

The Streamlit screen (ean_page.py) only places these strings and blocks on the page, so what the
owner reads is covered by tests.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace

import lookup
import members
from choice import effective_offer, is_preferred, recommend


def eur(value):
    """12345.6 -> '€ 12.345,60' (same format as app.fmt_eur)."""
    text = f"{value:,.2f}"
    return "€ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


def _pct(value):
    return f"{value}".replace(".", ",") + "%"


def severity(result):
    """'success', 'info', 'warning' or 'error': how loudly to show the message."""
    if result.status == lookup.OK:
        return "success"
    if result.status == lookup.CHOOSE:
        return "info"
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


def product_line(result):
    """Which product the source found, for the owner to check against the one he wants."""
    if not result.product_title:
        return None
    where = f" volgens {result.source}" if result.source else ""
    return f"Product{where}: **{result.product_title}**"


def source_line(result):
    """Which barcode was searched and where the offers came from."""
    parts = []
    if result.ean:
        parts.append(f"Gezocht op EAN {result.ean}")
    if getattr(result, "query", None):
        parts.append(f"Zoekterm bij Google: {result.query}")
    if result.source:
        parts.append(f"Bron: {result.source}")
    return " \u00b7 ".join(parts) or None


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


@dataclass(frozen=True)
class OfferCard:
    """One shop's offer as a block in the list on the phone screen."""

    shop: str
    total: str  # what the owner pays, e.g. "\u20ac 38,63"
    badge: str | None  # "Aanbevolen", "Goedkoopste" or None
    line: str  # price, shipping and delivery in one line
    title: str | None  # the product as this shop calls it (only when it differs from the product)
    details: tuple[str, ...]
    url: str | None  # link to the shop (http/https only)
    deliverable: bool
    member: bool = False  # on the Thuiswinkel Waarborg member list


def _same_text(a, b):
    """Same words, ignoring case, accents, spaces and punctuation."""

    def squash(text):
        plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
        return "".join(ch for ch in plain.lower() if ch.isalnum())

    return squash(a) == squash(b)


def _web_link(url):
    return url if str(url or "").startswith(("https://", "http://")) else None


MEMBER_BADGE = "Thuiswinkel Waarborg"
NO_MEMBER_OFFER = "Geen leverbaar aanbod bij een Thuiswinkel Waarborg-winkel."


def is_member_offer(offer, member_list):
    """Shown while only members are wanted: on the list, or a shop the owner trusts anyway."""
    return is_preferred(offer.shop) or (
        member_list is not None and member_list.is_member(offer.shop, offer.url)
    )


def apply_member_filter(result, member_list, only_members=True):
    """(result, hidden): without the offers of shops that are not members, when only members are
    wanted. The recommendation is made again among the offers left, so it never names a hidden
    shop. bol and Amazon always stay."""
    if not only_members or member_list is None or not result.offers:
        return result, 0
    kept = tuple(offer for offer in result.offers if is_member_offer(offer, member_list))
    hidden = len(result.offers) - len(kept)
    if not hidden:
        return result, 0
    advice = recommend(kept)
    fields = {"offers": kept, "recommendation": advice}
    if advice is None and result.recommendation is not None:
        fields.update(status=lookup.UNAVAILABLE, message=NO_MEMBER_OFFER)
    return replace(result, **fields), hidden


def hidden_line(hidden):
    """Why offers are missing, with the date of the list they were checked against."""
    if not hidden:
        return None
    shops = "1 winkel" if hidden == 1 else f"{hidden} winkels"
    return (
        f"{shops} verborgen: niet op de ledenlijst van Thuiswinkel Waarborg "
        f"(lijst van {members.LIST_DATE[8:]}-{members.LIST_DATE[5:7]}-{members.LIST_DATE[:4]}). "
        "Zet de schakelaar uit om ze te tonen."
    )


def offer_cards(result, member_list=None):
    """One OfferCard per offer: the recommended one first, then deliverable ones cheapest first,
    then the ones that cannot be had."""
    advice = result.recommendation
    pick_shop = advice.pick.shop if advice is not None else None
    ordered = sorted(
        result.offers,
        key=lambda offer: (offer.shop != pick_shop, not offer.deliverable, effective_offer(offer).total),
    )
    cards = []
    for offer in ordered:
        shown = effective_offer(offer)
        total = eur(shown.total) if shown.shipping is not None else eur(shown.total) + " + verzending?"
        is_pick = advice is not None and offer.shop == advice.pick.shop
        is_cheapest = advice is not None and offer.shop == advice.cheapest.shop
        badge = "Aanbevolen" if is_pick else "Goedkoopste" if is_cheapest else None
        parts = [] if offer.deliverable else ["Niet leverbaar"]
        parts.append(f"Prijs {eur(offer.price)}")
        parts.append(f"verzending {_shipping_text(offer)}")
        if offer.delivery:
            parts.append(f"levering {offer.delivery}")
        title = offer.title if offer.title and not _same_text(offer.title, result.product_title) else None
        cards.append(
            OfferCard(
                shop=offer.shop,
                total=total,
                badge=badge,
                line=" \u00b7 ".join(parts),
                title=title,
                details=tuple(offer.details),
                url=_web_link(offer.url),
                deliverable=offer.deliverable,
                member=member_list is not None and member_list.is_member(offer.shop, offer.url),
            )
        )
    return cards


CHOOSE_INTRO = (
    "Google Shopping zoekt niet exact op de EAN en gaf deze producten terug. Tik op het product "
    "dat je bedoelt: dan haal ik de winkels op (1 zoekopdracht)."
)


@dataclass(frozen=True)
class CandidateCard:
    """One product Google found, as a block to pick from on the phone screen."""

    title: str
    line: str  # shops and price range
    thumbnail: str | None


def candidate_line(candidate):
    """'2 winkels \u00b7 \u20ac 4,69 \u2013 \u20ac 12,03' (or '1 winkel \u00b7 \u20ac 35,28')."""
    count = len(candidate.shops)
    parts = [f"{count} winkel" + ("" if count == 1 else "s")] if count else []
    low, high = candidate.price_min, candidate.price_max
    if low is not None:
        parts.append(eur(low) if high is None or abs(high - low) < 0.005 else f"{eur(low)} \u2013 {eur(high)}")
    return " \u00b7 ".join(parts)


def candidate_cards(result):
    """The candidate products of a CHOOSE result, in the order to show them."""
    return [
        CandidateCard(c.title, candidate_line(c), _web_link(c.thumbnail)) for c in result.candidates
    ]
