"""Task result delivery, real completion and owner lifetime (Qt offscreen)."""
from threading import Event
import time
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import QCoreApplication,QEvent,QThread,QTimer
from PySide6.QtWidgets import QApplication,QProgressDialog,QWidget
from sme_gend.ui import bg_task
from sme_gend.ui.operation_progress_dialog import OperationProgressDialog


@pytest.fixture
def app(monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(bg_task.QMessageBox,'critical',MagicMock())
    yield app
    app.processEvents()


@pytest.mark.parametrize('determinate',[False,True])
def test_premature_progress_close_cannot_report_success_before_the_worker_finishes(app,monkeypatch,determinate):
    workers=[];original=bg_task._ProgressWorker if determinate else bg_task._OperationWorker
    class Tracked(original):
        def __init__(self,*a,**kw):super().__init__(*a,**kw);workers.append(self)
    monkeypatch.setattr(bg_task,'_ProgressWorker' if determinate else '_OperationWorker',Tracked)
    parent=QWidget();finished=Event()
    def work(*args):
        if args:args[0]('Synthetic final step before actual work')
        time.sleep(.08);finished.set();return 42
    def close_progress():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget,(QProgressDialog,OperationProgressDialog)):widget.close()
    QTimer.singleShot(10,close_progress)
    result=bg_task.run_with_determinate_progress(parent,'test',1,work) if determinate else bg_task.run_with_progress(parent,'test','test',work)
    completed_at_return=finished.is_set()
    # Old code can return while its worker is still running. Keep that worker
    # alive and join before asserting, so this reproduction never crashes Qt.
    assert finished.wait(2)
    for worker in workers:
        try:worker.wait(2000)
        except RuntimeError:pass
    app.processEvents();parent.deleteLater()
    assert completed_at_return and result==42
    assert len(workers)==1



def pump_until(app,predicate):
    deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.002)
    assert predicate()


@pytest.mark.parametrize('failed',[False,True])
def test_async_delivery_occurs_on_gui_thread_and_releases_worker(app,failed):
    parent=QWidget();received=[];backend=[]
    def work():
        backend.append(QThread.currentThread())
        if failed:raise ValueError('synthetic')
        return 42
    def receive(value):received.append((value,QThread.currentThread()))
    worker=bg_task.run_in_background(parent,work,receive,on_error=receive)
    pump_until(app,lambda:bool(received))
    assert received[0][1]==app.thread() and backend[0]!=app.thread()
    assert isinstance(received[0][0],ValueError) if failed else received[0][0]==42
    assert worker not in bg_task._active_workers
    parent.deleteLater()


def test_destroying_owner_does_not_destroy_running_thread_or_deliver_stale_ui(app):
    parent=QWidget();entered=Event();release=Event();done=Event();received=[]
    def work():entered.set();release.wait(2);done.set();return 42
    worker=bg_task.run_in_background(parent,work,received.append,on_error=received.append)
    assert entered.wait(2)
    parent.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert worker.parent() is None and worker.isRunning()
    release.set();pump_until(app,lambda:worker not in bg_task._active_workers)
    assert done.is_set() and received==[]


def test_success_callback_failure_is_reported_and_worker_released(app):
    errors=[]
    def broken(value):raise ValueError('synthetic callback')
    worker=bg_task.run_in_background(None,lambda:42,broken,on_error=errors.append)
    pump_until(app,lambda:bool(errors))
    assert isinstance(errors[0],ValueError) and worker not in bg_task._active_workers


def test_shutdown_joins_inflight_work_without_termination(app):
    done=Event();received=[]
    def work():time.sleep(.03);done.set();return 42
    worker=bg_task.run_in_background(None,work,received.append,on_error=received.append)
    try:
        bg_task._drain_workers()
        assert done.is_set() and not worker.isRunning()
        pump_until(app,lambda:worker not in bg_task._active_workers)
        assert received==[]
    finally:bg_task._quitting=False



