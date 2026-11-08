"""Measured phases for the few jobs that must read Qt models on the GUI thread."""
from contextlib import contextmanager

from PySide6.QtWidgets import QApplication

from sme_gend.services.operation_progress import operation_progress, progress_reporting
from sme_gend.ui.operation_progress_dialog import OperationProgressDialog


@contextmanager
def gui_operation_progress(title, text, steps):
    dialog = OperationProgressDialog(title, text)
    def display(snapshot):
        dialog.apply_snapshot(snapshot)
        QApplication.processEvents()
    try:
        dialog.show()
        with progress_reporting(display), operation_progress(steps) as progress:
            yield progress
    except BaseException:
        # The caller presents its existing error/result message. Preserve the
        # last percentage; an interrupted GUI export must never flash 100%.
        dialog.unconfirmed()
        raise
    else:
        dialog.success()
    finally:
        dialog.accept()
        dialog.deleteLater()
