"""Type d'unité appliqué à plusieurs structures."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from sme_gend.config.structure_famille import STRUCTURE_FAMILLE_CHOICES


class StructureBulkFamilleDialog(QDialog):
    def __init__(self, parent, *, count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Type d'unité — action groupée")
        self.setMinimumWidth(420)
        intro = QLabel(
            f"Appliquer un <b>type d'unité</b> aux <b>{count}</b> structure(s) cochée(s)."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        self._famille = QComboBox()
        for code, label in STRUCTURE_FAMILLE_CHOICES:
            self._famille.addItem(label, code)
        form = QFormLayout()
        form.addRow("Type d'unité", self._famille)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(bb)

    def famille_code(self) -> str | None:
        from sme_gend.config.structure_famille import normalize_structure_famille

        return normalize_structure_famille(str(self._famille.currentData() or ""))
