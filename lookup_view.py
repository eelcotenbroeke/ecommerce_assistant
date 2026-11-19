"""Texts and offer blocks for showing a LookupResult (Dutch). Pure: no Streamlit, no network.

The Streamlit screen (ean_page.py) only places these strings and blocks on the page, so what the
owner reads is covered by tests.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace

import lookup
import members
import variant
import verdict as verdicts
from linkname import name_from_link
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


def variant_warning(result):
    """A warning when the product found has another size or pack than the link or name asked for.

    What was asked for is the name that was searched on or, when a link was pasted, the words of
    its slug (a size in a shop's link is a good sign of the size he is looking at).
    """
    if not result.product_title:
        return None
    wanted = [result.query] if result.query else []
    link = getattr(result, "reference_link", None)
    if link:
        wanted.append(name_from_link(link))
    for text in wanted:
        warning = variant.mismatch(text, result.product_title)
        if warning:
            return warning
    return None


def _fit_key(offer):
    """An offer and its effective copy (free delivery applied) share this key."""
    return (offer.shop, offer.price, offer.title)


def wanted_texts(result):
    """What the person asked for: the search term and the words of the link he pasted."""
    texts = [result.query] if result.query else []
    link = getattr(result, "reference_link", None)
    if link:
        texts.append(name_from_link(link))
    return [text for text in texts if text]


def variant_fits(result):
    """{offer key: variant.Fit} for the offers of a result: is each shop's own title the variant
    (shade, colour, code) that was asked for? Empty when nothing like that was asked for."""
    texts = wanted_texts(result)
    if not texts or not result.offers:
        return {}
    fits = {_fit_key(offer): variant.check(texts, result.product_title, offer.title) for offer in result.offers}
    return {key: fit for key, fit in fits.items() if fit.state != variant.OK}


def apply_variant_check(result, fits):
    """The result with its recommendation made again without the offers of another variant. They
    stay in the list (marked), but they are not what the person asked for, so they never are the
    advice. When nothing else is left the result is returned as it was."""
    wrong = {key for key, fit in fits.items() if fit.state == variant.DIFFERS}
    if not wrong or result.recommendation is None:
        return result
    kept = tuple(offer for offer in result.offers if _fit_key(offer) not in wrong)
    advice = recommend(kept)
    return result if advice is None else replace(result, recommendation=advice)


def verdict_for(shown, everything=None, typed="", fits=None):
    """The verdict for what the screen shows: the link's shop against the recommended offer.

    `shown` is the result after the member filter (its recommendation is the pick); `everything`
    is the result before it, because the link's shop may not be a member and is still the one the
    person stands in. `typed` is the total he typed, when the shop is not in the offers.
    """
    everything = everything or shown
    link = getattr(everything, "reference_link", None)
    if not link or shown.recommendation is None:
        return None
    found = verdicts.verdict(
        link, everything.offers, shown.recommendation, typed_total=verdicts.parse_total(typed)
    )
    if found is not None and found.kind == verdicts.CHEAPER and fits:
        fit = fits.get(_fit_key(shown.recommendation.cheapest))
        if fit is not None and fit.state == variant.UNCONFIRMED:
            found = replace(found, caution=fit.note)
    return found


def headline_is_redundant(found):
    """True when the verdict already names the shop that the headline box would name."""
    return found is not None and found.kind in (verdicts.CHEAPER, verdicts.BEST) and found.pick_shop == found.best_shop


VERDICT_ASK = "Wat zie je als totaalprijs op de pagina (met verzending)?"
VERDICT_BAD_TOTAL = "Dat lees ik niet als bedrag; vul bijvoorbeeld 13,99 in."


def verdict_severity(found):
    """'success', 'warning' or 'info' for a Verdict."""
    if found.kind == verdicts.CHEAPER and found.caution:
        return "info"
    return {verdicts.BEST: "success", verdicts.CHEAPER: "warning", verdicts.GONE: "warning"}.get(
        found.kind, "info"
    )


def verdict_headline(found):
    if found.kind == verdicts.CHEAPER:
        label = "Mogelijk goedkoper elders" if found.caution else "Goedkoper elders"
        return f"{label}: bespaar {eur(found.saving)} ({_pct(found.saving_pct)}) bij {found.best_shop}"
    if found.kind == verdicts.BEST:
        return f"Je zit al goed: {found.shop} is de goedkoopste optie die we vonden"
    if found.kind == verdicts.GONE:
        return f"Niet leverbaar bij {found.shop}"
    return f"{found.shop} staat niet in de lijst"


def verdict_detail(found):
    """One or two sentences under the verdict headline."""
    where = "jouw prijs" if found.typed else found.shop
    if found.kind == verdicts.CHEAPER:
        text = (
            f"{found.best_shop}: {eur(found.best_total)} in totaal, tegen {eur(found.reference_total)} "
            f"bij {where}."
        )
        if found.pick_shop != found.best_shop and found.pick_saving >= verdicts.MIN_SAVING:
            text += (
                f" Bij {found.pick_shop} betaal je {eur(found.pick_total)} en bezorgen ze gratis: "
                f"je bespaart {eur(found.pick_saving)}."
            )
    elif found.kind == verdicts.BEST:
        text = f"{eur(found.reference_total)} in totaal bij {where}."
    elif found.kind == verdicts.GONE:
        text = f"Beste alternatief: {found.pick_shop}, {eur(found.pick_total)} in totaal."
    else:
        return (
            f"Vul de totaalprijs in die je ziet, dan vergelijk ik die met de goedkoopste optie, "
            f"{found.best_shop} ({eur(found.best_total)})."
        )
    if found.caution:
        text += f" {found.caution} Controleer dat voordat je gelooft dat het goedkoper is."
    if found.reference_total is not None and not found.shipping_known:
        text += f" De verzendkosten bij {found.shop} zijn onbekend: het kan duurder uitvallen."
    return text


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
    warning: str | None = None  # the variant is not the one asked for, or not confirmed
    excluded: bool = False  # another variant: not part of the advice


def _same_text(a, b):
    """Same words, ignoring case, accents, spaces and punctuation."""

    def squash(text):
        plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
        return "".join(ch for ch in plain.lower() if ch.isalnum())

    return squash(a) == squash(b)


def _web_link(url):
    return url if str(url or "").startswith(("https://", "http://")) else None


MEMBER_BADGE = "Thuiswinkel Waarborg"
NO_MEMBER_NOTE = (
    "Geen winkel van de ledenlijst van Thuiswinkel Waarborg heeft dit leverbaar. Hieronder staan "
    "alle winkels: controleer de winkel voordat je koopt."
)


def is_member_offer(offer, member_list):
    """Shown while only members are wanted: on the list, or a shop the owner trusts anyway."""
    return is_preferred(offer.shop) or (
        member_list is not None and member_list.is_member(offer.shop, offer.url)
    )


def apply_member_filter(result, member_list, only_members=True):
    """(result, hidden): without the offers of shops that are not members, when only members are
    wanted. The recommendation is made again among the offers left, so it never names a hidden
    shop. bol and Amazon always stay. When no member is left with a deliverable offer nothing is
    hidden: all shops are shown, with a note saying that none is a member."""
    if not only_members or member_list is None or not result.offers:
        return result, 0
    kept = tuple(offer for offer in result.offers if is_member_offer(offer, member_list))
    hidden = len(result.offers) - len(kept)
    if not hidden:
        return result, 0
    advice = recommend(kept)
    if advice is None and result.recommendation is not None:
        # an empty screen helps nobody: show every shop, with a warning that none is a member
        return replace(result, notes=(NO_MEMBER_NOTE, *result.notes)), 0
    return replace(result, offers=kept, recommendation=advice), hidden


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


def offer_cards(result, member_list=None, fits=None):
    """One OfferCard per offer: the recommended one first, then deliverable ones cheapest first,
    then the ones that cannot be had."""
    advice = result.recommendation
    pick_shop = advice.pick.shop if advice is not None else None
    fits = fits or {}

    def is_wrong(offer):
        fit = fits.get(_fit_key(offer))
        return fit is not None and fit.state == variant.DIFFERS

    ordered = sorted(
        result.offers,
        key=lambda offer: (
            offer.shop != pick_shop, is_wrong(offer), not offer.deliverable, effective_offer(offer).total,
        ),
    )
    cards = []
    for offer in ordered:
        fit = fits.get(_fit_key(offer))
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
                warning=fit.note if fit is not None else None,
                excluded=fit is not None and fit.state == variant.DIFFERS,
            )
        )
    return cards


CHOOSE_INTRO = (
    "Google Shopping zoekt niet exact op de EAN en gaf deze producten terug. Tik op het product "
    "dat je bedoelt: dan haal ik de winkels op (1 zoekopdracht)."
)
CHOOSE_INTRO_NAME = (
    "Google Shopping gaf deze producten terug voor je zoekterm. Tik op het product dat je "
    "bedoelt: dan haal ik de winkels op (1 zoekopdracht)."
)


def choose_intro(result):
    """Text above the candidate list: without an EAN nothing was searched 'exactly' on a barcode."""
    return CHOOSE_INTRO if getattr(result, "ean", None) else CHOOSE_INTRO_NAME


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
