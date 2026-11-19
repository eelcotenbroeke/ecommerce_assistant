"""Testrun: run a table of products through the lookup and write down what happened.

Pure helpers (no network, no Streamlit). The screen (ean_page.py) feeds them a pasted table, runs
the lookups and calls `record` once per run; this module only reads the table, decides which
product a user would tap in a list of candidates, and turns a lookup result into one flat,
JSON-ready record. What counts as better or cheaper than Google Shopping is worked out afterwards,
from the file, not here.

The table has the columns nr, categorie, url, zoekwoorden, product. `product` is the ground truth
(brand, product, size) used to judge matches and to tap the right candidate; it is never searched.
Every row is run twice: on the link and on the keywords exactly as typed. Even `nr` is the
holdout (the verdict set), odd `nr` is for development; that rule was fixed before any result.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import serp
from choice import effective_offer, is_preferred
from ean import parse_product_url

COLUMNS = ("nr", "categorie", "url", "zoekwoorden", "product")
LINK = "link"
KEYWORDS = "zoekwoorden"
MIN_PICK_SCORE = 0.5  # a tapped candidate with a lower share of the product's words is marked as doubtful
SEARCHES_PER_LOOKUP = 2  # candidate list plus the shops of the chosen product

ALL = "alle"
EVEN = "even"
ODD = "oneven"


@dataclass(frozen=True)
class Row:
    nr: int
    categorie: str
    url: str
    zoekwoorden: str
    product: str

    @property
    def holdout(self):
        """Even numbers are the verdict set (fixed 2026-11-19, before any result)."""
        return self.nr % 2 == 0


@dataclass(frozen=True)
class Pick:
    candidate: object
    score: float  # share of the product's words in the candidate's title (0..1)
    place: int  # position in the list the owner would see (1 = first)
    doubtful: bool


# --------------------------------------------------------------------------- the table


def _delimiter(line):
    counts = {delimiter: line.count(delimiter) for delimiter in ("\t", ";", ",")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else "\t"


def parse_table(text):
    """(rows, problems) from a table pasted out of a spreadsheet (tab-separated; ; and , also work).

    The first line may be a header with the column names; without one the columns are taken in
    the order nr, categorie, url, zoekwoorden, product. A row with neither a link nor keywords is
    skipped and reported.
    """
    text = (text or "").lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], ["De tabel is leeg."]
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=_delimiter(lines[0]))
    table = [[cell.strip() for cell in cells] for cells in reader]
    table = [cells for cells in table if any(cells)]

    names = [cell.lower() for cell in table[0]]
    if sum(name in COLUMNS for name in names) >= 2:
        header, body = names, table[1:]
        missing = [column for column in ("url", "zoekwoorden", "product") if column not in header]
        if missing:
            return [], ["Kolom ontbreekt: " + ", ".join(missing) + "."]
    else:
        header, body = list(COLUMNS), table

    rows, problems, seen = [], [], set()
    for position, cells in enumerate(body, start=1):
        data = {name: (cells[index] if index < len(cells) else "") for index, name in enumerate(header)}
        try:
            nr = int(float(data.get("nr", "")))
        except ValueError:
            nr = position
            problems.append(f"Regel {position}: geen nummer bij nr; regel {position} is gebruikt.")
        if nr in seen:
            problems.append(f"Nummer {nr} komt twee keer voor.")
        seen.add(nr)
        url, keywords = data.get("url", ""), data.get("zoekwoorden", "")
        if not url and not keywords:
            problems.append(f"Nummer {nr}: geen link en geen zoekwoorden, overgeslagen.")
            continue
        rows.append(Row(nr, data.get("categorie", ""), url, keywords, data.get("product", "")))
    return rows, problems


def tasks(rows, subset=ALL, links=True, keywords=True):
    """[(row, mode)] to run: all keyword runs first (they carry the price test), then the link runs."""
    chosen = [r for r in rows if subset == ALL or (subset == EVEN) == r.holdout]
    plan = []
    if keywords:
        plan += [(r, KEYWORDS) for r in chosen if r.zoekwoorden]
    if links:
        plan += [(r, LINK) for r in chosen if r.url]
    return plan


def text_of(row, mode):
    return row.url if mode == LINK else row.zoekwoorden


def max_searches(plan):
    """Most SerpApi searches the plan can cost: a keyword run, and a link of a shop whose page may be read."""
    total = 0
    for row, mode in plan:
        if mode == KEYWORDS or parse_product_url(row.url) is not None:
            total += SEARCHES_PER_LOOKUP
    return total


# --------------------------------------------------------------------------- tapping a candidate


def pick_candidate(candidates, truth):
    """The candidate a user who knows the product (`truth`) would tap, or None without candidates.

    Best share of the product's words in the title; of equal ones the tightest title (the fewest
    extra words), then Google's own order. A weak match is marked doubtful, but still taken: the
    file lists every candidate, so a wrong tap can be seen afterwards.
    """
    if not candidates:
        return None

    def key(item):
        index, candidate = item
        return (
            -serp.similarity(truth, candidate.title),
            -serp.similarity(candidate.title, truth),
            candidate.position,
            index,
        )

    index, candidate = min(enumerate(candidates), key=key)
    score = round(serp.similarity(truth, candidate.title), 3)
    return Pick(candidate, score, index + 1, score < MIN_PICK_SCORE)


# --------------------------------------------------------------------------- one record


def _offer(offer):
    effective = effective_offer(offer)
    known = effective.shipping is not None
    return {
        "winkel": offer.shop,
        "prijs": offer.price,
        "verzending": offer.shipping,
        "verzending_effectief": effective.shipping,
        "verzending_bekend": known,
        "totaal_onbekend_nul": effective.total,
        "totaal_bekend": effective.total if known else None,
        "leverbaar": offer.deliverable,
        "levering": offer.delivery,
        "voorkeurswinkel": is_preferred(offer.shop),
        "url": offer.url,
        "titel": offer.title,
    }


def _cheapest(offers, total_key):
    """The cheapest deliverable offer by the given total, as {winkel, totaal}; None when there is none."""
    usable = [o for o in offers if o["leverbaar"] and o[total_key] is not None]
    if not usable:
        return None
    best = min(usable, key=lambda o: o[total_key])
    return {"winkel": best["winkel"], "totaal": best[total_key]}


def _candidate(candidate):
    return {
        "titel": candidate.title,
        "winkels": list(candidate.shops),
        "prijs_min": candidate.price_min,
        "prijs_max": candidate.price_max,
        "positie_google": candidate.position + 1,
        "score_zoekwoorden": candidate.score,
    }


def record(row, mode, result, choose=None, pick=None, searches=0, seconds=None, raw=()):
    """One flat, JSON-ready record of a run.

    `result` is the final LookupResult. When the first answer was a list of candidates, `choose`
    is that list result and `pick` the candidate that was tapped; otherwise the product was
    settled without a list (or nothing was found).
    """
    offers = [_offer(o) for o in result.offers]
    advice = result.recommendation
    match = serp.similarity(row.product, result.product_title) if result.product_title else None
    return {
        "nr": row.nr,
        "categorie": row.categorie,
        "holdout": row.holdout,
        "soort": mode,
        "invoer": text_of(row, mode),
        "product": row.product,
        "status": result.status,
        "melding": result.message,
        "ean": result.ean,
        "zoekterm": result.query,
        "bron": result.source,
        "gevonden_product": result.product_title,
        "overeenkomst_met_product": None if match is None else round(match, 3),
        "lijst_getoond": choose is not None,
        "lijst": [_candidate(c) for c in choose.candidates] if choose is not None else [],
        "gekozen": None
        if pick is None
        else {
            "titel": pick.candidate.title,
            "score_product": pick.score,
            "plaats_in_lijst": pick.place,
            "twijfel": pick.doubtful,
        },
        "aantal_winkels": len(offers),
        "winkels": offers,
        "onbekende_verzending": sum(1 for o in offers if o["leverbaar"] and not o["verzending_bekend"]),
        "goedkoopste_onbekend_nul": _cheapest(offers, "totaal_onbekend_nul"),
        "goedkoopste_bekend": _cheapest(offers, "totaal_bekend"),
        "aanbevolen": None
        if advice is None
        else {
            "winkel": advice.pick.shop,
            "totaal": advice.pick.total,
            "reden": advice.reason,
            "extra": advice.extra,
        },
        "gelezen": result.read_at.isoformat(timespec="seconds") if result.read_at else None,
        "zoekopdrachten": searches,
        "seconden": None if seconds is None else round(seconds, 1),
        "stappen": list(result.steps),
        "opmerkingen": list(result.notes),
        "ruwe_antwoorden": list(raw),
    }


def results_document(records, meta=None):
    """The file the owner downloads: what was run, and one record per run."""
    return {"meta": dict(meta or {}), "regels": list(records)}


# --------------------------------------------------------------------------- the screen's summary


def _eur(amount):
    return "" if amount is None else f"{amount:.2f}".replace(".", ",")


def summary_rows(records):
    """Short rows for a table on the screen, one per run."""
    rows = []
    for item in records:
        cheapest = item["goedkoopste_onbekend_nul"]
        advice = item["aanbevolen"]
        rows.append(
            {
                "nr": item["nr"],
                "soort": item["soort"],
                "status": item["status"],
                "gekozen": (item["gekozen"] or {}).get("titel") or item["gevonden_product"] or "",
                "twijfel": bool((item["gekozen"] or {}).get("twijfel")),
                "winkels": item["aantal_winkels"],
                "goedkoopste": "" if cheapest is None else f"{cheapest['winkel']} {_eur(cheapest['totaal'])}",
                "aanbevolen": "" if advice is None else f"{advice['winkel']} {_eur(advice['totaal'])}",
                "zoekopdrachten": item["zoekopdrachten"],
            }
        )
    return rows
