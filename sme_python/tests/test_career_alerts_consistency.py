from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services import career_import_alerts as service
from sme_gend.services import career_alerts_cache as cache
from sme_gend.services.dashboard_service import build_career_alerts_kpi_card
from sme_gend.ui.views.career_alerts_view import CareerAlertsView
from test_career_import_alerts import _snap


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import pymysql
    monkeypatch.setattr(pymysql, "connect", MagicMock(side_effect=AssertionError("Real database access forbidden")))
    cache.invalidate_career_alerts_cache()
    yield
    cache.invalidate_career_alerts_cache()


@pytest.fixture
def engine(monkeypatch):
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-04")
    repo = MagicMock()
    repo.load_carriere_history_bulk.return_value = {
        "101": ("Alice", [_snap("2026-01", "2020-01-01"), _snap("2026-03", "1990-01-01"),
                           _snap("2026-09", "1991-01-01")]),
    }
    monkeypatch.setattr(service, "MySQLAgentRepository", lambda _: repo)
    monkeypatch.setattr(service, "_load_affectation_index", lambda _: {})
    monkeypatch.setattr(service, "_load_legacy_agent_map", lambda _: {})
    identities = [service._AgentIdentityRow("101", "Alice", "DUP", None, date(2000, 1, 1)),
                  service._AgentIdentityRow("202", "Bob", "DUP", None, None)]
    monkeypatch.setattr(service, "_load_agent_identity_index", lambda _: identities)
    monkeypatch.setattr(service, "_load_agents_without_carriere", lambda _: [("202", "Bob")])
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: set())
    return settings


def test_fiche_and_central_share_every_rule_period_and_ack(engine, monkeypatch):
    all_alerts = service.scan_all_career_alerts(engine)
    alice = service.alerts_for_agent(engine, "101")
    assert alice == [a for a in all_alerts if a.agent_legacy_id == "101"]
    assert {a.code for a in alice} == {"MATRICULE_DUPLIQUE", "DATE_INCOHERENTE"}
    assert all(a.mois <= "2026-04" for a in alice)
    bob = service.alerts_for_agent(engine, "202")
    assert {a.code for a in bob} == {"MATRICULE_DUPLIQUE", "AGENT_SANS_CARRIERE"}
    acknowledged = alice[0].occurrence_key()
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: {acknowledged})
    assert all(a.occurrence_key() != acknowledged for a in service.alerts_for_agent(engine, "101"))
    assert service.alerts_for_agent(engine, "101") == [a for a in service.scan_all_career_alerts(engine) if a.agent_legacy_id == "101"]


def test_changed_occurrence_reappears_without_unhiding_other_ack(engine, monkeypatch):
    current = service.scan_all_career_alerts(engine)
    alert = next(a for a in current if a.code == "DATE_INCOHERENTE")
    old_version = replace(alert, message="Ancien constat avec une autre date")
    other = next(a for a in current if a.agent_legacy_id == "202")
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: {old_version.occurrence_key(), other.occurrence_key()})
    active = service.scan_all_career_alerts(engine)
    assert alert in active
    assert other not in active
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: {alert.occurrence_key()})
    assert alert not in service.scan_all_career_alerts(engine)
    assert alert in service.scan_all_career_alerts(engine, include_acknowledged=True)


def test_legacy_ack_cannot_hide_unversioned_current_alert(engine, monkeypatch):
    alert = service.scan_all_career_alerts(engine)[0]
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: {(alert.agent_legacy_id, alert.code, alert.mois)})
    assert alert in service.scan_all_career_alerts(engine)


def test_fingerprint_includes_severity_and_message_not_display_name():
    alert = service.CareerAlert("warning", "101", "Alice", "", "CODE", "Constat A")
    assert alert.fingerprint() == replace(alert, noms="Alice corrigé").fingerprint()
    assert alert.fingerprint() != replace(alert, message="Constat B").fingerprint()
    assert alert.fingerprint() != replace(alert, severity="info").fingerprint()


