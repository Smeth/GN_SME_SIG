"""Progress follows completed work and confirmed publication, including failures."""
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import backup_restore_service as backup
from sme_gend.services.operation_progress import operation_progress, progress_reporting


def local_scope():
    return backup.BackupScope(include_mysql=False, include_user_config=True,
        include_app_data=False, include_app_config=False, include_media_photos=False,
        include_media_grades=False, include_publications=False)


def test_backup_progress_hashes_real_bytes_and_finishes_only_after_atomic_publication(tmp_path, monkeypatch):
    config = tmp_path / 'preferences.json'
    config.write_text(json.dumps({'theme': 'synthetic'}), encoding='utf-8')
    monkeypatch.setattr(backup, '_collect_config_paths', lambda: [config])
    destination = tmp_path / 'backup.zip'
    snapshots = []
    exists_when_complete = []
    def recorded(snapshot):
        snapshots.append(snapshot)
        if snapshot.percent == 100:
            exists_when_complete.append(destination.is_file())
    with progress_reporting(recorded):
        manifest = backup.create_backup(Settings(database_path=tmp_path/'unused'), destination, scope=local_scope())
    assert backup.inspect_backup(destination).config_files == manifest.config_files
    assert [step.key for step in snapshots[0].steps] == ['prepare', 'sql', 'files', 'publications', 'verify', 'publish']
    assert snapshots[-1].percent == 100
    assert exists_when_complete and all(exists_when_complete)
    assert all(step.state == 'done' for step in snapshots[-1].steps)
    assert [snapshot.percent for snapshot in snapshots] == sorted(snapshot.percent for snapshot in snapshots)
    assert any(snapshot.current == config.stat().st_size and snapshot.total == config.stat().st_size
               and snapshot.active_key == 'verify' for snapshot in snapshots)


def test_failed_atomic_publication_never_reports_completed_backup(tmp_path, monkeypatch):
    from sme_gend.services import safe_paths
    config = tmp_path / 'preferences.json'; config.write_text('{}', encoding='utf-8')
    destination = tmp_path / 'backup.zip'; destination.write_bytes(b'PREVIOUS')
    monkeypatch.setattr(backup, '_collect_config_paths', lambda: [config])
    real_replace = safe_paths.os.replace
    def denied(source, target):
        if Path(target) == destination:
            raise OSError('synthetic publication denied')
        return real_replace(source, target)
    monkeypatch.setattr(safe_paths.os, 'replace', denied)
    snapshots = []
    with progress_reporting(snapshots.append), pytest.raises(OSError, match='publication denied'):
        backup.create_backup(Settings(database_path=tmp_path/'unused'), destination, scope=local_scope())
    assert destination.read_bytes() == b'PREVIOUS'
    assert snapshots[-1].active_key == 'publish'
    assert snapshots[-1].percent < 100
    assert snapshots[-1].steps[-1].state == 'running'


def test_corrupted_archive_stays_in_verification_without_success(tmp_path):
    destination = tmp_path / 'altered.zip'
    manifest = backup.BackupManifest(4, '2026-11-08', 'synthetic', 'invalid.test', {}, [],
        file_inventory={'user_config/test.json': {'size': 2, 'sha256': '0' * 64}})
    with ZipFile(destination, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest.to_dict()))
        archive.writestr('user_config/test.json', '{}')
    snapshots = []
    with progress_reporting(snapshots.append), pytest.raises(ValueError, match='altérée'):
        backup.inspect_backup(destination)
    assert snapshots
    # Reading every byte is insufficient: the digest comparison must also pass.
    assert snapshots[-1].percent < 100
    assert snapshots[-1].active_key == 'check'
    assert snapshots[-1].steps[-1].state == 'running'


