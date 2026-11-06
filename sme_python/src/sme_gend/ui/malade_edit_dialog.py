"""Fiche maladie d'un agent : ajout / modification depuis la fiche personnel."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from sme_gend.models.agent_evenement import AgentMaladeRow
from sme_gend.services.date_display import fr_date
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class MaladeEditDialog(QDialog):
    """Période de maladie : dates jj/mm/aaaa, pathologie et observation.

    Utilisée pour l'ajout et la modification (``existing`` renseigné) ; la fiche
    enregistrée ici est marquée « saisie manuelle » et n'est donc pas effacée par
    le réimport de l'archive d'un mois.
    """

    def __init__(self, parent, *, existing: AgentMaladeRow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier la période de maladie" if existing else "Ajouter une maladie")
        self.setMinimumWidth(460)
        self._result: dict | None = None
        self._archive_note: QLabel | None = None

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Dates au format jj/mm/aaaa (chiffres seuls) ou via le calendrier 📅. "
            "La date de fin peut rester vide si l'arrêt est toujours en cours."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._debut = FrenchDateEdit(optional=False)
        self._fin = FrenchDateEdit()
        self._path = QLineEdit()
        self._path.setPlaceholderText("Pathologie constatée")
        self._obs = QTextEdit()
        self._obs.setMaximumHeight(72)

        if existing is not None:
            self._debut.set_from_text(existing.date_debut)
            self._fin.set_from_text(existing.date_fin)
            self._path.setText(existing.pathologie or "")
            self._obs.setPlainText(existing.observation or "")
            if not existing.saisie_manuelle:
                self._archive_note = QLabel(
                    f"<b>Fiche reprise de l’archive"
                    f"{' du ' + existing.periode_sme if existing.periode_sme else ''}</b>"
                    " — les modifications faites ici seront remplacées au prochain "
                    "réimport de ce mois."
                )
                self._archive_note.setWordWrap(True)
                lay.addWidget(self._archive_note)
        else:
            self._debut.set_from_datetime(datetime.now())

        form = QFormLayout()
        form.addRow("Date début *", self._debut)
        form.addRow("Date fin", self._fin)
        form.addRow("Pathologie", self._path)
        form.addRow("Observation", self._obs)
        lay.addLayout(form)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_ok(self) -> None:
        d0 = self._debut.to_datetime()
        if d0 is None:
            QMessageBox.warning(self, "Maladie", "La date de début est obligatoire (jj/mm/aaaa).")
            return
        d1 = self._fin.to_datetime()
        date_debut: date = d0.date()
        date_fin: date | None = d1.date() if d1 else None
        if date_fin is not None and date_fin < date_debut:
            QMessageBox.warning(
                self,
                "Maladie",
                "La date de fin est antérieure à la date de début "
                f"({fr_date(date_debut)} / {fr_date(date_fin)}).",
            )
            return
        self._result = {
            "date_debut": date_debut,
            "date_fin": date_fin,
            "pathologie": self._path.text().strip() or None,
            "observation": self._obs.toPlainText().strip() or None,
        }
        self.accept()

    def values(self) -> dict:
        assert self._result is not None
        return self._result