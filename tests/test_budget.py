from functools import partial

import pytest

import budget
from budget import Account

LIMIT = 100  # explicit here, so these tests do not depend on the default
check = partial(budget.check, limit=LIMIT)
remaining = partial(budget.remaining, limit=LIMIT)
summary = partial(budget.summary, limit=LIMIT)


def test_parse_account_takes_only_the_numbers():
    data = {
        "api_key": "SECRET",
        "account_email": "me@example.test",
        "this_month_usage": 24,
        "plan_searches_left": 226,
        "total_searches_left": 226,
        "searches_per_month": 250,
    }
    account = budget.parse_account(data)
    assert account == Account(24, 226)
    assert "SECRET" not in repr(account) and "example.test" not in repr(account)


def test_parse_account_falls_back_to_plan_searches_left_and_accepts_digit_strings():
    assert budget.parse_account({"this_month_usage": "7", "plan_searches_left": 93}) == Account(7, 93)
    assert budget.parse_account({"this_month_usage": 7}) == Account(7, None)


@pytest.mark.parametrize(
    "data",
    [None, [], "x", {}, {"this_month_usage": None}, {"this_month_usage": -1}, {"this_month_usage": True}, {"this_month_usage": "veel"}],
)
def test_parse_account_without_usage_is_none(data):
    assert budget.parse_account(data) is None


@pytest.mark.parametrize(
    "text, expected",
    [("150", 150), (" 60 ", 60), (None, None), ("", None), ("0", None), ("-5", None), ("veel", None), (80, 80)],
)
def test_parse_limit(text, expected):
    # None in the table means "unusable: the default"
    assert budget.parse_limit(text) == (budget.DEFAULT_LIMIT if expected is None else expected)


def test_the_default_limit_is_the_owners_250_a_month():
    assert budget.DEFAULT_LIMIT == 250


def test_check_allows_up_to_exactly_the_limit():
    assert check(Account(98, 152)) is None  # 98 + 2 = 100: the 100th search is allowed
    assert "Maandbudget bereikt: 99 van 100" in check(Account(99, 151))
    assert "Maandbudget bereikt: 100 van 100" in check(Account(100, 150))


def test_check_uses_the_given_limit_and_cost():
    assert check(Account(120, 130), limit=150) is None
    assert check(Account(149, 100), limit=150, cost=2) is not None
    assert check(Account(149, 100), limit=150, cost=1) is None


def test_check_stops_when_the_plan_itself_is_empty():
    reason = check(Account(10, 1))
    assert "geen zoekopdrachten meer over" in reason
    assert check(Account(10, 2)) is None
    assert check(Account(10, None)) is None  # plan unknown: only the owner's limit counts


def test_the_reason_names_the_setting_to_change():
    assert "SERPAPI_MONTHLY_LIMIT" in check(Account(100, 150))


def test_remaining_is_the_smaller_of_limit_and_plan():
    assert remaining(Account(24, 226)) == 76
    assert remaining(Account(24, 10)) == 10
    assert remaining(Account(130, 120)) == 0
    assert remaining(Account(24, None), limit=150) == 126


def test_summary_is_one_dutch_line():
    assert summary(Account(24, 226)) == (
        "SerpApi deze maand: 24 van 100 gebruikt, nog 76 over (genoeg voor 38 prijschecks)."
    )
    assert "nog 0 over (genoeg voor 0 prijschecks)" in summary(Account(100, 150))
    assert "nog 1 over (genoeg voor 0 prijschecks)" in summary(Account(99, 151))
