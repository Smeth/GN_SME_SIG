"""Tâches longues hors thread UI : worker + dialogue de progression."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

T = TypeVar("T")


class FunctionWorker(QThread):
    """Exécute une fonction sans arguments dans un thread secondaire."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
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
) -> T | None:
    """
    Exécute fn dans un thread secondaire avec dialogue modal indéterminé.
    Ne pas utiliser pour du code qui touche aux widgets / modèles Qt.

    minimum_duration : ms avant que le dialogue soit affiché (0 = immédiat).
    Retourne le résultat, ou None si erreur (QMessageBox critique affichée).
    """
    dlg = QProgressDialog(parent)
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
        dlg.accept()

    def on_fail(exc: object) -> None:
        err.append(exc)  # type: ignore[arg-type]
        dlg.accept()

    worker = FunctionWorker(fn, parent)
    worker.succeeded.connect(on_ok)
    worker.failed.connect(on_fail)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    dlg.exec()

    if err:
        e = err[0]
        QMessageBox.critical(
            parent,
            title,
            str(e) if isinstance(e, Exception) else repr(e),
        )
        return None
    return result[0] if result else None
