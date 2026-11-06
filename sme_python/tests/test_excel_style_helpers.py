"""Tests styles Excel et abréviations grade."""

from __future__ import annotations

from openpyxl import Workbook

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.services.excel_style_helpers import (
    build_grade_abbrev_map,
    clear_cell_value,
    grade_to_abbrev,
    write_cell_value,
)


def test_write_cell_value_on_merged_range() -> None:
    wb = Workbook()
    ws = wb.active
    ws.merge_cells("A1:C1")
    ws["A1"] = "Mois de janvier 2025"
    write_cell_value(ws, 1, 2, "Mois de juin 2026")
    assert ws["A1"].value == "Mois de juin 2026"


def test_clear_cell_value_on_merged_range() -> None:
    wb = Workbook()
    ws = wb.active
    ws.merge_cells("B2:D2")
    ws["B2"] = "Titre fusionné"
    clear_cell_value(ws, 2, 3)
    assert ws["B2"].value is None


def test_build_grade_abbrev_map_libelle_to_code() -> None:
    grades = [
        GradeRefRow(code="GDB", libelle="GENERAL DE BRIGADE", pref_ord=1, effectif_g=None),
        GradeRefRow(code="CLM", libelle="COLONEL-MAJOR", pref_ord=2, effectif_g=None),
    ]
    m = build_grade_abbrev_map(grades)
    assert grade_to_abbrev("GENERAL DE BRIGADE", m) == "GDB"
    assert grade_to_abbrev("GDB", m) == "GDB"
    assert grade_to_abbrev("COLONEL-MAJOR", m) == "CLM"
