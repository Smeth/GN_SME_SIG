from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QCoreApplication, QEvent
from sme_gend.config.settings import Settings
from sme_gend.services.bulk_diploma import add_bulk_diploma, BulkDiplomaResult


def payload():
    return dict(categorie='MILITAIRE',libelle='BREVET TEST',date_obtention=date(2026,9,20))


def test_permission_denied_before_service_or_database(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda pid:False)
    with pytest.raises(PermissionError):
        add_bulk_diploma(None, [], payload())


@pytest.mark.parametrize('values', [dict(categorie='CIVIL',libelle='',date_obtention=date(2026,9,20)),
    dict(categorie='UNKNOWN',libelle='A',date_obtention=date(2026,9,20)),
    dict(categorie='MILITAIRE',libelle='A',date_obtention=None)])
def test_invalid_payload_is_rejected_before_database(values):
    with pytest.raises(ValueError):
        add_bulk_diploma(None, [], values)


@pytest.mark.parametrize('revoke_after_dialog',[False,True])
@pytest.mark.parametrize('use_checks',[False,True])
def test_ui_menu_targets_checked_agents_and_rechecks_access(monkeypatch,revoke_after_dialog,use_checks):
    from sme_gend.ui.views.edition_sme_view import EditionSmeView
    from test_edition_sme_editor import _row
    from sme_gend.services import bulk_diploma
    app=QApplication.instance() or QApplication([])
    view=EditionSmeView(Settings(database_path=Path('unused'),mysql_view_mois='2026-09'))
    view._dirty=False
    view._apply_editing_lock=lambda:None
    view._start_career_alerts_scan=lambda:None
    view._require_editable=Mock(side_effect=[True,not revoke_after_dialog])
    view._model.load([_row('1'),_row('2')],None,[],[],[],[])
    view._model._checked_ids={'2'} if use_checks else set()
    view._table.selectRow(0)
    view._editing_locked=False
    view._update_action_buttons()
    action=view._bulk_menu_actions[-1]
    assert 'diplôme' in action.text() and action.isEnabled()
    dialog=NS(exec=lambda:QDialog.DialogCode.Accepted,values=payload,setWindowTitle=Mock())
    monkeypatch.setattr('sme_gend.ui.diplome_edit_dialog.DiplomeEditDialog',lambda *a:dialog)
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_yes_no',lambda *a,**kw:True)
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_determinate_progress',lambda parent,title,total,fn:fn(Mock()))
    monkeypatch.setattr('sme_gend.ui.views.edition_sme_view.QMessageBox.information',Mock())
    add=Mock(return_value=BulkDiplomaResult(added=1))
    monkeypatch.setattr(bulk_diploma,'add_bulk_diploma',add)
    view.load_data=Mock()
    action.trigger()
    if revoke_after_dialog:
        add.assert_not_called()
        view.load_data.assert_not_called()
    else:
        assert [r.id for r in add.call_args.args[1]]==(['2'] if use_checks else ['1'])
        assert add.call_args.args[2]['libelle']=='BREVET TEST'
        view.load_data.assert_called_once_with(preserve_ui=True)
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('stop_at',['readonly','empty','dialog','confirmation'])
def test_no_write_when_action_is_cancelled_or_unavailable(monkeypatch,stop_at):
    from sme_gend.ui.views.edition_sme_view import EditionSmeView
    from test_edition_sme_editor import _row
    from sme_gend.services import bulk_diploma
    add=Mock()
    monkeypatch.setattr(bulk_diploma,'add_bulk_diploma',add)
    monkeypatch.setattr('sme_gend.ui.views.edition_sme_view.QMessageBox.information',Mock())
    dialog=NS(exec=lambda:QDialog.DialogCode.Rejected if stop_at=='dialog' else QDialog.DialogCode.Accepted,
              values=payload,setWindowTitle=Mock())
    monkeypatch.setattr('sme_gend.ui.diplome_edit_dialog.DiplomeEditDialog',lambda *a:dialog)
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_yes_no',lambda *a,**kw:stop_at!='confirmation')
    view=NS(_apply_editing_lock=Mock(),_require_editable=lambda:stop_at!='readonly',
            _get_active_src_rows=lambda:[] if stop_at=='empty' else [0],_model=NS(_rows=[_row()]))
    EditionSmeView._action_bulk_diploma(view)
    add.assert_not_called()
