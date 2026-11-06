"""Motif de sortie obligatoire avant suppression d'une fiche personnel."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.personnel_loss_motifs import PERSONNEL_LOSS_MOTIFS


def pick_personnel_loss_motif(
    parent: QWidget | None,
    *,
    title: str = "Motif de sortie",
) -> str | None:
    """
    Demande le motif de perte (décès, retraite, etc.).

    Retourne le libellé choisi ou ``None`` si annulé / vide.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(400)
    lay = QVBoxLayout(dlg)
    lay.addWidget(
        QLabel(
            "La suppression est une <b>sortie d'effectif</b> : elle enregistre une "
            "<b>perte</b> dans Gains & pertes et retire la fiche du mois.\n\n"
            "La fiche peut être <b>restaurée</b> depuis le menu "
            "« Fiches retirées de la liste » en cas d'erreur (même identifiant carrière). "
            "La sortie retire l'agent du mois SME courant sans effacer l'historique."
        )
    )
    lay.addWidget(QLabel("Motif de perte * :"))
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.addItem("")
    for opt in PERSONNEL_LOSS_MOTIFS:
        combo.addItem(opt)
    lay.addWidget(combo)
    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
    )
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Continuer")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    motif = combo.currentText().strip()
    if not motif:
        return None
    return motif
