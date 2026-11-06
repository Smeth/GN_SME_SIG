"""Formulaire d'ajout/modification d'une mission d'agent."""

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

from sme_gend.models.agent_evenement import AgentMissionRow
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class AgentMissionEditDialog(QDialog):
    def __init__(self, parent: QWidget | None, *, mission: AgentMissionRow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier la mission" if mission else "Ajouter une mission")
        self.setMinimumWidth(440)
        self._nature = QLineEdit(mission.nature or "" if mission else "")
        self._lieu = QLineEdit(mission.lieu or "" if mission else "")
        self._reference = QLineEdit(mission.reference_dossier or "" if mission else "")
        self._debut = FrenchDateEdit()
        self._fin = FrenchDateEdit()
        if mission:
            self._debut.set_from_text(mission.date_debut)
            self._fin.set_from_text(mission.date_fin)
        self._observation = QTextEdit(mission.observation or "" if mission else "")
        self._observation.setMaximumHeight(96)
        form = QFormLayout()
        form.addRow("Nature * :", self._nature)
        form.addRow("Lieu :", self._lieu)
        form.addRow("Réf/note (facultatif) :", self._reference)
        form.addRow("Date de début :", self._debut)
        form.addRow("Date de fin :", self._fin)
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
        if not self._nature.text().strip():
            QMessageBox.warning(self, 'Nature obligatoire', 'Renseignez la nature de la mission.')
            self._nature.setFocus()
            return
        from sme_gend.services.access_date_parse import parse_access_date

        start = self._debut.text_for_validation().strip()
        end = self._fin.text_for_validation().strip()
        if any(value and parse_access_date(value) is None for value in (start, end)):
            QMessageBox.warning(self, "Dates invalides", "Saisissez les dates au format jj/mm/aaaa.")
            return
        d0, d1 = parse_access_date(start), parse_access_date(end)
        if d0 and d1 and d1 < d0:
            QMessageBox.warning(self, "Dates invalides", "La date de fin doit être après le début.")
            return
        self.accept()

    def values(self) -> dict[str, str | None]:
        return {
            "date_debut": self._debut.text_for_validation().strip() or None,
            "date_fin": self._fin.text_for_validation().strip() or None,
            "nature": self._nature.text().strip() or None,
            "lieu": self._lieu.text().strip() or None,
            "reference_dossier": self._reference.text().strip() or None,
            "observation": self._observation.toPlainText().strip() or None,
        }
