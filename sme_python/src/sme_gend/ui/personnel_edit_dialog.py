"""Dialogue d’édition d’une ligne ListeGenerale — listes déroulantes référentiel + design institutionnel."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.validators import FieldError, validate_personnel_fields
from sme_gend.ui.confirm_dialogs import confirm_yes_no


def _fmt_dt(v: datetime | None) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return (
            v.strftime("%Y-%m-%d %H:%M:%S")
            if v.hour or v.minute or v.second
            else v.strftime("%Y-%m-%d")
        )
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


def _fill_combo(combo: QComboBox, items: list[str], current: str | None) -> None:
    combo.clear()
    combo.addItem("")
    seen: set[str] = set()
    for x in items:
        x = (x or "").strip()
        if x and x not in seen:
            combo.addItem(x)
            seen.add(x)
    cur = (current or "").strip()
    if cur and cur not in seen:
        combo.addItem(cur)
    idx = combo.findText(cur)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _combo_text(combo: QComboBox) -> str | None:
    t = combo.currentText().strip()
    return t or None


class PersonnelEditDialog(QDialog):
    def __init__(self, parent, row: ListeGeneraleRow, referentiel: ReferentielService) -> None:
        super().__init__(parent)
        self.setObjectName("PersonnelEditDialog")
        self.setWindowTitle("Fiche personnel — modification")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._original = row
        self._result: ListeGeneraleRow | None = None
        self._ref = referentiel

        header = QFrame()
        header.setObjectName("PersonnelEditHeader")
        hl = QHBoxLayout(header)
        hl.setSpacing(16)
        icon_lbl = QLabel()
        icon_lbl.setObjectName("PersonnelEditIcon")
        icon_lbl.setFixedSize(56, 56)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # SP_UserIcon n'existe pas sur toutes les versions de Qt / PySide6
        _pix = getattr(
            QStyle.StandardPixmap,
            "SP_UserIcon",
            QStyle.StandardPixmap.SP_FileDialogContentsView,
        )
        icon_lbl.setPixmap(self.style().standardIcon(_pix).pixmap(48, 48))

        ht = QVBoxLayout()
        t1 = QLabel("Fiche personnel")
        t1.setObjectName("PersonnelEditTitle")
        tf = QFont()
        tf.setPointSize(15)
        tf.setWeight(QFont.Weight.DemiBold)
        t1.setFont(tf)
        t2 = QLabel(f"Id : {row.id}  ·  ListeGenerale")
        t2.setObjectName("PersonnelEditSubtitle")
        t2.setWordWrap(True)
        ht.addWidget(t1)
        ht.addWidget(t2)
        hl.addWidget(icon_lbl)
        hl.addLayout(ht, stretch=1)

        self._id = QLineEdit(row.id)
        self._id.setReadOnly(True)
        self._noms = QLineEdit(row.noms or "")
        self._date_n = QLineEdit(_fmt_dt(row.date_naissance))
        self._lieu_n = QLineEdit(row.lieu_naissance or "")
        self._grade = QComboBox()
        self._grade.setMinimumWidth(280)
        grades = [g.code for g in referentiel.grades()]
        _fill_combo(self._grade, grades, row.grade)
        self._date_grade = QLineEdit(_fmt_dt(row.date_grade))
        self._date_service = QLineEdit(row.date_service or "")
        self._ndecision = QLineEdit(row.ndecision or "")
        self._mat_mil = QLineEdit(row.matricule_militaire or "")
        self._mat_sol = QLineEdit(row.matricule_solde or "")
        self._padmin = QComboBox()
        _fill_combo(self._padmin, referentiel.position_admin_choices(), row.position_admin)
        self._fonction = QComboBox()
        _fill_combo(self._fonction, referentiel.fonctions_distinctes(), row.fonction)
        self._diplome = QLineEdit(row.diplome or "")
        self._date_obt = QLineEdit(row.date_obtention or "")
        self._structure = QComboBox()
        with QSignalBlocker(self._structure):
            self._structure.addItem("", None)
            cur_code = (row.structure or "").strip()
            found = False
            for s in referentiel.structures():
                label = f"{s.code} — {s.designation}" if s.designation else s.code
                self._structure.addItem(label, s.code)
                if s.code == cur_code:
                    self._structure.setCurrentIndex(self._structure.count() - 1)
                    found = True
            if cur_code and not found:
                self._structure.addItem(cur_code, cur_code)
                self._structure.setCurrentIndex(self._structure.count() - 1)
        self._sub_arme = QComboBox()
        self._fill_sub_arme_combo(preferred=row.subdivision_arme, allow_orphan_on_empty=True)
        self._structure.currentIndexChanged.connect(self._on_structure_changed)
        self._obs = QTextEdit(row.obs or "")
        self._obs.setMaximumHeight(80)
        self._ordre = QLineEdit("" if row.ordre_tri is None else str(row.ordre_tri))
        self._presseance = QLineEdit("" if row.presseance is None else str(row.presseance))

        for combo in (
            self._grade,
            self._padmin,
            self._fonction,
            self._sub_arme,
            self._structure,
        ):
            combo.setEditable(False)

        g_id = QGroupBox("Identité")
        f_id = QFormLayout(g_id)
        f_id.addRow("Id (lecture seule)", self._id)
        f_id.addRow("Noms et prénoms *", self._noms)
        f_id.addRow("Date de naissance", self._date_n)
        f_id.addRow("Lieu de naissance", self._lieu_n)
        f_id.addRow("Date de grade", self._date_grade)
        f_id.addRow("Diplôme", self._diplome)
        f_id.addRow("Date d'obtention", self._date_obt)

        g_af = QGroupBox("Affectation")
        f_af = QFormLayout(g_af)
        f_af.addRow("Structure", self._structure)
        f_af.addRow("Subdivision d'arme", self._sub_arme)
        f_af.addRow("Fonction", self._fonction)

        g_ad = QGroupBox("Statut & grade")
        f_ad = QFormLayout(g_ad)
        f_ad.addRow("Grade", self._grade)
        f_ad.addRow("Position adm. (Padmin)", self._padmin)
        f_ad.addRow("Date de service", self._date_service)
        f_ad.addRow("N° décision", self._ndecision)
        f_ad.addRow("Matricule militaire", self._mat_mil)
        f_ad.addRow("Matricule solde", self._mat_sol)
        f_ad.addRow("Ordre de tri", self._ordre)
        f_ad.addRow("Préséance", self._presseance)

        g_obs = QGroupBox("Observations")
        fo = QVBoxLayout(g_obs)
        fo.addWidget(self._obs)

        grid = QGridLayout()
        grid.addWidget(g_id, 0, 0)
        grid.addWidget(g_af, 0, 1)
        grid.addWidget(g_ad, 1, 0)
        grid.addWidget(g_obs, 1, 1)

        hint = QLabel(
            "Dates : AAAA-MM-JJ ou JJ/MM/AAAA. "
            "<b>Subdivision d'arme</b> : uniquement les valeurs déjà utilisées dans la liste générale "
            "pour la <b>structure</b> choisie (cohérence). "
            "Autres listes : référentiels Access ; valeur de fiche conservée si besoin. "
            "Enregistrement : Access + trace <code>data/personnel_audit.jsonl</code>."
        )
        hint.setObjectName("PersonnelEditHint")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)

        # Bannière d'erreurs de validation (cachée par défaut)
        self._error_bar = QLabel()
        self._error_bar.setObjectName("PersonnelEditErrorBar")
        self._error_bar.setWordWrap(True)
        self._error_bar.setTextFormat(Qt.TextFormat.RichText)
        self._error_bar.setStyleSheet(
            "background:#fff5f5; color:#c62828; border:1px solid #ffcdd2;"
            "border-left:4px solid #c62828; padding:8px 12px; border-radius:4px;"
        )
        self._error_bar.hide()

        # Mapping clé-champ → widget pour le surlignage
        self._field_widgets: dict[str, object] = {
            "noms": self._noms,
            "date_naissance": self._date_n,
            "date_grade": self._date_grade,
            "date_service": self._date_service,
            "date_obtention": self._date_obt,
            "matricule_militaire": self._mat_mil,
            "matricule_solde": self._mat_sol,
            "ordre_tri": self._ordre,
            "presseance": self._presseance,
        }

        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)
        save_btn = bb.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("Enregistrer")

        lay = QVBoxLayout(self)
        lay.addWidget(header)
        lay.addWidget(hint)
        lay.addWidget(scroll, stretch=1)
        lay.addWidget(self._error_bar)
        lay.addWidget(bb)

    def _structure_code(self) -> str:
        return (self._structure.currentData() or "").strip()

    def _sub_arme_items_for_structure(self, structure_code: str) -> list[str]:
        """SubArme déjà présents dans ListeGenerale pour ce code structure (cohérence métier)."""
        if not structure_code:
            return []
        return self._ref.subdivisions_arme_for_structure(structure_code)

    def _fill_sub_arme_combo(self, *, preferred: str | None, allow_orphan_on_empty: bool) -> None:
        """
        Remplit la liste SubArme à partir de la structure sélectionnée uniquement
        (ListeGenerale). Si des valeurs existent pour cette structure, seules elles sont proposées.
        Si aucune en base encore (nouvelle structure) : on peut garder la valeur de fiche au 1er chargement.
        """
        items = self._sub_arme_items_for_structure(self._structure_code())
        pref = (preferred or "").strip() or None
        if items:
            if pref and pref not in items:
                pref = None
        elif pref and not allow_orphan_on_empty:
            pref = None
        _fill_combo(self._sub_arme, items, pref)

    def _on_structure_changed(self) -> None:
        """Quand la structure change : ne garder le SubArme actuel que s'il est valide pour la nouvelle structure."""
        cur = (self._sub_arme.currentText() or "").strip()
        items = self._sub_arme_items_for_structure(self._structure_code())
        keep = cur if cur in items else None
        _fill_combo(self._sub_arme, items, keep)

    _INVALID_STYLE = "border: 1.5px solid #e53935; background: #fff8f8;"

    def _clear_validation(self) -> None:
        for w in self._field_widgets.values():
            if hasattr(w, "setStyleSheet"):
                w.setStyleSheet("")  # type: ignore[union-attr]
        self._error_bar.hide()
        self._error_bar.clear()

    def _show_field_errors(self, errors: list[FieldError]) -> None:
        for err in errors:
            w = self._field_widgets.get(err.field)
            if w and hasattr(w, "setStyleSheet"):
                w.setStyleSheet(self._INVALID_STYLE)  # type: ignore[union-attr]
        lines = "".join(f"<li><b>{e.label}</b> : {e.message}</li>" for e in errors)
        self._error_bar.setText(f"<ul style='margin:4px 0 0 0;padding-left:16px;'>{lines}</ul>")
        self._error_bar.show()

    def _on_save(self) -> None:
        self._clear_validation()

        errors = validate_personnel_fields(
            noms=self._noms.text(),
            date_naissance_str=self._date_n.text(),
            date_grade_str=self._date_grade.text(),
            date_service_str=self._date_service.text(),
            date_obtention_str=self._date_obt.text(),
            matricule_militaire=self._mat_mil.text(),
            matricule_solde=self._mat_sol.text(),
            ordre_tri_str=self._ordre.text(),
            presseance_str=self._presseance.text(),
        )
        if errors:
            self._show_field_errors(errors)
            return

        # Dates validées → parsing garanti sans erreur
        dn = _parse_dt_opt(self._date_n.text())
        dg = _parse_dt_opt(self._date_grade.text())
        ot = _parse_int_opt(self._ordre.text())
        pr = _parse_int_opt(self._presseance.text())
        noms = self._noms.text().strip()

        if not confirm_yes_no(
            self,
            "Enregistrer",
            "Enregistrer les modifications de cette fiche dans la base ?",
            default_no=False,
        ):
            return

        self._result = ListeGeneraleRow(
            id=self._original.id,
            noms=noms or None,
            date_naissance=dn,
            lieu_naissance=self._lieu_n.text().strip() or None,
            grade=_combo_text(self._grade),
            date_grade=dg,
            date_service=self._date_service.text().strip() or None,
            ndecision=self._ndecision.text().strip() or None,
            matricule_militaire=self._mat_mil.text().strip() or None,
            matricule_solde=self._mat_sol.text().strip() or None,
            position_admin=_combo_text(self._padmin),
            fonction=_combo_text(self._fonction),
            subdivision_arme=_combo_text(self._sub_arme),
            diplome=self._diplome.text().strip() or None,
            date_obtention=self._date_obt.text().strip() or None,
            structure=self._structure_code() or None,
            obs=self._obs.toPlainText().strip() or None,
            ordre_tri=ot,
            presseance=pr,
        )
        self.accept()

    def result_row(self) -> ListeGeneraleRow:
        assert self._result is not None
        return self._result
