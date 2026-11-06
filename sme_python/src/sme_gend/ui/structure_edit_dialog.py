"""Création / modification d'une structure (code, désignation, type d'unité)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from sme_gend.config.structure_famille import STRUCTURE_FAMILLE_CHOICES, normalize_structure_famille
from sme_gend.models.structure import StructureRow


class StructureEditDialog(QDialog):
    def __init__(self, parent, row: StructureRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle("Nouvelle structure" if row is None else "Modifier la structure")
        self._code = QLineEdit()
        self._designation = QLineEdit()
        self._ordre = QLineEdit()
        self._famille = QComboBox()
        for code, label in STRUCTURE_FAMILLE_CHOICES:
            self._famille.addItem(label, code)
        self._code.setPlaceholderText("Code unique")
        self._designation.setPlaceholderText("Désignation")
        self._ordre.setPlaceholderText("Ordre de tri (vide = aucun)")
        if row:
            self._code.setText(row.code)
            self._code.setReadOnly(True)
            self._designation.setText(row.designation)
            self._ordre.setText("" if row.ordre_tri is None else str(row.ordre_tri))
            fam = row.famille_unite or ""
            idx = self._famille.findData(fam)
            if idx >= 0:
                self._famille.setCurrentIndex(idx)
        form = QFormLayout()
        form.addRow("Code *", self._code)
        form.addRow("Désignation *", self._designation)
        form.addRow("Type d'unité", self._famille)
        form.addRow("Ordre tri", self._ordre)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def accept(self) -> None:
        try:
            code, designation, *_ = self.values()
            if not code or not designation:
                raise ValueError("Code et désignation sont obligatoires.")
        except ValueError:
            QMessageBox.warning(self, "Structure", "Code et désignation obligatoires ; ordre de tri : entier compris entre -2147483648 et 2147483647, ou vide.")
            self._ordre.setFocus()
            return
        super().accept()

    def values(self) -> tuple[str, str, int | None, str | None]:
        ordre = self._ordre.text().strip()
        ot: int | None = None
        if ordre:
            ot = int(ordre)
            if not -2147483648 <= ot <= 2147483647:
                raise ValueError("Ordre hors plage.")
        fam = normalize_structure_famille(str(self._famille.currentData() or ""))
        return (self._code.text().strip(), self._designation.text().strip(), ot, fam)
