"""Calendrier trimestriel des campagnes d'avancement."""

from datetime import date

from sme_gend.services.avancement_trimestre import (
    normalize_trimestre,
    periode_label,
    trimestre_debut,
    trimestre_fin,
    trimestre_from_date,
)


def test_trimestre_from_date() -> None:
    assert trimestre_from_date(date(2026, 1, 15)) == 1
    assert trimestre_from_date(date(2026, 4, 1)) == 2
    assert trimestre_from_date(date(2026, 9, 30)) == 3
    assert trimestre_from_date(date(2026, 12, 31)) == 4


def test_periode_label() -> None:
    assert "T2 2026" in periode_label(2026, 2)
    assert trimestre_debut(2026, 2) == date(2026, 4, 1)
    assert trimestre_fin(2026, 2) == date(2026, 6, 30)


def test_normalize_trimestre() -> None:
    assert normalize_trimestre(3) == 3
    assert normalize_trimestre(99) == trimestre_from_date()