def test_duplicate_order_does_not_change_fingerprint():
    identities = [service._AgentIdentityRow("101", "Alice", "DUP", None, None),
                  service._AgentIdentityRow("202", "Bob", "DUP", None, None)]
    forward = {a.occurrence_key() for a in service._scan_matricule_duplicates(identities)}
    reverse = {a.occurrence_key() for a in service._scan_matricule_duplicates(list(reversed(identities)))}
    assert forward == reverse


def test_two_anomalies_with_same_agent_code_month_are_independent(engine, monkeypatch):
    rows = [service._AgentIdentityRow("101", "Alice", "MIL", "SOLDE", None),
            service._AgentIdentityRow("202", "Bob", "MIL", "SOLDE", None)]
    monkeypatch.setattr(service, "_load_agent_identity_index", lambda _: rows)
    duplicates = [a for a in service.alerts_for_agent(engine, "101") if a.code == "MATRICULE_DUPLIQUE"]
    assert len(duplicates) == 2
    monkeypatch.setattr(service, "fetch_acknowledged_keys", lambda _: {duplicates[0].occurrence_key()})
    remaining = service.alerts_for_agent(engine, "101")
    assert duplicates[0] not in remaining
    assert duplicates[1] in remaining


def test_occurrence_storage_reads_all_four_key_parts(monkeypatch):
    connection = MagicMock()
    cursor = connection.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("101", "CODE", "2026-04", "a" * 64)]
    monkeypatch.setattr(service, "MySQLConnection", lambda _: connection)
    assert service.fetch_acknowledged_keys(Settings(database_path=Path("unused"))) == {("101", "CODE", "2026-04", "a" * 64)}
    assert "career_alert_ack_occurrence" in cursor.execute.call_args.args[0]


def test_new_and_existing_database_schema_preserve_legacy_ack():
    root = Path(__file__).resolve().parents[1] / "data" / "schema"
    from sme_gend.repositories.mysql_common import _split_sql_statements
    definitions = []
    for filename in ("mysql_career_alerts.sql", "mysql_smegend_unified.sql"):
        statements = _split_sql_statements((root / filename).read_text(encoding="utf-8"))
        definition = next(s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS career_alert_ack_occurrence"))
        assert "PRIMARY KEY (agent_legacy_id, alert_code, mois, fingerprint)" in definition
        assert any(s.startswith("CREATE TABLE IF NOT EXISTS career_alert_ack (") for s in statements)
        assert not any(s.startswith(("DROP TABLE career_alert_ack", "DELETE FROM career_alert_ack", "UPDATE career_alert_ack")) for s in statements)
        definitions.append(definition)
    assert definitions[0] == definitions[1]


def test_complete_scan_search_filters_and_counter_beyond_300(engine, monkeypatch):
    monkeypatch.setattr(service, "_load_agents_without_carriere", lambda _: [(str(i), f"Agent {i}") for i in range(450)])
    complete = service.scan_all_career_alerts(engine)
    assert len(complete) > 450
    # Même un ancien appelant qui demande 300 ne doit pas tronquer le cache partagé.
    snapshot = cache.scan_and_cache_career_alerts(engine, limit=300)
    assert snapshot.total == len(complete)
    assert snapshot.warning_count == sum(a.severity == "warning" for a in complete)
    card = build_career_alerts_kpi_card(engine, scan_if_missing=False)
    assert card.value == str(snapshot.warning_count)
    app = QApplication.instance() or QApplication([])
    from sme_gend.ui.views import career_alerts_view as ui
    monkeypatch.setattr(ui, "session_has_permission", lambda _: True)
    widget = CareerAlertsView(engine, MagicMock(), MagicMock())
    widget._loaded(snapshot)
    target = complete[-1]
    assert complete.index(target) >= 300
    widget.search.setText(target.noms)
    widget.code.setCurrentIndex(widget.code.findData(target.code))
    assert target in widget._visible
    widget.close()
