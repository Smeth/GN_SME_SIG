"""Comparatif détachés entre mois SME."""

from __future__ import annotations

from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.detaches_month_compare import _compare_signature


def _row(*, padmin: str | None = "Détaché", structure: str | None = "DETACHE") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms="Test",
        date_naissance=datetime(1990, 1, 1),
        lieu_naissance=None,
        grade="Sdt",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=padmin,
        fonction=None,
        subdivision_arme="SIG",
        diplome=None,
        date_obtention=None,
        structure=structure,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_compare_signature_detects_padmin_change() -> None:
    assert _compare_signature(_row(padmin="Détaché")) != _compare_signature(
        _row(padmin="Présent")
    )


def test_compare_signature_ignores_identical_rows() -> None:
    assert _compare_signature(_row()) == _compare_signature(_row())
