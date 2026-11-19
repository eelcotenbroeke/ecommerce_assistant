"""Streamlit screen for the EAN lookup: paste a bol link or an EAN, get the offer to buy.

Not a pure module: it only places the results of lookup.py / lookup_view.py on the page and
fetches through fetching.py. With a SERPAPI_KEY in the app's Secrets the offers come from Google
Shopping through SerpApi (serp.py); without one the Beslist chain is tried. Opened with
?url=<bol link or EAN> (for instance from a phone's share sheet) it runs the lookup straight away.

Every SerpApi search costs a credit, so a worked lookup is remembered for CACHE_SECONDS per
session, the screen counts the calls it made, and no lookup starts when the month's budget
(budget.py, default 100 searches, SERPAPI_MONTHLY_LIMIT in Secrets) is used up. The month's
usage comes from SerpApi's free Account API. The API key never reaches the page: it lives in
fetching.serp_fetcher and is removed from errors and from the downloadable raw answers.
"""
import json
import time
from datetime import datetime

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import budget
import fetching
import lookup
import lookup_view
import serp

CACHE_SECONDS = 15 * 60
LOG_LIMIT = 6  # raw SerpApi answers kept for the download (a lookup makes at most 2)
TIME_ZONE = "Europe/Amsterdam"


def _now():
    """Now in the owner's time zone: the server runs in UTC, the 'gelezen om' time is for him."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(TIME_ZONE))
    except Exception:  # no tz database on this machine: better the server's clock than a crash
        return datetime.now()


def _api_key():
    try:
        return str(st.secrets["SERPAPI_KEY"]).strip()
    except Exception:  # no secrets file, or no such secret
        return ""


def _limit():
    try:
        return budget.parse_limit(st.secrets["SERPAPI_MONTHLY_LIMIT"])
    except Exception:  # not set
        return budget.DEFAULT_LIMIT


def _refresh_account():
    """Ask SerpApi (free) how much was used this month; None when that fails."""
    data, _error = fetching.serp_account(_api_key())
    account = budget.parse_account(data)
    st.session_state["serp_account"] = account
    return account


def _guard(cost):
    """Reason not to search (budget used up), or None. Unknown usage does not block."""
    account = _refresh_account()
    return budget.check(account, _limit(), cost=cost) if account else None


def _record(params, data, error):
    log = st.session_state.setdefault("serp_log", [])
    log.append(
        {
            "tijd": _now().isoformat(timespec="seconds"),
            "aanroep": params,
            "fout": error,
            "antwoord": data,
        }
    )
    st.session_state["serp_calls"] = st.session_state.get("serp_calls", 0) + 1
    del log[:-LOG_LIMIT]


def _lookup(text, hint=""):
    key = _api_key()
    source = None
    if key:
        fetch_json = fetching.serp_fetcher(key, recorder=_record)
        source = serp.serp_source(fetch_json, now=_now, guard=_guard, hint=hint or None)
    result = lookup.lookup(text, fetching.fetch_url, sleep=time.sleep, now=_now, source=source)
    if key:
        _refresh_account()  # the usage on the screen includes this lookup
    return result


def _cached(key):
    entry = st.session_state.setdefault("ean_cache", {}).get(key)
    if entry and time.time() - entry[0] < CACHE_SECONDS:
        return entry
    return None


def _remember(key, result):
    cache = st.session_state.setdefault("ean_cache", {})
    if result.worked or result.status == lookup.CHOOSE:
        cache[key] = (time.time(), result)
    else:
        cache.pop(key, None)


def _set_result(result, cached_at=None):
    st.session_state["ean_result"] = result
    st.session_state["ean_cached_at"] = cached_at
    if result.status == lookup.CHOOSE:
        st.session_state["ean_choose_result"] = result


def _run(text, hint="", fresh=False):
    text, hint = text.strip(), hint.strip()
    key = f"{text}\n{hint}"
    st.session_state["ean_last"] = (text, hint)
    st.session_state.pop("ean_choose_result", None)
    entry = None if fresh else _cached(key)
    if entry:
        _set_result(entry[1], entry[0])
        return
    with st.spinner("Producten zoeken..."):
        result = _lookup(text, hint)
    _set_result(result)
    _remember(key, result)


def _choose(index):
    """The owner tapped candidate `index` of the pending CHOOSE result: fetch its shops."""
    pending = st.session_state.get("ean_choose_result")
    if pending is None or not 0 <= index < len(pending.candidates):
        return
    candidate = pending.candidates[index]
    key = f"{pending.ean}\n{candidate.key}"
    entry = _cached(key)
    if entry:
        _set_result(entry[1], entry[0])
        return
    with st.spinner("Winkels ophalen..."):
        result = lookup.finish_choice(pending, candidate)
        if _api_key():
            _refresh_account()
    _set_result(result)
    _remember(key, result)


def _ask_pick(index):
    st.session_state["ean_pick"] = index


def _back_to_choice():
    st.session_state["ean_result"] = st.session_state.get("ean_choose_result")
    st.session_state["ean_cached_at"] = None


def _ask_fresh():
    st.session_state["ean_force"] = True


def _offer_block(card):
    """One shop in the list: name and total, the line with price/shipping/delivery, details, link."""
    with st.container(border=True):
        st.markdown(f"**{card.shop}** \u00b7 {card.total}" + (f" \u00b7 _{card.badge}_" if card.badge else ""))
        st.caption(card.line)
        if card.title:
            st.write(card.title)
        for fact in card.details:
            st.caption(fact)
        if card.url:
            st.link_button(f"Open {card.shop}", card.url)
        else:
            st.caption("Geen directe link: gebruik de knop met het product hieronder.")


def _show_candidates(result):
    st.info(lookup_view.CHOOSE_INTRO)
    for index, card in enumerate(lookup_view.candidate_cards(result)):
        with st.container(border=True):
            picture, text = st.columns([1, 3])
            if card.thumbnail:
                picture.image(card.thumbnail, width=72)
            text.markdown(f"**{card.title}**")
            if card.line:
                text.caption(card.line)
            text.button("Dit is mijn product", key=f"ean_pick_{index}", on_click=_ask_pick, args=(index,))
    if result.product_url:
        st.link_button("Bekijk alle resultaten op Google Shopping", result.product_url)
    with st.expander("Stappen"):
        for step in result.steps:
            st.write(step)


def _show(result):
    if result.status == lookup.CHOOSE:
        _show_candidates(result)
        return
    shown = getattr(st, lookup_view.severity(result))
    headline = lookup_view.headline(result)
    shown(f"**{headline}**" if headline else result.message)
    if headline:
        st.write(lookup_view.explanation(result))
    tip = lookup_view.hint(result)
    if tip:
        st.info(tip)
    for card in lookup_view.offer_cards(result):
        _offer_block(card)
    product = lookup_view.product_line(result)
    if product:
        st.markdown(product)
    line = lookup_view.source_line(result)
    if line:
        st.caption(line)
    if result.notes:
        st.info("\n\n".join(result.notes))
    if result.product_url:
        st.link_button(lookup_view.product_link_label(result), result.product_url)
    note = lookup_view.read_note(result)
    if note:
        st.caption(note)
    if st.session_state.get("ean_choose_result") is not None:
        st.button("Ander product kiezen", key="ean_back", on_click=_back_to_choice)
    cached_at = st.session_state.get("ean_cached_at")
    if cached_at:
        minutes = max(1, round((time.time() - cached_at) / 60))
        st.caption(
            f"Uit het geheugen van deze sessie ({minutes} min oud); er is niets nieuws opgehaald."
        )
        st.button("Opnieuw ophalen", key="ean_refresh", on_click=_ask_fresh)
    with st.expander("Stappen"):
        for step in result.steps:
            st.write(step)


def _raw_answers():
    log = st.session_state.get("serp_log")
    if not log:
        return
    with st.expander("Ruwe SerpApi-antwoorden (voor controle)"):
        st.caption(
            "De laatste antwoorden van SerpApi, zonder je sleutel. Hiermee kan de parser worden "
            "vergeleken met wat SerpApi echt teruggeeft."
        )
        st.download_button(
            "Download antwoorden (JSON)",
            data=json.dumps(log, ensure_ascii=False, indent=2, default=str),
            file_name="serpapi-antwoorden.json",
            mime="application/json",
            key="serp_download",
        )


def _connection_test(using_serp):
    name = "SerpApi" if using_serp else "Beslist"
    with st.spinner(f"{name} testen..."):
        result = _lookup(lookup.SAMPLE_EAN)
    if result.status == lookup.CHOOSE:
        st.success(f"SerpApi werkt: {len(result.candidates)} producten gevonden voor het testproduct.")
    elif result.worked:
        if using_serp:
            st.success(f"SerpApi werkt: {len(result.offers)} aanbiedingen voor het testproduct.")
        else:
            st.success("Beslist is bereikbaar vanaf de server waar deze app draait.")
    else:
        if using_serp:
            st.error(f"SerpApi werkt niet: {result.message}")
        else:
            st.error(f"Beslist is niet bereikbaar vanaf deze server: {result.message}")
    for step in result.steps:
        st.write(step)


def render():
    if get_script_run_ctx() is None:
        # `import app` (the tests do that to reach the pure helpers) runs this script without a
        # Streamlit session; a form created there leaks into the next AppTest ("forms cannot be
        # nested"). There is nothing to show without a session anyway.
        return
    st.title("\U0001f3af Prijscheck via EAN")
    st.caption("Plak een bol-link of een EAN. Bij een Amazon-link plak je de EAN zelf.")
    using_serp = bool(_api_key())
    if using_serp and "serp_account" not in st.session_state:
        _refresh_account()
    if not using_serp:
        st.warning(
            "Geen SERPAPI_KEY gevonden onder Settings → Secrets. Zonder sleutel wordt alleen "
            "Beslist geprobeerd, en die site weigert servers meestal."
        )

    shared = (st.query_params.get("url") or "").strip()
    # In a form the keyboard's Enter key searches too, and the text is only read when the button
    # is tapped: outside a form a phone could show the result of the previous input.
    form = st.form("ean_form")
    text = form.text_input("bol-link of EAN", value=shared, key="ean_text", placeholder="Plak hier")
    hint = form.text_input(
        "Productnaam (optioneel)",
        key="ean_hint",
        placeholder="bijv. Kérastase Gloss Absolu shampoo",
        help="Google zoekt niet exact op EAN: soms vindt het niets of verkeerde producten. Met een naam zoekt Google op de naam en komt je product bovenaan.",
    )
    clicked = form.form_submit_button("Zoek beste aanbieding", type="primary")

    pick = st.session_state.pop("ean_pick", None)
    if pick is not None:
        _choose(pick)
    elif st.session_state.pop("ean_force", False) and st.session_state.get("ean_last"):
        last_text, last_hint = st.session_state["ean_last"]
        _run(last_text, last_hint, fresh=True)
    elif clicked:
        _run(text, hint)
    elif shared and st.session_state.get("ean_autorun") != shared:
        st.session_state["ean_autorun"] = shared
        _run(shared)

    result = st.session_state.get("ean_result")
    if result is not None:
        _show(result)
    with st.expander("Verbindingstest"):
        if using_serp:
            st.caption("Zoekt een testproduct op via SerpApi (maximaal 2 aanroepen).")
            label = "Test SerpApi"
        else:
            st.caption("Controleert of Beslist bereikbaar is vanaf de server waar deze app draait.")
            label = "Test verbinding met Beslist"
        if st.button(label, key="ean_conn_test"):
            _connection_test(using_serp)

    if using_serp:  # after the test, so the numbers and the download include its calls
        calls = st.session_state.get("serp_calls", 0)
        account = st.session_state.get("serp_account")
        if account:
            st.caption(f"{budget.summary(account, _limit())} Deze sessie: {calls} aanroepen.")
        else:
            st.caption(
                f"SerpApi-aanroepen in deze sessie: {calls}. Het maandverbruik kon niet worden "
                "opgehaald; kijk in je SerpApi-dashboard."
            )
        _raw_answers()
