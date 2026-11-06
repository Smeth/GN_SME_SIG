"""Périmètre SME DPF."""

from __future__ import annotations

from dataclasses import replace

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.sme_dpf_scope import row_in_sme_dpf_scope


def _row(**kwargs) -> ListeGeneraleRow:
    base = ListeGeneraleRow(
        id="1",
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
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
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    return replace(base, **kwargs) if kwargs else base


def test_dpf_scope_structures() -> None:
    assert row_in_sme_dpf_scope(_row(structure="DPF"))
    assert row_in_sme_dpf_scope(_row(structure="CS-DPF"))
    assert not row_in_sme_dpf_scope(_row(structure="RG-BZV"))
    assert row_in_sme_dpf_scope(_row(structure="COMGEND", fonction="DIRECTEUR DPF"))


def test_dpf_view_filter() -> None:
    from sme_gend.services.sme_dpf_scope import (
        DPF_VIEW_COMGEND,
        DPF_VIEW_CONTROLE,
        DPF_VIEW_DPF,
        row_matches_dpf_view_filter,
    )

    dpf = _row(structure="DPF")
    cs = _row(structure="CS-DPF")
    com = _row(structure="COMGEND", fonction="Adjoint DPF")
    other = _row(structure="COMGEND", fonction="Autre")

    assert row_matches_dpf_view_filter(dpf, DPF_VIEW_DPF)
    assert not row_matches_dpf_view_filter(cs, DPF_VIEW_DPF)
    assert row_matches_dpf_view_filter(cs, DPF_VIEW_CONTROLE)
    assert row_matches_dpf_view_filter(com, DPF_VIEW_COMGEND)
    assert not row_matches_dpf_view_filter(other, DPF_VIEW_COMGEND)
