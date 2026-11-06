"""Rapprochements réels uniquement dans une base MySQL jetable."""
from dataclasses import fields, replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database
from sme_gend.services import personnel_merge_service as merge


@pytest.fixture
def merge_database(alert_database, tmp_path, monkeypatch):
    settings, connect, params = alert_database
    monkeypatch.setattr(merge, "session_has_admin_role", lambda: True)
    monkeypatch.setattr(merge, "session_can_modify_personnel", lambda: True)
    backup = tmp_path / "before-merge.zip"
    backup.write_bytes(b"synthetic backup fixture")
    monkeypatch.setattr("sme_gend.services.backup_restore_service.create_backup_to_default_dir", lambda _: backup)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms,telephone) VALUES (2,'202','SYNTHETIC ALICE','source phone')")
            cur.execute("UPDATE agent SET telephone='target phone' WHERE id=1")
            cur.execute("UPDATE agent SET matricule_militaire='MIL',matricule_solde='SOLD',date_naissance='1970-01-01'")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-01','2026-01-31'),(2,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,est_actuelle,date_debut,date_fin) "
                        "VALUES (11,2,1,0,'2026-01-01','2026-01-31'),(22,1,2,1,'2026-09-01',NULL)")
            cur.execute("UPDATE carriere_situation SET cree_le='2026-09-18 22:03:48',updated_le='2026-09-18 22:03:48' WHERE id=11")
            cur.execute("INSERT INTO agent_diplome (id,agent_id,categorie,libelle_libre) VALUES (500,2,'CIVIL','SYNTHETIC DIPLOMA')")
    return settings, connect, params


def test_merge_transfers_january_and_history_preserving_ids_and_alias(merge_database):
    settings, connect, params = merge_database
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert not preview.conflicts
    assert preview.counts["carriere_situation"] == 1 and preview.counts["agent_diplome"] == 1
    assert preview.months == ("2026-01",)
    result = merge.apply_personnel_merge(settings, preview, reason="Synthetic verified same person")
    assert result["source"] == "202"
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,agent_id,sme_periode_id,est_actuelle FROM carriere_situation ORDER BY id")
            assert cur.fetchall() == ((11,1,1,0),(22,1,2,1))
            cur.execute("SELECT updated_le FROM carriere_situation WHERE id=11")
            assert str(cur.fetchone()[0]) == "2026-09-18 22:03:48"
            cur.execute("SELECT id,agent_id,libelle_libre FROM agent_diplome")
            assert cur.fetchone() == (500,1,"SYNTHETIC DIPLOMA")
            cur.execute("SELECT legacy_access_id,telephone FROM agent")
            assert cur.fetchall() == (("101","target phone"),)
            cur.execute("SELECT alias_legacy_id,canonical_agent_id,source_snapshot FROM agent_identity_alias")
            old, aid, snapshot = cur.fetchone()
            assert old == "202" and aid == 1
            assert json.loads(snapshot)["telephone"] == "source phone"
            cur.execute("SELECT action FROM audit_journal WHERE action='personnel_merge'")
            assert cur.fetchone() == ("personnel_merge",)


def test_conflicting_month_blocks_every_transfer(merge_database):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,est_actuelle) VALUES (33,1,1,0)")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert any("période" in issue or "Conflit carriere" in issue for issue in preview.conflicts)
    with pytest.raises(ValueError):
        merge.apply_personnel_merge(settings, preview, reason="Synthetic")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_id FROM agent_diplome WHERE id=500")
            assert cur.fetchone() == (2,)


@pytest.mark.parametrize("failure", ["stale", "period", "audit", "backup"])
def test_stale_snapshot_audit_or_backup_failure_leaves_source_intact(merge_database, monkeypatch, failure):
    settings, connect, params = merge_database
    preview = merge.preview_personnel_merge(settings, "202", "101")
    if failure in ("stale", "period"):
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE agent_diplome SET libelle_libre='CHANGED' WHERE id=500" if failure == "stale"
                            else "UPDATE sme_periode SET annee_mois='2026-02' WHERE id=1")
    elif failure == "audit":
        def fail(*_, **__):
            raise RuntimeError("Synthetic audit failure")
        monkeypatch.setattr("sme_gend.services.business_audit.record_business_action", fail)
    else:
        monkeypatch.setattr("sme_gend.services.backup_restore_service.create_backup_to_default_dir", lambda _: None)
    with pytest.raises(RuntimeError):
        merge.apply_personnel_merge(settings, preview, reason="Synthetic")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT agent_id FROM carriere_situation WHERE id=11")
            assert cur.fetchone() == (2,)
            cur.execute("SELECT COUNT(*) FROM agent_identity_alias")
            assert cur.fetchone()[0] == 0


def test_unknown_foreign_key_is_a_blocking_conflict(merge_database):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE synthetic_unknown (id INT PRIMARY KEY,owner_id BIGINT,FOREIGN KEY(owner_id) REFERENCES agent(id)) ENGINE=InnoDB")
            cur.execute("INSERT INTO synthetic_unknown VALUES (1,2)")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert any("synthetic_unknown.owner_id" in issue for issue in preview.conflicts)


