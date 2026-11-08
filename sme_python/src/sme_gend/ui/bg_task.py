"""Tâches longues hors thread UI : worker + dialogue de progression."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import Qt, QThread, Signal, QObject, Slot, QCoreApplication
import logging
from shiboken6 import isValid
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

T = TypeVar("T")
_active_workers: dict[QThread, "_WorkerDelivery"] = {}
_shutdown_app: QCoreApplication | None = None
_quitting = False
_log = logging.getLogger(__name__)


def _drain_workers() -> None:
    # Never terminate a thread that may be committing a database transaction.
    global _quitting
    _quitting = True
    for worker in tuple(_active_workers):
        if isValid(worker):
            worker.wait()


class _WorkerDelivery(QObject):
    """Explicit GUI-thread receiver, retained independently of a view's lifetime."""
    def __init__(self, worker, owner, success, error, finished=None):
        super().__init__()
        self.worker = worker
        self.success = success
        self.error = error
        self.finished_callback = finished
        self.alive = True
        self.outcome = None
        self.failure = None
        if owner is not None:
            owner.destroyed.connect(self.owner_destroyed)

    @Slot()
    def owner_destroyed(self):
        self.alive = False
        self.success = self.error = self.finished_callback = None

    @Slot(object)
    def succeeded(self, value):
        self.outcome = value

    @Slot(object)
    def failed(self, exc):
        self.failure = exc if isinstance(exc, BaseException) else RuntimeError("Échec du traitement.")

    @Slot()
    def finished(self):
        try:
            if self.alive and not _quitting:
                if self.failure is not None:
                    self.error(self.failure)
                else:
                    try:
                        self.success(self.outcome)
                    except Exception as exc:
                        self.error(exc)
        except Exception:
            # No payloads, connection strings or personal data in the log.
            _log.error("Échec de la notification d'une tâche de fond.")
        finally:
            try:
                if self.alive and self.finished_callback is not None:
                    self.finished_callback()
            except Exception:
                _log.error("Échec de la fermeture d'une tâche de fond.")
            _active_workers.pop(self.worker, None)
            self.worker._fn = None
            self.worker.deleteLater()
            self.success = self.error = self.finished_callback = None
            self.outcome = self.failure = None
            self.deleteLater()


def _retain_and_start(worker, delivery):
    global _shutdown_app, _quitting
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("Une application Qt est nécessaire pour une tâche de fond.")
    if QThread.currentThread() != app.thread():
        raise RuntimeError("Les tâches avec notification Qt doivent être lancées depuis le thread graphique.")
    if app is not _shutdown_app:
        _shutdown_app = app
        _quitting = False
        app.aboutToQuit.connect(_drain_workers)
    if _quitting:
        raise RuntimeError("L'application se ferme ; aucun nouveau traitement ne peut démarrer.")
    worker.setParent(None)
    _active_workers[worker] = delivery
    queued = Qt.ConnectionType.QueuedConnection
    worker.finished.connect(delivery.finished, queued)
    worker.start()


def _start_worker(worker, delivery):
    queued = Qt.ConnectionType.QueuedConnection
    worker.succeeded.connect(delivery.succeeded, queued)
    worker.failed.connect(delivery.failed, queued)
    _retain_and_start(worker, delivery)


class _SignalRelay(QObject):
    def __init__(self, delivery, callback):
        super().__init__(delivery)
        self.delivery = delivery
        self.callback = callback

    @Slot(int)
    @Slot(str)
    @Slot(object)
    def receive(self, value):
        if self.delivery.alive and not _quitting:
            try:
                self.callback(value)
            except Exception as exc:
                self.delivery.error(exc)


def start_managed_thread(worker, owner, *, signals=(), on_finished=None):
    """Retain a specialized read worker; dispatch its signals on the GUI thread.

    Closing a view requests cooperative interruption and suppresses stale UI
    callbacks. A blocked query may outlive the view, without deleting QThread.
    """
    def failed(exc):
        from sme_gend.services.mysql_health import format_mysql_error_for_user
        _log.warning("Notification d'une lecture de fond impossible.")
        QMessageBox.critical(owner, "Erreur", format_mysql_error_for_user(exc))

    delivery = _WorkerDelivery(worker, owner, lambda value: None, failed, on_finished)
    if owner is not None:
        owner.destroyed.connect(worker.requestInterruption)
    # The QObject parent retains each relay until the real finished signal.
    for signal, callback in signals:
        relay = _SignalRelay(delivery, callback)
        signal.connect(relay.receive, Qt.ConnectionType.QueuedConnection)
    _retain_and_start(worker, delivery)
    return worker


