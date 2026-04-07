"""Dialogue d’édition d’une ligne ListeGenerale (mise à jour Access + audit côté service)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_generale import ListeGeneraleRow


def _fmt_dt(v: datetime | None) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v.hour or v.minute or v.second else v.strftime("%Y-%m-%d")
    return str(v)


def _parse_dt_opt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Date / heure non reconnue : {s!r}")


def _parse_int_opt(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    return int(s)


class PersonnelEditDialog(QDialog):
    def __init__(self, parent, row: ListeGeneraleRow) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier le personnel")
        self._original = row
        self._result: ListeGeneraleRow | None = None

        self._id = QLineEdit(row.id)
        self._id.setReadOnly(True)
        self._noms = QLineEdit(row.noms or "")
        self._date_n = QLineEdit(_fmt_dt(row.date_naissance))
        self._lieu_n = QLineEdit(row.lieu_naissance or "")
        self._grade = QLineEdit(row.grade or "")
        self._date_grade = QLineEdit(_fmt_dt(row.date_grade))
        self._date_service = QLineEdit(row.date_service or "")
        self._ndecision = QLineEdit(row.ndecision or "")
        self._mat_mil = QLineEdit(row.matricule_militaire or "")
        self._mat_sol = QLineEdit(row.matricule_solde or "")
        self._padmin = QLineEdit(row.position_admin or "")
        self._fonction = QLineEdit(row.fonction or "")
        self._sub_arme = QLineEdit(row.subdivision_arme or "")
        self._diplome = QLineEdit(row.diplome or "")
        self._date_obt = QLineEdit(row.date_obtention or "")
        self._structure = QLineEdit(row.structure or "")
        self._obs = QTextEdit(row.obs or "")
        self._obs.setMaximumHeight(72)
        self._ordre = QLineEdit("" if row.ordre_tri is None else str(row.ordre_tri))
        self._presseance = QLineEdit("" if row.presseance is None else str(row.presseance))

        form = QFormLayout()
        form.addRow("Id (lecture seule)", self._id)
        form.addRow("Noms et prénoms *", self._noms)
        form.addRow("Date de naissance", self._date_n)
        form.addRow("Lieu de naissance", self._lieu_n)
        form.addRow("Grade", self._grade)
        form.addRow("Date de grade", self._date_grade)
        form.addRow("Date de service", self._date_service)
        form.addRow("N° décision", self._ndecision)
        form.addRow("Matricule militaire", self._mat_mil)
        form.addRow("Matricule solde", self._mat_sol)
        form.addRow("Position adm. (Padmin)", self._padmin)
        form.addRow("Fonction", self._fonction)
        form.addRow("Subdivision d'arme", self._sub_arme)
        form.addRow("Diplôme", self._diplome)
        form.addRow("Date d'obtention", self._date_obt)
        form.addRow("Structure (code)", self._structure)
        form.addRow("Observations", self._obs)
        form.addRow("Ordre de tri", self._ordre)
        form.addRow("Préséance", self._presseance)

        hint = QLabel(
            "Les dates peuvent être saisies au format AAAA-MM-JJ ou JJ/MM/AAAA. "
            "Enregistrement : mise à jour Access + trace dans data/personnel_audit.jsonl."
        )
        hint.setWordWrap(True)

        inner = QWidget()
        inner.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(scroll, stretch=1)
        lay.addWidget(bb)

    def _on_save(self) -> None:
        try:
            dn = _parse_dt_opt(self._date_n.text())
            dg = _parse_dt_opt(self._date_grade.text())
            ot = _parse_int_opt(self._ordre.text())
            pr = _parse_int_opt(self._presseance.text())
        except ValueError as e:
            QMessageBox.warning(self, "Saisie", str(e))
            return

        noms = self._noms.text().strip()
        if not noms:
            QMessageBox.warning(self, "Saisie", "Le nom est obligatoire.")
            return

        self._result = ListeGeneraleRow(
            id=self._original.id,
            noms=noms or None,
            date_naissance=dn,
            lieu_naissance=self._lieu_n.text().strip() or None,
            grade=self._grade.text().strip() or None,
            date_grade=dg,
            date_service=self._date_service.text().strip() or None,
            ndecision=self._ndecision.text().strip() or None,
            matricule_militaire=self._mat_mil.text().strip() or None,
            matricule_solde=self._mat_sol.text().strip() or None,
            position_admin=self._padmin.text().strip() or None,
            fonction=self._fonction.text().strip() or None,
            subdivision_arme=self._sub_arme.text().strip() or None,
            diplome=self._diplome.text().strip() or None,
            date_obtention=self._date_obt.text().strip() or None,
            structure=self._structure.text().strip() or None,
            obs=self._obs.toPlainText().strip() or None,
            ordre_tri=ot,
            presseance=pr,
        )
        self.accept()

    def result_row(self) -> ListeGeneraleRow:
        assert self._result is not None
        return self._result
