"""
Universele E-Commerce Price Sniper (Streamlit, Python 3.10+)

- Haalt een zoekterm uit een product-URL (of gebruik je eigen zoekterm)
- Zoekt via SerpApi (engine "google_shopping"), optioneel verdiept met "google_product"
- Berekent prijs per liter / per 100 ml, inclusief multi-packs en sets
- Spoort acties zoals "1+1 gratis" op door winkelpagina's te controleren (en laat je
  per kaart handmatig een actie kiezen)
- Valt terug op winkel-zoekpagina's i.p.v. Google-tracking-links
- Mobielvriendelijk: resultaten als kaarten (standaard) of als tabel
"""

import html
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import pandas as pd
import requests
import streamlit as st

import ean_page

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

SERPAPI_URL = "https://serpapi.com/search"
REQUEST_TIMEOUT = 25
SERP_TIMEOUT = 60
SERP_ATTEMPTS = 2

PAGE_TIMEOUT = 8
MAX_PAGE_CHARS = 1_500_000
DEFAULT_PAGE_CHECKS = 5
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
}

FREE_SHIPPING_THRESHOLD = 40.0
DEFAULT_SHIPPING = 5.95

INITIAL_CARDS = 8

STORE_SEARCH_URLS = {
    "bol": "https://www.bol.com/nl/nl/s/?searchtext={q}",
    "amazon": "https://www.amazon.nl/s?k={q}",
    "plein": "https://www.plein.nl/zoeken?q={q}",
    "parfumdreams": "https://www.parfumdreams.nl/Vat/Search.aspx?search={q}",
}

# Vergelijkingssites voor de "Zoek ook op"-links.
# search_url = None  -> Google site:-zoekopdracht op dat domein (werkt altijd).
# search_url = "https://.../zoek?q={q}" -> directe zoeklink; {q} wordt de zoekterm.
COMPARISON_SITES = [
    {"name": "Beslist.nl", "domain": "beslist.nl", "search_url": None},
    {"name": "Kieskeurig.nl", "domain": "kieskeurig.nl", "search_url": None},
    {"name": "Kelkoo.nl", "domain": "kelkoo.nl", "search_url": None},
]

