"""Jointure ligne liste ↔ fiche stage (Id Access / carrière)."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.services.stages_list_filter import stage_detail_for_row


def _row(**kw) -> ListeGeneraleRow:
    base = dict(
        id="999",
        noms="X",
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="StageEx",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
        access_join_id=None,
    )
    base.update(kw)
    return ListeGeneraleRow(**base)


def test_stage_detail_for_row_falls_back_to_legacy_access_id() -> None:
    row = _row(id="888", access_join_id="26267")
    details = {"26267": StageDetail("CAOG", "France", "2026")}
    detail = stage_detail_for_row(row, details)
    assert detail is not None
    assert detail.nature_stage == "CAOG"