def test_hiding_career_alerts_never_deletes_or_waits_for_a_running_query(app):
    from types import SimpleNamespace
    from sme_gend.ui.views.personnel_list_view import PersonnelListView
    worker=MagicMock();worker.isRunning.return_value=True;worker.wait.return_value=False
    view=SimpleNamespace(_alerts_worker=worker)
    PersonnelListView._stop_career_alerts_worker(view,wait_ms=0)
    worker.requestInterruption.assert_called_once()
    worker.wait.assert_not_called();worker.deleteLater.assert_not_called()
    assert view._alerts_worker is None


@pytest.mark.parametrize('kind',['preview','career'])
@pytest.mark.parametrize('destroy_owner',[False,True])
def test_specialized_read_workers_outlive_their_view_safely(app,monkeypatch,kind,destroy_owner):
    from types import SimpleNamespace
    entered=Event();release=Event();done=Event();received=[];parent=QWidget()
    def slow(*args,**kwargs):entered.set();release.wait(2);done.set();return 'synthetic'
    if kind=='preview':
        from sme_gend.ui.views import liste_present_view
        monkeypatch.setattr(liste_present_view,'capture_outline',slow)
        monkeypatch.setattr(liste_present_view,'render_outline',lambda snapshot,search_filter=None:snapshot)
        worker=liste_present_view._ListePresentPreviewWorker(SimpleNamespace(),None,1,parent)
        signal=worker.succeeded
    else:
        from sme_gend.ui.views.personnel_list_view import _CareerAlertsScanWorker
        monkeypatch.setattr('sme_gend.services.career_alerts_cache.scan_and_cache_career_alerts',slow)
        worker=_CareerAlertsScanWorker(None,parent);signal=worker.finished_scan
    bg_task.start_managed_thread(worker,parent,signals=((signal,lambda value:received.append((value,QThread.currentThread()))),))
    assert entered.wait(2)
    if destroy_owner:
        parent.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert worker.isRunning() and worker.parent() is None
    release.set();pump_until(app,lambda:worker not in bg_task._active_workers)
    assert done.is_set()
    if destroy_owner:assert received==[]
    else:
        assert received==[('synthetic',app.thread())]
        parent.deleteLater()


def test_old_preview_result_cannot_reset_the_current_request_state(app):
    from types import SimpleNamespace
    from sme_gend.ui.views.liste_present_view import ListePresentView
    view=SimpleNamespace(_refresh_busy=True,_refresh_token=2,_refresh_progress=MagicMock(),_btn_refresh=MagicMock())
    ListePresentView._finish_refresh(view,'stale',None,1)
    assert view._refresh_busy is True
    view._refresh_progress.setVisible.assert_not_called();view._btn_refresh.setEnabled.assert_not_called()



def test_career_scan_failure_is_explicit_and_does_not_report_empty_success(app,monkeypatch,caplog):
    from sme_gend.ui.views.personnel_list_view import _CareerAlertsScanWorker,PersonnelListView
    from types import SimpleNamespace
    def failed(*args,**kwargs):raise OSError('SYNTHETIC PRIVATE DETAIL')
    monkeypatch.setattr('sme_gend.services.career_alerts_cache.scan_and_cache_career_alerts',failed)
    success=[];errors=[];worker=_CareerAlertsScanWorker(None)
    bg_task.start_managed_thread(worker,None,signals=((worker.finished_scan,success.append),(worker.failed,errors.append)))
    pump_until(app,lambda:worker not in bg_task._active_workers)
    assert success==[] and errors==[None] and 'SYNTHETIC PRIVATE DETAIL' not in caplog.text
    view=SimpleNamespace(_alerts_worker=object(),_alerts_banner=MagicMock(),_apply_alerts_banner_style=MagicMock())
    PersonnelListView._on_career_alerts_failed(view,None)
    assert view._alerts_worker is None
    view._alerts_banner.setVisible.assert_called_once_with(True)
