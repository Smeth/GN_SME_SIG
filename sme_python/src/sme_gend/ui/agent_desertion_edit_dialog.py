"""Formulaire d'ajout/modification d'une désertion."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.agent_evenement import AgentDesertionRow
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class AgentDesertionEditDialog(QDialog):
    def __init__(
        self, parent: QWidget | None, *, desertion: AgentDesertionRow | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier la désertion" if desertion else "Ajouter une désertion")
        self.setMinimumWidth(420)
        self._date = FrenchDateEdit(optional=False)
        if desertion:
            self._date.set_from_text(desertion.date_desertion)
        self._observation = QTextEdit(desertion.observation or "" if desertion else "")
        self._observation.setMaximumHeight(96)
        self._reference = QLineEdit(desertion.reference_dossier or '' if desertion else '')
        self._reference.setMaxLength(256)
        self._reference.setPlaceholderText('Facultatif')
        form = QFormLayout()
        form.addRow("Date de désertion :", self._date)
        form.addRow('Réf/note (facultatif) :', self._reference)
        form.addRow("Observation :", self._observation)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_validated)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept_validated(self) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        value = self._date.text_for_validation().strip()
        if parse_access_date(value) is None:
            QMessageBox.warning(self, "Date invalide", "La date de désertion est obligatoire.")
            return
        self.accept()

    def values(self) -> dict[str, str | None]:
        return {
            "date_desertion": self._date.text_for_validation().strip(),
            "observation": self._observation.toPlainText().strip() or None,
            'reference_dossier': self._reference.text().strip() or None,
        }
