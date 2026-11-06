"""Tests logique import par période (sans MySQL réel)."""

from __future__ import annotations

import calendar
from datetime import date

import pytest

from sme_gend.services.mysql_import_period import (
    is_historical_import,
    last_day_of_month,
    normalize_import_mois,
)


def test_normalize_import_mois_formats():
    assert normalize_import_mois("2026-04") == "2026-04"
    assert normalize_import_mois("202604") == "2026-04"


def test_normalize_invalid_raises():
    with pytest.raises(ValueError):
        normalize_import_mois("pas-un-mois")


def test_last_day_of_month():
    assert last_day_of_month("2026-02") == date(2026, 2, 28)
    y, m = 2024, 2
    assert last_day_of_month("2024-02") == date(y, m, calendar.monthrange(y, m)[1])


def test_is_historical_import():
    assert is_historical_import("2026-03", "2026-05") is True
    assert is_historical_import("2026-05", "2026-05") is False
    assert is_historical_import("2026-06", "2026-05") is False
    assert is_historical_import("2026-04", None) is False
