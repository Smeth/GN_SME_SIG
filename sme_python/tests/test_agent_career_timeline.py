"""Frise de parcours agent (diff snapshots, sans MySQL)."""

from __future__ import annotations

from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.services.carriere_snapshot_diff import (
    diff_carriere_snapshots,
    fmt_mois_label,
)


def test_diff_carriere_detects_grade_change() -> None:
    before = CarriereSnapshotRow(
        annee_mois="2025-07",
        date_debut=None,
        date_fin=None,
        grade="LT",
        date_grade=None,
        structure="DPF",
        position_admin="Présent",
        fonction=None,
        subdivision_arme="SEC",
        date_service=None,
        ndecision=None,
        diplome=None,
    )
    after = CarriereSnapshotRow(
        annee_mois="2025-08",
        date_debut=None,
        date_fin=None,
        grade="CNE",
        date_grade="2025-08-01",
        structure="DPF",
        position_admin="CongéAdmin",
        fonction=None,
        subdivision_arme="SEC",
        date_service=None,
        ndecision=None,
        diplome=None,
    )
    ch = diff_carriere_snapshots(before, after)
    assert ch["grade"] == {"old": "LT", "new": "CNE"}
    assert ch["position_admin"] == {"old": "Présent", "new": "CongéAdmin"}
    assert "structure" not in ch


def test_fmt_mois_label() -> None:
    assert "Août" in fmt_mois_label("2025-08")
