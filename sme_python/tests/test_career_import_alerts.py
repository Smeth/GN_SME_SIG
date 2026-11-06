"""Alertes qualité parcours : nouveaux contrôles et filtrage d'acquittement (sans MySQL)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.services import career_import_alerts as cia
from sme_gend.services import career_alerts_cache as cac
from sme_gend.services.dashboard_service import build_career_alerts_kpi_card
from sme_gend.services.career_import_alerts import (
    _AgentIdentityRow,
    _scan_agents_sans_carriere,
    _scan_date_incoherente,
    _scan_matricule_duplicates,
)


def _snap(annee_mois: str, date_grade: str | None) -> CarriereSnapshotRow:
    return CarriereSnapshotRow(
        annee_mois=annee_mois,
        date_debut=None,
        date_fin=None,
        grade="LT",
        date_grade=date_grade,
        structure="DPF",
        position_admin="Présent",
        fonction=None,
        subdivision_arme="SEC",
        date_service=None,
        ndecision=None,
        diplome=None,
    )


def test_scan_matricule_duplicates_flags_both_agents() -> None:
    rows = [
        _AgentIdentityRow("100", "DUPONT", "MM123", None, None),
        _AgentIdentityRow("200", "MARTIN", "MM123", None, None),
        _AgentIdentityRow("300", "DURAND", "MM999", None, None),
    ]
    alerts = _scan_matricule_duplicates(rows)
    assert {a.agent_legacy_id for a in alerts} == {"100", "200"}
    assert all(a.code == "MATRICULE_DUPLIQUE" for a in alerts)
    assert all(a.severity == "warning" for a in alerts)


def test_scan_matricule_duplicates_ignores_empty_and_unique() -> None:
    rows = [
        _AgentIdentityRow("100", "DUPONT", None, None, None),
        _AgentIdentityRow("200", "MARTIN", "", None, None),
        _AgentIdentityRow("300", "DURAND", "MM999", None, None),
    ]
    assert _scan_matricule_duplicates(rows) == []


def test_scan_date_incoherente_detects_grade_before_birth() -> None:
    rows = [_AgentIdentityRow("100", "DUPONT", None, None, date(2000, 1, 1))]
    bulk = {"100": ("DUPONT", [_snap("2010-05", "1999-06-01")])}
    alerts = _scan_date_incoherente(rows, bulk)
    assert len(alerts) == 1
    assert alerts[0].code == "DATE_INCOHERENTE"
    assert alerts[0].agent_legacy_id == "100"


def test_scan_date_incoherente_no_alert_when_coherent() -> None:
    rows = [_AgentIdentityRow("100", "DUPONT", None, None, date(2000, 1, 1))]
    bulk = {"100": ("DUPONT", [_snap("2020-05", "2020-01-01")])}
    assert _scan_date_incoherente(rows, bulk) == []


def test_scan_agents_sans_carriere() -> None:
    alerts = _scan_agents_sans_carriere([("100", "DUPONT")])
    assert len(alerts) == 1
    assert alerts[0].code == "AGENT_SANS_CARRIERE"
    assert alerts[0].mois == ""


def test_scan_all_career_alerts_filters_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRepo:
        def __init__(self, settings: Settings) -> None:
            pass

        def load_carriere_history_bulk(self):
            return {"100": ("DUPONT", [])}

    monkeypatch.setattr(cia, "MySQLAgentRepository", _FakeRepo)
    monkeypatch.setattr(cia, "_load_affectation_index", lambda settings: {})
    monkeypatch.setattr(cia, "_load_legacy_agent_map", lambda settings: {})
    monkeypatch.setattr(cia, "_load_agent_identity_index", lambda settings: [])
    monkeypatch.setattr(
        cia, "_load_agents_without_carriere", lambda settings: [("100", "DUPONT")]
    )
    monkeypatch.setattr(
        cia,
        "fetch_acknowledged_keys",
        lambda settings: {_scan_agents_sans_carriere([("100", "DUPONT")])[0].occurrence_key()},
    )

    settings = Settings(database_path=Path("."), mysql_view_mois="2026-05")

    assert cia.scan_all_career_alerts(settings) == []

    alerts_all = cia.scan_all_career_alerts(settings, include_acknowledged=True)
    assert len(alerts_all) == 1
    assert alerts_all[0].code == "AGENT_SANS_CARRIERE"


def test_career_alerts_cache_can_retry_after_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(database_path=Path("."), mysql_view_mois="2026-05")
    cac.invalidate_career_alerts_cache()

    def _fail_scan(*args, **kwargs):
        raise RuntimeError("MySQL indisponible")

    monkeypatch.setattr(cac, "scan_all_career_alerts", _fail_scan)

    with pytest.raises(RuntimeError, match="MySQL indisponible"):
        cac.scan_and_cache_career_alerts(settings)

    snap = cac.get_career_alerts_snapshot(settings)
    assert not snap.scanning
    assert not snap.ready

    monkeypatch.setattr(cac, "scan_all_career_alerts", lambda *args, **kwargs: [])
    retried = cac.scan_and_cache_career_alerts(settings)
    assert retried.ready
    assert not retried.scanning


def test_dashboard_shows_alert_tile_when_career_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(database_path=Path("."), mysql_view_mois="2026-05")
    monkeypatch.setattr(cac, "get_career_alerts_snapshot", lambda _settings: cac.CareerAlertsSnapshot())

    def _fail_scan(*args, **kwargs):
        raise RuntimeError("MySQL indisponible")

    monkeypatch.setattr(cac, "scan_and_cache_career_alerts", _fail_scan)

    card = build_career_alerts_kpi_card(settings)

    assert card is not None
    assert card.value == "—"
    assert "impossible" in (card.subtitle or "").lower()
    assert "MySQL indisponible" in caplog.text
