"""Probe a shop's product page: can the app read it, and what identifies the product on it?

Slice A of the staggered lookup (EAN/GTIN, then brand + MPN, then the product name): before any
of that is built per shop, this module reports for one product link whether the shop answers an
honest request at all (a bot check or a 403 is a block, reported and never worked around) and
which identifiers are in the page: EAN/GTIN, MPN (manufacturer's article code), brand, name and
the shop's own SKU.

Pure apart from the injected `fetch(url) -> (status, text)`; no Streamlit, no direct network.
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import ean

READABLE = "readable"  # answered, with product data in it
NO_PRODUCT = "no_product"  # answered, but no product data found (JavaScript page, or another kind of page)
BLOCKED = "blocked"  # the shop refused or showed a bot check
NOT_FOUND = "not_found"  # 404/410: the link is wrong or the product is gone
NO_ANSWER = "no_answer"  # no HTTP answer at all (timeout, DNS, connection reset)
ERROR = "error"  # any other HTTP status

MAX_URLS = 8
PAUSE_SECONDS = 1.0  # between two requests
MAX_SPEC_PAIRS = 30
MAX_VALUES = 3  # of MPN and SKU values shown

_BLOCK_STATUSES = {202, 401, 403, 429, 451, 503}
_BLOCK_WORDS = (
    "captcha", "awswaf", "access denied", "just a moment", "verify you are human", "unusual traffic",
    "are you a robot", "bot detection", "px-captcha",
)
_PRODUCT_TYPES = {"product", "productgroup", "productmodel", "individualproduct"}
_RELATION_KEYS = {"isSimilarTo", "isRelatedTo", "isAccessoryOrSparePartFor", "isConsumableFor", "itemListElement"}
_LABEL_RE = re.compile(
    r"(?:ean(?:[- ]?code)?|gtin(?:[- ]?\d+)?|barcode|mpn|sku|artikel(?:nummer|code)|product(?:nummer|code|id)"
    r"|fabrikant(?:code|nummer|sartikelnummer)?|model(?:nummer|naam|code)?|type(?:nummer)?|merk|brand"
    r"|stijl(?:code|nummer)?|kleurcode|style(?: code)?)",
    re.IGNORECASE,
)
_INVISIBLE_RE = re.compile(r"[‎‏ ]")


# --------------------------------------------------------------------------- links


def clean_url(line):
    """(url, None) for a usable product link in `line`, or (None, Dutch reason)."""
    tokens = [t for t in str(line or "").split() if t]
    candidate = next((t for t in tokens if t.lower().startswith(("http://", "https://"))), None)
    if candidate is None:
        candidate = next((t for t in tokens if "." in t and "/" in t), None)
        if candidate is None:
            return None, "Dit lijkt geen link."
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or "." not in host:
        return None, "Dit lijkt geen link naar een winkel."
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        return None, "Links met inlog of poortnummer worden niet gelezen."
    try:
        ipaddress.ip_address(host)
        return None, "Links naar een IP-adres worden niet gelezen."
    except ValueError:
        pass
    if host.endswith((".local", ".internal", ".localhost")):
        return None, "Links naar interne adressen worden niet gelezen."
    return candidate, None


# --------------------------------------------------------------------------- HTML scan


class _Scan(HTMLParser):
    """Visible text nodes, JSON-LD blocks, meta tags and the page title, in page order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self.ld_blocks = []
        self.meta = {}
        self.title = ""
        self._script = None
        self._buf = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style"):
            is_ld = tag == "script" and "ld+json" in (attributes.get("type") or "").lower()
            self._script = "ld" if is_ld else "other"
            self._buf = []
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            content = (attributes.get("content") or "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._script:
            if self._script == "ld":
                self.ld_blocks.append("".join(self._buf))
            self._script = None
            self._buf = []
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._script:
            self._buf.append(data)
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        else:
            self.texts.append(text)


def _scan(html):
    scan = _Scan()
    try:
        scan.feed(str(html or ""))
        scan.close()
    except Exception:  # scraped HTML can be malformed: keep what was collected so far
        pass
    return scan


def _clip(value, limit=120):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _text(value):
    """A short string from a JSON-LD value (string, number, {'name': ...} or a list of those)."""
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = next((v for v in (_text(v) for v in value) if v), None)
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = _clip(value)
    return text or None


def _types(node):
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return {str(k).rsplit("/", 1)[-1].lower() for k in kinds if k}


def _product_nodes(blocks):
    """Every JSON-LD node of a product type, in document order (variants of a group included)."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if _types(node) & _PRODUCT_TYPES:
                found.append(node)
            for key, value in node.items():
                if key not in _RELATION_KEYS and isinstance(value, (dict, list)):
                    walk(value)

    for block in blocks:
        try:
            walk(json.loads(block))
        except ValueError:
            continue
    return found


def _unique(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _gtins(node):
    return [
        ean.normalize_gtin(value)
        for key, value in node.items()
        if re.fullmatch(r"(?:gtin\d*|ean)", key, re.IGNORECASE) and isinstance(value, (str, int))
    ]


def _trimmed(node):
    """A small copy of a product node for the download: identifiers and a price, no reviews."""
    keep = ("@type", "name", "brand", "mpn", "sku", "productID", "model", "category", "color", "url")
    out = {key: node[key] for key in keep if key in node}
    out.update({key: value for key, value in node.items() if re.fullmatch(r"(?:gtin\d*|ean)", key, re.IGNORECASE)})
    offers = node.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict):
        out["offers"] = {k: offer[k] for k in ("price", "lowPrice", "priceCurrency", "availability") if k in offer}
    if isinstance(node.get("hasVariant"), list):
        out["hasVariant"] = f"{len(node['hasVariant'])} varianten"
    return out


def spec_pairs(texts):
    """(label, value) pairs from the page text where a label such as 'EAN' or 'Merk' is followed by its value."""
    pairs = []
    for index, text in enumerate(texts):
        label = _INVISIBLE_RE.sub("", text).strip().rstrip(":").strip()
        if len(label) > 40 or not _LABEL_RE.fullmatch(label):
            continue
        for following in texts[index + 1 : index + 3]:
            value = _INVISIBLE_RE.sub("", following).strip().lstrip(":").strip()
            if value and len(value) <= 80 and not _LABEL_RE.fullmatch(value.rstrip(":")):
                pairs.append((label, value))
                break
        if len(pairs) >= MAX_SPEC_PAIRS:
            break
    return pairs


# --------------------------------------------------------------------------- the report


@dataclass(frozen=True)
class PageReport:
    url: str
    host: str
    verdict: str
    message: str  # Dutch, for the owner
    status: int | None = None
    ean: str | None = None
    ean_source: str | None = None  # "json-ld", "microdata" or "spec-table"
    mpns: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    brand: str | None = None
    name: str | None = None
    name_source: str | None = None  # "json-ld", "og:title" or "title"
    variants: int = 0  # product nodes in the JSON-LD; more than one is a group with variants
    raw: dict | None = None  # trimmed page data for the download (fixtures); never the full HTML

    @property
    def rungs(self):
        """Which steps of the staggered lookup this page can feed, best first."""
        steps = []
        if self.ean:
            steps.append("EAN")
        if self.brand and self.mpns:
            steps.append("merk + MPN")
        if self.name:
            steps.append("productnaam")
        return tuple(steps)


def _looks_blocked(status, text):
    if status in _BLOCK_STATUSES:
        return True
    if status == 200 and len(text) < 8000:
        lowered = text.lower()
        return any(word in lowered for word in _BLOCK_WORDS)
    return False


def read_page(url, status, text):
    """The report for one fetched page. `status` is None when there was no HTTP answer (`text` is then the error)."""
    host = (urlparse(url).hostname or "").lower()
    if status is None:
        return PageReport(url, host, NO_ANSWER, f"Geen antwoord van de winkel: {_clip(text, 160)}")
    text = text or ""
    if _looks_blocked(status, text):
        return PageReport(
            url, host, BLOCKED,
            f"De winkel liet de pagina niet lezen (HTTP {status}, waarschijnlijk een bot-controle). "
            "Dit wordt niet omzeild: plak de EAN of de productnaam zelf.",
            status,
        )
    if status in (404, 410):
        return PageReport(url, host, NOT_FOUND, f"Pagina niet gevonden (HTTP {status}): controleer de link.", status)
    if not 200 <= status < 300:
        return PageReport(url, host, ERROR, f"De winkel antwoordde met HTTP {status}.", status)

    scan = _scan(text)
    nodes = _product_nodes(scan.ld_blocks)
    ids = ean.extract_ids(text)
    name, name_source = next(((n, "json-ld") for n in (_text(node.get("name")) for node in nodes) if n), (None, None))
    if name is None and scan.meta.get("og:title"):
        name, name_source = _clip(scan.meta["og:title"]), "og:title"
    if name is None and scan.title:
        name, name_source = _clip(scan.title), "title"
    brand = next((b for b in (_text(node.get("brand")) for node in nodes) if b), None)
    brand = brand or _text(scan.meta.get("product:brand") or scan.meta.get("og:brand"))
    mpns = tuple(_unique(_text(node.get("mpn")) for node in nodes))[:MAX_VALUES]
    skus = tuple(_unique(_text(node.get("sku") or node.get("productID")) for node in nodes))[:MAX_VALUES]
    pairs = spec_pairs(scan.texts)
    raw = {
        "url": url,
        "status": status,
        "bytes": len(text),
        "title": _clip(scan.title, 200) or None,
        "meta": {k: _clip(v, 200) for k, v in scan.meta.items() if k.startswith(("og:", "product:", "twitter:title"))},
        "json_ld_products": [_trimmed(node) for node in nodes[:5]],
        "spec_pairs": [list(pair) for pair in pairs],
    }
    report = PageReport(
        url, host, READABLE, "", status, ids.ean, ids.source, mpns, skus, brand, name, name_source, len(nodes), raw
    )
    if not (nodes or ids.ean or pairs):
        return PageReport(
            url, host, NO_PRODUCT,
            "Pagina gelezen, maar er staan geen productgegevens in (de winkel laadt ze misschien met JavaScript).",
            status, name=name, name_source=name_source, raw=raw,
        )
    found = [label for label, value in (("EAN", report.ean), ("MPN", report.mpns), ("merk", report.brand), ("naam", report.name)) if value]
    missing = [label for label in ("EAN", "MPN", "merk", "naam") if label not in found]
    message = "Pagina gelezen. Gevonden: " + (", ".join(found) or "niets bruikbaars") + "."
    if missing:
        message += " Ontbreekt: " + ", ".join(missing) + "."
    return PageReport(**{**report.__dict__, "message": message})


def probe(urls, fetch, sleep=time.sleep, pause=PAUSE_SECONDS):
    """One request per link (at most MAX_URLS, duplicates dropped), with a pause in between."""
    reports = []
    seen = []
    for url in urls:
        if url in seen:
            continue
        seen.append(url)
        if len(seen) > MAX_URLS:
            break
        if len(seen) > 1:
            sleep(pause)
        status, text = fetch(url)
        reports.append(read_page(url, status, text))
    return reports


def download_data(reports):
    """What the owner can download: per link the verdict and the trimmed page data. No cookies, no full HTML."""
    return [
        {
            "link": report.url,
            "oordeel": report.verdict,
            "melding": report.message,
            "http_status": report.status,
            "gevonden": {
                "ean": report.ean,
                "ean_bron": report.ean_source,
                "mpn": list(report.mpns),
                "sku": list(report.skus),
                "merk": report.brand,
                "naam": report.name,
                "naam_bron": report.name_source,
                "varianten": report.variants,
            },
            "pagina": report.raw,
        }
        for report in reports
    ]
