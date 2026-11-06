"""Tests baseline récap indisponibles (mois figé = M-1)."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.numerique_recap_baseline import recap_indispo_totals_from_rows
from sme_gend.services.numerique_excel_export import _prev_ym_canon


def test_prev_ym_canon_rolls_year() -> None:
    assert _prev_ym_canon("2026-01") == "2025-12"
    assert _prev_ym_canon("2026-05") == "2026-04"


def test_recap_totals_length_and_total_row() -> None:
    rows = [
        ListeGeneraleRow(
            id="1",
            noms="A",
            date_naissance=None,
            lieu_naissance=None,
            grade="GND",
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin="Mission",
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure="EM-GEND",
            obs=None,
            ordre_tri=None,
            presseance=None,
        ),
    ]
    grades = ["GND"]
    totals = recap_indispo_totals_from_rows(rows, grades)
    assert len(totals) == 10
    assert totals[9] == sum(totals[:9])
