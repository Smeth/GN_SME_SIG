"""Correction permissions, required reason and unchanged publication copy guidance."""
from datetime import date
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox


def test_correction_permission_is_checked_before_schema_or_data_access(monkeypatch):
    from sme_gend.services import auth_guard, schema_migrations
    from sme_gend.services.activity_return_correction import load_activity_return
    monkeypatch.setattr(auth_guard, 'session_has_admin_role', lambda: False)
    capability = Mock()
    monkeypatch.setattr(schema_migrations, 'require_capability', capability)
    with pytest.raises(PermissionError, match='administrateur autorisé'):
        load_activity_return(None, 1, 'agent_mission', 1)
    capability.assert_not_called()


def test_return_correction_requires_a_date_and_reason(monkeypatch):
    from sme_gend.services.activity_return_correction import ActivityReturnSnapshot
    from sme_gend.ui.activity_return_correction_dialog import ActivityReturnCorrectionDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    snapshot = ActivityReturnSnapshot(1, 'agent_mission', 1, date(2026,9,10), date(2026,9,17),
                                      'Mission', 'TEST', 'NOTE', 'revision')
    dialog = ActivityReturnCorrectionDialog(None, snapshot)
    assert dialog._reference.text() == 'NOTE'
    dialog._validate()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog._reason.setPlainText('Date confirmée par la note')
    dialog._date.set_from_text('16/09/2026')
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values() == (date(2026,9,16), 'Date confirmée par la note', 'NOTE')
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize('admin,permission,allowed', [(True, True, True), (False, True, False), (True, False, False)])
def test_readonly_dossier_exposes_only_explicit_administrative_return_action(monkeypatch, admin, permission, allowed):
    from types import SimpleNamespace
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: admin)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda _: permission)
    names = ['stage_add','stage_edit','stage_del','mission_add','mission_edit','mission_del',
        'desertion_add','desertion_edit','desertion_del','conge','conge_add','conge_edit','conge_del',
        'passation','dip_add','dip_edit','dip_del','pas_add','pas_edit','pas_del','aff_add','aff_edit','aff_del']
    view = SimpleNamespace(_allow_edit=False, _btn_correct_return=Mock(), _lbl_history_admin_hint=Mock(),
        **{'_btn_' + name: Mock() for name in names})
    PersonnelDossierDialog._sync_history_edit_controls(view)
    view._btn_correct_return.setVisible.assert_called_with(allowed)
    view._btn_correct_return.setEnabled.assert_called_with(allowed)
    assert view._allow_edit is False
    for name in names:
        getattr(view, '_btn_' + name).hide.assert_called_once()


def test_operator_cannot_invoke_return_correction_even_directly(monkeypatch):
    from types import SimpleNamespace
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: False)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    view = SimpleNamespace(_allow_edit=False)
    PersonnelDossierDialog._on_correct_return(view)
    warning.assert_called_once()


def test_historical_correction_refreshes_cached_dossier_without_unlocking_fields(monkeypatch):
    from types import SimpleNamespace
    from sme_gend.ui import personnel_dossier_dialog as ui
    from sme_gend.ui import activity_return_correction_dialog as correction
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda _: True)
    service = SimpleNamespace(agent_id_for_row=lambda _: 12)
    monkeypatch.setattr(ui, 'AgentDossierService', lambda _: service)
    monkeypatch.setattr(ui, 'AgentDossierEventsService', lambda _: object())
    monkeypatch.setattr(ui, 'ReferentielService', lambda _: object())
    monkeypatch.setattr('sme_gend.services.agent_dossier_service.AgentDossierService', lambda _: service)
    monkeypatch.setattr(correction, 'run_activity_return_correction', lambda parent, settings, aid: aid == 12)
    old = SimpleNamespace(satellite_join_key=lambda: 'SOURCE42')
    fresh = object()
    context = object()
    reload_row = Mock(return_value=(fresh, context))
    view = SimpleNamespace(_allow_edit=False, _settings=object(), _row=old,
        _readonly_snapshot={'cached': True}, _initial_tables_pending={'cached': True},
        _historical_context=object(), _service=SimpleNamespace(dossier_row_for_legacy_id=reload_row),
        _liste_generale_changed=False, _refresh_all=Mock())
    ui.PersonnelDossierDialog._on_correct_return(view)
    assert view._liste_generale_changed and not view._allow_edit
    assert view._row is fresh and view._historical_context is context
    assert view._readonly_snapshot is None and view._initial_tables_pending is None
    reload_row.assert_called_once_with('SOURCE42')
    view._refresh_all.assert_called_once()
