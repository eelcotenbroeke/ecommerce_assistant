"""Is the product that was found the size and pack the person meant? Pure: no network, no Streamlit.

A "deal" on 200 ml is no deal for someone who wants 400 ml, and a single item is not the 7-pack.
Google Shopping matches on words, so a size in the link ("...-shampoo-400ml") or the name that was
typed can be lost on the way. This module reads sizes ("400 ml", "2,4 l", "395 gr") and pack counts
("3 x", "x3", "10x395 gr", "6 stuks", "duopack") from two texts and says when they disagree.

It only speaks when both texts state something and the statements differ. A title without a size is
not a mismatch (it just cannot be checked), and so is an input without one. Counts of 100 or more
("484st" on a LEGO box) are pieces, not a pack, and are ignored.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UNITS = {
    "ml": ("ml", 1), "cl": ("ml", 10), "l": ("ml", 1000), "ltr": ("ml", 1000), "liter": ("ml", 1000),
    "g": ("g", 1), "gr": ("g", 1), "gram": ("g", 1), "kg": ("g", 1000), "kilo": ("g", 1000),
}
_SIZE_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(ml|cl|ltr|liter|l|kilo|kg|gram|gr|g)\b", re.IGNORECASE)
_BEFORE_RE = re.compile(r"(?<![\w.,])(\d{1,2})\s*[x×]\s*(?=[\d(a-z])", re.IGNORECASE)
_AFTER_RE = re.compile(r"(?:^|[\s(])[x×]\s*(\d{1,2})\b", re.IGNORECASE)
_PACK_RE = re.compile(r"(?<![\w.,])(\d{1,2})\s*[- ]?(?:pack|stuks|stuk|st|pak|delig|pcs|x)(?![a-z])", re.IGNORECASE)
_WORD_COUNTS = {"duo": 2, "duopack": 2, "trio": 3, "triopack": 3}


@dataclass(frozen=True)
class Spec:
    sizes: tuple = ()  # ((unit, amount), ...) in ml or g, in the order they appear
    count: int | None = None  # pack count, when the text states one


def _plain(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def read(text):
    """The sizes and the pack count a text states."""
    text = _plain(text)
    sizes = []
    for match in _SIZE_RE.finditer(text):
        unit, factor = _UNITS[match.group(2).lower()]
        amount = round(float(match.group(1).replace(",", ".")) * factor, 1)
        if (unit, amount) not in sizes:
            sizes.append((unit, amount))
    count = None
    for pattern in (_BEFORE_RE, _AFTER_RE, _PACK_RE):
        match = pattern.search(text)
        if match and int(match.group(1)) >= 1:
            count = int(match.group(1))
            break
    if count is None:
        for word, number in _WORD_COUNTS.items():
            if re.search(rf"\b{word}\b", text, re.IGNORECASE):
                count = number
                break
    return Spec(tuple(sizes), count)


def _say(unit, amount):
    if unit == "ml" and amount >= 1000:
        number, unit = amount / 1000, "l"
    elif unit == "g" and amount >= 1000:
        number, unit = amount / 1000, "kg"
    else:
        number = amount
    text = f"{number:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} {unit}"


def mismatch(wanted, found):
    """A Dutch warning when `wanted` (link words or the typed name) and `found` (the title of the
    product that was found) state a different size or pack; None when they agree or cannot be
    compared."""
    want, have = read(wanted), read(found)
    for unit, amount in want.sizes:
        same_unit = [size for size in have.sizes if size[0] == unit]
        if same_unit and (unit, amount) not in same_unit:
            return (
                f"Let op: je zocht {_say(unit, amount)}, het gevonden product noemt "
                f"{_say(*same_unit[0])}. Controleer of dit het product is dat je bedoelt."
            )
    if want.count is not None and want.count >= 2 and want.count != (have.count or 1):
        stated = f"een verpakking van {have.count}" if have.count else "geen verpakking van meerdere"
        return (
            f"Let op: je zocht een verpakking van {want.count}, het gevonden product noemt {stated}. "
            "Controleer of dit het product is dat je bedoelt."
        )
    return None
