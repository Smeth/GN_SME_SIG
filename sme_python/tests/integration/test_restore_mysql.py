"""MySQL jetable uniquement : refus explicite de tout autre datadir/port."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_pool import invalidate_mysql_pool
from sme_gend.services import backup_restore_service as backup


@pytest.fixture
def isolated_mysql(tmp_path, monkeypatch):
    metadata = os.environ.get("IMPRESSION_MYSQL_TEST_METADATA")
    if not metadata:
        pytest.skip("Instance MySQL jetable non configurée")
    info = json.loads(Path(metadata).read_text(encoding="utf-8-sig"))
    build = Path(__file__).resolve().parents[2] / "build"
    data = Path(info["datadir"]).resolve()
    assert data.is_relative_to(build.resolve())
    assert int(info["port"]) != 3306
    connect = pymysql.connect
    def guarded_connect(**kwargs):
        assert kwargs.get("host") == "127.0.0.1"
        assert int(kwargs.get("port", 0)) == int(info["port"])
        return connect(**kwargs)
    monkeypatch.setattr(pymysql, "connect", guarded_connect)
    params = dict(host="127.0.0.1", port=info["port"], user="root", password="", autocommit=True)
    conn = guarded_connect(**params)
    with conn.cursor() as cur:
        cur.execute("SELECT @@datadir")
        assert Path(cur.fetchone()[0]).resolve() == data
        database = "impression_test_" + uuid4().hex
        cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    settings = Settings(database_path=tmp_path / "unused.mdb", mysql_host="127.0.0.1",
                        mysql_port=info["port"], mysql_user="root", mysql_password="",
                        mysql_database=database)
    from sme_gend.services import mysql_restore_staging
    owned_schemas = []
    def test_schema_names():
        prefix = database + "_" + uuid4().hex[:6]
        pair = (prefix + "_new", prefix + "_old")
        owned_schemas.extend(pair)
        return pair
    monkeypatch.setattr(mysql_restore_staging, "schema_names", test_schema_names)
    try:
        yield settings, guarded_connect, params | {"database": database}
    finally:
        invalidate_mysql_pool(settings)
        # Seulement la base créée par cette fixture, après vérification du serveur.
        with conn.cursor() as cur:
            for owned_schema in owned_schemas:
                assert owned_schema.startswith(database + "_") and len(owned_schema) <= 64
                cur.execute(f"DROP DATABASE IF EXISTS `{owned_schema}`")
            cur.execute(f"DROP DATABASE `{database}`")
        conn.close()


def test_dump_restore_retains_multiline_unicode_and_binary(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params = isolated_mysql
    payload = (1, "Observation;\nDeuxième ligne 'citée' \\ fin", b"\x00\x01\xff")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE agent (id INT PRIMARY KEY, noms TEXT, image_data BLOB) ENGINE=InnoDB")
            cur.execute("INSERT INTO agent VALUES (%s,%s,%s)", payload)
    sql, counts = backup._export_mysql_sql(settings)
    recovery = tmp_path / "safety.sql"
    recovery.write_text(sql, encoding="utf-8")
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *args, **kwargs: recovery)
    backup._execute_restore_sql(settings, backup._split_sql_statements(sql))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM agent")
            assert cur.fetchall() == (payload,)
    assert counts == {"agent": 1}


def test_dml_failure_rolls_back_and_does_not_return_connection_to_pool(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE agent (id INT PRIMARY KEY) ENGINE=InnoDB")
            cur.execute("INSERT INTO agent VALUES (1)")
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *args, **kwargs: tmp_path / "safety.zip")
    with pytest.raises(RuntimeError, match="interrompue"):
        backup._execute_restore_sql(settings, ["SET FOREIGN_KEY_CHECKS = 0;",
                                               "INSERT INTO agent VALUES (2);",
                                               "INSERT INTO agent VALUES (1);"])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM agent ORDER BY id")
            assert cur.fetchall() == ((1,),)
            cur.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
            assert cur.fetchone()[0] == 1


def test_latest_leave_is_selected_per_type(isolated_mysql):
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE agent (id INT PRIMARY KEY, legacy_access_id VARCHAR(20))")
            cur.execute("CREATE TABLE sme_periode (id INT PRIMARY KEY, annee_mois VARCHAR(7))")
            cur.execute("CREATE TABLE agent_conge (id INT PRIMARY KEY, agent_id INT, sme_periode_id INT, "
                        "type_conge_code VARCHAR(20), date_debut DATE, date_fin DATE, quota_jours DECIMAL(5,1), "
                        "observation TEXT, source_code VARCHAR(20))")
            cur.execute("INSERT INTO agent VALUES (1, '7')")
            cur.execute("INSERT INTO sme_periode VALUES (1, '2026-09')")
            cur.execute("INSERT INTO agent_conge VALUES "
                        "(1,1,1,'ADMIN','2026-09-01','2026-09-10',10,'','MANUEL'),"
                        "(2,1,1,'EXPECTATIVE','2026-09-20','2026-09-30',11,'','MANUEL')")
    repo = MySQLNumeriqueRepository.__new__(MySQLNumeriqueRepository)
    repo._export_mois = lambda: '2026-09'
    repo._connect = lambda **kwargs: connect(**params)
    assert repo.load_latest_conges_for_period()["7"].type_conge == "EXPECTATIVE"
    result = repo.load_latest_conges_for_period(by_type=True)
    assert result[("7", "ADMIN")].date_debut == "2026-09-01"
    assert result[("7", "EXPECTATIVE")].date_debut == "2026-09-20"


def test_shipped_schema_initializes_only_configured_database(isolated_mysql):
    from sme_gend.repositories.mysql_common import apply_mysql_schema
    settings, connect, params = isolated_mysql
    apply_mysql_schema(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            assert cur.fetchone()[0] == settings.mysql_database
            cur.execute("SELECT COUNT(*) FROM agent")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name='smegend'")
            assert cur.fetchone() is None


def test_personnel_history_failure_rolls_back_fiche(isolated_mysql, monkeypatch):
    from dataclasses import fields, replace
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services import personnel_service as service_module
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE agent (id INT PRIMARY KEY, obs TEXT) ENGINE=InnoDB")
            cur.execute("INSERT INTO agent VALUES (1,'original')")
    class Repository:
        def update_row(self, row, **kwargs):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE agent SET obs=%s WHERE id=%s", (row.obs, row.id))
    service = service_module.PersonnelService.__new__(service_module.PersonnelService)
    service._settings = settings
    service._repo = Repository()
    service._assert_fonction_si_changee = lambda *args: None
    def history_fails(*args):
        raise RuntimeError("synthetic history failure")
    monkeypatch.setattr(service_module, "sync_dossier_after_personnel_save", history_fails)
    before = ListeGeneraleRow(**{f.name: None for f in fields(ListeGeneraleRow)} | {"id": "1", "obs": "original"})
    with pytest.raises(RuntimeError, match="synthetic history"):
        service.update_personnel(before, replace(before, obs="new"))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT obs FROM agent WHERE id=1")
            assert cur.fetchone()[0] == "original"


def test_two_writers_cannot_overwrite_same_snapshot(isolated_mysql):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    import threading
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params = isolated_mysql
    settings = replace(settings, mysql_view_mois="2026-09")
    repo = MySQLAgentRepository(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms) VALUES (1,'synthetic','SYNTHETIC')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,obs) VALUES (1,1,1,'original')")
    snapshot = repo.fetch_by_id("1")
    gate = threading.Barrier(2)
    def write(value):
        gate.wait(timeout=5)
        try:
            repo.update_row(replace(snapshot, obs=value), expected=snapshot)
            return True
        except ValueError as exc:
            assert "autre poste" in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["first", "second"]))
    assert sorted(results) == [False, True]
    assert repo.fetch_by_id("1").obs in {"first", "second"}


def test_grade_projection_excludes_future_events(isolated_mysql):
    from sme_gend.repositories.mysql_common import apply_mysql_schema
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    settings, connect, params = isolated_mysql
    apply_mysql_schema(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id) VALUES (1,'synthetic')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES "
                        "(1,'2026-05','2026-05-31'),(2,'2026-06','2026-06-30'),(3,'2026-03','2026-03-31')")
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,grade_libelle) VALUES "
                        "(1,1,1,'OLD'),(2,1,2,'OLD'),(3,1,3,'SNAPSHOT')")
            cur.execute("INSERT INTO agent_passation_grade (agent_id,grade_libelle,date_passation) VALUES "
                        "(1,'LTN','2026-05-10'),(1,'CNE','2026-06-10')")
    repo = MySQLAgentDossierRepository.__new__(MySQLAgentDossierRepository)
    repo._settings = settings
    repo._mc = MySQLConnection(settings)
    for career in ("1", "2", "3"):
        repo.sync_carriere_grade_from_passations(1, career)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT grade_libelle FROM carriere_situation ORDER BY id")
            assert cur.fetchall() == (("LTN",), ("CNE",), ("SNAPSHOT",))


def test_config_batch_failure_rolls_back_all_keys(isolated_mysql, monkeypatch):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE app_config (config_key VARCHAR(64) PRIMARY KEY, payload JSON NOT NULL, "
                        "updated_by VARCHAR(128), updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB")
    repo = AppConfigRepository(settings)
    original = repo.set_json
    def fail_second(key, payload, **kwargs):
        if key == "second":
            raise RuntimeError("synthetic interrupted configuration restore")
        original(key, payload, **kwargs)
    monkeypatch.setattr(repo, "set_json", fail_second)
    with pytest.raises(RuntimeError, match="synthetic"):
        repo.import_config_rows([{"config_key": "first", "payload": {}},
                                 {"config_key": "second", "payload": {}}])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app_config")
            assert cur.fetchone()[0] == 0


def _lot7_dossier(isolated_mysql, monkeypatch):
    from dataclasses import fields
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.repositories.mysql_common import apply_mysql_schema
    from sme_gend.services.agent_dossier_service import AgentDossierService
    settings, connect, params = isolated_mysql
    apply_mysql_schema(settings)
    service = AgentDossierService(settings)
    monkeypatch.setattr("sme_gend.services.sensitive_data_policy.assert_may_mutate_dossier_history", lambda **kw: None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent (id,legacy_access_id,noms) VALUES (1,'42','SYNTHETIC'),(2,'43','OTHER SYNTHETIC')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,grade_libelle,position_admin_libelle) VALUES (1,1,1,'MDL','Présent')")
    row = ListeGeneraleRow(**({f.name: None for f in fields(ListeGeneraleRow)} |
                            dict(id="1", noms="SYNTHETIC", grade="MDL", access_join_id="42", position_admin="Présent")))
    return settings, connect, params, service, row


@pytest.mark.parametrize("failure", ["projection", "audit"])
def test_direct_dossier_failure_rolls_back_history(isolated_mysql, monkeypatch, failure):
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic " + failure + " failure")
    monkeypatch.setattr(service, "_project_carriere" if failure == "projection" else "_log_history", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        service.add_diplome(row, categorie="CIVIL", libelle="SYNTHETIC DIPLOMA")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_diplome WHERE agent_id=1")
            assert cur.fetchone()[0] == 0


def test_direct_history_cannot_delete_another_agents_event(isolated_mysql, monkeypatch):
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_passation_grade (id,agent_id,grade_libelle) VALUES (333,2,'SYNTHETIC')")
    with pytest.raises(ValueError, match="agent|dossier"):
        from sme_gend.models.agent_dossier import AgentPassationGradeRow
        service.delete_passation(row,333,expected=AgentPassationGradeRow(333,2,'SYNTHETIC'))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_id FROM agent_passation_grade WHERE id=333")
            assert cur.fetchone() == (2,)


@pytest.mark.parametrize("fail", [False, True])
def test_position_model_register_and_revision_are_atomic(isolated_mysql, monkeypatch, fail):
    from PySide6.QtWidgets import QApplication
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.data_sync import read_data_revision
    from sme_gend.ui.views.edition_sme_view import _EditionModel, _COL_IDX
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    from dataclasses import replace
    settings = replace(settings, mysql_view_mois="2026-09")
    app = QApplication.instance() or QApplication([])
    model = _EditionModel()
    model.load([row], None, [], [], [], [], settings=settings)
    def persist(before, after):
        assert has_import_transaction(settings)
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE carriere_situation SET position_admin_libelle=%s WHERE id=1", (after.position_admin,))
    model._persist_update = persist
    events = AgentDossierEventsService(settings)
    def dialog(*args):
        assert not has_import_transaction(settings)
        def write():
            events.add_conge("42", type_code="ADMIN", date_debut=date(2026, 9, 1), date_fin=None,
                             quota_jours=None, observation="SYNTHETIC", sme_mois="2026-09")
            if fail:
                raise RuntimeError("synthetic failure after register INSERT")
        return write
    from datetime import date
    model.position_change_prompt = dialog
    assert model.setData(model.index(0, _COL_IDX["position_admin"]), "CongéAdmin") == (not fail)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT position_admin_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone()[0] == ("Présent" if fail else "CongéAdmin")
            cur.execute("SELECT COUNT(*) FROM agent_conge WHERE agent_id=1")
            assert cur.fetchone()[0] == (0 if fail else 1)
    assert bool(read_data_revision(settings)) == (not fail)


def test_direct_registry_revision_failure_rolls_back_entry(isolated_mysql, monkeypatch):
    from datetime import date
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    events = AgentDossierEventsService(settings)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic revision failure")
    monkeypatch.setattr("sme_gend.services.data_sync.publish_data_change", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        events.add_conge("42", type_code="ADMIN", date_debut=date(2026, 9, 1), date_fin=None,
                         quota_jours=None, observation="SYNTHETIC", sme_mois="2026-09")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_conge WHERE agent_id=1")
            assert cur.fetchone()[0] == 0


def test_native_nested_initialization_lock_remains_owned_until_outer_exit(isolated_mysql):
    import hashlib
    from sme_gend.repositories.mysql_connection import mysql_database_key, mysql_initialization_lock
    settings, connect, params = isolated_mysql
    identity = ":".join(str(value) for value in mysql_database_key(settings))
    name = "sme_gend_init_" + hashlib.sha256(identity.encode()).hexdigest()[:40]
    with connect(**params) as observer:
        with observer.cursor() as cur:
            with mysql_initialization_lock(settings):
                with mysql_initialization_lock(settings):
                    cur.execute("SELECT GET_LOCK(%s,0)", (name,))
                    assert cur.fetchone() == (0,)
                cur.execute("SELECT GET_LOCK(%s,0)", (name,))
                assert cur.fetchone() == (0,)
            cur.execute("SELECT GET_LOCK(%s,0)", (name,))
            assert cur.fetchone() == (1,)
            cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
            assert cur.fetchone() == (1,)


def test_initialization_really_seeds_distinction_references(isolated_mysql, monkeypatch):
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for table in ("ref_medaille", "ref_decoration"):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                assert cur.fetchone()[0] > 0


def test_direct_history_notifies_other_posts_without_projecting_when_disabled(isolated_mysql, monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    service.add_passation(row, grade_libelle="SYNTHETIC GRADE", sync_fiche=False)
    assert read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT grade_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone() == ("MDL",)
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE agent_id=1")
            assert cur.fetchone() == (1,)


def test_bulk_failure_rolls_back_only_failed_agent_without_compensating_writes(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from sme_gend.models.numerique_satellite import MissionDetail
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    from sme_gend.services import padmin_satellite_apply as prompts
    from sme_gend.ui.padmin_satellite_dialog import SatelliteFormResult
    settings, connect, params, service, first = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation (id,agent_id,sme_periode_id,position_admin_libelle) VALUES (2,2,1,'Présent')")
    second = replace(first, id="2", access_join_id="43")
    updates = []
    class Personnel:
        def update_personnel(self, before, after, **kwargs):
            assert has_import_transaction(settings)
            updates.append(after.id)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE carriere_situation SET position_admin_libelle=%s WHERE id=%s", (after.position_admin, int(after.id)))
    monkeypatch.setattr("sme_gend.services.personnel_service.PersonnelService", lambda config: Personnel())
    form = SatelliteFormResult(kind="mission", mission=MissionDetail("SYNTHETIC", "TEST", "TEST-1", "01/09/2026"))
    monkeypatch.setattr(prompts.BulkMissionStageDialog, "run", lambda *a, **kw: ("mission", form))
    original = prompts._apply_form
    def write(sat, key, payload, **kwargs):
        original(sat, key, payload, **kwargs)
        if key == "43":
            raise RuntimeError("synthetic second-agent failure after INSERT")
    monkeypatch.setattr(prompts, "_apply_form", write)
    count, errors = prompts.bulk_apply_mission_or_stage(None, settings, [first, second])
    assert count == 1 and len(errors) == 1 and "synthetic second-agent" in errors[0]
    assert updates == ["1", "2"]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT position_admin_libelle FROM carriere_situation ORDER BY id")
            assert cur.fetchall() == (("Mission",), ("Présent",))
            cur.execute("SELECT agent_id FROM agent_mission ORDER BY agent_id")
            assert cur.fetchall() == ((1,),)


def test_direct_history_audit_uses_selected_month(isolated_mysql, monkeypatch):
    from dataclasses import replace
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    service._settings = replace(settings, mysql_view_mois="2026-09")
    monkeypatch.setattr("sme_gend.config.sme_context.mois_actif_courant", lambda: "2026-10")
    service.add_passation(row, grade_libelle="SYNTHETIC GRADE", sync_fiche=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sme_mois_context FROM audit_journal WHERE action='dossier_passation_add' ORDER BY id DESC LIMIT 1")
            assert cur.fetchone() == ("2026-09",)


def test_dossier_dialog_cannot_overwrite_an_event_changed_while_open(isolated_mysql, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication, QDialog
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    event_id = service.add_diplome(row, categorie="CIVIL", libelle="INITIAL SYNTHETIC")
    displayed = service.list_diplomes(row)[0]
    app = QApplication.instance() or QApplication([])
    dialog = MagicMock()
    def edit_elsewhere():
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE agent_diplome SET libelle_libre='OTHER POST' WHERE id=%s", (event_id,))
        return QDialog.DialogCode.Accepted
    dialog.exec.side_effect = edit_elsewhere
    dialog.values.return_value = {"categorie": "CIVIL", "libelle": "STALE FORM"}
    monkeypatch.setattr("sme_gend.ui.diplome_edit_dialog.DiplomeEditDialog", lambda *a, **kw: dialog)
    error = MagicMock()
    monkeypatch.setattr("sme_gend.ui.personnel_dossier_dialog.QMessageBox.critical", error)
    view = SimpleNamespace(_tbl_diplomes=SimpleNamespace(currentRow=lambda: 0), _diplome_rows=[displayed],
        _row=row, _dossier_svc=service, _after_carriere_dossier_mutation=MagicMock(),
        _diploma_groups=[SimpleNamespace(row=displayed)], _pick_group_source=lambda group: group.row)
    PersonnelDossierDialog._on_edit_diplome(view)
    assert error.called
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT libelle_libre FROM agent_diplome WHERE id=%s", (event_id,))
            assert cur.fetchone() == ("OTHER POST",)


def test_upsert_mission_does_not_erase_another_month(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from sme_gend.models.numerique_satellite import MissionDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (2,'2026-10','2026-10-31')")
            cur.execute("INSERT INTO agent_mission (agent_id,sme_periode_id,nature_libelle,source_ligne_legacy) VALUES (1,1,'SEPTEMBER','42'),(1,2,'OCTOBER','42')")
    MySQLNumeriqueRepository(settings).upsert_mission("42", MissionDetail("NEW SEPTEMBER", None, None, "01/09/2026"))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nature_libelle FROM agent_mission WHERE agent_id=1 AND sme_periode_id=2")
            assert cur.fetchone() == ("OCTOBER",)



@pytest.mark.parametrize("family", ["diplome", "passation", "affectation", "distinction",
                                    "mission", "stage", "malade", "desertion", "conge"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_all_dossier_histories_refuse_stale_forms(isolated_mysql, monkeypatch, family, operation):
    from datetime import date
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.data_sync import read_data_revision
    settings, connect, params, core, row = _lot7_dossier(isolated_mysql, monkeypatch)
    core_values = {
        "diplome": dict(categorie="CIVIL", libelle="SYNTHETIC"),
        "passation": dict(grade_libelle="MDL", date_passation=date(2026,9,1)),
        "affectation": dict(structure_code="SYNTHETIC", date_affectation=date(2026,9,1)),
    }
    event_values = {
        "mission": dict(date_debut="01/09/2026", date_fin=None, nature="SYNTHETIC", lieu=None,
                        reference_dossier=None, observation=None),
        "stage": dict(type_code="LOCAL", date_debut="01/09/2026", date_fin=None,
                      nature="SYNTHETIC", lieu=None, observation=None),
        "malade": dict(date_debut=date(2026,9,1), date_fin=None, pathologie="SYNTHETIC", observation=None),
        "desertion": dict(date_desertion="01/09/2026", observation=None),
        "conge": dict(type_code="ADMIN", date_debut=date(2026,9,1), date_fin=None,
                      quota_jours=None, observation=None, sme_mois=None),
    }
    tables = {"diplome": ("agent_diplome", "obs"), "passation": ("agent_passation_grade", "obs"),
              "affectation": ("agent_affectation", "obs"), "distinction": ("agent_distinction", "citation"),
              "mission": ("agent_mission", "observation"), "stage": ("parcours_stage", "observation"),
              "malade": ("agent_malade", "observation"), "desertion": ("agent_desertion", "observation"),
              "conge": ("agent_conge", "observation")}
    if family == "distinction":
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ref_medaille ORDER BY id LIMIT 1")
                medaille = cur.fetchone()[0]
        core_values[family] = dict(kind="medaille", ref_medaille_id=medaille)
    if family in core_values:
        service, context, values = core, row, core_values[family]
        reader = lambda: getattr(service, "list_" + {"passation":"passations", "affectation":"affectations", "diplome":"diplomes", "distinction":"distinctions"}[family])(row)
    else:
        service, context, values = AgentDossierEventsService(settings), "42", event_values[family]
        reader = lambda: getattr(service, {"mission":"missions", "stage":"stages", "malade":"malades", "desertion":"desertions", "conge":"conges"}[family])("42")
    getattr(service, "add_"+family)(context, **values)
    shown = reader()[0]
    event_id = getattr(shown, "malade_id" if family == "malade" else "id")
    table, column = tables[family]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET {column}='OTHER POST' WHERE id=%s", (event_id,))
    revision = read_data_revision(settings)
    args = (event_id,) if family == "malade" else (context, event_id)
    kwargs = dict(values) if operation == "update" else {}
    if family == "malade":
        kwargs["owner_legacy_id"] = "42"
    with pytest.raises(ValueError, match="modifié ou supprimé"):
        getattr(service, operation+"_"+family)(*args, expected=shown, **kwargs)
    assert read_data_revision(settings) == revision
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {column} FROM {table} WHERE id=%s", (event_id,))
            assert cur.fetchone() == ("OTHER POST",)


def test_two_posts_editing_the_same_history_have_exactly_one_winner(isolated_mysql, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    event_id = service.add_diplome(row, categorie="CIVIL", libelle="INITIAL SYNTHETIC")
    shown = service.list_diplomes(row)[0]
    gate = threading.Barrier(2)
    def save(value):
        gate.wait(timeout=10)
        try:
            service.update_diplome(row, event_id, categorie="CIVIL", libelle=value, expected=shown)
            return True
        except ValueError as exc:
            assert "modifié ou supprimé" in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["FIRST POST", "SECOND POST"]))
    assert sorted(results) == [False, True]
    assert service.list_diplomes(row)[0].libelle in {"FIRST POST", "SECOND POST"}


def test_projection_sees_other_commits_after_an_earlier_read(isolated_mysql, monkeypatch):
    import threading
    from datetime import date
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    ready, committed = threading.Event(), threading.Event()
    def earlier_read_then_save():
        with _personnel_write(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM agent_diplome WHERE agent_id=1")
                    assert cur.fetchone() == (0,)
            ready.set()
            assert committed.wait(timeout=10)
            service.add_diplome(row, categorie="MILITAIRE", libelle="OLDER EVENT", date_obtention=date(2026,9,1))
    def other_post():
        assert ready.wait(timeout=10)
        try:
            service.add_diplome(row, categorie="MILITAIRE", libelle="LATEST EVENT", date_obtention=date(2026,9,20))
        finally:
            committed.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(earlier_read_then_save), pool.submit(other_post)]
        for task in tasks:
            task.result(timeout=20)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT diplome_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone() == ("LATEST EVENT",)


def test_direct_multi_repository_clear_rolls_back_as_one_write(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.services.data_sync import read_data_revision
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    repo = MySQLNumeriqueRepository(settings)
    for kind in ("LOCAL", "EXTERIEUR"):
        repo.upsert_stage("42", StageDetail(kind, None, None), type_code=kind)
    revision = read_data_revision(settings)
    clear = repo.clear_stage_for_period
    def fail_second(key, kind):
        clear(key, kind)
        if kind == "EXTERIEUR":
            raise RuntimeError("synthetic second-clear failure")
    monkeypatch.setattr(repo, "clear_stage_for_period", fail_second)
    with pytest.raises(RuntimeError, match="second-clear"):
        repo.clear_other_stages_for_period("42", keep_type="INTERNE")
    assert read_data_revision(settings) == revision
    assert {stage.type_stage for stage in repo.list_stages_for_agent("42")} == {"LOCAL", "EXTERIEUR"}


def test_read_committed_setting_does_not_leak_into_next_pooled_transaction(isolated_mysql):
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE synthetic_isolation (id INT PRIMARY KEY, value INT)")
            cur.execute("INSERT INTO synthetic_isolation VALUES (1, 0)")
    def read(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM synthetic_isolation WHERE id=1")
            return cur.fetchone()[0]
    def change(value):
        with connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("UPDATE synthetic_isolation SET value=%s WHERE id=1", (value,))
    with mysql_import_transaction(settings, read_committed=True):
        with MySQLConnection(settings).connect() as conn:
            assert read(conn) == 0
            change(1)
            assert read(conn) == 1
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            assert read(conn) == 1
            change(2)
            assert read(conn) == 1  # Server default RR is restored, not session RC.



def _lot8_monthly(isolated_mysql, monkeypatch, family):
    from dataclasses import replace
    from sme_gend.models.numerique_satellite import MissionDetail, MaladeDetail, DeserteurDetail, StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    repo = MySQLNumeriqueRepository(settings)
    specs = {
        "mission": (MissionDetail("SYNTHETIC", None, None, "01/09/2026"), "agent_mission", "load_missions", {}),
        "malade": (MaladeDetail("01/09/2026", "SYNTHETIC", None), "agent_malade", "load_malades", {}),
        "desertion": (DeserteurDetail("01/09/2026"), "agent_desertion", "load_deserteurs", {}),
        "stage": (StageDetail("SYNTHETIC", None, "01/09/2026"), "parcours_stage", "load_stages_loc", {"type_code":"LOCAL", "date_debut":"01/09/2026"}),
    }
    detail, table, loader, options = specs[family]
    getattr(repo, "upsert_"+family)("42", detail, **options)
    snapshot = getattr(repo, loader)()["42"]
    assert snapshot.write_token is not None
    return settings, connect, params, repo, detail, table, loader, options, snapshot


@pytest.mark.parametrize("family", ["mission", "malade", "desertion", "stage"])
@pytest.mark.parametrize("change", ["edit", "reimport", "create"])
def test_monthly_edit_refuses_stale_or_replaced_records(isolated_mysql, monkeypatch, family, change):
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, family)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if change == "edit":
                cur.execute(f"UPDATE {table} SET observation='OTHER POST' WHERE id=%s", (shown.write_token[0],))
            elif change == "reimport":
                cur.execute(f"DELETE FROM {table} WHERE id=%s", (shown.write_token[0],))
    if change == "reimport":
        getattr(repo, "upsert_"+family)("42", detail, **options)
    expected = None if change == "create" else shown
    with pytest.raises(ValueError, match="autre poste"):
        getattr(repo, "upsert_"+family)("42", detail, expected=expected, **options)
    if change != "create":
        assert getattr(repo, loader)()["42"].write_token != shown.write_token


@pytest.mark.parametrize("family", ["mission", "malade", "desertion", "stage"])
def test_monthly_editor_preserves_row_identity_and_unedited_history_fields(isolated_mysql, monkeypatch, family):
    from dataclasses import replace
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, family)
    if family in {"mission", "malade"}:
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET date_fin='2026-09-20' WHERE id=%s", (shown.write_token[0],))
        shown = getattr(repo, loader)()["42"]
    # Add a second historical entry in the same month. Editing the displayed
    # row must leave this older entry intact instead of deleting the whole set.
    with connect(**params) as conn:
        with conn.cursor() as cur:
            date_column = "date_desertion" if family == "desertion" else "date_debut"
            if family == "stage":
                cur.execute("INSERT INTO parcours_stage (agent_id,sme_periode_id,type_stage_code,date_debut,source_ligne_legacy) VALUES (1,1,'LOCAL','2026-08-31',NULL)")
            else:
                cur.execute(f"INSERT INTO {table} (agent_id,sme_periode_id,{date_column},source_ligne_legacy) VALUES (1,1,'2026-08-31',NULL)")
            other_id = cur.lastrowid
    new_value = {"mission": {"nature":"EDITED"}, "malade":{"pathologie":"EDITED"},
                 "desertion":{"dat_desertion":"02/09/2026"}, "stage":{"nature_stage":"EDITED"}}[family]
    getattr(repo, "upsert_"+family)("42", replace(detail, **new_value), expected=shown, **options)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} WHERE agent_id=1 ORDER BY id")
            assert {record[0] for record in cur.fetchall()} == {shown.write_token[0], other_id}
            if family in {"mission", "malade"}:
                cur.execute(f"SELECT date_fin FROM {table} WHERE id=%s", (shown.write_token[0],))
                from datetime import date
                assert cur.fetchone() == (date(2026,9,20),)


def test_stage_fallback_creates_selected_month_without_modifying_previous_month(isolated_mysql, monkeypatch):
    from dataclasses import replace
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, "stage")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (2,'2026-10','2026-10-31')")
    repo._settings = replace(settings, mysql_view_mois="2026-10")
    fallback = repo.load_stages_loc()["42"]
    assert fallback.write_token == shown.write_token
    repo.upsert_stage("42", replace(detail, nature_stage="OCTOBER"), expected=fallback,
                      type_code="LOCAL", date_debut="01/10/2026")
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sme_periode_id,nature_libelle FROM parcours_stage ORDER BY sme_periode_id")
            assert cur.fetchall() == ((1,"SYNTHETIC"), (2,"OCTOBER"))


@pytest.mark.parametrize("cancel", [False, True])
def test_stage_start_ui_cannot_leave_half_written_position(isolated_mysql, monkeypatch, cancel):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QDialog
    from sme_gend.ui.views import stages_view as ui
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    sat = NumeriqueSatelliteRepository(settings)
    sat.upsert_stage("42", StageDetail("OLD EX", None, None), type_code="EXTERIEUR")
    updates = []
    class Personnel:
        def update_personnel(self, before, after, **kwargs):
            updates.append(after.position_admin)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE carriere_situation SET position_admin_libelle=%s WHERE id=1", (after.position_admin,))
    monkeypatch.setattr("sme_gend.services.personnel_service.PersonnelService", lambda config: Personnel())
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Rejected if cancel else QDialog.DialogCode.Accepted
    dialog.stage_detail.return_value = StageDetail("NEW LOCAL", None, None)
    dialog.stage_dates.return_value = ("01/09/2026", None)
    monkeypatch.setattr(ui, "StageStartDialog", lambda *a, **kw: dialog)
    real = sat.upsert_stage
    def fail_after_insert(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("synthetic stage insert failure")
    monkeypatch.setattr(sat, "upsert_stage", fail_after_insert)
    monkeypatch.setattr(ui.QMessageBox, "information", MagicMock())
    monkeypatch.setattr(ui.QMessageBox, "critical", MagicMock())
    view = SimpleNamespace(_scoped_settings=settings, _sat=sat, _checked={"local":set()}, _view_data=MagicMock(),
        _prepare_stage_rows=lambda rows:{row.id:row}, _details_for=lambda kind:{},
        _agent_label_for_start_dialog=lambda rows:"SYNTHETIC", _confirm_start_stages=lambda *a,**kw:True,
        _invalidate_liste_caches=MagicMock(), _reload_lists_async=MagicMock(), _period_label=lambda:"09/2026")
    ui.StagesView._execute_start_stage(view, "local", [row])
    assert updates == ([] if cancel else ["StageLocal"])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT position_admin_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone() == ("Présent",)
            cur.execute("SELECT type_stage_code,nature_libelle FROM parcours_stage WHERE agent_id=1")
            assert cur.fetchall() == (("EXTERIEUR","OLD EX"),)



@pytest.mark.parametrize("family", ["mission", "malade", "desertion", "stage"])
def test_monthly_parallel_edit_has_one_winner(isolated_mysql, monkeypatch, family):
    import threading
    from dataclasses import replace
    from concurrent.futures import ThreadPoolExecutor
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, family)
    gate = threading.Barrier(2)
    def save(index):
        values = {"mission":{"nature":f"POST {index}"}, "malade":{"pathologie":f"POST {index}"},
                  "desertion":{"dat_desertion":f"0{index+2}/09/2026"}, "stage":{"nature_stage":f"POST {index}"}}[family]
        gate.wait(timeout=10)
        try:
            getattr(repo, "upsert_"+family)("42", replace(detail, **values), expected=shown, **options)
            return True
        except ValueError as exc:
            assert "autre poste" in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [0,1]))
    assert sorted(results) == [False, True]
    assert getattr(repo, loader)()["42"].write_token[0] == shown.write_token[0]


def test_monthly_snapshot_checks_current_rows_inside_outer_repeatable_read(isolated_mysql, monkeypatch):
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, "mission")
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM agent_mission")
                assert cur.fetchone() == (1,)
        with connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("UPDATE agent_mission SET observation='OTHER POST' WHERE id=%s", (shown.write_token[0],))
        with pytest.raises(ValueError, match="autre poste"):
            repo.upsert_mission("42", detail, expected=shown)



@pytest.mark.parametrize("cancel", [False, True])
def test_mission_picker_cancel_or_satellite_failure_keeps_position_unchanged(isolated_mysql, monkeypatch, cancel):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QDialog
    from sme_gend.ui.views import missions_view as ui
    from sme_gend.services import padmin_satellite_apply as prompts
    from sme_gend.models.numerique_satellite import MissionDetail
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    picker = MagicMock()
    picker.DialogCode = QDialog.DialogCode
    picker.exec.return_value = QDialog.DialogCode.Accepted
    picker.selected_row.return_value = row
    monkeypatch.setattr("sme_gend.ui.agent_pick_dialog.AgentPickDialog", lambda *a, **kw: picker)
    monkeypatch.setattr(ui, "confirm_yes_no", lambda *a, **kw: True)
    writes = []
    class Personnel:
        def update_personnel(self, before, after, **kwargs):
            writes.append(after.position_admin)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE carriere_situation SET position_admin_libelle=%s WHERE id=1", (after.position_admin,))
    monkeypatch.setattr("sme_gend.services.personnel_service.PersonnelService", lambda config: Personnel())
    def failed_write():
        NumeriqueSatelliteRepository(settings).upsert_mission("42", MissionDetail("SYNTHETIC", None, None, "01/09/2026"), expected=None)
        raise RuntimeError("synthetic mission insert failure")
    def prepare(*args, **kwargs):
        assert kwargs["prepare_only"] is True
        assert writes == []
        return False if cancel else failed_write
    monkeypatch.setattr(prompts, "prompt_satellite_for_row", prepare)
    error = MagicMock()
    monkeypatch.setattr(ui.QMessageBox, "critical", error)
    view = SimpleNamespace(_check_can_edit=lambda:True, _scoped_settings=settings, _liste=SimpleNamespace(fetch_all=lambda:[row]))
    ui.MissionsView._on_add_from_effectif(view)
    assert writes == ([] if cancel else ["Mission"])
    assert error.called is (not cancel)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT position_admin_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone() == ("Présent",)
            cur.execute("SELECT COUNT(*) FROM agent_mission WHERE agent_id=1")
            assert cur.fetchone() == (0,)



@pytest.mark.parametrize("mode", ["success", "rollback", "commit_failure", "mirror_failure"])
def test_local_mirror_follows_the_real_database_commit(isolated_mysql, monkeypatch, tmp_path, mode):
    import json
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction, after_mysql_commit, CommittedMirrorError
    from sme_gend.services.local_audit_mirror import append_json_mirror
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE synthetic_commit (id INT PRIMARY KEY) ENGINE=InnoDB")
    target = tmp_path / "audit.jsonl"
    callbacks = []
    def execute():
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as shared:
                with shared.cursor() as cur:
                    cur.execute("INSERT INTO synthetic_commit VALUES (1)")
                append_json_mirror(target, [{"id":1, "text":"SYNTHETIC"}])
                with mysql_import_transaction(settings):
                    after_mysql_commit(lambda: callbacks.append("nested"))
                assert not target.exists()
                assert callbacks == []
                if mode == "rollback":
                    raise RuntimeError("synthetic rollback")
                if mode == "commit_failure":
                    def fail(): raise RuntimeError("synthetic commit failure")
                    monkeypatch.setattr(shared._connection, "commit", fail)
                if mode == "mirror_failure":
                    # The commit is valid; one failed mirror must neither undo
                    # SQL nor prevent the remaining after-commit callbacks.
                    target.mkdir()
                    after_mysql_commit(lambda: callbacks.append("last"))
    if mode in {"rollback", "commit_failure"}:
        with pytest.raises(RuntimeError, match="synthetic"):
            execute()
    elif mode == "mirror_failure":
        with pytest.raises(CommittedMirrorError, match="enregistrées dans MySQL") as error:
            execute()
        assert error.value.committed
    else:
        execute()
    committed = mode in {"success", "mirror_failure"}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM synthetic_commit")
            assert cur.fetchone() == (int(committed),)
    if mode == "success":
        assert json.loads(target.read_text(encoding="utf-8")) == {"id":1,"text":"SYNTHETIC"}
        assert callbacks == ["nested"]
    elif mode == "mirror_failure":
        assert callbacks == ["nested", "last"]
    else:
        assert not target.exists()
        assert callbacks == []


def test_batch_audit_failure_aborts_outer_import_and_produces_no_local_success(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction
    from sme_gend.services import audit_log
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE synthetic_import (id INT PRIMARY KEY) ENGINE=InnoDB")
    def fail(entries):
        raise RuntimeError("synthetic audit unavailable")
    monkeypatch.setattr(audit_log, "_persist_personnel_audit_batch_mysql", fail)
    monkeypatch.setattr(audit_log, "_audit_file_path", lambda *a: tmp_path / "audit.jsonl")
    with pytest.raises(RuntimeError, match="Importation annulée"):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO synthetic_import VALUES (1)")
            audit_log.append_personnel_audit_batch([dict(action="update", personnel_id="42", changes={"obs":{"old":None,"new":"SYNTHETIC"}})])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM synthetic_import")
            assert cur.fetchone() == (0,)
    assert not (tmp_path / "audit.jsonl").exists()



@pytest.mark.parametrize("family", ["mission", "malade", "desertion", "stage"])
def test_missing_selected_period_never_falls_back_to_global_rows(isolated_mysql, monkeypatch, family):
    from dataclasses import replace
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, family)
    repo._settings = replace(settings, mysql_view_mois="2026-12")
    with pytest.raises(ValueError, match="période demandée n'existe pas"):
        getattr(repo, "upsert_"+family)("42", detail, **options)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id,sme_periode_id FROM {table} WHERE agent_id=1")
            assert cur.fetchall() == ((shown.write_token[0],1),)


def test_monthly_delete_still_protects_manually_entered_dossier_maladies(isolated_mysql, monkeypatch):
    from datetime import date
    settings, connect, params, repo, detail, table, loader, options, shown = _lot8_monthly(isolated_mysql, monkeypatch, "malade")
    repo.add_malade_row("42", date_debut=date(2026,10,1), date_fin=None, pathologie="MANUAL DOSSIER", observation=None)
    monkeypatch.setattr(repo, "_export_mois", lambda:None)
    monkeypatch.setattr(repo, "_periode_id", lambda cur:None)
    manual = repo.load_malades()["42"]
    assert manual.write_token[1] is None
    with pytest.raises(ValueError, match="dossier personnel"):
        repo.delete_malade("42", expected=manual)
    assert repo.load_malades()["42"].write_token == manual.write_token



def test_creation_allocates_a_distinct_internal_key_and_preserves_access_agents(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, dossier, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    service = PersonnelService(settings)
    new_row = replace(row, id="0", noms="NEW SYNTHETIC", access_join_id=None)
    new_id = service.create_personnel(new_row)
    saved = service.get_by_id(new_id)
    assert saved is not None
    assert saved.access_join_id.startswith("GEN-APP-")
    assert saved.satellite_join_key() != saved.id
    second_id = service.create_personnel(replace(new_row, noms="SECOND SYNTHETIC"))
    assert service.get_by_id(second_id).satellite_join_key() != saved.satellite_join_key()
    AgentDossierEventsService(settings).add_mission(saved.satellite_join_key(), date_debut="01/09/2026",
        date_fin=None, nature="NEW AGENT MISSION", lieu=None, reference_dossier=None, observation=None)
    assert dossier._repo.list_diplomes(1) == []
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,legacy_access_id FROM agent WHERE id IN (1,2) ORDER BY id")
            assert cur.fetchall() == ((1,"42"),(2,"43"))
            cur.execute("SELECT a.legacy_access_id FROM agent_mission m JOIN agent a ON a.id=m.agent_id WHERE m.nature_libelle='NEW AGENT MISSION'")
            assert cur.fetchone() == (saved.satellite_join_key(),)
            cur.execute("SELECT personnel_id FROM audit_journal WHERE action='create' ORDER BY id LIMIT 1")
            assert cur.fetchone() == (saved.satellite_join_key(),)


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_full_form_personnel_and_satellite_commit_or_rollback_together(isolated_mysql, monkeypatch, create, fail):
    from dataclasses import replace
    from types import SimpleNamespace
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.personnel_form_service import save_personnel_form
    from sme_gend.models.numerique_satellite import MissionDetail
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    settings, connect, params, dossier, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    service = PersonnelService(settings)
    before = service.get_by_id(row.id)
    after = replace(before, position_admin="Mission")
    if create:
        after = replace(after, id="0", access_join_id=None, noms="NEW FORM SYNTHETIC")
    observed = []
    def prepared(saved):
        observed.append(saved)
        assert saved.access_join_id
        assert saved.id != "0"
        NumeriqueSatelliteRepository(settings).upsert_mission(saved.satellite_join_key(),
            MissionDetail("FORM SYNTHETIC",None,None,"01/09/2026"), expected=None)
        if fail:
            raise RuntimeError("synthetic deferred form failure")
    form = SimpleNamespace(result_row=lambda:after, apply_prepared_writes=prepared)
    if fail:
        with pytest.raises(RuntimeError, match="deferred form"):
            save_personnel_form(service, form, before=None if create else before)
    else:
        result = save_personnel_form(service, form, before=None if create else before)
        assert result.id == observed[0].id
    assert len(observed) == 1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_mission WHERE nature_libelle='FORM SYNTHETIC'")
            assert cur.fetchone() == (int(not fail),)
            cur.execute("SELECT COUNT(*) FROM agent")
            assert cur.fetchone() == (2 + int(create and not fail),)
            cur.execute("SELECT position_admin_libelle FROM carriere_situation WHERE id=1")
            expected = "Présent" if create or fail else "Mission"
            assert cur.fetchone() == (expected,)



def test_full_form_catalog_failure_rolls_back_personnel_and_reference_insert(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.personnel_form_service import save_personnel_form
    from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog
    settings, connect, params, dossier, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    service = PersonnelService(settings)
    before = service.get_by_id(row.id)
    after = replace(before, fonction="SYNTHETIC FUNCTION", structure="SYNTHETIC UNIT")
    monkeypatch.setattr("sme_gend.services.fonction_structure_policy.structure_fonctions_non_liees", lambda *a:False)
    def catalog(*args):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ref_fonction (libelle) VALUES ('SYNTHETIC CATALOG MARKER')")
        raise RuntimeError("synthetic catalog failure")
    prepared = SimpleNamespace(_settings=settings, _ref=SimpleNamespace(fonction_ensure_catalog_and_link=catalog), _pending_position_write=MagicMock())
    form = SimpleNamespace(result_row=lambda:after,
        apply_prepared_writes=lambda saved:PersonnelEditDialog.apply_prepared_writes(prepared, saved))
    with pytest.raises(RuntimeError, match="catalog failure"):
        save_personnel_form(service, form, before=before)
    prepared._pending_position_write.assert_not_called()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ref_fonction WHERE libelle='SYNTHETIC CATALOG MARKER'")
            assert cur.fetchone() == (0,)
    assert service.get_by_id(row.id) == before



@pytest.mark.parametrize("shared", [False, True])
def test_bulk_form_cannot_overwrite_fiche_changed_during_the_dialog(isolated_mysql, monkeypatch, shared):
    from dataclasses import replace
    from sme_gend.services import padmin_satellite_apply as prompts
    from sme_gend.models.numerique_satellite import MissionDetail
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    from sme_gend.ui.padmin_satellite_dialog import SatelliteFormResult
    from sme_gend.services.personnel_service import _personnel_write
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    sat = NumeriqueSatelliteRepository(settings)
    form = SatelliteFormResult(kind="mission", mission=MissionDetail("STALE FORM",None,None,"01/09/2026"))
    def dialog(*args, **kwargs):
        sat.upsert_mission("42", MissionDetail("OTHER POST",None,None,"01/09/2026"))
        return form if shared else ("mission", form)
    if shared:
        monkeypatch.setattr(prompts.PadminSatelliteDialog, "run", dialog)
        prepared = prompts.prompt_satellite_bulk_shared(None, settings, [row], new_padmin="Mission", prepare_only=True)
        assert callable(prepared)
        with pytest.raises(ValueError, match="autre poste"):
            with _personnel_write(settings):
                prepared(row)
    else:
        monkeypatch.setattr(prompts.BulkMissionStageDialog, "run", dialog)
        count, errors = prompts.bulk_apply_mission_or_stage(None, settings, [row])
        assert count == 0 and "autre poste" in errors[0]
    assert sat.load_missions()["42"].nature == "OTHER POST"
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT position_admin_libelle FROM carriere_situation WHERE id=1")
            assert cur.fetchone() == ("Présent",)



@pytest.mark.parametrize("parallel", [False, True])
def test_two_conge_additions_never_overwrite_an_existing_manual_history(isolated_mysql, monkeypatch, parallel):
    from datetime import date
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, dossier, row = _lot7_dossier(isolated_mysql, monkeypatch)
    service = AgentDossierEventsService(settings)
    gate = threading.Barrier(2) if parallel else None
    def add(observation):
        if gate:
            gate.wait(timeout=10)
        try:
            service.add_conge("42", type_code="ADMIN", date_debut=date(2026,9,1), date_fin=None,
                quota_jours=None, observation=observation, sme_mois="2026-09")
            return True
        except ValueError as exc:
            assert "existe déjà" in str(exc)
            return False
    if parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, ["FIRST POST", "SECOND POST"]))
    else:
        results = list(map(add, ["FIRST POST", "SECOND POST"]))
    assert sorted(results) == [False, True]
    conges = service.conges("42")
    assert len(conges) == 1
    assert conges[0].observation in {"FIRST POST", "SECOND POST"} if parallel else conges[0].observation == "FIRST POST"



def test_access_reimport_reuses_internal_agent_even_without_matricule(isolated_mysql, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from contextlib import contextmanager
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.repositories.liste_generale_access_repository import AccessListeGeneraleRepository
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, dossier, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois="2026-09")
    personnel = PersonnelService(settings)
    created = personnel.create_personnel(replace(row,id="0",access_join_id=None,noms="INTERNAL ROUNDTRIP",matricule_militaire=None))
    saved = personnel.get_by_id(created)
    fields = ["noms","date_naissance","lieu_naissance","grade","date_grade","date_service","ndecision", "matricule_militaire","matricule_solde","position_admin","fonction","subdivision_arme","diplome","date_obtention","structure","obs","ordre_tri","presseance"]
    raw = (100, *(getattr(saved,name) for name in fields))
    class Cursor:
        def execute(self, sql, *args): self.sql=sql;return self
        def fetchall(self): return [("100",saved.access_join_id)] if "SGI_AgentIdentity" in self.sql else [raw]
        def tables(self, **kwargs): return [SimpleNamespace(table_name="SGI_AgentIdentity",table_type="TABLE")]
        def columns(self, **kwargs): return []
        def close(self): pass
    class Conn:
        def cursor(self): return Cursor()
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def close(self): pass
    access = AccessListeGeneraleRepository.__new__(AccessListeGeneraleRepository)
    access._conn_factory = SimpleNamespace(connect=Conn)
    imported = access.fetch_all()
    inserted, updated, stats = MySQLAgentRepository(settings).bulk_import_from_access_rows(imported,
        sme_periode_id=1, import_lot_id=None, mois="2026-09")
    assert inserted == 0, "An exported internal agent must not become a new numeric Access agent"
    assert updated == 1
    assert imported[0].id == saved.access_join_id
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent")
            assert cur.fetchone() == (3,)
            cur.execute("SELECT legacy_access_id FROM agent WHERE id IN (1,2) ORDER BY id")
            assert cur.fetchall() == (("42",),("43",))



def test_closure_python_backup_restores_binary_unicode_zero_ids_and_consistent_tables(isolated_mysql,monkeypatch,tmp_path):
    from decimal import Decimal
    from datetime import datetime
    from sme_gend.services import mysql_backup_service as closure_backup
    settings,connect,params=isolated_mysql
    payload=(0,"OLD-A", "é\n'\\;",b"\x00\xff",b"",Decimal("123456.789012"),datetime(2026,9,11,12,34,56))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO'")
            cur.execute("CREATE TABLE a_backup (id INT AUTO_INCREMENT PRIMARY KEY, marker VARCHAR(20),txt TEXT,bin BLOB,empty_bin BLOB,num DECIMAL(18,6),stamp DATETIME) ENGINE=InnoDB")
            cur.execute("CREATE TABLE b_backup (id INT PRIMARY KEY,marker VARCHAR(20)) ENGINE=InnoDB")
            cur.execute("INSERT INTO a_backup VALUES (%s,%s,%s,%s,%s,%s,%s)",payload)
            cur.execute("INSERT INTO b_backup VALUES (1,'OLD-B')")
    original=closure_backup._literal
    changed=[]
    def update_between_tables(value):
        if value=="OLD-A" and not changed:
            with connect(**params) as other:
                with other.cursor() as cur:
                    cur.execute("UPDATE a_backup SET marker='NEW-A'")
                    cur.execute("UPDATE b_backup SET marker='NEW-B'")
            changed.append(True)
        return original(value)
    monkeypatch.setattr(closure_backup,"_literal",update_between_tables)
    dump=tmp_path/"closure.sql"
    closure_backup._python_sql_dump(settings,dump)
    assert changed
    statements=backup._split_sql_statements(dump.read_text(encoding="utf-8"))
    from sme_gend.services.backup_sql import validate_restore_statement
    for statement in statements:validate_restore_statement(statement)
    safety=tmp_path/"safety.sql";safety.write_bytes(b"TEST SAFETY PREREQUISITE")
    monkeypatch.setattr(backup,"ensure_recent_backup",lambda *a,**kw:safety)
    backup._execute_restore_sql(settings,statements)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM a_backup");assert cur.fetchone()==payload
            cur.execute("SELECT marker FROM b_backup");assert cur.fetchone()==("OLD-B",)


def test_internal_stage_identity_is_not_truncated_into_access_provenance(isolated_mysql,monkeypatch):
    from dataclasses import replace
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.models.numerique_satellite import StageDetail
    settings,connect,params,dossier,row=_lot7_dossier(isolated_mysql,monkeypatch)
    settings=replace(settings,mysql_view_mois="2026-09")
    personnel=PersonnelService(settings)
    created=personnel.create_personnel(replace(row,id="0",access_join_id=None,noms="INTERNAL STAGE"))
    key=personnel.get_by_id(created).satellite_join_key()
    assert len(key)>32
    sat=NumeriqueSatelliteRepository(settings)
    sat.upsert_stage(key,StageDetail("SYNTHETIC",None,None),type_code="LOCAL")
    assert sat.load_stages_loc()[key].nature_stage=="SYNTHETIC"
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT p.source_ligne_legacy,a.legacy_access_id FROM parcours_stage p JOIN agent a ON a.id=p.agent_id")
            assert cur.fetchone()==(None,key)



@pytest.mark.parametrize("unsupported",["view","myisam"])
def test_closure_backup_refuses_unsafe_fallback_without_publishing_sql(isolated_mysql,monkeypatch,tmp_path,unsupported):
    from sme_gend.services import mysql_backup_service as closure_backup
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            engine="MyISAM" if unsupported=="myisam" else "InnoDB"
            cur.execute(f"CREATE TABLE backup_guard (id INT PRIMARY KEY) ENGINE={engine}")
            cur.execute("INSERT INTO backup_guard VALUES (1)")
            if unsupported=="view":cur.execute("CREATE VIEW backup_view AS SELECT id FROM backup_guard")
    monkeypatch.setattr(closure_backup,"_find_mysqldump",lambda:None)
    result,warnings=closure_backup.backup_mysql_database(settings,tmp_path,"2026-09")
    assert result is None and any("échouée" in warning for warning in warnings)
    assert not list(tmp_path.glob("*.sql")) and not list(tmp_path.glob(".sme-*"))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM backup_guard");assert cur.fetchall()==((1,),)



def test_failed_ddl_restore_keeps_the_entire_original_database(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE restore_original (id INT PRIMARY KEY,marker VARCHAR(30)) ENGINE=InnoDB")
            cur.execute("INSERT INTO restore_original VALUES (7,'ORIGINAL')")
    safety=tmp_path/"safety.sql";safety.write_bytes(b"SYNTHETIC SAFETY")
    monkeypatch.setattr(backup,"ensure_recent_backup",lambda *a,**kw:safety)
    statements=["DROP TABLE IF EXISTS restore_original;",
        "CREATE TABLE restore_original (id INT PRIMARY KEY,marker VARCHAR(30)) ENGINE=InnoDB;",
        "INSERT INTO restore_original VALUES (1,'ARCHIVE'),(1,'DUPLICATE');"]
    with pytest.raises(RuntimeError):backup._execute_restore_sql(settings,statements)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restore_original")
            assert cur.fetchall()==((7,"ORIGINAL"),),"DDL failure must not erase the live table"


def test_restore_rejects_orphan_rows_before_publishing_any_table(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE restore_parent (id INT PRIMARY KEY) ENGINE=InnoDB")
            cur.execute("CREATE TABLE restore_child (id INT PRIMARY KEY,parent_id INT,CONSTRAINT restore_fk FOREIGN KEY(parent_id) REFERENCES restore_parent(id)) ENGINE=InnoDB")
            cur.execute("INSERT INTO restore_parent VALUES (7)")
            cur.execute("INSERT INTO restore_child VALUES (1,7)")
    safety=tmp_path/"safety.sql";safety.write_bytes(b"SYNTHETIC SAFETY")
    monkeypatch.setattr(backup,"ensure_recent_backup",lambda *a,**kw:safety)
    statements=["SET FOREIGN_KEY_CHECKS=0;","DROP TABLE IF EXISTS restore_child;","DROP TABLE IF EXISTS restore_parent;",
        "CREATE TABLE restore_parent (id INT PRIMARY KEY) ENGINE=InnoDB;",
        "CREATE TABLE restore_child (id INT PRIMARY KEY,parent_id INT,CONSTRAINT restore_fk FOREIGN KEY(parent_id) REFERENCES restore_parent(id)) ENGINE=InnoDB;",
        "INSERT INTO restore_child VALUES (1,999);","SET FOREIGN_KEY_CHECKS=1;"]
    with pytest.raises(RuntimeError):backup._execute_restore_sql(settings,statements)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restore_child");assert cur.fetchall()==((1,7),)



@pytest.mark.parametrize("fail_files",[False,True])
def test_prepared_full_restore_preserves_accounts_dependencies_and_rolls_back_file_failure(isolated_mysql,monkeypatch,tmp_path,fail_files):
    from zipfile import ZipFile
    from dataclasses import replace
    from sme_gend.services import restore_files
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE restore_parent (id INT PRIMARY KEY) ENGINE=InnoDB")
            cur.execute("CREATE TABLE restore_child (id INT PRIMARY KEY,parent_id INT,CONSTRAINT restore_fk FOREIGN KEY(parent_id) REFERENCES restore_parent(id)) ENGINE=InnoDB")
            cur.execute("CREATE TABLE untouched (id INT PRIMARY KEY,stamp DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB")
            cur.execute("INSERT INTO untouched VALUES (1,'2001-01-02 03:04:05')")
            cur.execute("CREATE TABLE app_config (config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(100),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)) ENGINE=InnoDB")
            cur.execute("INSERT INTO app_config(config_key,payload) VALUES ('auth_config',%s)",(json.dumps({'users':[{'username':'ARCHIVED'}]}),))
            cur.execute("INSERT INTO restore_parent VALUES (7)")
            cur.execute("INSERT INTO restore_child VALUES (1,7)")
    sql,counts=backup._export_mysql_sql(settings,tables=['restore_parent','restore_child','app_config'])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO restore_parent VALUES (8)")
            cur.execute("UPDATE restore_child SET parent_id=8")
            cur.execute("DELETE FROM restore_parent WHERE id=7")
            cur.execute("UPDATE app_config SET payload=%s WHERE config_key='auth_config'",(json.dumps({'users':[{'username':'CURRENT'}]}),))
    archive=tmp_path/'full.zip'
    manifest=backup.BackupManifest(4,'2026-10-30','synthetic','invalid.test',counts,[])
    with ZipFile(archive,'w') as zf:
        zf.writestr(backup.MANIFEST_NAME,json.dumps(manifest.to_dict()))
        zf.writestr(backup.SQL_DUMP_NAME,sql)
        zf.writestr(backup.APP_CONFIG_DUMP_NAME,json.dumps([{'config_key':'auth_config','payload':{'users':[{'username':'ARCHIVED'}]}},{'config_key':'business','payload':{'value':'NEW'}}]))
        zf.writestr('personnel_media/photos/42.png',b'NEW PHOTO')
        zf.writestr('personnel_media/photos/43.png',b'OTHER PHOTO')
    root=tmp_path/'media';(root/'photos').mkdir(parents=True)
    photo=root/'photos/42.png';photo.write_bytes(b'OLD PHOTO')
    monkeypatch.setattr(backup,'resolved_personnel_media_root',lambda:root)
    monkeypatch.setattr(backup,'default_backup_directory',lambda:tmp_path/'backups')
    safety=tmp_path/'safety.sql';safety.write_bytes(b'SYNTHETIC BACKUP PREREQUISITE')
    monkeypatch.setattr(backup,'ensure_recent_backup',lambda *a,**kw:safety)
    if fail_files:
        original_copy=restore_files.shutil.copyfile
        def fail_second(source,destination,*args,**kwargs):
            if str(source).endswith('1.new'):raise OSError('synthetic interrupted photo publish')
            return original_copy(source,destination,*args,**kwargs)
        monkeypatch.setattr(restore_files.shutil,'copyfile',fail_second)
        with pytest.raises(RuntimeError,match='interrompue'):
            backup.restore_full_backup(settings,archive,restore_user_config=False,restore_app_data=False,maintenance_confirmed=True)
    else:
        backup.restore_full_backup(settings,archive,restore_user_config=False,restore_app_data=False,maintenance_confirmed=True)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT parent_id FROM restore_child");assert cur.fetchone()==((8,) if fail_files else (7,))
            cur.execute("SELECT payload FROM app_config WHERE config_key='auth_config'")
            assert json.loads(cur.fetchone()[0])=={'users':[{'username':'CURRENT'}]}
            cur.execute("SELECT stamp FROM untouched");assert str(cur.fetchone()[0])=='2001-01-02 03:04:05'
            cur.execute("SELECT REFERENCED_TABLE_SCHEMA FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND TABLE_NAME='restore_child' AND REFERENCED_TABLE_NAME IS NOT NULL",(params['database'],))
            assert cur.fetchone()==(params['database'],)
            cur.execute("SELECT COUNT(*) FROM app_config WHERE config_key='business'");assert cur.fetchone()==((0,) if fail_files else (1,))
    assert photo.read_bytes()==(b'OLD PHOTO' if fail_files else b'NEW PHOTO')
    assert (root/'photos/43.png').exists() is (not fail_files)
    recovery=list((tmp_path/'backups/restore_sessions').glob('*/mysql_recovery.json'))
    assert len(recovery)==1
    assert json.loads(recovery[0].read_text())['phase']==('rolled_back' if fail_files else 'completed')



@pytest.mark.parametrize('failure',['lost_acknowledgement','rename_rejected'])
def test_restore_switch_failure_preserves_all_original_tables(isolated_mysql,monkeypatch,tmp_path,failure):
    from sme_gend.services.mysql_restore_staging import PreparedRestore
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE restore_switch (id INT PRIMARY KEY) ENGINE=InnoDB')
            cur.execute('INSERT INTO restore_switch VALUES (7)')
    safety=tmp_path/'safety';safety.write_bytes(b'SYNTHETIC SAFETY')
    monkeypatch.setattr(backup,'ensure_recent_backup',lambda *a,**kw:safety)
    original=PreparedRestore.publish
    def fail(self):
        if failure=='lost_acknowledgement':
            self.uncertain=True
            with self.conn.cursor() as cur:cur.execute(self.rename_sql())
            self.conn.close()
            raise pymysql.OperationalError(2013,'synthetic lost acknowledgement')
        with self.conn.cursor() as cur:cur.execute(f'CREATE TABLE `{self.old}`.restore_switch (id INT PRIMARY KEY) ENGINE=InnoDB')
        return original(self)
    monkeypatch.setattr(PreparedRestore,'publish',fail)
    statements=['DROP TABLE IF EXISTS restore_switch;','CREATE TABLE restore_switch (id INT PRIMARY KEY) ENGINE=InnoDB;','INSERT INTO restore_switch VALUES (1);']
    with pytest.raises(RuntimeError,match='interrompue'):backup._execute_restore_sql(settings,statements)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM restore_switch');assert cur.fetchall()==((7,),)


def test_entire_application_schema_backup_and_restore_is_prepared(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,dossier,row=_lot7_dossier(isolated_mysql,monkeypatch)
    destination=tmp_path/'full_real_schema.zip'
    scope=backup.BackupScope(include_user_config=False,include_app_data=False,include_app_config=False,
        include_media_photos=False,include_media_grades=False)
    manifest=backup.create_backup(settings,destination,scope=scope)
    assert len(manifest.table_counts)>30
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE agent SET noms='SYNTHETIC ALTERED' WHERE id=1")
    monkeypatch.setattr(backup,'ensure_recent_backup',lambda *a,**kw:destination)
    backup.restore_full_backup(settings,destination,restore_user_config=False,restore_app_data=False,
        restore_personnel_media=False,preserve_local_accounts=False,maintenance_confirmed=True)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT noms FROM agent WHERE id=1');assert cur.fetchone()[0]!='SYNTHETIC ALTERED'
            cur.execute('SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_SCHEMA IS NOT NULL AND REFERENCED_TABLE_SCHEMA<>%s',(params['database'],params['database']))
            assert cur.fetchone()==(0,)



def test_second_restore_is_refused_while_the_first_is_preparing(isolated_mysql,tmp_path):
    from sme_gend.services.mysql_restore_staging import prepared_restore
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE restore_lock (id INT PRIMARY KEY) ENGINE=InnoDB')
            cur.execute('INSERT INTO restore_lock VALUES (7)')
    statements=['DROP TABLE IF EXISTS restore_lock;','CREATE TABLE restore_lock (id INT PRIMARY KEY) ENGINE=InnoDB;','INSERT INTO restore_lock VALUES (1);']
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    with prepared_restore(settings,statements,first):
        with pytest.raises(RuntimeError,match='autre.*restauration'):
            with prepared_restore(settings,statements,second):
                raise AssertionError('Second restore acquired the same lock')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM restore_lock');assert cur.fetchall()==((7,),)



def _lot11_closure(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import sme_month_closure as closure,mysql_backup_service
    settings,connect,params,dossier,row=_lot7_dossier(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-10','2026-10-31','Fermé'),(3,'2026-08','2026-08-31','Réouvert')")
            cur.execute("UPDATE carriere_situation SET est_actuelle=1")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,est_actuelle) VALUES (2,1,2,0)")
    backup_file=tmp_path/'safety.sql';backup_file.write_bytes(b'SYNTHETIC BACKUP PREREQUISITE')
    monkeypatch.setattr(mysql_backup_service,'backup_mysql_database',lambda *a:(backup_file,[]))
    monkeypatch.setattr(closure,'sme_archives_dir',lambda:tmp_path/'archives')
    monkeypatch.setattr(closure,'_snapshot_layout_from_central',lambda *a:None)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    return settings,connect,params,closure


def test_month_closure_activation_failure_cannot_commit_statuses(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    def fail(*a,**kw):raise RuntimeError('synthetic activation interruption')
    monkeypatch.setattr(SmeWorkspaceService,'close_month_transition',fail)
    with pytest.raises(RuntimeError,match='activation|clôture'):
        closure.run_piste_a_month_closure(settings,'2026-09','2026-10')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode WHERE id=1');assert cur.fetchone()==('Ouvert',)
            cur.execute('SELECT est_actuelle FROM carriere_situation WHERE id=1');assert cur.fetchone()==(1,)


def test_month_closure_audit_failure_rolls_back_statuses_and_projection(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    def fail(**kw):raise RuntimeError('synthetic audit interruption')
    monkeypatch.setattr(closure,'append_audit_event',fail)
    with pytest.raises(RuntimeError):closure.run_piste_a_month_closure(settings,'2026-09','2026-10')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode WHERE id=1');assert cur.fetchone()==('Ouvert',)
            cur.execute('SELECT est_actuelle FROM carriere_situation ORDER BY id');assert cur.fetchall()==((1,),(0,))


@pytest.mark.parametrize('has_workspace',[False,True])
def test_existing_workspace_cannot_be_overwritten_by_old_local_month(isolated_mysql,monkeypatch,tmp_path,has_workspace):
    from sme_gend.config import sme_context
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    if has_workspace:
        AppConfigRepository(settings).set_json('sme_workspace',{'editeur_defaut':'CURRENT CONFIG'})
    service=SmeWorkspaceService(settings)
    service.set_active_mois('2026-10',expected=service.load_active_month_snapshot())
    legacy=tmp_path/'legacy_context.json';legacy.write_text(json.dumps({'mois_actif':'2026-09','editeur_defaut':'OBSOLETE'}))
    monkeypatch.setattr(sme_context,'_legacy_context_file',lambda:legacy)
    sme_context._migrate_legacy_context_to_db(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT annee_mois FROM sme_periode WHERE statut='Ouvert'")
            assert cur.fetchall()==(('2026-10',),)



@pytest.mark.parametrize('parallel',[False,True])
def test_month_closure_is_atomic_idempotent_and_preserves_reopened_month(isolated_mysql,monkeypatch,tmp_path,parallel):
    from concurrent.futures import ThreadPoolExecutor
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    if parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:closure.run_piste_a_month_closure(settings,'2026-09','2026-10'),range(2)))
    else:
        results=[closure.run_piste_a_month_closure(settings,'2026-09','2026-10') for _ in range(2)]
    assert sorted(result.tabsme_rows_closed for result in results)==[0,1]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode ORDER BY id');assert cur.fetchall()==(('Fermé',),('Ouvert',),('Réouvert',))
            cur.execute('SELECT est_actuelle FROM carriere_situation ORDER BY id');assert cur.fetchall()==((0,),(1,))
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_closure_piste_a'");assert cur.fetchone()==(1,)
            cur.execute("SELECT payload FROM app_config WHERE config_key='shared_data_revision'");assert json.loads(cur.fetchone()[0])['reason']=='month_closure'


@pytest.mark.parametrize('archived_month',['2026-09','2026-03'])
def test_archive_close_without_activation_does_not_reopen_the_source(isolated_mysql,monkeypatch,tmp_path,archived_month):
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    archive_dir=tmp_path/'archives';archive_dir.mkdir()
    source=tmp_path/'source.mdb';source.write_bytes(b'SYNTHETIC ARCHIVE')
    if archived_month=='2026-03':
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES (4,'2026-03','2026-03-31','Fermé')")
    result=closure.import_existing_db_for_month(settings,source,archived_month,None,import_to_mysql=False,archive_and_close=True)
    assert result.tabsme_rows_closed==1 and result.tabsme_rows_opened==0
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sme_periode WHERE statut='Ouvert'");assert cur.fetchone()==((1,) if archived_month=='2026-03' else (0,))
            cur.execute('SELECT statut FROM sme_periode WHERE id=1');assert cur.fetchone()==(('Ouvert',) if archived_month=='2026-03' else ('Fermé',))


def _lot11_journals(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import audit_mirror_recovery as recovery,audit_log,sensitive_audit_log,doc_gen_log
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,dossier,row=_lot7_dossier(isolated_mysql,monkeypatch)
    logs=tmp_path/'logs'
    monkeypatch.setattr(audit_log,'_audit_dir',lambda:logs)
    monkeypatch.setattr(sensitive_audit_log,'_local_log_path',lambda:logs/'sensitive_audit_log.jsonl')
    monkeypatch.setattr(doc_gen_log,'_log_path',lambda:logs/'doc_gen_log.jsonl')
    node_path=tmp_path/'delivery/node_id'
    monkeypatch.setattr(recovery,'_node_file',lambda:node_path)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    return settings,connect,params,recovery,AppConfigRepository(settings),logs,node_path


@pytest.mark.parametrize('failure',['before_append','partial_append','after_fsync'])
def test_committed_journal_replays_after_interruption_without_duplicate(isolated_mysql,monkeypatch,tmp_path,failure):
    import importlib
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction,CommittedMirrorError
    settings,connect,params,recovery,repository,logs,node_path=_lot11_journals(isolated_mysql,monkeypatch,tmp_path)
    path=logs/'personnel_audit_2026-10.jsonl'
    original_write=recovery.write_all
    def fault(stream,payload):
        if str(getattr(stream,'name','')).endswith(path.name) and b'_mirror_event_id' in payload:
            if failure=='partial_append':
                end=payload.find('é'.encode())+1
                original_write(stream,payload[:end])
            if failure!='after_fsync':raise OSError('synthetic interrupted mirror')
        return original_write(stream,payload)
    original_connect=MySQLConnection.connect
    from contextlib import contextmanager
    @contextmanager
    def fail_ack(self,*args,**kwargs):
        with original_connect(self,*args,**kwargs) as connection:
            if failure!='after_fsync':yield connection;return
            class Cursor:
                def __init__(self,cursor):self.cursor=cursor
                def __enter__(self):self.cursor.__enter__();return self
                def __exit__(self,*exc):return self.cursor.__exit__(*exc)
                def __getattr__(self,key):return getattr(self.cursor,key)
                def execute(self,sql,*params):
                    if sql.startswith('DELETE FROM app_config WHERE config_key='):
                        raise OSError('synthetic stop after durable file write')
                    return self.cursor.execute(sql,*params)
            class Connection:
                def __getattr__(self,key):return getattr(connection,key)
                def cursor(self,*a,**kw):return Cursor(connection.cursor(*a,**kw))
            yield Connection()
    monkeypatch.setattr(recovery,'write_all',fault)
    monkeypatch.setattr(MySQLConnection,'connect',fail_ack)
    entry={'ts_utc':'2026-10-30T12:00:00+00:00','text':'é\n'*2000,'action':'SYNTHETIC RECOVERY'}
    with pytest.raises(CommittedMirrorError):
        with mysql_import_transaction(settings,read_committed=True):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:cur.execute("UPDATE agent SET noms='COMMITTED' WHERE id=1")
            repository.append_audit(action='synthetic_recovery',payload={'value':'SYNTHETIC'},mirror=(path,[entry]))
            entry['text']='CALLER MUTATED AFTER ENQUEUE'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT noms FROM agent WHERE id=1');assert cur.fetchone()==('COMMITTED',)
            cur.execute("SELECT COUNT(*) FROM app_config WHERE LEFT(config_key,13)='audit_mirror:'");assert cur.fetchone()==(1,)
    monkeypatch.setattr(MySQLConnection,'connect',original_connect)
    recovery=importlib.reload(recovery)  # No in-memory callback/state survives.
    monkeypatch.setattr(recovery,'_node_file',lambda:node_path)
    assert recovery.recover_pending(settings)=={'delivered':1,'failed':0}
    assert recovery.recover_pending(settings)=={'delivered':0,'failed':0}
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert len(rows)==1 and rows[0]['text']=='é\n'*2000
    assert len(rows[0]['_mirror_event_id'])==32
    if failure=='partial_append':
        fragments=list((logs/'.audit-recovery').glob('*.part'))
        assert len(fragments)==1 and fragments[0].read_bytes().endswith(b'\xc3')


def test_audit_rollback_discards_delivery_and_never_creates_mirror(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,recovery,repository,logs,node_path=_lot11_journals(isolated_mysql,monkeypatch,tmp_path)
    path=logs/'personnel_audit_2026-10.jsonl'
    with pytest.raises(RuntimeError,match='cancel'):
        with mysql_import_transaction(settings):
            repository.append_audit(action='synthetic_cancel',mirror=(path,[{'action':'CANCEL'}]))
            raise RuntimeError('synthetic cancel')
    assert not path.exists()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='synthetic_cancel'");assert cur.fetchone()==(0,)
            cur.execute("SELECT COUNT(*) FROM app_config WHERE LEFT(config_key,13)='audit_mirror:'");assert cur.fetchone()==(0,)


def test_recovery_only_delivers_its_own_profile_and_pauses_for_restore(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.mysql_restore_staging import maintenance_lock_name
    settings,connect,params,recovery,repository,logs,node_path=_lot11_journals(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr(recovery,'schedule_delivery',lambda *a:None)
    path=logs/'personnel_audit_2026-10.jsonl'
    repository.append_audit(action='synthetic_pending',mirror=(path,[{'action':'PENDING'}]))
    monkeypatch.setattr(recovery,'_node_file',lambda:tmp_path/'other_profile/node_id')
    recovery.node_id()
    assert recovery.recover_pending(settings)=={'delivered':0,'failed':0}
    assert not path.exists()
    monkeypatch.setattr(recovery,'_node_file',lambda:node_path)
    assert recovery.recover_pending(settings,enabled_kinds=set())=={'delivered':0,'failed':0}
    assert not path.exists()
    with connect(**params) as owner:
        with owner.cursor() as cur:
            cur.execute('SELECT GET_LOCK(%s,0)',(maintenance_lock_name(settings),));assert cur.fetchone()==(1,)
            assert recovery.recover_pending(settings).get('paused') is True
            assert not path.exists()
            cur.execute('SELECT RELEASE_LOCK(%s)',(maintenance_lock_name(settings),))
    assert recovery.recover_pending(settings)=={'delivered':1,'failed':0}


def test_batch_audit_and_all_four_mirror_routes_are_recoverable(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,recovery,repository,logs,node_path=_lot11_journals(isolated_mysql,monkeypatch,tmp_path)
    paths=[logs/'personnel_audit_2026-10.jsonl',logs/'user_activity.jsonl',logs/'sensitive_audit_log.jsonl',logs/'doc_gen_log.jsonl']
    for index,path in enumerate(paths):
        entries=[{'action':f'SYNTHETIC-{index}-{item}'} for item in range(2)]
        repository.append_audit_batch([{'action':entry['action'],'module':'synthetic'} for entry in entries],mirror=(path,entries))
        data=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        assert len(data)==2 and len({entry['_mirror_event_id'] for entry in data})==2
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE module='synthetic'");assert cur.fetchone()==(8,)
            cur.execute("SELECT COUNT(*) FROM app_config WHERE LEFT(config_key,13)='audit_mirror:'");assert cur.fetchone()==(0,)



def test_document_fallback_after_lost_ack_is_not_duplicated_by_replay(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import doc_gen_log
    from sme_gend.services.local_audit_mirror import append_json_mirror
    from uuid import uuid4
    settings,connect,params,recovery,repository,logs,node_path=_lot11_journals(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr(recovery,'schedule_delivery',lambda *a:None)
    path=logs/'doc_gen_log.jsonl'
    append_json_mirror(path,[{'doc_type':'OLD','ts_utc':'2026-10-01T00:00:00Z'}])
    entry={'doc_type':'SYNTHETIC DOCUMENT','ts_utc':'2026-10-30T12:00:00Z','_mirror_event_id':uuid4().hex}
    repository.append_audit(action='doc_gen',module='doc_gen',mirror=(path,[entry]))
    # Simulate the historical local fallback after an uncertain acknowledgement.
    append_json_mirror(path,[entry])
    assert recovery.recover_pending(settings)=={'delivered':1,'failed':0}
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert len(rows)==2 and rows[-1]==entry



def test_closure_can_activate_next_month_when_no_month_is_open(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=1")
    closure.run_piste_a_month_closure(settings,'2026-09','2026-10')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode ORDER BY id');assert cur.fetchall()==(('Fermé',),('Ouvert',),('Réouvert',))



def _sync_lot12_tables(connect, params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY, payload JSON NOT NULL, '
                        'updated_by VARCHAR(128), updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE synthetic_sync(id INT PRIMARY KEY, value VARCHAR(64)) ENGINE=InnoDB')
            cur.execute("INSERT INTO synthetic_sync VALUES(1,'before')")


@pytest.mark.parametrize('rollback', [False, True])
def test_sync_lot12_second_client_sees_only_committed_data_and_revision(isolated_mysql, rollback):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.data_sync import read_data_revision, publish_data_change
    settings, connect, params = isolated_mysql
    _sync_lot12_tables(connect, params)
    before=publish_data_change(settings, reason='synthetic_initial')
    second=replace(settings, mysql_view_mois='2026-09')
    def observe():
        token=read_data_revision(second)
        with MySQLConnection(second).connect(read_only=True) as connection:
            with connection.cursor() as cur:
                cur.execute('SELECT value FROM synthetic_sync WHERE id=1')
                return token, cur.fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as reader:
        assert reader.submit(observe).result(timeout=10)==(before,'before')
        try:
            with _personnel_write(settings, reason='synthetic_sync'):
                with MySQLConnection(settings).connect() as connection:
                    with connection.cursor() as cur:
                        cur.execute("UPDATE synthetic_sync SET value='after' WHERE id=1")
                # Thread has its own transaction context and real pooled connection.
                assert reader.submit(observe).result(timeout=10)==(before,'before')
                if rollback:
                    raise RuntimeError('synthetic rollback')
        except RuntimeError:
            if not rollback:
                raise
        token,value=reader.submit(observe).result(timeout=10)
        assert value==('before' if rollback else 'after')
        assert (token==before) is rollback
        if not rollback:
            later=publish_data_change(settings, reason='synthetic_later')
            assert later!=token
            assert reader.submit(observe).result(timeout=10)==(later,'after')


@pytest.mark.parametrize('fail_signal', [False, True])
def test_sync_lot12_shared_root_and_revision_are_committed_together(isolated_mysql, tmp_path, monkeypatch, fail_signal):
    from sme_gend.services import shared_media, data_sync
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params = isolated_mysql
    _sync_lot12_tables(connect, params)
    repository=AppConfigRepository(settings)
    old=r'\\synthetic-server\old-share\images'
    new=r'\\synthetic-server\new-share\images'
    repository.set_json(shared_media.KEY_SHARED_MEDIA, {'unc_path': old})
    before=data_sync.publish_data_change(settings, reason='synthetic_initial')
    # UNC stays in the SQL payload; filesystem operations are confined locally.
    path_type=Path
    monkeypatch.setattr(shared_media, 'canonical_network_path', lambda value: new)
    (tmp_path/'old-share').mkdir()
    monkeypatch.setattr(shared_media, 'Path', lambda value: tmp_path/'share' if str(value)==new else tmp_path/'old-share' if str(value)==old else path_type(value))
    monkeypatch.setattr('sme_gend.config.user_paths.user_application_data_dir', lambda: tmp_path/'profile')
    if fail_signal:
        def failure(*args, **kwargs):
            raise RuntimeError('synthetic notification failure')
        monkeypatch.setattr(data_sync, 'publish_data_change', failure)
        with pytest.raises(RuntimeError, match='synthetic notification'):
            shared_media.configure_shared_media(settings, new, tmp_path/'previous')
    else:
        assert shared_media.configure_shared_media(settings, new, tmp_path/'previous')==new
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path': old if fail_signal else new}
    assert (data_sync.read_data_revision(settings)==before) is fail_signal


def test_sync_lot12_image_change_notifies_an_independent_client(isolated_mysql, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.services import personnel_media, data_sync
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params = isolated_mysql
    _sync_lot12_tables(connect, params)
    before=data_sync.publish_data_change(settings, reason='synthetic_initial')
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr(personnel_media, 'personnel_media_root', lambda: tmp_path/'media')
    notify=personnel_media._notify_shared_media_write
    # Real file copy on the owned local directory, real SQL notification with UNC semantics.
    monkeypatch.setattr(personnel_media, '_notify_shared_media_write', lambda root,**kwargs: notify(Path(r'\\synthetic-server\share\images')))
    source=tmp_path/'photo.png';source.write_bytes(b'SYNTHETIC IMAGE')
    dest=personnel_media.save_person_photo('42',source)
    assert dest.read_bytes()==b'SYNTHETIC IMAGE'
    with ThreadPoolExecutor(max_workers=1) as reader:
        after=reader.submit(data_sync.read_data_revision,settings).result(timeout=10)
    assert after and after!=before
    assert AppConfigRepository(settings).get_json(data_sync.KEY_DATA_REVISION)['reason']=='personnel_media'



def _media_lot13_setup(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services import shared_media, personnel_media
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params=isolated_mysql
    _sync_lot12_tables(connect,params)
    names=[r'\\synthetic-server\share'+'\\'+part for part in ('old','new','third')]
    roots={name:tmp_path/part for name,part in zip(names,('old','new','third'))}
    for root in roots.values():root.mkdir()
    real_path=Path
    monkeypatch.setattr(shared_media,'Path',lambda value:roots.get(str(value),real_path(value)))
    contained=personnel_media.contained_path
    monkeypatch.setattr(personnel_media,'contained_path',lambda root,value:contained(roots.get(str(root),root),value))
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr(personnel_media,'personnel_media_root',lambda:real_path(names[0]))
    repository=AppConfigRepository(settings)
    repository.set_json(shared_media.KEY_SHARED_MEDIA,{'unc_path':names[0]})
    return settings,repository,names,roots


def test_media_lot13_stale_client_writes_to_latest_shared_root(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media,personnel_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    source=tmp_path/'upload.png';source.write_bytes(b'SYNTHETIC NEW')
    dest=personnel_media.save_person_photo('42',source)
    assert dest==roots[names[1]]/'photos/42.png'
    assert dest.read_bytes()==b'SYNTHETIC NEW'
    assert not (roots[names[0]]/'photos/42.png').exists()


def test_media_lot13_migration_reads_authoritative_root_not_stale_profile(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    (roots[names[0]]/'photos/42.jpg').write_bytes(b'AUTHORITATIVE')
    shared_media.configure_shared_media(settings,names[1],tmp_path/'stale-local-profile')
    assert (roots[names[1]]/'photos/42.jpg').read_bytes()==b'AUTHORITATIVE'


def test_media_lot13_missing_shared_source_cannot_activate_empty_target(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    roots[names[0]].rmdir()
    with pytest.raises(OSError):
        shared_media.configure_shared_media(settings,names[1],tmp_path/'stale-local-profile')
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}


def test_media_lot13_conflicting_target_requires_resolution_without_switch(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    for name in names[:2]:(roots[name]/'photos').mkdir()
    (roots[names[0]]/'photos/42.jpg').write_bytes(b'SOURCE PHOTO')
    (roots[names[1]]/'photos/42.png').write_bytes(b'DIFFERENT TARGET PHOTO')
    with pytest.raises(ValueError,match='images|Images'):
        shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}
    assert (roots[names[1]]/'photos/42.png').read_bytes()==b'DIFFERENT TARGET PHOTO'
    assert (roots[names[0]]/'photos/42.jpg').read_bytes()==b'SOURCE PHOTO'


def test_media_lot13_retry_cannot_promote_stale_partial_migration(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media,data_sync
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    source=roots[names[0]]/'photos/42.jpg';source.write_bytes(b'BEFORE')
    def failure(*args,**kwargs):raise RuntimeError('synthetic precommit interruption')
    with monkeypatch.context() as patch:
        patch.setattr(data_sync,'publish_data_change',failure)
        with pytest.raises(RuntimeError):shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert (roots[names[1]]/'photos/42.jpg').read_bytes()==b'BEFORE'
    source.write_bytes(b'AFTER')
    with pytest.raises(ValueError,match='images|Images'):
        shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}
    assert source.read_bytes()==b'AFTER'


def test_media_lot13_writer_during_switch_reselects_root_after_wait(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sme_gend.services import shared_media,personnel_media,data_sync
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    (roots[names[0]]/'photos/42.jpg').write_bytes(b'BEFORE')
    upload=tmp_path/'upload.png';upload.write_bytes(b'AFTER')
    ready,finish=Event(),Event()
    original=data_sync.publish_data_change
    def pause(settings,*,reason,**kwargs):
        if reason=='media_storage':
            ready.set()
            assert finish.wait(8),'Test did not release synthetic migration'
        return original(settings,reason=reason,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',pause)
    with ThreadPoolExecutor(max_workers=2) as workers:
        migration=workers.submit(shared_media.configure_shared_media,settings,names[1],roots[names[0]])
        assert ready.wait(8)
        writer=workers.submit(personnel_media.save_person_photo,'42',upload)
        finish.set()
        assert migration.result(timeout=12)==names[1]
        assert writer.result(timeout=12)==roots[names[1]]/'photos/42.png'
    assert (roots[names[1]]/'photos/42.png').read_bytes()==b'AFTER'



def test_media_lot13_busy_gate_writes_nothing_and_other_business_can_continue(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.services import media_storage_coordination as coordination
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    _,connect,params=isolated_mysql
    def another_post():
        with pytest.raises(coordination.MediaStorageBusyError):
            with coordination.media_storage_gate(settings,timeout=0):pass
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE synthetic_sync SET value='business continues' WHERE id=1")
    with coordination.media_storage_gate(settings) as owner:
        with ThreadPoolExecutor(max_workers=1) as workers:workers.submit(another_post).result(timeout=8)
        owner.check()
    assert list(roots[names[1]].iterdir())==[]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT value FROM synthetic_sync WHERE id=1');assert cur.fetchone()==('business continues',)
    with coordination.media_storage_gate(settings,timeout=0) as successor:successor.check()


def test_media_lot13_host_aliases_share_same_gate(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services import media_storage_coordination as coordination
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    alias=replace(settings,mysql_host='synthetic-host-alias')
    params=MySQLConnection(settings).connect_params()
    monkeypatch.setattr(MySQLConnection,'connect_params',lambda self:dict(params))
    def acquire_alias():
        with pytest.raises(coordination.MediaStorageBusyError):
            with coordination.media_storage_gate(alias,timeout=0):pass
    with coordination.media_storage_gate(settings):
        with ThreadPoolExecutor(max_workers=1) as workers:workers.submit(acquire_alias).result(timeout=8)


def test_media_lot13_loss_of_coordination_keeps_old_config_and_can_retry(isolated_mysql,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import shared_media,media_storage_coordination as coordination
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    (roots[names[0]]/'photos/42.jpg').write_bytes(b'COMPLETE')
    observed=[];original_gate=coordination.media_storage_gate;original_copy=shared_media._copy_media_sources
    @contextmanager
    def capture(*args,**kwargs):
        with original_gate(*args,**kwargs) as gate:
            observed.append(gate);yield gate
    def disconnect_after_copy(*args):
        original_copy(*args);observed[0].connection.close()
    with monkeypatch.context() as patch:
        patch.setattr(coordination,'media_storage_gate',capture)
        patch.setattr(shared_media,'_copy_media_sources',disconnect_after_copy)
        with pytest.raises(coordination.MediaStorageLockLostError):
            shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}
    assert (roots[names[1]]/'photos/42.jpg').read_bytes()==b'COMPLETE'
    assert shared_media.configure_shared_media(settings,names[1],roots[names[0]])==names[1]


def test_media_lot13_two_switches_inherit_latest_authoritative_images(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sme_gend.services import shared_media,data_sync
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    for name in names[:2]:(roots[name]/'photos').mkdir()
    (roots[names[0]]/'photos/42.jpg').write_bytes(b'BASE')
    (roots[names[1]]/'photos/99.png').write_bytes(b'PRESERVE TARGET EXTRA')
    ready,finish=Event(),Event();original=data_sync.publish_data_change;calls=[]
    def pause(settings,*,reason,**kwargs):
        if reason=='media_storage':
            calls.append(1)
            if len(calls)==1:
                ready.set();assert finish.wait(8)
        return original(settings,reason=reason,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',pause)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(shared_media.configure_shared_media,settings,names[1],roots[names[0]])
        assert ready.wait(8)
        second=workers.submit(shared_media.configure_shared_media,settings,names[2],roots[names[0]])
        finish.set()
        first.result(timeout=12);second.result(timeout=12)
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[2]}
    assert (roots[names[2]]/'photos/99.png').read_bytes()==b'PRESERVE TARGET EXTRA'
    assert (roots[names[2]]/'photos/42.jpg').read_bytes()==b'BASE'


def test_media_lot13_lost_commit_acknowledgement_is_confirmed_without_second_switch(isolated_mysql,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import shared_media
    from sme_gend.repositories import mysql_connection
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    original=mysql_connection.mysql_import_transaction
    @contextmanager
    def lost_ack(*args,**kwargs):
        with original(*args,**kwargs):yield
        raise OSError('synthetic lost acknowledgement after real commit')
    monkeypatch.setattr(mysql_connection,'mysql_import_transaction',lost_ack)
    with pytest.raises(shared_media.SharedMediaSwitchCommittedError,match='déjà enregistrée') as error:
        shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert error.value.committed is True
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[1]}


def test_media_lot13_rejects_switch_inside_business_transaction(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    with mysql_import_transaction(settings):
        with pytest.raises(RuntimeError,match='transaction métier'):
            shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert list(roots[names[1]].iterdir())==[]
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}



def test_media_lot13_unreadable_source_image_cannot_be_silently_skipped(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    source=roots[names[0]]/'photos/42.jpg';source.write_bytes(b'PRESERVE')
    stat=Path.stat
    def unavailable(path,*args,**kwargs):
        if path==source:raise PermissionError('synthetic source metadata denied')
        return stat(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',unavailable)
    with pytest.raises(OSError):shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[0]}


def test_media_lot13_unknown_commit_does_not_claim_rollback(isolated_mysql,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import shared_media,media_storage_coordination as coordination
    from sme_gend.repositories import mysql_connection
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    observed=[];original_gate=coordination.media_storage_gate;original_transaction=mysql_connection.mysql_import_transaction
    @contextmanager
    def capture(*args,**kwargs):
        with original_gate(*args,**kwargs) as gate:
            observed.append(gate);yield gate
    @contextmanager
    def lose_both_connections(*args,**kwargs):
        with original_transaction(*args,**kwargs):yield
        observed[0].connection.close()
        raise OSError('synthetic real commit, both acknowledgements unavailable')
    monkeypatch.setattr(coordination,'media_storage_gate',capture)
    monkeypatch.setattr(mysql_connection,'mysql_import_transaction',lose_both_connections)
    with pytest.raises(shared_media.SharedMediaSwitchOutcomeUnknownError,match='incertain') as error:
        shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert error.value.committed is None
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[1]}



def test_media_lot13_unchanged_root_does_not_rehash_existing_photos(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import shared_media
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    (roots[names[0]]/'photos').mkdir()
    for extension,payload in [('jpg',b'LEGACY JPG'),('png',b'LEGACY PNG')]:
        (roots[names[0]]/('photos/42.'+extension)).write_bytes(payload)
    monkeypatch.setattr(shared_media,'_file_digest',lambda path:pytest.fail('Unchanged destination must not rehash all photos'))
    assert shared_media.configure_shared_media(settings,names[0],roots[names[0]])==names[0]
    assert (roots[names[0]]/'photos/42.jpg').read_bytes()==b'LEGACY JPG'
    assert (roots[names[0]]/'photos/42.png').read_bytes()==b'LEGACY PNG'



def test_media_lot13_other_business_revision_does_not_hide_uncertain_commit(isolated_mysql,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import shared_media,data_sync
    from sme_gend.repositories import mysql_connection
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    original=mysql_connection.mysql_import_transaction
    @contextmanager
    def lost_ack_with_other_revision(*args,**kwargs):
        with original(*args,**kwargs):yield
        data_sync.publish_data_change(settings,reason='synthetic concurrent business change')
        raise OSError('synthetic lost commit acknowledgement')
    monkeypatch.setattr(mysql_connection,'mysql_import_transaction',lost_ack_with_other_revision)
    with pytest.raises(shared_media.SharedMediaSwitchOutcomeUnknownError,match='incertain'):
        shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json(shared_media.KEY_SHARED_MEDIA)=={'unc_path':names[1]}



def _config_lot14_setup(isolated_mysql,tmp_path,monkeypatch):
    from zipfile import ZipFile
    from sme_gend.services import data_sync
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    _audit_fixture_schema(isolated_mysql[1], isolated_mysql[2])
    repository.set_json('limite_age_config',{'version':'before'})
    before=data_sync.publish_data_change(settings,reason='synthetic_before')
    monkeypatch.setattr(backup,'default_backup_directory',lambda:tmp_path/'backups')
    monkeypatch.setattr(backup,'user_application_data_dir',lambda:tmp_path/'user')
    monkeypatch.setattr(backup,'user_paths_config_file',lambda:tmp_path/'user/paths.json')
    monkeypatch.setattr(backup,'resolved_personnel_media_root',lambda:roots[names[0]])
    rows=[{'config_key':'limite_age_config','payload':{'version':'archived'}}]
    def archive(extra=None,config_rows=None):
        path=tmp_path/'configuration.zip'
        manifest=backup.BackupManifest(4,'2026-10-30','synthetic','invalid.test',{},[],app_config_exported=True)
        with ZipFile(path,'w') as zf:
            zf.writestr(backup.MANIFEST_NAME,json.dumps(manifest.to_dict()))
            zf.writestr(backup.APP_CONFIG_DUMP_NAME,json.dumps(rows if config_rows is None else config_rows))
            for name,value in (extra or {}).items():zf.writestr(name,value)
        return path
    scope=backup.RestoreScope(restore_mysql=False,restore_app_config=True,restore_user_config=True,
        restore_app_data=False,restore_media_photos=False,restore_media_grades=False,preserve_local_accounts=False)
    return settings,repository,names,roots,before,archive,scope


def test_config_lot14_preserves_active_shared_paths_and_pending_deliveries(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import data_sync
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    live='audit_mirror:'+'1'*32;old='audit_mirror:'+'2'*32
    repository.set_json(live,{'marker':'ACTIVE'})
    rows=[{'config_key':'limite_age_config','payload':{'version':'archived'}},
          {'config_key':'SHARED_PERSONNEL_MEDIA','payload':{'unc_path':names[2]}},
          {'config_key':'shared_data_revision','payload':{'token':'ARCHIVED'}},
          {'config_key':live,'payload':{'marker':'ARCHIVED'}},
          {'config_key':old,'payload':{'marker':'ARCHIVED'}}]
    backup.restore_backup(settings,archive(config_rows=rows),scope=scope)
    assert repository.get_json('shared_personnel_media')=={'unc_path':names[0]}
    assert repository.get_json(live)=={'marker':'ACTIVE'}
    assert repository.get_json(old) is None
    after=data_sync.read_data_revision(settings)
    assert after and after not in (before,'ARCHIVED')


def test_config_lot14_file_failure_rolls_back_shared_config(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services.restore_files import RestoreFiles
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    target=tmp_path/'user/preferences.json';target.parent.mkdir();target.write_bytes(b'{"theme":"before"}')
    publish=RestoreFiles.publish
    def fail(files):
        publish(files);raise OSError('synthetic file publication failure')
    monkeypatch.setattr(RestoreFiles,'publish',fail)
    with pytest.raises(OSError):backup.restore_backup(settings,archive({'user_config/preferences.json':'{"theme":"after"}'}),scope=scope)
    assert repository.get_json('limite_age_config')=={'version':'before'}
    assert target.read_bytes()==b'{"theme":"before"}'


def test_config_lot14_success_publishes_a_fresh_revision(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import data_sync
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    backup.restore_backup(settings,archive(),scope=scope)
    assert repository.get_json('limite_age_config')=={'version':'archived'}
    assert data_sync.read_data_revision(settings)!=before



@pytest.mark.parametrize('committed',[False,True])
def test_config_lot14_pending_restore_is_recovered_before_next_attempt(isolated_mysql,tmp_path,monkeypatch,committed):
    from sme_gend.services import configuration_restore as restore
    from sme_gend.services.restore_files import RestoreFiles
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    target=tmp_path/'user/preferences.json';target.parent.mkdir();target.write_bytes(b'{"theme":"before"}')
    publish,complete=RestoreFiles.publish,RestoreFiles.complete
    def interruption(files):
        if not committed:publish(files)
        raise OSError('synthetic interruption')
    with monkeypatch.context() as patch:
        patch.setattr(RestoreFiles,'complete' if committed else 'publish',interruption)
        patch.setattr(restore,'_finish_or_rollback',lambda *a:(_ for _ in ()).throw(OSError('synthetic recovery unavailable')))
        with pytest.raises(restore.ConfigurationRestoreRecoveryRequired):
            backup.restore_backup(settings,archive({'user_config/preferences.json':'{"theme":"after"}'}),scope=scope)
    assert target.read_bytes()==b'{"theme":"after"}'
    assert repository.get_json('limite_age_config')=={'version':'archived' if committed else 'before'}
    first=list((tmp_path/'backups/restore_sessions').glob('*/configuration_recovery.json'))[0]
    backup.restore_backup(settings,archive(config_rows=[]),scope=scope)
    assert target.read_bytes()==(b'{"theme":"after"}' if committed else b'{"theme":"before"}')
    assert json.loads(first.read_text())['phase']==('completed' if committed else 'rolled_back')


def test_config_lot14_recovery_never_overwrites_an_external_file_change(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import configuration_restore as restore
    from sme_gend.services.restore_files import RestoreFiles
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    target=tmp_path/'user/preferences.json';target.parent.mkdir();target.write_bytes(b'{"theme":"before"}')
    publish=RestoreFiles.publish
    def fail(files):publish(files);raise OSError('synthetic publication interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(RestoreFiles,'publish',fail)
        patch.setattr(restore,'_finish_or_rollback',lambda *a:(_ for _ in ()).throw(OSError('synthetic recovery unavailable')))
        with pytest.raises(restore.ConfigurationRestoreRecoveryRequired):
            backup.restore_backup(settings,archive({'user_config/preferences.json':'{"theme":"after"}'}),scope=scope)
    target.write_bytes(b'{"theme":"external"}')
    with pytest.raises(restore.ConfigurationRestoreRecoveryRequired):
        backup.restore_backup(settings,archive(config_rows=[]),scope=scope)
    assert target.read_bytes()==b'{"theme":"external"}'


def test_config_lot14_commit_ack_loss_uses_stable_marker(isolated_mysql,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import configuration_restore as restore,data_sync
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    original=restore.mysql_import_transaction
    @contextmanager
    def lost_ack(*args,**kwargs):
        with original(*args,**kwargs):yield
        data_sync.publish_data_change(settings,reason='synthetic concurrent business change')
        raise OSError('synthetic commit acknowledgement lost')
    monkeypatch.setattr(restore,'mysql_import_transaction',lost_ack)
    with pytest.raises(restore.ConfigurationRestoreCommittedError) as error:
        backup.restore_backup(settings,archive(),scope=scope)
    assert error.value.committed is True
    assert repository.get_json('limite_age_config')=={'version':'archived'}
    state=json.loads(next((tmp_path/'backups/restore_sessions').glob('*/configuration_recovery.json')).read_text())
    assert state['phase']=='completed'


def test_config_lot14_current_marker_read_waits_for_inflight_transaction(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from uuid import uuid4
    from sme_gend.services import configuration_restore as restore
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    operation=uuid4().hex;state={'marker':'configuration_restore:'+operation,'operation':operation}
    _, connect, params = isolated_mysql
    def probe():
        with restore._sql_gate(settings) as gate:
            return restore._committed(gate, state)
    with connect(**(params | {'autocommit': False})) as writer:
        with writer.cursor() as cur:
            cur.execute('INSERT INTO app_config(config_key,payload) VALUES(%s,%s)',
                        (state['marker'], json.dumps({'operation':operation,'state':'committed'})))
        with ThreadPoolExecutor(max_workers=1) as worker:
            result=worker.submit(probe)
            with pytest.raises(TimeoutError):result.result(timeout=.1)
            writer.commit()
            assert result.result(timeout=8) is True


def test_config_lot14_media_restores_to_current_root_not_cached_root(isolated_mysql,tmp_path,monkeypatch):
    from dataclasses import replace
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    repository.set_json('shared_personnel_media',{'unc_path':names[1]})
    backup.restore_backup(settings,archive({'personnel_media/photos/42.png':b'RESTORED'}),
        scope=replace(scope,restore_media_photos=True))
    assert (roots[names[1]]/'photos/42.png').read_bytes()==b'RESTORED'
    assert not (roots[names[0]]/'photos/42.png').exists()


def test_config_lot14_shared_switch_refuses_during_restore_then_copies_its_images(isolated_mysql,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event
    from sme_gend.services import shared_media
    from sme_gend.services.database_maintenance import MaintenanceActiveError
    from sme_gend.services.restore_files import RestoreFiles
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    ready,finish=Event(),Event();publish=RestoreFiles.publish
    def pause(files):
        publish(files);ready.set();assert finish.wait(8)
    monkeypatch.setattr(RestoreFiles,'publish',pause)
    with ThreadPoolExecutor(max_workers=2) as workers:
        restoring=workers.submit(backup.restore_backup,settings,archive({'personnel_media/photos/42.png':b'RESTORED'}),scope=replace(scope,restore_media_photos=True))
        assert ready.wait(8)
        switching=workers.submit(shared_media.configure_shared_media,settings,names[1],roots[names[0]])
        try:
            with pytest.raises(MaintenanceActiveError):
                switching.result(timeout=8)
            assert repository.get_json('shared_personnel_media') == {'unc_path': names[0]}
            assert not (roots[names[1]]/'photos/42.png').exists()
        finally:
            finish.set()
        restoring.result(timeout=12)
    # The user can retry after maintenance; the restored image must be copied.
    shared_media.configure_shared_media(settings,names[1],roots[names[0]])
    assert repository.get_json('shared_personnel_media')=={'unc_path':names[1]}
    assert (roots[names[1]]/'photos/42.png').read_bytes()==b'RESTORED'


@pytest.mark.parametrize('preserve',[False,True])
def test_config_lot14_account_preservation_handles_collation_and_absence(isolated_mysql,tmp_path,monkeypatch,preserve):
    from dataclasses import replace
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    # Existing account set is absent. Preservation means keeping that absence.
    rows=[{'config_key':'AUTH_CÓNFIG','payload':{'users':[{'username':'SYNTHETIC'}]}}]
    backup.restore_backup(settings,archive(config_rows=rows),scope=replace(scope,preserve_local_accounts=preserve))
    assert (repository.get_json('auth_config') is None) is preserve


def test_config_lot14_duplicate_database_keys_refused_before_restore(isolated_mysql,tmp_path,monkeypatch):
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    rows=[{'config_key':'limite_age_config','payload':{'version':'first'}},
          {'config_key':'limite_áge_config','payload':{'version':'second'}}]
    with pytest.raises(ValueError):backup.restore_backup(settings,archive(config_rows=rows),scope=scope)
    assert repository.get_json('limite_age_config')=={'version':'before'}



def test_config_lot14_full_restore_lock_refuses_configuration_restore(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services.mysql_restore_staging import maintenance_lock_name
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    _,connect,params=isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute('SELECT GET_LOCK(%s,0)',(maintenance_lock_name(settings),));assert cur.fetchone()==(1,)
            try:
                with pytest.raises(RuntimeError,match='déjà en cours'):
                    backup.restore_backup(settings,archive(),scope=scope)
            finally:cur.execute('SELECT RELEASE_LOCK(%s)',(maintenance_lock_name(settings),))
    assert repository.get_json('limite_age_config')=={'version':'before'}


def test_config_lot14_busy_media_gate_refuses_before_publication(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services import media_storage_coordination as coordination
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    original=coordination.media_storage_gate
    with original(settings):
        monkeypatch.setattr(coordination,'media_storage_gate',lambda settings:original(settings,timeout=0))
        # Starting exclusive maintenance inside an admitted media write is
        # refused before any file publication; no upgrade of that lease.
        with pytest.raises(RuntimeError, match='hors transaction métier'):
            backup.restore_backup(settings,archive({'user_config/preferences.json':'{"theme":"NEW"}'}),scope=scope)
    assert repository.get_json('limite_age_config')=={'version':'before'}
    assert not (tmp_path/'user/preferences.json').exists()


def test_config_lot14_explicit_local_media_override_is_preserved(isolated_mysql,tmp_path,monkeypatch):
    from dataclasses import replace
    from sme_gend.config.user_paths import UserPathsOverrides
    settings,repository,names,roots,before,archive,scope=_config_lot14_setup(isolated_mysql,tmp_path,monkeypatch)
    repository.set_json('shared_personnel_media',{'unc_path':names[1]})
    monkeypatch.setattr('sme_gend.config.user_paths.load_user_paths',lambda:UserPathsOverrides(
        personnel_media_local_override=True,personnel_media_root_path=str(roots[names[0]])))
    backup.restore_backup(settings,archive({'personnel_media/photos/42.png':b'LOCAL RESTORED'}),
        scope=replace(scope,restore_media_photos=True))
    assert (roots[names[0]]/'photos/42.png').read_bytes()==b'LOCAL RESTORED'
    assert not (roots[names[1]]/'photos/42.png').exists()
    assert repository.get_json('shared_personnel_media')=={'unc_path':names[1]}



@pytest.mark.parametrize('lose_lease',[False,True])
def test_contention_lot16_photo_rechecks_mysql_gate_and_notifies_once(isolated_mysql,tmp_path,monkeypatch,lose_lease):
    from contextlib import contextmanager
    from sme_gend.services import personnel_media,media_storage_coordination as coordination,file_contention
    settings,repository,names,roots=_media_lot13_setup(isolated_mysql,tmp_path,monkeypatch)
    target=roots[names[0]]/'photos/42.jpg';target.parent.mkdir();target.write_bytes(b'OLD')
    source=tmp_path/'source.jpg';source.write_bytes(b'NEW COMPLETE')
    gates=[];original_gate=coordination.media_storage_gate;original_replace=Path.replace;calls=[]
    @contextmanager
    def capture(*args,**kwargs):
        with original_gate(*args,**kwargs) as gate:
            gates.append(gate);yield gate
    def busy_once(path,dest):
        if Path(dest)==target:
            calls.append(path)
            if len(calls)==1:
                if lose_lease:gates[0].connection.close()
                error=PermissionError('synthetic transient sharing contention');error.winerror=32;raise error
        return original_replace(path,dest)
    monkeypatch.setattr(coordination,'media_storage_gate',capture)
    monkeypatch.setattr(Path,'replace',busy_once)
    monkeypatch.setattr(file_contention,'_pause',lambda seconds:None)
    before=repository.get_json('shared_data_revision')
    if lose_lease:
        with pytest.raises(coordination.MediaStorageLockLostError):personnel_media.save_person_photo('42',source)
        assert target.read_bytes()==b'OLD' and len(calls)==1
        assert repository.get_json('shared_data_revision')==before
    else:
        assert personnel_media.save_person_photo('42',source)==target
        assert target.read_bytes()==b'NEW COMPLETE' and len(calls)==2
        after=repository.get_json('shared_data_revision')
        assert after!=before and after['reason']=='personnel_media'
    assert not list(target.parent.glob('.sme-*'))
    assert repository.get_json('shared_personnel_media')=={'unc_path':names[0]}



@pytest.mark.parametrize('interruption',[KeyboardInterrupt,SystemExit,GeneratorExit])
@pytest.mark.parametrize('background',[False,True])
def test_pool_lot17_interrupted_write_cannot_commit_with_the_next_operation(isolated_mysql,interruption,background):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction,after_mysql_commit
    from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
    settings,connect,params=isolated_mysql
    with connect(**params) as admin:
        with admin.cursor() as cur:cur.execute('CREATE TABLE interruption_probe (id INT PRIMARY KEY) ENGINE=InnoDB')
    notifications=[]
    def operations():
        original=interruption('synthetic interrupted write')
        first=None
        try:
            with pytest.raises(interruption) as raised:
                with mysql_import_transaction(settings):
                    with MySQLConnection(settings).connect() as first:
                        with first.cursor() as cur:cur.execute('INSERT INTO interruption_probe VALUES (1)')
                    after_mysql_commit(lambda:notifications.append('INTERRUPTED'))
                    raise original
            assert raised.value is original
            with mysql_import_transaction(settings):
                with MySQLConnection(settings).connect() as connection:
                    with connection.cursor() as cur:cur.execute('INSERT INTO interruption_probe VALUES (2)')
                after_mysql_commit(lambda:notifications.append('NEXT'))
            assert not first.open or not first.server_status & SERVER_STATUS_IN_TRANS
        finally:
            # Defensive cleanup also when run against the pre-fix implementation.
            if first is not None and first.open:first.close()
            invalidate_mysql_pool(settings)
    if background:
        with ThreadPoolExecutor(max_workers=1) as workers:workers.submit(operations).result(timeout=10)
    else:operations()
    with connect(**params) as admin:
        with admin.cursor() as cur:
            cur.execute('SELECT id FROM interruption_probe ORDER BY id');assert cur.fetchall()==((2,),)
    assert notifications==['NEXT']


def test_pool_lot17_failed_rollback_cannot_leak_a_write_into_the_next_commit(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings,connect,params=isolated_mysql
    with connect(**params) as admin:
        with admin.cursor() as cur:cur.execute('CREATE TABLE rollback_probe (id INT PRIMARY KEY) ENGINE=InnoDB')
    original=ValueError('synthetic failed business operation')
    first=None
    try:
        with pytest.raises(ValueError) as raised:
            with MySQLConnection(settings).connect() as first:
                monkeypatch.setattr(first,'rollback',lambda:(_ for _ in ()).throw(OSError('synthetic rollback failure')))
                with first.cursor() as cur:cur.execute('INSERT INTO rollback_probe VALUES (1)')
                raise original
        assert raised.value is original
        with MySQLConnection(settings).connect() as next_connection:
            with next_connection.cursor() as cur:cur.execute('INSERT INTO rollback_probe VALUES (2)')
        with connect(**params) as admin:
            with admin.cursor() as cur:
                cur.execute('SELECT id FROM rollback_probe ORDER BY id');assert cur.fetchall()==((2,),)
        assert not first.open
    finally:
        if first is not None and first.open:first.close()


from dataclasses import replace
from types import SimpleNamespace
import pytest


def _organigram_lot18_setup(isolated_mysql, monkeypatch):
    from sme_gend.config import liste_generale_scope, sme_context
    from sme_gend.repositories import mysql_common
    from sme_gend.services.mysql_ref_etl import RefEtlCache
    from sme_gend.repositories import mysql_organigram_repository as organigram
    settings, connect, params = isolated_mysql
    monkeypatch.setattr(mysql_common, 'ensure_mysql_schema', lambda settings: None)
    monkeypatch.setattr(liste_generale_scope, 'load_sme_context', lambda: SimpleNamespace(mois_actif='2026-10'))
    monkeypatch.setattr(sme_context, 'is_mois_ferme', lambda month: month == '2026-09')
    # The attachment test starts with an existing, already-resolved reference.
    # Keep its reference resolution separate from the SQL update under test.
    monkeypatch.setattr(organigram, 'ensure_ref_etl_cache', lambda cur: RefEtlCache())
    monkeypatch.setattr(organigram, 'load_subdiv_flat_rows', lambda cur: [])
    monkeypatch.setattr(organigram, 'ensure_ref_subdiv_arme', lambda cur, cache, **kwargs: 10)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE app_config (config_key VARCHAR(64) PRIMARY KEY, payload JSON NOT NULL, updated_by VARCHAR(128), updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE ref_structure (id INT PRIMARY KEY, code VARCHAR(20), ordre_tri INT) ENGINE=InnoDB')
            cur.execute('CREATE TABLE ref_subdivision_arme (id INT PRIMARY KEY, structure_id INT, libelle VARCHAR(100), ordre_tri INT, actif BOOL) ENGINE=InnoDB')
            cur.execute('CREATE TABLE sme_periode (id INT PRIMARY KEY, annee_mois VARCHAR(7)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE carriere_situation (id INT PRIMARY KEY, agent_id INT, sme_periode_id INT, est_actuelle BOOL, date_debut DATE, date_fin DATE, structure_id INT, structure_code VARCHAR(20), subdivision_arme_id INT NULL, subdivision_arme_libelle VARCHAR(100)) ENGINE=InnoDB')
            cur.execute("INSERT INTO ref_structure VALUES (1,'OLD',1),(2,'DEST',2)")
            cur.execute("INSERT INTO ref_subdivision_arme VALUES (10,1,'UNIT',0,0)")
            cur.execute("INSERT INTO sme_periode VALUES (1,'2026-09'),(2,'2026-10')")
            cur.execute("INSERT INTO carriere_situation VALUES (1,42,1,0,'2026-09-01','2026-09-30',1,'OLD',10,'UNIT'),(2,42,2,1,'2026-10-01',NULL,1,'OLD',10,'UNIT')")
    return settings, connect, params, organigram.MySQLOrganigramRepository


def _organigram_lot18_apply(repository, operation):
    if operation == 'move':
        repository.move_subdivision_to_structure(10, 2, 'DEST')
    elif operation == 'rename':
        assert repository.rename_subdivision(10, 'NEW UNIT', 'OLD') == 1
    else:
        assert repository.attach_subdivision_from_data(1, 'UNIT') == 10


@pytest.mark.parametrize('month', [None, '2026-09'])
@pytest.mark.parametrize('operation', ['move', 'rename', 'attach'])
def test_organigram_lot18_updates_only_the_requested_month(isolated_mysql, monkeypatch, month, operation):
    settings, connect, params, repository_class = _organigram_lot18_setup(isolated_mysql, monkeypatch)
    if operation == 'attach':
        with connect(**params) as conn:
            with conn.cursor() as cur: cur.execute('UPDATE carriere_situation SET subdivision_arme_id=NULL')
    repository = repository_class(replace(settings, mysql_view_mois=month))
    _organigram_lot18_apply(repository, operation)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT structure_id,structure_code,subdivision_arme_id,subdivision_arme_libelle FROM carriere_situation ORDER BY id')
            rows = list(cur.fetchall())
            cur.execute('SELECT structure_id,libelle,actif FROM ref_subdivision_arme WHERE id=10')
            reference = cur.fetchone()
    untouched = (1, 'OLD', None if operation == 'attach' else 10, 'UNIT')
    changed = {'move':(2,'DEST',10,'UNIT'),'rename':(1,'OLD',10,'NEW UNIT'),'attach':(1,'OLD',10,'UNIT')}[operation]
    expected = [untouched, untouched]
    expected[0 if month else 1] = changed
    assert rows == expected
    assert reference == {'move':(2,'UNIT',0),'rename':(1,'NEW UNIT',0),'attach':(1,'UNIT',1)}[operation]


@pytest.mark.parametrize('operation', ['move', 'rename', 'attach'])
def test_organigram_lot18_failed_career_update_rolls_back_the_reference(isolated_mysql, monkeypatch, operation):
    settings, connect, params, repository_class = _organigram_lot18_setup(isolated_mysql, monkeypatch)
    if operation == 'attach':
        with connect(**params) as conn:
            with conn.cursor() as cur: cur.execute('UPDATE carriere_situation SET subdivision_arme_id=NULL')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TRIGGER synthetic_veto BEFORE UPDATE ON carriere_situation FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic career update veto'")
    repository = repository_class(replace(settings, mysql_view_mois='2026-09'))
    with pytest.raises(pymysql.err.OperationalError, match='synthetic career update veto'):
        _organigram_lot18_apply(repository, operation)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT structure_id,libelle,ordre_tri,actif FROM ref_subdivision_arme WHERE id=10')
            assert cur.fetchone() == (1,'UNIT',0,0)
            cur.execute('SELECT structure_id,structure_code,subdivision_arme_id,subdivision_arme_libelle FROM carriere_situation ORDER BY id')
            expected = (1,'OLD',None if operation == 'attach' else 10,'UNIT')
            assert cur.fetchall() == (expected,expected)


@pytest.mark.parametrize('month', [None, '2026-09'])
@pytest.mark.parametrize('operation', ['backfill', 'structure', 'rename_named', 'rename_empty', 'rename_to_empty'])
def test_scoped_reference_lot18_agent_updates_preserve_adjacent_months(isolated_mysql, monkeypatch, month, operation):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _ = _organigram_lot18_setup(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if operation == 'backfill': cur.execute('UPDATE carriere_situation SET structure_id=NULL')
            if operation == 'rename_empty': cur.execute('UPDATE carriere_situation SET subdivision_arme_libelle=NULL, subdivision_arme_id=NULL')
    repository = MySQLAgentRepository(replace(settings, mysql_view_mois=month))
    if operation == 'backfill': assert repository.backfill_structure_id_for_code('OLD') == 1
    elif operation == 'structure': assert repository.update_structure_column('OLD', 'DEST') == 1
    else:
        old = '(Sans subdivision)' if operation == 'rename_empty' else 'UNIT'
        new = '(Sans subdivision)' if operation == 'rename_to_empty' else 'NEW UNIT'
        assert repository.rename_subarme_for_structure('OLD', old, new) == 1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT structure_id,structure_code,subdivision_arme_libelle FROM carriere_situation ORDER BY id')
            rows = list(cur.fetchall())
            cur.execute("SELECT COUNT(*) FROM app_config WHERE config_key='shared_data_revision'")
            assert cur.fetchone() == (1,)
    untouched = (None if operation == 'backfill' else 1, 'OLD', None if operation == 'rename_empty' else 'UNIT')
    changed = {'backfill':(1,'OLD','UNIT'), 'structure':(2,'DEST','UNIT'),
               'rename_named':(1,'OLD','NEW UNIT'), 'rename_empty':(1,'OLD','NEW UNIT'),
               'rename_to_empty':(1,'OLD',None)}[operation]
    expected = [untouched, untouched]; expected[0 if month else 1] = changed
    assert rows == expected


def _padmin_lot18_setup(isolated_mysql, monkeypatch):
    settings, connect, params, _ = _organigram_lot18_setup(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE ref_position_admin (id INT PRIMARY KEY, libelle VARCHAR(50) UNIQUE, compte_comme_present BOOL, ordre_tri INT) ENGINE=InnoDB')
            cur.execute("INSERT INTO ref_position_admin VALUES (9,'OLD POSITION',1,2)")
            cur.execute('ALTER TABLE carriere_situation ADD position_admin_libelle VARCHAR(50), ADD position_admin_id INT NULL')
            cur.execute("UPDATE carriere_situation SET position_admin_libelle='OLD POSITION',position_admin_id=NULL")
    _audit_fixture_schema(connect,params)
    return settings, connect, params


@pytest.mark.parametrize('month', [None, '2026-09'])
def test_scoped_reference_lot18_padmin_rename_updates_the_matching_month_and_reference(isolated_mysql, monkeypatch, month):
    from sme_gend.repositories.mysql_bundle_repositories import MySQLPadminRepository
    settings, connect, params = _padmin_lot18_setup(isolated_mysql, monkeypatch)
    repository = MySQLPadminRepository(replace(settings, mysql_view_mois=month))
    repository.update('OLD POSITION', 'NEW POSITION', False, 4, expected=repository.fetch_all()[0])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT position_admin_libelle,position_admin_id FROM carriere_situation ORDER BY id')
            # A catalogue rename is global, including all historical months.
            expected = [('NEW POSITION',9),('NEW POSITION',9)]
            assert list(cur.fetchall()) == expected
            cur.execute('SELECT id,libelle,compte_comme_present,ordre_tri FROM ref_position_admin')
            assert cur.fetchall() == ((9,'NEW POSITION',0,4),)


def test_scoped_reference_lot18_padmin_second_update_failure_rolls_back_the_entire_rename(isolated_mysql, monkeypatch):
    from sme_gend.repositories.mysql_bundle_repositories import MySQLPadminRepository
    settings, connect, params = _padmin_lot18_setup(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TRIGGER padmin_veto BEFORE UPDATE ON carriere_situation FOR EACH ROW BEGIN IF NEW.position_admin_id IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic position FK veto'; END IF; END")
    repository = MySQLPadminRepository(replace(settings, mysql_view_mois='2026-09'))
    with pytest.raises(pymysql.err.OperationalError, match='synthetic position FK veto'):
        repository.update('OLD POSITION', 'NEW POSITION', False, 4, expected=repository.fetch_all()[0])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT position_admin_libelle,position_admin_id FROM carriere_situation ORDER BY id')
            assert cur.fetchall() == (('OLD POSITION',None),('OLD POSITION',None))
            cur.execute('SELECT id,libelle,compte_comme_present,ordre_tri FROM ref_position_admin')
            assert cur.fetchall() == ((9,'OLD POSITION',1,2),)



@pytest.mark.parametrize('month', [None, '2026-09'])
@pytest.mark.parametrize('derived_merge', ['on', 'off'])
def test_scoped_reference_lot18_write_scope_matches_read_scope_without_session_changes(isolated_mysql, monkeypatch, month, derived_merge):
    from sme_gend.config.liste_generale_scope import carriere_scope_for_settings,carriere_write_scope_for_settings
    settings, connect, params, _ = _organigram_lot18_setup(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois=month)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO carriere_situation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',[
                (3,50,None,1,'2026-09-01',None,1,'OLD',10,'UNIT'),
                (4,51,2,1,'2026-08-01',None,1,'OLD',10,'UNIT'),
                (5,52,1,0,None,None,1,'OLD',10,'UNIT'),
                (6,53,None,1,'2026-09-01','2026-09-29',1,'OLD',10,'UNIT'),
                (7,54,None,0,'2026-09-01',None,1,'OLD',10,'UNIT'),
                (8,55,None,1,'2026-10-01',None,1,'OLD',10,'UNIT'),
                (9,56,1,0,None,None,1,'OLD',10,'UNIT'),
                (10,56,None,1,'2026-09-01',None,1,'OLD',10,'UNIT'),
            ])
            cur.execute('SELECT @@SESSION.optimizer_switch');original=cur.fetchone()[0]
            try:
                cur.execute('SET SESSION optimizer_switch=%s',('derived_merge='+derived_merge,))
                cur.execute('SELECT @@SESSION.optimizer_switch');applied=cur.fetchone()[0]
                read_sql,read_params=carriere_scope_for_settings(settings)
                cur.execute('SELECT c.id FROM carriere_situation c WHERE '+read_sql+' ORDER BY c.id',read_params)
                expected=cur.fetchall()
                assert expected and len(expected)<10
                write_sql,write_params=carriere_write_scope_for_settings(settings)
                cur.execute('UPDATE carriere_situation c SET subdivision_arme_libelle=%s WHERE '+write_sql,('MATCHED',)+write_params)
                assert cur.rowcount==len(expected)
                cur.execute("SELECT id FROM carriere_situation WHERE subdivision_arme_libelle='MATCHED' ORDER BY id")
                assert cur.fetchall()==expected
                cur.execute('SELECT @@SESSION.optimizer_switch');assert cur.fetchone()[0]==applied
            finally:
                cur.execute('SET SESSION optimizer_switch=%s',(original,))



_REFERENCE_LOT19_OPERATIONS = (
    'grade_insert','grade_update','grade_delete',
    'structure_insert','structure_update','structure_delete','structure_rename',
    'fonction_insert','fonction_update','fonction_delete',
    'padmin_insert','padmin_update','padmin_delete',
    'organigram_order_structures','organigram_order_subdivisions','organigram_move',
    'organigram_attach','organigram_add','organigram_rename','organigram_delete',
    'organigram_delete_structure','organigram_delete_grade',
    'links_structure','links_fonction','links_ensure','links_backfill',
)


def _audit_fixture_schema(connect, params):
    """Only the disposable fixture schema; business mutations now require audit storage."""
    from sme_gend.repositories.mysql_common import _split_sql_statements
    sql=(Path(__file__).resolve().parents[2]/'data/schema/mysql_app_config.sql').read_text(encoding='utf-8')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            for statement in _split_sql_statements(sql):cursor.execute(statement)


def _reference_lot19_setup(isolated_mysql,monkeypatch):
    settings,connect,params,_=_organigram_lot18_setup(isolated_mysql,monkeypatch)
    from sme_gend.repositories.mysql_bundle_repositories import (
        MySQLGradeRepository,MySQLStructureRepository,MySQLFonctionsRepository,MySQLPadminRepository)
    from sme_gend.repositories.mysql_fonction_structure_repository import MySQLFonctionStructureRepository
    from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
            cur.execute('ALTER TABLE ref_structure MODIFY id INT NOT NULL AUTO_INCREMENT, ADD designation VARCHAR(100), ADD famille_unite VARCHAR(30), ADD UNIQUE KEY lot22_structure_code(code)')
            cur.execute("UPDATE ref_structure SET designation=code,famille_unite='test'")
            cur.execute("INSERT INTO ref_structure VALUES(3,'FREE',3,'FREE','test')")
            cur.execute("INSERT INTO ref_subdivision_arme VALUES(11,1,'UNIT TWO',2,1),(12,2,'FREE UNIT',1,1),(13,3,'FREE CHILD',1,1)")
            cur.execute('CREATE TABLE ref_grade(id INT AUTO_INCREMENT PRIMARY KEY,code VARCHAR(20) UNIQUE,libelle VARCHAR(80),pref_ord INT,effectif_g INT) ENGINE=InnoDB')
            cur.execute("INSERT INTO ref_grade VALUES(1,'GOLD','GRADE',1,2),(3,'GFREE','UNUSED',3,0)")
            cur.execute('CREATE TABLE ref_fonction(id INT AUTO_INCREMENT PRIMARY KEY,libelle VARCHAR(100) UNIQUE) ENGINE=InnoDB')
            cur.execute("INSERT INTO ref_fonction VALUES(1,'FUNC'),(2,'FNEW'),(3,'FREE FUNCTION')")
            cur.execute('CREATE TABLE ref_fonction_structure(structure_id INT,fonction_id INT,ordre_tri INT,PRIMARY KEY(structure_id,fonction_id)) ENGINE=InnoDB')
            cur.execute('INSERT INTO ref_fonction_structure VALUES(1,1,0)')
            cur.execute('CREATE TABLE ref_position_admin(id INT AUTO_INCREMENT PRIMARY KEY,libelle VARCHAR(50) UNIQUE,compte_comme_present BOOL,ordre_tri INT) ENGINE=InnoDB')
            cur.execute("INSERT INTO ref_position_admin VALUES(1,'OLD POSITION',1,2),(3,'FREE POSITION',0,3)")
            cur.execute('CREATE TABLE agent_affectation(id INT PRIMARY KEY,fonction_libelle VARCHAR(100)) ENGINE=InnoDB')
            cur.execute("INSERT INTO agent_affectation VALUES(1,'FUNC')")
            cur.execute('ALTER TABLE carriere_situation ADD grade_id INT,ADD grade_libelle VARCHAR(80),ADD fonction_id INT,ADD fonction_libelle VARCHAR(100),ADD position_admin_id INT,ADD position_admin_libelle VARCHAR(50)')
            cur.execute("UPDATE carriere_situation SET grade_id=1,grade_libelle='GRADE',fonction_id=1,fonction_libelle='FUNC',position_admin_id=NULL,position_admin_libelle='OLD POSITION'")
    repos=dict(grade=MySQLGradeRepository(settings),structure=MySQLStructureRepository(settings),
        fonction=MySQLFonctionsRepository(settings),padmin=MySQLPadminRepository(settings),
        links=MySQLFonctionStructureRepository(settings),organigram=MySQLOrganigramRepository(settings))
    snapshots={kind:{getattr(row,'code',getattr(row,'id',None)):row for row in repos[kind].fetch_all()}
               for kind in ('grade','structure','fonction')}
    padmin_rows = {row.libelle: row for row in repos['padmin'].fetch_all()}
    linked_structure=repos['links'].load_links_structure_snapshot('OLD')
    linked_fonction=repos['links'].load_links_fonction_snapshot(1)
    order_structures=repos['organigram'].load_order_structures_snapshot()
    order_subdivisions=repos['organigram'].load_order_subdivisions_snapshot(1)
    actions=dict(
        grade_insert=lambda:repos['grade'].insert('GNEW','NEW GRADE',4,0),
        grade_update=lambda:repos['grade'].update('GOLD','NEW GRADE',5,7,expected=snapshots['grade']['GOLD']),
        grade_delete=lambda:repos['grade'].delete('GFREE',expected=snapshots['grade']['GFREE']),
        structure_insert=lambda:repos['structure'].insert('NEW','NEW STRUCTURE',4,famille_unite='test'),
        structure_update=lambda:repos['structure'].update('OLD','NEW STRUCTURE',7,famille_unite='test',expected=snapshots['structure']['OLD']),
        structure_delete=lambda:repos['structure'].delete('FREE',expected=snapshots['structure']['FREE']),
        structure_rename=lambda:repos['structure'].rename_code('OLD','RENAMED',expected=snapshots['structure']['OLD']),
        fonction_insert=lambda:repos['fonction'].insert('NEW FUNCTION'),
        fonction_update=lambda:repos['fonction'].update(1,'FUNC','RENAMED FUNCTION',expected=snapshots['fonction'][1]),
        fonction_delete=lambda:repos['fonction'].delete(3,expected=snapshots['fonction'][3]),
        padmin_insert=lambda:repos['padmin'].insert('NEW POSITION',False,4),
        padmin_update=lambda:repos['padmin'].update('OLD POSITION','NEW POSITION',False,4,expected=padmin_rows['OLD POSITION']),
        padmin_delete=lambda:repos['padmin'].delete('FREE POSITION',expected=padmin_rows['FREE POSITION']),
        organigram_order_structures=lambda:repos['organigram'].reorder_structures(['DEST','OLD'],expected=order_structures),
        organigram_order_subdivisions=lambda:repos['organigram'].reorder_subdivisions(1,[11,10],expected=order_subdivisions),
        organigram_move=lambda:repos['organigram'].move_subdivision_to_structure(10,2,'DEST'),
        organigram_attach=lambda:repos['organigram'].attach_subdivision_from_data(1,'UNIT'),
        organigram_add=lambda:repos['organigram'].add_subdivision(1,'UNIT'),
        organigram_rename=lambda:repos['organigram'].rename_subdivision(10,'RENAMED UNIT','OLD'),
        organigram_delete=lambda:repos['organigram'].delete_subdivision(12),
        organigram_delete_structure=lambda:repos['organigram'].safe_delete_structure('FREE',expected=snapshots['structure']['FREE']),
        organigram_delete_grade=lambda:repos['organigram'].safe_delete_grade('GFREE',expected=snapshots['grade']['GFREE']),
        links_structure=lambda:repos['links'].set_links_for_structure('OLD',['FNEW'],expected=linked_structure),
        links_fonction=lambda:repos['links'].set_links_for_fonction(1,['DEST'],expected=linked_fonction),
        links_ensure=lambda:repos['links'].ensure_link('DEST','FNEW'),
        links_backfill=lambda:repos['links'].backfill_from_carriere(),
    )
    _audit_fixture_schema(connect,params)
    return settings,connect,params,repos,actions


def _reference_lot19_prepare(operation,connect,params):
    if operation=='links_backfill':
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM ref_fonction_structure')


def _reference_lot19_snapshot(connect,params):
    result={}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for table,order in [('ref_grade','id'),('ref_structure','id'),('ref_fonction','id'),
                ('ref_position_admin','id'),('ref_subdivision_arme','id'),
                ('ref_fonction_structure','structure_id,fonction_id'),('carriere_situation','id'),('agent_affectation','id')]:
                cur.execute('SELECT * FROM '+table+' ORDER BY '+order);result[table]=cur.fetchall()
    return result


@pytest.mark.parametrize('operation',_REFERENCE_LOT19_OPERATIONS)
def test_reference_lot19_each_direct_write_publishes_one_committed_revision(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync
    from sme_gend.repositories.mysql_connection import has_import_transaction
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    _reference_lot19_prepare(operation,connect,params)
    before=_reference_lot19_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    calls=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):
        calls.append(has_import_transaction(settings));return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    actions[operation]()
    assert _reference_lot19_snapshot(connect,params)!=before
    assert data_sync.read_data_revision(settings)!=revision
    assert calls==[True]


@pytest.mark.parametrize('operation',_REFERENCE_LOT19_OPERATIONS)
def test_reference_lot19_notification_failure_rolls_back_all_direct_changes(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    _reference_lot19_prepare(operation,connect,params)
    before=_reference_lot19_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    def failed(*args,**kwargs):raise OSError('synthetic revision unavailable')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    with pytest.raises(OSError,match='synthetic revision unavailable'):actions[operation]()
    assert _reference_lot19_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision



@pytest.mark.parametrize('operation',['grade_same','structure_same','padmin_same','existing_link','backfill_same','missing_grade','missing_function'])
def test_reference_lot19_noop_does_not_publish_or_invalidate(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync,shared_reference_write
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    def unexpected(*args,**kwargs):pytest.fail('No data changed: no revision or invalidation expected')
    monkeypatch.setattr(data_sync,'publish_data_change',unexpected)
    monkeypatch.setattr(shared_reference_write,'_invalidate_reference_caches',unexpected)
    if operation=='grade_same':repos['grade'].update('GOLD','GRADE',1,2,expected=next(row for row in repos['grade'].fetch_all() if row.code=='GOLD'))
    elif operation=='structure_same':repos['structure'].update('OLD','OLD',1,famille_unite='test',expected=repos['structure'].fetch_by_code('OLD'))
    elif operation=='padmin_same':repos['padmin'].update('OLD POSITION','OLD POSITION',True,2,
        expected=next(r for r in repos['padmin'].fetch_all() if r.libelle=='OLD POSITION'))
    elif operation=='existing_link':repos['links'].ensure_link('OLD','FUNC')
    elif operation=='backfill_same':assert repos['links'].backfill_from_carriere()==0
    elif operation=='missing_grade':
        with pytest.raises(ValueError,match='Version du référentiel indisponible'):repos['grade'].delete('ABSENT')
    else:
        with pytest.raises(ValueError,match='Version du référentiel indisponible'):repos['fonction'].delete(404)
    assert _reference_lot19_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision


@pytest.mark.parametrize('commit',[False,True])
def test_reference_lot19_observer_sees_revision_with_data_only_after_commit(isolated_mysql,monkeypatch,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services import data_sync,liste_generale_cache
    from sme_gend.padmin_present import load_padmin_presence_index
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    keys=[(settings,'synthetic'),(replace(settings,mysql_view_mois='2026-09'),'synthetic')]
    for key in keys:assert liste_generale_cache.get_liste_rows_by_key(key,lambda:['OLD'])==['OLD']
    assert load_padmin_presence_index(settings).compte_comme_present('OLD POSITION') is True
    observer=connect(**params)
    def observed():
        with observer.cursor() as cur:
            cur.execute("SELECT compte_comme_present FROM ref_position_admin WHERE libelle='OLD POSITION'");flag=cur.fetchone()[0]
            cur.execute("SELECT payload FROM app_config WHERE config_key='shared_data_revision'");row=cur.fetchone()
        return flag,row[0] if row else None
    original=observed()
    try:
        try:
            with mysql_import_transaction(settings):
                repos['padmin'].update('OLD POSITION','OLD POSITION',False,2,
                                       expected=next(r for r in repos['padmin'].fetch_all() if r.libelle=='OLD POSITION'))
                assert observed()==original
                assert load_padmin_presence_index(settings).compte_comme_present('OLD POSITION') is True
                for key in keys:assert liste_generale_cache.get_liste_rows_by_key(key,lambda:['UNEXPECTED'])==['OLD']
                if not commit:raise ValueError('synthetic outer transaction aborted')
        except ValueError:
            assert not commit
        flag,revision=observed()
        if commit:
            assert flag==0 and revision is not None
            assert load_padmin_presence_index(settings).compte_comme_present('OLD POSITION') is False
            for key in keys:assert liste_generale_cache.get_liste_rows_by_key(key,lambda:['FRESH'])==['FRESH']
        else:
            assert (flag,revision)==original
            assert load_padmin_presence_index(settings).compte_comme_present('OLD POSITION') is True
            for key in keys:assert liste_generale_cache.get_liste_rows_by_key(key,lambda:['UNEXPECTED'])==['OLD']
    finally:observer.close()


def test_reference_lot19_handled_failure_in_outer_transaction_does_not_keep_partial_reference_changes(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('CREATE TABLE outer_probe(id INT PRIMARY KEY) ENGINE=InnoDB')
    def fail(*args,**kwargs):raise OSError('synthetic publication failure')
    monkeypatch.setattr(data_sync,'publish_data_change',fail)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO outer_probe VALUES(1)')
        with pytest.raises(OSError,match='publication failure'):actions['links_structure']()
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO outer_probe VALUES(2)')
    assert _reference_lot19_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM outer_probe ORDER BY id');assert cur.fetchall()==((1,),(2,))


def test_reference_lot19_caught_nested_failure_aborts_the_reference_batch(isolated_mysql,monkeypatch):
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params);revision=read_data_revision(settings)
    with pytest.raises(RuntimeError,match='imbriquée'):
        with shared_reference_write(settings):
            actions['grade_update']()
            try:repos['fonction'].update(1,'FUNC','FNEW',expected=next(row for row in repos['fonction'].fetch_all() if row.id==1))
            except pymysql.err.IntegrityError:pass
    assert _reference_lot19_snapshot(connect,params)==before
    assert read_data_revision(settings)==revision


@pytest.mark.parametrize('abort_first',[False,True])
def test_reference_lot19_simultaneous_writers_have_independent_transaction_state(isolated_mysql,monkeypatch,abort_first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    barrier=Barrier(2);published=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):
        published.append(True);return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    def writer(first):
        try:
            barrier.wait(timeout=5)
            with shared_reference_write(settings):
                repos['grade'].update('GOLD' if first else 'GFREE','FIRST' if first else 'SECOND',1,1,expected=next(row for row in repos['grade'].fetch_all() if row.code==('GOLD' if first else 'GFREE')))
                if first and abort_first:raise ValueError('synthetic writer aborted')
        except ValueError:
            assert first and abort_first
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(writer,first) for first in (True,False)]
        for job in jobs:job.result(timeout=10)
    assert len(published)==(1 if abort_first else 2)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT libelle FROM ref_grade ORDER BY id')
            assert cur.fetchall()==(('GRADE' if abort_first else 'FIRST',),('SECOND',))
    assert data_sync.read_data_revision(settings)


def test_reference_lot19_savepoint_rollback_failure_is_explicit_and_outer_rollback_still_runs(isolated_mysql,monkeypatch,caplog):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services import data_sync,shared_reference_write
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params)
    original_savepoint=shared_reference_write._savepoint
    def fail_rollback(settings,command,name):
        if command=='ROLLBACK TO SAVEPOINT':raise OSError('SYNTHETIC PRIVATE ROLLBACK DETAIL')
        return original_savepoint(settings,command,name)
    def fail_publish(*args,**kwargs):raise OSError('synthetic revision unavailable')
    monkeypatch.setattr(shared_reference_write,'_savepoint',fail_rollback)
    monkeypatch.setattr(data_sync,'publish_data_change',fail_publish)
    with pytest.raises(RuntimeError,match='Annulation du référentiel impossible') as raised:
        with mysql_import_transaction(settings):actions['links_structure']()
    assert isinstance(raised.value.__cause__,OSError)
    assert _reference_lot19_snapshot(connect,params)==before
    assert not data_sync.read_data_revision(settings)
    assert 'SYNTHETIC PRIVATE ROLLBACK DETAIL' not in caplog.text


@pytest.mark.parametrize('operation',['duplicate_function','partial_links'])
def test_reference_lot19_business_error_rolls_back_without_notification(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params)
    def unexpected(*args,**kwargs):pytest.fail('Failed reference write must not notify')
    monkeypatch.setattr(data_sync,'publish_data_change',unexpected)
    with pytest.raises(ValueError):
        if operation=='duplicate_function':repos['fonction'].insert('FUNC')
        else:repos['links'].set_links_for_fonction(1,['DEST','MISSING'],expected=repos['links'].load_links_fonction_snapshot(1))
    assert _reference_lot19_snapshot(connect,params)==before
    assert not data_sync.read_data_revision(settings)



def test_reference_lot19_reference_batch_notifies_once_for_several_repositories(isolated_mysql,monkeypatch):
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    published=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):
        published.append(True);return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    with shared_reference_write(settings):
        actions['grade_update']()
        actions['structure_update']()
        assert not published
    assert published==[True] and data_sync.read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT libelle FROM ref_grade WHERE code='GOLD'");assert cur.fetchone()==('NEW GRADE',)
            cur.execute("SELECT designation FROM ref_structure WHERE code='OLD'");assert cur.fetchone()==('NEW STRUCTURE',)



def _business_lot20_setup(isolated_mysql,monkeypatch):
    from sme_gend.repositories import mysql_common
    from sme_gend.repositories.mysql_bundle_repositories import MySQLGainPerteRepository
    from sme_gend.services import workflow_task_service as workflow
    from sme_gend.models.gain_perte import GainPerteRow
    from sme_gend.services.pre_closure_service import PreClosureReport,PreClosureCheck
    settings,connect,params=isolated_mysql
    monkeypatch.setattr(mysql_common,'ensure_mysql_schema',lambda settings:None)
    monkeypatch.setattr(workflow,'resolve_audit_actor',lambda:'synthetic actor')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE agent(id INT PRIMARY KEY,legacy_access_id VARCHAR(40) UNIQUE) ENGINE=InnoDB')
            cur.execute("INSERT INTO agent VALUES(1,'legacy-1'),(2,'legacy-2')")
            cur.execute('CREATE TABLE agent_identity_alias(alias_legacy_id VARCHAR(40) PRIMARY KEY,canonical_agent_id INT NOT NULL) ENGINE=InnoDB')
            cur.execute('CREATE TABLE sme_periode(id INT PRIMARY KEY,annee_mois VARCHAR(7)) ENGINE=InnoDB')
            cur.execute("INSERT INTO sme_periode VALUES(1,'2026-09'),(2,'2026-10')")
            cur.execute("ALTER TABLE sme_periode ADD statut VARCHAR(32) DEFAULT 'Ouvert'")
            cur.execute('CREATE TABLE mouvement_effectif(id INT AUTO_INCREMENT PRIMARY KEY,sme_periode_id INT,agent_id INT,identifiant_ligne VARCHAR(40),grade_snapshot VARCHAR(50),nom_snapshot VARCHAR(100),type_mouvement VARCHAR(30),categorie_mouvement VARCHAR(30),motif VARCHAR(255),cdsme_derive VARCHAR(10),observ TEXT,date_reference DATE,import_lot_id BIGINT NULL) ENGINE=InnoDB')
            cur.execute("INSERT INTO mouvement_effectif(id,sme_periode_id,agent_id,identifiant_ligne,grade_snapshot,nom_snapshot,type_mouvement,categorie_mouvement,motif,cdsme_derive,observ,date_reference) VALUES(10,1,1,'legacy-1','GND','SYNTHETIC','PERTE','PERTE','RETRAITE','092026','RETRAITE','2026-09-30'),(11,1,1,'legacy-1','GND','OTHER SEPTEMBER','GAIN','GAIN','ARRIVEE','092026','ARRIVEE','2026-09-30'),(12,2,1,'legacy-1','GND','OTHER OCTOBER','PERTE','PERTE','RETRAITE','102026','RETRAITE','2026-10-31')")
            schema=(Path(__file__).resolve().parents[2]/'data/schema/mysql_workflow.sql').read_text(encoding='utf-8')
            for statement in mysql_common._split_sql_statements(schema):cur.execute(statement)
            cur.execute("INSERT INTO workflow_task(id,mois,task_kind,status,priority,title,detail) VALUES(1,'2026-09','manual','open','normal','SYNTHETIC TASK','DETAIL'),(2,'2026-09','manual','done','normal','DONE TASK','DETAIL'),(3,'2026-10','manual','open','normal','OTHER MONTH','DETAIL')")
            cur.execute('CREATE TABLE business_outer(id INT PRIMARY KEY) ENGINE=InnoDB')
    repo=MySQLGainPerteRepository(settings);tasks=workflow.WorkflowTaskService(settings)
    insert=GainPerteRow(id='legacy-2',grade='GND',nom='SYNTHETIC NEW',objet='GAIN',cdsme='092026',observ='ARRIVEE',mois_ym='2026-09',date_reference='2026-09-30')
    original=next(row for row in repo.fetch_all() if row.movement_id==10)
    update=replace(original,id='legacy-2',grade='SGT',nom='SYNTHETIC MOVED',objet='PERTE',cdsme='102026',observ='RETRAITE',mois_ym='2026-10',date_reference='2026-10-31')
    report=PreClosureReport('2026-09','2026-10',(
        PreClosureCheck('first','warn','FIRST CHECK','FIRST DETAIL'),
        PreClosureCheck('second','block','SECOND CHECK','SECOND DETAIL'),
        PreClosureCheck('ok','ok','OK','OK'),
    ))
    actions=dict(
        gain_insert=lambda:repo.insert_row(insert),
        gain_update=lambda:repo.update_row(update,where_id='legacy-1',where_cdsme='092026',movement_id=10),
        gain_delete=lambda:repo.delete_row('legacy-1','092026',movement_id=10,expected=original),
        task_create=lambda:tasks.create_task(title='NEW TASK',detail='NEW DETAIL',mois='2026-09'),
        task_resolve=lambda:tasks.resolve_task(1,expected=next(row for row in tasks.list_open() if row.id==1)),
        task_sync=lambda:tasks.sync_from_pre_closure(report),
    )
    _audit_fixture_schema(connect,params)
    return settings,connect,params,repo,tasks,report,insert,actions


def _business_lot20_snapshot(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM mouvement_effectif ORDER BY id');movements=cur.fetchall()
            cur.execute('SELECT id,mois,task_kind,status,priority,title,detail,assigned_role,created_by,resolved_by,resolved_at FROM workflow_task ORDER BY id');tasks=cur.fetchall()
    return movements,tasks


@pytest.mark.parametrize('operation',['gain_insert','gain_update','gain_delete','task_create','task_resolve','task_sync'])
def test_business_lot20_data_and_revision_are_committed_together(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync
    from sme_gend.repositories.mysql_connection import has_import_transaction
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    original=data_sync.publish_data_change;published=[]
    def publish(settings,**kwargs):
        published.append(has_import_transaction(settings));return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    result=actions[operation]()
    after=_business_lot20_snapshot(connect,params)
    assert after!=before
    current=data_sync.read_data_revision(settings)
    assert current!=revision and published==[True]
    assert [item for item in after[0] if item[0] in (11,12)]==[item for item in before[0] if item[0] in (11,12)]
    if operation=='task_create':assert result and result>0
    if operation=='task_resolve':assert result is True


@pytest.mark.parametrize('operation',['gain_insert','gain_update','gain_delete','task_create','task_resolve','task_sync'])
def test_business_lot20_revision_failure_rolls_back_the_operation(isolated_mysql,monkeypatch,operation):
    from sme_gend.services import data_sync
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    original=OSError('synthetic revision failure')
    def failed(*args,**kwargs):raise original
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    with pytest.raises(RuntimeError if operation.startswith('task_') else OSError) as raised:actions[operation]()
    cause=raised.value
    while cause is not original and cause.__cause__ is not None:cause=cause.__cause__
    assert cause is original
    assert _business_lot20_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision


@pytest.mark.parametrize('operation',['list','create','resolve'])
def test_business_lot20_workflow_does_not_implicitly_commit_the_callers_transaction(isolated_mysql,monkeypatch,operation):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    with pytest.raises(ValueError,match='synthetic outer abort'):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:cur.execute('INSERT INTO business_outer VALUES(1)')
            if operation=='list':tasks.list_open()
            elif operation=='create':tasks.create_task(title='TRANSACTIONAL TASK')
            else:tasks.resolve_task(1,expected=next(row for row in tasks.list_open() if row.id==1))
            raise ValueError('synthetic outer abort')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM business_outer');assert cur.fetchall()==()
    assert _business_lot20_snapshot(connect,params)==before


def test_business_lot20_preclosure_deduplication_is_not_limited_to_first_100_tasks(isolated_mysql,monkeypatch):
    from sme_gend.services.pre_closure_service import PreClosureReport,PreClosureCheck
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    title='FIRST CHECK';kind='pre_closure_first'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE workflow_task SET task_kind=%s,title=%s WHERE id=1',(kind,title))
            cur.executemany("INSERT INTO workflow_task(mois,task_kind,status,priority,title) VALUES('2026-09','manual','open','normal',%s)",[(f'SYNTHETIC {index}',) for index in range(125)])
    tasks.sync_from_pre_closure(PreClosureReport('2026-09','2026-10',(PreClosureCheck('first','warn',title,'DETAIL'),)))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE mois='2026-09' AND task_kind=%s AND title=%s AND status='open'",(kind,title))
            assert cur.fetchone()==(1,)


def test_business_lot20_preclosure_batch_is_not_partially_saved_when_second_task_fails(isolated_mysql,monkeypatch):
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TRIGGER workflow_veto BEFORE INSERT ON workflow_task FOR EACH ROW BEGIN IF NEW.task_kind='pre_closure_second' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic second task veto'; END IF; END")
    with pytest.raises(RuntimeError):tasks.sync_from_pre_closure(report)
    assert _business_lot20_snapshot(connect,params)==before



@pytest.mark.parametrize('operation',['list','count','create','resolve'])
def test_business_lot20_workflow_storage_errors_are_explicit_and_do_not_log_details(isolated_mysql,monkeypatch,caplog,operation):
    from contextlib import contextmanager
    from sme_gend.services.workflow_task_service import WorkflowTaskError
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    displayed=next(row for row in tasks.list_open() if row.id==1)
    original=OSError('SYNTHETIC PRIVATE STORAGE DETAIL')
    monkeypatch.setattr(tasks,'_ensure_table',lambda:True)
    @contextmanager
    def failed(**kwargs):raise original;yield
    monkeypatch.setattr(tasks._repo._mc,'connect',failed)
    with pytest.raises(WorkflowTaskError) as raised:
        if operation=='list':tasks.list_open()
        elif operation=='count':tasks.count_open()
        elif operation=='create':tasks.create_task(title='FAILED')
        else:tasks.resolve_task(1,expected=displayed)
    assert raised.value.__cause__ is original
    assert _business_lot20_snapshot(connect,params)==before
    assert 'SYNTHETIC PRIVATE STORAGE DETAIL' not in caplog.text
    assert 'SYNTHETIC PRIVATE STORAGE DETAIL' not in str(raised.value)


def test_business_lot20_missing_workflow_table_is_not_created_inside_an_active_transaction(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction
    from sme_gend.services.workflow_task_service import WorkflowTaskError
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE workflow_task')
    with pytest.raises(WorkflowTaskError):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:cur.execute('INSERT INTO business_outer VALUES(1)')
            tasks.create_task(title='UNEXPECTED')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM business_outer');assert cur.fetchall()==()
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='workflow_task'")
            assert cur.fetchone()==(0,)


def test_business_lot20_missing_workflow_table_can_still_initialize_standalone(isolated_mysql,monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE workflow_task')
    identifier=tasks.create_task(title='LAZY INITIALIZED',mois='2026-09')
    assert identifier and read_data_revision(settings)
    assert [task.title for task in tasks.list_open()]==['LAZY INITIALIZED']


def test_business_lot20_existing_workflow_reads_do_not_execute_ddl_or_repeat_metadata_lookup(isolated_mysql,monkeypatch):
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    statements=[];original=pymysql.cursors.Cursor.execute
    def observed(cursor,query,args=None):
        statements.append(query);return original(cursor,query,args)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',observed)
    assert len(tasks.list_open())==2
    assert len(tasks.list_open())==2
    assert tasks.count_open(mois='2026-09')==1
    sql=[' '.join(text.split()).upper() for text in statements if isinstance(text,str)]
    assert not any(text.startswith(('CREATE','ALTER','SET NAMES')) for text in sql)
    assert sum('INFORMATION_SCHEMA.TABLES' in text for text in sql)==1


def test_business_lot20_workflow_count_is_not_a_limited_page(isolated_mysql,monkeypatch):
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO workflow_task(mois,task_kind,title) VALUES('2026-09','manual',%s)",[(f'COUNT {index}',) for index in range(125)])
    assert tasks.count_open(mois='2026-09')==126
    assert tasks.count_open()==127
    assert len(tasks.list_open(mois='2026-09'))==50


@pytest.mark.parametrize('mode',['same_report','long_title','already_resolved'])
def test_business_lot20_workflow_sync_only_recreates_a_still_required_resolved_check(isolated_mysql,monkeypatch,mode):
    from sme_gend.services.data_sync import read_data_revision
    from sme_gend.services.pre_closure_service import PreClosureReport,PreClosureCheck
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    title='X'*300 if mode=='long_title' else 'FIRST CHECK'
    report=PreClosureReport('2026-09','2026-10',(PreClosureCheck('first','warn',title,'DETAIL'),))
    tasks.sync_from_pre_closure(report)
    revision=read_data_revision(settings)
    if mode=='already_resolved':
        created=[item for item in tasks.list_open() if item.task_kind=='pre_closure_first']
        assert len(created)==1 and tasks.resolve_task(created[0].id,expected=created[0])
        revision=read_data_revision(settings)
    tasks.sync_from_pre_closure(report)
    assert (read_data_revision(settings)!=revision)==(mode=='already_resolved')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE task_kind='pre_closure_first' AND status='open'")
            assert cur.fetchone()==(1,)


def test_business_lot20_concurrent_preclosure_clients_create_one_open_task(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services.workflow_task_service import WorkflowTaskService
    from sme_gend.services.pre_closure_service import PreClosureReport,PreClosureCheck
    from sme_gend.services import data_sync
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    report=PreClosureReport('2026-09','2026-10',(PreClosureCheck('first','warn','ONE CHECK','DETAIL'),))
    gate=Barrier(2);published=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):published.append(True);return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    def synchronize():gate.wait(timeout=5);WorkflowTaskService(settings).sync_from_pre_closure(report)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(synchronize) for _ in range(2)]
        for job in jobs:job.result(timeout=12)
    assert published==[True]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE task_kind='pre_closure_first' AND status='open'")
            assert cur.fetchone()==(1,)


def test_business_lot20_concurrent_resolution_has_one_winner_and_one_revision(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services.workflow_task_service import WorkflowTaskService
    from sme_gend.services import data_sync
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    gate=Barrier(2);published=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):published.append(True);return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    shown=next(row for row in tasks.list_open() if row.id==1)
    def resolve():gate.wait(timeout=5);return WorkflowTaskService(settings).resolve_task(1,expected=shown)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(resolve) for _ in range(2)]
        assert sorted(job.result(timeout=12) for job in jobs)==[False,True]
    assert published==[True]


@pytest.mark.parametrize('same_month',[False,True])
def test_business_lot20_gain_batch_notifies_once_and_keeps_target_months(isolated_mysql,monkeypatch,same_month):
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services import data_sync
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    facade=GainPerteRepository.__new__(GainPerteRepository);facade._settings=settings;facade._impl=repo
    other=replace(row,id='legacy-1',objet='PERTE',observ='DECES',mois_ym='2026-09' if same_month else '2026-10',cdsme='092026' if same_month else '102026')
    published=[];original=data_sync.publish_data_change
    def publish(settings,**kwargs):published.append(True);return original(settings,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    facade.insert_rows([row,other])
    assert published==[True]
    payload=AppConfigRepository(settings).get_json(data_sync.KEY_DATA_REVISION)
    assert payload['reason']=='gain_perte' and payload['mois']==('2026-09' if same_month else None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sme_periode_id FROM mouvement_effectif WHERE id>12 ORDER BY id')
            assert cur.fetchall()==((1,),(1 if same_month else 2,))


def test_business_lot20_gain_batch_second_failure_leaves_no_partial_movement(isolated_mysql,monkeypatch):
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    facade=GainPerteRepository.__new__(GainPerteRepository);facade._settings=settings;facade._impl=repo
    with pytest.raises(ValueError):facade.insert_rows([row,replace(row,id='missing')])
    assert _business_lot20_snapshot(connect,params)==before
    assert not read_data_revision(settings)


@pytest.mark.parametrize('operation',['gain_insert','gain_update','gain_delete','task_create','task_resolve','task_sync'])
@pytest.mark.parametrize('commit',[False,True])
def test_business_lot20_second_client_sees_data_and_revision_after_real_commit(isolated_mysql,monkeypatch,operation,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    def revision():
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM app_config WHERE config_key='shared_data_revision'");value=cur.fetchone()
                return value[0] if value else None
    initial=revision()
    try:
        with mysql_import_transaction(settings):
            actions[operation]()
            assert _business_lot20_snapshot(connect,params)==before and revision()==initial
            if not commit:raise ValueError('synthetic caller aborted')
    except ValueError:
        assert not commit
    assert (_business_lot20_snapshot(connect,params)!=before)==commit
    assert (revision()!=initial)==commit


@pytest.mark.parametrize('operation',['gain_insert','gain_update','gain_delete','task_create','task_resolve','task_sync'])
def test_business_lot20_handled_notification_failure_does_not_leak_partial_changes_to_outer_commit(isolated_mysql,monkeypatch,operation):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    from sme_gend.services import data_sync
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params)
    original=OSError('synthetic business notification failure')
    def fail(*args,**kwargs):raise original
    monkeypatch.setattr(data_sync,'publish_data_change',fail)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO business_outer VALUES(1)')
        with pytest.raises(RuntimeError if operation.startswith('task_') else OSError):actions[operation]()
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO business_outer VALUES(2)')
    assert _business_lot20_snapshot(connect,params)==before
    assert not data_sync.read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM business_outer ORDER BY id');assert cur.fetchall()==((1,),(2,))



def _gain_lot21_setup(isolated_mysql,monkeypatch):
    setup=_business_lot20_setup(isolated_mysql,monkeypatch)
    settings,connect,params,repo,*_=setup
    return settings,connect,params,repo


def _gain_lot21_save(repo,row):
    repo.update_row(row,where_id=row.id,where_cdsme=row.cdsme,movement_id=row.movement_id)


@pytest.mark.parametrize('concurrent_change',['edit','hidden_motif','delete'])
def test_gain_lot21_stale_editor_cannot_overwrite_a_concurrent_change(isolated_mysql,monkeypatch,concurrent_change):
    from sme_gend.services import data_sync
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    first=next(row for row in repo.fetch_all() if row.movement_id==10)
    second=next(row for row in repo.fetch_all() if row.movement_id==10)
    if concurrent_change=='edit':
        _gain_lot21_save(repo,replace(first,observ='FIRST WRITER'))
    else:
        with connect(**params) as conn:
            with conn.cursor() as cur:
                if concurrent_change=='delete':cur.execute('DELETE FROM mouvement_effectif WHERE id=10')
                else:cur.execute("UPDATE mouvement_effectif SET motif='HIDDEN CHANGE' WHERE id=10")
    before=_business_lot20_snapshot(connect,params)
    revision=data_sync.read_data_revision(settings)
    published=[];publish=data_sync.publish_data_change
    def observed(*args,**kwargs):published.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    with pytest.raises(ValueError,match='modifié ou supprimé'):
        _gain_lot21_save(repo,replace(second,nom='SECOND WRITER'))
    assert published==[]
    assert _business_lot20_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision



@pytest.mark.parametrize('fetch',['all','month'])
def test_gain_lot21_loading_attaches_an_opaque_raw_snapshot(isolated_mysql,monkeypatch,fetch):
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    rows=repo.fetch_all() if fetch=='all' else repo.fetch_for_cdsme('092026')
    row=next(row for row in rows if row.movement_id==10)
    assert row.write_token[0]==10 and len(row.write_token[1])==64
    assert row.write_token[1] not in repr(row)
    before=_business_lot20_snapshot(connect,params)
    assert next(r for r in repo.fetch_all() if r.movement_id==10).write_token==row.write_token
    assert _business_lot20_snapshot(connect,params)==before


@pytest.mark.parametrize('field,value',[
    ('grade_snapshot',None),('grade_snapshot','gnd'),('nom_snapshot','synthetic'),
    ('type_mouvement','HIDDEN'),('categorie_mouvement',None),('motif','HIDDEN'),
    ('cdsme_derive',None),('observ','RETRAITE '),('date_reference','2026-09-29'),
    ('sme_periode_id',2),('agent_id',2),('identifiant_ligne','CHANGED'),('import_lot_id',42),
])
def test_gain_lot21_raw_changes_are_detected_even_when_display_is_normalized(isolated_mysql,monkeypatch,field,value):
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    # Identifiers are fixed by the test parameter allowlist above; values are bound.
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE mouvement_effectif SET '+field+'=%s WHERE id=10',(value,))
    before=_business_lot20_snapshot(connect,params)
    with pytest.raises(GainPerteConflictError):_gain_lot21_save(repo,replace(stale,nom='STALE EDIT'))
    assert _business_lot20_snapshot(connect,params)==before
    assert not read_data_revision(settings)


@pytest.mark.parametrize('parent',['agent','period'])
def test_gain_lot21_changed_linked_identity_is_not_silently_retargeted(isolated_mysql,monkeypatch,parent):
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if parent=='agent':cur.execute("UPDATE agent SET legacy_access_id='RENAMED' WHERE id=1")
            else:cur.execute("UPDATE sme_periode SET annee_mois='2026-08' WHERE id=1")
    before=_business_lot20_snapshot(connect,params)
    with pytest.raises(GainPerteConflictError):_gain_lot21_save(repo,replace(stale,nom='STALE'))
    assert _business_lot20_snapshot(connect,params)==before


@pytest.mark.parametrize('change',['modified','deleted'])
def test_gain_lot21_deletion_refuses_an_outdated_movement(isolated_mysql,monkeypatch,change):
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if change=='modified':cur.execute("UPDATE mouvement_effectif SET observ='FIRST' WHERE id=10")
            else:cur.execute('DELETE FROM mouvement_effectif WHERE id=10')
    before=_business_lot20_snapshot(connect,params)
    with pytest.raises(GainPerteConflictError):repo.delete_row(stale.id,stale.cdsme,movement_id=10,expected=stale)
    assert _business_lot20_snapshot(connect,params)==before
    assert not read_data_revision(settings)


def test_gain_lot21_reload_permits_retargeted_update_and_then_deletion(isolated_mysql,monkeypatch):
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    others=[row for row in repo.fetch_all() if row.movement_id!=10]
    published=[];publish=data_sync.publish_data_change
    def observed(*args,**kwargs):published.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    _gain_lot21_save(repo,replace(stale,id='legacy-2',mois_ym='2026-10',cdsme='102026',date_reference='2026-10-31',nom='NEW'))
    fresh=next(row for row in repo.fetch_all() if row.movement_id==10)
    assert fresh.id=='legacy-2' and fresh.mois_ym=='2026-10' and fresh.date_reference=='2026-10-31'
    assert fresh.write_token!=stale.write_token
    with pytest.raises(GainPerteConflictError):repo.delete_row(stale.id,stale.cdsme,movement_id=10,expected=stale)
    repo.delete_row(fresh.id,fresh.cdsme,movement_id=10,expected=fresh)
    assert repo.fetch_all()==others
    assert published==[True,True]


@pytest.mark.parametrize('actions',[('update','update'),('update','delete'),('delete','delete')])
def test_gain_lot21_simultaneous_writers_have_one_winner_and_one_revision(isolated_mysql,monkeypatch,actions):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.repositories.mysql_bundle_repositories import MySQLGainPerteRepository
    from sme_gend.services import data_sync
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    snapshots=[next(row for row in repo.fetch_all() if row.movement_id==10) for _ in actions]
    before=_business_lot20_snapshot(connect,params)[0]
    published=[];publish=data_sync.publish_data_change
    def observed(*args,**kwargs):published.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    gate=Barrier(2)
    def write(index):
        other=MySQLGainPerteRepository(settings);snapshot=snapshots[index]
        gate.wait(timeout=5)
        try:
            if actions[index]=='update':_gain_lot21_save(other,replace(snapshot,nom='WRITER '+str(index)))
            else:other.delete_row(snapshot.id,snapshot.cdsme,movement_id=10,expected=snapshot)
            return 'saved',index
        except GainPerteConflictError:return 'conflict',index
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,index) for index in range(2)]
        results=[job.result(timeout=12) for job in jobs]
    assert sorted(result[0] for result in results)==['conflict','saved']
    assert published==[True] and data_sync.read_data_revision(settings)
    after=_business_lot20_snapshot(connect,params)[0]
    assert [row for row in after if row[0]!=10]==[row for row in before if row[0]!=10]
    winner=next(index for status,index in results if status=='saved')
    if actions[winner]=='delete':assert not any(row[0]==10 for row in after)
    else:assert next(row for row in after if row[0]==10)[5]=='WRITER '+str(winner)


def test_gain_lot21_guard_reads_current_values_inside_repeatable_read_and_preserves_outer_work(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cur.execute('START TRANSACTION')
        stale=next(row for row in repo.fetch_all() if row.movement_id==10)
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(_gain_lot21_save,repo,replace(stale,observ='FIRST')).result(timeout=10)
        assert next(row for row in repo.fetch_all() if row.movement_id==10).observ==stale.observ
        with pytest.raises(GainPerteConflictError):_gain_lot21_save(repo,replace(stale,nom='STALE'))
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO business_outer VALUES(1)')
    fresh=next(row for row in repo.fetch_all() if row.movement_id==10)
    assert fresh.observ=='FIRST' and fresh.nom==stale.nom and read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT * FROM business_outer');assert cur.fetchall()==((1,),)


def test_gain_lot21_handled_second_conflict_keeps_first_write_and_its_revision(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    published=[];publish=data_sync.publish_data_change
    def observed(*args,**kwargs):published.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    with mysql_import_transaction(settings):
        _gain_lot21_save(repo,replace(stale,nom='FIRST'))
        with pytest.raises(GainPerteConflictError):_gain_lot21_save(repo,replace(stale,nom='SECOND'))
    assert next(row for row in repo.fetch_all() if row.movement_id==10).nom=='FIRST'
    assert published==[True] and data_sync.read_data_revision(settings)


def test_gain_lot21_revision_failure_rolls_back_and_same_snapshot_can_be_retried(isolated_mysql,monkeypatch):
    from sme_gend.services import data_sync
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    stale=next(row for row in repo.fetch_all() if row.movement_id==10)
    before=_business_lot20_snapshot(connect,params)
    publish=data_sync.publish_data_change
    def failed(*args,**kwargs):raise OSError('synthetic publication failure')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    with pytest.raises(OSError):_gain_lot21_save(repo,replace(stale,nom='FIRST'))
    assert _business_lot20_snapshot(connect,params)==before and not data_sync.read_data_revision(settings)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    _gain_lot21_save(repo,replace(stale,nom='RETRIED'))
    assert next(row for row in repo.fetch_all() if row.movement_id==10).nom=='RETRIED'


def test_gain_lot21_guard_does_not_exclusively_lock_other_movements_or_their_shared_parents(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    loaded=next(row for row in repo.fetch_all() if row.movement_id==10)
    with mysql_import_transaction(settings):
        _gain_lot21_save(repo,replace(loaded,nom='PENDING'))
        with connect(**(params|{'autocommit':False})) as other:
            with other.cursor() as cur:
                cur.execute('SELECT m.id FROM mouvement_effectif m INNER JOIN agent a ON a.id=m.agent_id LEFT JOIN sme_periode p ON p.id=m.sme_periode_id WHERE m.id=11 FOR UPDATE OF m NOWAIT FOR SHARE OF a,p NOWAIT')
                assert cur.fetchone()==(11,)
            other.rollback()


def test_gain_lot21_change_of_another_movement_does_not_conflict(isolated_mysql,monkeypatch):
    settings,connect,params,repo=_gain_lot21_setup(isolated_mysql,monkeypatch)
    loaded=next(row for row in repo.fetch_all() if row.movement_id==10)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE mouvement_effectif SET nom_snapshot='OTHER EDIT' WHERE id=11")
    _gain_lot21_save(repo,replace(loaded,nom='VALID EDIT'))
    rows={row.movement_id:row for row in repo.fetch_all()}
    assert rows[10].nom=='VALID EDIT' and rows[11].nom=='OTHER EDIT'



def _catalog_lot22_save(repo,kind,row,label):
    # Accept the pre-fix DTO for reproductions; the fixed loader always adds a
    # write_token field, whose absence/invalidity is separately tested.
    kw={'expected':row} if hasattr(row,'write_token') else {}
    if kind=='grade':repo.update(row.code,label,row.pref_ord,row.effectif_g,**kw)
    elif kind=='structure':repo.update(row.code,label,row.ordre_tri,famille_unite=row.famille_unite,**kw)
    else:repo.update(row.id,row.libelle,label,**kw)


@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_catalog_lot22_second_editor_cannot_overwrite_the_first_commit(isolated_mysql,monkeypatch,kind):
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    key={'grade':'GOLD','structure':'OLD','fonction':1}[kind]
    choose=lambda:next(row for row in repos[kind].fetch_all() if getattr(row,'code',getattr(row,'id',None))==key)
    first=choose();second=choose()
    _catalog_lot22_save(repos[kind],kind,first,'FIRST EDITOR')
    before=_reference_lot19_snapshot(connect,params);revision=data_sync.read_data_revision(settings)
    with pytest.raises(ValueError,match='modifié|modifiée'):
        _catalog_lot22_save(repos[kind],kind,second,'SECOND EDITOR')
    assert _reference_lot19_snapshot(connect,params)==before
    assert data_sync.read_data_revision(settings)==revision


@pytest.mark.parametrize('kind',['grade','structure'])
def test_catalog_lot22_creation_does_not_replace_an_existing_code(isolated_mysql,monkeypatch,kind):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ValueError,match='existe'):
        if kind=='grade':repos[kind].insert('GOLD','REPLACEMENT',8,8)
        else:repos[kind].insert('OLD','REPLACEMENT',8,famille_unite='changed')
    assert _reference_lot19_snapshot(connect,params)==before
    assert not read_data_revision(settings)



def _catalog_lot22_row(repos,kind,free=False):
    key=({'grade':'GFREE','structure':'FREE','fonction':3} if free else
         {'grade':'GOLD','structure':'OLD','fonction':1})[kind]
    return next(row for row in repos[kind].fetch_all() if getattr(row,'code',getattr(row,'id',None))==key)


@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_catalog_lot22_loaded_tokens_are_stable_and_not_exported_in_diagnostics(isolated_mysql,monkeypatch,kind):
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    row=_catalog_lot22_row(repos,kind)
    assert row.write_token[:2]==(kind,str(getattr(row,'code',getattr(row,'id',None))))
    assert len(row.write_token[2])==64 and row.write_token[2] not in repr(row)
    assert _catalog_lot22_row(repos,kind).write_token==row.write_token


@pytest.mark.parametrize('kind,column,value',[
    ('grade','libelle','grade'),('grade','pref_ord',None),('grade','effectif_g',91),
    ('structure','designation','OLD '),('structure','ordre_tri',None),('structure','famille_unite',None),
    ('fonction','libelle','func'),('fonction','libelle','FUNC '),
])
def test_catalog_lot22_raw_field_changes_cannot_be_hidden_by_display_normalization(isolated_mysql,monkeypatch,kind,column,value):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    row=_catalog_lot22_row(repos,kind)
    table={'grade':'ref_grade','structure':'ref_structure','fonction':'ref_fonction'}[kind]
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE '+table+' SET '+column+'=%s WHERE id=1',(value,))
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ReferenceConflictError):_catalog_lot22_save(repos[kind],kind,row,'STALE')
    assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)


@pytest.mark.parametrize('kind',['grade','structure','fonction'])
@pytest.mark.parametrize('operation',['update','delete'])
def test_catalog_lot22_deleted_entry_is_a_conflict_not_a_silent_success(isolated_mysql,monkeypatch,kind,operation):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    row=_catalog_lot22_row(repos,kind,free=True)
    table={'grade':'ref_grade','structure':'ref_structure','fonction':'ref_fonction'}[kind]
    key=getattr(row,'code',getattr(row,'id',None))
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DELETE FROM '+table+' WHERE '+('id' if kind=='fonction' else 'code')+'=%s',(key,))
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ReferenceConflictError):
        if operation=='update':_catalog_lot22_save(repos[kind],kind,row,'STALE')
        else:repos[kind].delete(key,expected=row)
    assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)


@pytest.mark.parametrize('kind',['grade','structure'])
def test_catalog_lot22_organigram_deletion_cannot_remove_an_edited_entry(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    row=_catalog_lot22_row(repos,kind,free=True)
    _catalog_lot22_save(repos[kind],kind,row,'FIRST')
    before=_reference_lot19_snapshot(connect,params);revision=read_data_revision(settings)
    with pytest.raises(ReferenceConflictError):getattr(repos['organigram'],'safe_delete_'+kind)(row.code,expected=row)
    assert _reference_lot19_snapshot(connect,params)==before and read_data_revision(settings)==revision


@pytest.mark.parametrize('kind',['grade','structure','fonction'])
@pytest.mark.parametrize('operations',[('update','update'),('update','delete'),('delete','delete')])
def test_catalog_lot22_two_simultaneous_writers_have_one_winner(isolated_mysql,monkeypatch,kind,operations):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    rows=[_catalog_lot22_row(repos,kind,free=True) for _ in range(2)]
    published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    gate=Barrier(2)
    def writer(index):
        gate.wait(timeout=5);row=rows[index]
        try:
            if operations[index]=='update':_catalog_lot22_save(repos[kind],kind,row,'WRITER '+str(index))
            else:repos[kind].delete(getattr(row,'code',getattr(row,'id',None)),expected=row)
            return 'saved',index
        except ReferenceConflictError:return 'conflict',index
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(writer,index) for index in range(2)]
        results=[job.result(timeout=12) for job in jobs]
    assert sorted(status for status,index in results)==['conflict','saved']
    assert published==[True] and data_sync.read_data_revision(settings)
    winner=next(index for status,index in results if status=='saved')
    matching=[row for row in repos[kind].fetch_all() if getattr(row,'code',getattr(row,'id',None))==getattr(rows[0],'code',getattr(rows[0],'id',None))]
    if operations[winner]=='delete':assert not matching
    else:assert getattr(matching[0],'designation',getattr(matching[0],'libelle',None))=='WRITER '+str(winner)


@pytest.mark.parametrize('kind',['grade','structure'])
def test_catalog_lot22_simultaneous_creations_never_replace_the_first_values(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def create(index):
        gate.wait(timeout=5)
        try:
            if kind=='grade':repos[kind].insert('RACE','WRITER '+str(index),1,index)
            else:repos[kind].insert('RACE','WRITER '+str(index),index,famille_unite='test')
            return index
        except ReferenceConflictError:return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(create,index) for index in range(2)]
        results=[job.result(timeout=12) for job in jobs]
    winners=[index for index in results if index is not None]
    assert len(winners)==1 and published==[True]
    rows=[row for row in repos[kind].fetch_all() if row.code=='RACE']
    assert len(rows)==1 and getattr(rows[0],'designation',getattr(rows[0],'libelle',None))=='WRITER '+str(winners[0])


@pytest.mark.parametrize('kind',['grade','structure'])
def test_catalog_lot22_recreated_code_has_a_different_identity(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    old=_catalog_lot22_row(repos,kind,free=True)
    repos[kind].delete(old.code,expected=old)
    if kind=='grade':repos[kind].insert(old.code,old.libelle,old.pref_ord,old.effectif_g)
    else:repos[kind].insert(old.code,old.designation,old.ordre_tri,famille_unite=old.famille_unite)
    fresh=_catalog_lot22_row(repos,kind,free=True)
    assert old==fresh and old.write_token!=fresh.write_token
    with pytest.raises(ReferenceConflictError):_catalog_lot22_save(repos[kind],kind,old,'STALE')


@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_catalog_lot22_current_locking_read_beats_an_outer_repeatable_read_snapshot(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');cur.execute('START TRANSACTION')
        old=_catalog_lot22_row(repos,kind)
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(_catalog_lot22_save,repos[kind],kind,old,'FIRST').result(timeout=12)
        assert _catalog_lot22_row(repos,kind).write_token==old.write_token
        with pytest.raises(ReferenceConflictError):_catalog_lot22_save(repos[kind],kind,old,'STALE')
    fresh=_catalog_lot22_row(repos,kind)
    assert getattr(fresh,'designation',getattr(fresh,'libelle',None))=='FIRST'


@pytest.mark.parametrize('conflict',[False,True])
def test_catalog_lot22_bulk_structure_type_uses_displayed_snapshots_and_is_atomic(isolated_mysql,monkeypatch,conflict):
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service=ReferentielService.__new__(ReferentielService);service._settings=settings;service._structures=repos['structure']
    rows=[row for row in repos['structure'].fetch_all() if row.code in ('OLD','DEST')]
    if conflict:
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE ref_structure SET designation='CONCURRENT' WHERE code='OLD'")
    before=_reference_lot19_snapshot(connect,params);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    if conflict:
        with pytest.raises(ReferenceConflictError):service.structure_bulk_set_famille(['OLD','DEST'],'new',expected=rows)
        assert _reference_lot19_snapshot(connect,params)==before and published==[]
    else:
        assert service.structure_bulk_set_famille(['OLD','DEST'],'new',expected=rows)==2
        assert all(row.famille_unite=='new' for row in repos['structure'].fetch_all() if row.code in ('OLD','DEST'))
        assert published==[True]


@pytest.mark.parametrize('late_failure',[False,True])
def test_catalog_lot22_layout_rename_and_identity_edit_keep_one_transaction_and_preserve_family(isolated_mysql,monkeypatch,late_failure):
    from types import SimpleNamespace
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service=LayoutConfigService.__new__(LayoutConfigService);service._settings=settings;service._structures=repos['structure']
    service._liste_generale=SimpleNamespace(update_structure_column=lambda *a:None,backfill_structure_id_for_code=lambda *a:None)
    cfg=ListeLayoutConfig(sections=[SectionBlock('one',0,'OLD')]);row=_catalog_lot22_row(repos,'structure')
    before=_reference_lot19_snapshot(connect,params)
    if late_failure:
        def failed(*a,**kw):raise RuntimeError('synthetic second phase failure')
        monkeypatch.setattr(service,'update_structure_referentiel',failed)
        with pytest.raises(RuntimeError,match='second phase'):service.edit_structure_referentiel(cfg,'OLD','RENAMED','NEW',9,expected=row)
        assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)
    else:
        edited=service.edit_structure_referentiel(cfg,'OLD','RENAMED','NEW',9,expected=row)
        fresh=repos['structure'].fetch_by_code('RENAMED')
        assert fresh.designation=='NEW' and fresh.ordre_tri==9 and fresh.famille_unite==row.famille_unite
        assert edited.sections[0].structure_code=='RENAMED' and read_data_revision(settings)
    assert cfg.sections[0].structure_code=='OLD'



def _links_lot23_expected(repo,kind,key=None):
    # Reproductions exercise the historical API before snapshot support exists.
    method=getattr(repo,'load_'+kind+'_snapshot',None)
    return {'expected':method(key) if key is not None else method()} if method else {}


@pytest.mark.parametrize('direction',['structure','fonction'])
def test_links_lot23_stale_selection_cannot_replace_another_clients_links(isolated_mysql,monkeypatch,direction):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    links=repos['links'];key='OLD' if direction=='structure' else 1
    old=_links_lot23_expected(links,'links_'+direction,key)
    getattr(links,'set_links_for_'+direction)(key,['FNEW'] if direction=='structure' else ['DEST'],**old)
    before=_reference_lot19_snapshot(connect,params);revision=read_data_revision(settings)
    with pytest.raises(ValueError,match='modifié|modifiée'):
        getattr(links,'set_links_for_'+direction)(key,[] if direction=='structure' else ['FREE'],**old)
    assert _reference_lot19_snapshot(connect,params)==before
    assert read_data_revision(settings)==revision


@pytest.mark.parametrize('kind',['structures','subdivisions'])
def test_links_lot23_stale_organigram_order_cannot_replace_the_first_order(isolated_mysql,monkeypatch,kind):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    org=repos['organigram'];old=_links_lot23_expected(org,'order_'+kind,1 if kind=='subdivisions' else None)
    if kind=='structures':org.reorder_structures(['DEST','OLD'],**old)
    else:org.reorder_subdivisions(1,[11,10],**old)
    before=_reference_lot19_snapshot(connect,params);revision=read_data_revision(settings)
    with pytest.raises(ValueError,match='modifié|modifiée'):
        if kind=='structures':org.reorder_structures(['OLD','DEST'],**old)
        else:org.reorder_subdivisions(1,[10,11],**old)
    assert _reference_lot19_snapshot(connect,params)==before
    assert read_data_revision(settings)==revision


def test_links_lot23_stale_layout_order_cannot_replace_a_saved_configuration(isolated_mysql,monkeypatch):
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services import app_config_store
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr(app_config_store,'resolve_audit_actor',lambda:'synthetic')
    cfg=ListeLayoutConfig(sections=[SectionBlock('a',0,'OLD'),SectionBlock('b',1,'DEST')])
    store=AppConfigRepository(settings);store.set_json(app_config_store.KEY_LISTE_LAYOUT,cfg.to_dict())
    service=LayoutConfigService.__new__(LayoutConfigService);service._settings=settings
    first=service.load();second=service.load()
    first.sections[0].order=2;service.save(first)
    before=store.get_json(app_config_store.KEY_LISTE_LAYOUT)
    second.sections[1].order=2
    with pytest.raises(ValueError,match='modifié|modifiée'):service.save(second)
    assert store.get_json(app_config_store.KEY_LISTE_LAYOUT)==before



def _links_lot23_layout(settings):
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.app_config_store import KEY_LISTE_LAYOUT
    store=AppConfigRepository(settings)
    store.set_json(KEY_LISTE_LAYOUT,ListeLayoutConfig(sections=[SectionBlock('a',0,'OLD'),SectionBlock('b',1,'DEST')]).to_dict())
    service=LayoutConfigService.__new__(LayoutConfigService);service._settings=settings
    return service,store,KEY_LISTE_LAYOUT


@pytest.mark.parametrize('empty',[False,True])
@pytest.mark.parametrize('direction',['structure','fonction'])
def test_links_lot23_concurrent_same_selection_has_one_winner_even_when_initially_empty(isolated_mysql,monkeypatch,empty,direction):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    if empty:
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM ref_fonction_structure')
    links=repos['links'];key='OLD' if direction=='structure' else 1
    snapshots=[getattr(links,'load_links_'+direction+'_snapshot')(key) for _ in range(2)]
    payloads=[['FNEW'],[]] if direction=='structure' else [['DEST'],['FREE']]
    if empty and direction=='structure':payloads=[['FUNC'],['FNEW']]
    gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def write(index):
        gate.wait(timeout=5)
        try:getattr(links,'set_links_for_'+direction)(key,payloads[index],expected=snapshots[index]);return 'saved',index
        except ReferenceConflictError:return 'conflict',index
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,i) for i in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert sorted(status for status,index in results)==['conflict','saved'] and published==[True]
    assert data_sync.read_data_revision(settings)


@pytest.mark.parametrize('first_direction',['structure','fonction'])
def test_links_lot23_opposite_dialogs_detect_the_same_changed_intersection(isolated_mysql,monkeypatch,first_direction):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    links=repos['links'];a=links.load_links_structure_snapshot('OLD');b=links.load_links_fonction_snapshot(1)
    if first_direction=='structure':links.set_links_for_structure('OLD',['FNEW'],expected=a)
    else:links.set_links_for_fonction(1,['DEST'],expected=b)
    before=_reference_lot19_snapshot(connect,params);revision=read_data_revision(settings)
    with pytest.raises(ReferenceConflictError):
        if first_direction=='structure':links.set_links_for_fonction(1,['FREE'],expected=b)
        else:links.set_links_for_structure('OLD',[],expected=a)
    assert _reference_lot19_snapshot(connect,params)==before and read_data_revision(settings)==revision


@pytest.mark.parametrize('scope',['structure','fonction'])
def test_links_lot23_empty_noop_has_no_revision_and_unknown_choice_rolls_back(scope,isolated_mysql,monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DELETE FROM ref_fonction_structure')
    key='OLD' if scope=='structure' else 1;links=repos['links']
    snapshot=getattr(links,'load_links_'+scope+'_snapshot')(key)
    getattr(links,'set_links_for_'+scope)(key,[],expected=snapshot)
    assert not read_data_revision(settings)
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ValueError):getattr(links,'set_links_for_'+scope)(key,['UNKNOWN'],expected=snapshot)
    assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)


def test_links_lot23_additive_merge_preserves_existing_order_and_concurrent_additions(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    links=repos['links'];gate=Barrier(2)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE ref_fonction_structure SET ordre_tri=17 WHERE fonction_id=1')
    def add(code):gate.wait(timeout=5);links.merge_links_for_fonction(1,[code])
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(add,code) for code in ('DEST','FREE')]
        for job in jobs:job.result(timeout=15)
    assert set(links.fetch_structure_codes_for_fonction(1))=={'OLD','DEST','FREE'}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT ordre_tri FROM ref_fonction_structure WHERE fonction_id=1 AND structure_id=1')
            assert cur.fetchone()==(17,)


@pytest.mark.parametrize('scope',['links_structure','links_fonction','order_structures','order_subdivisions'])
def test_links_lot23_parent_or_membership_change_invalidates_a_collection(scope,isolated_mysql,monkeypatch):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    target=repos['links'] if scope.startswith('links') else repos['organigram']
    key='OLD' if scope=='links_structure' else 1
    snapshot=getattr(target,'load_'+scope+'_snapshot')(*(() if scope=='order_structures' else (key,)))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if scope=='links_structure':cur.execute("UPDATE ref_fonction SET libelle='CHANGED' WHERE id=2")
            elif scope=='links_fonction':cur.execute("UPDATE ref_structure SET designation='CHANGED' WHERE id=2")
            elif scope=='order_structures':cur.execute("INSERT INTO ref_structure(code,designation) VALUES('ADDED','NEW')")
            else:cur.execute('UPDATE ref_subdivision_arme SET actif=1-actif WHERE id=10')
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ReferenceConflictError):
        if scope=='links_structure':target.set_links_for_structure('OLD',[],expected=snapshot)
        elif scope=='links_fonction':target.set_links_for_fonction(1,[],expected=snapshot)
        elif scope=='order_structures':target.reorder_structures(['DEST','OLD'],expected=snapshot)
        else:target.reorder_subdivisions(1,[11,10],expected=snapshot)
    assert _reference_lot19_snapshot(connect,params)==before


@pytest.mark.parametrize('scope',['links_structure','links_fonction','order_structures','order_subdivisions'])
def test_links_lot23_check_uses_current_rows_in_an_outer_repeatable_read(scope,isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    target=repos['links'] if scope.startswith('links') else repos['organigram'];key='OLD' if scope=='links_structure' else 1
    load=lambda:getattr(target,'load_'+scope+'_snapshot')(*(() if scope=='order_structures' else (key,)))
    def write(snapshot,first):
        if scope=='links_structure':target.set_links_for_structure('OLD',['FNEW'] if first else [],expected=snapshot)
        elif scope=='links_fonction':target.set_links_for_fonction(1,['DEST'] if first else [],expected=snapshot)
        elif scope=='order_structures':target.reorder_structures(['DEST','OLD'] if first else ['OLD','DEST'],expected=snapshot)
        else:target.reorder_subdivisions(1,[11,10] if first else [10,11],expected=snapshot)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');cur.execute('START TRANSACTION')
        old=load()
        with ThreadPoolExecutor(max_workers=1) as worker:worker.submit(write,old,True).result(timeout=15)
        assert load().revision==old.revision
        with pytest.raises(ReferenceConflictError):write(old,False)


@pytest.mark.parametrize('kind',['structures','subdivisions'])
def test_links_lot23_invalid_order_payload_does_not_mutate(kind,isolated_mysql,monkeypatch):
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    org=repos['organigram'];before=_reference_lot19_snapshot(connect,params)
    if kind=='structures':
        snapshot=org.load_order_structures_snapshot()
        with pytest.raises(ValueError):org.reorder_structures(['OLD','OLD'],expected=snapshot)
        with pytest.raises(ValueError):org.reorder_structures(['UNKNOWN'],expected=snapshot)
    else:
        snapshot=org.load_order_subdivisions_snapshot(1)
        with pytest.raises(ValueError):org.reorder_subdivisions(1,[10,10],expected=snapshot)
        with pytest.raises(ValueError):org.reorder_subdivisions(1,[999],expected=snapshot)
    assert _reference_lot19_snapshot(connect,params)==before


@pytest.mark.parametrize('commit',[False,True])
def test_links_lot23_layout_token_follows_real_commit_with_repeated_saves(isolated_mysql,monkeypatch,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.reference_collection_guard import fingerprint
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service,store,key=_links_lot23_layout(settings);cfg=service.load();original=cfg.write_token;before=store.get_json(key)
    try:
        with mysql_import_transaction(settings):
            cfg.sections[0].order=4;service.save(cfg)
            assert cfg.write_token==original and store.get_json(key)!=before
            cfg.sections[0].order=7;service.save(cfg)
            assert cfg.write_token==original
            if not commit:raise ValueError('synthetic outer rollback')
    except ValueError:assert not commit
    if commit:assert cfg.write_token==fingerprint(store.get_json(key)) and cfg.write_token!=original
    else:
        assert cfg.write_token==original and store.get_json(key)==before
        service.save(cfg)
        assert cfg.write_token==fingerprint(store.get_json(key))


def test_links_lot23_layout_revision_failure_restores_pending_snapshot_and_can_retry(isolated_mysql,monkeypatch):
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service,store,key=_links_lot23_layout(settings);cfg=service.load();old=cfg.write_token;before=store.get_json(key)
    publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic signal failure')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    cfg.sections[0].order=3
    with pytest.raises(OSError):service.save(cfg)
    assert cfg.write_token==old and cfg._pending_write is None and store.get_json(key)==before
    monkeypatch.setattr(data_sync,'publish_data_change',publish);service.save(cfg)
    assert cfg.write_token!=old


def test_links_lot23_simultaneous_layout_edits_have_one_winner_and_one_signal(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service,store,key=_links_lot23_layout(settings);a=service.load();b=service.load();a.sections[0].order=3;b.sections[1].order=4
    gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def write(cfg):
        gate.wait(timeout=5)
        try:service.save(cfg);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,cfg) for cfg in (a,b)];results=[job.result(timeout=15) for job in jobs]
    assert sorted(results)==[False,True] and published==[True]
    winner=(a,b)[results.index(True)]
    assert store.get_json(key)==winner.to_dict()


def test_links_lot23_failed_layout_read_never_builds_or_saves_a_default(isolated_mysql,monkeypatch):
    from sme_gend.services import layout_write_guard
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service,store,key=_links_lot23_layout(settings);before=store.get_json(key)
    def failed(*a,**kw):raise OSError('synthetic offline')
    monkeypatch.setattr(layout_write_guard.MySQLConnection,'connect',failed)
    with pytest.raises(RuntimeError,match='Impossible de charger'):service.load()
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT payload FROM app_config WHERE config_key=%s',(key,));assert json.loads(cur.fetchone()[0])==before


def test_links_lot23_two_first_layout_loads_initialize_once_without_replacing(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def initialize(index):
        service=LayoutConfigService.__new__(LayoutConfigService);service._settings=settings
        def default():gate.wait(timeout=5);return ListeLayoutConfig(sections=[SectionBlock(str(index),0,'OLD')])
        service.build_default_from_database=default
        return service.load()
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(initialize,index) for index in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert results[0].to_dict()==results[1].to_dict() and results[0].write_token==results[1].write_token and published==[True]



@pytest.mark.parametrize('kind',['structures','subdivisions'])
def test_links_lot23_two_order_writers_cannot_overwrite_each_other(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    org=repos['organigram']
    load=lambda:org.load_order_structures_snapshot() if kind=='structures' else org.load_order_subdivisions_snapshot(1)
    snapshots=[load(),load()];gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def write(index):
        gate.wait(timeout=5)
        try:
            if kind=='structures':org.reorder_structures([['DEST','FREE','OLD'],['FREE','OLD','DEST']][index],expected=snapshots[index])
            else:org.reorder_subdivisions(1,[[11,10],[10,11]][index],expected=snapshots[index])
            return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,i) for i in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert sorted(results)==[False,True] and published==[True]


def test_links_lot23_checked_smart_batch_rolls_back_rename_when_link_addition_fails(isolated_mysql,monkeypatch):
    from sme_gend.repositories.fonctions_repository import FonctionsRepository
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    facade=FonctionsRepository(settings);snapshot=facade.links_snapshot_all()
    original=next(row for row in facade.fetch_all() if row.id==1)
    before=_reference_lot19_snapshot(connect,params)
    with pytest.raises(ValueError,match='Structure inconnue'):
        with shared_reference_write(settings):
            facade.validate_links_snapshot(snapshot)
            facade.update(1,'FUNC','RENAMED',expected=original)
            facade.merge_structure_links(1,['UNKNOWN'])
    assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)


def test_links_lot23_layout_handled_failure_does_not_advance_the_wrong_callback(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.services import data_sync
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    service,store,key=_links_lot23_layout(settings);cfg=service.load();publish=data_sync.publish_data_change
    with mysql_import_transaction(settings):
        cfg.sections[0].order=3;service.save(cfg);first=cfg.to_dict()
        def failed(*a,**kw):raise OSError('synthetic second signal failure')
        monkeypatch.setattr(data_sync,'publish_data_change',failed)
        cfg.sections[0].order=6
        with pytest.raises(OSError):service.save(cfg)
        monkeypatch.setattr(data_sync,'publish_data_change',publish)
    assert store.get_json(key)==first and cfg.write_token==fingerprint(first)
    assert cfg.sections[0].order==6
    service.save(cfg)
    assert store.get_json(key)==cfg.to_dict()


@pytest.mark.parametrize('kind',['structures','subdivisions'])
def test_links_lot23_order_service_rolls_back_sql_if_layout_save_fails(isolated_mysql,monkeypatch,kind):
    from types import SimpleNamespace
    from sme_gend.services.structure_organigram_service import StructureOrganigramService
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    layout,store,key=_links_lot23_layout(settings)
    service=StructureOrganigramService.__new__(StructureOrganigramService);service._settings=settings;service._org=repos['organigram'];service._layout=layout
    before=_reference_lot19_snapshot(connect,params)
    def failed(*a,**kw):raise RuntimeError('synthetic layout veto')
    monkeypatch.setattr(layout,'save',failed)
    with pytest.raises(RuntimeError,match='layout veto'):
        if kind=='structures':service.reorder_structures(['DEST','OLD'],expected=service.load_order_structures_snapshot())
        else:service.reorder_subdivisions('OLD',1,[11,10],['UNIT TWO','UNIT'],expected=service.load_order_subdivisions_snapshot(1))
    assert _reference_lot19_snapshot(connect,params)==before and not read_data_revision(settings)


def test_links_lot23_one_early_revision_lock_and_one_signal_per_nested_reference_batch(isolated_mysql,monkeypatch):
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services import data_sync
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    locks=[];published=[];execute=pymysql.cursors.Cursor.execute;publish=data_sync.publish_data_change
    def observed(cursor,query,args=None):
        if isinstance(query,str) and 'ON DUPLICATE KEY UPDATE config_key=VALUES(config_key)' in query:locks.append(True)
        return execute(cursor,query,args)
    def notified(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',observed);monkeypatch.setattr(data_sync,'publish_data_change',notified)
    with shared_reference_write(settings):actions['grade_update']();actions['structure_update']()
    assert locks==[True] and published==[True]



@pytest.mark.parametrize('kind',['links_structure','links_fonction','order_structures','order_subdivisions','layout'])
@pytest.mark.parametrize('commit',[False,True])
def test_links_lot23_second_connection_sees_data_and_revision_at_the_same_real_commit(isolated_mysql,monkeypatch,kind,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    layout,store,key=_links_lot23_layout(settings)
    links=repos['links'];org=repos['organigram']
    if kind=='links_structure':snapshot=links.load_links_structure_snapshot('OLD')
    elif kind=='links_fonction':snapshot=links.load_links_fonction_snapshot(1)
    elif kind=='order_structures':snapshot=org.load_order_structures_snapshot()
    elif kind=='order_subdivisions':snapshot=org.load_order_subdivisions_snapshot(1)
    else:snapshot=layout.load()
    observer=connect(**params)
    def observed():
        values=[]
        with observer.cursor() as cur:
            for sql in ['SELECT * FROM ref_fonction_structure ORDER BY structure_id,fonction_id','SELECT id,ordre_tri FROM ref_structure ORDER BY id','SELECT id,ordre_tri FROM ref_subdivision_arme ORDER BY id',"SELECT config_key,payload FROM app_config WHERE config_key IN ('liste_present_layout','shared_data_revision') ORDER BY config_key"]:
                cur.execute(sql);values.append(cur.fetchall())
        return values
    before=observed()
    try:
        try:
            with mysql_import_transaction(settings):
                if kind=='links_structure':links.set_links_for_structure('OLD',['FNEW'],expected=snapshot)
                elif kind=='links_fonction':links.set_links_for_fonction(1,['DEST'],expected=snapshot)
                elif kind=='order_structures':org.reorder_structures(['DEST','OLD'],expected=snapshot)
                elif kind=='order_subdivisions':org.reorder_subdivisions(1,[11,10],expected=snapshot)
                else:snapshot.sections[0].order=3;layout.save(snapshot)
                assert observed()==before
                if not commit:raise ValueError('synthetic caller rollback')
        except ValueError:assert not commit
        after=observed()
        if commit:assert after!=before and after[-1]!=before[-1]
        else:assert after==before
    finally:observer.close()


@pytest.mark.parametrize('direction',['structure','fonction'])
def test_links_lot23_visible_collection_comes_from_one_sql_statement(isolated_mysql,monkeypatch,direction):
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    calls=[];execute=pymysql.cursors.Cursor.execute
    def observed(cursor,query,args=None):calls.append(query);return execute(cursor,query,args)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',observed)
    result=getattr(repos['links'],'load_links_'+direction+'_snapshot')('OLD' if direction=='structure' else 1)
    queries=[query for query in calls if isinstance(query,str) and query.lstrip().upper().startswith('SELECT')]
    assert result.owner and len(queries)==1 and 'JSON_ARRAYAGG' in queries[0]



def _settings_lot24_config(settings,kind):
    if kind=='priority':
        from sme_gend.services.fonction_priority_service import load_fonction_priority_config,save_fonction_priority_config
        return 'fonction_priority_order',lambda:load_fonction_priority_config(settings),lambda cfg:save_fonction_priority_config(settings,cfg)
    if kind=='detaches':
        from sme_gend.services.detaches_config_service import DetachesConfigService
        service=DetachesConfigService(settings)
        return 'detaches_config',service.load,service.save
    if kind=='age':
        from sme_gend.services.limite_age_service import LimiteAgeService
        service=LimiteAgeService()
        return 'limite_age_config',lambda:service.load(settings),lambda cfg:service.save(cfg,settings)
    from sme_gend.services.soit_transmis_service import SoitTransmisService
    service=SoitTransmisService()
    return 'soit_transmis_config',lambda:service.load(settings),lambda cfg:service.save(cfg,settings)


def _settings_lot24_change(cfg,kind,index):
    if kind=='priority':cfg.fonction_ids=[2] if index==1 else [1,2]
    elif kind=='detaches':cfg.options.lieu_defaut='FIRST' if index==1 else 'SECOND'
    elif kind=='age':cfg.options.ville='FIRST' if index==1 else 'SECOND'
    else:cfg.ville='FIRST' if index==1 else 'SECOND'


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_stale_shared_config_cannot_overwrite_a_first_commit(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services import app_config_store
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr(app_config_store,'resolve_audit_actor',lambda:'synthetic')
    key,load,save=_settings_lot24_config(settings,kind)
    initial={'fonction_ids':[1]} if kind=='priority' else {}
    store=AppConfigRepository(settings);store.set_json(key,initial)
    first=load();second=load();_settings_lot24_change(first,kind,1);save(first)
    before=store.get_json(key);_settings_lot24_change(second,kind,2)
    with pytest.raises(ValueError,match='modifié|modifiée'):save(second)
    assert store.get_json(key)==before


def test_settings_lot24_explicit_empty_priority_is_not_bootstrapped_again(isolated_mysql,monkeypatch):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.fonction_priority_service import ensure_fonction_priority_config
    from sme_gend.services import app_config_store
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr(app_config_store,'resolve_audit_actor',lambda:'synthetic')
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE ref_fonction SET libelle='COMGEND' WHERE id=1")
    store=AppConfigRepository(settings);store.set_json('fonction_priority_order',{'version':1,'fonction_ids':[]})
    assert ensure_fonction_priority_config(settings).fonction_ids==[]
    assert store.get_json('fonction_priority_order')['fonction_ids']==[]



def _settings_lot24_setup(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services import auth_context
    settings,connect,params,repos,actions=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr(auth_context,'resolve_audit_actor',lambda:'synthetic')
    key,load,save=_settings_lot24_config(settings,kind)
    store=AppConfigRepository(settings)
    store.set_json(key,{'version':1,'fonction_ids':[1]} if kind=='priority' else {})
    cfg=load()  # Complete legacy defaults / statutory alignment before the baseline.
    save(cfg)
    return settings,connect,params,repos,store,key,load,save


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
@pytest.mark.parametrize('commit',[False,True])
def test_settings_lot24_version_and_notifications_follow_only_real_commit(isolated_mysql,monkeypatch,kind,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    cfg=load();old=cfg.write_token;before=store.get_json(key);revision=read_data_revision(settings)
    observer=connect(**params)
    def observed():
        with observer.cursor() as cur:
            cur.execute('SELECT config_key,payload FROM app_config ORDER BY config_key');return cur.fetchall()
    baseline=observed()
    try:
        try:
            with mysql_import_transaction(settings):
                _settings_lot24_change(cfg,kind,1);save(cfg)
                _settings_lot24_change(cfg,kind,2);save(cfg)
                assert cfg.write_token==old and observed()==baseline
                if not commit:raise ValueError('synthetic outer rollback')
        except ValueError:assert not commit
        if commit:
            assert cfg.write_token==fingerprint(store.get_json(key)) and cfg.write_token!=old
            assert observed()!=baseline and read_data_revision(settings)!=revision
        else:
            assert store.get_json(key)==before and cfg.write_token==old and observed()==baseline
            assert read_data_revision(settings)==revision
            save(cfg);assert cfg.write_token==fingerprint(store.get_json(key))
    finally:observer.close()


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_signal_failure_rolls_back_payload_and_can_retry(isolated_mysql,monkeypatch,kind):
    from sme_gend.services import data_sync
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    cfg=load();before=store.get_json(key);token=cfg.write_token;revision=data_sync.read_data_revision(settings)
    publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic signal failure')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    _settings_lot24_change(cfg,kind,1)
    with pytest.raises(OSError):save(cfg)
    assert store.get_json(key)==before and cfg.write_token==token and cfg._pending_write is None
    assert data_sync.read_data_revision(settings)==revision
    monkeypatch.setattr(data_sync,'publish_data_change',publish);save(cfg)
    assert cfg.write_token!=token and data_sync.read_data_revision(settings)!=revision


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_noop_does_not_publish_or_invalidate(isolated_mysql,monkeypatch,kind):
    from sme_gend.services import data_sync,shared_reference_write
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    cfg=load();before=store.get_json(key);revision=data_sync.read_data_revision(settings)
    def forbidden(*a,**kw):pytest.fail('No-op must not publish or invalidate')
    monkeypatch.setattr(data_sync,'publish_data_change',forbidden)
    monkeypatch.setattr(shared_reference_write,'_invalidate_reference_caches',forbidden)
    save(cfg)
    assert store.get_json(key)==before and data_sync.read_data_revision(settings)==revision


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_two_simultaneous_editors_have_one_winner(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    configs=[load(),load()];gate=Barrier(2);published=[];publish=data_sync.publish_data_change
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    def write(index):
        _settings_lot24_change(configs[index],kind,index+1);gate.wait(timeout=5)
        try:save(configs[index]);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,i) for i in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert sorted(results)==[False,True] and published==[True]
    assert store.get_json(key)==configs[results.index(True)].to_dict()


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_corrupt_or_failed_read_never_saves_defaults(isolated_mysql,monkeypatch,kind):
    from sme_gend.services import shared_config_write
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE app_config SET payload=%s WHERE config_key=%s',('[1,2]',key))
    with pytest.raises(RuntimeError,match='Impossible de charger'):load()
    def failed(*a,**kw):raise OSError('synthetic offline')
    monkeypatch.setattr(shared_config_write.MySQLConnection,'connect',failed)
    with pytest.raises(RuntimeError,match='Impossible de charger'):load()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT payload FROM app_config WHERE config_key=%s',(key,));assert json.loads(cur.fetchone()[0])==[1,2]


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_first_initializers_keep_the_winner(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services import shared_config_write,data_sync
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    key,load,save=_settings_lot24_config(settings,kind)
    if kind=='priority':
        from sme_gend.services.fonction_priority_service import ensure_fonction_priority_config
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE ref_fonction SET libelle='COMGEND' WHERE id=1")
        load=lambda:ensure_fonction_priority_config(settings)
    gate=Barrier(2);read=shared_config_write.load_shared_payload;publish=data_sync.publish_data_change;published=[]
    def synchronized(st,config_key):
        raw=read(st,config_key)
        if config_key==key and raw is None:gate.wait(timeout=5)
        return raw
    def observed(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(shared_config_write,'load_shared_payload',synchronized)
    monkeypatch.setattr(data_sync,'publish_data_change',observed)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(load) for _ in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert results[0].to_dict()==results[1].to_dict()
    assert results[0].write_token==results[1].write_token and published==[True]
    assert AppConfigRepository(settings).get_json(key)==results[0].to_dict()


def test_settings_lot24_new_priority_rejects_a_function_deleted_since_display(isolated_mysql,monkeypatch):
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,'priority')
    cfg=load();cfg.fonction_ids=[3];before=store.get_json(key)
    repos['fonction'].delete(3,expected=next(row for row in repos['fonction'].fetch_all() if row.id==3))
    with pytest.raises(ValueError,match='supprimée'):save(cfg)
    assert store.get_json(key)==before


@pytest.mark.parametrize('failure',['none','prune','signal'])
def test_settings_lot24_function_delete_and_priority_prune_are_atomic(isolated_mysql,monkeypatch,failure):
    from sme_gend.services import fonction_priority_service,data_sync
    from sme_gend.services.referentiel_service import ReferentielService
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,'priority')
    cfg=load();cfg.fonction_ids=[3];save(cfg)
    service=ReferentielService.__new__(ReferentielService);service._settings=settings;service._fonctions=repos['fonction']
    original=next(row for row in repos['fonction'].fetch_all() if row.id==3)
    before=store.get_json(key);revision=data_sync.read_data_revision(settings)
    def failed(*a,**kw):raise OSError('synthetic atomic failure')
    if failure=='prune':monkeypatch.setattr(fonction_priority_service,'prune_fonction_from_priority',failed)
    if failure=='signal':monkeypatch.setattr(data_sync,'publish_data_change',failed)
    if failure=='none':service.fonction_delete(3,expected=original)
    else:
        with pytest.raises(OSError):service.fonction_delete(3,expected=original)
    ids={row.id for row in repos['fonction'].fetch_all()}
    if failure=='none':assert 3 not in ids and store.get_json(key)['fonction_ids']==[] and data_sync.read_data_revision(settings)!=revision
    else:assert 3 in ids and store.get_json(key)==before and data_sync.read_data_revision(settings)==revision


@pytest.mark.parametrize('kind',['exclusions','views'])
def test_settings_lot24_distinct_concurrent_intents_are_both_preserved(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services import postes_tete_exclusions as exclusions
    from sme_gend.config import view_state
    from sme_gend.config import get_settings
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    gate=Barrier(2)
    def write(index):
        gate.wait(timeout=5)
        if kind=='exclusions':exclusions.exclude_expected_poste(settings,'OLD',None,'CHEF '+str(index))
        else:view_state.set_view_state('VIEW'+str(index),{'checked':True},'2026-09')
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,index) for index in range(2)]
        for job in jobs:job.result(timeout=15)
    if kind=='exclusions':assert len(exclusions.load_exclusion_keys(settings))==2
    else:assert all(view_state.get_view_state('VIEW'+str(i),'2026-09')=={'checked':True} for i in range(2))


@pytest.mark.parametrize('kind',['exclusions','views'])
def test_settings_lot24_targeted_intent_uses_current_read_after_an_old_rr_snapshot(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    from sme_gend.services import postes_tete_exclusions as exclusions
    from sme_gend.services.shared_config_write import mutate_shared_dict
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    key='postes_tete_exclus' if kind=='exclusions' else 'ui_view_state'
    store=AppConfigRepository(settings);store.set_json(key,{'items':[]} if kind=='exclusions' else {})
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT payload FROM app_config WHERE config_key=%s',(key,));cur.fetchone()
        with connect(**params) as other:
            with other.cursor() as cur:
                payload={'items':[{'structure':'DEST','subdivision':'','poste':'OTHER'}]} if kind=='exclusions' else {'OTHER':{'value':1}}
                cur.execute('UPDATE app_config SET payload=%s WHERE config_key=%s',(json.dumps(payload),key))
        if kind=='exclusions':exclusions.exclude_expected_poste(settings,'OLD',None,'NEW')
        else:mutate_shared_dict(settings,key,lambda raw:dict(raw,NEW={'value':2}))
    result=store.get_json(key)
    if kind=='exclusions':assert {item['poste'] for item in result['items']}=={'OTHER','NEW'}
    else:assert result=={'OTHER':{'value':1},'NEW':{'value':2}}


@pytest.mark.parametrize('kind',['exclusions','views'])
def test_settings_lot24_targeted_intent_rollback_preserves_unrelated_data(isolated_mysql,monkeypatch,kind):
    from sme_gend.services import postes_tete_exclusions as exclusions,data_sync
    from sme_gend.config import view_state
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    key='postes_tete_exclus' if kind=='exclusions' else 'ui_view_state';store=AppConfigRepository(settings)
    payload={'items':[{'structure':'OLD','subdivision':'','poste':'TARGET'},{'structure':'DEST','subdivision':'','poste':'OTHER'}],'future':42} if kind=='exclusions' else {'TARGET':{'v':1},'TARGET::2026-09':{'v':2},'OTHER':{'v':3}}
    store.set_json(key,payload);revision=data_sync.read_data_revision(settings);publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic signal failure')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    action=lambda:exclusions.clear_expected_poste(settings,'OLD','TARGET') if kind=='exclusions' else view_state.clear_view_state_for_view('TARGET')
    with pytest.raises(OSError):action()
    assert store.get_json(key)==payload and data_sync.read_data_revision(settings)==revision
    monkeypatch.setattr(data_sync,'publish_data_change',publish);action()
    result=store.get_json(key)
    if kind=='exclusions':assert result['future']==42 and [item['poste'] for item in result['items']]==['OTHER']
    else:assert result=={'OTHER':{'v':3}}



@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
def test_settings_lot24_handled_savepoint_failure_keeps_the_correct_pending_version(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.services import data_sync
    settings,connect,params,repos,store,key,load,save=_settings_lot24_setup(isolated_mysql,monkeypatch,kind)
    cfg=load();original=cfg.write_token;publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic second signal failure')
    with mysql_import_transaction(settings):
        _settings_lot24_change(cfg,kind,1);save(cfg);first=store.get_json(key)
        previous=cfg._pending_write
        _settings_lot24_change(cfg,kind,2)
        monkeypatch.setattr(data_sync,'publish_data_change',failed)
        with pytest.raises(OSError):save(cfg)
        assert cfg.write_token==original and cfg._pending_write is previous and store.get_json(key)==first
        monkeypatch.setattr(data_sync,'publish_data_change',publish);save(cfg)
        assert cfg.write_token==original
    assert cfg.write_token==fingerprint(store.get_json(key)) and cfg.to_dict()==store.get_json(key)



def _workspace_lot25_setup(isolated_mysql,monkeypatch):
    from sme_gend.config import sme_context
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    from sme_gend.services import auth_context,app_config_store
    settings,connect,params,repos,_=_reference_lot19_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr(app_config_store,'ensure_central_config_ready',lambda st:None)
    monkeypatch.setattr(sme_context,'_migrate_legacy_context_to_db',lambda st:None)
    monkeypatch.setattr(sme_context,'_load_local_ui_prefs',lambda:{'theme':'light'})
    monkeypatch.setattr(sme_context,'_save_local_ui_prefs',lambda ctx:None)
    monkeypatch.setattr(auth_context,'resolve_audit_actor',lambda:'synthetic')
    monkeypatch.setattr(SmeWorkspaceService,'get_active_mois',lambda self:'2026-09')
    from sme_gend.services.active_month_guard import ActiveMonthSnapshot
    from sme_gend.repositories.mysql_connection import mysql_database_key
    monkeypatch.setattr(SmeWorkspaceService,'load_active_month_snapshot',lambda self:ActiveMonthSnapshot(
        mysql_database_key(settings),((1,'2026-09','Ouvert','SME-202609'),),'a'*64))
    monkeypatch.setattr(SmeWorkspaceService,'list_closed_months',lambda self:[])
    store=AppConfigRepository(settings)
    store.set_json('sme_workspace',{'editeur_defaut':'ORIGINAL','jour_cloture':25,'prompt_mois_au_demarrage':True,'presences_mode':'present','future':{'preserved':True}})
    return settings,connect,params,store,SmeWorkspaceService(settings)


def test_workspace_lot25_stale_preferences_are_refused(isolated_mysql,monkeypatch):
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    first=service.load_workspace_payload();second=service.load_workspace_payload()
    first['editeur_defaut']='FIRST';service.save_workspace_payload(first);before=store.get_json('sme_workspace')
    second['editeur_defaut']='SECOND'
    with pytest.raises(ValueError,match='modifiée'):service.save_workspace_payload(second)
    assert store.get_json('sme_workspace')==before


def test_workspace_lot25_local_only_change_never_rewrites_shared_preferences(isolated_mysql,monkeypatch):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    ctx=load_sme_context();fresh=store.get_json('sme_workspace');fresh['editeur_defaut']='OTHER CLIENT'
    store.set_json('sme_workspace',fresh);ctx.theme='dark'
    save_sme_context(ctx,promote_operational_month=False)
    assert store.get_json('sme_workspace')==fresh


def test_workspace_lot25_shared_save_failure_is_explicit(isolated_mysql,monkeypatch):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.services import sme_workspace_service
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    ctx=load_sme_context();ctx.editeur_defaut='NEW'
    def failed(*a,**kw):raise OSError('synthetic shared write failure')
    monkeypatch.setattr(sme_workspace_service.SmeWorkspaceService,'save_workspace_payload',failed)
    with pytest.raises(OSError):save_sme_context(ctx,promote_operational_month=False)


def test_workspace_lot25_preferences_commit_publishes_a_shared_revision(isolated_mysql,monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    before=read_data_revision(settings);payload=service.load_workspace_payload();payload['jour_cloture']=28
    service.save_workspace_payload(payload)
    assert read_data_revision(settings)!=before


def test_workspace_lot25_signatory_save_never_reactivates_an_old_month(isolated_mysql,monkeypatch):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    ctx=load_sme_context();ctx.editeur_defaut='NEW';months=[]
    monkeypatch.setattr(SmeWorkspaceService,'get_active_mois',lambda self:'2026-10')
    monkeypatch.setattr(SmeWorkspaceService,'set_active_mois',lambda self,month:months.append(month))
    save_sme_context(ctx)
    assert months==[]



def _workspace_lot25_editor(service,route):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    if route=='service':return service.load_workspace_payload(),service.save_workspace_payload
    return load_sme_context(),lambda ctx:save_sme_context(ctx,promote_operational_month=False)


def _workspace_lot25_set(cfg,route,value):
    if route=='service':cfg['editeur_defaut']=value
    else:cfg.editeur_defaut=value


@pytest.mark.parametrize('field,value',[('editeur_defaut','NEW'),('jour_cloture',28),('prompt_mois_au_demarrage',False),('presences_mode','les_deux')])
def test_workspace_lot25_each_preference_uses_its_original_context_snapshot(isolated_mysql,monkeypatch,field,value):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    first=load_sme_context();second=load_sme_context()
    setattr(first,field,value);save_sme_context(first,promote_operational_month=False)
    before=store.get_json('sme_workspace');setattr(second,field,value)
    with pytest.raises(ValueError,match='modifiée'):save_sme_context(second,promote_operational_month=False)
    assert store.get_json('sme_workspace')==before and before['future']=={'preserved':True}


@pytest.mark.parametrize('exists',[False,True])
def test_workspace_lot25_simultaneous_clients_have_one_winner_and_signal(isolated_mysql,monkeypatch,exists):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services import data_sync
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    if not exists:
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("DELETE FROM app_config WHERE config_key='sme_workspace'")
    cfgs=[service.load_workspace_payload(),service.load_workspace_payload()];gate=Barrier(2)
    published=[];publish=data_sync.publish_data_change
    def notify(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',notify)
    def write(index):
        cfgs[index]['editeur_defaut']='CLIENT '+str(index);gate.wait(timeout=5)
        try:service.save_workspace_payload(cfgs[index]);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,index) for index in range(2)];results=[job.result(timeout=15) for job in jobs]
    assert sorted(results)==[False,True] and published==[True]
    assert store.get_json('sme_workspace')==cfgs[results.index(True)].to_dict()


@pytest.mark.parametrize('route',['service','context'])
@pytest.mark.parametrize('commit',[False,True])
def test_workspace_lot25_data_revision_and_snapshot_follow_real_commit(isolated_mysql,monkeypatch,route,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    cfg,save=_workspace_lot25_editor(service,route)
    version=lambda:cfg.write_token if route=='service' else cfg._workspace_snapshot.write_token
    old=version();before=store.get_json('sme_workspace');revision=read_data_revision(settings)
    observer=connect(**params)
    def observed():
        with observer.cursor() as cur:
            cur.execute('SELECT config_key,payload FROM app_config ORDER BY config_key');return cur.fetchall()
    baseline=observed()
    try:
        try:
            with mysql_import_transaction(settings):
                _workspace_lot25_set(cfg,route,'FIRST');save(cfg)
                _workspace_lot25_set(cfg,route,'SECOND');save(cfg)
                assert observed()==baseline and version()==old
                if not commit:raise ValueError('synthetic outer rollback')
        except ValueError:assert not commit
        if commit:
            assert version()==fingerprint(store.get_json('sme_workspace')) and observed()!=baseline
            assert read_data_revision(settings)!=revision
        else:
            assert observed()==baseline and version()==old and store.get_json('sme_workspace')==before
            save(cfg);assert version()==fingerprint(store.get_json('sme_workspace'))
    finally:observer.close()


@pytest.mark.parametrize('route',['service','context'])
@pytest.mark.parametrize('joined',[False,True])
def test_workspace_lot25_failed_signal_rolls_back_and_retry_remains_possible(isolated_mysql,monkeypatch,route,joined):
    from contextlib import nullcontext
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services import data_sync
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    cfg,save=_workspace_lot25_editor(service,route);publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic signal failure')
    with mysql_import_transaction(settings) if joined else nullcontext():
        _workspace_lot25_set(cfg,route,'FIRST');save(cfg);before=store.get_json('sme_workspace')
        _workspace_lot25_set(cfg,route,'SECOND');monkeypatch.setattr(data_sync,'publish_data_change',failed)
        with pytest.raises(OSError):save(cfg)
        assert store.get_json('sme_workspace')==before
        monkeypatch.setattr(data_sync,'publish_data_change',publish);save(cfg)
    assert store.get_json('sme_workspace')['editeur_defaut']=='SECOND'


def test_workspace_lot25_unchanged_context_needs_no_sql_even_if_snapshot_is_stale(isolated_mysql,monkeypatch):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.services import shared_config_write
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    ctx=load_sme_context()
    def forbidden(*a,**kw):pytest.fail('An unchanged context must not access MySQL')
    monkeypatch.setattr(shared_config_write.MySQLConnection,'connect',forbidden)
    ctx.hidden_columns=['local'];save_sme_context(ctx)


def test_workspace_lot25_unchanged_preferences_do_not_emit_a_notification(isolated_mysql,monkeypatch):
    from sme_gend.services import data_sync
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    cfg=service.load_workspace_payload();before=store.get_json('sme_workspace')
    monkeypatch.setattr(data_sync,'publish_data_change',lambda *a,**kw:pytest.fail('No-op emitted a signal'))
    service.save_workspace_payload(cfg)
    assert store.get_json('sme_workspace')==before


@pytest.mark.parametrize('failure',['invalid','offline'])
def test_workspace_lot25_failed_load_cannot_replace_shared_state_with_defaults(isolated_mysql,monkeypatch,failure):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.services import shared_config_write
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    if failure=='invalid':
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE app_config SET payload='[]' WHERE config_key='sme_workspace'")
    else:
        def failed(*a,**kw):raise OSError('synthetic offline')
        monkeypatch.setattr(shared_config_write.MySQLConnection,'connect',failed)
    with pytest.raises(RuntimeError,match='Impossible de charger'):service.load_workspace_payload()
    ctx=load_sme_context();assert ctx._workspace_snapshot is None
    ctx.editeur_defaut='DEFAULT'
    with pytest.raises(ValueError,match="n'a pas pu être chargé"):save_sme_context(ctx,promote_operational_month=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM app_config WHERE config_key='sme_workspace'")
            result=json.loads(cur.fetchone()[0])
    assert result==[] if failure=='invalid' else result['editeur_defaut']=='ORIGINAL'


def test_workspace_lot25_legacy_initializer_does_not_replace_a_concurrent_winner(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config import sme_context
    migrate=sme_context._migrate_legacy_context_to_db
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    settings,connect,params,store,service=_workspace_lot25_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("DELETE FROM app_config WHERE config_key='sme_workspace'")
    legacy=tmp_path/'legacy.json';legacy.write_text(json.dumps({'editeur_defaut':'OBSOLETE','mois_actif':'2020-01'}))
    monkeypatch.setattr(sme_context,'_legacy_context_file',lambda:legacy)
    load=SmeWorkspaceService.load_workspace_payload;first=[]
    def competing(self):
        cfg=load(self)
        if not first:
            first.append(True);store.set_json('sme_workspace',{'editeur_defaut':'CONCURRENT'})
        return cfg
    monkeypatch.setattr(SmeWorkspaceService,'load_workspace_payload',competing)
    monkeypatch.setattr(SmeWorkspaceService,'set_active_mois',lambda *a:pytest.fail('Old local month reactivated'))
    migrate(settings)
    assert store.get_json('sme_workspace')=={'editeur_defaut':'CONCURRENT'}



def _month_lot26_setup(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config import sme_context
    from sme_gend.services import app_config_store
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    settings,connect,params,closure=_lot11_closure(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr(app_config_store,'ensure_central_config_ready',lambda st:None)
    monkeypatch.setattr(sme_context,'_migrate_legacy_context_to_db',lambda st:None)
    monkeypatch.setattr(sme_context,'_load_local_ui_prefs',lambda:{})
    monkeypatch.setattr(sme_context,'_save_local_ui_prefs',lambda ctx:None)
    return settings,connect,params,closure,SmeWorkspaceService(settings)


def _month_lot26_load(service):
    return service.load_active_month_snapshot()


def _month_lot26_set(service,month,snapshot):
    return service.set_active_mois(month,expected=snapshot)


def test_month_lot26_stale_active_month_choice_is_refused(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    first=_month_lot26_load(service);second=_month_lot26_load(service)
    _month_lot26_set(service,'2026-10',first)
    with pytest.raises(ValueError,match='modifié|changé'):_month_lot26_set(service,'2026-11',second)
    assert service.get_active_mois()=='2026-10'


def test_month_lot26_context_keeps_the_displayed_operational_month_version(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    first=load_sme_context();second=load_sme_context()
    first.mois_actif='2026-10';save_sme_context(first)
    second.mois_actif='2026-11'
    with pytest.raises(ValueError,match='modifié|changé'):save_sme_context(second)
    assert service.get_active_mois()=='2026-10'


def test_month_lot26_return_to_the_old_month_does_not_revive_an_old_snapshot(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    old=_month_lot26_load(service);_month_lot26_set(service,'2026-10',old)
    _month_lot26_set(service,'2026-09',_month_lot26_load(service))
    with pytest.raises(ValueError,match='modifié|changé'):_month_lot26_set(service,'2026-11',old)


def test_month_lot26_multiple_open_months_are_reported_instead_of_choosing_the_latest(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE annee_mois='2026-10'")
    with pytest.raises((ValueError,RuntimeError),match='Plusieurs|plusieurs'):_month_lot26_load(service)


def test_month_lot26_stale_month_cannot_commit_new_workspace_preferences(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    old=load_sme_context();store=AppConfigRepository(settings);before=store.get_json('sme_workspace')
    _month_lot26_set(service,'2026-10',_month_lot26_load(service))
    old.mois_actif='2026-11';old.prompt_mois_au_demarrage=False
    with pytest.raises(ValueError,match='modifié|changé'):save_sme_context(old)
    assert store.get_json('sme_workspace')==before



def _month_lot26_observe(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,annee_mois,statut,codsme FROM sme_periode ORDER BY id');periods=cur.fetchall()
            cur.execute('SELECT id,est_actuelle,date_debut,date_fin FROM carriere_situation ORDER BY id');careers=cur.fetchall()
            cur.execute("SELECT config_key,payload FROM app_config WHERE config_key IN ('active_month_revision','shared_data_revision','sme_workspace') ORDER BY config_key");config=cur.fetchall()
            cur.execute('SELECT COUNT(*) FROM audit_journal');audit=cur.fetchone()
    return periods,careers,config,audit


def _month_lot26_writer(service,route):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    if route=='context':
        cfg=load_sme_context()
        def save(target):cfg.mois_actif=target;return save_sme_context(cfg,force_month_change=True)
        return cfg,save
    cfg=service.load_active_month_snapshot()
    def save(target):return service.set_active_mois(target,expected=service.load_active_month_snapshot())
    return cfg,save


@pytest.mark.parametrize('route',['service','context'])
@pytest.mark.parametrize('commit',[False,True])
def test_month_lot26_statuses_projection_revision_and_memory_follow_only_real_commit(isolated_mysql,monkeypatch,tmp_path,route,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    cfg,save=_month_lot26_writer(service,route);before=_month_lot26_observe(connect,params)
    original=cfg._month_snapshot if route=='context' else cfg
    try:
        with mysql_import_transaction(settings):
            save('2026-10');save('2026-09')
            assert _month_lot26_observe(connect,params)==before
            if route=='context':assert cfg._month_snapshot is original
            if not commit:raise ValueError('synthetic outer rollback')
    except ValueError:assert not commit
    after=_month_lot26_observe(connect,params)
    if commit:
        assert after!=before and service.get_active_mois()=='2026-09'
        if route=='context':assert cfg._month_snapshot==service.load_active_month_snapshot() and cfg._loaded_mois_actif=='2026-09'
    else:
        assert after==before
        if route=='context':assert cfg._month_snapshot is original
        save('2026-10');assert service.get_active_mois()=='2026-10'


@pytest.mark.parametrize('route',['service','context'])
@pytest.mark.parametrize('joined',[False,True])
def test_month_lot26_signal_failure_keeps_data_and_versions_then_allows_retry(isolated_mysql,monkeypatch,tmp_path,route,joined):
    from contextlib import nullcontext
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services import data_sync
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    cfg,save=_month_lot26_writer(service,route);before=_month_lot26_observe(connect,params)
    old=cfg._month_snapshot if route=='context' else cfg;publish=data_sync.publish_data_change
    def failed(*a,**kw):raise OSError('synthetic month signal failure')
    monkeypatch.setattr(data_sync,'publish_data_change',failed)
    with mysql_import_transaction(settings) if joined else nullcontext():
        with pytest.raises(OSError):save('2026-10')
    assert _month_lot26_observe(connect,params)==before
    if route=='context':assert cfg._month_snapshot is old and cfg._pending_month_change is None
    monkeypatch.setattr(data_sync,'publish_data_change',publish);save('2026-10')
    assert service.get_active_mois()=='2026-10'


@pytest.mark.parametrize('failure',['audit','projection','transition_marker'])
def test_month_lot26_combined_preferences_and_month_failure_roll_back_together(isolated_mysql,monkeypatch,tmp_path,failure):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    from sme_gend.services import sme_workspace_service,active_month_guard
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    ctx=load_sme_context();old=ctx._month_snapshot;before=_month_lot26_observe(connect,params)
    def failed(*a,**kw):raise OSError('synthetic atomic month failure')
    if failure=='audit':monkeypatch.setattr(AppConfigRepository,'append_audit',failed)
    elif failure=='projection':monkeypatch.setattr(sme_workspace_service,'refresh_est_actuelle_flags',failed)
    else:
        execute=pymysql.cursors.Cursor.execute
        def intercept(cursor,query,args=None):
            if isinstance(args,tuple) and args and args[0]==active_month_guard.KEY_MONTH_REVISION and query.startswith('INSERT'):failed()
            return execute(cursor,query,args)
        monkeypatch.setattr(pymysql.cursors.Cursor,'execute',intercept)
    ctx.mois_actif='2026-10';ctx.editeur_defaut='NEW'
    with pytest.raises(OSError):save_sme_context(ctx)
    assert _month_lot26_observe(connect,params)==before and ctx._month_snapshot is old
    assert ctx.editeur_defaut=='NEW' and ctx.mois_actif=='2026-10'


@pytest.mark.parametrize('same_target',[False,True])
def test_month_lot26_simultaneous_choices_have_one_commit_and_one_conflict(isolated_mysql,monkeypatch,tmp_path,same_target):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    versions=[service.load_active_month_snapshot(),service.load_active_month_snapshot()];gate=Barrier(2)
    published=[];publish=data_sync.publish_data_change
    def notify(*a,**kw):published.append(True);return publish(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',notify)
    targets=['2026-10','2026-10' if same_target else '2026-11']
    def write(index):
        gate.wait(timeout=5)
        try:service.set_active_mois(targets[index],expected=versions[index]);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,i) for i in range(2)];results=[job.result(timeout=20) for job in jobs]
    assert sorted(results)==[False,True] and published==[True]
    assert service.get_active_mois()==targets[results.index(True)]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT statut FROM sme_periode WHERE annee_mois='2026-08'");assert cur.fetchone()==('Réouvert',)


def test_month_lot26_empty_database_has_one_first_activation(isolated_mysql,monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM carriere_situation');cur.execute('DELETE FROM sme_periode')
    versions=[service.load_active_month_snapshot(),service.load_active_month_snapshot()];gate=Barrier(2)
    def write(index):
        gate.wait(timeout=5)
        try:service.set_active_mois(['2026-09','2026-10'][index],expected=versions[index]);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs=[workers.submit(write,i) for i in range(2)];assert sorted(job.result(timeout=20) for job in jobs)==[False,True]
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("SELECT COUNT(*) FROM sme_periode WHERE statut='Ouvert'");assert cur.fetchone()==(1,)


def test_month_lot26_current_check_bypasses_an_older_repeatable_read_snapshot(isolated_mysql,monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    old=service.load_active_month_snapshot()
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('SELECT * FROM sme_periode');cur.fetchall()
        with ThreadPoolExecutor(max_workers=1) as workers:workers.submit(service.set_active_mois,'2026-10',expected=old).result(timeout=20)
        with pytest.raises(ValueError,match='changé'):service.set_active_mois('2026-11',expected=old)
    assert service.get_active_mois()=='2026-10'


def test_month_lot26_explicit_choice_of_the_old_displayed_month_still_checks_freshness(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config.sme_context import load_sme_context,save_sme_context
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    ctx=load_sme_context();service.set_active_mois('2026-10',expected=service.load_active_month_snapshot())
    with pytest.raises(ValueError,match='changé'):save_sme_context(ctx,force_month_change=True)
    assert service.get_active_mois()=='2026-10'


def test_month_lot26_noop_has_no_audit_or_shared_notification(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import data_sync
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    before=_month_lot26_observe(connect,params);version=service.load_active_month_snapshot()
    monkeypatch.setattr(data_sync,'publish_data_change',lambda *a,**kw:pytest.fail('No-op emitted a notification'))
    service.set_active_mois('2026-09',expected=version)
    # The neutral reference lock row may be initialized; it has no revision token.
    after=_month_lot26_observe(connect,params)
    assert after[0:2]==before[0:2] and after[3]==before[3]
    assert service.load_active_month_snapshot()==version


@pytest.mark.parametrize('aba',[False,True])
def test_month_lot26_month_changed_during_backup_prevents_a_stale_closure(isolated_mysql,monkeypatch,tmp_path,aba):
    from sme_gend.services import mysql_backup_service
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    expected=service.load_active_month_snapshot();backup_file=tmp_path/'new_safety.sql';backup_file.write_bytes(b'SYNTHETIC BACKUP')
    def backup(*a):
        service.set_active_mois('2026-10',expected=service.load_active_month_snapshot())
        service.set_active_mois('2026-09' if aba else '2026-11',expected=service.load_active_month_snapshot())
        return backup_file,[]
    monkeypatch.setattr(mysql_backup_service,'backup_mysql_database',backup)
    with pytest.raises(ValueError,match='changé'):closure.run_piste_a_month_closure(settings,'2026-09','2026-10',expected=expected)
    assert service.get_active_mois()==('2026-09' if aba else '2026-11')


def test_month_lot26_archive_cannot_replace_existing_live_statuses(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.active_month_guard import period_state_write,preserved_import_period
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    with period_state_write(settings,reason='career_import'):
        with __import__('sme_gend.repositories.mysql_connection',fromlist=['MySQLConnection']).MySQLConnection(settings).connect() as conn:
            preserved_import_period(conn,'2026-09',statut='Fermé',selected_month='2026-09')
            preserved_import_period(conn,'2026-08',statut='Ouvert',selected_month='2026-09')
            preserved_import_period(conn,'2026-11',statut='Ouvert',selected_month='2026-11')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois')
            assert cur.fetchall()==(('2026-08','Réouvert'),('2026-09','Ouvert'),('2026-10','Fermé'),('2026-11','Fermé'))


@pytest.mark.parametrize('fail_audit',[False,True])
def test_month_lot26_reopening_preserves_active_month_and_is_transactional(isolated_mysql,monkeypatch,tmp_path,fail_audit):
    from sme_gend.services import sme_editing_policy as policy
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    before=_month_lot26_observe(connect,params)
    if fail_audit:
        def failed(**kw):raise OSError('synthetic reopen audit failure')
        monkeypatch.setattr(policy,'append_audit_event',failed)
        with pytest.raises(OSError):policy.reopen_closed_month_for_corrections(settings,'2026-10',auteur='synthetic',motif='test')
        assert _month_lot26_observe(connect,params)==before
    else:
        result=policy.reopen_closed_month_for_corrections(settings,'2026-10',auteur='synthetic',motif='test')
        assert result.rows_updated==1 and service.get_active_mois()=='2026-09'



@pytest.mark.parametrize('commit',[False,True])
def test_month_lot26_import_callback_observes_only_the_real_commit(isolated_mysql,monkeypatch,tmp_path,commit):
    from sme_gend.services import mysql_bundle_import as bundle
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup',lambda *a,**kw:tmp_path/'synthetic-safety.sql')
    def imported(*a,**kw):
        service.set_active_mois('2026-10',expected=service.load_active_month_snapshot())
        return {'sme_mois':'2026-09'}
    monkeypatch.setattr(bundle,'_run_mysql_bundle_import',imported)
    observed=[];before=_month_lot26_observe(connect,params)
    try:
        with mysql_import_transaction(settings):
            bundle.import_full_mysql_bundle_from_access(settings,settings,mois='2026-09',on_month_committed=observed.append)
            assert observed==[] and _month_lot26_observe(connect,params)==before
            if not commit:raise ValueError('synthetic import caller rollback')
    except ValueError:assert not commit
    if commit:assert observed==[service.load_active_month_snapshot()] and service.get_active_mois()=='2026-10'
    else:assert observed==[] and _month_lot26_observe(connect,params)==before


def test_month_lot26_import_remains_committed_if_another_post_prevents_its_closure(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import mysql_bundle_import as bundle
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.mysql_import_period import ensure_sme_periode
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup',lambda *a,**kw:tmp_path/'synthetic-safety.sql')
    source=tmp_path/'synthetic.mdb';source.write_bytes(b'SYNTHETIC SOURCE')
    def imported(*a,**kw):
        with MySQLConnection(settings).connect() as conn:ensure_sme_periode(conn,'2030-01',statut='Fermé')
        return {'sme_mois':'2026-09','referentials':{'warnings':[]}}
    monkeypatch.setattr(bundle,'_run_mysql_bundle_import',imported)
    def archive(*a):
        service.set_active_mois('2026-10',expected=service.load_active_month_snapshot())
        path=tmp_path/'archived.mdb';path.write_bytes(b'SYNTHETIC ARCHIVE');return path
    monkeypatch.setattr(closure,'_snapshot_file',archive)
    with pytest.raises(ValueError,match='import MySQL est enregistré'):
        closure.import_existing_db_for_month(settings,source,'2026-09','2026-11')
    assert service.get_active_mois()=='2026-10'
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("SELECT statut FROM sme_periode WHERE annee_mois='2030-01'");assert cur.fetchone()==('Fermé',)


def test_month_lot26_reopening_on_another_post_invalidates_an_old_month_choice(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.sme_editing_policy import reopen_closed_month_for_corrections
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    old=service.load_active_month_snapshot()
    reopen_closed_month_for_corrections(settings,'2026-10',auteur='synthetic',motif='test')
    with pytest.raises(ValueError,match='changé'):service.set_active_mois('2026-11',expected=old)
    assert service.get_active_mois()=='2026-09'


def test_month_lot26_low_level_period_update_cannot_create_two_open_months(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    before=_month_lot26_observe(connect,params)
    with pytest.raises(ValueError,match='Plusieurs'):service._sme.update('SME-202610','2026-10','Ouvert')
    assert _month_lot26_observe(connect,params)==before



def test_month_lot26_legacy_first_month_cannot_replace_another_clients_initialization(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.config import sme_context
    migrate=sme_context._migrate_legacy_context_to_db
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params,closure,service=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM carriere_situation');cur.execute('DELETE FROM sme_periode')
            cur.execute("DELETE FROM app_config WHERE config_key='sme_workspace'")
    legacy=tmp_path/'legacy-context.json';legacy.write_text(json.dumps({'mois_actif':'2026-09','editeur_defaut':'SYNTHETIC'}))
    monkeypatch.setattr(sme_context,'_legacy_context_file',lambda:legacy)
    load=SmeWorkspaceService.load_active_month_snapshot;observed=[]
    def competing(self):
        snapshot=load(self)
        if not observed and not snapshot.rows:
            observed.append(True);service.set_active_mois('2026-10',expected=snapshot)
        return snapshot
    monkeypatch.setattr(SmeWorkspaceService,'load_active_month_snapshot',competing)
    migrate(settings)
    assert service.get_active_mois()=='2026-10'



def _task_lot27_resolve(tasks,identifier,snapshot):
    return tasks.resolve_task(identifier,expected=snapshot)


def test_business_lot27_public_history_update_requires_a_displayed_snapshot(isolated_mysql,monkeypatch):
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    identifier=service.add_diplome(row,categorie='CIVIL',libelle='SYNTHETIC ORIGINAL')
    with pytest.raises(ValueError,match='Version|instantané'):service.update_diplome(row,identifier,categorie='CIVIL',libelle='STALE')
    assert service.list_diplomes(row)[0].libelle=='SYNTHETIC ORIGINAL'


def test_business_lot27_nested_history_write_still_notifies_at_the_real_commit(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    old=read_data_revision(settings)
    with mysql_import_transaction(settings):service.add_diplome(row,categorie='CIVIL',libelle='SYNTHETIC')
    assert read_data_revision(settings)!=old


def test_business_lot27_task_changed_since_display_cannot_be_resolved(isolated_mysql,monkeypatch):
    settings,connect,params,repo,tasks,report,insert,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    shown=next(row for row in tasks.list_open() if row.id==1)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE workflow_task SET detail='OTHER POST' WHERE id=1")
    with pytest.raises(ValueError,match='modifiée|changé'):_task_lot27_resolve(tasks,1,shown)
    assert any(row.id==1 and row.detail=='OTHER POST' for row in tasks.list_open())


def _sex_lot27_setup(isolated_mysql,monkeypatch):
    from sme_gend.services import personnel_sexe_reference_sync as sync
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE agent(id INT PRIMARY KEY,legacy_access_id VARCHAR(40),noms VARCHAR(100),sexe CHAR(1),matricule_militaire VARCHAR(40)) ENGINE=InnoDB')
            cur.execute("INSERT INTO agent VALUES(1,'LEG1','SYNTHETIC ONE','M','MAT1'),(2,'LEG2','SYNTHETIC TWO','M','MAT2')")
            cur.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
    snapshots=sync._load_agents(settings)
    plan=sync.SexeSyncPlan()
    for row in snapshots:
        action=sync.SexeSyncAction(row.agent_id,row.legacy_access_id,row.noms,'nom_exact','F',row.matricule_militaire,row.sexe,row.matricule_militaire,new_sexe='F',update_sexe=True)
        if hasattr(action,'write_token'):action.write_token=row.write_token;action._write_database=row._write_database
        plan.actions.append(action)
    return settings,connect,params,sync,plan


def test_business_lot27_sex_reference_plan_cannot_overwrite_a_concurrent_edit(isolated_mysql,monkeypatch):
    settings,connect,params,sync,plan=_sex_lot27_setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE agent SET matricule_militaire='OTHER POST' WHERE id=2")
    with pytest.raises(ValueError,match='modifié|changé'):sync.apply_sexe_sync_plan(settings,plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT sexe FROM agent ORDER BY id');assert cur.fetchall()==(('M',),('M',))


def test_business_lot27_sex_reference_plan_publishes_a_shared_revision(isolated_mysql,monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,sync,plan=_sex_lot27_setup(isolated_mysql,monkeypatch)
    old=read_data_revision(settings);result=sync.apply_sexe_sync_plan(settings,plan)
    assert result['sexe']==2 and read_data_revision(settings)!=old



@pytest.mark.parametrize('archive',[False,True])
def test_performance_lot27_dashboard_context_benchmark(isolated_mysql,monkeypatch,tmp_path,archive):
    import cProfile,hashlib,pstats,statistics,time
    from dataclasses import asdict,replace
    from types import SimpleNamespace
    from sme_gend.services import dashboard_service as dash,liste_generale_cache
    from sme_gend.config import sme_context,liste_generale_scope
    from sme_gend.models.liste_generale import ListeGeneraleRow
    settings,connect,params,closure,workspace=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    data_settings=replace(settings,mysql_view_mois='2026-08' if archive else None)
    rows=[ListeGeneraleRow(id=str(i),noms='SYNTHETIC '+str(i),date_naissance=None,lieu_naissance=None,grade='G'+str(i%12),date_grade=None,date_service=None,ndecision=None,matricule_militaire=None,matricule_solde=None,position_admin='Présent' if i%3 else 'Malade',fonction=None,subdivision_arme=None,diplome=None,date_obtention=None,structure='S'+str(i%35),obs=None,ordre_tri=None,presseance=None) for i in range(5000)]
    service=dash.DashboardService.__new__(dash.DashboardService)
    service._settings=settings;service._data_settings=data_settings
    service._liste=SimpleNamespace(fetch_all=lambda:rows)
    service._structures=SimpleNamespace(fetch_all=lambda:[]);service._grades=SimpleNamespace(fetch_all=lambda:[])
    service._sme=SimpleNamespace(table_exists=lambda:False)
    service._gain_perte=SimpleNamespace(fetch_for_month=lambda month:[])
    service._snapshot_cache_key=None;service._snapshot_cache_val=None
    monkeypatch.setattr(liste_generale_cache,'get_liste_rows',lambda st,loader:rows)
    monkeypatch.setattr(dash,'padmin_counts_as_present',lambda value,*a,**kw:value=='Présent')
    monkeypatch.setattr(dash,'build_career_alerts_kpi_card',lambda *a,**kw:None)
    monkeypatch.setattr(dash,'build_conge_long_alert_kpi_card',lambda *a,**kw:None)
    monkeypatch.setattr('sme_gend.repositories.subdivision_repository.SubdivisionRepository',lambda st:SimpleNamespace(fetch_flat_hierarchy=lambda:[]))
    counts={'context':0,'sql':0};original=sme_context.load_sme_context;execute=pymysql.cursors.Cursor.execute
    def context():counts['context']+=1;return original()
    def observed(cursor,query,args=None):counts['sql']+=1;return execute(cursor,query,args)
    monkeypatch.setattr(sme_context,'load_sme_context',context);monkeypatch.setattr(liste_generale_scope,'load_sme_context',context)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',observed)
    def run():service._invalidate_snapshot_cache();return service.snapshot()
    run();times=[];observations=[];digest=None
    for _ in range(6):
        counts.update(context=0,sql=0);started=time.perf_counter();result=run();times.append(time.perf_counter()-started)
        observations.append(dict(counts));current=hashlib.sha256(json.dumps(asdict(result),sort_keys=True,default=str).encode()).hexdigest()
        assert digest in (None,current);digest=current
    profile=cProfile.Profile();profile.enable();run();profile.disable()
    top=sorted(pstats.Stats(profile).stats.items(),key=lambda item:item[1][3],reverse=True)[:12]
    source=Path(__file__).resolve().parents[2];base=source/'build/corrections_20261031_lot27'
    initial=json.loads((base/'baseline_hashes.json').read_text(encoding='utf-8'))
    path='src/sme_gend/services/dashboard_service.py';current_hash=hashlib.sha256((source/path).read_bytes()).hexdigest()
    stage='before' if current_hash==initial[path] else 'after'
    evidence=dict(stage=stage,archive=archive,rows=5000,repeats=6,median_seconds=statistics.median(times),seconds=times,
        calls=observations,snapshot_sha256=digest,native_mysql_context=True,synthetic_cached_personnel_and_reference_repositories=True,
        source_sha256=current_hash,profile_top=[dict(file=Path(key[0]).name,line=key[1],function=key[2],calls=value[1],cumulative_seconds=value[3]) for key,value in top])
    (tmp_path/('dashboard_benchmark_'+stage+('_archive' if archive else '_active')+'.json')).write_text(json.dumps(evidence,indent=2),encoding='utf-8')



@pytest.mark.parametrize('commit',[False,True])
def test_business_lot27_sex_batch_and_notification_follow_outer_commit(isolated_mysql,monkeypatch,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,sync,plan=_sex_lot27_setup(isolated_mysql,monkeypatch)
    before=read_data_revision(settings)
    try:
        with mysql_import_transaction(settings):
            sync.apply_sexe_sync_plan(settings,plan)
            with connect(**params) as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT sexe FROM agent ORDER BY id');assert cur.fetchall()==(('M',),('M',))
                    cur.execute('SELECT COUNT(*) FROM app_config');assert cur.fetchone()==(0,)
            if not commit:raise ValueError('synthetic rollback')
    except ValueError:assert not commit
    assert (read_data_revision(settings)!=before)==commit
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sexe FROM agent ORDER BY id');assert cur.fetchall()==((('F',),('F',)) if commit else (('M',),('M',)))


@pytest.mark.parametrize('invalid',['missing','database','duplicate','deleted'])
def test_business_lot27_sex_plan_invalid_versions_write_nothing(isolated_mysql,monkeypatch,invalid):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,sync,plan=_sex_lot27_setup(isolated_mysql,monkeypatch)
    if invalid=='missing':plan.actions[1].write_token=None
    elif invalid=='database':plan.actions[1]._write_database=('other',)
    elif invalid=='duplicate':plan.actions.append(plan.actions[0])
    else:
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM agent WHERE id=2')
    old=read_data_revision(settings)
    with pytest.raises(ValueError):sync.apply_sexe_sync_plan(settings,plan)
    assert read_data_revision(settings)==old
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT sexe FROM agent WHERE id=1');assert cur.fetchone()==('M',)


def test_business_lot27_unchanged_sex_plan_does_not_notify(isolated_mysql,monkeypatch):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,sync,plan=_sex_lot27_setup(isolated_mysql,monkeypatch)
    for action in plan.actions:action.new_sexe='M'
    old=read_data_revision(settings);result=sync.apply_sexe_sync_plan(settings,plan)
    assert result['sexe']==0 and read_data_revision(settings)==old


@pytest.mark.parametrize('invalid',['missing','database','id'])
def test_business_lot27_task_missing_or_foreign_version_is_rejected(isolated_mysql,monkeypatch,invalid):
    from copy import deepcopy
    settings,connect,params,repo,tasks,report,insert,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    shown=deepcopy(next(row for row in tasks.list_open() if row.id==1))
    if invalid=='missing':shown.write_token=None
    elif invalid=='database':shown._write_database=('other',)
    else:shown.id=2
    with pytest.raises(ValueError):tasks.resolve_task(1,expected=shown)
    assert any(row.id==1 for row in tasks.list_open())


def test_business_lot27_nested_caught_history_failure_rolls_back_its_savepoint(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    old=read_data_revision(settings)
    original=service._repo.insert_diplome
    def failed(*args,**kwargs):original(*args,**kwargs);raise ValueError('synthetic insert failure')
    monkeypatch.setattr(service._repo,'insert_diplome',failed)
    with mysql_import_transaction(settings):
        with pytest.raises(ValueError):service.add_diplome(row,categorie='CIVIL',libelle='SYNTHETIC')
    assert service.list_diplomes(row)==[] and read_data_revision(settings)==old



def test_lot_active_month_recovery_is_atomic_audited_and_notifies_posts(isolated_mysql,monkeypatch):
    settings,connect,params=isolated_mysql
    from sme_gend.services import active_month_guard as guard
    audit=[];refresh=[]
    monkeypatch.setattr('sme_gend.services.mysql_import_period.refresh_est_actuelle_flags',lambda conn,month:refresh.append(('active',month)))
    monkeypatch.setattr('sme_gend.services.mysql_import_period.refresh_carriere_segment_dates',lambda conn:refresh.append(('dates',None)))
    monkeypatch.setattr('sme_gend.services.audit_log.append_audit_event',lambda **kw:audit.append(kw))
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.invalidate_editing_state_cache',lambda:None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE sme_periode(id BIGINT PRIMARY KEY,annee_mois CHAR(7),codsme VARCHAR(32),statut VARCHAR(64)) ENGINE=InnoDB")
            cur.execute("CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)) ENGINE=InnoDB")
            cur.executemany("INSERT INTO sme_periode VALUES(%s,%s,%s,%s)",[(1,'2026-09','SME-202609','Ouvert'),(2,'2026-10','SME-202610','Ouvert'),(3,'2026-08','SME-202608','Fermé'),(4,'2026-07','SME-202607','Réouvert')])
    assert guard.list_open_months_for_recovery(settings)==(('2026-09',('SME-202609',)),('2026-10',('SME-202610',)))
    result=guard.recover_multiple_open_months(settings,'2026-09',expected_open_months=('2026-09','2026-10'),actor='ADMIN.TEST')
    assert result=={'active_month':'2026-09','closed_months':('2026-10',),'rows_closed':1,'already_resolved':False}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois")
            assert cur.fetchall()==(('2026-07','Réouvert'),('2026-08','Fermé'),('2026-09','Ouvert'),('2026-10','Fermé'))
            cur.execute("SELECT config_key FROM app_config ORDER BY config_key")
            keys={row[0] for row in cur.fetchall()}
            assert guard.KEY_MONTH_REVISION in keys and 'shared_data_revision' in keys
    assert refresh==[('active','2026-09'),('dates',None)]
    assert len(audit)==1 and audit[0]['actor']=='ADMIN.TEST'
    assert audit[0]['action']=='sme_active_month_recovery' and audit[0]['mois_fermes']==['2026-10']


def test_lot_active_month_recovery_rolls_back_when_career_refresh_fails(isolated_mysql,monkeypatch):
    settings,connect,params=isolated_mysql
    from sme_gend.services import active_month_guard as guard
    monkeypatch.setattr('sme_gend.services.mysql_import_period.refresh_est_actuelle_flags',lambda *a:None)
    monkeypatch.setattr('sme_gend.services.mysql_import_period.refresh_carriere_segment_dates',lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic refresh failure')))
    monkeypatch.setattr('sme_gend.services.audit_log.append_audit_event',lambda **kw:pytest.fail('Rolled back recovery must not audit a success'))
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.invalidate_editing_state_cache',lambda:None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE sme_periode(id BIGINT PRIMARY KEY,annee_mois CHAR(7),codsme VARCHAR(32),statut VARCHAR(64)) ENGINE=InnoDB")
            cur.execute("CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)) ENGINE=InnoDB")
            cur.executemany("INSERT INTO sme_periode VALUES(%s,%s,%s,%s)",[(1,'2026-09','SME-202609','Ouvert'),(2,'2026-10','SME-202610','Ouvert')])
    with pytest.raises(RuntimeError,match='synthetic refresh failure'):
        guard.recover_multiple_open_months(settings,'2026-09',expected_open_months=('2026-09','2026-10'),actor='ADMIN.TEST')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois")
            assert cur.fetchall()==(('2026-09','Ouvert'),('2026-10','Ouvert'))
            cur.execute("SELECT COUNT(*) FROM app_config")
            assert cur.fetchone()[0]==0
