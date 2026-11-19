"""Monthly SerpApi budget: what is used, what is left, whether one more lookup may start.

Pure: no network, no Streamlit. The numbers come from SerpApi's Account API (fetched in
fetching.py; that call is free and does not count as a search). The owner accepts at most
DEFAULT_LIMIT searches a month (2026-11-18); a price check costs LOOKUP_COST of them.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_LIMIT = 250  # the owner raised it from 100 on 2026-11-18, after the first live check
LOOKUP_COST = 2  # a whole lookup: google_shopping + google_immersive_product (one search each)


@dataclass(frozen=True)
class Account:
    used: int  # searches made this month, as SerpApi counts them
    left: int | None  # searches the plan still allows, when SerpApi says


def _count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_account(data):
    """Account API answer -> Account, or None when it does not say how much was used.

    Only the two numbers are taken over: the answer also holds the API key and the owner's
    e-mail address, which must never be kept.
    """
    if not isinstance(data, dict):
        return None
    used = _count(data.get("this_month_usage"))
    if used is None:
        return None
    left = _count(data.get("total_searches_left"))
    if left is None:
        left = _count(data.get("plan_searches_left"))
    return Account(used, left)


def parse_limit(text, default=DEFAULT_LIMIT):
    """The monthly limit from a secret ("150"); anything unusable gives the default."""
    value = _count(str(text).strip()) if text is not None else None
    return value if value else default


def remaining(account, limit=DEFAULT_LIMIT):
    """Searches still allowed this month: under the owner's limit and under the plan's."""
    room = max(limit - account.used, 0)
    return room if account.left is None else min(room, account.left)


def check(account, limit=DEFAULT_LIMIT, cost=LOOKUP_COST):
    """None when a lookup may start, otherwise the reason (Dutch) why not."""
    if account.left is not None and account.left < cost:
        return "Je SerpApi-abonnement heeft geen zoekopdrachten meer over. Er is niets opgehaald."
    if account.used + cost > limit:
        return (
            f"Maandbudget bereikt: {account.used} van {limit} SerpApi-zoekopdrachten gebruikt en "
            f"deze stap kost er {cost}. Er is niets opgehaald. Wil je meer toestaan, zet dan "
            "SERPAPI_MONTHLY_LIMIT onder Secrets hoger."
        )
    return None


def summary(account, limit=DEFAULT_LIMIT, cost=LOOKUP_COST):
    """One line for the screen: used, left and how many price checks that is."""
    left = remaining(account, limit)
    return (
        f"SerpApi deze maand: {account.used} van {limit} gebruikt, nog {left} over "
        f"(genoeg voor {left // cost} prijschecks)."
    )
