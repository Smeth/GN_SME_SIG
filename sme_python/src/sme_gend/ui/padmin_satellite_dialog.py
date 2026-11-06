"""Dialogues fiche satellite (malade / mission / stage)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.numerique_satellite import MaladeDetail, MissionDetail, StageDetail
from sme_gend.services.padmin_satellite import SatelliteKind, padmin_label_for_kind
from sme_gend.ui.widgets.date_input import FrenchDateEdit


def _editable_nature_combo(placeholder: str = "") -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    le = combo.lineEdit()
    if le and placeholder:
        le.setPlaceholderText(placeholder)
    return combo


def _fill_nature_combo(combo: QComboBox, natures: list[str]) -> None:
    current = combo.currentText().strip()
    combo.blockSignals(True)
    combo.clear()
    for nat in natures:
        combo.addItem(nat)
    if current:
        idx = combo.findText(current, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(current)
    combo.blockSignals(False)


@dataclass
class SatelliteFormResult:
    kind: SatelliteKind
    malade: MaladeDetail | None = None
    mission: MissionDetail | None = None
    stage: StageDetail | None = None
    stage_date_debut: str | None = None
    stage_date_fin: str | None = None


def _stage_ddf(deb: str, fin: str) -> str | None:
    deb, fin = deb.strip(), fin.strip()
    if deb and fin:
        return f"{deb} — {fin}"
    return deb or fin or None


def _date_text(widget: FrenchDateEdit) -> str:
    if widget.is_empty():
        return ""
    return widget.text_for_validation().strip()


def _warn_invalid_date(parent: QWidget, label: str) -> bool:
    QMessageBox.warning(
        parent,
        "Date invalide",
        f"Saisissez {label} au format jj/mm/aaaa (chiffres uniquement).",
    )
    return False


def _validate_optional_date(parent: QWidget, widget: FrenchDateEdit, label: str) -> str | None:
    """Retourne la date jj/mm/aaaa, ``None`` si vide, ou lève via message si incomplète."""
    if widget.is_empty():
        return None
    if not widget.is_complete() or widget.to_qdate() is None:
        _warn_invalid_date(parent, label)
        raise ValueError("invalid_date")
    return widget.text_for_validation()


class PadminSatelliteDialog(QDialog):
    """Saisie des informations complémentaires pour une position donnée."""

    def __init__(
        self,
        parent,
        *,
        kind: SatelliteKind,
        agent_label: str,
        malade: MaladeDetail | None = None,
        mission: MissionDetail | None = None,
        stage: StageDetail | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._result: SatelliteFormResult | None = None
        title = {
            "malade": "Fiche maladie",
            "mission": "Fiche mission",
            "stage_local": "Fiche stage (local)",
            "stage_ex": "Fiche stage (extérieur)",
            "stage_interne": "Fiche stage (interne)",
        }[kind]
        self.setWindowTitle(f"{title} — {agent_label}")
        self.setMinimumWidth(420)

        hint = QLabel(
            f"Position : <b>{padmin_label_for_kind(kind)}</b> — "
            "informations pour la situation numérique.<br>"
            "<b>* : champ obligatoire.</b><br>"
            "Dates : <b>jj/mm/aaaa</b> (chiffres seuls, barres automatiques, ou 📅)."
        )
        hint.setWordWrap(True)

        form = QFormLayout()
        if kind == "malade":
            self._f1 = FrenchDateEdit()
            self._f1.set_from_text(malade.date_constat if malade else None)
            self._f2 = QLineEdit(malade.pathologie if malade else "")
            self._f3 = QLineEdit(malade.obs if malade else "")
            form.addRow("Date constat *", self._f1)
            form.addRow("Pathologie", self._f2)
            form.addRow("Observations", self._f3)
        elif kind == "mission":
            self._f1 = QLineEdit(mission.nature if mission else "")
            self._f2 = QLineEdit(mission.lieu_mission if mission else "")
            self._f3 = QLineEdit(mission.refnds if mission else "")
            self._f4 = FrenchDateEdit()
            self._f4.set_from_text(mission.date_depart if mission else None)
            form.addRow("Nature *", self._f1)
            form.addRow("Lieu", self._f2)
            form.addRow("Réf/note", self._f3)
            form.addRow("Date départ", self._f4)
        else:
            ddf = (stage.date_debut_fin or "") if stage else ""
            deb, fin = "", ""
            if " — " in ddf:
                deb, fin = ddf.split(" — ", 1)
            elif ddf:
                deb = ddf
            self._f1 = QLineEdit(stage.nature_stage if stage else "")
            self._f2 = QLineEdit(stage.lieu_stage if stage else "")
            self._stage_ref = QLineEdit(stage.reference_dossier if stage else "")
            self._stage_ref.setPlaceholderText("Facultatif")
            self._stage_ref.setMaxLength(256)
            self._stage_session = QLineEdit(stage.session or '' if stage else '')
            self._stage_session.setMaxLength(256)
            self._stage_session.setPlaceholderText('Facultatif')
            self._f3 = FrenchDateEdit()
            self._f3.set_from_text(deb.strip() or None)
            self._f4 = FrenchDateEdit()
            self._f4.set_from_text(fin.strip() or None)
            form.addRow("Nature *", self._f1)
            form.addRow("Lieu", self._f2)
            form.addRow("Réf/note (facultatif)", self._stage_ref)
            if kind == 'stage_interne':
                form.addRow('Session (facultatif)', self._stage_session)
            form.addRow("Date début", self._f3)
            form.addRow("Date fin", self._f4)

        skip_btn = QPushButton("Passer")
        skip_btn.clicked.connect(self._on_skip)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(form)
        from PySide6.QtCore import Qt

        lay.addWidget(skip_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(buttons)

    def _on_skip(self) -> None:
        self._result = None
        self.reject()

    def _on_ok(self) -> None:
        if self._kind != 'malade' and not self._f1.text().strip():
            QMessageBox.warning(self, 'Nature obligatoire', 'Renseignez la nature de la mission ou du stage.')
            self._f1.setFocus()
            return
        if self._kind == "malade":
            try:
                date_constat = _validate_optional_date(self, self._f1, "la date de constat")
            except ValueError:
                return
            if not date_constat:
                QMessageBox.warning(
                    self,
                    "Fiche maladie",
                    "Saisissez une date de constat valide (jj/mm/aaaa).",
                )
                return
            self._result = SatelliteFormResult(
                kind="malade",
                malade=MaladeDetail(
                    date_constat=date_constat,
                    pathologie=self._f2.text().strip() or None,
                    obs=self._f3.text().strip() or None,
                ),
            )
        elif self._kind == "mission":
            try:
                _validate_optional_date(self, self._f4, "la date de départ")
            except ValueError:
                return
            self._result = SatelliteFormResult(
                kind="mission",
                mission=MissionDetail(
                    nature=self._f1.text().strip() or None,
                    lieu_mission=self._f2.text().strip() or None,
                    refnds=self._f3.text().strip() or None,
                    date_depart=_date_text(self._f4) or None,
                ),
            )
        else:
            try:
                deb = _validate_optional_date(self, self._f3, "la date de début") or ""
                fin = _validate_optional_date(self, self._f4, "la date de fin") or ""
            except ValueError:
                return
            self._result = SatelliteFormResult(
                kind=self._kind,
                stage=StageDetail(
                    nature_stage=self._f1.text().strip() or None,
                    lieu_stage=self._f2.text().strip() or None,
                    date_debut_fin=_stage_ddf(deb, fin),
                    reference_dossier=self._stage_ref.text().strip() or None,
                    session=self._stage_session.text().strip() or None if self._kind == 'stage_interne' else None,
                ),
                stage_date_debut=deb or None,
                stage_date_fin=fin or None,
            )
        self.accept()

    @staticmethod
    def run(
        parent,
        *,
        kind: SatelliteKind,
        agent_label: str,
        **existing,
    ) -> SatelliteFormResult | None:
        dlg = PadminSatelliteDialog(
            parent,
            kind=kind,
            agent_label=agent_label,
            malade=existing.get("malade"),
            mission=existing.get("mission"),
            stage=existing.get("stage"),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg._result


class BulkMissionStageDialog(QDialog):
    """Affectation en masse : malade, mission ou stage + champs communs."""

    def __init__(
        self,
        parent,
        *,
        count: int,
        nature_suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Affectation en masse")
        self.setMinimumWidth(440)
        self._result: tuple[SatelliteKind, SatelliteFormResult] | None = None

        self._kind_combo = QComboBox()
        self._kind_combo.addItem("Malade", "malade")
        self._kind_combo.addItem("Mission", "mission")
        self._kind_combo.addItem("Stage local", "stage_local")
        self._kind_combo.addItem("Stage extérieur", "stage_ex")
        self._kind_combo.addItem("Stage interne", "stage_interne")

        self._stack = QStackedWidget()
        # 0 malade
        w0 = QWidget()
        f0 = QFormLayout(w0)
        self._md = FrenchDateEdit()
        self._mp = QLineEdit()
        self._mo = QLineEdit()
        f0.addRow("Date constat *", self._md)
        f0.addRow("Pathologie", self._mp)
        f0.addRow("Observations", self._mo)
        self._stack.addWidget(w0)
        # 1 mission
        w1 = QWidget()
        f1 = QFormLayout(w1)
        self._mn = QLineEdit()
        self._ml = QLineEdit()
        self._mr = QLineEdit()
        self._mdep = FrenchDateEdit()
        f1.addRow("Nature *", self._mn)
        f1.addRow("Lieu", self._ml)
        f1.addRow("Réf/note", self._mr)
        f1.addRow("Date départ", self._mdep)
        self._stack.addWidget(w1)
        # 2 stage (shared)
        w2 = QWidget()
        f2 = QFormLayout(w2)
        self._sn = _editable_nature_combo("Ex. BCG, OPJ…")
        _fill_nature_combo(self._sn, nature_suggestions or [])
        self._sl = QLineEdit()
        self._sr = QLineEdit()
        self._sr.setPlaceholderText("Facultatif")
        self._sr.setMaxLength(256)
        self._ssession = QLineEdit()
        self._ssession.setMaxLength(256)
        self._ssession.setPlaceholderText('Facultatif')
        self._sd = FrenchDateEdit()
        self._sf = FrenchDateEdit()
        f2.addRow("Nature *", self._sn)
        f2.addRow("Lieu", self._sl)
        f2.addRow("Réf/note (facultatif)", self._sr)
        f2.addRow('Session (facultatif)', self._ssession)
        self._session_label = f2.labelForField(self._ssession)
        f2.addRow("Date début", self._sd)
        f2.addRow("Date fin", self._sf)
        self._stack.addWidget(w2)

        self._kind_combo.currentIndexChanged.connect(self._sync_stack)

        hint = QLabel(
            f"<b>{count}</b> agent(s). La position administrative sera mise à jour "
            "pour tous, avec la même fiche complémentaire.<br>"
            "<b>* : champ obligatoire.</b><br>"
            "Dates : <b>jj/mm/aaaa</b> (saisie chiffres avec <code>__/__/____</code> "
            "ou calendrier 📅)."
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(hint)
        root.addWidget(QLabel("Type :"))
        root.addWidget(self._kind_combo)
        root.addWidget(self._stack)
        root.addWidget(buttons)
        self._sync_stack()

    def _sync_stack(self) -> None:
        k = self._kind_combo.currentData()
        idx = {
            "malade": 0,
            "mission": 1,
            "stage_local": 2,
            "stage_ex": 2,
            "stage_interne": 2,
        }.get(k, 0)
        self._stack.setCurrentIndex(idx)
        self._ssession.setVisible(k == 'stage_interne')
        self._session_label.setVisible(k == 'stage_interne')

    def _on_ok(self) -> None:
        k = self._kind_combo.currentData()
        nature = self._mn if k == 'mission' else self._sn.lineEdit()
        if k != 'malade' and not nature.text().strip():
            QMessageBox.warning(self, 'Nature obligatoire', 'Renseignez la nature de la mission ou du stage.')
            nature.setFocus()
            return
        try:
            if k == "malade":
                date_constat = _validate_optional_date(self, self._md, "la date de constat")
            elif k == "mission":
                _validate_optional_date(self, self._mdep, "la date de départ")
            else:
                _validate_optional_date(self, self._sd, "la date de début")
                _validate_optional_date(self, self._sf, "la date de fin")
        except ValueError:
            return
        if k == 'malade' and not date_constat:
            QMessageBox.warning(self, 'Date obligatoire', 'Renseignez la date de constat de la maladie.')
            self._md.setFocus()
            return
        if k == "malade":
            res = SatelliteFormResult(
                kind="malade",
                malade=MaladeDetail(
                    date_constat=_date_text(self._md) or None,
                    pathologie=self._mp.text().strip() or None,
                    obs=self._mo.text().strip() or None,
                ),
            )
        elif k == "mission":
            res = SatelliteFormResult(
                kind="mission",
                mission=MissionDetail(
                    nature=self._mn.text().strip() or None,
                    lieu_mission=self._ml.text().strip() or None,
                    refnds=self._mr.text().strip() or None,
                    date_depart=_date_text(self._mdep) or None,
                ),
            )
        else:
            deb = _date_text(self._sd)
            fin = _date_text(self._sf)
            res = SatelliteFormResult(
                kind=k,
                stage=StageDetail(
                    nature_stage=self._sn.currentText().strip() or None,
                    lieu_stage=self._sl.text().strip() or None,
                    date_debut_fin=_stage_ddf(deb, fin),
                    reference_dossier=self._sr.text().strip() or None,
                    session=(self._ssession.text().strip() or None) if k == 'stage_interne' else None,
                ),
                stage_date_debut=deb or None,
                stage_date_fin=fin or None,
            )
        self._result = (k, res)
        self.accept()

    @staticmethod
    def run(
        parent,
        *,
        count: int,
        nature_suggestions: list[str] | None = None,
    ) -> tuple[SatelliteKind, SatelliteFormResult] | None:
        dlg = BulkMissionStageDialog(
            parent,
            count=count,
            nature_suggestions=nature_suggestions,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg._result is None:
            return None
        return dlg._result
