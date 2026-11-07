"""
Universele E-Commerce Price Sniper (Streamlit, Python 3.10+)

- Haalt een zoekterm uit een product-URL (of gebruik je eigen zoekterm)
- Zoekt via SerpApi (engine "google_shopping"), optioneel verdiept met "google_product"
- Berekent prijs per liter / per 100 ml, inclusief multi-packs ("6 x 330 ml", "3 stuks 100 ml")
- Valt terug op winkel-zoekpagina's i.p.v. Google-tracking-links
"""

import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

SERPAPI_URL = "https://serpapi.com/search"
REQUEST_TIMEOUT = 25

FREE_SHIPPING_THRESHOLD = 40.0
DEFAULT_SHIPPING = 5.95

STORE_SEARCH_URLS = {
    "bol": "https://www.bol.com/nl/nl/s/?searchtext={q}",
    "amazon": "https://www.amazon.nl/s?k={q}",
    "plein": "https://www.plein.nl/zoeken?q={q}",
    "parfumdreams": "https://www.parfumdreams.nl/Vat/Search.aspx?search={q}",
}

UNIT_FACTORS = {
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "ltr": 1000.0,
    "liter": 1000.0,
    "litre": 1000.0,
}

_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(ml|cl|ltr|liter|litre|l)(?![a-z\u00e0-\u00ff'\u2019])"

MULTI_RE = re.compile(rf"(\d+)\s*x\s*{_NUM}\s*{_UNIT}")
SINGLE_RE = re.compile(rf"{_NUM}\s*{_UNIT}")
COUNT_RE = re.compile(
    r"(?:(?:set|pak|pack|verpakking|doos|box)\s+(?:van|of)\s+(\d+))"
    r"|(?:(\d+)\s*-?\s*(?:stuks|stuk|pack|pak|flessen|fles|flesjes|st)(?![a-z\u00e0-\u00ff]))"
)

URL_IGNORE_WORDS = {
    "p", "product", "dp", "nl", "en", "ref", "item", "g", "c", "f", "bp",
    "www", "html", "htm", "aspx", "php",
}


# ---------------------------------------------------------------------------
# Hulpfuncties: getallen en prijzen
# ---------------------------------------------------------------------------

def parse_number(raw):
    """Zet '0,33', '1.5' of '1.000' om naar een float (NL-duizendtallen herkend)."""
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        return float(text.replace(".", ""))
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_price(raw):
    """Zet '€ 1.234,56', '€12,99' of 12.5 om naar een float. Geeft None bij mislukken."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    cleaned = re.sub(r"[^\d.,]", "", str(raw))
    if not cleaned:
        return None

    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")

    if last_dot != -1 and last_comma != -1:
        if last_comma > last_dot:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif last_comma != -1:
        decimals = len(cleaned) - last_comma - 1
        if decimals in (1, 2):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif last_dot != -1:
        decimals = len(cleaned) - last_dot - 1
        if decimals == 3:
            cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def fmt_eur(value):
    """12345.6 -> '€ 12.345,60'"""
    text = f"{value:,.2f}"
    return "€ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


# ---------------------------------------------------------------------------
# Hulpfuncties: URL, links en winkels
# ---------------------------------------------------------------------------

def extract_query_from_url(url):
    if not url:
        return ""

    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    for param in ["q", "search", "searchtext", "query", "keyword", "keywords", "k"]:
        if param in query_params and query_params[param]:
            value = query_params[param][0].strip()
            if value:
                return value

    path_parts = unquote(parsed.path).lower().split("/")
    cleaned_words = []

    for part in path_parts:
        if not part or part.isdigit() or (len(part) <= 2 and part not in ("ml", "l")):
            continue
        subs = [s for s in part.replace("_", "-").split("-") if s]
        for index, sub in enumerate(subs):
            next_sub = subs[index + 1] if index + 1 < len(subs) else ""
            if sub.isdigit() and next_sub in ("ml", "cl", "l", "liter"):
                cleaned_words.append(sub)
                continue
            if sub in URL_IGNORE_WORDS or sub.startswith("b0") or sub.isdigit():
                continue
            cleaned_words.append(sub)

    if cleaned_words:
        return " ".join(cleaned_words[:6])

    return "product"


def is_direct_link(url):
    """True als de link een echte winkelpagina is (geen Google- of tracking-link)."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return False

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(marker in host for marker in ("google.", "googleadservices", "doubleclick", "gstatic.")):
        return False
    if "/aclk" in parsed.path.lower():
        return False
    return True


def pick_direct_link(item):
    """Kies de eerste echte winkellink uit een SerpApi-resultaat (of None)."""
    for key in ("direct_link", "offer_page_link", "link", "product_link"):
        candidate = item.get(key)
        if is_direct_link(candidate):
            return candidate
    return None


def detect_store(merchant_lower):
    name = merchant_lower.strip()
    if "bol.com" in name or name == "bol":
        return "bol"
    if "amazon" in name:
        return "amazon"
    if "plein" in name:
        return "plein"
    if "parfumdreams" in name:
        return "parfumdreams"
    return None


def clean_store_link(raw_link, merchant="", product_title=""):
    """Geef de directe link terug, of anders een zoekpagina van de winkel."""
    if is_direct_link(raw_link):
        return raw_link

    store = detect_store(str(merchant).lower())
    if store:
        query = quote_plus(str(product_title).strip())
        return STORE_SEARCH_URLS[store].format(q=query)

    fallback_query = f"{merchant} {product_title}".strip()
    return "https://www.google.com/search?q=" + quote_plus(fallback_query)


# ---------------------------------------------------------------------------
# Volume en verzendkosten
# ---------------------------------------------------------------------------

def to_ml(amount_text, unit):
    amount = parse_number(amount_text)
    factor = UNIT_FACTORS.get(unit)
    if amount is None or factor is None:
        return None
    return amount * factor


def extract_volume_ml(text):
    """
    Haalt het totale volume in ml uit een tekst.
    Herkent o.a. '6 x 330 ml', '6x0,33l', '2 stuks 50 ml', 'set van 3 ... 100 ml', '1,5 l'.
    Bij 'N stuks' + los volume wordt het volume per stuk aangenomen.
    """
    text_lower = str(text).lower().replace("\u00d7", "x")

    multi = MULTI_RE.search(text_lower)
    if multi:
        count = int(multi.group(1))
        amount = to_ml(multi.group(2), multi.group(3))
        if amount and 0 < count <= 1000:
            return count * amount

    single = SINGLE_RE.search(text_lower)
    if not single:
        return None

    amount = to_ml(single.group(1), single.group(2))
    if not amount or amount <= 0:
        return None

    count = 1
    count_match = COUNT_RE.search(text_lower)
    if count_match:
        found = int(count_match.group(1) or count_match.group(2))
        if 1 <= found <= 1000:
            count = found

    return amount * count


def shipping_from_text(raw):
    """Zet verzendtekst om naar kosten. None als er niets bruikbaars staat."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if "gratis" in text or "free" in text:
        return 0.0
    return parse_price(text)


def estimate_shipping(merchant_lower, price):
    store = detect_store(merchant_lower)
    if store == "bol":
        return 0.0, "\u20ac 0,00 (Bol Select Voordeel)"
    if store == "amazon":
        return 0.0, "\u20ac 0,00 (Amazon Prime Voordeel)"
    if price >= FREE_SHIPPING_THRESHOLD:
        return 0.0, "\u20ac 0,00 (Gratis verzending > \u20ac40)"
    return DEFAULT_SHIPPING, "Ca. \u20ac 5,95"


def resolve_shipping(merchant_lower, price, shipping_raw):
    explicit = shipping_from_text(shipping_raw)
    if explicit is not None:
        if explicit == 0:
            return 0.0, "\u20ac 0,00 (Gratis)"
        return explicit, fmt_eur(explicit)
    return estimate_shipping(merchant_lower, price)


def build_row(merchant, title, price, link, shipping_cost, shipping_text, total_ml, source):
    total_cost = price + shipping_cost
    per_liter = None
    per_100ml = None
    if total_ml and total_ml > 0:
        per_liter = total_cost / (total_ml / 1000.0)
        per_100ml = total_cost / (total_ml / 100.0)

    return {
        "Winkel": merchant,
        "Product": title,
        "Prijs": price,
        "Verzending": shipping_text,
        "Totaal": total_cost,
        "Volume (ml)": total_ml,
        "Per liter": per_liter,
        "Per 100 ml": per_100ml,
        "Bron": source,
        "Link": link,
    }


# ---------------------------------------------------------------------------
# SerpApi
# ---------------------------------------------------------------------------

def fetch_serp(params):
    """Roept SerpApi aan. Geeft (data, foutmelding). data is None bij een harde fout."""
    try:
        res = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return None, f"Netwerkfout: {exc}"

    try:
        data = res.json()
    except ValueError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    if res.status_code != 200:
        message = data.get("error") or f"SerpApi gaf HTTP-status {res.status_code}."
        return None, str(message)

    error = data.get("error")
    return data, (str(error) if error else None)


def extract_online_sellers(detail):
    sellers = detail.get("sellers_results")
    if isinstance(sellers, dict):
        offers = sellers.get("online_sellers")
        if isinstance(offers, list):
            return [offer for offer in offers if isinstance(offer, dict)]
    return []


def run_search(query, api_key, deep_scan, deep_limit):
    """Voert de zoekopdracht uit. Geeft (rijen, notities)."""
    rows = []
    notes = []
    seen_items = set()
    deep_candidates = []

    params = {
        "engine": "google_shopping",
        "q": query,
        "hl": "nl",
        "gl": "nl",
        "api_key": api_key,
    }

    data, error = fetch_serp(params)
    if data is None:
        return rows, [error or "Onbekende fout bij het ophalen van resultaten."]

    shopping_results = data.get("shopping_results") or []
    if not shopping_results:
        notes.append(error or "Geen shopping-resultaten gevonden voor deze zoekterm.")
        return rows, notes

    for item in shopping_results:
        if not isinstance(item, dict):
            continue

        merchant = str(item.get("source") or "Onbekende winkel")
        merchant_lower = merchant.lower()
        title = str(item.get("title") or "Onbekend product")

        numeric_price = parse_price(item.get("extracted_price"))
        if numeric_price is None:
            numeric_price = parse_price(item.get("price"))
        if numeric_price is None:
            continue

        item_identifier = (merchant_lower, round(numeric_price, 2), title.lower())
        if item_identifier in seen_items:
            continue
        seen_items.add(item_identifier)

        link = clean_store_link(pick_direct_link(item), merchant, title)

        snippet = str(item.get("snippet") or "")
        ext_list = item.get("extensions")
        extensions = " ".join(str(e) for e in ext_list) if isinstance(ext_list, list) else ""

        total_ml = extract_volume_ml(title)
        if total_ml is None:
            total_ml = extract_volume_ml(f"{title} {snippet} {extensions}")

        shipping_cost, shipping_text = resolve_shipping(
            merchant_lower, numeric_price, item.get("delivery")
        )

        rows.append(
            build_row(
                merchant, title, numeric_price, link,
                shipping_cost, shipping_text, total_ml, "google_shopping",
            )
        )

        if deep_scan and item.get("product_id") and len(deep_candidates) < deep_limit:
            deep_candidates.append((str(item["product_id"]), title, total_ml))

    for product_id, parent_title, parent_ml in deep_candidates:
        detail_params = {
            "engine": "google_product",
            "product_id": product_id,
            "offers": "1",
            "hl": "nl",
            "gl": "nl",
            "api_key": api_key,
        }
        detail, detail_error = fetch_serp(detail_params)
        short_title = parent_title[:40]

        if detail is None:
            notes.append(f"google_product mislukt voor '{short_title}': {detail_error}")
            continue

        offers = extract_online_sellers(detail)
        if not offers:
            notes.append(f"Geen extra aanbieders gevonden voor '{short_title}'.")
            continue

        for offer in offers:
            offer_merchant = str(offer.get("name") or "Onbekende winkel")
            offer_merchant_lower = offer_merchant.lower()

            offer_price = parse_price(offer.get("base_price"))
            if offer_price is None:
                offer_price = parse_price(offer.get("extracted_price"))
            if offer_price is None:
                continue

            offer_identifier = (offer_merchant_lower, round(offer_price, 2), parent_title.lower())
            if offer_identifier in seen_items:
                continue
            seen_items.add(offer_identifier)

            additional = offer.get("additional_price")
            shipping_raw = additional.get("shipping") if isinstance(additional, dict) else None
            shipping_cost, shipping_text = resolve_shipping(
                offer_merchant_lower, offer_price, shipping_raw
            )

            offer_link = clean_store_link(offer.get("link"), offer_merchant, parent_title)

            rows.append(
                build_row(
                    offer_merchant, parent_title, offer_price, offer_link,
                    shipping_cost, shipping_text, parent_ml, "google_product",
                )
            )

    return rows, notes


# ---------------------------------------------------------------------------
# UI-hulpfuncties
# ---------------------------------------------------------------------------

def get_serp_key_from_secrets():
    try:
        if "SERPAPI_KEY" in st.secrets:
            return str(st.secrets["SERPAPI_KEY"])
    except Exception:
        pass
    return ""


def link_column():
    try:
        return st.column_config.LinkColumn("Link", display_text="Open winkel")
    except TypeError:
        return st.column_config.LinkColumn("Link")


def render_results(snipe):
    for note in snipe["notes"]:
        st.warning(note)

    rows = snipe["rows"]
    if not rows:
        st.warning("Geen bruikbare resultaten gevonden.")
        return

    st.subheader(f"Resultaten voor: {snipe['query']}")

    sort_choice = st.radio(
        "Sorteer op:",
        ["Prijs per liter", "Totaalprijs"],
        horizontal=True,
    )
    sort_column = "Per liter" if sort_choice == "Prijs per liter" else "Totaal"

    df = pd.DataFrame(rows)
    df = df.sort_values(sort_column, ascending=True, na_position="last").reset_index(drop=True)

    with_volume = df[df["Per liter"].notna()]
    if not with_volume.empty:
        best = with_volume.sort_values("Per liter").iloc[0]
        st.success(
            f"Beste deal per liter: {best['Winkel']} voor {fmt_eur(best['Per liter'])} per liter "
            f"({fmt_eur(best['Totaal'])} totaal, {best['Volume (ml)']:.0f} ml)."
        )
        st.link_button("Open beste deal", best["Link"])
    else:
        st.info("Bij geen enkel resultaat kon een volume worden herkend, dus er is geen prijs per liter.")

    st.dataframe(
        df,
        hide_index=True,
        column_config={
            "Product": st.column_config.TextColumn("Product", width="large"),
            "Prijs": st.column_config.NumberColumn("Prijs", format="\u20ac %.2f"),
            "Totaal": st.column_config.NumberColumn("Totaal incl. verz.", format="\u20ac %.2f"),
            "Volume (ml)": st.column_config.NumberColumn("Volume (ml)", format="%.0f"),
            "Per liter": st.column_config.NumberColumn("Per liter", format="\u20ac %.2f"),
            "Per 100 ml": st.column_config.NumberColumn("Per 100 ml", format="\u20ac %.2f"),
            "Link": link_column(),
        },
    )

    st.caption(
        "Per liter en per 100 ml zijn berekend over prijs plus (geschatte) verzendkosten. "
        "Bij 'N stuks + volume' in de titel wordt het volume per stuk aangenomen."
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Price Sniper App", page_icon="\U0001f3af", layout="wide")

st.title("\U0001f3af Universele E-Commerce Price Sniper")
st.write("Automatische prijs- en volume-sniper met directe winkel-zoeklinks.")

product_url = st.text_input("Product URL:", placeholder="Plak hier de product-URL...")

serp_key = get_serp_key_from_secrets()
if not serp_key:
    serp_key = st.text_input("SerpApi Key:", type="password", placeholder="Jouw SerpApi Key")

default_query = extract_query_from_url(product_url) if product_url else ""
search_query = st.text_input(
    "Actieve zoekterm (inclusief gedetecteerd merk):",
    value=default_query,
)

detected_ml = extract_volume_ml(search_query) if search_query else None
if detected_ml:
    st.caption(f"Gedetecteerd volume in zoekterm: {detected_ml:.0f} ml")

with st.expander("Geavanceerd"):
    deep_scan = st.checkbox(
        "Diepe scan via google_product (kost 1 extra API-credit per product)",
        value=False,
    )
    deep_limit = st.slider(
        "Aantal producten voor diepe scan",
        min_value=1,
        max_value=5,
        value=3,
        disabled=not deep_scan,
    )

if st.button("Snipe Prijzen", type="primary"):
    if not serp_key:
        st.warning("Geen SerpApi Key gevonden. Stel die in via Secrets of vul hem hierboven in.")
    elif not search_query.strip():
        st.warning("Vul een product-URL of een zoekterm in.")
    else:
        with st.spinner("Markt aan het doorzoeken..."):
            result_rows, result_notes = run_search(
                search_query.strip(), serp_key, deep_scan, deep_limit
            )
        st.session_state["snipe"] = {
            "query": search_query.strip(),
            "rows": result_rows,
            "notes": result_notes,
        }

saved_snipe = st.session_state.get("snipe")
if saved_snipe:
    render_results(saved_snipe)
