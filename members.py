"""Is this shop a Thuiswinkel Waarborg member? Pure: reads one CSV next to this file, no network.

The list (`thuiswinkel_leden.csv`, 1,169 members: slug and domain) is the owner's own export of the
member search on thuiswinkel.org, supplied 2026-11-19. It is a snapshot: members join and leave,
so it is a trust signal, not a guarantee.

A shop is matched in this order, the first hit counts:

1. The host of the offer's link (a `www.` prefix and subdomains are ignored). Google's own links
   (`/goto?url=...`) say nothing about the shop and are skipped.
2. The shop name when it is itself a domain ("Kruidvat.nl"): the domain must be on the list as
   written, so "easycosmetic.DE" is not the Dutch member easycosmetic.nl.
3. The shop name when it is a plain name ("Trekpleister", "Albert Heijn"): compared with the
   domain's first label and the member's slug, ignoring case, accents and punctuation.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

LIST_PATH = Path(__file__).with_name("thuiswinkel_leden.csv")
LIST_DATE = "2026-11-19"
MIN_NAME_LENGTH = 3  # a shorter name ("ah") is too likely to mean something else

# Names Google uses that the domain or slug does not give away.
ALIASES = {"albertheijn": "ah.nl"}

_LEGAL_TAIL_RE = re.compile(r"(?:-(?:b-v|bv|n-v|nv|vof|nl|com|holding))+$")
_DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})(?:[/?#].*)?")


def squash(text):
    """'Albert Heijn' -> 'albertheijn' (case, accents, spaces and punctuation ignored)."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return "".join(ch for ch in plain.lower() if ch.isalnum())


def _clean_host(host):
    host = (host or "").strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def host_of(url):
    """The host of a web link, or '' (also for relative links and Google's own pages)."""
    text = str(url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    try:
        host = _clean_host(urlparse(text).hostname)
    except ValueError:
        return ""
    return "" if "google." in host else host


def domain_in_name(name):
    """'Kruidvat.nl' -> 'kruidvat.nl'; None when the name is not a domain ('Albert Heijn')."""
    match = _DOMAIN_RE.fullmatch(str(name or "").strip().lower())
    return match.group(1) if match else None


def _parents(host):
    """'shop.bol.com' -> ['shop.bol.com', 'bol.com']: every suffix that still has a dot."""
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


@dataclass(frozen=True)
class MemberList:
    domains: frozenset = frozenset()
    names: dict = field(default_factory=dict)  # squashed name -> member domain

    def __len__(self):
        return len(self.domains)

    def match(self, shop=None, url=None):
        """The member domain this shop is, or None when it is not on the list."""
        host = host_of(url)
        for candidate in _parents(host):
            if candidate in self.domains:
                return candidate
        named = domain_in_name(shop)
        if named:
            for candidate in _parents(named):
                if candidate in self.domains:
                    return candidate
            return None
        key = squash(shop)
        return self.names.get(key) if len(key) >= MIN_NAME_LENGTH else None

    def is_member(self, shop=None, url=None):
        return self.match(shop, url) is not None


def build(rows):
    """A MemberList from (slug, domain) pairs."""
    domains, names = set(), {}
    for slug, domain in rows:
        domain = _clean_host(domain)
        if not domain:
            continue
        domains.add(domain)
        label = domain.split(".")[0]
        for text in (label, _LEGAL_TAIL_RE.sub("", (slug or "").strip().lower())):
            key = squash(text)
            if len(key) >= MIN_NAME_LENGTH:
                names.setdefault(key, domain)
    for key, domain in ALIASES.items():
        if domain in domains:
            names[key] = domain
    return MemberList(frozenset(domains), names)


@lru_cache(maxsize=1)
def load(path=LIST_PATH):
    """The member list from the CSV (cached: one read per process)."""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = [(row.get("webshop_slug"), row.get("domain")) for row in csv.DictReader(handle)]
    return build(rows)
