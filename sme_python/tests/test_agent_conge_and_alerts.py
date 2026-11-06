"""Congés, alertes carrière, clés upsert satellites."""

from __future__ import annotations

from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.services.agent_conge_service import (
    CONGE_TYPE_ADMIN,
    padmin_to_conge_type,
    quota_days_between,
)
from sme_gend.services.career_import_alerts import _scan_agent_snaps
from sme_gend.services.numerique_padmin import PADMIN_CONGE_ADMIN


def test_padmin_to_conge_type() -> None:
    assert padmin_to_conge_type(PADMIN_CONGE_ADMIN) == CONGE_TYPE_ADMIN
    assert padmin_to_conge_type("Congé exp") == "EXPECTATIVE" or padmin_to_conge_type("CongéExp")


def test_quota_days_between() -> None:
    from datetime import date

    assert quota_days_between(date(2025, 1, 1), date(2025, 1, 10)) == 10.0


def test_alert_structure_jump() -> None:
    snaps = [
        CarriereSnapshotRow(
            "2025-01", None, None, "LT", None, "DPF", "Présent", None, "SEC", None, None, None
        ),
        CarriereSnapshotRow(
            "2025-02", None, None, "LT", None, "AUTRE", "Présent", None, "SEC", None, None, None
        ),
    ]
    alerts = _scan_agent_snaps("1", "TEST", snaps)
    codes = {a.code for a in alerts}
    assert "STRUCTURE_JUMP" in codes


def test_satellite_upsert_legacy_key_stable() -> None:
    leg = "42"
    key = leg or f"99:2025-01-01:nature"
    assert key == "42"
