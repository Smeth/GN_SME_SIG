from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from sme_gend.ui import dated_effectif_movement_dialog as ui


@pytest.fixture(scope='module')
def app():
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    return app


@pytest.fixture
def setup(monkeypatch):
    values = [SimpleNamespace(id=1, agent_id=45, agent_name='PERSONNE SYNTHÉTIQUE', category='GAIN',
        movement_kind='RÉINTÉGRATION', effective_date=date(2026,11,1), reference_note=None,
        details=None, recorded_at=datetime(2026,11,2), recorded_by='synthetic')]
    monkeypatch.setattr('sme_gend.services.dated_effectif_movement.list_movements', Mock(return_value=values))
    monkeypatch.setattr(ui, 'run_in_background', lambda parent, work, done, **kw: done(work()))
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    return values


def test_reader_can_read_but_cannot_add_correct_or_delete(app, setup, monkeypatch):
    monkeypatch.setattr(ui, 'session_has_permission', lambda p: p=='situation.gain_perte')
    view = ui.DatedEffectifMovementDialog(object())
    view._table.selectRow(0)
    assert view._table.rowCount()==1
    assert not view._new.isEnabled() and not view._edit.isEnabled() and not view._delete.isEnabled()
    view.close()


def test_nonadmin_editor_cannot_correct_existing_movements(app, setup, monkeypatch):
    monkeypatch.setattr(ui, 'session_has_permission', lambda p: True)
    monkeypatch.setattr(ui, 'session_has_admin_role', lambda: False)
    view = ui.DatedEffectifMovementDialog(object())
    view._table.selectRow(0)
    assert view._new.isEnabled() and not view._edit.isEnabled() and not view._delete.isEnabled()
    view.close()


def test_date_kind_required_and_reference_note_optional(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    view = ui.DatedMovementEditDialog(object())
    view._agent_id = 45
    view._date.set_from_text('2026-01-05')
    view._validate()
    assert not view.result()
    view._kind.setText('AJOUT')
    view._validate()
    assert view.result()==ui.QDialog.DialogCode.Accepted
    assert view.values()['reference_note'] is None
    assert view.values()['effective_date']==date(2026,1,5)
    view.close()


def test_table_occupies_main_height(app, setup, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, 'session_has_permission', lambda p: True)
    view = ui.DatedEffectifMovementDialog(object())
    view.show()
    app.processEvents()
    assert view._table.height()>view.height()*.72
    assert view.grab().save(str(tmp_path/'dated_movements.png'))
    view.close()


def test_revocation_after_async_load_clears_personnel(app, setup, monkeypatch):
    allowed = [True]
    monkeypatch.setattr(ui, 'session_has_permission', lambda p: allowed[0])
    callbacks = []
    monkeypatch.setattr(ui, 'run_in_background', lambda parent, work, done, **kw: callbacks.append((work,done)))
    view = ui.DatedEffectifMovementDialog(object())
    work, done = callbacks[0]
    rows = work()
    allowed[0] = False
    done(rows)
    assert view._records==[] and view._table.rowCount()==0
    assert not view._new.isEnabled()
    view.close()


def test_committed_mirror_failure_reports_commit_and_reloads(app, setup, monkeypatch):
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    monkeypatch.setattr(ui, 'session_has_permission', lambda p: True)
    monkeypatch.setattr(ui, 'run_with_progress', lambda parent,title,message,work: work())
    view = ui.DatedEffectifMovementDialog(object())
    view.reload = Mock()
    def committed(): raise CommittedMirrorError('synthetic mirror unavailable')
    view._mutate(committed)
    view.reload.assert_called_once()
    assert QMessageBox.warning.call_args.args[1]=='Mouvement enregistré'
    view.close()
