"""Qt-bound exports keep real counters and wait for the final file close."""
import csv
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtGui import QStandardItem,QStandardItemModel
from PySide6.QtWidgets import QApplication

from sme_gend.ui.gui_operation_progress import gui_operation_progress
from sme_gend.ui.operation_progress_dialog import OperationProgressDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_csv_progress_tracks_filtered_qt_rows_and_confirms_after_file_close(app,monkeypatch,tmp_path):
    from sme_gend.ui.views import personnel_list_view as ui
    path=tmp_path/'filtered.csv'
    model=QStandardItemModel()
    model.setHorizontalHeaderLabels(['Nom','Grade','Actions'])
    for name,grade in [('Alice','CNE'),('Benoît','MDL')]:
        model.appendRow([QStandardItem(name),QStandardItem(grade),QStandardItem('')])
    proxy=QSortFilterProxyModel();proxy.setSourceModel(model)
    proxy.setFilterFixedString('Benoît')
    view=SimpleNamespace(_model=model,_proxy=proxy,_show_status=Mock())
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:True)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(path),'CSV'))
    counters=[];confirmed=[]
    apply=OperationProgressDialog.apply_snapshot
    success=OperationProgressDialog.success
    def record(dialog,event):
        if event.current is not None:counters.append((event.current,event.total))
        return apply(dialog,event)
    def done(dialog):
        # The stream's context manager has exited before success is announced.
        with path.open(encoding='utf-8-sig',newline='') as stream:
            confirmed.extend(csv.reader(stream,delimiter=';'))
        return success(dialog)
    monkeypatch.setattr(OperationProgressDialog,'apply_snapshot',record)
    monkeypatch.setattr(OperationProgressDialog,'success',done)
    ui.PersonnelListView._export_csv(view)
    assert confirmed==[['Nom','Grade'],['Benoît','MDL']]
    assert (1,1) in counters
    view._show_status.assert_called_once()


def test_gui_failure_does_not_display_success_and_propagates_the_original_error(app,monkeypatch):
    successes=[];events=[]
    apply=OperationProgressDialog.apply_snapshot
    def record(dialog,event):events.append(event);return apply(dialog,event)
    monkeypatch.setattr(OperationProgressDialog,'apply_snapshot',record)
    monkeypatch.setattr(OperationProgressDialog,'success',lambda dialog:successes.append(True))
    error=OSError('SYNTHETIC FILE FAILURE')
    with pytest.raises(OSError) as caught:
        with gui_operation_progress('Export','Écriture',(('write','Écrire'),('close','Fermer'))) as progress:
            progress.start('write',total=100)
            progress.update('write',current=25,total=100)
            raise error
    assert caught.value is error and not successes
    assert events[-1].percent<100 and events[-1].active_key=='write'
