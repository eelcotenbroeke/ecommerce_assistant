"""Streamlit screen for the EAN lookup: paste a bol link or an EAN, get the offer to buy.

Not a pure module: it only places the results of lookup.py / lookup_view.py on the page and
fetches pages through fetching.py. Opened with ?url=<bol link or EAN> (for instance from a phone's
share sheet) it runs the lookup straight away.
"""
import time

import streamlit as st

import fetching
import lookup
import lookup_view


def _run(text):
    with st.spinner("Aanbiedingen ophalen..."):
        st.session_state["ean_result"] = lookup.lookup(text, fetching.fetch_url, sleep=time.sleep)


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
    if result.product_url:
        st.link_button("Bekijk op Beslist", result.product_url)
    note = lookup_view.read_note(result)
    if note:
        st.caption(note)
    with st.expander("Stappen"):
        for step in result.steps:
            st.write(step)


def _connection_test():
    with st.spinner("Beslist testen..."):
        result = lookup.lookup(lookup.SAMPLE_EAN, fetching.fetch_url, sleep=time.sleep)
    if result.worked:
        st.success("Beslist is bereikbaar vanaf de server waar deze app draait.")
    else:
        st.error(f"Beslist is niet bereikbaar vanaf deze server: {result.message}")
    for step in result.steps:
        st.write(step)


def render():
    st.title("\U0001f3af Prijscheck via EAN")
    st.caption("Plak een bol-link of een EAN. Bij een Amazon-link plak je de EAN zelf.")

    shared = (st.query_params.get("url") or "").strip()
    text = st.text_input("bol-link of EAN", value=shared, key="ean_text", placeholder="Plak hier")
    clicked = st.button("Zoek beste aanbieding", type="primary", key="ean_go")

    if clicked:
        _run(text)
    elif shared and st.session_state.get("ean_autorun") != shared:
        st.session_state["ean_autorun"] = shared
        _run(shared)

    result = st.session_state.get("ean_result")
    if result is not None:
        _show(result)

    with st.expander("Verbindingstest"):
        st.caption("Controleert of Beslist bereikbaar is vanaf de server waar deze app draait.")
        if st.button("Test verbinding met Beslist", key="ean_conn_test"):
            _connection_test()
