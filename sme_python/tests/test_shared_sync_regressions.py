"""Remote refresh races and shared-media publication, using synthetic files only."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import shutil

import pytest
from PySide6.QtWidgets import QApplication, QTableView, QAbstractItemView

from sme_gend.config.settings import Settings
from sme_gend.services import shared_media
from sme_gend.ui.shell_window import ShellWindow


def shell_probe(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    view = SimpleNamespace(_shared_revision_busy=False, _shared_revision='old',
        _settings=Settings(database_path=Path('unused')), _stack=MagicMock(),
        _page_widget=lambda *a, **kw: None, _has_unsaved_changes=lambda: False,
        refresh_for_month_change=MagicMock(), _reload_current_view=MagicMock(), statusBar=MagicMock())
    monkeypatch.setattr('sme_gend.ui.bg_task.run_in_background',
                        lambda parent, work, done, **kw: pending.append((work, done, kw)))
    monkeypatch.setattr('sme_gend.services.data_sync.read_data_revision', lambda settings: 'new')
    return app, view, pending


@pytest.mark.parametrize('interruption', ['modal', 'dirty', 'editing'])
def test_remote_reply_defers_refresh_if_input_started_during_query(monkeypatch, interruption):
    app, view, pending = shell_probe(monkeypatch)
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    assert len(pending) == 1
    table = QTableView()
    if interruption == 'modal':
        monkeypatch.setattr(QApplication, 'activeModalWidget', lambda: object())
    elif interruption == 'dirty':
        view._has_unsaved_changes = lambda: True
    else:
        table.setState(QAbstractItemView.State.EditingState)
        view._page_widget = lambda *a, **kw: SimpleNamespace(_table=table)
    pending[0][1](pending[0][0]())
    assert view._shared_revision == 'old'
    assert not view._shared_revision_busy
    view.refresh_for_month_change.assert_not_called()
    view._reload_current_view.assert_not_called()
    monkeypatch.setattr(QApplication, 'activeModalWidget', lambda: None)
    view._has_unsaved_changes = lambda: False
    table.setState(QAbstractItemView.State.NoState)
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    pending[1][1](pending[1][0]())
    assert view._shared_revision == 'new'
    view._reload_current_view.assert_called_once()


def test_remote_poll_does_not_query_with_unsaved_form(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    view._has_unsaved_changes = lambda: True
    ShellWindow._poll_shared_data_revision(view)
    assert pending == []


def test_remote_revision_is_acknowledged_only_after_successful_refresh(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    view._reload_current_view.side_effect = OSError('synthetic unavailable view')
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    try:
        pending[0][1]('new')
    except OSError:
        pass
    assert view._shared_revision == 'old'
    assert not view._shared_revision_busy


def test_reply_from_previous_database_does_not_refresh_new_profile(monkeypatch):
    from dataclasses import replace
    app, view, pending = shell_probe(monkeypatch)
    ShellWindow._poll_shared_data_revision(view)
    view._settings = replace(view._settings, mysql_database='different_synthetic_database')
    pending[0][1]('new')
    assert view._shared_revision == 'old'
    view._reload_current_view.assert_not_called()


@pytest.mark.parametrize('value', ['', r'\\server', r'\\.\C:\photos', r'Z:photos', r'\\server\share\..\other'])
def test_ambiguous_or_device_network_selection_is_refused_before_io(value):
    with pytest.raises(ValueError):
        shared_media.canonical_network_path(value)


def test_interrupted_migration_does_not_publish_partial_image(tmp_path, monkeypatch):
    source, target = tmp_path/'source', tmp_path/'target'
    (source/'photos').mkdir(parents=True)
    (source/'photos/42.jpg').write_bytes(b'whole image')
    monkeypatch.setattr(shared_media, 'canonical_network_path', lambda value: str(target))
    monkeypatch.setattr('sme_gend.config.user_paths.user_application_data_dir', lambda: tmp_path/'profile')
    def broken_copy(source_file, destination):
        Path(destination).write_bytes(b'partial')
        raise OSError('synthetic lost connection')
    monkeypatch.setattr(shutil, 'copy2', broken_copy)
    with pytest.raises(OSError):
        shared_media.configure_shared_media(object(), str(target), source)
    assert not (target/'photos/42.jpg').exists()
    assert (source/'photos/42.jpg').read_bytes() == b'whole image'
    assert not list(target.rglob('.sme-*'))


def test_concurrent_existing_image_is_not_overwritten_during_migration(tmp_path, monkeypatch):
    source, target = tmp_path/'source', tmp_path/'target'
    (source/'photos').mkdir(parents=True)
    (source/'photos/42.jpg').write_bytes(b'migration')
    monkeypatch.setattr(shared_media, 'canonical_network_path', lambda value: str(target))
    monkeypatch.setattr('sme_gend.config.user_paths.user_application_data_dir', lambda: tmp_path/'profile')
    original = shutil.copy2
    def racing_copy(source_file, destination):
        (target/'photos/42.jpg').write_bytes(b'other workstation')
        return original(source_file, destination)
    monkeypatch.setattr(shutil, 'copy2', racing_copy)
    shared_media.configure_shared_media(object(), str(target), source)
    assert (target/'photos/42.jpg').read_bytes() == b'other workstation'


def test_media_save_captures_one_root_for_the_whole_write(tmp_path, monkeypatch):
    from sme_gend.services import personnel_media
    roots = [tmp_path/'first', tmp_path/'second']
    calls = []
    def root():
        calls.append(1)
        return roots[min(len(calls)-1, 1)]
    monkeypatch.setattr(personnel_media, 'personnel_media_root', root)
    source = tmp_path/'source.png'
    source.write_bytes(b'synthetic image')
    result = personnel_media.save_person_photo('42', source)
    assert result == roots[0]/'photos/42.png'
    assert len(calls) == 1



def test_migration_keeps_existing_photo_with_a_different_extension(tmp_path, monkeypatch):
    source, target = tmp_path/'source', tmp_path/'target'
    (source/'photos').mkdir(parents=True)
    (target/'photos').mkdir(parents=True)
    (source/'photos/42.jpg').write_bytes(b'older local photo')
    (target/'photos/42.png').write_bytes(b'current shared photo')
    monkeypatch.setattr(shared_media, 'canonical_network_path', lambda value: str(target))
    monkeypatch.setattr('sme_gend.config.user_paths.user_application_data_dir', lambda: tmp_path/'profile')
    shared_media.configure_shared_media(object(), str(target), source)
    assert not (target/'photos/42.jpg').exists()
    assert (target/'photos/42.png').read_bytes() == b'current shared photo'


def test_shared_cache_refresh_keeps_last_unc_when_mysql_is_unavailable(monkeypatch):
    import time
    settings = Settings(database_path=Path('unused'))
    from sme_gend.repositories.mysql_connection import mysql_database_key
    old = Path(r'\\synthetic-server\share\previous')
    monkeypatch.setattr(shared_media, '_cached', (time.monotonic(), mysql_database_key(settings), old))
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    def unavailable(key):
        raise OSError('synthetic connection down')
    repo = SimpleNamespace(get_json=unavailable)
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository', lambda settings: repo)
    shared_media.invalidate_shared_media_cache()
    assert shared_media.shared_media_root() == old
    monkeypatch.setattr(shared_media, '_cached', (0, ('other-host', 64000, 'other'), old))
    assert shared_media.shared_media_root() is None


def test_remote_refresh_invalidates_shared_root_before_rebinding(monkeypatch):
    import time
    from sme_gend.repositories.mysql_connection import mysql_database_key
    app, view, pending = shell_probe(monkeypatch)
    old, new = Path(r'\\synthetic-server\share\old'), Path(r'\\synthetic-server\share\new')
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: view._settings)
    monkeypatch.setattr(shared_media, '_cached', (time.monotonic(), mysql_database_key(view._settings), old))
    repo = SimpleNamespace(get_json=lambda key: {'unc_path': str(new)})
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository', lambda settings: repo)
    paths = []
    view.refresh_for_month_change.side_effect = lambda **kwargs: paths.append(shared_media.shared_media_root())
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    pending[0][1]('new')
    assert paths == [new]


def test_repeated_remote_writes_are_coalesced_without_reloading(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    for token in ('new-1', 'new-2', 'new-3', 'new-3'):
        ShellWindow._poll_shared_data_revision(view)
        pending[-1][1](token)
    assert view._shared_revision == 'old'
    assert view._shared_revision_pending == 'new-3'
    assert view._sync_state == 'available'
    view.refresh_for_month_change.assert_not_called()
    view._reload_current_view.assert_not_called()
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    pending[-1][1]('new-3')
    view.refresh_for_month_change.assert_called_once_with(strict=True, refresh_current=False)
    view._reload_current_view.assert_called_once()
    assert view._shared_revision == 'new-3'
    assert view._shared_revision_pending is None


def test_initial_revision_establishes_baseline_without_rebinding(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    view._shared_revision = None
    ShellWindow._poll_shared_data_revision(view)
    pending[0][1]('initial')
    assert view._shared_revision == 'initial'
    view.refresh_for_month_change.assert_not_called()
    view._reload_current_view.assert_not_called()


def test_manual_refresh_without_remote_change_loads_only_visible_view(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    pending[0][1]('old')
    view.refresh_for_month_change.assert_not_called()
    view._reload_current_view.assert_called_once()


def test_f5_during_automatic_poll_is_not_dropped(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    ShellWindow._poll_shared_data_revision(view)
    for _ in range(3):
        ShellWindow._poll_shared_data_revision(view, refresh=True)
    assert len(pending) == 1
    assert view._shared_manual_refresh_pending
    pending[0][1]('old')
    view._reload_current_view.assert_called_once()
    assert not view._shared_manual_refresh_pending


def test_deferred_f5_resumes_after_edit_is_saved(monkeypatch):
    app, view, pending = shell_probe(monkeypatch)
    view._has_unsaved_changes = lambda: True
    ShellWindow._poll_shared_data_revision(view, refresh=True)
    assert not pending
    assert view._shared_manual_refresh_pending
    view._has_unsaved_changes = lambda: False
    ShellWindow._poll_shared_data_revision(view)
    pending[0][1]('old')
    view._reload_current_view.assert_called_once()
    assert not view._shared_manual_refresh_pending


def test_query_in_flight_cannot_refill_invalidated_media_cache(monkeypatch):
    import time
    from sme_gend.repositories.mysql_connection import mysql_database_key
    settings = Settings(database_path=Path('unused'))
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr(shared_media, '_cached', (0, mysql_database_key(settings), None))
    def invalidate_during_query(key):
        shared_media.invalidate_shared_media_cache()
        return {'unc_path': r'\\synthetic-server\share\stale'}
    repo = SimpleNamespace(get_json=invalidate_during_query)
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository', lambda settings: repo)
    shared_media.shared_media_root()
    assert shared_media._cached[0] < 0


@pytest.mark.parametrize('value, expected', [
    (r'\\serveur\partage\photos', r'\\serveur\partage\photos'),
    ('//serveur/partage/photos', r'\\serveur\partage\photos'),
    (r'\\?\UNC\serveur\partage\photos', r'\\serveur\partage\photos'),
])
def test_unc_canonicalization_is_offline_and_preserves_server_share(value, expected):
    assert shared_media.canonical_network_path(value) == expected


def test_image_notification_failure_is_explicitly_post_write(monkeypatch):
    from sme_gend.services import personnel_media
    settings = Settings(database_path=Path('unused'))
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    def failure(*args, **kwargs):
        raise OSError('synthetic server down')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change', failure)
    with pytest.raises(personnel_media.MediaWriteCommittedError, match='déjà copiée') as error:
        personnel_media._notify_shared_media_write(Path(r'\\synthetic-server\share'))
    assert error.value.committed is True


def test_media_cleanup_keeps_unrelated_file_and_reports_permission_failure(tmp_path, monkeypatch):
    from sme_gend.services import personnel_media
    monkeypatch.setattr(personnel_media, 'personnel_media_root', lambda: tmp_path)
    (tmp_path/'photos').mkdir()
    old = tmp_path/'photos/42.jpg'
    old.write_bytes(b'previous')
    (tmp_path/'photos/42.txt').write_bytes(b'keep')
    source=tmp_path/'source.png'; source.write_bytes(b'new')
    unlink=Path.unlink
    def denied(path, *args, **kwargs):
        if path == old:
            raise PermissionError('synthetic denied')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', denied)
    with pytest.raises(personnel_media.MediaWriteCommittedError):
        personnel_media.save_person_photo('42', source)
    assert old.read_bytes()==b'previous'
    assert (tmp_path/'photos/42.png').read_bytes()==b'new'
    assert (tmp_path/'photos/42.txt').read_bytes()==b'keep'



@pytest.mark.parametrize('month', ['2026-10', ''])
def test_remote_rebinding_failure_propagates_without_acknowledging(month):
    def failed(*args):
        raise OSError('synthetic database interruption')
    view=SimpleNamespace(_operational_mois=lambda: month, _last_operational_mois=month,
        _display_mois=lambda: month, _apply_month_data_source=failed,
        _rebind_data_services=failed, _settings=object(), _home_view=MagicMock(),
        _refresh_period_banner=MagicMock(), _refresh_active_views_on_month_change=MagicMock())
    with pytest.raises(OSError):
        ShellWindow.refresh_for_month_change(view, strict=True)
    view._home_view.mark_dirty.assert_not_called()



@pytest.mark.parametrize('drive_type', [1, 4])
def test_disconnected_or_unresolved_drive_is_not_treated_as_local(monkeypatch, drive_type):
    import ctypes
    import os
    if os.name!='nt':
        pytest.skip('Windows drive API')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name: SimpleNamespace(WNetGetUniversalNameW=lambda *args: 1201))
    monkeypatch.setattr(ctypes.windll.kernel32, 'GetDriveTypeW', lambda path: drive_type)
    with pytest.raises(OSError, match='UNC'):
        shared_media.canonical_network_path(r'Z:\images')


def test_windows_universal_name_retries_required_buffer_size(monkeypatch):
    import ctypes
    import os
    from ctypes import wintypes
    if os.name!='nt':
        pytest.skip('Windows drive API')
    unc=ctypes.create_unicode_buffer(r'\\synthetic-server\share\images')
    calls=[]
    def api(value, level, buffer, size):
        calls.append(value)
        if len(calls)==1:
            ctypes.cast(size,ctypes.POINTER(wintypes.DWORD))[0]=131072
            return 234
        ctypes.cast(buffer,ctypes.POINTER(wintypes.LPWSTR))[0]=ctypes.cast(unc,wintypes.LPWSTR)
        return 0
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name: SimpleNamespace(WNetGetUniversalNameW=api))
    assert shared_media.canonical_network_path(r'P:\images')==r'\\synthetic-server\share\images'
    assert len(calls)==2
