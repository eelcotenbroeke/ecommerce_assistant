"""Parsing and smoke tests for app.py.

`import app` runs the Streamlit script in bare mode (no server), which is fine for tests:
it only logs a warning. Keep the pure helpers free of Streamlit calls so they stay testable.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.mark.parametrize(
    "title, expected_ml",
    [
        ("Persil Sensitive Gel 6 x 900 ml wasmiddel", 5400.0),
        ("Coca-Cola 24 x 0,33 L blik", 7920.0),
        ("Weleda Calendula Babyshampoo 200 ml", 200.0),
        ("Kérastase Bain Hydra-Glaze Shampoo 500ml", 500.0),
        ("Shampoo 2 x 500ml voordeelverpakking", 1000.0),
        ("set van 3 flessen 250 ml", 750.0),
        ("Product zonder volume", None),
    ],
)
def test_extract_volume_ml(title, expected_ml):
    assert app.extract_volume_ml(title) == expected_ml


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("€ 14,05", 14.05),
        ("14.05", 14.05),
        ("EUR 1.234,56", 1234.56),
        ("1.234,56", 1234.56),
        (12.5, 12.5),
        ("gratis", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(raw, expected):
    assert app.parse_price(raw) == expected


def test_fmt_eur():
    assert app.fmt_eur(14.05) == "€ 14,05"


def test_app_starts_without_exception():
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
