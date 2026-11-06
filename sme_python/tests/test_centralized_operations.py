from dataclasses import replace
from types import SimpleNamespace
import pytest
from sme_gend.services.agent_dossier_sync import validate_monthly_history_edit
from sme_gend.services.backup_restore_service import RestoreScope


@pytest.mark.parametrize('field', ['diplome', 'date_obtention'])
def test_history_cannot_be_silently_removed_through_monthly_list(field):
    before = SimpleNamespace(grade=None, diplome='Diplôme conservé', date_obtention='2020-01-01')
    after = SimpleNamespace(**vars(before))
    setattr(after, field, None)
    with pytest.raises(ValueError, match='dossier agent'):
        validate_monthly_history_edit(before, after)


def test_list_can_add_a_diploma_without_removing_history():
    validate_monthly_history_edit(SimpleNamespace(grade=None, diplome=None, date_obtention=None),
                                 SimpleNamespace(grade=None, diplome='Nouveau diplôme', date_obtention=None))


def test_restore_preserves_current_audit_by_default():
    assert RestoreScope.full().preserve_audit_journal


def test_maintenance_is_independent_of_month_and_server_alias():
    from sme_gend.config.settings import Settings
    from sme_gend.services.database_maintenance import lock_name, _request_lock
    from pathlib import Path
    settings = Settings(database_path=Path('unused'))
    assert lock_name(settings) == lock_name(replace(settings, mysql_host='localhost', mysql_view_mois='2026-09'))
    assert len(lock_name(settings)) <= 64 and len(_request_lock(settings)) <= 64


def test_consultation_captures_only_bounded_metadata(monkeypatch):
    from sme_gend.services.consultation_audit import capture_consultation
    monkeypatch.setattr('sme_gend.services.auth_context.capture_audit_identity', lambda: 'ORIGINAL')
    event = capture_consultation(SimpleNamespace(mysql_view_mois='2026-09'),
        action='section_consulted', section='x'*300)
    assert event.actor == 'ORIGINAL' and event.month == '2026-09' and len(event.section) == 160
    with pytest.raises(ValueError):
        capture_consultation(SimpleNamespace(mysql_view_mois='2026-09'), action='keystroke', section='X')


def test_core_dossier_history_log_keeps_full_before_and_after(monkeypatch):
    from sme_gend.services.dossier_write import history_audit_context
    from sme_gend.services.agent_dossier_service import AgentDossierService
    captured = []
    monkeypatch.setattr('sme_gend.services.sensitive_audit_log.log_dossier_history_action', lambda **kw: captured.append(kw))
    snapshot = {'before':{'id':12,'libelle':'Ancien diplôme','date_obtention':'2001-02-03'}, 'after':None}
    token = history_audit_context.set(snapshot)
    try:
        AgentDossierService.__new__(AgentDossierService)._log_history('dossier_diplome_delete', object(), 'Retrait', diplome_id=12)
    finally:
        history_audit_context.reset(token)
    assert captured[0]['extra']['before'] == snapshot['before']
    assert captured[0]['extra']['after'] is None


def test_page_consultations_are_not_duplicated_by_refresh_or_recorded_when_denied(monkeypatch):
    from sme_gend.ui import shell_window
    captured = []
    monkeypatch.setattr('sme_gend.services.consultation_audit.queue_consultation', lambda *a, **kw: captured.append(kw))
    monkeypatch.setattr(shell_window, 'session_can_access_page', lambda index: index != 1)
    shell = SimpleNamespace(_nav_labels=['Personnel','Interdit'], _settings=object())
    shell_window.ShellWindow._trace_visible_section(shell, 0)
    shell_window.ShellWindow._trace_visible_section(shell, 0)
    shell_window.ShellWindow._trace_visible_section(shell, 1)
    assert len(captured) == 1 and captured[0]['section'] == 'Personnel'


def test_consultation_capture_never_loads_shared_config_on_gui_thread(monkeypatch):
    from sme_gend.services.consultation_audit import capture_consultation
    monkeypatch.setattr('sme_gend.services.auth_context.capture_audit_identity', lambda: '')
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant', lambda: pytest.fail('GUI performed SQL context lookup'))
    monkeypatch.setattr('sme_gend.config.sme_context.peek_editeur_defaut', lambda: pytest.fail('GUI loaded shared audit identity'))
    event = capture_consultation(SimpleNamespace(mysql_view_mois=None), action='section_consulted', section='Personnel')
    assert event.actor.startswith('Windows:') and event.month is None


def test_replacing_shared_audit_requires_a_separate_confirmation(monkeypatch):
    from sme_gend.ui.views.backup_restore_view import BackupRestoreView
    calls = []
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_destructive_with_ack', lambda *a, **kw: calls.append(kw) or False)
    assert BackupRestoreView._confirm_audit_replacement(object(), True)
    assert not calls
    assert not BackupRestoreView._confirm_audit_replacement(object(), False)
    assert len(calls) == 1 and 'tous les postes' in calls[0]['ack_label']
