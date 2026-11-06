"""Catalogue, navigation and denial before any persistence or document action."""
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sme_gend.config.auth_permissions import all_permission_ids
from sme_gend.ui.access_control import PAGE_PERMISSION, session_can_access_page
from sme_gend.ui import navigation_pages as nav
from sme_gend.services.workflow_permissions import WORKFLOW_ACTION_PERMISSION
from sme_gend.services.shared_config_write import CONFIG_WRITE_PERMISSIONS


def test_all_pages_and_actions_use_catalogued_permissions():
    catalog = set(all_permission_ids())
    assert set(PAGE_PERMISSION) == set(range(nav.PAGE_COUNT)) - {nav.PAGE_CSV_IMPORT}
    assert set(PAGE_PERMISSION.values()) <= catalog
    assert set(WORKFLOW_ACTION_PERMISSION.values()) <= catalog
    assert set(CONFIG_WRITE_PERMISSIONS.values()) <= catalog
    root = Path(__file__).parents[1] / 'src' / 'sme_gend'
    unknown = []
    checks = {'require_permission', 'session_has_permission', '_session_has', 'assert_any_permission',
              'requires_any_permission'}
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
            if name not in checks:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and '.' in arg.value:
                    if arg.value not in catalog:
                        unknown.append((path.name, node.lineno, arg.value))
    assert not unknown


@pytest.mark.parametrize('permission,page', [
    ('system.restore', nav.PAGE_BACKUP_RESTORE),
    ('effectifs.restore', nav.PAGE_DELETED_PERSONNEL),
    ('system.backup', nav.PAGE_BACKUP_RESTORE),
    ('effectifs.delete', nav.PAGE_DELETED_PERSONNEL),
])
def test_action_right_opens_corresponding_page(monkeypatch, permission, page):
    auth = SimpleNamespace(session_has_permission=lambda pid: pid == permission)
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: auth)
    assert session_can_access_page(page)
    assert not session_can_access_page(-1)
    assert not session_can_access_page(nav.PAGE_COUNT)
    assert not session_can_access_page(nav.PAGE_CSV_IMPORT)


def test_service_mutations_deny_before_reading_or_writing(monkeypatch):
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.services.structure_organigram_service import StructureOrganigramService
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda pid: False)
    checked = []
    for cls in (ReferentielService, StructureOrganigramService):
        service = cls.__new__(cls)  # No repositories, settings or database available.
        for name, method in inspect.getmembers(cls, inspect.isfunction):
            source = inspect.getsource(method)
            if '@requires_any_permission' not in source:
                continue
            args = {p.name: None for p in inspect.signature(method).parameters.values()
                    if p.name != 'self' and p.default is inspect.Parameter.empty
                    and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)}
            with pytest.raises(PermissionError):
                getattr(service, name)(**args)
            checked.append(name)
    assert len(checked) >= 30


@pytest.mark.parametrize('key', CONFIG_WRITE_PERMISSIONS)
def test_shared_config_denies_before_database_access(monkeypatch, key):
    from sme_gend.services.shared_config_write import save_shared_model
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda pid: False)
    with pytest.raises(PermissionError):
        save_shared_model(None, key, None)


def test_read_only_config_load_does_not_initialize_shared_settings(monkeypatch):
    from sme_gend.services import shared_config_write as config
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda pid: False)
    monkeypatch.setattr(config, 'load_shared_payload', lambda *a: None)
    monkeypatch.setattr(config, 'bind_config_version', lambda cfg, *a: cfg)
    save = Mock()
    monkeypatch.setattr(config, 'save_shared_model', save)
    assert config.load_shared_model(None, 'detaches_config', dict) == {}
    save.assert_not_called()


def test_structure_right_only_updates_layout_during_its_own_sync(monkeypatch):
    from sme_gend.services.shared_config_write import may_write_config
    from sme_gend.services.action_permissions import structure_layout_sync
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',
                        lambda pid: pid == 'structures.codes')
    assert not may_write_config('liste_present_layout')
    with structure_layout_sync():
        assert may_write_config('liste_present_layout')
        assert not may_write_config('detaches_config')
    assert not may_write_config('liste_present_layout')


@pytest.mark.parametrize('permissions,granted', [
    (set(), False), ({'effectifs.liste'}, False),
    ({'structures.codes'}, True), ({'referentiels.fonctions'}, True),
])
def test_alternative_reference_permissions_match_both_entry_screens(monkeypatch, permissions, granted):
    from sme_gend.services.action_permissions import requires_any_permission
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda pid: pid in permissions)
    action = Mock(return_value='saved')
    guarded = requires_any_permission('structures.codes', 'referentiels.fonctions')(action)
    if granted:
        assert guarded() == 'saved'
        action.assert_called_once()
    else:
        with pytest.raises(PermissionError):
            guarded()
        action.assert_not_called()


@pytest.mark.parametrize('module,cls,action', [
    ('structure_effectifs_view', 'StructureEffectifsView', '_on_print'),
    ('settings_view', 'SettingsView', '_on_save'),
])
def test_denied_ui_action_stops_before_accessing_data(monkeypatch, module, cls, action):
    from importlib import import_module
    monkeypatch.setattr('sme_gend.ui.document_job.require_export', lambda parent: False)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a, **kw: False)
    view_class = getattr(import_module('sme_gend.ui.views.' + module), cls)
    getattr(view_class, action)(SimpleNamespace())


def test_user_sessions_and_journal_keep_independent_permissions(monkeypatch):
    from sme_gend.ui.views.users_auth_view import UsersAuthView
    sessions, journal = Mock(), Mock()
    view = SimpleNamespace(_reload_active_sessions=sessions, _reload_activity_log_async=journal,
                           _log_loaded=False)
    UsersAuthView._on_tab_changed(view, 2)
    sessions.assert_not_called()
    journal.assert_not_called()
    UsersAuthView._on_tab_changed(view, 3)
    sessions.assert_called_once()
    UsersAuthView._on_tab_changed(view, 4)
    journal.assert_called_once()
