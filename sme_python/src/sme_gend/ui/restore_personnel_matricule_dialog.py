"""Matricules alternatifs lors d'une restauration (conflit en liste)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.matricule_format import normalize_matricule_solde
from sme_gend.services.personnel_deleted_service import (
    DeletedPersonnelStatus,
    assess_deleted_restore,
)


def _norm_mat_mil(value: str | None) -> str | None:
    if value is None:
        return None
    t = value.strip()
    return t or None


def row_with_restore_matricules(
    row: ListeGeneraleRow,
    matricule_militaire: str | None,
    matricule_solde: str | None,
) -> ListeGeneraleRow:
    return replace(
        row,
        matricule_militaire=_norm_mat_mil(matricule_militaire),
        matricule_solde=normalize_matricule_solde(matricule_solde),
    )


def pick_restore_matricules(
    parent: QWidget | None,
    settings: Settings,
    row: ListeGeneraleRow,
    deleted_career_id: str,
    *,
    conflict_detail: str = "",
) -> ListeGeneraleRow | None:
    """
    Propose de modifier ou vider les matricules avant restauration.

    Retourne une copie de ``row`` avec les matricules saisis, ou ``None`` si annulé.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Matricules pour la restauration")
    dlg.setModal(True)
    dlg.setMinimumWidth(440)
    lay = QVBoxLayout(dlg)
    intro = (
        "Un autre agent en liste utilise déjà ce matricule.\n"
        "Indiquez d'autres valeurs ou laissez vide pour continuer la restauration."
    )
    if conflict_detail.strip():
        intro = f"{conflict_detail.strip()}\n\n{intro}"
    lay.addWidget(QLabel(intro))
    lay.addWidget(QLabel("Matricule militaire :"))
    ed_mil = QLineEdit()
    ed_mil.setPlaceholderText("Laisser vide si inconnu")
    ed_mil.setText((row.matricule_militaire or "").strip())
    lay.addWidget(ed_mil)
    lay.addWidget(QLabel("Matricule solde :"))
    ed_solde = QLineEdit()
    ed_solde.setPlaceholderText("Laisser vide si absent")
    ed_solde.setText((row.matricule_solde or "").strip())
    lay.addWidget(ed_solde)
    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
    )
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Continuer")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    candidate = row_with_restore_matricules(
        row,
        ed_mil.text(),
        ed_solde.text(),
    )
    repo = ListeGeneraleRepository(settings)
    st, detail = assess_deleted_restore(repo, candidate, deleted_career_id)
    if st == DeletedPersonnelStatus.ALREADY_PRESENT:
        QMessageBox.warning(
            parent,
            "Restauration",
            detail or "Cette personne est déjà en liste.",
        )
        return None
    if st == DeletedPersonnelStatus.MATRICULE_CONFLICT:
        QMessageBox.warning(
            parent,
            "Matricules",
            detail or "Ces matricules sont encore utilisés par une autre fiche.",
        )
        return None
    if st == DeletedPersonnelStatus.NO_SNAPSHOT:
        QMessageBox.warning(parent, "Restauration", detail or "Snapshot incomplet.")
        return None
    return candidate
