"""Saisie d'une affectation agent (structure / fonction)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from sme_gend.models.agent_dossier import AgentAffectationRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_label_format import format_structure_display
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class AffectationEditDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        referentiel: ReferentielService | None = None,
        existing: AgentAffectationRow | None = None,
        default_structure: str | None = None,
        default_fonction: str | None = None,
        default_subdivision: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier l'affectation" if existing else "Ajouter une affectation")
        self.setMinimumWidth(460)
        self._result: dict | None = None
        self._referentiel = referentiel

        self._structure = QComboBox()
        self._structure.setEditable(False)
        self._structure.setMinimumWidth(320)
        self._fill_structure_combo(
            existing.structure_code if existing else default_structure
        )
        self._date = FrenchDateEdit()
        self._fonction = QLineEdit()
        self._subdiv = QLineEdit()
        self._obs = QLineEdit()

        if existing:
            if existing.date_affectation:
                self._date.set_from_text(existing.date_affectation.isoformat())
            self._fonction.setText(existing.fonction or "")
            self._subdiv.setText(existing.subdivision_arme or "")
            self._obs.setText(existing.obs or "")
        else:
            self._fonction.setText(default_fonction or "")
            self._subdiv.setText(default_subdivision or "")

        form = QFormLayout()
        form.addRow("Structure *", self._structure)
        form.addRow("Date d'affectation *", self._date)
        form.addRow("Fonction", self._fonction)
        form.addRow("Subdivision d'arme", self._subdiv)
        form.addRow("Observation", self._obs)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _fill_structure_combo(self, current_code: str | None) -> None:
        from sme_gend.ui.structure_combo import fill_structure_combo

        structures = []
        if self._referentiel is not None:
            structures = list(self._referentiel.structures())
        with QSignalBlocker(self._structure):
            fill_structure_combo(
                self._structure,
                structures,
                current_code,
                include_empty=False,
                extra_codes=[current_code] if current_code else None,
            )
            if self._structure.count() == 0 and current_code:
                self._structure.addItem(
                    format_structure_display(current_code, structures),
                    current_code,
                )

    def _on_ok(self) -> None:
        sc = self._structure.currentData()
        if sc is None:
            sc = self._structure.currentText()
        sc = (str(sc) if sc is not None else "").strip()
        if not sc:
            QMessageBox.warning(self, "Affectation", "La structure est obligatoire.")
            return
        dt = self._date.to_datetime()
        if dt is None:
            QMessageBox.warning(self, "Affectation", "La date d'affectation est obligatoire.")
            return
        self._result = {
            "structure_code": sc,
            "date_affectation": dt.date(),
            "fonction": self._fonction.text().strip() or None,
            "subdivision_arme": self._subdiv.text().strip() or None,
            "obs": self._obs.text().strip() or None,
        }
        self.accept()

    def values(self) -> dict:
        assert self._result is not None
        return self._result
