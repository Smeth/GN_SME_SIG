"""Global period mutations and connection repair have distinct authorization boundaries."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget

from sme_gend.config.settings import Settings
from sme_gend.config.user_paths import UserPathsOverrides
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.services import active_month_guard as guard
from sme_gend.services import auth_guard, sme_editing_policy, sme_month_closure, shared_media
from sme_gend.services.sme_workspace_service import SmeWorkspaceService
from sme_gend.ui import paths_config_form as config_ui
from sme_gend.ui.shell_window import ShellWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_auth_or_dialogs(monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: None)
    monkeypatch.setattr(QMessageBox, "warning", Mock())


@pytest.mark.parametrize("operation", ["activate", "reopen", "close", "close_commit", "close_transition", "import", "media"])
def test_business_mutations_deny_before_any_repository_access(monkeypatch, operation):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    service = SmeWorkspaceService.__new__(SmeWorkspaceService)
    calls = {
        "activate": lambda: service.set_active_mois("2026-10"),
        "reopen": lambda: sme_editing_policy.reopen_closed_month_for_corrections(None, "2026-10", "actor", "reason"),
        "close": lambda: sme_month_closure.run_piste_a_month_closure(None, "2026-09", "2026-10"),
        "close_commit": lambda: sme_month_closure._commit_closure_transition(None, "2026-09", "2026-10", action="test", audit_payload={}),
        "close_transition": lambda: service.close_month_transition("2026-09", "2026-10"),
        "import": lambda: sme_month_closure.import_existing_db_for_month(None, None, "2026-09"),
        "media": lambda: shared_media.configure_shared_media(None, "unused", None),
    }
    with pytest.raises(PermissionError):
        calls[operation]()


@pytest.mark.parametrize("permission", ["workflow.import_archive", "workflow.close_month"])
def test_import_and_close_requires_both_rights_before_import(monkeypatch, permission):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda value: value == permission)
    with pytest.raises(PermissionError):
        sme_month_closure.import_existing_db_for_month(None, None, "2026-09", archive_and_close=True)


@pytest.mark.parametrize("target_status,allowed", [("Fermé", False), ("Closed", False), ("Clôturé", False), ("Réouvert", True)])
def test_closed_month_must_follow_exceptional_reopening_first(monkeypatch, target_status, allowed):
    settings = Settings(database_path=Path("unused.mdb"))
    before = guard.ActiveMonthSnapshot(mysql_database_key(settings),
        ((1, "2026-09", "Ouvert", "SME-202609"), (2, "2026-10", target_status, "SME-202610")), "revision")
    service = SmeWorkspaceService.__new__(SmeWorkspaceService)
    service._settings = settings
    service._apply_active_month_locked = Mock()
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: permission == "workflow.set_active_month")
    @contextmanager
    def period_write(*args, **kwargs):
        yield SimpleNamespace(before=before)
    monkeypatch.setattr(guard, "period_state_write", period_write)
    monkeypatch.setattr(guard, "load_active_month_snapshot", lambda *args, **kwargs: before)
    if allowed:
        assert service.set_active_mois("2026-10", expected=before) == "2026-10"
        service._apply_active_month_locked.assert_called_once_with("2026-10")
    else:
        with pytest.raises(ValueError, match="réouverture exceptionnelle"):
            service.set_active_mois("2026-10", expected=before)
        service._apply_active_month_locked.assert_not_called()


def test_reader_can_navigate_without_activating_shared_month(monkeypatch):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    monkeypatch.setattr("sme_gend.services.sme_workspace_service.SmeWorkspaceService",
        lambda settings: SimpleNamespace(list_known_months=lambda: ["2026-08", "2026-09", "2026-10"]))
    parent = SimpleNamespace(_settings=None, _display_mois=lambda: "2026-09", _activate_period=Mock())
    assert ShellWindow._month_nav_bounds(parent) == (True, True)
    ShellWindow._navigate_month(parent, -1)
    parent._activate_period.assert_called_once_with("2026-08")


@pytest.mark.parametrize("target,expected", [("2026-08", "2026-08"), ("2026-10", "2026-10"), ("2026-09", None)])
def test_display_change_never_saves_operational_context(monkeypatch, target, expected):
    monkeypatch.setattr("sme_gend.ui.shell_window.save_sme_context", lambda *args, **kwargs: pytest.fail("Consultation wrote shared state"))
    parent = SimpleNamespace(_has_unsaved_changes=lambda: False, _operational_mois=lambda: "2026-09",
                             refresh_for_month_change=Mock())
    ShellWindow._activate_period(parent, target)
    assert parent._consultation_mois == expected
    parent.refresh_for_month_change.assert_called_once()


def test_configuration_menu_denies_before_opening_credentials(monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth",
        lambda: SimpleNamespace(session_has_permission=lambda permission: False))
    ShellWindow._open_paths_config(SimpleNamespace())


def test_configuration_form_denies_before_loading_credentials(app, monkeypatch):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    monkeypatch.setattr(config_ui, "load_user_paths", lambda: pytest.fail("Read credentials without permission"))
    with pytest.raises(PermissionError):
        config_ui.PathsConfigForm()


def test_configuration_save_rechecks_right_before_reading_form(monkeypatch):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    assert config_ui.PathsConfigForm.try_save(SimpleNamespace(), confirm=False) is False


def _form_stub(*, bootstrap=False):
    form = Mock()
    form._bootstrap_configuration = bootstrap
    for field in ("_db", "_tpl", "_tnum", "_tdet", "_arch", "_media"):
        getattr(form, field).text.return_value = ""
    form._mysql_host.text.return_value = "new-server"
    form._mysql_database.text.return_value = "sme_test"
    form._mysql_user.text.return_value = "application"
    form._mysql_password.text.return_value = "synthetic"
    form._validated_mysql_port.return_value = 3306
    form._chk_startup.isEnabled.return_value = False
    form._build_mysql_settings.return_value = object()
    return form


def test_permission_revoked_during_connection_test_prevents_save(app, monkeypatch):
    allowed = {"value": True}
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: allowed["value"])
    def connected(connection):
        allowed["value"] = False
        return True, "ok"
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection.test_connection", connected)
    save = Mock()
    monkeypatch.setattr(config_ui, "save_user_paths", save)
    assert config_ui.PathsConfigForm.try_save(_form_stub(), confirm=False) is False
    save.assert_not_called()


def test_bootstrap_repairs_only_connection_preserving_other_paths(app, monkeypatch):
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    previous = UserPathsOverrides(database_path="missing-old.mdb", template_excel_path="missing-old.xlsx",
        sme_archives_path="missing-archives", personnel_media_root_path="old-media", personnel_media_local_override=True)
    monkeypatch.setattr(config_ui, "load_user_paths", lambda: previous)
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection.test_connection", lambda self: (True, "ok"))
    monkeypatch.setattr(shared_media, "configure_shared_media", lambda *args: pytest.fail("Bootstrap switched shared storage"))
    monkeypatch.setattr("sme_gend.services.connection_config_audit.record_connection_config_saved", lambda settings: "")
    save = Mock()
    monkeypatch.setattr(config_ui, "save_user_paths", save)
    form = _form_stub(bootstrap=True)
    form._media.text.return_value = "different-media"
    assert config_ui.PathsConfigForm.try_save(form, confirm=False)
    assert save.call_args.kwargs["mysql_host"] == "new-server"
    assert save.call_args.kwargs["database_path"] == "missing-old.mdb"
    assert save.call_args.kwargs["template_excel_path"] == "missing-old.xlsx"
    assert save.call_args.kwargs["personnel_media_root_path"] == "old-media"
    assert save.call_args.kwargs["personnel_media_local_override"] is True


def test_bootstrap_flag_cannot_bypass_an_initialized_auth_service(monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: object())
    assert config_ui.PathsConfigForm.try_save(SimpleNamespace(_bootstrap_configuration=True), confirm=False) is False


def test_bootstrap_form_disables_import_and_non_connection_settings(app, monkeypatch):
    monkeypatch.setattr(config_ui, "load_user_paths", UserPathsOverrides)
    monkeypatch.setattr(shared_media, "shared_media_root", lambda: pytest.fail("Bootstrap read shared media settings"))
    monkeypatch.setattr("sme_gend.services.windows_startup.is_supported", lambda: False)
    form = config_ui.PathsConfigForm(bootstrap_configuration=True)
    tabs = form.findChild(QTabWidget)
    assert tabs.isTabEnabled(0)
    assert all(not tabs.isTabEnabled(index) for index in range(1, tabs.count()))
    assert not form._btn_import_mysql.isEnabled()
    assert not form._db.isEnabled()
    form.close()


@pytest.mark.parametrize("value", ["2026-00", "2026-13", "2026-99", "0000-01", "2026-011", "12026-01", "202600", "202613", "002026", "132026"])
def test_invalid_month_labels_do_not_reach_period_transitions(value):
    assert sme_month_closure.extract_yyyy_mm(value) is None
    assert sme_month_closure.canonical_sme_mois_yyyy_mm(value) is None


@pytest.mark.parametrize("value,expected", [("2026-01", "2026-01"), ("2026-12", "2026-12"),
    ("Période 2026-12 SME", "2026-12"), ("202601", "2026-01"), ("122026", "2026-12")])
def test_valid_month_labels_keep_access_archive_compatibility(value, expected):
    assert sme_month_closure.canonical_sme_mois_yyyy_mm(value) == expected


def test_lost_closure_right_after_import_reports_what_is_already_committed(monkeypatch, tmp_path):
    settings = Settings(database_path=tmp_path / "source.mdb")
    settings.database_path.write_bytes(b"SYNTHETIC ACCESS")
    monkeypatch.setattr(guard, "load_active_month_snapshot", lambda *args, **kwargs: object())
    monkeypatch.setattr(sme_month_closure, "sme_archives_dir", lambda: tmp_path)
    monkeypatch.setattr(sme_month_closure, "_snapshot_file", lambda *args: tmp_path / "archive.mdb")
    monkeypatch.setattr("sme_gend.services.mysql_bundle_import.import_full_mysql_bundle_from_access",
        lambda *args, **kwargs: {"sme_mois": "2026-09", "referentials": {"warnings": []}})
    def denied(*args, **kwargs):
        raise PermissionError("Lost closure right")
    monkeypatch.setattr(sme_month_closure, "_commit_closure_transition", denied)
    with pytest.raises(PermissionError, match="import MySQL est enregistré.*vos droits ont changé"):
        sme_month_closure.import_existing_db_for_month(settings, settings.database_path, "2026-09", "2026-10")