@pytest.mark.parametrize("historical", [False, True])
def test_old_identity_is_resolved_for_import_and_audit(merge_database, monkeypatch, historical):
    settings, connect, params = merge_database
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.mysql_access_numerique_import import _agent_id
    from sme_gend.services.agent_identity_alias import resolve_agent_id
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO audit_journal (ts_utc,action,module,personnel_id,payload) VALUES (UTC_TIMESTAMP(),'update','personnel','202',%s)",
                        (json.dumps({"changes":{"grade":{"old":"A","new":"B"}}}),))
    preview = merge.preview_personnel_merge(settings, "202", "101")
    merge.apply_personnel_merge(settings, preview, reason="Synthetic")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            assert resolve_agent_id(cur, "202") == 1
            assert _agent_id(cur, "202") == 1
    assert AppConfigRepository(settings).read_personnel_audit_for("101")[0]["changes"]["grade"]["new"] == "B"
    # Même le réimport sous l'ancien id doit réutiliser l'identité conservée.
    from sme_gend.models.liste_generale import ListeGeneraleRow
    monkeypatch.setattr("sme_gend.config.liste_generale_scope.load_sme_context", lambda: type("Context", (), {"mois_actif":"2026-09"})())
    monkeypatch.setattr("sme_gend.config.sme_context.is_mois_ferme", lambda _: False)
    repo = MySQLAgentRepository(settings)
    assert repo.search_current_personnel("202")[0].satellite_join_key() == "101"
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO import_lot (id,sme_periode_id) VALUES (7,1)")
    row = ListeGeneraleRow(**({field.name: None for field in fields(ListeGeneraleRow)} |
        dict(id="202", access_join_id="202", noms="SYNTHETIC ALICE", telephone="obsolete")))
    repo.bulk_import_from_access_rows([row], sme_periode_id=1, import_lot_id=7, mois="2026-01", historical=historical)
    with pytest.raises(ValueError, match="identit"):
        repo.bulk_import_from_access_rows([replace(row, matricule_militaire="OTHER")],
            sme_periode_id=1, import_lot_id=7, mois="2026-01", historical=historical)
    with pytest.raises(ValueError, match="Plusieurs identifiants"):
        repo.bulk_import_from_access_rows([row, replace(row, id="101", access_join_id="101")],
            sme_periode_id=1, import_lot_id=7, mois="2026-01", historical=historical)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT legacy_access_id,noms,telephone FROM agent")
            assert cur.fetchall() == (("101","SYNTHETIC ALICE","target phone"),)
            cur.execute("SELECT agent_id FROM carriere_situation WHERE id=11")
            assert cur.fetchone() == (1,)
            cur.execute("UPDATE carriere_situation SET est_actuelle=0")
    assert repo.search_historical_personnel("202")[0][1].satellite_join_key() == "101"


def test_identity_mismatch_and_duplicate_event_are_blocked(merge_database):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET matricule_militaire='DIFFERENT' WHERE id=1")
            cur.execute("UPDATE agent SET matricule_militaire='SOURCE' WHERE id=2")
            cur.execute("INSERT INTO agent_diplome (id,agent_id,categorie,libelle_libre) VALUES (501,1,'CIVIL','SYNTHETIC DIPLOMA')")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert any("Matricule militaire" in issue for issue in preview.conflicts)
    assert any("possiblement déjà présent" in issue for issue in preview.conflicts)


def test_two_simultaneous_validations_only_merge_once(merge_database):
    settings, connect, params = merge_database
    preview = merge.preview_personnel_merge(settings, "202", "101")
    gate = Barrier(2)
    def execute(_):
        gate.wait(timeout=10)
        try:
            merge.apply_personnel_merge(settings, preview, reason="Synthetic concurrent request")
            return "merged"
        except (ValueError, RuntimeError):
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(execute, range(2))) == ["conflict", "merged"]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_identity_alias")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='personnel_merge'")
            assert cur.fetchone()[0] == 1


def test_chained_merges_keep_all_old_identifiers_pointing_to_final_identity(merge_database):
    settings, connect, params = merge_database
    merge.apply_personnel_merge(settings, merge.preview_personnel_merge(settings, "202", "101"), reason="First synthetic verification")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms,matricule_militaire,matricule_solde,date_naissance) VALUES (3,'303','SYNTHETIC ALICE','MIL','SOLD','1970-01-01')")
    merge.apply_personnel_merge(settings, merge.preview_personnel_merge(settings, "101", "303"), reason="Second synthetic verification")
    from sme_gend.services.agent_identity_alias import resolve_agent_id
    with connect(**params) as conn:
        with conn.cursor() as cur:
            assert resolve_agent_id(cur, "202") == 3
            assert resolve_agent_id(cur, "101") == 3
            cur.execute("SELECT DISTINCT agent_id FROM carriere_situation")
            assert cur.fetchall() == ((3,),)
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='personnel_merge'")
            assert cur.fetchone()[0] == 2


