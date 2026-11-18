"""EAN/GTIN helpers: validate and normalise barcodes, read them from a product page, and
recognise bol.com / Amazon product URLs.

Pure functions only (no network, no Streamlit), so they are easy to test. The page shapes were
checked against live pages on 2026-11-18:

- bol.com: JSON-LD ProductGroup -> hasVariant -> Product with "gtin13", plus spec rows labelled
  "EAN" and "Child GTIN" (the single item inside a multipack).
- amazon.nl: no JSON-LD; a product overview table row "Global Trade Identification Number"
  holding a GTIN-14 with a leading zero.

A wrong match is worse than no match, so ambiguous pages return no EAN instead of a guess.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

# --------------------------------------------------------------------------- GTIN

_GTIN_LENGTHS = (8, 12, 13, 14)
_INVISIBLE_RE = re.compile(r"[\u200e\u200f\u00a0]")


def _check_digit_ok(digits):
    """GS1 mod-10 check digit over a digit string (last digit is the check digit)."""
    total = sum(
        int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(digits[:-1]))
    )
    return (10 - total % 10) % 10 == int(digits[-1])


def normalize_gtin(raw):
    """Return a validated barcode as a digit string, or None.

    Accepts EAN-8, UPC-A (12), EAN-13 and GTIN-14. A 12-digit UPC is padded to 13 digits and a
    14-digit GTIN with a leading zero is shortened to 13, so the same product compares equal.
    Spaces, hyphens and invisible direction marks are ignored. Wrong length or a wrong check
    digit, or all zeros, gives None.
    """
    if raw is None:
        return None
    text = re.sub(r"[\s\-]", "", _INVISIBLE_RE.sub("", str(raw)))
    if not re.fullmatch(r"[0-9]+", text) or len(text) not in _GTIN_LENGTHS:
        return None
    if not _check_digit_ok(text) or not text.strip("0"):  # all zeros has a valid check digit
        return None
    if len(text) == 14 and text.startswith("0"):
        return text[1:]
    if len(text) == 12:
        return "0" + text
    return text


# --------------------------------------------------------------------------- URLs


@dataclass(frozen=True)
class ShopRef:
    shop: str  # "bol" or "amazon"
    product_id: str  # bol product id, or the Amazon ASIN


_BOL_PATH_RE = re.compile(r"/p/[^/]+/(\d{10,})(?:/|$)")
_ASIN_PATH_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)", re.IGNORECASE)
_AMAZON_HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)*amazon\.[a-z.]{2,6}")


def parse_product_url(url):
    """Recognise a bol.com or Amazon product URL. Returns ShopRef, or None for anything else
    (other shops, bol account/order pages, search pages, garbage)."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlparse(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None

    if host == "bol.com" or host.endswith(".bol.com"):
        match = _BOL_PATH_RE.search(parts.path)
        return ShopRef("bol", match.group(1)) if match else None
    if _AMAZON_HOST_RE.fullmatch(host):
        match = _ASIN_PATH_RE.search(parts.path)
        return ShopRef("amazon", match.group(1).upper()) if match else None
    return None


# --------------------------------------------------------------------------- pages


@dataclass(frozen=True)
class ProductIds:
    ean: str | None  # normalised EAN-13 (or EAN-8) of the product on the page
    child_gtins: tuple[str, ...] = ()  # barcodes of items inside a multipack
    source: str | None = None  # "json-ld", "microdata" or "spec-table"


_ITEMPROP_RE = re.compile(r"(?:gtin\d*|ean)", re.IGNORECASE)
_GTIN_KEY_RE = re.compile(r"(?:gtin\d*|ean)", re.IGNORECASE)
# JSON-LD keys that point at OTHER products; their barcodes must not be taken as this page's.
_RELATION_KEYS = {
    "isSimilarTo",
    "isRelatedTo",
    "isAccessoryOrSparePartFor",
    "isConsumableFor",
    "itemListElement",
}
_EAN_LABEL_RE = re.compile(
    r"(?:ean(?:[- ]?code)?|gtin(?:[- ]?(?:8|12|13|14))?|global trade identification number)",
    re.IGNORECASE,
)
_CHILD_LABEL_RE = re.compile(r"child gtin", re.IGNORECASE)


class _PageScan(HTMLParser):
    """Collects visible text nodes, JSON-LD blocks and microdata barcode values, in page order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self.ld_blocks = []
        self.microdata = []
        self._script = None  # None, "ld" or "other" while inside <script>/<style>
        self._buf = []
        self._want_itemprop_text = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style"):
            is_ld = "ld+json" in (attributes.get("type") or "").lower()
            self._script = "ld" if tag == "script" and is_ld else "other"
            self._buf = []
            return
        if _ITEMPROP_RE.fullmatch((attributes.get("itemprop") or "").strip()):
            if attributes.get("content"):
                self.microdata.append(attributes["content"])
            else:
                self._want_itemprop_text = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._script:
            if self._script == "ld":
                self.ld_blocks.append("".join(self._buf))
            self._script = None
            self._buf = []

    def handle_data(self, data):
        if self._script:
            self._buf.append(data)
            return
        text = data.strip()
        if not text:
            return
        self.texts.append(text)
        if self._want_itemprop_text:
            self.microdata.append(text)
            self._want_itemprop_text = False


def _unique(values):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _jsonld_gtins(blocks):
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in _RELATION_KEYS:
                    continue
                if _GTIN_KEY_RE.fullmatch(key) and isinstance(value, (str, int)):
                    found.append(normalize_gtin(value))
                elif isinstance(value, (dict, list)):
                    walk(value)

    for block in blocks:
        try:
            walk(json.loads(block))
        except ValueError:
            continue
    return _unique(found)


def _clean_label(text):
    return _INVISIBLE_RE.sub("", text).strip().rstrip(":").strip()


def _values_after_labels(texts, label_re):
    """Barcodes that follow a matching label in the page text (label, optional ':', value)."""
    found = []
    for index, text in enumerate(texts):
        if not label_re.fullmatch(_clean_label(text)):
            continue
        for following in texts[index + 1 : index + 3]:
            value = normalize_gtin(_clean_label(following))
            if value:
                found.append(value)
                break
    return _unique(found)


def extract_ids(html):
    """Find the product's EAN (and any child GTINs) in the HTML of a product page.

    Sources are tried in order: JSON-LD, microdata, then spec-table rows labelled EAN/GTIN. A
    source is used only if it names exactly one valid barcode; a source with several (for
    example a variant list) is skipped, and if no source is unambiguous the EAN is None.
    """
    if not html:
        return ProductIds(None)
    scan = _PageScan()
    try:
        scan.feed(str(html))
        scan.close()
    except Exception:  # scraped HTML can be malformed: keep what was collected so far
        pass

    candidates = (
        ("json-ld", _jsonld_gtins(scan.ld_blocks)),
        ("microdata", _unique(normalize_gtin(v) for v in scan.microdata)),
        ("spec-table", _values_after_labels(scan.texts, _EAN_LABEL_RE)),
    )
    ean, source = None, None
    for name, values in candidates:
        if len(values) == 1:
            ean, source = values[0], name
            break

    children = tuple(
        value for value in _values_after_labels(scan.texts, _CHILD_LABEL_RE) if value != ean
    )
    return ProductIds(ean, children, source)
