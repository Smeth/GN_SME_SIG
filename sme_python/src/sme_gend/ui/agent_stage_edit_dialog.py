"""Formulaire d'ajout et modification d'un stage du dossier agent."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.agent_evenement import AgentStageRow
from sme_gend.ui.widgets.date_input import FrenchDateEdit

_STAGE_TYPES = (
    ("LOCAL", "Stage local"),
    ("EXTERIEUR", "Stage extérieur"),
    ("INTERNE", "Stage interne"),
)


class AgentStageEditDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        stage: AgentStageRow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier le stage" if stage else "Ajouter un stage")
        self.setMinimumWidth(440)

        self._type = QComboBox()
        for code, label in _STAGE_TYPES:
            self._type.addItem(label, code)
        if stage:
            index = self._type.findData(stage.type_stage)
            if index >= 0:
                self._type.setCurrentIndex(index)

        self._nature = QLineEdit(stage.nature or "" if stage else "")
        self._lieu = QLineEdit(stage.lieu or "" if stage else "")
        self._reference = QLineEdit(stage.reference_dossier or "" if stage else "")
        self._reference.setPlaceholderText("Facultatif")
        self._reference.setMaxLength(256)
        self._session = QLineEdit(stage.session or '' if stage else '')
        self._session.setMaxLength(256)
        self._session.setPlaceholderText('Facultatif')
        self._date_debut = FrenchDateEdit()
        self._date_fin = FrenchDateEdit()
        if stage:
            self._date_debut.set_from_text(stage.date_debut)
            self._date_fin.set_from_text(stage.date_fin)
        self._observation = QTextEdit(stage.observation or "" if stage else "")
        self._observation.setMaximumHeight(100)

        form = QFormLayout()
        form.addRow("Type de stage :", self._type)
        form.addRow("Nature * :", self._nature)
        form.addRow("Lieu :", self._lieu)
        form.addRow("Réf/note (facultatif) :", self._reference)
        form.addRow('Session (facultatif) :', self._session)
        self._session_label = form.labelForField(self._session)
        self._type.currentIndexChanged.connect(self._sync_session)
        self._sync_session()
        form.addRow("Date de début :", self._date_debut)
        form.addRow("Date de fin :", self._date_fin)
        form.addRow("Observation :", self._observation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_validated)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _sync_session(self) -> None:
        visible = self._type.currentData() == 'INTERNE'
        self._session.setVisible(visible)
        self._session_label.setVisible(visible)

    def _accept_validated(self) -> None:
        if not self._nature.text().strip():
            QMessageBox.warning(self, 'Nature obligatoire', 'Renseignez la nature du stage.')
            self._nature.setFocus()
            return
        from sme_gend.services.access_date_parse import parse_access_date

        values = (
            self._date_debut.text_for_validation().strip(),
            self._date_fin.text_for_validation().strip(),
        )
        if any(value and parse_access_date(value) is None for value in values):
            QMessageBox.warning(self, "Dates invalides", "Saisissez les dates au format jj/mm/aaaa.")
            return
        start = parse_access_date(values[0])
        end = parse_access_date(values[1])
        if start and end and end < start:
            QMessageBox.warning(
                self,
                "Dates invalides",
                "La date de fin doit être postérieure ou égale à la date de début.",
            )
            return
        self.accept()

    def values(self) -> dict[str, str | None]:
        return {
            "type_code": str(self._type.currentData()),
            "nature": self._nature.text().strip() or None,
            "lieu": self._lieu.text().strip() or None,
            "date_debut": self._date_debut.text_for_validation().strip() or None,
            "date_fin": self._date_fin.text_for_validation().strip() or None,
            "observation": self._observation.toPlainText().strip() or None,
            "reference_dossier": self._reference.text().strip() or None,
            'session': (self._session.text().strip() or None) if self._type.currentData() == 'INTERNE' else None,
        }