def test_existing_database_alias_migration_is_idempotent(merge_database):
    from sme_gend.services.agent_identity_alias import apply_identity_alias_schema
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE agent_identity_alias")
    apply_identity_alias_schema(settings)
    apply_identity_alias_schema(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT COUNT(*) FROM agent_identity_alias")
            assert cur.fetchone()[0] == 0


def test_birth_date_fill_requires_age_permission_and_is_audited(merge_database, monkeypatch):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET date_naissance='1966-02-08' WHERE id=2")
            cur.execute("UPDATE agent SET date_naissance=NULL WHERE id=1")
    monkeypatch.setattr("sme_gend.services.auth_guard.session_may_edit_age", lambda: False)
    blocked = merge.preview_personnel_merge(settings, "202", "101")
    assert any("permission" in conflict for conflict in blocked.conflicts)
    monkeypatch.setattr("sme_gend.services.auth_guard.session_may_edit_age", lambda: True)
    allowed = merge.preview_personnel_merge(settings, "202", "101")
    assert "date_naissance" in allowed.fills
    merge.apply_personnel_merge(settings, allowed, reason="Synthetic verified birth certificate")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT date_naissance FROM agent WHERE id=1")
            assert str(cur.fetchone()[0]) == "1966-02-08"
            cur.execute("SELECT payload FROM audit_journal WHERE action='update' AND personnel_id='101' ORDER BY id DESC LIMIT 1")
            assert json.loads(cur.fetchone()[0])["changes"]["date_naissance"]["new"] == "1966-02-08"


def test_reimport_audit_reports_persisted_changes_not_obsolete_alias_profile(merge_database, monkeypatch):
    from unittest.mock import MagicMock
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services import liste_generale_import as importer
    settings, connect, params = merge_database
    merge.apply_personnel_merge(settings, merge.preview_personnel_merge(settings, "202", "101"), reason="Verified synthetic identity")
    monkeypatch.setattr("sme_gend.config.liste_generale_scope.load_sme_context", lambda: type("Context", (), {"mois_actif":"2026-09"})())
    monkeypatch.setattr("sme_gend.config.sme_context.is_mois_ferme", lambda _: False)
    MySQLAgentRepository(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO import_lot (id,sme_periode_id) VALUES (8,1)")
    row = ListeGeneraleRow(**({field.name: None for field in fields(ListeGeneraleRow)} |
        dict(id="202", access_join_id="202", noms="SYNTHETIC ALICE", matricule_militaire="MIL", telephone="obsolete", grade="GND")))
    access = MagicMock()
    access.fetch_all.return_value = [row]
    monkeypatch.setattr(importer, "AccessListeGeneraleRepository", lambda _: access)
    with mysql_import_transaction(settings):
        importer.import_liste_generale_access_to_mysql(settings, settings, mois="2026-01",
            sme_periode_id=1, import_lot_id=8, historical=True, record_audit=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM audit_journal WHERE action='update' AND personnel_id='101' ORDER BY id DESC LIMIT 1")
            changes = json.loads(cur.fetchone()[0])["changes"]
            assert "grade" in changes and "telephone" not in changes


def test_same_date_affectation_and_overlapping_missions_are_blocked(merge_database):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_affectation (id,agent_id,date_affectation,structure_code) VALUES (600,2,'2026-01-01','A'),(601,1,'2026-01-01','B')")
            cur.execute("INSERT INTO agent_mission (id,agent_id,date_debut,date_fin) VALUES (700,2,'2026-01-01','2026-01-20'),(701,1,'2026-01-10','2026-01-31')")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert any("même date" in issue for issue in preview.conflicts)
    assert any("événements qui se chevauchent" in issue for issue in preview.conflicts)


@pytest.mark.parametrize("case", ["different_people", "insufficient_evidence"])
def test_shared_matricule_alone_never_authorizes_backend_merge(merge_database, monkeypatch, case):
    from unittest.mock import MagicMock
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if case == "different_people":
                cur.execute("UPDATE agent SET noms='ANOTHER SYNTHETIC PERSON',date_naissance='1971-12-09',matricule_solde='OTHER' WHERE id=2")
            else:
                cur.execute("UPDATE agent SET date_naissance=NULL,matricule_solde=NULL")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert preview.conflicts
    backup = MagicMock()
    monkeypatch.setattr("sme_gend.services.backup_restore_service.create_backup_to_default_dir", backup)
    with pytest.raises(ValueError):
        merge.apply_personnel_merge(settings, preview, reason="Must not bypass identity checks")
    backup.assert_not_called()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_id FROM carriere_situation WHERE id=11")
            assert cur.fetchone() == (2,)


def test_typographical_name_variants_share_same_ui_and_backend_rules(merge_database):
    settings, connect, params = merge_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET noms='ÉXAMPLE NAME' WHERE id=1")
            cur.execute("UPDATE agent SET noms='example-name' WHERE id=2")
    preview = merge.preview_personnel_merge(settings, "202", "101")
    assert not preview.conflicts
    merge.apply_personnel_merge(settings, preview, reason="Same identity verified on synthetic documents")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT noms FROM agent")
            assert cur.fetchall() == (("ÉXAMPLE NAME",),)
