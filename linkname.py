"""The product name that sits in the words of a product link.

Pure functions only (no network, no Streamlit). Most Dutch shops put the product's name in the
address of its page (".../brita-waterfilterkan-style-essential-wit-2-4l/p/130611"), next to an
id. Reading those words needs no request to the shop, so it also works for shops that refuse
this app (bol, Kruidvat, Albert Heijn and others answer HTTP 403). The words are only a search
term for Google: they are not a barcode and not a promise that the page is that product.

The rules are general, not written for one shop: the segment of the path with the most real
words is the slug; ids and barcodes at its ends are dropped; folders such as "nl", "p" or
"producten" have too few words to win. A link with fewer than two real words has no name.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

MIN_WORDS = 2  # a name needs at least this many real words (letters only): "calendula" alone is no product
MAX_CHARS = 120  # same limit as the search term Google is asked for
_EXTENSION_RE = re.compile(r"\.(?:html?|php|aspx?|jsp)$", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W\d_]{2,}")  # letters only, at least two
_TOKEN_SPLIT_RE = re.compile(r"[-_+\s]+")


def looks_like_link(text):
    """True for something that is an address, also of a shop that is not known: no words around it."""
    text = (text or "").strip()
    if not text:
        return False
    if "://" in text:
        return True
    return not re.search(r"\s", text) and re.search(r"[./]", text) is not None


def _is_code(token):
    """An id or a barcode: six or more characters of which at least five are digits."""
    return len(token) >= 6 and sum(ch.isdigit() for ch in token) >= 5


def _is_word(token):
    return _WORD_RE.fullmatch(token) is not None


def _tokens(segment):
    return [token for token in _TOKEN_SPLIT_RE.split(_EXTENSION_RE.sub("", unquote(segment))) if token]


def _trim_ids(tokens):
    """Drop ids and barcodes at both ends, and a short leading number ("362-cs-3200-...")."""
    tokens = list(tokens)
    while tokens and _is_code(tokens[-1]):
        tokens.pop()
    while tokens and _is_code(tokens[0]):
        tokens.pop(0)
    if len(tokens) >= 4 and tokens[0].isdigit() and len(tokens[0]) >= 3:
        tokens.pop(0)
    return tokens


def _clip(text):
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS].rsplit(" ", 1)[0]


def name_from_link(url):
    """The words of the product's slug in `url`, lower-case, or None when there are too few.

    "https://www.bol.com/nl/nl/p/logitech-mx-master-4-draadloze-muis/9300000236202343/?cid=1"
    gives "logitech mx master 4 draadloze muis". The query string is ignored.
    """
    text = str(url or "").strip()
    if not looks_like_link(text) or re.search(r"\s", text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlparse(text)
        host = parts.hostname or ""
    except ValueError:
        return None
    if "." not in host:
        return None

    best, best_score = None, 0
    for segment in parts.path.split("/"):
        tokens = _trim_ids(_tokens(segment))
        score = sum(_is_word(token) for token in tokens)
        # most real words wins; of equal ones the longer, then the later segment (the slug follows its folders)
        if score and (score, len(" ".join(tokens))) >= (best_score, len(" ".join(best or ()))):
            best, best_score = tokens, score
    if best is None or best_score < MIN_WORDS:
        return None
    return _clip(" ".join(best).lower())
