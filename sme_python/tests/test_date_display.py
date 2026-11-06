"""Tests du formatage des dates à l'écran (jj/mm/aaaa)."""

from __future__ import annotations

from datetime import date, datetime

from sme_gend.services.date_display import fr_date


def test_date_objet_en_format_francais() -> None:
    assert fr_date(date(1985, 3, 12)) == "12/03/1985"
    assert fr_date(datetime(2026, 5, 1, 8, 30)) == "01/05/2026"


def test_chaine_iso_convertie() -> None:
    assert fr_date("2026-05-01") == "01/05/2026"
    assert fr_date("2026-05-01 00:00:00") == "01/05/2026"


def test_valeur_deja_formatee_conservation() -> None:
    assert fr_date("12/03/1985") == "12/03/1985"
    assert fr_date("2026-05") == "2026-05"  # période SME : pas une date


def test_vide_et_none_tiret() -> None:
    assert fr_date(None) == "—"
    assert fr_date("") == "—"
    assert fr_date("  ") == "—"
    assert fr_date(None, empty="") == ""


def test_annee_courte_ne_leve_pas() -> None:
    # strftime refuse les années < 1000 sur certaines plateformes : ne doit pas lever.
    assert fr_date(date(850, 1, 2)).startswith("02/01/")
