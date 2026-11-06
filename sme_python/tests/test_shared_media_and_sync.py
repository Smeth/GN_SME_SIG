"""Deux postes : révision centrale et lecteurs différents vers un même partage."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import ctypes
import os

import pytest

from sme_gend.config.settings import Settings


def test_two_clients_observe_the_same_revision(monkeypatch):
    from sme_gend.services import data_sync
    state = {}
    repo = SimpleNamespace(table_exists=lambda name: name == "app_config",
                           get_json=lambda key: state.get(key),
                           set_json=lambda key, value: state.update({key: value}))
    monkeypatch.setattr("sme_gend.repositories.app_config_repository.AppConfigRepository", lambda settings: repo)
    first, second = object(), object()
    before = data_sync.read_data_revision(second)
    token = data_sync.publish_data_change(first, reason="career_import", mois="2026-09")
    assert token != before
    assert data_sync.read_data_revision(second) == token


@pytest.mark.skipif(os.name != "nt", reason="API des lecteurs Windows")
def test_different_drive_letters_resolve_to_identical_unc(monkeypatch):
    from ctypes import wintypes
    from sme_gend.services import shared_media
    unc = ctypes.create_unicode_buffer(r"\\serveur\partage\personnel_media")
    seen = []
    def win_api(local, level, buffer, size):
        seen.append(local)
        assert level == 1
        ctypes.cast(buffer, ctypes.POINTER(wintypes.LPWSTR))[0] = ctypes.cast(unc, wintypes.LPWSTR)
        return 0
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: SimpleNamespace(WNetGetUniversalNameW=win_api))
    assert shared_media.canonical_network_path(r"Z:\personnel_media") == shared_media.canonical_network_path(r"P:\personnel_media")
    assert len(seen) == 2


def test_shared_configuration_overrides_local_drive(monkeypatch):
    from sme_gend.services import shared_media
    settings = Settings(database_path=Path("unused"))
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: settings)
    monkeypatch.setattr(shared_media, "_cached", (0.0, None, None))
    repo = SimpleNamespace(get_json=lambda key: {"unc_path": r"\\serveur\partage\images"})
    monkeypatch.setattr("sme_gend.repositories.app_config_repository.AppConfigRepository", lambda settings: repo)
    assert str(shared_media.shared_media_root()) == r"\\serveur\partage\images"


def test_switching_storage_copies_photos_without_overwrite_or_deletion(tmp_path, monkeypatch):
    from sme_gend.services import shared_media
    old, new, local = tmp_path / "old", tmp_path / "new", tmp_path / "local"
    for root in (old, new, local / "personnel_media"):
        (root / "photos").mkdir(parents=True)
    (old / "photos/42.jpg").write_bytes(b"old")
    (old / "photos/43.jpg").write_bytes(b"copy")
    (new / "photos/42.jpg").write_bytes(b"shared-existing")
    (local / "personnel_media/photos/44.jpg").write_bytes(b"local-old")
    monkeypatch.setattr(shared_media, "canonical_network_path", lambda path: str(new))
    monkeypatch.setattr("sme_gend.config.user_paths.user_application_data_dir", lambda: local)
    shared_media.configure_shared_media(object(), str(new), old)
    assert (new / "photos/42.jpg").read_bytes() == b"shared-existing"
    assert (new / "photos/43.jpg").read_bytes() == b"copy"
    assert (new / "photos/44.jpg").read_bytes() == b"local-old"
    assert (old / "photos/43.jpg").is_file()


def test_remote_revision_notifies_without_reloading_current_screen(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.shell_window import ShellWindow
    app = QApplication.instance() or QApplication([])
    view = SimpleNamespace(_shared_revision_busy=False, _shared_revision="old", _settings=Settings(database_path=Path("unused")),
                           _stack=MagicMock(), _page_widget=lambda *a, **kw: None,
                           refresh_for_month_change=MagicMock(), _reload_current_view=MagicMock(), statusBar=MagicMock())
    monkeypatch.setattr("sme_gend.services.data_sync.read_data_revision", lambda settings: "new")
    monkeypatch.setattr("sme_gend.ui.bg_task.run_in_background", lambda parent, work, done, **kw: done(work()))
    ShellWindow._poll_shared_data_revision(view)
    assert view._shared_revision == "old"
    assert view._shared_revision_pending == "new"
    assert view._sync_state == 'available'
    assert not view._shared_revision_busy
    view.refresh_for_month_change.assert_not_called()
    view._reload_current_view.assert_not_called()


def test_closing_while_sync_runs_does_not_block_gui(monkeypatch):
    import sme_gend.ui.shell_window as shell
    monkeypatch.setattr(shell, "confirm_yes_no", lambda *args, **kwargs: True)
    worker = MagicMock()
    worker.isRunning.return_value = True
    worker.wait.side_effect = AssertionError("GUI thread must not wait")
    view = SimpleNamespace(_has_unsaved_changes=lambda: False, _shared_revision_timer=MagicMock(),
                           _shared_revision_worker=worker, close=MagicMock(), statusBar=MagicMock())
    event = MagicMock()
    shell.ShellWindow.closeEvent(view, event)
    event.ignore.assert_called_once()
    worker.finished.connect.assert_called_once_with(view.close)
    worker.wait.assert_not_called()
