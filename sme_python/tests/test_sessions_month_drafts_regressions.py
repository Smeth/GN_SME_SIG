"""Regression coverage for sessions, monthly deletion, audit and unsaved drafts."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def layout():
    from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
    return ListeLayoutConfig(sections=[SectionBlock(id='a',order=0,structure_code='A',title_main_override='Original A'),
                                       SectionBlock(id='b',order=1,structure_code='B',title_main_override='Original B')])


def test_advanced_layout_keeps_title_when_section_changes_before_autosave(app):
    from sme_gend.ui.views.layout_editor_view import LayoutEditorView
    cfg=layout(); view=LayoutEditorView(SimpleNamespace(load=lambda:cfg))
    view._title_main.setText('UNSAVED TITLE')
    view._list.setCurrentRow(1)
    assert cfg.sections[0].title_main_override == 'UNSAVED TITLE'
    view._list.setCurrentRow(0)
    assert view._title_main.text() == 'UNSAVED TITLE'
    view._autosave_timer.stop(); view.close()


def test_quick_panel_draft_is_protected_from_runtime_reload(app):
    from sme_gend.ui.views.liste_present_view import _QuickEditPanel, ListePresentView
    from sme_gend.ui.shell_window import ShellWindow
    fresh=layout()
    svc=SimpleNamespace(load=lambda:deepcopy(fresh), all_structure_designations=lambda:{},
                        present_counts_by_structure=lambda:{},database_structure_codes=lambda:set())
    view=ListePresentView(SimpleNamespace(), layout_cfg=svc)
    panel=_QuickEditPanel(svc, lambda:None); panel._populate()
    view._panel=panel
    panel._le_titre1.setText('UNSAVED TITLE')
    shell=SimpleNamespace(_stack=SimpleNamespace(count=lambda:1), _page_widget=lambda i:view)
    assert ShellWindow._has_unsaved_changes(shell) is True
    view.apply_runtime_after_config_change()
    assert panel._cfg.sections[0].title_main_override == 'UNSAVED TITLE'
    panel._timer.stop(); view._search_timer.stop(); panel.close(); view.close()


def test_session_table_readiness_is_checked_for_each_database(monkeypatch):
    from sme_gend.services import auth_session_registry as registry
    checked=[]
    monkeypatch.setattr(registry,'ensure_auth_sessions_table',lambda settings:checked.append(settings))
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',
                        lambda settings:SimpleNamespace(table_exists=lambda name:settings=='A'))
    assert registry._table_ready('A') is True
    assert registry._table_ready('B') is False
    assert checked == ['B']


def test_missing_registry_refuses_registration_with_single_session_policy(monkeypatch):
    from sme_gend.services import auth_session_registry as registry
    from sme_gend.config.auth_config import AuthConfig
    monkeypatch.setattr(registry,'_table_ready',lambda settings:False)
    token,error=registry.try_register_session(object(),AuthConfig(concurrent_sessions_mode='single'),
        username='synthetic',session_rev=0,client_instance_id='synthetic',machine_label='synthetic',
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    assert token is None and error


def test_remote_session_revision_revokes_when_concurrent_policy_is_off():
    from sme_gend.services.auth_service import AuthService, AuthSession
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    auth=AuthService.__new__(AuthService)
    user=AuthUser('SYNTHETIC','','',role='operateur',session_rev=2)
    auth._config=AuthConfig(enabled=True,users=[user],concurrent_sessions_mode='off')
    auth._enforced=True; auth.reload_config=lambda:None
    auth._session=AuthSession('SYNTHETIC','SYNTHETIC','operateur',datetime.now(timezone.utc)+timedelta(hours=1),session_rev=1)
    assert auth.sync_session_from_config() == 'invalid'
    assert auth.current_session() is None


def test_registry_outage_suspends_but_preserves_local_token(monkeypatch):
    from sme_gend.services.auth_service import AuthService, AuthSession
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    auth = AuthService.__new__(AuthService)
    auth._config = AuthConfig(enabled=True, users=[AuthUser('SYNTHETIC', '', '')])
    auth._enforced = True; auth.reload_config = lambda: None; auth._settings = lambda: object()
    auth._session = AuthSession('SYNTHETIC', 'SYNTHETIC', 'operateur', datetime.now(timezone.utc)+timedelta(hours=1), session_token='TOKEN')
    def unavailable(*a, **kw):
        raise RuntimeError('SYNTHETIC OUTAGE')
    monkeypatch.setattr('sme_gend.services.auth_session_registry.heartbeat_session', unavailable)
    with pytest.raises(RuntimeError, match='OUTAGE'):
        auth.sync_session_from_config()
    assert auth.current_session() is None
    assert auth._session.session_token == 'TOKEN'


def test_advanced_draft_is_clean_only_after_confirmed_save(app, monkeypatch):
    from sme_gend.ui.views.layout_editor_view import LayoutEditorView
    from sme_gend.ui.views import layout_editor_view as module
    cfg = layout()
    outcomes = [RuntimeError('SYNTHETIC FAILURE'), None]
    def save(candidate):
        error = outcomes.pop(0)
        if error:
            raise error
    view = LayoutEditorView(SimpleNamespace(load=lambda: cfg, save=save))
    monkeypatch.setattr(module.QMessageBox, 'critical', lambda *a: None)
    view._title_main.setText('DRAFT')
    view._autosave_timer.stop()
    view._do_save()
    assert view.has_unsaved_changes()
    view._do_save()
    assert not view.has_unsaved_changes()
    view.close()


def test_month_metadata_in_import_trace_populates_the_sme_context_column(monkeypatch):
    from sme_gend.services import audit_log
    traces=[]
    monkeypatch.setattr(audit_log,'_append_audit_mysql',lambda **kw:traces.append(kw))
    audit_log.append_audit_event(action='mysql_bundle_import_access',sme_mois='2026-09',actor='SYNTHETIC')
    assert traces[0]['sme_mois_context'] == '2026-09'
    assert traces[0]['payload']['sme_mois']=='2026-09'


def test_present_and_stage_internal_are_distinct():
    from sme_gend.padmin_present import PadminPresenceIndex, padmin_counts_as_present
    index=PadminPresenceIndex({'présent':False,'stageinterne':False})
    assert padmin_counts_as_present('Présent',index=index) is True
    assert padmin_counts_as_present('StageInterne',index=index) is False
    assert padmin_counts_as_present('Non présent',index=PadminPresenceIndex({})) is False


def test_month_empty_audit_failure_prevents_database_commit(tmp_path,monkeypatch):
    from sme_gend.services import empty_sme_month as mod
    calls=[]
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:True)
    monkeypatch.setattr(mod,'_assert_no_dated_movements',lambda *a:None)
    from contextlib import nullcontext
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.schema_metadata_lock',lambda *a:nullcontext())
    monkeypatch.setattr('sme_gend.services.database_maintenance.centralized_maintenance',lambda *a,**kw:nullcontext())
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema',lambda s:None)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready',lambda s:None)
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.invalidate_liste_cache',lambda s:None)
    monkeypatch.setattr('sme_gend.services.sme_month_closure.sme_archives_dir',lambda:tmp_path)
    backup=tmp_path/'safety.zip';backup.write_bytes(b'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.backup_restore_service.create_backup_to_default_dir',lambda *a,**kw:backup)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.inspect_backup',lambda *a:None)
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',lambda *a,**kw:None)
    @contextmanager
    def transaction(s):
        calls.append('begin');yield;calls.append('commit')
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.mysql_import_transaction',transaction)
    monkeypatch.setattr('sme_gend.services.mysql_import_period.ensure_sme_periode',lambda *a:1)
    cur=MagicMock();cur.__enter__.return_value=cur;cur.rowcount=0
    cur.fetchall.return_value=[];cur.fetchone.return_value=(0,)
    conn=MagicMock();conn.__enter__.return_value=conn;conn.cursor.return_value=cur
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.MySQLConnection',lambda s:SimpleNamespace(connect=lambda:conn))
    monkeypatch.setattr(mod,'purge_sme_periode_for_reimport',lambda *a,**kw:calls.append('purge') or {})
    def audit(**kw):calls.append('audit');raise RuntimeError('SYNTHETIC AUDIT FAILURE')
    monkeypatch.setattr('sme_gend.services.audit_log.append_audit_event',audit)
    with pytest.raises(RuntimeError,match='AUDIT FAILURE'):mod.empty_sme_month(object(),'2026-09')
    assert calls == ['begin','purge','audit']
