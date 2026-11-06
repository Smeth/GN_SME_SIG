"""Saisie d'une passation de grade."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from sme_gend.models.agent_dossier import AgentPassationGradeRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class PassationGradeEditDialog(QDialog):
    def __init__(
        self,
        parent,
        referentiel: ReferentielService,
        *,
        existing: AgentPassationGradeRow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier la passation" if existing else "Ajouter une passation de grade")
        self.setMinimumWidth(440)
        self._result: dict | None = None

        self._grade = QComboBox()
        self._grade.setEditable(True)
        self._grade.setMinimumWidth(280)
        grades = [g.code for g in referentiel.grades()]
        self._grade.addItem("")
        for g in sorted(set(grades)):
            self._grade.addItem(g)
        self._date = FrenchDateEdit()
        self._ndec = QLineEdit()
        self._obs = QLineEdit()

        if existing:
            idx = self._grade.findText(existing.grade_libelle)
            if idx >= 0:
                self._grade.setCurrentIndex(idx)
            else:
                self._grade.setEditText(existing.grade_libelle)
            if existing.date_passation:
                self._date.set_from_text(existing.date_passation.isoformat())
            self._ndec.setText(existing.ndecision or "")
            self._obs.setText(existing.obs or "")

        form = QFormLayout()
        form.addRow("Grade *", self._grade)
        form.addRow("Date de passation", self._date)
        form.addRow("N° décision", self._ndec)
        form.addRow("Observation", self._obs)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _on_ok(self) -> None:
        gl = self._grade.currentText().strip()
        if not gl:
            QMessageBox.warning(self, "Passation", "Le grade est obligatoire.")
            return
        dt = self._date.to_datetime()
        d_pass: date | None = dt.date() if dt else None
        self._result = {
            "grade_libelle": gl,
            "date_passation": d_pass,
            "ndecision": self._ndec.text().strip() or None,
            "obs": self._obs.text().strip() or None,
        }
        self.accept()

    def values(self) -> dict:
        assert self._result is not None
        return self._result
