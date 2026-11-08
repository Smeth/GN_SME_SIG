"""Real job completion, nested service phases and delayed short-read dialogs."""
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sme_gend.services.operation_progress import operation_progress, progress_reporting
from sme_gend.ui import bg_task
from sme_gend.ui.operation_progress_dialog import OperationProgressDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_expanded_service_steps_stay_visible_through_job_finalization():
    snapshots=[]
    with progress_reporting(snapshots.append), operation_progress(
            (('prepare','Préparation'),('job','Export'),('finish','Finalisation')),
            include_substeps=True) as outer:
        outer.start('prepare');outer.finish('prepare');outer.start('job')
        with operation_progress((('read','Lecture des données'),('write','Écriture des feuilles'))) as service:
            service.start('read');service.finish('read');service.start('write')
            service.update('write',current=50,total=100)
            event=snapshots[-1]
            assert [step.label for step in event.steps]==['Préparation','Lecture des données','Écriture des feuilles','Finalisation']
            assert event.active_key=='job/write' and event.percent==58
            assert event.current==50 and event.total==100
            service.finish('write')
        outer.finish('job');outer.start('finish')
        assert len(snapshots[-1].steps)==4 and snapshots[-1].active_key=='finish'
        outer.finish('finish')
    assert all(step.state=='done' for step in snapshots[-1].steps)
    assert snapshots[-1].percent==100
    assert [event.percent for event in snapshots]==sorted(event.percent for event in snapshots)


def test_generic_runner_uses_measured_service_phases_and_real_completion(app,monkeypatch):
    events=[];completion=[];observed=[]
    apply=OperationProgressDialog.apply_snapshot
    success=OperationProgressDialog.success
    def record(dialog,event):events.append(event);apply(dialog,event)
    def done(dialog):
        assert completion==[True]
        success(dialog)
    monkeypatch.setattr(OperationProgressDialog,'apply_snapshot',record)
    monkeypatch.setattr(OperationProgressDialog,'success',done)
    def work():
        with operation_progress((('read','Lecture'),('write','Écriture'))) as service:
            service.start('read');service.finish('read');service.start('write')
            service.update('write',current=2,total=4)
            time.sleep(.05)
            service.finish('write')
        completion.append(True)
        return 42
    def observe():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog,OperationProgressDialog) and dialog.isVisible():
                observed.append(dialog._bar.value());dialog.close()
    timer=QTimer();timer.timeout.connect(observe);timer.start(5)
    try:assert bg_task.run_with_progress(None,'Export','Création du document',work)==42
    finally:timer.stop()
    assert observed and max(observed)<100
    assert any(event.current==2 and event.total==4 and any(step.label=='Écriture' for step in event.steps) for event in events)


@pytest.mark.parametrize('value',[False,(False,ValueError('SYNTHETIC FAILURE'))])
def test_error_envelopes_are_preserved_without_flashing_success(app,monkeypatch,value):
    successes=[]
    monkeypatch.setattr(OperationProgressDialog,'success',lambda dialog:successes.append(True))
    assert bg_task.run_with_progress(None,'Référentiel','Enregistrement',lambda:value) is value
    assert not successes


def test_silent_fast_reads_do_not_flash_a_dialog(app,monkeypatch):
    shown=[]
    original=OperationProgressDialog.showEvent
    def show(dialog,event):shown.append(True);return original(dialog,event)
    monkeypatch.setattr(OperationProgressDialog,'showEvent',show)
    assert bg_task.run_with_progress(None,'Lecture','Lecture catalogue',lambda:42,silent=True)==42
    assert not shown


def test_a_slow_delayed_job_shows_real_progress(app,monkeypatch):
    shown=[]
    original=OperationProgressDialog.showEvent
    def show(dialog,event):shown.append(dialog._bar.value());return original(dialog,event)
    monkeypatch.setattr(OperationProgressDialog,'showEvent',show)
    def work():time.sleep(.07);return 42
    assert bg_task.run_with_progress(None,'Lecture','Lecture catalogue',work,minimum_duration=10)==42
    assert shown and all(0<=value<100 for value in shown)


def test_a_fast_silent_failure_still_shows_its_last_stage_until_acknowledged(app):
    errors=[]
    def work():raise ValueError('SYNTHETIC FAILURE')
    def acknowledge():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog,OperationProgressDialog) and dialog._failed and dialog.isVisible():
                errors.append((dialog._bar.value(),dialog._detail.text()))
                dialog._close.click()
    timer=QTimer();timer.timeout.connect(acknowledge);timer.start(5)
    try:assert bg_task.run_with_progress(None,'Lecture','Lecture catalogue',work,silent=True) is None
    finally:timer.stop()
    assert errors and errors[0][0]<100 and 'SYNTHETIC FAILURE' in errors[0][1]


def test_legacy_runner_prefers_the_complete_service_plan(app,monkeypatch):
    plans=[];original=OperationProgressDialog.apply_snapshot
    def record(dialog,event):plans.append(tuple(step.label for step in event.steps));original(dialog,event)
    monkeypatch.setattr(OperationProgressDialog,'apply_snapshot',record)
    def work(report):
        report('Ancien libellé')
        with operation_progress((('read','Lecture réelle'),('write','Écriture réelle'))) as progress:
            progress.start('read');progress.finish('read');progress.start('write');progress.finish('write')
        report('Ancien dernier libellé')
        return 42
    assert bg_task.run_with_determinate_progress(None,'Import',2,work)==42
    assert plans[-1]==('Lecture réelle','Écriture réelle')


def test_a_deferred_commit_callback_updates_its_original_parent_phase():
    snapshots=[]
    with progress_reporting(snapshots.append),operation_progress(
            (('rows','Écriture'),('commit','Validation')), include_substeps=True) as parent:
        parent.start('rows')
        with operation_progress((('write','Données'),('commit','Attente COMMIT'))) as child:
            child.start('write');child.finish('write');child.start('commit')
        parent.finish('rows');parent.start('commit',total=2,detail='Contrôle final')
        parent.update('commit',current=1,total=2,detail='Contrôle final')
        before=snapshots[-1]
        child.finish('commit',detail='Ancienne sous-opération validée')
        after=snapshots[-1]
        assert after.percent==before.percent==75
        assert after.active_key=='commit' and after.detail=='Contrôle final'
        assert (after.current,after.total)==(1,2)
