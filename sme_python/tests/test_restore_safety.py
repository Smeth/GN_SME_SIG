"""Archives synthétiques : aucune connexion ni donnée utilisateur réelle."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import backup_restore_service as backup
from sme_gend.services import personnel_media as media


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    active = True
    base = os.path.normcase(os.path.abspath(tmp_path))
    def guard(event, args):
        if not active:
            return
        paths = []
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            if writing:
                paths.append(path)
        elif event in ("os.mkdir", "os.remove", "os.rmdir"):
            paths.append(args[0])
        elif event in ("socket.connect", "socket.sendto"):
            raise AssertionError("External I/O forbidden in synthetic restore test")
        for path in paths:
            if isinstance(path, int):
                continue
            candidate = os.path.normcase(os.path.abspath(path))
            if not candidate.startswith(base + os.sep) and candidate != base:
                raise AssertionError("Write outside synthetic test directory forbidden")
    sys.addaudithook(guard)
    monkeypatch.setattr(backup, "user_application_data_dir", lambda: tmp_path / "user")
    monkeypatch.setattr(backup, "user_paths_config_file", lambda: tmp_path / "user/paths.json")
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    monkeypatch.setattr(backup, "resolved_personnel_media_root", lambda: tmp_path / "media")
    monkeypatch.setattr(media, "personnel_media_root", lambda: tmp_path / "media")
    monkeypatch.setattr(backup, "_snapshot_local_auth", lambda settings: None)
    monkeypatch.setattr(backup, "default_backup_directory", lambda: tmp_path / "backups")
    # Une connexion accidentelle doit faire échouer le test avant toute opération.
    monkeypatch.setattr("pymysql.connect", lambda **kwargs: pytest.fail("Unexpected MySQL connection"))
    yield Settings(database_path=tmp_path / "unused.mdb")
    active = False


def archive(tmp_path, entries):
    path = tmp_path / "synthetic.zip"
    manifest = backup.BackupManifest(4, "2026-10-29", "synthetic", "invalid.test", {}, [])
    with ZipFile(path, "w") as zf:
        zf.writestr(backup.MANIFEST_NAME, json.dumps(manifest.to_dict()))
        for name, value in entries.items():
            zf.writestr(name, value)
    return path


def media_scope():
    return backup.RestoreScope(restore_mysql=False, restore_app_config=False,
                               restore_app_data=False, restore_user_config=False,
                               preserve_local_accounts=False)


@pytest.mark.parametrize("bad", ["../escape.png", "C:/outside.png", "//server/share/x.png",
                                  r"photos\..\escape.png", "photos/CON.png", "photos/a:b.png"])
def test_restore_rejects_unsafe_entries_before_writing(tmp_path, isolated, bad):
    path = archive(tmp_path, {"personnel_media/photos/valid.png": b"valid",
                              "personnel_media/" + bad: b"bad"})
    with pytest.raises(ValueError):
        backup.restore_backup(isolated, path, scope=media_scope())
    assert not (tmp_path / "media/photos/valid.png").exists()


def test_restore_rejects_absolute_path_inside_archive(tmp_path, isolated):
    escaped = tmp_path / "outside.png"
    path = archive(tmp_path, {"personnel_media/" + escaped.as_posix(): b"bad"})
    with pytest.raises(ValueError):
        backup.restore_backup(isolated, path, scope=media_scope())
    assert not escaped.exists()


def test_valid_media_restore_retains_layout(tmp_path, isolated):
    path = archive(tmp_path, {"personnel_media/photos/42.png": b"photo",
                              "personnel_media/grades/lieutenant.png": b"grade"})
    backup.restore_backup(isolated, path, scope=media_scope())
    assert (tmp_path / "media/photos/42.png").read_bytes() == b"photo"
    assert (tmp_path / "media/grades/lieutenant.png").read_bytes() == b"grade"


@pytest.mark.parametrize("key", ["../outside", r"..\outside", "C:/outside", "CON", "a:b"])
def test_photo_key_cannot_escape_media(tmp_path, isolated, key):
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    with pytest.raises(ValueError):
        media.save_person_photo(key, source)
    assert not (tmp_path / "media/outside.png").exists()


def test_split_sql_round_trip_multiline_and_comments():
    value = "première ligne;\nseconde 'ligne' \\ fin"
    statement = "INSERT INTO `agent` (`noms`) VALUES (" + backup._sql_escape(value) + ");"
    assert backup._split_sql_statements(statement) == [statement]
    assert len(backup._split_sql_statements("-- comment;\n" + statement + "\n# tail;\nSELECT 1;")) == 2


@pytest.mark.parametrize("text", ["SELECT 'unterminated", "SELECT 1; /* unfinished"])
def test_malformed_sql_is_rejected_before_execution(text):
    with pytest.raises(ValueError):
        backup._split_sql_statements(text)


def test_table_filter_ignores_names_inside_values():
    assert not backup._should_run_sql_statement(
        "INSERT INTO `other` VALUES ('agent');", allowed_tables={"agent"}, preserve_audit_journal=False)
    assert backup._should_run_sql_statement(
        "INSERT INTO `agent` VALUES ('audit_journal');", allowed_tables={"agent"}, preserve_audit_journal=True)


@pytest.mark.parametrize("statement", ["DROP TABLE agent, other;", "INSERT INTO agent SELECT * FROM other;",
                                       "SET GLOBAL sql_mode='';", "INSERT INTO agent VALUES ((SELECT 1));"])
def test_unexpected_sql_is_rejected_even_in_full_restore(statement):
    with pytest.raises(ValueError):
        backup._should_run_sql_statement(statement, allowed_tables=None, preserve_audit_journal=False)


def test_configuration_restore_failure_is_not_success(tmp_path, isolated, monkeypatch):
    _isolated_config_backend(monkeypatch)
    path = archive(tmp_path, {backup.APP_CONFIG_DUMP_NAME: '[{"config_key":"test","payload":{}}]'})
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(backup, "AppConfigRepository", lambda settings: SimpleNamespace(import_config_rows=fail, set_json=lambda *a, **kw: None))
    scope = media_scope()
    scope = backup.RestoreScope(**(scope.to_dict() | {"restore_app_config": True}))
    with pytest.raises(RuntimeError):
        backup.restore_backup(isolated, path, scope=scope)


@pytest.mark.parametrize("rows", [[{"config_key": "test"}],
    [{"config_key": "", "payload": {}}], [{"config_key": 42, "payload": {}}],
    [{"config_key": "test", "payload": {}}, {"config_key": "test", "payload": {}}]])
def test_invalid_config_rejected_before_any_restore_write(tmp_path, isolated, monkeypatch, rows):
    path = archive(tmp_path, {backup.APP_CONFIG_DUMP_NAME: json.dumps(rows),
                              "personnel_media/photos/42.png": b"synthetic photo"})
    calls = []
    monkeypatch.setattr(backup, "AppConfigRepository", lambda settings: SimpleNamespace(
        import_config_rows=lambda *args: calls.append(args) or 0))
    scope = backup.RestoreScope(**(media_scope().to_dict() | {"restore_app_config": True}))
    with pytest.raises(ValueError):
        backup.restore_backup(isolated, path, scope=scope)
    assert calls == []
    assert not (tmp_path / "media/photos/42.png").exists()


def test_incomplete_config_restore_never_reports_success(tmp_path, isolated, monkeypatch):
    _isolated_config_backend(monkeypatch)
    path = archive(tmp_path, {backup.APP_CONFIG_DUMP_NAME: '[{"config_key":"test","payload":{}}]'})
    monkeypatch.setattr(backup, "AppConfigRepository", lambda settings: SimpleNamespace(
        import_config_rows=lambda *args: 0, set_json=lambda *a, **kw: None))
    scope = backup.RestoreScope(**(media_scope().to_dict() | {"restore_app_config": True}))
    with pytest.raises(RuntimeError, match="configuration"):
        backup.restore_backup(isolated, path, scope=scope)


def test_preserved_accounts_are_filtered_case_insensitively():
    from sme_gend.services.app_config_store import KEY_AUTH
    rows = [{"config_key": KEY_AUTH.upper(), "payload": {}}, {"config_key": "test", "payload": {}}]
    assert backup._app_config_rows_for_restore(rows, preserve_local_accounts=True) == rows[1:]


def test_sql_error_closes_connection_and_restores_session_state(tmp_path, isolated, monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr('sme_gend.services.database_maintenance.coordinated_write', lambda *a, **kw: nullcontext())
    from unittest.mock import MagicMock
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    def execute(statement, *args):
        if statement.startswith("INSERT"):
            raise RuntimeError("duplicate synthetic value")
    cursor.execute.side_effect = execute
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr("pymysql.connect", lambda **kwargs: conn)
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *args, **kwargs: tmp_path / "safety.zip")
    with pytest.raises(RuntimeError, match="interrompue"):
        backup._execute_restore_sql(isolated, ["SET FOREIGN_KEY_CHECKS = 0;", "INSERT INTO agent VALUES (1);"])
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()
    cursor.execute.assert_any_call("SET SESSION FOREIGN_KEY_CHECKS = %s", (1,))
    conn.commit.assert_not_called()


def test_restore_without_safety_backup_never_connects(isolated, monkeypatch):
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="sauvegarde préalable"):
        backup._execute_restore_sql(isolated, ["DROP TABLE agent;"])


def test_photo_copy_failure_preserves_existing_file(tmp_path, isolated, monkeypatch):
    from sme_gend.services import safe_paths
    existing = tmp_path / "media/photos/42.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"original")
    source = tmp_path / "source.png"
    source.write_bytes(b"new")
    def failed_copy(src, dest):
        Path(dest).write_bytes(b"partial")
        raise OSError("synthetic interrupted copy")
    monkeypatch.setattr(safe_paths.shutil, "copy2", failed_copy)
    with pytest.raises(OSError):
        media.save_person_photo("42", source)
    assert existing.read_bytes() == b"original"
    assert not list(existing.parent.glob("*.part"))



def test_full_sql_restore_requires_explicit_maintenance_before_connecting(tmp_path,isolated):
    path=archive(tmp_path,{backup.SQL_DUMP_NAME:"DROP TABLE IF EXISTS agent; CREATE TABLE agent (id INT) ENGINE=InnoDB;"})
    # Match the selected inventory, so maintenance is the reason for refusal.
    with ZipFile(path,'r') as zf:
        sql=zf.read(backup.SQL_DUMP_NAME)
    manifest=backup.BackupManifest(4,'2026-10-30','synthetic','invalid.test',{'agent':0},[])
    with ZipFile(path,'w') as zf:
        zf.writestr(backup.MANIFEST_NAME,json.dumps(manifest.to_dict()))
        zf.writestr(backup.SQL_DUMP_NAME,sql)
    scope=backup.RestoreScope(preserve_local_accounts=False,restore_user_config=False,
        restore_app_data=False,restore_app_config=False,restore_media_photos=False,restore_media_grades=False)
    with pytest.raises(RuntimeError,match='maintenance'):
        backup.restore_backup(isolated,path,scope=scope)



def _isolated_config_backend(monkeypatch):
    # These tests cover orchestration failures, not SQL transactions. The native
    # suite independently verifies real commit/rollback with the same scenarios.
    from contextlib import nullcontext
    from sme_gend.services import configuration_restore as restore
    monkeypatch.setattr('sme_gend.services.database_maintenance.centralized_maintenance', lambda *a, **kw: nullcontext(SimpleNamespace(token='1'*32, keep_blocked=lambda:None)))
    gate=SimpleNamespace(check=lambda:None,payload=lambda *a:None)
    monkeypatch.setattr(restore,'_sql_gate',lambda *a:nullcontext(gate))
    monkeypatch.setattr(restore,'_filter_database_keys',lambda gate,rows,**kw:rows)
    monkeypatch.setattr(restore,'_before_rows',lambda *a:[])
    monkeypatch.setattr(restore,'mysql_import_transaction',lambda *a,**kw:nullcontext())
