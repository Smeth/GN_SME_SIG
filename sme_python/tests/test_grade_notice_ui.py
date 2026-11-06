from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QDialog, QMessageBox
from test_edition_sme_editor import _row
from sme_gend.services.grade_save_notice import grade_save_notice


def test_dated_grade_notice_identifies_target_month():
    requested = replace(_row(), grade='ADM', date_grade=datetime(2026,10,1))
    saved = replace(_row(), grade='MDL', date_grade=datetime(2026,8,1))
    notice = grade_save_notice(requested, saved)
    assert '2026-10' in notice and '01/10/2026' in notice and 'ADM' in notice
    assert grade_save_notice(requested, requested) is None


def test_full_dossier_confirms_future_history_without_faking_current_grade(monkeypatch):
    from sme_gend.ui import personnel_dossier_dialog as module
    from sme_gend.services import personnel_form_service
    current = replace(_row(), grade='MDL', date_grade=datetime(2026,8,1))
    requested = replace(current, grade='ADM', date_grade=datetime(2026,10,1))
    dialog = Mock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.result_row.return_value = requested
    monkeypatch.setattr(module, 'PersonnelEditDialog', lambda *a, **kw: dialog)
    monkeypatch.setattr(personnel_form_service, 'save_personnel_form', Mock(return_value=current))
    monkeypatch.setattr(QMessageBox, 'information', Mock())
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    view = SimpleNamespace(_allow_edit=True, _row=current, _service=SimpleNamespace(
        get_by_id=lambda _: current, settings=object()), _ref=object(), _refresh_all=Mock())
    module.PersonnelDossierDialog._on_edit(view)
    assert view._row.grade == 'MDL'
    assert view._liste_generale_changed
    assert '2026-10' in QMessageBox.information.call_args.args[2]
    QMessageBox.critical.assert_not_called()
