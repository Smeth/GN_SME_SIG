"""Tri colonne Grade via PersonnelFilterProxy (préséance, pas alphabétique)."""

from __future__ import annotations

import sys
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
from sme_gend.ui.personnel_table_model import PersonnelTableModel


def _row(nom: str, grade: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=nom,
        noms=nom,
        sexe=None,
        grade=grade,
        fonction=None,
        subdivision_arme=None,
        position_admin=None,
        structure=None,
        matricule_militaire=None,
        matricule_solde=None,
        date_naissance=None,
        lieu_naissance=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        diplome=None,
        date_obtention=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def _grades() -> list[GradeRefRow]:
    return [
        GradeRefRow(code="COL", libelle="Colonel", pref_ord=1, effectif_g=None),
        GradeRefRow(code="CNE", libelle="Commandant", pref_ord=5, effectif_g=None),
        GradeRefRow(code="LTN", libelle="Lieutenant", pref_ord=12, effectif_g=None),
    ]


def test_proxy_sorts_grade_by_presence_not_alphabetically():
    app = QApplication.instance() or QApplication(sys.argv)

    rows = [
        _row("Alpha", "Lieutenant"),
        _row("Bravo", "Colonel"),
        _row("Charlie", "Commandant"),
    ]
    model = PersonnelTableModel(rows)
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_grade_sort_helper(GradeSortHelper(_grades()))

    proxy.sort(3, Qt.SortOrder.AscendingOrder)

    ordered = [proxy.index(r, 3).data() for r in range(proxy.rowCount())]
    assert ordered == ["Colonel", "Commandant", "Lieutenant"]


def test_default_grade_sort_places_both_gdb_first_regardless_of_function():
    app = QApplication.instance() or QApplication(sys.argv)

    rows = [
        _row("Colonel", "COLONEL"),
        _row("GDB B", "GENERAL DE BRIGADE"),
        _row("GDB A", "GENERAL DE BRIGADE"),
    ]
    rows[1].fonction = "Fonction secondaire"
    rows[2].fonction = "Fonction prioritaire"
    grades = [
        GradeRefRow(code="GDB", libelle="GENERAL DE BRIGADE", pref_ord=1, effectif_g=None),
        GradeRefRow(code="COL", libelle="COLONEL", pref_ord=2, effectif_g=None),
    ]
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(PersonnelTableModel(rows))
    proxy.set_grade_sort_helper(GradeSortHelper(grades))

    proxy.sort_by_grade()

    ordered_names = [proxy.index(r, 1).data() for r in range(proxy.rowCount())]
    assert ordered_names[:2] == ["GDB B", "GDB A"]
    assert ordered_names[2] == "Colonel"
