"""Boîtes de dialogue de confirmation pour limiter les erreurs de manipulation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget


def confirm_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    default_no: bool = True,
) -> bool:
    """Retourne True si l'utilisateur choisit Oui.

    Les libellés Oui/Non sont fixés explicitement : sans traductions Qt (qtbase_fr.qm),
    QMessageBox afficherait « Yes » / « No ».
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    box.setDefaultButton(
        QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes,
    )
    box.setButtonText(QMessageBox.StandardButton.Yes, "Oui")
    box.setButtonText(QMessageBox.StandardButton.No, "Non")
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_overwrite_file(parent: QWidget | None, path: str | Path) -> bool:
    """Si le fichier existe, demande avant d'écraser. Sinon True."""
    p = Path(path)
    if not p.is_file():
        return True
    return confirm_yes_no(
        parent,
        "Fichier existant",
        f"Ce fichier existe déjà :\n{p}\n\nSouhaitez-vous le remplacer ?",
        default_no=True,
    )
