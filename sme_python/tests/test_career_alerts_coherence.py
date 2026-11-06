from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.personnel_lookup import HistoricalDossierContext
from sme_gend.services import career_import_alerts as alerts
from sme_gend.services import career_alerts_cache as cache
from sme_gend.services.personnel_lookup_service import PersonnelLookupService
from test_career_import_alerts import _snap


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    from sme_gend.services import personnel_lookup_service
    monkeypatch.setattr(personnel_lookup_service, "MySQLAgentRepository", MagicMock())


def test_resolver_never_confuses_legacy_with_career_id():
    service = PersonnelLookupService(Settings(database_path=Path("unused"), mysql_view_mois="2026-09"))
    repo = MagicMock()
    service._agents = repo
    row = MagicMock(id="9876")
    repo.fetch_scoped_by_legacy.return_value = row
    service.dossier_row_for_legacy_id = MagicMock()
    assert service.resolve_dossier_for_legacy_id("101") == (row, None)
    repo.fetch_scoped_by_legacy.assert_called_once_with("101")
    repo.fetch_by_id.assert_not_called()
    service.dossier_row_for_legacy_id.assert_not_called()


def test_historical_only_after_absence_in_consulted_month():
    service = PersonnelLookupService(Settings(database_path=Path("unused"), mysql_view_mois="2026-04"))
    service._agents = MagicMock()
    service._agents.fetch_scoped_by_legacy.return_value = None
    row = MagicMock()
    service.dossier_row_for_legacy_id = MagicMock(return_value=(row, HistoricalDossierContext("2026-09", "2026-03", None)))
    result, context = service.resolve_dossier_for_legacy_id("101")
    assert result is row
    assert context.active_mois == "2026-04"
    assert "mois consulté (2026-04)" in context.banner_text()
    assert "position administrative" in context.banner_text()


def test_lookup_error_is_not_misreported_as_historical_absence():
    service = PersonnelLookupService(Settings(database_path=Path("unused")))
    service._agents = MagicMock()
    service._agents.fetch_scoped_by_legacy.side_effect = RuntimeError("offline")
    service.dossier_row_for_legacy_id = MagicMock()
    with pytest.raises(RuntimeError):
        service.resolve_dossier_for_legacy_id("101")
    service.dossier_row_for_legacy_id.assert_not_called()


def test_grade_returning_to_previous_value_is_not_unchanged():
    snapshots = [_snap("2022-01", None), replace(_snap("2023-01", None), grade="CNE"), _snap("2024-01", None)]
    assert not any(a.code == "GRADE_LONG_UNCHANGED" for a in alerts._scan_agent_snaps("101", "Test", snapshots))


def test_grade_duration_is_an_observation_not_a_promotion_judgment():
    result = alerts._scan_agent_snaps("101", "Test", [_snap("2022-01", None), _snap("2024-01", None)])
    alert = next(a for a in result if a.code == "GRADE_LONG_UNCHANGED")
    assert alert.severity == "info"
    assert "situations disponibles" in alert.message
    assert "ne constitue pas" in alert.message


def test_invalid_grade_date_detected_in_later_month():
    identity = alerts._AgentIdentityRow("101", "Test", None, None, date(2000, 1, 1))
    result = alerts._scan_date_incoherente([identity], {"101": ("Test", [_snap("2026-01", "2020-01-01"), _snap("2026-02", "1990-01-01")])})
    assert [a.mois for a in result] == ["2026-02"]


def test_cache_scans_consulted_month(monkeypatch):
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-04")
    scan = MagicMock(return_value=[])
    monkeypatch.setattr(cache, "scan_all_career_alerts", scan)
    cache.invalidate_career_alerts_cache()
    cache.scan_and_cache_career_alerts(settings)
    assert scan.call_args.kwargs["mois_focus"] == "2026-04"
    cache.invalidate_career_alerts_cache()


def test_future_invalid_date_excluded_from_past_scan(monkeypatch):
    repo = MagicMock()
    repo.load_carriere_history_bulk.return_value = {"101": ("Test", [_snap("2026-01", "2020-01-01"), _snap("2026-09", "1990-01-01")])}
    monkeypatch.setattr(alerts, "MySQLAgentRepository", lambda _: repo)
    monkeypatch.setattr(alerts, "_load_affectation_index", lambda _: {})
    monkeypatch.setattr(alerts, "_load_legacy_agent_map", lambda _: {})
    monkeypatch.setattr(alerts, "_load_agent_identity_index", lambda _: [alerts._AgentIdentityRow("101", "Test", None, None, date(2000, 1, 1))])
    monkeypatch.setattr(alerts, "_load_agents_without_carriere", lambda _: [])
    monkeypatch.setattr(alerts, "fetch_acknowledged_keys", lambda _: set())
    result = alerts.scan_all_career_alerts(Settings(database_path=Path("unused")), mois_focus="2026-04")
    assert result == []


def test_ack_read_failure_is_visible(monkeypatch):
    connection = MagicMock()
    cursor = connection.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("ack storage unavailable")
    monkeypatch.setattr(alerts, "MySQLConnection", lambda _: connection)
    with pytest.raises(RuntimeError, match="ack storage"):
        alerts.fetch_acknowledged_keys(Settings(database_path=Path("unused")))


def test_repository_query_uses_legacy_identity_and_list_scope(monkeypatch):
    from sme_gend.repositories import mysql_agent_repository as module
    repo = object.__new__(module.MySQLAgentRepository)
    monkeypatch.setattr(module.MySQLAgentRepository, "_scope_mois", property(lambda _: "2026-09"))
    connection = MagicMock()
    repo._connect = MagicMock(return_value=connection)
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None
    assert repo.fetch_scoped_by_legacy("101") is None
    sql, params = cursor.execute.call_args.args
    assert "WHERE a.legacy_access_id = %s" in sql
    assert "c.id = %s" not in sql
    assert params[-1] == "101"
    assert "2026-09" in params[:-1]
