import time
from unittest.mock import Mock
import pytest
from PySide6.QtCore import QThread,QTimer
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtWidgets import QApplication
from sme_gend.services.operation_progress import ProgressSnapshot,ProgressStep,operation_progress
from sme_gend.ui.operation_progress_dialog import OperationProgressDialog
from sme_gend.ui import bg_task


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI',10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app,'light')
    return app


def snapshot(percent=40):
    return ProgressSnapshot((ProgressStep('backup','Sauvegarde de sécurité','done'),
        ProgressStep('delete','Suppression des données','running'),ProgressStep('commit','Validation MySQL','pending')),
        'delete',percent,'Situations mensuelles : 4 000 / 10 000 lignes',4000,10000)


def test_percentage_checklist_and_counters_are_visible_and_never_finish_early(app,tmp_path):
    dialog=OperationProgressDialog('Réinitialisation','Préparation')
    dialog.apply_snapshot(snapshot());dialog.show();app.processEvents()
    assert dialog._bar.value()==40 and dialog._bar.isTextVisible()
    assert dialog._percentage.text()=='40 %' and dialog._percentage.isVisible()
    assert dialog._steps.count()==3 and 'Terminée' in dialog._steps.item(0).text()
    assert 'En cours' in dialog._steps.item(1).text() and 'À venir' in dialog._steps.item(2).text()
    assert '4 000' in dialog._counter.text()
    assert dialog.grab().save(str(tmp_path/'operation_progress.png'))
    dialog.apply_snapshot(snapshot(100))
    assert dialog._bar.value()==99
    dialog.close();app.processEvents()
    assert dialog.isVisible()
    dialog.success();assert dialog._bar.value()==100
    dialog.close();dialog.deleteLater();app.processEvents()


def test_operation_worker_updates_ui_on_gui_thread_and_returns_only_after_finish(app,monkeypatch):
    deliveries=[];observations=[]
    original=OperationProgressDialog.apply_snapshot
    def apply(dialog,event):
        deliveries.append(QThread.currentThread());return original(dialog,event)
    monkeypatch.setattr(OperationProgressDialog,'apply_snapshot',apply)
    def work():
        with operation_progress((('copy','Copie'),('commit','Validation'))) as progress:
            progress.start('copy');progress.finish('copy');progress.start('commit')
            time.sleep(.06);progress.finish('commit');time.sleep(.06)
        return 42
    def observe():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog,OperationProgressDialog):
                observations.append(dialog._bar.value());dialog.close()
    timer=QTimer();timer.timeout.connect(observe);timer.start(5)
    try:assert bg_task.run_with_operation_progress(None,'Test','Préparation',work)==42
    finally:timer.stop()
    assert observations and max(observations)<100
    assert deliveries and all(thread==app.thread() for thread in deliveries)


def test_failure_keeps_the_last_stage_visible_until_user_acknowledges(app):
    errors=[]
    def work():
        with operation_progress((('backup','Sauvegarde'),('delete','Suppression'))) as progress:
            progress.start('backup');progress.finish('backup');progress.start('delete')
            raise RuntimeError('Synthetic failure')
    def close_error():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog,OperationProgressDialog) and dialog._failed:
                errors.append((dialog._bar.value(),dialog._steps.item(1).text(),dialog._detail.text(),dialog._counter.text()))
                dialog._close.click()
    timer=QTimer();timer.timeout.connect(close_error);timer.start(5)
    try:assert bg_task.run_with_operation_progress(None,'Test','Préparation',work) is None
    finally:timer.stop()
    assert errors and errors[0][0]<100 and 'Interrompue' in errors[0][1]
    assert 'Synthetic failure' in errors[0][2]
    assert 'non terminée' in errors[0][3]


@pytest.mark.parametrize('total',[0,-1])
def test_determinate_runner_with_no_declared_steps_still_waits_for_real_work(app,total):
    completed=[]
    def work(report):
        report('Traitement');completed.append(True);return 42
    assert bg_task.run_with_determinate_progress(None,'Test',total,work)==42
    assert completed==[True]
