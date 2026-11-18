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


def _guard():
    """Reason not to search (budget used up), or None. Unknown usage does not block."""
    account = _refresh_account()
    return budget.check(account, _limit()) if account else None


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


def _lookup(text):
    key = _api_key()
    source = None
    if key:
        fetch_json = fetching.serp_fetcher(key, recorder=_record)
        source = serp.serp_source(fetch_json, now=_now, guard=_guard)
    result = lookup.lookup(text, fetching.fetch_url, sleep=time.sleep, now=_now, source=source)
    if key:
        _refresh_account()  # the usage on the screen includes this lookup
    return result


def _run(text, fresh=False):
    text = text.strip()
    cache = st.session_state.setdefault("ean_cache", {})
    entry = cache.get(text)
    st.session_state["ean_last"] = text
    if entry and not fresh and time.time() - entry[0] < CACHE_SECONDS:
        st.session_state["ean_result"] = entry[1]
        st.session_state["ean_cached_at"] = entry[0]
        return
    with st.spinner("Aanbiedingen ophalen..."):
        result = _lookup(text)
    st.session_state["ean_result"] = result
    st.session_state["ean_cached_at"] = None
    if result.worked:
        cache[text] = (time.time(), result)
    else:
        cache.pop(text, None)


def _ask_fresh():
    st.session_state["ean_force"] = True


def _show(result):
    shown = getattr(st, lookup_view.severity(result))
    headline = lookup_view.headline(result)
    shown(f"**{headline}**" if headline else result.message)
    if headline:
        st.write(lookup_view.explanation(result))
    tip = lookup_view.hint(result)
    if tip:
        st.info(tip)
    if result.offers:
        st.dataframe(lookup_view.offer_rows(result), hide_index=True, use_container_width=True)
    line = lookup_view.source_line(result)
    if line:
        st.caption(line)
    if result.notes:
        st.info("\n\n".join(result.notes))
    link = lookup_view.pick_link(result)
    if link:
        st.link_button(*link)
    if result.product_url:
        st.link_button(lookup_view.product_link_label(result), result.product_url)
    note = lookup_view.read_note(result)
    if note:
        st.caption(note)
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
    if result.worked:
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
    text = st.text_input("bol-link of EAN", value=shared, key="ean_text", placeholder="Plak hier")
    clicked = st.button("Zoek beste aanbieding", type="primary", key="ean_go")

    if st.session_state.pop("ean_force", False) and st.session_state.get("ean_last"):
        _run(st.session_state["ean_last"], fresh=True)
    elif clicked:
        _run(text)
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