class _TaskProgressDialog(QProgressDialog):
    # The close button and Escape cannot turn a still-running write into an
    # apparent successful return. Only actual QThread.finished closes the modal.
    def reject(self):
        pass

    def closeEvent(self, event):  # noqa: N802
        event.ignore()


class _ProgressDelivery(_WorkerDelivery):
    def __init__(self, worker, dialog, success, error):
        super().__init__(worker, dialog, success, error, dialog.accept)
        self.dialog = dialog

    @Slot(int, int, str)
    def step(self, current, total, message):
        if self.alive and not _quitting:
            if hasattr(self.dialog, 'legacy_step'):
                self.dialog.legacy_step(current, total, message)
                return
            self.dialog.setMaximum(total)
            self.dialog.setValue(current)
            self.dialog.setLabelText(f"({current}/{total}) {message}")

    @Slot(str)
    def detail(self, message):
        if self.alive and not _quitting:
            if hasattr(self.dialog, 'legacy_detail'):
                self.dialog.legacy_detail(message)
                return
            self.dialog.setLabelText(f"({self.dialog.value()}/{self.dialog.maximum()}) {message}")



class FunctionWorker(QThread):
    """Exécute une fonction sans arguments dans un thread secondaire."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        from sme_gend.services.auth_context import capture_audit_identity
        self._audit_identity = capture_audit_identity()

    def run(self) -> None:
        try:
            from sme_gend.services.auth_context import audit_identity_scope
            with audit_identity_scope(self._audit_identity):
                self.succeeded.emit(self._fn())
        except BaseException as e:
            self.failed.emit(e)


def run_with_progress(
    parent: QWidget | None,
    title: str,
    text: str,
    fn: Callable[[], T],
    *,
    minimum_duration: int = 0,
    silent: bool = False,
) -> T | None:
    """
    Exécute fn dans un thread secondaire avec dialogue modal indéterminé.
    Ne pas utiliser pour du code qui touche aux widgets / modèles Qt.

    minimum_duration : ms avant que le dialogue soit affiché (0 = immédiat).
    silent : n'affiche la boîte de progression qu'après ~400 ms si le chargement continue.
    Retourne le résultat, ou None si erreur (QMessageBox critique affichée).
    """
    if silent and minimum_duration <= 0:
        minimum_duration = 400
    # The owner can be destroyed during application shutdown. Keep the modal
    # alive until its detached worker really finishes.
    dlg = _TaskProgressDialog()
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setWindowTitle(title)
    dlg.setLabelText(text)
    dlg.setRange(0, 0)
    dlg.setCancelButton(None)
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(minimum_duration)
    dlg.setMinimumWidth(340)

    result: list[T] = []
    err: list[BaseException] = []

    def on_ok(value: object) -> None:
        result.append(value)  # type: ignore[arg-type]

    def on_fail(exc: object) -> None:
        err.append(exc)  # type: ignore[arg-type]

    worker = FunctionWorker(fn, parent)
    delivery = _WorkerDelivery(worker, dlg, on_ok, on_fail, dlg.accept)
    _start_worker(worker, delivery)
    dlg.exec()
    worker.wait() if isValid(worker) else None
    dlg.deleteLater()

    if err:
        e = err[0]
        from sme_gend.services.mysql_health import format_mysql_error_for_user

        detail = format_mysql_error_for_user(e)
        QMessageBox.critical(
            parent if parent is None or isValid(parent) else None,
            title,
            detail,
        )
        return None
    return result[0] if result else None


class _ProgressWorker(QThread):
    """Comme FunctionWorker, mais ``fn`` reçoit un callable de compte-rendu d'étape."""

    succeeded = Signal(object)
    failed = Signal(object)
    step = Signal(int, int, str)
    detail = Signal(str)

    def __init__(
        self,
        fn: Callable[[Callable[[str], None]], Any],
        total_steps: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        from sme_gend.services.auth_context import capture_audit_identity
        self._audit_identity = capture_audit_identity()
        self._total = total_steps
        self._current = 0

    def _report(self, message: str) -> None:
        self._current = min(self._current + 1, self._total)
        self.step.emit(self._current, self._total, message)

    def _report_detail(self, message: str) -> None:
        self.detail.emit(message)

    def run(self) -> None:
        try:
            from sme_gend.services.auth_context import audit_identity_scope
            with audit_identity_scope(self._audit_identity):
                self.succeeded.emit(self._fn(_ProgressReporter(self._report, self._report_detail)))
        except BaseException as e:
            self.failed.emit(e)


class _ProgressReporter:
    def __init__(
        self,
        report_step: Callable[[str], None],
        report_detail: Callable[[str], None],
    ) -> None:
        self._report_step = report_step
        self.detail = report_detail

    def __call__(self, message: str) -> None:
        self._report_step(message)


def run_with_determinate_progress(
    parent: QWidget | None,
    title: str,
    total_steps: int,
    fn: Callable[[Callable[[str], None]], T],
) -> T | None:
    """
    Comme ``run_with_progress``, mais avec une vraie barre 0→100 % au lieu d'un
    spinner indéterminé : ``fn`` reçoit un callable ``report(message)`` à appeler
    à chaque étape franchie (jusqu'à ``total_steps`` fois). La barre et le
    libellé se mettent à jour à chaque appel.
    """
    total_steps = max(1, total_steps)
    # The owner can be destroyed during application shutdown. Keep the modal
    # alive until its detached worker really finishes.
    from sme_gend.ui.operation_progress_dialog import OperationProgressDialog
    dlg = OperationProgressDialog(title, 'Préparation…')
    dlg.legacy_step(1,max(1,total_steps),'Préparation…')
    # QProgressDialog ferme automatiquement dès que setValue() atteint le maximum
    # (comportement Qt par défaut) — désactivé : la dernière étape signalée ne
    # correspond pas forcément à la fin réelle de fn (code encore à exécuter après),
    # et fermer prématurément faisait revenir un résultat vide même en cas de succès.

    result: list[T] = []
    err: list[BaseException] = []

    def on_ok(value: object) -> None:
        result.append(value)  # type: ignore[arg-type]
        dlg.success()

    def on_fail(exc: object) -> None:
        err.append(exc)  # type: ignore[arg-type]
        from sme_gend.services.mysql_health import format_mysql_error_for_user
        dlg.failure(format_mysql_error_for_user(exc))

    worker = _ProgressWorker(fn, total_steps, parent)
    delivery = _ProgressDelivery(worker, dlg, on_ok, on_fail)
    delivery.finished_callback = dlg.after_worker_finished
    worker.step.connect(delivery.step, Qt.ConnectionType.QueuedConnection)
    worker.detail.connect(delivery.detail, Qt.ConnectionType.QueuedConnection)
    _start_worker(worker, delivery)
    dlg.exec()
    worker.wait() if isValid(worker) else None
    dlg.deleteLater()

    if err:
        return None
    return result[0] if result else None


class _OperationWorker(FunctionWorker):
    progress = Signal(object)

    def run(self):
        from sme_gend.services.operation_progress import progress_reporting
        with progress_reporting(self.progress.emit):
            super().run()


class _OperationDelivery(_WorkerDelivery):
    def __init__(self, worker, dialog, success, error):
        super().__init__(worker, dialog, success, error, dialog.after_worker_finished)
        self.dialog = dialog

    @Slot(object)
    def progress(self, snapshot):
        if self.alive and not _quitting:
            self.dialog.apply_snapshot(snapshot)


def run_with_operation_progress(parent, title, text, fn):
    """Run an instrumented service with real percentages and the complete phase list."""
    from sme_gend.ui.operation_progress_dialog import OperationProgressDialog
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    dlg = OperationProgressDialog(title, text)
    result=[]
    def success(value):
        result.append(value)
        dlg.success()
    def failure(exc):
        dlg.failure(format_mysql_error_for_user(exc))
    worker = _OperationWorker(fn, parent)
    delivery = _OperationDelivery(worker, dlg, success, failure)
    worker.progress.connect(delivery.progress, Qt.ConnectionType.QueuedConnection)
    _start_worker(worker, delivery)
    dlg.exec()
    worker.wait() if isValid(worker) else None
    dlg.deleteLater()
    return result[0] if result else None


def run_in_background(
    parent: QWidget | None,
    fn: Callable[[], T],
    on_success: Callable[[T], None],
    *,
    on_error: Callable[[BaseException], None] | None = None,
) -> FunctionWorker:
    """
    Lance fn dans un thread secondaire ; on_success / on_error sont appelés sur le thread UI.
    Pas de dialogue modal — adapté au chargement différé des vues.
    """

    def _fail(exc: object) -> None:
        if not isinstance(exc, BaseException):
            exc = RuntimeError(repr(exc))
        if on_error is not None:
            on_error(exc)
        else:
            from sme_gend.services.mysql_health import format_mysql_error_for_user

            QMessageBox.critical(
                parent,
                "Erreur",
                format_mysql_error_for_user(exc)
                + "\n\nMenu Fichier → Configuration MySQL et chemins…",
            )

    worker = FunctionWorker(fn, parent)
    delivery = _WorkerDelivery(worker, parent, on_success, _fail)
    _start_worker(worker, delivery)
    return worker
