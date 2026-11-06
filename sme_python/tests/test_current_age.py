"""Calcul d'âge actuel (limite d'âge)."""

from datetime import date

from sme_gend.services.limite_age_service import current_age_years, match_age_filter


def test_current_age_years_before_birthday() -> None:
    assert current_age_years(date(1980, 6, 15), on=date(2026, 3, 1)) == 45


def test_current_age_years_after_birthday() -> None:
    assert current_age_years(date(1980, 6, 15), on=date(2026, 7, 1)) == 46


def test_current_age_years_on_birthday() -> None:
    assert current_age_years(date(1980, 6, 15), on=date(2026, 6, 15)) == 46


def test_current_age_years_missing_birth() -> None:
    assert current_age_years(None) is None


def test_match_age_filter_operators() -> None:
    assert match_age_filter(55, ">", 50) is True
    assert match_age_filter(50, ">", 50) is False
    assert match_age_filter(49, "<", 50) is True
    assert match_age_filter(50, "<", 50) is False
    assert match_age_filter(50, "=", 50) is True
    assert match_age_filter(51, "=", 50) is False
    assert match_age_filter(None, "=", 50) is False

