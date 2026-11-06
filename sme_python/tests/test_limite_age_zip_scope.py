"""Tests export ZIP attestations limite d'âge."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.limite_age_service import LimiteDateResult
from sme_gend.ui.views.limite_age_view import resolve_zip_export_rows


def _row(noms: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=noms,
        noms=noms,
        date_naissance=None,
        lieu_naissance=None,
        grade="MDL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def _pair(noms: str) -> tuple[ListeGeneraleRow, LimiteDateResult]:
    return _row(noms), LimiteDateResult(
        limit_date=None,
        limit_type="—",
        age_limit_date=None,
        service_limit_date=None,
        grade_entry=None,
    )


def test_resolve_zip_prefers_checked() -> None:
    checked = [_pair("A")]
    filtered = [_pair("A"), _pair("B")]
    rows, mode = resolve_zip_export_rows(checked, filtered)
    assert mode == "checked"
    assert len(rows) == 1


def test_resolve_zip_fallback_filtered() -> None:
    filtered = [_pair("A"), _pair("B")]
    rows, mode = resolve_zip_export_rows([], filtered)
    assert mode == "filtered"
    assert len(rows) == 2
