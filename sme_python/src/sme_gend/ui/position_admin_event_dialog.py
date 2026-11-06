"""Informations complémentaires d'une position administrative."""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox,
    QTextEdit, QVBoxLayout,
)
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class PositionAdminEventDialog(QDialog):
    def __init__(self, parent, *, position_admin, agent_label):
        super().__init__(parent)
        self.setWindowTitle(f"{position_admin} — {agent_label}")
        self.setMinimumWidth(460)
        self._debut = FrenchDateEdit(optional=False)
        self._fin = FrenchDateEdit(optional=True)
        self._lieu = QLineEdit()
        self._reference = QLineEdit()
        self._observation = QTextEdit()
        self._observation.setMaximumHeight(100)
        labels = {
            "Détaché": ("Début du détachement *", "Fin du détachement", "Organisme / lieu d'accueil", "Motif / observations"),
            "Inst Mise Route": ("Date de mise en route *", "Date d'arrivée prévue", "Destination", "Instructions / observations"),
            "Punis": ("Début de la punition *", "Fin de la punition", "Lieu d'exécution", "Nature / motif de la punition"),
            "Prison MA": ("Date d'incarcération *", "Date de sortie", "Établissement pénitentiaire", "Motif / observations"),
        }[position_admin]
        form = QFormLayout()
        for label, field in zip(labels[:3], (self._debut, self._fin, self._lieu)):
            form.addRow(label, field)
        form.addRow("Référence de la décision", self._reference)
        form.addRow(labels[3], self._observation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self):
        debut = self._debut.to_datetime()
        fin = self._fin.to_datetime()
        return dict(date_debut=debut.date() if debut else None,
                    date_fin=fin.date() if fin else None,
                    lieu=self._lieu.text().strip() or None,
                    reference_decision=self._reference.text().strip() or None,
                    observation=self._observation.toPlainText().strip() or None)

    def _validate(self):
        values = self.values()
        if not values['date_debut'] or (
            self._fin.text_for_validation().strip() and not values['date_fin']
        ) or (values['date_fin'] and values['date_fin'] < values['date_debut']):
            QMessageBox.warning(self, "Dates invalides", "Renseignez une date de début valide et une fin postérieure ou égale au début.")
            return
        self.accept()
