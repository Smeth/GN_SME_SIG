"""Vidage complet : sauvegarde, portée mensuelle et protection des archives."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest

from sme_gend.services.empty_sme_month import empty_sme_month, month_archive_files


def test_archive_selection_is_limited_to_month_and_known_types(tmp_path):
    selected = ["sme_bundle_2026-09_20260930T120000Z.mdb", "liste_present_layout_202609_20260930T120000Z.json"]
    untouched = ["sme_bundle_2026-08_20260831T120000Z.mdb", "sme_bundle_2026-09_notes.txt", "source.mdb"]
    for name in selected + untouched:
        (tmp_path / name).write_bytes(b"archive")
    assert {p.name for p in month_archive_files(tmp_path, "2026-09")} == set(selected)


def test_unauthorized_month_empty_does_not_start(monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_admin_role", lambda: False)
    with pytest.raises(PermissionError):
        empty_sme_month(object(), "2026-09")


@pytest.mark.parametrize("backup_ok,db_ok", [(True, True), (False, True), (True, False)])
def test_month_empty_backups_before_database_and_preserves_other_files(tmp_path, monkeypatch, backup_ok, db_ok):
    archives = tmp_path / "archives"
    archives.mkdir()
    target = archives / "sme_bundle_2026-09_20260930T120000Z.mdb"
    other = archives / "sme_bundle_2026-08_20260831T120000Z.mdb"
    target.write_bytes(b"septembre")
    other.write_bytes(b"aout")
    backup = tmp_path / "backup.zip"
    backup.write_bytes(b"backup")
    calls = []
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_admin_role", lambda: True)
    monkeypatch.setattr("sme_gend.repositories.mysql_common.ensure_mysql_schema", lambda s: None)
    monkeypatch.setattr("sme_gend.services.app_config_store.ensure_central_config_ready", lambda s: None)
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.invalidate_liste_cache", lambda s: None)
    monkeypatch.setattr("sme_gend.services.sme_month_closure.sme_archives_dir", lambda: archives)
    monkeypatch.setattr("sme_gend.services.audit_log.append_audit_event", lambda **kw: None)
    monkeypatch.setattr("sme_gend.services.data_sync.publish_data_change", lambda *a, **kw: None)
    def make_backup(*a, **kw):
        calls.append("backup")
        return backup if backup_ok else None
    monkeypatch.setattr("sme_gend.services.backup_restore_service.ensure_recent_backup", make_backup)
    @contextmanager
    def transaction(s):
        calls.append("begin")
        yield
        calls.append("commit")
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.mysql_import_transaction", transaction)
    monkeypatch.setattr("sme_gend.services.mysql_import_period.ensure_sme_periode", lambda c, m: 51)
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.rowcount = 1
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    factory = MagicMock()
    factory.connect.return_value = connection
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection", lambda s: factory)
    def purge(settings, month, *, imported_only):
        assert imported_only is False
        assert month == "2026-09"
        assert target.exists()
        assert (tmp_path / "backup_archives_2026-09.zip").is_file()
        calls.append("purge")
        if not db_ok:
            raise RuntimeError("Erreur SQL")
        return {"sme_mois": month}
    monkeypatch.setattr("sme_gend.services.empty_sme_month.purge_sme_periode_for_reimport", purge)
    if backup_ok and db_ok:
        result = empty_sme_month(object(), "2026-09")
        assert calls == ["backup", "begin", "purge", "commit"]
        assert not target.exists() and other.exists()
        assert result["archives_removed"] == 1
        with ZipFile(result["archive_backup"]) as zf:
            assert zf.read(target.name) == b"septembre"
        for call in cursor.execute.call_args_list:
            assert "WHERE" in call.args[0]
            assert call.args[1] in ((51,), ("2026-09",), ("202609",))
    else:
        with pytest.raises(RuntimeError):
            empty_sme_month(object(), "2026-09")
        assert target.exists() and other.exists()
        assert "commit" not in calls
        if not backup_ok:
            assert calls == ["backup"]
