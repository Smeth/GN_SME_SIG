"""Saisie d'un diplôme agent (militaire ou civil)."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from sme_gend.models.agent_dossier import (
    AgentDiplomeRow,
    DIPLOME_CATEGORIES,
    DIPLOME_CIVIL,
    DIPLOME_DEFAULT,
    DIPLOME_MILITAIRE,
)
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class DiplomeEditDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        existing: AgentDiplomeRow | None = None,
        default_categorie: str = DIPLOME_DEFAULT,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier le diplôme" if existing else "Ajouter un diplôme")
        self.setMinimumWidth(460)
        self._result: dict | None = None
        self._imported_unknown = bool(existing and existing.date_obtention is None
                                      and (existing.obs or '').startswith('Import archive '))

        self._cat = QComboBox()
        self._cat.addItem("Militaire", DIPLOME_MILITAIRE)
        self._cat.addItem("Civil", DIPLOME_CIVIL)
        self._lib = QLineEdit()
        self._lib.setPlaceholderText("Intitulé du diplôme")
        self._date = FrenchDateEdit()
        self._lieu = QLineEdit()
        self._mention = QLineEdit()
        self._etab = QLineEdit()
        self._obs = QTextEdit()
        self._obs.setMaximumHeight(72)

        if existing:
            idx = self._cat.findData(existing.categorie)
            if idx >= 0:
                self._cat.setCurrentIndex(idx)
            self._lib.setText(existing.libelle)
            if existing.date_obtention:
                self._date.set_from_text(existing.date_obtention.isoformat())
            self._lieu.setText(existing.lieu_obtention or "")
            self._mention.setText(existing.mention or "")
            self._etab.setText(existing.etablissement or "")
            self._obs.setPlainText(existing.obs or "")
        else:
            idx = self._cat.findData(default_categorie)
            if idx >= 0:
                self._cat.setCurrentIndex(idx)

        form = QFormLayout()
        form.addRow("Catégorie *", self._cat)
        form.addRow("Libellé *", self._lib)
        form.addRow("Date d'obtention" if self._imported_unknown else "Date d'obtention *", self._date)
        self._keep_unknown = QCheckBox("Date importée non confirmée — conserver le diplôme sans date")
        self._keep_unknown.setChecked(self._imported_unknown)
        self._keep_unknown.setVisible(self._imported_unknown)
        self._keep_unknown.setToolTip("La valeur Access d'origine reste dans les observations. Renseignez une date à quatre chiffres après vérification.")
        form.addRow('', self._keep_unknown)
        form.addRow("Lieu", self._lieu)
        form.addRow("Mention", self._mention)
        form.addRow("Établissement", self._etab)
        form.addRow("Observations", self._obs)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _on_ok(self) -> None:
        lib = self._lib.text().strip()
        if not lib:
            QMessageBox.warning(self, "Diplôme", "Le libellé est obligatoire.")
            return
        cat = str(self._cat.currentData() or "").strip()
        if cat not in DIPLOME_CATEGORIES:
            QMessageBox.warning(self, "Diplôme", "La catégorie (Militaire ou Civil) est obligatoire.")
            return
        dt = self._date.to_datetime()
        allow_unknown = self._imported_unknown and self._keep_unknown.isChecked() and self._date.is_empty()
        if not dt and not allow_unknown:
            message = (
                "La date d'obtention est obligatoire."
                if self._date.is_empty()
                else "La date d'obtention est invalide. Utilisez le format jj/mm/aaaa."
            )
            QMessageBox.warning(self, "Diplôme", message)
            self._date.date_edit().setFocus()
            return
        d_ob: date | None = dt.date() if dt else None
        self._result = {
            "categorie": cat,
            "libelle": lib,
            "date_obtention": d_ob,
            "lieu": self._lieu.text().strip() or None,
            "mention": self._mention.text().strip() or None,
            "etablissement": self._etab.text().strip() or None,
            "obs": self._obs.toPlainText().strip() or None,
        }
        self.accept()

    def values(self) -> dict:
        assert self._result is not None
        return self._result
