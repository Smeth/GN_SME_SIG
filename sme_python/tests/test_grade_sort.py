"""Tri colonne Grade par ordre de préséance (PrefOrd TabGrade)."""

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.services.personnel_list_order import GradeSortHelper


def _grades() -> list[GradeRefRow]:
    return [
        GradeRefRow(code="COL", libelle="Colonel", pref_ord=1, effectif_g=None),
        GradeRefRow(code="CNE", libelle="Commandant", pref_ord=5, effectif_g=None),
        GradeRefRow(code="LTN", libelle="Lieutenant", pref_ord=12, effectif_g=None),
    ]


def test_grade_sort_helper_pref_ord_not_alphabetical():
    helper = GradeSortHelper(_grades())
    assert helper.compare_less("COL", "CNE")
    assert helper.compare_less("CNE", "LTN")
    assert not helper.compare_less("LTN", "CNE")


def test_grade_sort_helper_matches_libelle():
    helper = GradeSortHelper(_grades())
    assert helper.compare_less("Commandant", "Lieutenant")


def test_grade_sort_helper_unknown_falls_back_to_hierarchy():
    helper = GradeSortHelper(_grades())
    # LT inconnu du référentiel mais reconnu par la table interne
    assert helper.compare_less("LT", "MDL")
