"""Diplôme optionnel à la clôture d'un ou plusieurs stages."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.agent_dossier import (
    DIPLOME_CATEGORIES,
    DIPLOME_CIVIL,
    DIPLOME_MILITAIRE,
)
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class StageEndDiplomaDialog(QDialog):
    """Propose d'ajouter le même diplôme à tous les agents dont le stage se termine."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        agent_count: int,
        default_date_iso: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fin de stage — diplôme")
        self.setMinimumWidth(440)
        self._payload: dict | None = None

        intro = QLabel(
            f"Vous allez terminer <b>{agent_count}</b> stage(s). "
            "Vous pouvez enregistrer le diplôme obtenu pour chaque agent "
            "au dossier. La SME affiche le dernier diplôme militaire obtenu à la date du mois consulté ; "
            "les diplômes civils restent consultables dans le dossier."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._enable = QCheckBox("Ajouter un diplôme obtenu à la fin du stage")
        self._enable.setChecked(True)
        self._reference = QLineEdit()
        self._end_date = FrenchDateEdit()
        if default_date_iso:
            self._end_date.set_from_text(default_date_iso[:10])
        self._reference.setMaxLength(256)
        self._reference.setPlaceholderText("Facultatif — vide : conserver la référence existante")

        self._cat = QComboBox()
        self._cat.addItem("Militaire", DIPLOME_MILITAIRE)
        self._cat.addItem("Civil", DIPLOME_CIVIL)
        self._lib = QLineEdit()
        self._lib.setPlaceholderText("Ex. Brevet OPJ, Certificat…")
        self._date = FrenchDateEdit()
        if default_date_iso:
            self._date.set_from_text(default_date_iso[:10])

        form = QFormLayout()
        form.addRow("Catégorie *", self._cat)
        form.addRow("Libellé *", self._lib)
        form.addRow("Date d'obtention *", self._date)
        self._form = form

        self._enable.toggled.connect(self._sync_enabled)
        self._sync_enabled(self._enable.isChecked())

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Terminer le(s) stage(s)")
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        reference_form = QFormLayout()
        reference_form.addRow("Date réelle de fin de stage *", self._end_date)
        reference_form.addRow("Réf/note de fin de stage :", self._reference)
        lay.addLayout(reference_form)
        lay.addWidget(self._enable)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _sync_enabled(self, on: bool) -> None:
        for i in range(self._form.rowCount()):
            item = self._form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if item and item.widget():
                item.widget().setEnabled(on)

    def _on_ok(self) -> None:
        if self._end_date.to_datetime() is None:
            QMessageBox.warning(self, "Fin de stage", "La date réelle de fin de stage est obligatoire (jj/mm/aaaa).")
            return
        if not self._enable.isChecked():
            self._payload = None
            self.accept()
            return
        lib = self._lib.text().strip()
        if not lib:
            QMessageBox.warning(self, "Diplôme", "Le libellé du diplôme est obligatoire.")
            return
        cat = str(self._cat.currentData() or "").strip()
        if cat not in DIPLOME_CATEGORIES:
            QMessageBox.warning(self, "Diplôme", "Choisissez une catégorie (Militaire ou Civil).")
            return
        dt = self._date.to_datetime()
        if not dt:
            QMessageBox.warning(self, "Diplôme", "La date d'obtention est obligatoire (jj/mm/aaaa).")
            return
        d_ob: date = dt.date()
        self._payload = {
            "categorie": cat,
            "libelle": lib,
            "date_obtention": d_ob,
            "lieu": None,
            "mention": None,
            "etablissement": None,
            "obs": "Ajouté automatiquement à la fin de stage (module Stages).",
        }
        self.accept()

    def diploma_payload(self) -> dict | None:
        return self._payload

    @staticmethod
    def run(
        parent: QWidget | None,
        *,
        agent_count: int,
        default_date_iso: str,
    ) -> dict | None | False:
        """
        ``False`` si annulé ; sinon diplôme optionnel et informations de clôture.
        """
        dlg = StageEndDiplomaDialog(
            parent,
            agent_count=agent_count,
            default_date_iso=default_date_iso,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        result = dict(dlg.diploma_payload() or {})
        result['_stage_end_date'] = dlg._end_date.to_datetime().date().isoformat()
        reference = dlg._reference.text().strip()
        if reference:
            result['_stage_reference'] = reference
        return result
