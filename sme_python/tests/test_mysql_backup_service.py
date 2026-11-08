"""Tests sauvegarde MySQL."""

from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
import pytest

from sme_gend.services import mysql_backup_service as mbs


@pytest.fixture(autouse=True)
def isolated_schema_mutex(monkeypatch):
    monkeypatch.setattr(mbs,'schema_metadata_lock',lambda settings:nullcontext())
    monkeypatch.setattr("sme_gend.services.document_month_cycle.get_cycle_mode", lambda settings: "legacy")
    monkeypatch.setattr("sme_gend.services.document_month_cycle._has_dated_movements", lambda settings: False)


def test_find_mysqldump_respects_env(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "mysqldump.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("SME_MYSQLDUMP_PATH", str(fake))
    assert mbs._find_mysqldump() == str(fake)



def test_mysql_password_is_not_a_process_argument(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from sme_gend.config.settings import Settings
    secret="SYNTHETIC-ONLY-DO-NOT-PRINT"
    settings=Settings(database_path=tmp_path/"unused.mdb",mysql_password=secret)
    monkeypatch.setattr(mbs,"_find_mysqldump",lambda:"mysqldump.exe")
    monkeypatch.setattr(mbs,"_check_native_snapshot",lambda settings:None)
    @contextmanager
    def synthetic_options(config):
        # This test checks argv; private-file ACLs have their own tests.
        assert config.mysql_password == secret
        yield tmp_path / 'synthetic-options.cnf', {}
    monkeypatch.setattr(mbs, 'private_mysql_options', synthetic_options)
    def run(command,**kwargs):
        assert not any(secret in part for part in command), "Connection password exposed in process arguments"
        assert any(part.startswith('--defaults-file=') for part in command)
        out=kwargs["stdout"];out.write(b"-- synthetic dump\n" if "b" in out.mode else "-- synthetic dump\n")
        return SimpleNamespace(returncode=0,stderr="")
    monkeypatch.setattr(mbs.subprocess,"run",run)
    path,warnings=mbs.backup_mysql_database(settings,tmp_path,"2026-09")
    assert path and path.is_file()


def test_failed_backup_does_not_delete_an_existing_archive(monkeypatch,tmp_path):
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from sme_gend.config.settings import Settings
    fixed=datetime(2026,10,30,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr(mbs,"datetime",SimpleNamespace(now=lambda tz:fixed))
    monkeypatch.setattr(mbs,"_find_mysqldump",lambda:None)
    existing=tmp_path/"mysql_smegend_2026-09_20261030T120000Z.sql"
    existing.write_bytes(b"PREVIOUS VALID BACKUP")
    def fail(settings,path):
        path.write_bytes(b"PARTIAL")
        raise RuntimeError("synthetic interrupted dump")
    monkeypatch.setattr(mbs,"_python_sql_dump",fail)
    result,warnings=mbs.backup_mysql_database(Settings(database_path=tmp_path/"unused"),tmp_path,"2026-09")
    assert result is None and warnings
    assert existing.read_bytes()==b"PREVIOUS VALID BACKUP"
    assert list(tmp_path.glob("*.sql"))==[existing]


def test_month_closure_refuses_to_mutate_without_a_valid_backup(monkeypatch,tmp_path):
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:object())
    import pytest
    from unittest.mock import MagicMock
    from sme_gend.config.settings import Settings
    from sme_gend.services import sme_month_closure as closure
    monkeypatch.setattr(closure,"sme_archives_dir",lambda:tmp_path)
    monkeypatch.setattr(closure,"_snapshot_layout_from_central",lambda *a:None)
    monkeypatch.setattr(mbs,"backup_mysql_database",lambda *a:(None,["synthetic backup unavailable"]))
    mutation=MagicMock(side_effect=AssertionError("closure reached a write before valid backup"))
    monkeypatch.setattr(closure,"_apply_tabsme_closure",mutation)
    with pytest.raises(RuntimeError,match="sauvegarde"):
        closure.run_piste_a_month_closure(Settings(database_path=tmp_path/"missing.mdb"),"2026-09","2026-10")
    mutation.assert_not_called()



@__import__("pytest").mark.parametrize("outcome",["failure","empty","timeout"])
def test_native_failure_cleans_private_options_and_uses_only_complete_fallback(monkeypatch,tmp_path,outcome):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from sme_gend.config.settings import Settings
    monkeypatch.setattr(mbs,"_find_mysqldump",lambda:"mysqldump.exe")
    monkeypatch.setattr(mbs,"_check_native_snapshot",lambda settings:None)
    monkeypatch.setenv("MYSQL_PWD","UNRELATED SYNTHETIC VALUE")
    seen=[]
    def run(command,**kwargs):
        assert command[1].startswith("--defaults-file=")
        private=Path(command[1].split("=",1)[1]);seen.append(private)
        assert private.is_file()
        assert "MYSQL_PWD" not in kwargs["env"]
        assert kwargs["timeout"]==300
        assert command[-2]=="--"
        if outcome=="timeout":raise mbs.subprocess.TimeoutExpired(command,300)
        if outcome=="failure":kwargs["stdout"].write(b"PARTIAL")
        return SimpleNamespace(returncode=0 if outcome=="empty" else 1,stderr=b"SYNTHETIC SECRET ERROR")
    monkeypatch.setattr(mbs.subprocess,"run",run)
    def fallback(settings,path):path.write_bytes(b"COMPLETE FALLBACK")
    monkeypatch.setattr(mbs,"_python_sql_dump",fallback)
    result,warnings=mbs.backup_mysql_database(Settings(database_path=tmp_path/"unused"),tmp_path,"2026-09")
    assert result.read_bytes()==b"COMPLETE FALLBACK" and warnings
    assert "SYNTHETIC SECRET" not in " ".join(warnings)
    assert all(not path.exists() and not path.parent.exists() for path in seen)
    assert not list(tmp_path.glob(".sme-*"))


def test_private_directory_failure_occurs_before_a_password_is_written(monkeypatch,tmp_path):
    import pytest
    from sme_gend.services import mysql_private_options as private
    from sme_gend.config.settings import Settings
    inspected=[]
    def deny(path):
        inspected.append(path)
        assert list(path.iterdir())==[]
        raise OSError("synthetic ACL failure")
    monkeypatch.setattr(private,"_restrict_folder",deny)
    with pytest.raises(OSError):
        with private.private_mysql_options(Settings(database_path=tmp_path/"unused",mysql_password="SYNTHETIC")):
            raise AssertionError("must not run")
    assert all(not path.exists() for path in inspected)


def test_backup_literal_dialect_preserves_utf8_binary_and_rejects_arbitrary_settings():
    import pytest
    from sme_gend.services.backup_sql import validate_restore_statement
    validate_restore_statement("SET SESSION SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';")
    validate_restore_statement("INSERT INTO `test` VALUES (_utf8mb4 0x636166c3a9,0x00ff,'');")
    with pytest.raises(ValueError):validate_restore_statement("SET GLOBAL SQL_MODE='';")
    with pytest.raises(ValueError):validate_restore_statement("INSERT INTO `test` VALUES (_utf8mb4 0x12 + SLEEP(10));")



@__import__("pytest").mark.parametrize("kind",["missing","empty"])
def test_closure_rejects_missing_or_empty_backup_before_mutation(monkeypatch,tmp_path,kind):
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:object())
    import pytest
    from unittest.mock import MagicMock
    from sme_gend.config.settings import Settings
    from sme_gend.services import sme_month_closure as closure
    artifact=tmp_path/"closure.sql"
    if kind=="empty":artifact.touch()
    monkeypatch.setattr(closure,"sme_archives_dir",lambda:tmp_path)
    monkeypatch.setattr(closure,"_snapshot_layout_from_central",lambda *a:None)
    monkeypatch.setattr(mbs,"backup_mysql_database",lambda *a:(artifact,[]))
    mutation=MagicMock()
    monkeypatch.setattr(closure,"_apply_tabsme_closure",mutation)
    with pytest.raises(RuntimeError,match="sauvegarde"):
        closure.run_piste_a_month_closure(Settings(database_path=tmp_path/"unused.mdb"),"2026-09","2026-10")
    mutation.assert_not_called()


def test_successive_backups_at_the_same_time_are_kept(monkeypatch,tmp_path):
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from sme_gend.config.settings import Settings
    fixed=datetime(2026,10,30,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr(mbs,"datetime",SimpleNamespace(now=lambda tz:fixed))
    monkeypatch.setattr(mbs,"_find_mysqldump",lambda:None)
    monkeypatch.setattr(mbs,"_python_sql_dump",lambda settings,path:path.write_bytes(b"COMPLETE"))
    settings=Settings(database_path=tmp_path/"unused.mdb")
    first,_=mbs.backup_mysql_database(settings,tmp_path,"2026-09")
    second,_=mbs.backup_mysql_database(settings,tmp_path,"2026-09")
    assert first!=second and first.is_file() and second.is_file()