# Acties: label -> (aantal stuks dat je koopt, aantal stuks dat je betaalt)
PROMO_RULES = {
    "Geen": None,
    "1+1 gratis": (2, 1.0),
    "2e halve prijs": (2, 1.5),
    "2+1 gratis": (3, 2.0),
    "3 voor 2": (3, 2.0),
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

ITEM_RE = re.compile(rf"(?:(\d+)\s*x\s*)?{_NUM}\s*{_UNIT}")
COUNT_RE = re.compile(
    r"(?:(?:set|pak|pack|packung|verpakking|doos|box)\s+(?:van|of|mit)\s+(\d+))"
    r"|(?:(\d+)\s*-?\s*(?:stuks|stuk|st\u00fcck|stk|pack|packung|pak|flessen|fles|flesjes|st)(?![a-z\u00e0-\u00ff]))"
    r"|(?:(\d+)er[\s-]?(?:pack|packung|set|pak))"
)
COUNT_X_RE = re.compile(r"(?<![\w.,])(\d+)\s*x(?![a-z0-9\u00e0-\u00ff])")
SEGMENT_SPLIT_RE = re.compile(r"[|+;]")
BUNDLE_RE = re.compile(
    r"\b(?:\w*set|pakket|bundel|duo|trio|combi\w*|geschenk\w*|cadeau\w*|kit)\b"
)
UNIT_FAMILY = {"ml": "ml", "cl": "cl", "l": "l", "ltr": "l", "liter": "l", "litre": "l"}

PROMO_PLUS_RE = re.compile(r"\b(\d)\s*\+\s*(\d)\s*gratis")
PROMO_SECOND_FREE_RE = re.compile(r"\b(?:2e|2de|tweede)\s+(?:product\s+)?gratis")
PROMO_SECOND_HALF_RE = re.compile(
    r"\b(?:2e|2de|tweede)\s+(?:product\s+)?(?:voor\s+de\s+helft|halve\s+prijs|half\s+geld)"
    r"|\b50\s*%\s*korting\s+op\s+(?:de\s+)?(?:2e|2de|tweede)"
)
PROMO_FOR_RE = re.compile(r"\b(\d)\s*voor\s*(\d)\b(?![.,]?\d)(?!\s*(?:\u20ac|eur))")

BOL_TOKEN_URL = "https://login.bol.com/token"
BOL_API_BASE = "https://api.bol.com/marketing/catalog/v1"
BOL_PAGE_SIZE = 50

MATCH_LEVELS = ["Strikt", "Gemiddeld", "Alles"]
MATCH_HELP = (
    "Strikt: zelfde product, zelfde producttype en zelfde inhoud (multipacks van dezelfde "
    "verpakking mogen), geen sets. Gemiddeld: zelfde product en producttype, inhoud en sets "
    "mogen afwijken. Alles: geen filter."
)

STOPWORDS = {
    "de", "het", "een", "voor", "met", "en", "van", "the", "for", "and", "with", "of",
    "bij", "op", "in", "set", "stuks", "stuk", "pack", "nieuw", "new", "original",
    "originele",
}

# Producttype-families. Woorden in SUBSTRING_OK mogen ook midden in een woord voorkomen
# ('babyshampoo'); de rest moet een heel woord zijn.
TYPE_FAMILIES = {
    "shampoo": ["shampoo", "shampooing", "shampoing", "bain"],
    "conditioner": ["conditioner", "conditionneur", "fondant", "spoeling"],
    "masker": ["masker", "mask", "masque", "haarmasker"],
    "serum": ["serum"],
    "olie": ["olie", "oil", "huile"],
    "spray": ["spray"],
    "creme": ["creme", "cream"],
    "lotion": ["lotion"],
    "gel": ["douchegel", "douchecreme", "bodywash", "gel"],
    "deodorant": ["deodorant", "deo"],
    "parfum": ["parfum", "edp", "edt", "eau"],
}
SUBSTRING_OK = {
    "shampoo", "shampooing", "shampoing", "conditioner", "conditionneur", "masker",
    "masque", "haarmasker", "serum", "huile", "spray", "douchegel", "douchecreme",
    "bodywash", "deodorant", "lotion",
}
BUNDLE_FALSE_POSITIVES = {"reset", "offset", "onset", "upset", "unset", "preset", "inset"}

URL_IGNORE_WORDS = {
    "p", "product", "dp", "nl", "en", "ref", "item", "g", "c", "f", "bp",
    "www", "html", "htm", "aspx", "php",
}

MOBILE_CSS = """
<style>
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"], .block-container {
        padding: 1.25rem 0.75rem 3rem 0.75rem;
    }
    h1 {
        font-size: 1.6rem !important;
        line-height: 1.25 !important;
    }
}
</style>
"""


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
    return "\u20ac " + text.replace(",", "_").replace(".", ",").replace("_", ".")


def md_escape(text):
    """Escape markdown-tekens zodat titels (met $, *, _ enz.) letterlijk getoond worden."""
    escaped = str(text)
    for char in ("\\", "`", "*", "_", "$", "[", "]", "<", ">", "~", "#"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def safe_md_url(url):
    """Maak een URL veilig voor gebruik in een markdown-link."""
    return str(url).replace(" ", "%20").replace("(", "%28").replace(")", "%29")


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


def _find_count(segment):
    """Zoekt een aantal-aanduiding ('3x', '2 stuks', 'set van 3', '3er pack') in een tekstdeel."""
    match = COUNT_RE.search(segment)
    if match:
        value = next((g for g in match.groups() if g), None)
    else:
        match = COUNT_X_RE.search(segment)
        value = match.group(1) if match else None

    if value is None:
        return 1
    count = int(value)
    return count if 1 <= count <= 1000 else 1


def _parse_segment(segment, bundle):
    """
    Leest het volume uit een tekstdeel (een product of een set-onderdeel).
    Geeft (totaal_ml, uitleg) of None als er geen volume in staat.
    """
    items = []
    seen = []
    explicit_count = False

    for match in ITEM_RE.finditer(segment):
        per_item = to_ml(match.group(2), match.group(3))
        if not per_item or per_item <= 0:
            continue

        count = int(match.group(1)) if match.group(1) else 1
        if not 1 <= count <= 1000:
            count = 1

        family = UNIT_FAMILY[match.group(3)]
        item_total = per_item * count

        # Hetzelfde volume in een andere eenheid, bijv. '330 ml (0,33 l)', niet dubbel tellen.
        is_duplicate = any(
            abs(item_total - prev_total) <= 0.01 * item_total and family != prev_family
            for prev_total, prev_family in seen
        )
        if is_duplicate:
            continue
        seen.append((item_total, family))

        if match.group(1):
            explicit_count = True

        if count > 1:
            label = f"{count} \u00d7 {per_item:g} ml"
        else:
            label = f"{per_item:g} ml"
        items.append((item_total, label))

    if not items:
        return None

    chosen = items if (bundle and len(items) > 1) else items[:1]
    total = sum(entry[0] for entry in chosen)
    labels = [entry[1] for entry in chosen]
    label = " + ".join(labels)

    if not explicit_count:
        count = _find_count(segment)
        if count > 1:
            total *= count
            if len(labels) == 1:
                label = f"{count} \u00d7 {label}"
            else:
                label = f"{count} \u00d7 ({label})"

    return total, label


def extract_volume_info(text, sum_segments=True):
    """
    Haalt het totale volume in ml uit een tekst, plus een korte uitleg van de berekening.
    Geeft (totaal_ml, uitleg), of (None, "") als er geen volume is gevonden.

    Herkent o.a.:
      '6 x 330 ml', '6x0,33l', '3x Product (400 ml)', '2 stuks 50 ml',
      'set van 3 ... 100 ml', '3er Pack 250 ml', '1,5 l', '75 cl'.
    Delen gescheiden door '|', '+' of ';' worden opgeteld (sets met meerdere producten).
    Hetzelfde volume in twee eenheden ('330 ml (0,33 l)') telt maar een keer.
    Met sum_segments=False wordt alleen het eerste gevonden volume gebruikt.
    """
    text_lower = str(text).lower().replace("\u00d7", "x")
    bundle = sum_segments and bool(BUNDLE_RE.search(text_lower))

    parsed = []
    seen_segments = set()
    for segment in SEGMENT_SPLIT_RE.split(text_lower):
        key = segment.strip()
        if not key or key in seen_segments:
            continue
        seen_segments.add(key)
        result = _parse_segment(segment, bundle)
        if result:
            parsed.append(result)

    if not parsed:
        return None, ""

    chosen = parsed if sum_segments else parsed[:1]
    total = sum(entry[0] for entry in chosen)
    joined = " + ".join(entry[1] for entry in chosen)

    if len(chosen) > 1 or "\u00d7" in joined or " + " in joined:
        note = f"{joined} = {total:g} ml"
    else:
        note = f"{total:g} ml"

    return total, note


def extract_volume_ml(text, sum_segments=True):
    """Alleen het totale volume in ml (of None)."""
    return extract_volume_info(text, sum_segments)[0]


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


def build_row(
    merchant, title, price, link, shipping_cost, shipping_text, total_ml, source,
    volume_note="", direct_link=False,
):
    total_cost = price + shipping_cost
    per_liter = None
    per_100ml = None
    if total_ml and total_ml > 0:
        per_liter = total_cost / (total_ml / 1000.0)
        per_100ml = total_cost / (total_ml / 100.0)

    return {
        "ID": 0,
        "Winkel": merchant,
        "Product": title,
        "Prijs": price,
        "Verzending": shipping_text,
        "Totaal": total_cost,
        "Volume (ml)": total_ml,
        "Volume-berekening": volume_note,
        "Per liter": per_liter,
        "Per 100 ml": per_100ml,
        "Bron": source,
        "Link": link,
        "Directe link": direct_link,
        "Actie (gedetecteerd)": "",
        "Actie-aantal": 0,
        "Actie-betaal": 0.0,
        "Actie-tekst": "",
    }


# ---------------------------------------------------------------------------
# Acties (1+1 gratis, 2e halve prijs, ...)
# ---------------------------------------------------------------------------

def _make_promo(label, units, paid, source_text, match):
    start = max(0, match.start() - 20)
    end = min(len(source_text), match.end() + 170)
    snippet = " ".join(source_text[start:end].split())
    return {"label": label, "units": units, "paid": paid, "snippet": snippet}


def detect_promo(text):
    """
    Zoekt een multi-buy actie in de tekst van een winkelpagina.
    Geeft {"label", "units", "paid", "snippet"} of None.
    Procentuele kortingen worden bewust genegeerd: die zitten meestal al in de feedprijs.
    """
    lowered = str(text).lower()
    source = str(text) if len(str(text)) == len(lowered) else lowered

    match = PROMO_PLUS_RE.search(lowered)
    if match:
        paid = int(match.group(1))
        free = int(match.group(2))
        if paid >= 1 and free >= 1 and paid + free <= 6:
            return _make_promo(f"{paid}+{free} gratis", paid + free, float(paid), source, match)

    match = PROMO_SECOND_FREE_RE.search(lowered)
    if match:
        return _make_promo("1+1 gratis", 2, 1.0, source, match)

    match = PROMO_SECOND_HALF_RE.search(lowered)
    if match:
        return _make_promo("2e halve prijs", 2, 1.5, source, match)

    match = PROMO_FOR_RE.search(lowered)
    if match:
        buy = int(match.group(1))
        pay = int(match.group(2))
        if 2 <= buy <= 6 and 1 <= pay < buy:
            return _make_promo(f"{buy} voor {pay}", buy, float(pay), source, match)

    return None


def fetch_page_text(url):
    """Haalt een winkelpagina op en geeft de zichtbare tekst terug (None bij mislukken)."""
    try:
        res = requests.get(url, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
    except requests.RequestException:
        return None

    if res.status_code != 200:
        return None
    if "html" not in str(res.headers.get("Content-Type", "")).lower():
        return None

    raw_html = res.text[:MAX_PAGE_CHARS]
    raw_html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw_html)
    raw_html = re.sub(r"(?s)<[^>]+>", " ", raw_html)
    return " ".join(html.unescape(raw_html).split())


def apply_page_promos(rows, limit):
    """
    Controleert de pagina's van de beste resultaten op acties en vult de rijen aan.
    Geeft een lijst met infoberichten terug.
    """
    candidates = [row for row in rows if row.get("Directe link")]
    candidates.sort(
        key=lambda r: (
            r["Per liter"] is None,
            r["Per liter"] if r["Per liter"] is not None else r["Totaal"],
        )
    )

    targets = []
    seen_urls = set()
    for row in candidates:
        if row["Link"] in seen_urls:
            continue
        seen_urls.add(row["Link"])
        targets.append(row)
        if len(targets) >= limit:
            break

    if not targets:
        return []

    with ThreadPoolExecutor(max_workers=min(5, len(targets))) as pool:
        texts = list(pool.map(fetch_page_text, [row["Link"] for row in targets]))

    readable = 0
    found = 0
    for row, text in zip(targets, texts):
        if not text:
            continue
        readable += 1
        promo = detect_promo(text)
        if promo:
            found += 1
            row["Actie (gedetecteerd)"] = promo["label"]
            row["Actie-aantal"] = promo["units"]
            row["Actie-betaal"] = promo["paid"]
            row["Actie-tekst"] = promo["snippet"]

    info = (
        f"{readable} van {len(targets)} winkelpagina's gecontroleerd op acties; "
        f"{found} actie(s) gevonden."
    )
    if readable < len(targets):
        info += " Sommige winkels blokkeren automatische bezoeken."
    return [info]


def resolve_promo(row, chosen_label):
    """Geeft (aantal_kopen, aantal_betalen) voor het gekozen label, of None."""
    if not chosen_label or chosen_label == "Geen":
        return None
    if chosen_label == row.get("Actie (gedetecteerd)") and row.get("Actie-aantal"):
        return int(row["Actie-aantal"]), float(row["Actie-betaal"])
    return PROMO_RULES.get(chosen_label)


def promo_widget_key(token, row_id):
    return f"promo_widget_{token}_{row_id}"


def get_promo_choice(token, row_id, default_label):
    choices = st.session_state.setdefault("promo_choices", {})
    return choices.get((token, row_id), default_label)


def remember_promo_choice(token, row_id):
    key = promo_widget_key(token, row_id)
    st.session_state.setdefault("promo_choices", {})[(token, row_id)] = st.session_state[key]


def build_dataframe(rows, token):
    """Maakt de resultaattabel en rekent de gekozen acties door."""
    records = []
    for row in rows:
        record = dict(row)
        detected = str(row.get("Actie (gedetecteerd)") or "")
        label = get_promo_choice(token, row["ID"], detected or "Geen")
        rule = resolve_promo(row, label)

        total_promo = None
        volume_promo = None
        per_liter_promo = None
        units = 1

        if rule:
            units, paid = rule
            shipping = row["Totaal"] - row["Prijs"]
            total_promo = paid * row["Prijs"] + shipping
            if row["Volume (ml)"]:
                volume_promo = units * row["Volume (ml)"]
                per_liter_promo = total_promo / (volume_promo / 1000.0)

        record["Actie"] = label
        record["Koop-aantal"] = units
        record["Totaal (actie)"] = total_promo
        record["Volume (actie)"] = volume_promo
        record["Per liter (actie)"] = per_liter_promo
        record["Sorteerwaarde"] = per_liter_promo if per_liter_promo is not None else row["Per liter"]
        records.append(record)

    df = pd.DataFrame(records)
    for column in (
        "Volume (ml)", "Per liter", "Per 100 ml", "Totaal (actie)",
        "Volume (actie)", "Per liter (actie)", "Sorteerwaarde",
    ):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Relevantie: houdt alleen het gezochte product over
# ---------------------------------------------------------------------------

def normalize_text(text):
    """Kleine letters, zonder accenten of leestekens: 'K\u00e9rastase & Co' -> 'kerastase co'."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.lower().replace("&", " ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped).split())


def query_tokens(query):
    """Betekenisvolle woorden uit de zoekterm (zonder volume-aanduidingen en stopwoorden)."""
    cleaned = ITEM_RE.sub(" ", str(query).lower().replace("\u00d7", "x"))
    tokens = []
    for tok in normalize_text(cleaned).split():
        if tok in STOPWORDS or (len(tok) < 2 and not tok.isdigit()):
            continue
        if tok not in tokens:
            tokens.append(tok)
    return tokens


def token_in_title(token, words, compact):
    if token in words:
        return True
    return len(token) >= 4 and token in compact


def detect_families(words, compact):
    found = set()
    for family, names in TYPE_FAMILIES.items():
        for name in names:
            if name in words or (name in SUBSTRING_OK and name in compact):
                found.add(family)
                break
    return found


def build_match_context(query, excluded_text):
    norm_query = normalize_text(query)
    q_words = set(norm_query.split())
    excluded = []
    for part in re.split(r"[,;\n]+", str(excluded_text or "")):
        word = normalize_text(part)
        if word and word not in excluded:
            excluded.append(word)
    return {
        "tokens": query_tokens(query),
        "families": detect_families(q_words, norm_query.replace(" ", "")),
        "query_ml": extract_volume_ml(query),
        "excluded": excluded,
    }


def judge_row(row, ctx, level):
    """Geeft None als het resultaat past, anders een korte reden voor afwijzing."""
    title = str(row["Product"])
    title_norm = normalize_text(title)
    words = set(title_norm.split())
    compact = title_norm.replace(" ", "")

    for word in ctx["excluded"]:
        if token_in_title(word, words, compact) or word in title_norm:
            return f"bevat uitgesloten woord '{word}'"
    if level == "Alles":
        return None

    tokens = ctx["tokens"]
    missing = [t for t in tokens if not token_in_title(t, words, compact)]
    if len(missing) > len(tokens) // 5:
        return "mist: " + ", ".join(missing[:3])

    q_families = ctx["families"]
    if q_families:
        extra = detect_families(words, compact) - q_families
        if "shampoo" in q_families:
            extra.discard("gel")
        if "gel" in q_families:
            extra.discard("shampoo")
        if extra:
            return "ander producttype: " + ", ".join(sorted(extra))

    if level == "Gemiddeld":
        return None

    bundle_words = [
        w for w in BUNDLE_RE.findall(title.lower()) if w not in BUNDLE_FALSE_POSITIVES
    ]
    if bundle_words and not any(normalize_text(w) in tokens for w in bundle_words):
        return f"set of bundel ('{bundle_words[0]}')"

    query_ml = ctx["query_ml"]
    volume = row.get("Volume (ml)")
    if query_ml and volume:
        ratio = volume / query_ml
        nearest = round(ratio)
        same = abs(ratio - 1) <= 0.02
        has_count = "×" in str(row.get("Volume-berekening") or "")
        multipack = has_count and 2 <= nearest <= 12 and abs(ratio - nearest) <= 0.02
        if not (same or multipack):
            return f"andere inhoud ({volume:g} ml i.p.v. {query_ml:g} ml)"

    return None


def filter_rows(rows, query, level, excluded_text=""):
    """Splitst resultaten in (behouden, [(afgewezen_rij, reden), ...])."""
    ctx = build_match_context(query, excluded_text)
    kept = []
    rejected = []
    for row in rows:
        reason = judge_row(row, ctx, level)
        if reason:
            rejected.append((row, reason))
        else:
            kept.append(row)

    # Een live bol-prijs (API) vervangt de Google-prijs van bol.com voor hetzelfde zoekresultaat.
    if any(r["Bron"] == "bol_api" for r in kept):
        remaining = []
        for row in kept:
            is_google_bol = (
                detect_store(str(row["Winkel"]).lower()) == "bol" and row["Bron"] != "bol_api"
            )
            if is_google_bol:
                rejected.append((row, "vervangen door live bol-prijs"))
            else:
                remaining.append(row)
        kept = remaining

    return kept, rejected


# ---------------------------------------------------------------------------
# Bol.com Marketing Catalog API (optioneel, vraagt Partnerprogramma-inloggegevens)
# ---------------------------------------------------------------------------

def get_bol_token(client_id, client_secret):
    """Haalt een OAuth-token op (en hergebruikt het tot kort voor verlopen)."""
    cached = st.session_state.get("bol_token")
    if cached and cached.get("client_id") == client_id and cached.get("expires", 0) > time.time() + 20:
        return cached["token"], None

    try:
        res = requests.post(
            BOL_TOKEN_URL,
            params={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return None, f"bol-login mislukt: {exc}"

    if res.status_code == 401:
        return None, "bol-login geweigerd: controleer je Client ID en Client Secret."
    if res.status_code != 200:
        return None, f"bol-login gaf HTTP-status {res.status_code}."

    try:
        data = res.json()
    except ValueError:
        data = {}
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        return None, "bol-login gaf geen token terug."

    try:
        lifetime = int(data.get("expires_in", 299))
    except (TypeError, ValueError):
        lifetime = 299
    st.session_state["bol_token"] = {
        "client_id": client_id,
        "token": token,
        "expires": time.time() + lifetime,
    }
    return token, None


def fetch_bol_rows(query, client_id, client_secret):
    """Zoekt op bol.com via de officiele API. Geeft (rijen, notities)."""
    token, error = get_bol_token(client_id, client_secret)
    if token is None:
        return [], [error]

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "nl",
    }
    params = {
        "search-term": query,
        "country-code": "NL",
        "page-size": BOL_PAGE_SIZE,
        "include-offer": "true",
    }
    try:
        res = requests.get(
            f"{BOL_API_BASE}/products/search",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return [], [f"bol-zoekopdracht mislukt: {exc}"]

    if res.status_code == 401:
        st.session_state.pop("bol_token", None)
        return [], ["bol-API weigerde het token (401). Probeer het opnieuw."]
    if res.status_code == 429:
        return [], ["bol-API: te veel verzoeken (429). Wacht even en probeer opnieuw."]
    if res.status_code != 200:
        return [], [f"bol-zoekopdracht gaf HTTP-status {res.status_code}."]

    try:
        data = res.json()
    except ValueError:
        return [], ["bol-API gaf een onleesbaar antwoord."]

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return [], ["bol-API gaf geen resultaten terug."]

    rows = []
    for item in results:
        if not isinstance(item, dict):
            continue
        offer = item.get("offer")
        if not isinstance(offer, dict):
            continue
        price = parse_price(offer.get("price"))
        if price is None:
            continue

        title = str(item.get("title") or "Onbekend product")
        description = str(item.get("description") or "")
        raw_url = item.get("url")
        link = clean_store_link(raw_url, "bol.com", title)

        total_ml, volume_note = extract_volume_info(title)
        if total_ml is None:
            total_ml, volume_note = extract_volume_info(
                f"{title} {description}", sum_segments=False
            )

        shipping_cost, shipping_text = resolve_shipping("bol.com", price, None)
        delivery = str(offer.get("deliveryDescription") or "").strip()
        if delivery:
            shipping_text = f"{shipping_text} \u00b7 {delivery}"

        rows.append(
            build_row(
                "Bol.com", title, price, link, shipping_cost, shipping_text,
                total_ml, "bol_api", volume_note, is_direct_link(raw_url),
            )
        )

    return rows, []


# ---------------------------------------------------------------------------
# SerpApi
# ---------------------------------------------------------------------------

def fetch_serp(params):
    """Roept SerpApi aan. Geeft (data, foutmelding). data is None bij een harde fout."""
    res = None
    last_error = ""
    for attempt in range(SERP_ATTEMPTS):
        try:
            res = requests.get(SERPAPI_URL, params=params, timeout=SERP_TIMEOUT)
        except requests.Timeout:
            res = None
            last_error = (
                f"SerpApi reageerde niet binnen {SERP_TIMEOUT} seconden. "
                "Probeer het over een minuutje opnieuw."
            )
            continue
        except requests.RequestException as exc:
            res = None
            last_error = f"Netwerkfout: {exc}"
            continue
        if res.status_code in (500, 502, 503, 504) and attempt + 1 < SERP_ATTEMPTS:
            last_error = f"SerpApi gaf HTTP-status {res.status_code}."
            res = None
            continue
        break

    if res is None:
        return None, last_error or "SerpApi is niet bereikbaar."

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


def run_search(
    query, api_key, deep_scan, deep_limit, check_promos=True, page_limit=DEFAULT_PAGE_CHECKS,
    bol_id="", bol_secret="",
):
    """Voert de zoekopdracht uit. Geeft (rijen, waarschuwingen, infoberichten)."""
    rows = []
    notes = []
    infos = []
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
        notes.append(error or "Onbekende fout bij het ophalen van resultaten.")
        data = {}
        error = None

    shopping_results = data.get("shopping_results") or []
    if not shopping_results and not notes:
        notes.append(error or "Geen shopping-resultaten gevonden voor deze zoekterm.")

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

        direct_url = pick_direct_link(item)
        link = clean_store_link(direct_url, merchant, title)

        snippet = str(item.get("snippet") or "")
        ext_list = item.get("extensions")
        extensions = " ".join(str(e) for e in ext_list) if isinstance(ext_list, list) else ""

        total_ml, volume_note = extract_volume_info(title)
        if total_ml is None:
            total_ml, volume_note = extract_volume_info(
                f"{title} {snippet} {extensions}", sum_segments=False
            )

        shipping_cost, shipping_text = resolve_shipping(
            merchant_lower, numeric_price, item.get("delivery")
        )

        rows.append(
            build_row(
                merchant, title, numeric_price, link,
                shipping_cost, shipping_text, total_ml, "google_shopping",
                volume_note, direct_url is not None,
            )
        )

        if deep_scan and item.get("product_id") and len(deep_candidates) < deep_limit:
            deep_candidates.append((str(item["product_id"]), title, total_ml, volume_note))

    for product_id, parent_title, parent_ml, parent_note in deep_candidates:
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

            raw_offer_link = offer.get("link")
            offer_direct = raw_offer_link if is_direct_link(raw_offer_link) else None
            offer_link = clean_store_link(offer_direct, offer_merchant, parent_title)

            rows.append(
                build_row(
                    offer_merchant, parent_title, offer_price, offer_link,
                    shipping_cost, shipping_text, parent_ml, "google_product",
                    parent_note, offer_direct is not None,
                )
            )

    if bol_id and bol_secret:
        bol_rows, bol_notes = fetch_bol_rows(query, bol_id, bol_secret)
        notes.extend(bol_notes)
        if bol_rows:
            rows.extend(bol_rows)
            infos.append(
                f"Bol.com: {len(bol_rows)} live aanbiedingen opgehaald via de bol API. "
                "Verzendkosten bij bol zijn een schatting (gratis aangenomen)."
            )

    if check_promos and rows:
        infos.extend(apply_page_promos(rows, page_limit))

    for index, row in enumerate(rows):
        row["ID"] = index

    return rows, notes, infos


# ---------------------------------------------------------------------------
# UI-hulpfuncties
# ---------------------------------------------------------------------------

def get_secret(name):
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return ""


def get_serp_key_from_secrets():
    return get_secret("SERPAPI_KEY")


def link_column():
    try:
        return st.column_config.LinkColumn("Link", display_text="Open winkel")
    except TypeError:
        return st.column_config.LinkColumn("Link")


def render_promo_picker(row, token):
    """Keuzelijst waarmee je per kaart een actie kunt instellen."""
    detected = str(row["Actie (gedetecteerd)"] or "")
    options = list(PROMO_RULES.keys())
    if detected and detected not in options:
        options.append(detected)

    current = get_promo_choice(token, int(row["ID"]), detected or "Geen")
    if current not in options:
        current = "Geen"

    st.selectbox(
        "Actie",
        options,
        index=options.index(current),
        key=promo_widget_key(token, int(row["ID"])),
        on_change=remember_promo_choice,
        args=(token, int(row["ID"])),
        help="Kies een actie om de prijs bij aankoop van meerdere stuks te zien.",
    )


def render_cards(df, token):
    """Toont resultaten als kaarten, ideaal voor een smal telefoonscherm."""
    total = len(df)
    show_all = bool(st.session_state.get("show_all_cards", False))
    limit = total if show_all else min(INITIAL_CARDS, total)

    best_index = None
    if df["Sorteerwaarde"].notna().any():
        best_index = df["Sorteerwaarde"].idxmin()

    for index in range(limit):
        row = df.iloc[index]
        is_best = best_index is not None and index == best_index

        with st.container(border=True):
            prefix = "\U0001f3c6 " if is_best else ""
            st.markdown(f"{prefix}**{md_escape(row['Winkel'])}**")
            st.caption(md_escape(row["Product"]))

            price_line = f"**{fmt_eur(row['Totaal'])}** totaal"
            if pd.notna(row["Per liter"]):
                price_line += (
                    f" \u00b7 {row['Volume (ml)']:.0f} ml"
                    f" \u00b7 **{fmt_eur(row['Per liter'])} / L**"
                )
            else:
                price_line += " \u00b7 volume onbekend"
            st.markdown(price_line)

            volume_note = str(row["Volume-berekening"] or "")
            if "=" in volume_note:
                st.caption(f"Volume: {md_escape(volume_note)}")

            st.caption(
                f"Prijs {fmt_eur(row['Prijs'])} \u00b7 verzending: {md_escape(row['Verzending'])}"
            )

            if str(row["Actie"]) != "Geen" and pd.notna(row["Totaal (actie)"]):
                promo_line = (
                    f"**Met actie {md_escape(row['Actie'])}** "
                    f"(koop {int(row['Koop-aantal'])}): {fmt_eur(row['Totaal (actie)'])}"
                )
                if pd.notna(row["Per liter (actie)"]):
                    promo_line += (
                        f" \u00b7 {row['Volume (actie)']:.0f} ml"
                        f" \u00b7 **{fmt_eur(row['Per liter (actie)'])} / L**"
                    )
                st.markdown(promo_line)

            if str(row["Actie-tekst"]):
                st.caption(
                    f"Gespot op de winkelpagina: \u00ab{md_escape(row['Actie-tekst'])}\u00bb "
                    "Controleer de voorwaarden."
                )

            render_promo_picker(row, token)
            st.markdown(f"[Open winkel \u2197]({safe_md_url(row['Link'])})")

    if total > limit:
        if st.button(f"Toon alle {total} resultaten"):
            st.session_state["show_all_cards"] = True
            st.rerun()


def render_table(df):
    """Toont resultaten als tabel (handig op een groter scherm)."""
    st.dataframe(
        df,
        hide_index=True,
        column_order=[
            "Winkel", "Product", "Prijs", "Totaal", "Volume (ml)", "Per liter",
            "Actie", "Per liter (actie)", "Volume-berekening", "Link",
        ],
        column_config={
            "Product": st.column_config.TextColumn("Product", width="large"),
            "Prijs": st.column_config.NumberColumn("Prijs", format="\u20ac %.2f"),
            "Totaal": st.column_config.NumberColumn("Totaal incl. verz.", format="\u20ac %.2f"),
            "Volume (ml)": st.column_config.NumberColumn("Volume (ml)", format="%.0f"),
            "Per liter": st.column_config.NumberColumn("Per liter", format="\u20ac %.2f"),
            "Per liter (actie)": st.column_config.NumberColumn(
                "Per liter met actie", format="\u20ac %.2f"
            ),
            "Link": link_column(),
        },
    )
    st.caption("Een actie kies je per product in de kaartweergave.")


def comparison_link(site, query):
    """Bouw een zoeklink naar een vergelijkingssite (direct of via Google site:)."""
    template = site.get("search_url")
    if template:
        return template.format(q=quote_plus(query))
    return "https://www.google.com/search?q=" + quote_plus(f"site:{site['domain']} {query}")


def render_comparison_links(query):
    """Toont snelle links om dezelfde zoekterm op Nederlandse vergelijkingssites te openen."""
    if not query:
        return
    parts = []
    for site in COMPARISON_SITES:
        url = safe_md_url(comparison_link(site, query))
        parts.append(f"[{md_escape(site['name'])} \u2197]({url})")
    st.markdown("**Zoek ook op:** " + " \u00b7 ".join(parts))


def render_rejected(rejected):
    """Toont welke resultaten zijn weggefilterd en waarom."""
    if not rejected:
        return
    with st.expander(f"Afgewezen resultaten ({len(rejected)})"):
        for row, reason in rejected[:60]:
            st.caption(
                f"{md_escape(row['Winkel'])} \u00b7 {md_escape(row['Product'])} "
                f"\u00b7 {fmt_eur(row['Prijs'])} \u2014 {md_escape(reason)}"
            )
        if len(rejected) > 60:
            st.caption(f"... en nog {len(rejected) - 60} andere.")


def render_results(snipe):
    for note in snipe["notes"]:
        st.warning(note)
    for info in snipe.get("infos", []):
        st.caption(info)

    rows = snipe["rows"]
    if not rows:
        st.warning("Geen bruikbare resultaten gevonden.")
        render_comparison_links(snipe["query"])
        return

    token = snipe["token"]

    st.subheader(f"Resultaten voor: {snipe['query']}")

    level = st.radio("Match-niveau:", MATCH_LEVELS, horizontal=True, help=MATCH_HELP)
    excluded_text = st.text_input(
        "Woorden uitsluiten (optioneel):",
        placeholder="bijv. masque, conditioner",
        key="exclude_words",
    )

    kept, rejected = filter_rows(rows, snipe["query"], level, excluded_text)
    if not kept and level == "Strikt":
        relaxed_kept, relaxed_rejected = filter_rows(
            rows, snipe["query"], "Gemiddeld", excluded_text
        )
        if relaxed_kept:
            st.info("Geen exacte matches gevonden; hieronder staat het niveau 'Gemiddeld'.")
            kept, rejected = relaxed_kept, relaxed_rejected

    st.caption(f"{len(kept)} van {len(rows)} resultaten passen bij deze zoekterm.")
    if not kept:
        st.warning("Geen resultaten die passen. Probeer 'Gemiddeld' of 'Alles'.")
        render_rejected(rejected)
        render_comparison_links(snipe["query"])
        return

    sort_choice = st.radio(
        "Sorteer op:",
        ["Prijs per liter", "Totaalprijs"],
        horizontal=True,
    )
    view_choice = st.radio(
        "Weergave:",
        ["Kaarten", "Tabel"],
        horizontal=True,
    )
    sort_column = "Sorteerwaarde" if sort_choice == "Prijs per liter" else "Totaal"

    df = build_dataframe(kept, token)
    df = df.sort_values(sort_column, ascending=True, na_position="last").reset_index(drop=True)

    with_volume = df[df["Sorteerwaarde"].notna()]
    if not with_volume.empty:
        best = with_volume.sort_values("Sorteerwaarde").iloc[0]
        if pd.notna(best["Per liter (actie)"]):
            promo_text = f" met actie {best['Actie']}"
            best_total = best["Totaal (actie)"]
            best_volume = best["Volume (actie)"]
        else:
            promo_text = ""
            best_total = best["Totaal"]
            best_volume = best["Volume (ml)"]
        st.success(
            f"Beste deal per liter: {best['Winkel']} voor {fmt_eur(best['Sorteerwaarde'])} per liter"
            f"{promo_text} ({fmt_eur(best_total)} totaal, {best_volume:.0f} ml)."
        )
        st.link_button("Open beste deal", best["Link"])
    else:
        st.info("Bij geen enkel resultaat kon een volume worden herkend, dus er is geen prijs per liter.")

    if view_choice == "Kaarten":
        render_cards(df, token)
    else:
        render_table(df)

    st.caption(
        "Per liter en per 100 ml zijn berekend over prijs plus (geschatte) verzendkosten. "
        "Het volume wordt uit de titel afgeleid: aantallen zoals '3x' tellen mee en sets met "
        "meerdere producten worden opgeteld. Twijfel je, check dan de volume-berekening op de kaart."
    )
    st.caption(
        "Prijzen 'met actie' gelden alleen bij aankoop van het genoemde aantal. Bij acties over "
        "verschillende producten is meestal het goedkoopste product gratis. Verzendkosten zijn "
        "onveranderd gerekend."
    )
    render_rejected(rejected)
    render_comparison_links(snipe["query"])


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Price Sniper App", page_icon="\U0001f3af", layout="centered")
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

mode = st.radio(
    "Weergave", ["Prijscheck (EAN)", "Zoeken (oud)"], horizontal=True, label_visibility="collapsed"
)
if mode == "Prijscheck (EAN)":
    ean_page.render()
    st.stop()

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
    check_promos = st.checkbox(
        "Winkelpagina's controleren op acties (1+1 gratis e.d.)",
        value=True,
        help="Opent de pagina's van de beste resultaten. Kost geen API-credits, maar duurt iets langer.",
    )
    page_limit = st.slider(
        "Aantal pagina's om te controleren",
        min_value=1,
        max_value=8,
        value=DEFAULT_PAGE_CHECKS,
        disabled=not check_promos,
    )
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
    st.markdown("**Bol.com API (optioneel, voor live bol-prijzen)**")
    bol_id = get_secret("BOL_CLIENT_ID")
    bol_secret = get_secret("BOL_CLIENT_SECRET")
    if bol_id and bol_secret:
        st.caption("Bol-API gekoppeld via Secrets.")
    else:
        bol_id = st.text_input("bol Client ID", placeholder="Client ID")
        bol_secret = st.text_input("bol Client Secret", type="password", placeholder="Client Secret")
        st.caption(
            "Aanmaken kan met een account in het bol Partnerprogramma (Open API-instellingen). "
            "Zet ze bij voorkeur in Secrets als BOL_CLIENT_ID en BOL_CLIENT_SECRET."
        )

if st.button("Snipe Prijzen", type="primary"):
    if not serp_key:
        st.warning("Geen SerpApi Key gevonden. Stel die in via Secrets of vul hem hierboven in.")
    elif not search_query.strip():
        st.warning("Vul een product-URL of een zoekterm in.")
    else:
        with st.spinner("Markt aan het doorzoeken..."):
            result_rows, result_notes, result_infos = run_search(
                search_query.strip(), serp_key, deep_scan, deep_limit, check_promos, page_limit,
                bol_id.strip(), bol_secret.strip(),
            )
        st.session_state["search_counter"] = st.session_state.get("search_counter", 0) + 1
        st.session_state["show_all_cards"] = False
        st.session_state["snipe"] = {
            "token": st.session_state["search_counter"],
            "query": search_query.strip(),
            "rows": result_rows,
            "notes": result_notes,
            "infos": result_infos,
        }

saved_snipe = st.session_state.get("snipe")
if saved_snipe:
    render_results(saved_snipe)
