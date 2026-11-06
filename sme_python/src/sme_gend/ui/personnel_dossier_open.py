"""Ouverture robuste de la fiche dossier personnel (liste générale, édition SME, recherche…)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget


class DossierOpenResult(NamedTuple):
    code: QDialog.DialogCode
    liste_generale_changed: bool

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import HistoricalDossierContext
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog

_log = logging.getLogger("sme_gend")


def dialog_host(widget: QWidget | None) -> QWidget | None:
    """Fenêtre top-level — évite une fiche « miniature » enfant d'un panneau empilé."""
    if widget is None:
        return None
    win = widget.window()
    return win if win is not None else widget


def resolve_dossier_row(
    service: PersonnelService,
    row: ListeGeneraleRow,
    *,
    refresh_from_db: bool = True,
) -> ListeGeneraleRow:
    """Relit la ligne en base si possible ; sinon conserve la ligne affichée."""
    if not refresh_from_db:
        return row
    try:
        fresh = service.get_by_id(row.id)
    except Exception:
        _log.exception("get_by_id(%s) en échec — fiche ouverte depuis la liste", row.id)
        raise RuntimeError("Identité non actualisée : vérifiez la connexion puis réessayez.")
    if fresh is not None:
        return fresh
    raise LookupError("Cette situation n’est plus disponible dans le mois consulté. Actualisez la liste avant d’ouvrir le dossier.")


def open_personnel_dossier_dialog(
    host_widget: QWidget | None,
    row: ListeGeneraleRow,
    referentiel: ReferentielService,
    service: PersonnelService,
    *,
    allow_edit: bool = True,
    historical_context: HistoricalDossierContext | None = None,
    refresh_from_db: bool = True,
    readonly_snapshot: dict | None = None,
    permission_id: str = 'effectifs.liste',
) -> DossierOpenResult:
    """
    Affiche la fiche dossier en modal application.

    Ne pas appeler depuis un handler souris direct : différer avec QTimer.singleShot.
    """
    parent = dialog_host(host_widget)
    name = row.noms or row.id or "—"
    initial_snapshot = None
    audit_recorded = False
    if readonly_snapshot is None:
        from sme_gend.ui.bg_task import run_with_progress
        from sme_gend.services.dossier_read_snapshot import load_dossier_read_snapshot
        from sme_gend.services.data_sync import read_data_revision
        from sme_gend.services.activity_audit_log import log_dossier_opened
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
        settings = service.settings

        def prepare():
            worker_service = PersonnelService(settings)
            for _ in range(3):
                before = read_data_revision(settings)
                current = resolve_dossier_row(worker_service, row, refresh_from_db=refresh_from_db)
                snapshot = load_dossier_read_snapshot(settings, current, include_alerts=False)
                editable = historical_context is None and not evaluate_editing_state_for_settings(settings).locked
                if before == read_data_revision(settings):
                    log_dossier_opened(settings, current)
                    return current, snapshot, editable
            raise RuntimeError('Le dossier change pendant le chargement ; actualisez puis réessayez.')

        prepared = run_with_progress(parent, 'Fiche dossier', 'Chargement du dossier…', prepare, silent=True)
        if prepared is None:
            return DossierOpenResult(QDialog.DialogCode.Rejected, False)
        from sme_gend.services.auth_guard import session_has_permission, session_can_modify_personnel
        from shiboken6 import isValid
        if (service.settings != settings or not session_has_permission(permission_id)
                or parent is not None and not isValid(parent)):
            return DossierOpenResult(QDialog.DialogCode.Rejected, False)
        row, initial_snapshot, editable = prepared
        allow_edit = allow_edit and editable and session_can_modify_personnel()
        refresh_from_db = False
        audit_recorded = True
    try:
        row_to_show = resolve_dossier_row(service, row, refresh_from_db=refresh_from_db)
        dlg = PersonnelDossierDialog(
            parent,
            row_to_show,
            referentiel,
            service,
            allow_edit=allow_edit,
            historical_context=historical_context,
            **({"readonly_snapshot": readonly_snapshot} if readonly_snapshot is not None else {}),
            **({"initial_snapshot": initial_snapshot} if initial_snapshot is not None else {}),
        )
    except Exception as exc:
        _log.exception("Création PersonnelDossierDialog pour %s", name)
        if host_widget is not None:
            QMessageBox.critical(
                host_widget,
                "Fiche dossier",
                f"Impossible d'ouvrir la fiche de « {name} » :\n{exc}",
            )
        return DossierOpenResult(QDialog.DialogCode.Rejected, False)

    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setWindowFlag(Qt.WindowType.Window, True)
    try:
        from sme_gend.services.activity_audit_log import log_dossier_opened
        if not audit_recorded:
            log_dossier_opened(service.settings, row_to_show)
    except Exception:
        QMessageBox.critical(parent, "Fiche dossier", "Consultation non ouverte : journalisation impossible. Vérifiez la connexion MySQL puis réessayez.")
        dlg.deleteLater()
        return DossierOpenResult(QDialog.DialogCode.Rejected, False)
    dlg.prepare_for_exec()
    code = dlg.exec()
    return DossierOpenResult(code, dlg.liste_generale_changed)