def selected_fixture(tmp_path, monkeypatch, *, fail_commit=False):
    from sme_gend.services import backup_month_selection as selection, month_backup, database_maintenance
    monkeypatch.setattr(selection, '_authorize', lambda: None)
    monkeypatch.setattr(selection, 'mysql_database_key', lambda settings: ('synthetic',))
    monkeypatch.setattr(database_maintenance, 'centralized_maintenance', lambda *a, **k: nullcontext())
    @contextmanager
    def transaction(settings):
        yield
        if fail_commit:
            raise RuntimeError('synthetic lost COMMIT confirmation')
    monkeypatch.setattr(selection, 'mysql_import_transaction', transaction)
    monkeypatch.setattr(month_backup, '_create_month_backup', lambda *a: None)
    monkeypatch.setattr(month_backup, 'inspect_month_backup', lambda *a: {})
    monkeypatch.setattr(backup, 'create_backup_to_default_dir', lambda *a: tmp_path/'safety.zip')
    monkeypatch.setattr(backup, 'inspect_backup', lambda *a: object())
    prepared = SimpleNamespace(owner=('synthetic',), months=('2026-08', '2026-09'),
                               workspace=tmp_path, source_settings=object())
    return selection, month_backup, prepared


@pytest.mark.parametrize('fail_commit', [False, True])
def test_selected_month_progress_reserves_each_month_and_waits_for_shared_commit(tmp_path, monkeypatch, fail_commit):
    selection, month_backup, prepared = selected_fixture(tmp_path, monkeypatch, fail_commit=fail_commit)
    snapshots = []
    def restore(settings, archive, *, confirmed_month, **kwargs):
        if confirmed_month == '2026-09':
            # One of two children has completed, not the restore phase itself.
            assert snapshots[-1].percent < 75
            assert next(step for step in snapshots[-1].steps if step.key == 'restore').state == 'running'
        with operation_progress((('rows', 'Restauration des lignes'),)) as child:
            child.start('rows', total=10)
            child.update('rows', current=10, total=10, detail=confirmed_month)
            child.finish('rows')
        return {'grade_history_reapplied': 0}
    monkeypatch.setattr(month_backup, 'restore_month_backup', restore)
    with progress_reporting(snapshots.append):
        if fail_commit:
            with pytest.raises(RuntimeError, match='COMMIT'):
                selection.restore_selected_backup_months(object(), prepared, prepared.months)
        else:
            result = selection.restore_selected_backup_months(object(), prepared, prepared.months)
            assert result['months'] == prepared.months
    assert snapshots[-1].active_key == 'commit'
    assert (snapshots[-1].percent == 100) is not fail_commit
    assert snapshots[-1].steps[-1].state == ('running' if fail_commit else 'done')


def test_configuration_restore_progress_waits_until_file_publication(tmp_path, monkeypatch):
    from sme_gend.services.restore_files import RestoreFiles
    target = tmp_path / 'paths.json'; target.write_text('{"theme":"before"}', encoding='utf-8')
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'backups')
    monkeypatch.setattr(backup, 'user_paths_config_file', lambda: target)
    manifest = backup.BackupManifest(4, '2026-11-08', 'synthetic', 'invalid.test', {}, [])
    archive = tmp_path / 'configuration.zip'
    with ZipFile(archive, 'w') as stream:
        stream.writestr('manifest.json', json.dumps(manifest.to_dict()))
        stream.writestr('user_config/paths.json', '{"theme":"after"}')
    original = RestoreFiles.publish
    snapshots = []
    def publish(files):
        assert snapshots[-1].active_key == 'publish'
        assert snapshots[-1].percent < 100
        assert json.loads(target.read_text())['theme'] == 'before'
        return original(files)
    monkeypatch.setattr(RestoreFiles, 'publish', publish)
    scope = backup.RestoreScope(restore_mysql=False, restore_app_config=False, restore_app_data=False,
        restore_media_photos=False, restore_media_grades=False, preserve_local_accounts=False)
    with progress_reporting(snapshots.append):
        backup.restore_backup(Settings(database_path=tmp_path/'unused'), archive, scope=scope)
    assert snapshots[-1].percent == 100
    assert json.loads(target.read_text())['theme'] == 'after'
