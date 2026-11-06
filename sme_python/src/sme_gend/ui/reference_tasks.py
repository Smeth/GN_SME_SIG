"""Catalogue writes outside Qt's GUI thread, with explicit failure feedback."""
from PySide6.QtWidgets import QMessageBox
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict


def execute_reference_change(parent, fn, *, draft=True):
    def work():
        try:
            fn()
            return True, None
        except Exception as exc:
            return False, exc
    outcome = run_with_progress(parent, 'Référentiel', 'Enregistrement en cours…', work)
    if outcome is None:
        return False
    ok, error = outcome
    if not ok:
        if isinstance(error, ReferenceConflictError):
            show_reference_conflict(parent, error, draft=draft)
        else:
            from sme_gend.services.mysql_health import format_mysql_error_for_user
            QMessageBox.warning(parent, 'Modification non confirmée',
                str(error) if isinstance(error, (ValueError, PermissionError)) else format_mysql_error_for_user(error))
    return ok
