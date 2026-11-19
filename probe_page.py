"""Phone screen part: test whether shops let the app read a product page (slice A of the ladder).

Texts and layout only; what a page means is decided in `pageprobe.py`. Costs no SerpApi searches.
"""
from __future__ import annotations

import json
import time

import streamlit as st

import fetching
import pageprobe

VERDICT_LABEL = {
    pageprobe.READABLE: "Leesbaar",
    pageprobe.NO_PRODUCT: "Gelezen, geen productgegevens",
    pageprobe.BLOCKED: "Geweigerd door de winkel",
    pageprobe.NOT_FOUND: "Pagina niet gevonden",
    pageprobe.NO_ANSWER: "Geen antwoord",
    pageprobe.ERROR: "Fout van de winkel",
}


def _fetch(url):
    return fetching.fetch_url(url, timeout=20)


def _run(text):
    urls, problems = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        url, reason = pageprobe.clean_url(line)
        if url:
            urls.append(url)
        else:
            problems.append(f"Regel {number}: {reason}")
    st.session_state["probe_problems"] = problems
    st.session_state["probe_cut"] = len(set(urls)) > pageprobe.MAX_URLS
    with st.spinner("Pagina's ophalen..."):
        st.session_state["probe_reports"] = pageprobe.probe(urls, _fetch, sleep=time.sleep)


def _show_report(report):
    with st.container(border=True):
        st.markdown(f"**{report.host}** · {VERDICT_LABEL[report.verdict]}")
        shown = {
            pageprobe.READABLE: st.success,
            pageprobe.BLOCKED: st.error,
        }.get(report.verdict, st.warning)
        shown(report.message)
        if report.verdict not in (pageprobe.READABLE, pageprobe.NO_PRODUCT):
            return
        lines = []
        if report.ean:
            lines.append(f"EAN {report.ean} (uit {report.ean_source})")
        if report.mpns:
            lines.append("MPN (fabrikantcode): " + ", ".join(report.mpns))
        if report.brand:
            lines.append(f"Merk: {report.brand}")
        if report.name:
            lines.append(f"Naam: {report.name} (uit {report.name_source})")
        if report.skus:
            lines.append("Eigen artikelcode van de winkel: " + ", ".join(report.skus))
        if report.variants > 1:
            lines.append(f"{report.variants} varianten in de paginagegevens")
        for line in lines:
            st.caption(line)
        if report.rungs:
            st.caption("Bruikbaar voor: " + ", ".join(report.rungs))


def render():
    st.caption(
        "Plak per regel een productlink van een winkel (maximaal "
        f"{pageprobe.MAX_URLS}). De app haalt elke pagina één keer op, met een eerlijke User-Agent, en "
        "meldt of de winkel de pagina laat lezen en welke codes erin staan. Dit kost geen SerpApi-zoekopdrachten. "
        "Weigert een winkel de pagina, dan stopt het daar: dat wordt niet omzeild."
    )
    form = st.form("probe_form")
    text = form.text_area("Productlinks", key="probe_links", height=120, placeholder="https://www.coolblue.nl/product/...")
    clicked = form.form_submit_button("Test pagina's")
    if clicked:
        _run(text)
    for problem in st.session_state.get("probe_problems", []):
        st.warning(problem)
    reports = st.session_state.get("probe_reports")
    if st.session_state.get("probe_cut"):
        st.caption(f"Alleen de eerste {pageprobe.MAX_URLS} verschillende links zijn getest.")
    for report in reports or []:
        _show_report(report)
    if reports:
        st.download_button(
            "Download paginagegevens (JSON)",
            json.dumps(pageprobe.download_data(reports), ensure_ascii=False, indent=2),
            file_name="winkelpagina-probe.json",
            mime="application/json",
        )
        st.caption("Het bestand bevat de uitslag en de productgegevens van de pagina's, niet de hele pagina.")
