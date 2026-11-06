"""Tests agrégats situation numérique."""

from __future__ import annotations

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.excel_style_helpers import build_grade_abbrev_map
from sme_gend.services.numerique_stats import build_numerique_matrix, count_by_grades


def _row(grade: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade=grade,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Present",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_count_by_grades_maps_libelle_to_template_code() -> None:
    grades_tpl = ["GDB", "CNE"]
    gmap = build_grade_abbrev_map(
        [
            GradeRefRow(code="GDB", libelle="GENERAL DE BRIGADE", pref_ord=1, effectif_g=None),
            GradeRefRow(code="CNE", libelle="CAPITAINE", pref_ord=2, effectif_g=None),
        ]
    )
    rows = [_row("CAPITAINE"), _row("GENERAL DE BRIGADE"), _row("CAPITAINE")]
    counts = count_by_grades(rows, grades_tpl, lambda _r: True, grade_abbrev_map=gmap)
    assert counts["CNE"] == 2
    assert counts["GDB"] == 1


def test_build_numerique_matrix_inscrits_non_zero_with_libelle() -> None:
    grades_tpl = ["CNE"]
    gmap = build_grade_abbrev_map(
        [GradeRefRow(code="CNE", libelle="CAPITAINE", pref_ord=1, effectif_g=None)]
    )
    rows = [_row("CAPITAINE")]
    matrix = build_numerique_matrix(rows, grades_tpl, grade_abbrev_map=gmap)
    assert matrix["inscrits"]["CNE"] == 1
