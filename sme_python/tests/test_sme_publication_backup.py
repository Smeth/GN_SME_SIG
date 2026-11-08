from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from test_sme_export_revision import save
from sme_gend.config.settings import Settings
from sme_gend.services import backup_restore_service as backup
from sme_gend.services import sme_publication_backup as publications


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    source = tmp_path / "source_versions"
    revision = save(source / ("a" * 20))
    monkeypatch.setattr(publications, "storage_root", lambda: source)
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "_fetch_audit_journal_by_module", lambda settings: {})
    def snapshot(*args, snapshot_data=None, **kwargs):
        snapshot_data.update(captured=True,app_config_rows=[],audit_by_module={},
                             consistency={'sql_and_shared_config':'same_transaction'})
        return "-- synthetic SQL", {}
    monkeypatch.setattr(backup, "_export_mysql_sql", snapshot)
    monkeypatch.setattr(backup, "AppConfigRepository", lambda settings: SimpleNamespace(export_all_config_rows=lambda: []))
    monkeypatch.setattr(backup, "default_backup_directory", lambda: tmp_path / "sessions")
    settings = Settings(database_path=tmp_path / "unused.mdb")
    archive = tmp_path / "full.zip"
    manifest = backup.create_full_backup(settings, archive)
    restored = tmp_path / "restored_versions"
    monkeypatch.setattr(publications, "storage_root", lambda: restored)
    return settings, archive, manifest, revision, restored


def restore_scope():
    return backup.RestoreScope(restore_mysql=False, restore_app_config=False, restore_user_config=False,
        restore_media_photos=False, restore_media_grades=False, preserve_local_accounts=False)


def test_full_backup_and_application_data_restore_retain_exact_revisions(scenario):
    settings, archive, manifest, revision, restored = scenario
    assert manifest.backup_kind == "full" and manifest.has_publications()
    assert len(manifest.publication_files) == 4
    backup.restore_backup(settings, archive, scope=restore_scope())
    target = restored / ("a" * 20) / "2026-11" / revision.revision_id
    assert {file.name: file.read_bytes() for file in target.iterdir()} == {
        file.name: file.read_bytes() for file in revision.folder.iterdir()}
    # Repeating restoration preserves existing byte-identical revisions.
    backup.restore_backup(settings, archive, scope=restore_scope())
    meta = revision.journal_metadata() | {"revision_folder": "absent-on-receiving-machine"}
    assert publications.locate_revision(meta, "2026-11") == target


def test_backup_retains_restored_source_identity_even_if_receiving_database_differs(scenario, tmp_path, monkeypatch):
    settings, archive, manifest, revision, restored = scenario
    backup.restore_backup(settings, archive, scope=restore_scope())
    following = tmp_path / "following.zip"
    new = backup.create_full_backup(replace(settings, mysql_database="receiving_server"), following)
    assert new.publication_files == manifest.publication_files


def test_restore_never_replaces_a_different_existing_revision(scenario):
    settings, archive, manifest, revision, restored = scenario
    target = restored / ("a" * 20) / "2026-11" / revision.revision_id / "classeur.xlsx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"different existing publication")
    with pytest.raises(ValueError, match="Conflit de version"):
        backup.restore_backup(settings, archive, scope=restore_scope())
    assert target.read_bytes() == b"different existing publication"
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("tamper", ["payload", "missing", "path"])
def test_corrupt_versions_are_rejected_before_any_target_publication(scenario, tmp_path, tamper):
    settings, archive, manifest, revision, restored = scenario
    bad = tmp_path / "bad.zip"
    with ZipFile(archive) as source, ZipFile(bad, "w") as destination:
        for name in source.namelist():
            if name == manifest.publication_files[0]:
                if tamper == "missing":
                    continue
                if tamper == "path":
                    destination.writestr("sme_publications/../escape.xlsx", source.read(name))
                    continue
                destination.writestr(name, b"altered")
            else:
                destination.writestr(name, source.read(name))
    with pytest.raises(ValueError):
        backup.restore_backup(settings, bad, scope=restore_scope())
    assert not restored.exists()


def test_publication_file_failure_rolls_back_the_whole_file_publication(scenario, monkeypatch):
    from sme_gend.services.restore_files import RestoreFiles
    settings, archive, manifest, revision, restored = scenario
    original = RestoreFiles.publish
    def interrupted(self):
        original(self)
        raise OSError("synthetic interruption after publication")
    monkeypatch.setattr(RestoreFiles, "publish", interrupted)
    with pytest.raises(OSError, match="synthetic interruption"):
        backup.restore_backup(settings, archive, scope=restore_scope())
    assert not any(path.is_file() for path in restored.rglob("*"))


def test_restoring_without_publications_leaves_revisions_unmodified(scenario):
    settings, archive, manifest, revision, restored = scenario
    backup.restore_backup(settings, archive, scope=replace(restore_scope(), restore_publications=False))
    assert not restored.exists()


def test_legacy_v4_backup_without_revision_inventory_is_still_readable(tmp_path):
    archive = tmp_path / "legacy.zip"
    manifest = backup.BackupManifest(4, "2026-11-01", "test", "localhost", {}, [], app_data_files=["example.json"])
    with ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest.to_dict()))
        output.writestr("app_data/example.json", "{}")
    assert backup.inspect_backup(archive).has_app_data()


def test_concurrent_new_file_cannot_be_overwritten_at_the_final_publication_step(tmp_path, monkeypatch):
    from io import BytesIO
    from sme_gend.services.restore_files import RestoreFiles
    target = tmp_path / "revision" / "classeur.xlsx"
    files = RestoreFiles(tmp_path / "private")
    files.add(target, BytesIO(b"incoming"), expected_before=None)
    original = Path.rename
    def concurrent_create(path, destination):
        if Path(destination) == target:
            target.write_bytes(b"concurrent version")
        return original(path, destination)
    monkeypatch.setattr(Path, "rename", concurrent_create)
    with pytest.raises(FileExistsError):
        files.publish()
    assert target.read_bytes() == b"concurrent version"


@pytest.mark.parametrize("current", [{}, {"sme_archives_path": "receiving/archives"}])
def test_archive_routing_stays_local_when_restoring_version_files(tmp_path, current):
    target = tmp_path / "paths.json"
    target.write_text(json.dumps(current), encoding="utf-8")
    result = publications.receiving_archive_paths({"sme_archives_path": "source/archives", "mysql_host": "source"}, target)
    assert result.get("sme_archives_path") == current.get("sme_archives_path")
    assert result["mysql_host"] == "source"
