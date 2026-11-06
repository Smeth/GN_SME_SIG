"""Publication d'un mois seulement, contre une instance explicitement jetable."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from test_restore_mysql import isolated_mysql
from test_month_transfer import source, live
from sme_gend.services import month_transfer as service
from sme_gend.repositories.mysql_common import _split_sql_statements


@pytest.fixture
def seeded(isolated_mysql,live,monkeypatch,tmp_path):
    settings,connect,params = isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for name in service._ordered_tables(live):
                table = live[name]
                cur.execute(table["ddl"])
                columns = table["columns"]
                for row in table["rows"]:
                    cur.execute(f"INSERT INTO `{name}` ("+",".join(service.quote(c) for c in columns)+") VALUES ("+",".join(["%s"]*len(columns))+")",tuple(row[c] for c in columns))
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            schema = (Path(__file__).resolve().parents[2] / "data/schema/mysql_app_config.sql").read_text(encoding="utf-8-sig")
            for statement in _split_sql_statements(schema): cur.execute(statement)
    monkeypatch.setattr(service,"_authorize",lambda **kw:None)
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_write_actor",lambda *a:"recette_mensuelle")
    backup = tmp_path/"prior.sql"
    from sme_gend.services.mysql_backup_service import _python_sql_dump
    _python_sql_dump(settings,backup)
    monkeypatch.setattr("sme_gend.services.backup_restore_service.ensure_recent_backup",lambda *a,**kw:backup)
    return settings,connect,params


def test_month_restore_preserves_other_months_statuses_and_globals_with_real_foreign_keys(seeded,source):
    settings,connect,params = seeded
    before = service.read_database(settings)
    plan = service.preview_month(settings,source,"2026-01")
    assert plan.conflicts == ()
    service.apply_month(settings,plan)
    after = service.read_database(settings)
    for name in ("agent","ref_grade","sme_periode"):
        assert before[name]["rows"] == after[name]["rows"]
    assert service.extract_month(before,"2026-09")["carriere_situation"]["rows"] == service.extract_month(after,"2026-09")["carriere_situation"]["rows"]
    restored = service.extract_month(after,"2026-01")
    assert restored["carriere_situation"]["rows"][0]["obs"] == "JAN ORIGINAL"
    assert restored["carriere_situation"]["rows"][0]["sme_periode_id"] == 10
    assert restored["carriere_situation"]["rows"][0]["est_actuelle"] == 0
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_restore'")
            assert cur.fetchone()[0] == 1


def test_real_sql_global_extraction_and_reimport_one_month(seeded,tmp_path):
    settings,connect,params = seeded
    from sme_gend.services.mysql_backup_service import _python_sql_dump
    sql = tmp_path/"global.sql"
    _python_sql_dump(settings,sql)
    source = service.load_source(sql)
    assert service.months_in(source) == ["2026-01","2026-09"]
    archive = tmp_path/"month.zip"
    service.write_month(archive,source,"2026-01")
    selected = service.load_source(archive)
    assert service.months_in(selected) == ["2026-01"]
    plan = service.preview_month(settings,selected,"2026-01")
    assert not plan.conflicts
    service.apply_month(settings,plan)
    assert service.months_in(service.read_database(settings)) == ["2026-01","2026-09"]


def test_sql_failure_inside_real_month_restore_rolls_back_and_has_no_success_audit(seeded,source,monkeypatch):
    settings,connect,params = seeded
    def fail_audit(*a,**kw):
        from sme_gend.repositories.mysql_connection import MySQLConnection
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cur:
                cur.execute("SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic refusal'")
    monkeypatch.setattr("sme_gend.services.business_audit.record_business_action",fail_audit)
    before = service.read_database(settings)
    plan = service.preview_month(settings,source,"2026-01")
    with pytest.raises(Exception,match="synthetic refusal"): service.apply_month(settings,plan)
    after = service.read_database(settings)
    for name in before: assert before[name]["rows"] == after[name]["rows"]
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_restore'")
            assert cur.fetchone()[0] == 0


def test_conflict_from_another_connection_after_preview_cannot_overwrite(seeded,source):
    settings,connect,params = seeded
    plan = service.preview_month(settings,source,"2026-01")
    with connect(**params) as connection:
        with connection.cursor() as cur: cur.execute("UPDATE carriere_situation SET obs='POSTE CONCURRENT' WHERE sme_periode_id=10")
    with pytest.raises(Exception,match="changé"): service.apply_month(settings,plan)
    after = service.extract_month(service.read_database(settings),"2026-01")
    assert after["carriere_situation"]["rows"][0]["obs"] == "POSTE CONCURRENT"


def test_native_mysqldump_global_is_read_without_execution_and_restores_one_month(seeded,tmp_path):
    import subprocess
    settings,connect,params = seeded
    executable = Path("C:/Program Files/MySQL/MySQL Server 8.0/bin/mysqldump.exe")
    if not executable.is_file(): pytest.skip("Client natif absent")
    path = tmp_path/"native-global.sql"
    command = [str(executable),"--no-defaults","--host=127.0.0.1",f"--port={settings.mysql_port}","--user=root",
        "--single-transaction","--hex-blob","--no-tablespaces","--set-gtid-purged=OFF","--column-statistics=0",settings.mysql_database]
    with path.open("wb") as stream:
        result = subprocess.run(command,stdout=stream,stderr=subprocess.PIPE,timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    assert result.returncode == 0
    source = service.load_source(path)
    assert service.months_in(source) == ["2026-01","2026-09"]
    plan = service.preview_month(settings,source,"2026-01")
    assert not plan.conflicts
    service.apply_month(settings,plan)
    assert service.months_in(service.read_database(settings)) == ["2026-01","2026-09"]


def test_import_absent_month_creates_closed_period_without_changing_active_month(seeded,source):
    settings,connect,params = seeded
    with connect(**params) as connection:
        with connection.cursor() as cur:
            for name in ("agent_diplome","carriere_situation","agent_mission"):
                column = "import_lot_id" if name == "agent_diplome" else "sme_periode_id"
                cur.execute(f"DELETE FROM `{name}` WHERE `{column}`=%s",(20 if column == "import_lot_id" else 10,))
            cur.execute("DELETE FROM import_lot WHERE sme_periode_id=10")
            cur.execute("DELETE FROM sme_periode WHERE id=10")
    plan = service.preview_month(settings,source,"2026-01")
    assert not plan.conflicts
    service.apply_month(settings,plan)
    after = service.read_database(settings)
    assert {r["annee_mois"]:r["statut"] for r in after["sme_periode"]["rows"]} == {"2026-01":"Fermé","2026-09":"Ouvert"}
    assert service.extract_month(after,"2026-01")["carriere_situation"]["rows"][0]["est_actuelle"] == 0


def test_restoring_active_month_keeps_active_projection_and_other_history(seeded,source):
    settings,connect,params = seeded
    before = service.extract_month(service.read_database(settings),"2026-01")
    plan = service.preview_month(settings,source,"2026-09")
    assert not plan.conflicts
    service.apply_month(settings,plan)
    after = service.read_database(settings)
    assert service.extract_month(after,"2026-01")["carriere_situation"]["rows"] == before["carriere_situation"]["rows"]
    assert service.extract_month(after,"2026-09")["carriere_situation"]["rows"][0]["est_actuelle"] == 1


def test_month_transfer_on_full_application_schema_including_aliases_and_history(isolated_mysql,monkeypatch,tmp_path):
    from test_restore_mysql import _lot7_dossier
    settings,connect,params,dossier,row = _lot7_dossier(isolated_mysql,monkeypatch)
    monkeypatch.setattr(service,"_authorize",lambda **kw:None)
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_write_actor",lambda *a:"recette_mensuelle")
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-01','2026-01-31','Fermé')")
            cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,est_actuelle,obs) VALUES (1,2,0,'JAN ARCHIVE')")
    before = service.read_database(settings)
    archive = tmp_path/"month.zip"
    service.write_month(archive,before,"2026-01")
    with connect(**params) as connection:
        with connection.cursor() as cur: cur.execute("UPDATE carriere_situation SET obs='JAN MODIFIE' WHERE sme_periode_id=2")
    from sme_gend.services.mysql_backup_service import _python_sql_dump
    backup = tmp_path/"prior.sql"
    _python_sql_dump(settings,backup)
    monkeypatch.setattr("sme_gend.services.backup_restore_service.ensure_recent_backup",lambda *a,**kw:backup)
    plan = service.preview_month(settings,service.load_source(archive),"2026-01")
    assert not plan.conflicts
    service.apply_month(settings,plan)
    after = service.read_database(settings)
    assert service.extract_month(after,"2026-01")["carriere_situation"]["rows"][0]["obs"] == "JAN ARCHIVE"
    assert service.extract_month(after,"2026-09")["carriere_situation"]["rows"] == service.extract_month(before,"2026-09")["carriere_situation"]["rows"]
