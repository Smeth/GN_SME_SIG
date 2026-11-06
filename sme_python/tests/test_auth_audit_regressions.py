"""Régressions : session, droits révoqués, concurrence et boutons de rôles."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from sme_gend.config.auth_config import AuthConfig, AuthUser
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services.auth_service import AuthService, AuthSession


@pytest.fixture
def store(monkeypatch):
    state = {}
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: object())
    monkeypatch.setattr("sme_gend.services.app_config_store.load_config_dict", lambda s, k, **kwargs: deepcopy(state.get(k)))
    def save(s, key, payload, **kw):
        if kw.get("expected_revision", 0) != state.get(key, {}).get("config_rev", 0):
            raise ValueError("Configuration modifiée sur un autre poste")
        state[key] = deepcopy(payload)
    monkeypatch.setattr("sme_gend.services.app_config_store.save_config_dict", save)
    monkeypatch.setattr(AuthService, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(AuthService, "_sync_registry_after_rev_bump", lambda *a: None)
    monkeypatch.setattr("sme_gend.services.auth_session_registry.try_register_session", lambda *a, **kw: ("", None))
    return state


@pytest.mark.parametrize("session_state", ["missing", "expired", "reader", "disabled"])
def test_creation_requires_valid_authorized_session(store, session_state):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("reader", "readerpass", role="lecteur")
    auth.set_enabled(True)
    if session_state == "expired":
        auth._session = AuthSession("admin", "Admin", "super_admin", datetime.now(timezone.utc) - timedelta(seconds=1))
    elif session_state == "reader":
        assert auth.authenticate("reader", "readerpass") is not None
    elif session_state == "disabled":
        assert auth.authenticate("admin", "adminpass") is not None
        from sme_gend.services.app_config_store import KEY_AUTH
        store[KEY_AUTH]["users"][0]["disabled"] = True
    with pytest.raises(ValueError):
        auth.add_user("forbidden", "password123", role="super_admin")
    assert "forbidden" not in [u["username"] for u in next(iter(store.values()))["users"]]


def test_revoked_permissions_survive_save_and_reload(store):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    for role_id, denied in (("operateur", "effectifs.delete"), ("lecteur", "avancement.view")):
        role = auth.config.role_by_id(role_id)
        auth.update_role(role_id, permissions=[p for p in role.permissions if p != denied])
        auth.reload_config()
        assert denied not in auth.config.role_by_id(role_id).permissions


def test_stale_auth_save_cannot_remove_another_admins_account(store):
    from sme_gend.config.auth_config import save_auth_config
    first = AuthService()
    first.ensure_default_admin("adminpass")
    stale = AuthConfig.from_dict(first.config.to_dict())
    first.add_user("created_a", "password123")
    stale.users.append(AuthUser(username="created_b", password_hash="hash", salt="salt"))
    with pytest.raises(ValueError, match="autre poste"):
        save_auth_config(stale)
    first.reload_config()
    assert first.get_user("created_a") is not None
    assert first.get_user("created_b") is None
    first.add_user("created_b", "password123")
    first.reload_config()
    assert first.get_user("created_a") and first.get_user("created_b")


def test_delegated_account_manager_cannot_reset_administrator(store):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_role("manager", "Gestionnaire", permissions=["admin.users.accounts"])
    auth.add_user("manager1", "managerpass", role="manager")
    auth.add_user("admin2", "adminpass2", role="admin")
    auth.set_enabled(True)
    assert auth.authenticate("manager1", "managerpass") is not None
    with pytest.raises(ValueError, match="administrateur"):
        auth.change_password("admin2", "replacement123")


def test_disabled_super_admin_is_not_a_valid_replacement(store):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("disabled_root", "rootpass123", role="super_admin")
    auth.update_user("disabled_root", disabled=True)
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    with pytest.raises(ValueError, match="dernier super administrateur actif"):
        auth.update_user("admin", role="operateur")


def test_sql_revision_check_serializes_two_competing_writes():
    """Exerce le vrai dépôt avec deux transactions concurrentes et une ligne partagée."""
    lock, barrier = Lock(), Barrier(2)
    shared = {"payload": json.dumps({"config_rev": 0, "users": []})}
    results = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def execute(self, sql, params):
            if sql.startswith("SELECT"):
                assert "FOR UPDATE" in sql
            elif sql.startswith("UPDATE"):
                shared["payload"] = params[0]
            else:
                assert sql.startswith("INSERT INTO")
                assert "ON DUPLICATE KEY UPDATE" in sql
        def fetchone(self): return (shared["payload"],)
    class Connection:
        def cursor(self): return Cursor()
    class Factory:
        @contextmanager
        def connect(self):
            with lock:
                original = shared["payload"]
                try:
                    yield Connection()
                except Exception:
                    shared["payload"] = original
                    raise
    repo = AppConfigRepository.__new__(AppConfigRepository)
    repo._mc = Factory()
    def writer(name):
        barrier.wait(timeout=5)
        try:
            repo.set_json_if_revision("auth", {"config_rev": 1, "users": [name]}, expected_revision=0)
            results.append("saved")
        except ValueError:
            results.append("conflict")
    threads = [Thread(target=writer, args=(name,)) for name in ("user_a", "user_b")]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert sorted(results) == ["conflict", "saved"]
    saved = json.loads(shared["payload"])
    assert len(saved["users"]) == 1
    assert saved["config_rev"] == 1


@pytest.mark.parametrize("role_id,can_manage", [("operateur", False), ("lecteur", True), ("admin", False), ("super_admin", True), ("custom_role", True)])
def test_role_buttons_follow_permission_and_protection(role_id, can_manage, monkeypatch):
    from sme_gend.config.auth_config import AuthRole
    from sme_gend.ui.views import users_auth_view as ui
    cfg = AuthConfig()
    cfg.ensure_roles_and_catalog()
    cfg.roles.append(AuthRole(id="custom_role", name="Personnalisé"))
    fields = ["_role_list", "_role_name", "_role_desc", "_role_badge", "_role_lock_hint", "_btn_save_role", "_btn_del_role"]
    view = SimpleNamespace(**{name: MagicMock() for name in fields})
    view._role_list.item.return_value.data.return_value = role_id
    view._auth = SimpleNamespace(config=cfg)
    view._role_perm_checks = {"effectifs.edition": MagicMock()}
    monkeypatch.setattr(ui, "session_can_manage_user_roles", lambda: can_manage)
    ui.UsersAuthView._on_role_selected(view, 0)
    assert view._btn_save_role.setEnabled.call_args.args[0] == (can_manage and role_id != "super_admin")
    assert view._btn_del_role.setEnabled.call_args.args[0] == (can_manage and role_id == "custom_role")
    assert view._role_name.setReadOnly.call_args.args[0] == (not can_manage or role_id == "super_admin")
    if role_id in ("operateur", "lecteur"):
        assert "Rôle système" in view._role_badge.setText.call_args.args[0]


# These units use in-memory configuration; atomic SQL/audit rollback is tested natively.
import pytest
from contextlib import nullcontext

@pytest.fixture(autouse=True)
def isolated_auth_transaction(monkeypatch):
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_data_write',lambda *a,**kw:nullcontext())
    monkeypatch.setattr('sme_gend.services.audit_log._append_audit_mysql',lambda **kw:None)


def test_modern_admin_name_never_promotes_and_legacy_migrates_once():
    from sme_gend.config.auth_permissions import reconcile_system_users
    user = AuthUser(username="admin", password_hash="x", salt="y", role="admin")
    assert not reconcile_system_users([user])
    cfg = AuthConfig(users=[user])
    cfg.ensure_roles_and_catalog()
    assert AuthConfig.from_dict(cfg.to_dict()).users[0].role == "admin"
    legacy = cfg.to_dict()
    legacy.pop("auth_policy_version")
    migrated = AuthConfig.from_dict(legacy)
    assert migrated.users[0].role == "super_admin"
    migrated.users[0].role = "admin"
    assert AuthConfig.from_dict(migrated.to_dict()).users[0].role == "admin"


@pytest.mark.parametrize("role", ["admin", "settings_manager"])
def test_only_super_admin_can_disable_authentication(store, role):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_role("settings_manager", "Connexion", permissions=["admin.users.settings"])
    auth.add_user("delegate", "password123", role=role)
    auth.set_enabled(True)
    auth.authenticate("delegate", "password123")
    with pytest.raises(ValueError, match="super administrateur"):
        auth.set_enabled(False)
    assert auth.config.enabled and auth.is_enforced()
    auth.save_auth_settings(enabled=True, session_timeout_minutes=120)
    assert auth.config.session_timeout_minutes == 120
    auth.authenticate("admin", "adminpass")
    auth.set_enabled(False)
    assert not auth.is_enforced()


@pytest.mark.parametrize("target", ["admin", "admin2"])
@pytest.mark.parametrize("single", [False, True])
def test_delegated_session_revocation_respects_account_hierarchy(store, monkeypatch, target, single):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("admin2", "password123", role="admin")
    auth.add_role("manager", "Gestionnaire", permissions=["admin.users.accounts", "admin.users.settings"])
    auth.add_user("delegate", "password123", role="manager")
    auth.set_enabled(True)
    auth.authenticate("delegate", "password123")
    revoked = MagicMock()
    monkeypatch.setattr("sme_gend.services.auth_session_registry.revoke_user_sessions", revoked)
    monkeypatch.setattr("sme_gend.services.auth_session_registry.unregister_session", revoked)
    row = SimpleNamespace(session_token="remote", username=target, machine_label="Poste test")
    monkeypatch.setattr("sme_gend.services.auth_session_registry.list_active_sessions", lambda *a: [row])
    with pytest.raises(ValueError, match="administrateur"):
        if single:
            auth.revoke_active_session("remote")
        else:
            auth.revoke_all_sessions_for_user(target)
    revoked.assert_not_called()


def test_backup_delegation_never_grants_restore(store, monkeypatch, tmp_path):
    from sme_gend.config.auth_permissions import operateur_role_permissions
    from sme_gend.services.backup_restore_service import restore_backup
    assert "system.backup" not in operateur_role_permissions()
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_role("backup", "Sauvegarde", permissions=["system.backup", "system.restore"])
    auth.add_user("delegate", "password123", role="backup")
    auth.set_enabled(True)
    auth.authenticate("delegate", "password123")
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    assert auth.session_has_permission("system.backup")
    assert not auth.session_has_permission("system.restore")
    with pytest.raises(PermissionError, match="restauration"):
        restore_backup(object(), tmp_path / "nonexistent.zip")


@pytest.fixture
def users_view(store, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from sme_gend.ui.theme import apply_app_theme
    from sme_gend.ui.views import users_auth_view as ui
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    apply_app_theme(app, "light")
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("agent", "password123")
    auth.add_user("admin2", "password123", role="admin")
    auth.add_role("manager", "Gestionnaire", permissions=["admin.users.accounts", "admin.users.settings"])
    auth.add_user("delegate", "password123", role="manager")
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    monkeypatch.setattr(ui, "get_app_auth", lambda: auth)
    monkeypatch.setattr(ui.UsersAuthView, "on_page_activated", lambda self: None)
    reload_sessions = ui.UsersAuthView._reload_active_sessions
    monkeypatch.setattr(ui.UsersAuthView, "_reload_active_sessions", lambda self: None)
    monkeypatch.setattr(ui.UsersAuthView, "_reload_activity_log_async", lambda self: None)
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a: None)
    view = ui.UsersAuthView()
    view._test_reload_sessions = reload_sessions.__get__(view)
    view._apply_reload_payload(ui._AuthReloadPayload(auth.config, []))
    yield app, auth, view
    view.close()
    view.deleteLater()
    app.processEvents()


def _select_user(view, username):
    row = next(i for i in range(view._user_table.rowCount())
               if view._user_table.item(i, 0).data(256) == username)
    view._user_table.setCurrentCell(row, 0)
    return row


def test_user_field_reenabled_and_admin_actions_match_backend(users_view):
    app, auth, view = users_view
    view._clear_user_detail()
    row = _select_user(view, "agent")
    view._load_user_detail(row)
    assert view._detail_display.isEnabled() and not view._detail_display.isReadOnly()
    auth.authenticate("delegate", "password123")
    _select_user(view, "admin2")
    assert view._detail_display.isReadOnly()
    assert not view._btn_save_user.isEnabled()
    assert not view._btn_revoke_all_sessions.isEnabled()
    assert "admin" not in [r.id for r in view._assignable_roles(auth.config)]


def test_role_draft_survives_account_selection_and_cancel(users_view, monkeypatch):
    from sme_gend.ui.views import users_auth_view as ui
    app, auth, view = users_view
    role_row = next(i for i in range(view._role_list.count())
                    if view._role_list.item(i).data(256) == "operateur")
    view._role_list.setCurrentRow(role_row)
    view._role_name.setText("Brouillon à conserver")
    view._role_perm_checks["effectifs.delete"].setChecked(False)
    _select_user(view, "agent")
    assert view._role_name.text() == "Brouillon à conserver"
    assert not view._role_perm_checks["effectifs.delete"].isChecked()
    monkeypatch.setattr(ui, "confirm_yes_no", lambda *a, **kw: False)
    view._role_list.setCurrentRow(0)
    assert view._role_list.currentRow() == role_row
    assert view._role_name.text() == "Brouillon à conserver"
    assert view.has_unsaved_changes()
    view._detail_display.setText("Brouillon agent")
    _select_user(view, "admin2")
    assert view._selected_username() == "agent"
    assert view._detail_display.text() == "Brouillon agent"


def test_real_worker_keeps_qt_responsive_and_adopts_state(users_view, monkeypatch):
    from PySide6.QtCore import QThread, QTimer
    from time import sleep
    app, auth, view = users_view
    threads, ticks = [], []
    def slow_update(service, username, **kw):
        threads.append(QThread.currentThread() == app.thread())
        sleep(0.12)
        service.get_user(username).display_name = kw["display_name"]
    monkeypatch.setattr(AuthService, "update_user", slow_update)
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(10)
    view._perform_auth_change("update_user", "agent", display_name="Nouveau nom")
    timer.stop()
    assert threads == [False] and len(ticks) >= 2
    assert auth.get_user("agent").display_name == "Nouveau nom"


def test_stale_forms_cannot_overwrite_another_post(store):
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("agent", "password123")
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    user = auth.get_user("agent")
    old_user = (user.display_name or user.username, user.role, bool(user.disabled))
    auth.update_user("agent", display_name="Modifié ailleurs")
    with pytest.raises(ValueError, match="autre poste"):
        auth.update_user("agent", display_name="Ancien formulaire", expected_profile=old_user)
    role = auth.config.role_by_id("operateur")
    old_role = (role.name, role.description, tuple(sorted(role.permissions)))
    auth.update_role("operateur", name="Modifié ailleurs")
    with pytest.raises(ValueError, match="autre poste"):
        auth.update_role("operateur", name="Ancien formulaire", expected_profile=old_role)


def test_worker_never_revives_a_session_revoked_during_write(users_view, monkeypatch):
    from PySide6.QtCore import QTimer
    from time import sleep
    app, auth, view = users_view
    def slow_update(service, *args):
        sleep(0.1)
    monkeypatch.setattr(AuthService, "update_user", slow_update)
    QTimer.singleShot(10, lambda: setattr(auth, "_session", None))
    view._perform_auth_change("update_user", "agent")
    assert auth.current_session() is None


def test_users_ui_render_small_and_large(users_view, tmp_path):
    from PySide6.QtWidgets import QPushButton
    app, auth, view = users_view
    for width, height in ((960, 720), (1366, 768)):
        view.resize(width, height)
        view.show()
        app.processEvents()
        _select_user(view, "agent")
        for index, name in ((0, "comptes"), (1, "roles"), (2, "connexion"), (3, "sessions"), (4, "journal")):
            view._tabs.setCurrentIndex(index)
            app.processEvents()
            assert view.width() <= width
            assert view.height() <= height
            if index == 2:
                assert not view._sessions_table.isVisible()
                assert view._chk_enabled.isVisible()
            if index == 3:
                assert view._sessions_table.isVisible()
                assert not view._chk_enabled.isVisible()
                assert view._sessions_table.height() > height * .55
            assert view.grab().save(str(tmp_path / f"utilisateurs_{name}_{width}.png"))
        view._tabs.setCurrentIndex(0)
        more = next(b for b in view.findChildren(QPushButton) if b.text() == "Actions du compte…")
        assert more.isVisible() and more.menu() is not None
        more.menu().aboutToShow.emit()
        assert len(more.menu().actions()) == 5
        view._btn_new_account.setChecked(True)
        app.processEvents()
        assert view.height() <= height and view.width() <= width
        assert view.grab().save(str(tmp_path / f"utilisateurs_creation_{width}.png"))
        view._btn_new_account.setChecked(False)


def test_session_reload_runs_in_worker_and_hides_protected_actions(users_view, monkeypatch):
    from PySide6.QtCore import QElapsedTimer, QThread
    from PySide6.QtTest import QTest
    app, auth, view = users_view
    auth.authenticate("delegate", "password123")
    threads = []
    def verify(service):
        threads.append(QThread.currentThread() == app.thread())
    now = datetime.now(timezone.utc)
    rows = [SimpleNamespace(username=name, session_token=name, machine_label="Poste test",
             client_instance_id=name, last_seen_at=now, expires_at=now)
            for name in ("admin", "admin2", "agent")]
    monkeypatch.setattr(AuthService, "ensure_active_session_registered", verify)
    monkeypatch.setattr(AuthService, "local_session_context", lambda self: ("HERE", "CLIENT", "Poste local"))
    monkeypatch.setattr(AuthService, "list_active_sessions", lambda self: rows)
    view._test_reload_sessions()
    timer = QElapsedTimer()
    timer.start()
    while view._sessions_busy and timer.elapsed() < 3000:
        QTest.qWait(10)
    assert not view._sessions_busy and threads == [False]
    assert [view._sessions_table.cellWidget(i, 4).isEnabled() for i in range(3)] == [False, False, True]
