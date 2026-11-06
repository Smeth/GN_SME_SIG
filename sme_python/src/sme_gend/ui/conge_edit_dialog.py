"""Saisie manuelle d'une période de congé (registre agent_conge)."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from sme_gend.config.settings import Settings
from sme_gend.services.agent_conge_service import (
    CONGE_TYPE_ADMIN,
    CONGE_TYPE_AUTRE,
    CONGE_TYPE_EXPECTATIVE,
    CONGE_TYPE_LABELS,
    quota_days_between,
)
from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class CongeEditDialog(QDialog):
    def __init__(
        self,
        parent,
        settings: Settings,
        legacy_agent_id: str,
        *,
        sme_periode_mois: str | None = None,
        conge: AgentCongeRow | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._legacy = str(legacy_agent_id or "").strip()
        self._mois = (sme_periode_mois or "").strip() or None
        self.setWindowTitle("Modifier un congé" if conge else "Ajouter un congé")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.resize(460, 300)

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Enregistrez une période de congé (dates et type). Les dates se saisissent "
            "en jj/mm/aaaa (chiffres seuls) ou via le calendrier 📅. "
            "Le nombre de jours est calculé automatiquement entre la date de début "
            "et la date de fin (inclus)."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        form = QFormLayout()
        self._type = QComboBox()
        for code in (CONGE_TYPE_ADMIN, CONGE_TYPE_EXPECTATIVE, CONGE_TYPE_AUTRE):
            self._type.addItem(CONGE_TYPE_LABELS[code], code)
        self._debut = FrenchDateEdit(optional=False)
        self._fin = FrenchDateEdit(optional=False)
        if conge:
            self._debut.set_from_text(conge.date_debut)
            self._fin.set_from_text(conge.date_fin)
            idx = self._type.findData(conge.type_conge)
            if idx >= 0:
                self._type.setCurrentIndex(idx)
            self._obs = QLineEdit(conge.observation or "")
        else:
            today = datetime.now()
            self._debut.set_from_datetime(today)
            self._fin.set_from_datetime(today)
            self._obs = QLineEdit()
        self._jours = QLabel("1")
        self._jours.setObjectName("congeJoursValue")
        self._jours.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("Type :", self._type)
        form.addRow("Date début :", self._debut)
        form.addRow("Date fin :", self._fin)
        form.addRow("Jours :", self._jours)
        form.addRow("Observation :", self._obs)
        lay.addLayout(form)

        self._debut.changed.connect(self._sync_jours)
        self._fin.changed.connect(self._sync_jours)
        self._sync_jours()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _dates(self) -> tuple[date, date] | None:
        """Les deux dates saisies, ou ``None`` si un champ est incomplet."""
        d0 = self._debut.to_datetime()
        d1 = self._fin.to_datetime()
        if d0 is None or d1 is None:
            return None
        return d0.date(), d1.date()

    def _sync_jours(self) -> None:
        pair = self._dates()
        if pair is None:
            self._jours.setText("—")
            self._jours.setStyleSheet("")
            self._jours.setToolTip("Renseignez les deux dates (jj/mm/aaaa).")
            return
        d0, d1 = pair
        quota = quota_days_between(d0, d1)
        if quota is None:
            self._jours.setText("—")
            self._jours.setStyleSheet("color: #c62828;")
            self._jours.setToolTip("La date de fin doit être ≥ à la date de début.")
            return
        self._jours.setStyleSheet("")
        self._jours.setToolTip("Nombre de jours calendaires (début et fin inclus).")
        n = int(quota) if quota == int(quota) else quota
        label = f"{n} jour" if n == 1 else f"{n} jours"
        self._jours.setText(label)

    def _on_ok(self) -> None:
        if not self._legacy:
            QMessageBox.warning(self, "Congé", "Identifiant agent manquant.")
            return
        pair = self._dates()
        if pair is None:
            QMessageBox.warning(
                self,
                "Congé",
                "Renseignez la date de début et la date de fin (jj/mm/aaaa).",
            )
            return
        d0, d1 = pair
        if d1 < d0:
            QMessageBox.warning(self, "Congé", "La date de fin doit être ≥ à la date de début.")
            return
        self.accept()

    def values(self) -> dict:
        pair = self._dates()
        if pair is None:
            raise ValueError("Dates de congé incomplètes.")
        d0, d1 = pair
        return {
            "type_code": str(self._type.currentData()),
            "date_debut": d0,
            "date_fin": d1,
            "quota_jours": quota_days_between(d0, d1),
            "observation": self._obs.text().strip() or None,
            "sme_mois": self._mois,
        }
