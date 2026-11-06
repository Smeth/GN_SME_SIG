"""Alertes parcours : stockage, audit et concurrence sur instance MySQL jetable."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from test_restore_mysql import isolated_mysql
from sme_gend.services import career_import_alerts as alerts


@pytest.fixture
def alert_database(isolated_mysql, monkeypatch):
    settings, connect, params = isolated_mysql
    from sme_gend.repositories.mysql_common import apply_mysql_schema
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    apply_mysql_schema(settings)
    settings = replace(settings, mysql_view_mois="2026-09")
    AppConfigRepository(settings).ensure_tables()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms) VALUES (1,'101','SYNTHETIC ALICE')")
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission", lambda _: True)
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_audit_actor", lambda: "test-operator")
    return settings, connect, params


def test_existing_schema_initializes_twice_without_losing_legacy_ack(alert_database):
    settings, connect, params = alert_database
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO career_alert_ack (agent_legacy_id,alert_code,mois,note) VALUES ('101','OLD','','legacy note')")
    AppConfigRepository(settings).ensure_tables()
    AppConfigRepository(settings).ensure_tables()
    records = alerts.list_career_acknowledgements(settings)
    assert len(records) == 1
    assert records[0].note == "legacy note" and records[0].fingerprint is None
    assert alerts.fetch_acknowledged_keys(settings) == set()


def test_ack_reopen_metadata_and_business_audit_are_persisted(alert_database):
    settings, connect, params = alert_database
    alert = alerts.CareerAlert("warning", "101", "SYNTHETIC ALICE", "2026-09", "GRADE_CLEARED", "Synthetic grade cleared")
    alerts.acknowledge_alert(settings, alert.agent_legacy_id, alert.code, alert.mois,
                             fingerprint=alert.fingerprint(), note="Synthetic vérification", alert=alert)
    record, = alerts.list_career_acknowledgements(settings)
    assert record.by == "test-operator" and record.note == "Synthetic vérification"
    assert record.noms == "SYNTHETIC ALICE" and record.at and record.token
    assert alert.occurrence_key() in alerts.fetch_acknowledged_keys(settings)
    alerts.reopen_career_alert(settings, record)
    assert alerts.fetch_acknowledged_keys(settings) == set()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT action FROM audit_journal WHERE module='career_alerts' ORDER BY id")
            assert cur.fetchall() == (("career_alert_ack",), ("career_alert_reopen",))


def test_simultaneous_ack_cannot_overwrite_first_actor_comment(alert_database):
    settings, connect, params = alert_database
    gate = Barrier(2)
    def acknowledge(note):
        gate.wait(timeout=10)
        try:
            alerts.acknowledge_alert(settings, "101", "CODE", "2026-09", fingerprint="a" * 64, note=note)
            return "ok", note
        except RuntimeError as error:
            assert "déjà été acquittée" in str(error)
            return "conflict", note
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(acknowledge, ["FIRST", "SECOND"]))
    winner, = [note for status, note in results if status == "ok"]
    assert sum(status == "conflict" for status, _ in results) == 1
    record, = alerts.list_career_acknowledgements(settings)
    assert record.note == winner
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE module='career_alerts' AND action='career_alert_ack'")
            assert cur.fetchone()[0] == 1


def test_stale_reactivation_and_audit_failure_cannot_delete_ack(alert_database, monkeypatch):
    settings, connect, params = alert_database
    alerts.acknowledge_alert(settings, "101", "CODE", "2026-09", fingerprint="b" * 64)
    record, = alerts.list_career_acknowledgements(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE career_alert_ack_occurrence SET note='changed elsewhere'")
    with pytest.raises(RuntimeError, match="changé"):
        alerts.reopen_career_alert(settings, record)
    updated, = alerts.list_career_acknowledgements(settings)
    def audit_failure(*_, **__):
        raise RuntimeError("Synthetic audit unavailable")
    monkeypatch.setattr("sme_gend.services.business_audit.record_business_action", audit_failure)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        alerts.reopen_career_alert(settings, updated)
    assert len(alerts.list_career_acknowledgements(settings)) == 1


def test_synthetic_career_detection_dossier_and_ack_are_consistent(alert_database, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from sme_gend.config import liste_generale_scope
    from sme_gend.services.personnel_lookup_service import PersonnelLookupService
    from sme_gend.services.dossier_read_snapshot import load_dossier_read_snapshot
    from sme_gend.services.career_alerts_cache import scan_and_cache_career_alerts
    settings, connect, params = alert_database
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: settings)
    monkeypatch.setattr(liste_generale_scope, "load_sme_context", lambda: SimpleNamespace(mois_actif="2026-09"))
    monkeypatch.setattr("sme_gend.config.sme_context.is_mois_ferme", lambda _: False)
    monkeypatch.setenv("SME_PERSONNEL_MEDIA_PATH", str(tmp_path / "media"))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-01','2026-01-31'),(2,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,grade_libelle,structure_code,position_admin_libelle,est_actuelle) "
                        "VALUES (11,1,1,'LT','A','Présent',0),(22,1,2,'LT','B','Présent',1)")
    central = scan_and_cache_career_alerts(settings)
    assert len(central.alerts) == 1
    assert central.alerts[0].code == "STRUCTURE_JUMP"
    row, context = PersonnelLookupService(settings).resolve_dossier_for_legacy_id("101")
    assert row.id == "22" and context is None
    snapshot = load_dossier_read_snapshot(settings, row)
    assert snapshot["alerts"] == central.alerts
    alert = central.alerts[0]
    alerts.acknowledge_alert(settings, "101", alert.code, alert.mois, fingerprint=alert.fingerprint(), alert=alert)
    assert scan_and_cache_career_alerts(settings).total == 0
    assert alerts.alerts_for_agent(settings, "101") == []


@pytest.mark.parametrize("both_in_september", [False, True])
def test_provenance_reads_month_overlap_import_and_explicit_audit(alert_database, monkeypatch, both_in_september):
    import json
    from sme_gend.services import career_alert_provenance as provenance
    settings, connect, params = alert_database
    monkeypatch.setattr(provenance, "session_has_permission", lambda _: True)
    monkeypatch.setattr(provenance, "resolve_carriere_scope_mois", lambda _: "2026-09")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET cree_le='2026-10-01',updated_le='2026-10-02' WHERE id=1")
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms,cree_le,updated_le) VALUES (2,'202','SYNTHETIC ALICE','2026-10-01','2026-10-02')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-01','2026-01-31'),(2,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO import_lot (id,sme_periode_id,chemin_fichier,importe_le) VALUES (7,1,%s,'2026-10-01 12:00:00')", (r"C:\synthetic\january.mdb",))
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,import_lot_id,est_actuelle) VALUES (11,1,1,7,0),(12,1,2,NULL,1),(21,2,1,7,0)")
            if both_in_september:
                cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,est_actuelle) VALUES (22,2,2,1)")
            cur.execute("INSERT INTO audit_journal (ts_utc,action,actor,module,personnel_id,payload) VALUES ('2026-10-01','create','SYNTHETIC AUTHOR','personnel','101',%s)",
                (json.dumps({"after": {"access_join_id": "101"}, "edit_motif": "Synthetic verified entry"}),))
    report = provenance.load_career_provenance(settings, ["101", "202"])
    assert not report.warnings
    assert report.fiches[0].months == ["2026-01", "2026-09"]
    assert report.fiches[0].created.startswith("2026-10-01")
    assert report.fiches[1].visible_in_month is both_in_september
    assert sum(f.visible_in_month for f in report.fiches) == (2 if both_in_september else 1)
    assert len([event for event in report.events if event.kind == "Situation liée à un import"]) == 2
    creation, = [event for event in report.events if event.kind == "Création journalisée"]
    assert creation.legacy_id == "101" and creation.actor == "SYNTHETIC AUTHOR"
    assert "Synthetic verified entry" in provenance.describe_provenance(report)
