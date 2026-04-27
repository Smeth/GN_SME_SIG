"""Clôture SME Piste A — utilitaires de normalisation de mois."""

from __future__ import annotations

from sme_gend.services.sme_month_closure import (
    canonical_sme_mois_yyyy_mm,
    extract_yyyy_mm,
    mois_labels_equivalent,
    slugify_mois_label,
)


def test_extract_yyyy_mm() -> None:
    assert extract_yyyy_mm("2026-04") == "2026-04"
    assert extract_yyyy_mm("Période 2026-04 SME") == "2026-04"
    assert extract_yyyy_mm("Avril") is None


def test_mois_labels_equivalent_yyyy() -> None:
    assert mois_labels_equivalent("2026-04", "2026-04")
    assert mois_labels_equivalent("2026-04", "Période 2026-04")
    assert not mois_labels_equivalent("2026-04", "2026-05")


def test_mois_labels_equivalent_plain() -> None:
    assert mois_labels_equivalent("Avril 2026", "avril 2026")


def test_canonical_sme_mois_yyyy_mm() -> None:
    assert canonical_sme_mois_yyyy_mm("2026-04") == "2026-04"
    assert canonical_sme_mois_yyyy_mm("202604") == "2026-04"
    assert canonical_sme_mois_yyyy_mm("042026") == "2026-04"
    assert canonical_sme_mois_yyyy_mm("012026") == "2026-01"
    assert canonical_sme_mois_yyyy_mm("032025") == "2025-03"


def test_mois_labels_equivalent_compact_formats() -> None:
    assert mois_labels_equivalent("042026", "2026-04")
    assert mois_labels_equivalent("202604", "2026-04")
    assert mois_labels_equivalent("012026", "2026-01")
    assert not mois_labels_equivalent("012026", "2026-04")


def test_slugify_mois_label() -> None:
    assert "2026-04" in slugify_mois_label("2026-04")
    assert slugify_mois_label("  ") == "mois"
