"""Dialogue d’édition d’une ligne ListeGenerale — listes déroulantes référentiel + design institutionnel."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QFont, QGuiApplication, QShowEvent
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
    QPushButton,
    QScrollArea,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sexe import SEXE_COMBO_ITEMS, normalize_sexe
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.matricule_format import normalize_matricule_solde
from sme_gend.services.validators import FieldError, validate_personnel_fields
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox
from sme_gend.ui.widgets.date_input import FrenchDateEdit


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
    def __init__(
        self,
        parent,
        row: ListeGeneraleRow,
        referentiel: ReferentielService,
        *,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PersonnelEditDialog")
        self.setWindowTitle("Fiche personnel — modification")
        self.setModal(True)
        self.setMinimumSize(920, 640)
        self.resize(980, 720)
        if not row.access_join_id and (row.id or "").strip() in {"", "0"}:
            from sme_gend.models.personnel_identity import new_internal_personnel_key
            row = replace(row, access_join_id=new_internal_personnel_key())
        self._original = row
        self._result: ListeGeneraleRow | None = None
        self._pending_position_write = None
        self._ref = referentiel
        self._settings = settings

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
        t2 = QLabel(f"N° {row.id}")
        t2.setObjectName("PersonnelEditSubtitle")
        t2.setWordWrap(True)
        ht.addWidget(t1)
        ht.addWidget(t2)
        hl.addWidget(icon_lbl)
        hl.addLayout(ht, stretch=1)

        self._id = QLineEdit(row.id)
        self._id.setReadOnly(True)
        self._noms = QLineEdit(row.noms or "")
        self._date_n = FrenchDateEdit()
        self._date_n.set_from_datetime(row.date_naissance)
        self._lieu_n = QLineEdit(row.lieu_naissance or "")
        self._sexe = NoWheelComboBox()
        for code, label in SEXE_COMBO_ITEMS:
            self._sexe.addItem(label, code or None)
        cur_sexe = normalize_sexe(row.sexe)
        for i in range(self._sexe.count()):
            if self._sexe.itemData(i) == cur_sexe:
                self._sexe.setCurrentIndex(i)
                break
        self._telephone = QLineEdit(row.telephone or "")
        self._telephone.setPlaceholderText("Ex. +241 …")
        self._adresse_residence = QLineEdit(row.adresse_residence or "")
        self._adresse_residence.setPlaceholderText("Adresse postale ou résidence habituelle")
        self._grade = NoWheelComboBox()
        self._grade.setMinimumWidth(280)
        grades = [g.code for g in referentiel.grades()]
        _fill_combo(self._grade, grades, row.grade)
        self._date_grade = FrenchDateEdit()
        self._date_grade.set_from_datetime(row.date_grade)
        self._date_service = QLineEdit(row.date_service or "")
        self._date_service.setPlaceholderText("jj/mm/aaaa ou année")
        self._ndecision = QLineEdit(row.ndecision or "")
        self._mat_mil = QLineEdit(row.matricule_militaire or "")
        self._mat_sol = QLineEdit(normalize_matricule_solde(row.matricule_solde) or "")
        self._mat_sol.setPlaceholderText("Laisser vide si absent (/)")
        self._padmin = NoWheelComboBox()
        _fill_combo(self._padmin, referentiel.position_admin_choices(), row.position_admin)
        from sme_gend.services.structure_label_format import structure_combo_label

        self._structure = NoWheelComboBox()
        with QSignalBlocker(self._structure):
            self._structure.addItem("", None)
            cur_code = (row.structure or "").strip()
            found = False
            self._structures_by_code = {}
            for s in referentiel.structures():
                self._structures_by_code[(s.code or "").casefold()] = s
                label = structure_combo_label(s.code, s.designation)
                self._structure.addItem(label, s.code)
                if s.code == cur_code:
                    self._structure.setCurrentIndex(self._structure.count() - 1)
                    found = True
            if cur_code and not found:
                self._structure.addItem(cur_code, cur_code)
                self._structure.setCurrentIndex(self._structure.count() - 1)
        self._syncing_sub = False
        self._sub_arme = NoWheelComboBox()
        self._fonction = NoWheelComboBox()
        self._fill_sub_arme_combo(preferred=row.subdivision_arme, allow_orphan_on_empty=True)
        self._fill_fonction_combo(preferred=row.fonction)
        self._sub_arme.currentTextChanged.connect(self._on_subdivision_changed)
        self._diplome = QLineEdit(row.diplome or "")
        self._diplome.setPlaceholderText("Diplôme (aperçu liste)")
        self._diplome.setToolTip("Information issue du dossier. Pour retirer un diplôme ou sa date, utilisez Dossier agent → Diplômes ; l'historique n'est pas effacé depuis la liste mensuelle.")
        self._date_obt = FrenchDateEdit()
        self._date_obt.set_from_text(row.date_obtention)
        self._structure.currentIndexChanged.connect(self._on_structure_changed)
        self._obs = QTextEdit(row.obs or "")
        self._obs.setMaximumHeight(80)
        self._ordre = QLineEdit("" if row.ordre_tri is None else str(row.ordre_tri))
        self._presseance = QLineEdit("" if row.presseance is None else str(row.presseance))

        for combo in (
            self._grade,
            self._padmin,
            self._structure,
        ):
            combo.setEditable(False)
        self._sub_arme.setEditable(True)
        self._sub_arme.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        g_id = QGroupBox("Identité")
        f_id = QFormLayout(g_id)
        f_id.addRow("N°", self._id)
        f_id.addRow("Noms et prénoms *", self._noms)
        f_id.addRow("Date de naissance", self._date_n)
        f_id.addRow("Lieu de naissance", self._lieu_n)
        f_id.addRow("Sexe", self._sexe)
        f_id.addRow("Téléphone", self._telephone)
        f_id.addRow("Adresse de résidence", self._adresse_residence)
        f_id.addRow("Date de grade", self._date_grade)
        f_id.addRow("Diplôme", self._diplome)
        f_id.addRow("Date d'obtention", self._date_obt)

        g_af = QGroupBox("Affectation")
        f_af = QFormLayout(g_af)
        f_af.addRow("Structure", self._structure)
        f_af.addRow("Subdivision d'arme", self._sub_arme)
        fonct_row = QHBoxLayout()
        fonct_row.addWidget(self._fonction, stretch=1)
        self._btn_fonction_new = QPushButton("+")
        self._btn_fonction_new.setFixedWidth(32)
        self._btn_fonction_new.setToolTip("Créer une nouvelle fonction dans le référentiel.")
        self._btn_fonction_new.clicked.connect(self._on_new_fonction)
        self._btn_fonction_ref = QPushButton("…")
        self._btn_fonction_ref.setFixedWidth(32)
        self._btn_fonction_ref.setToolTip(
            "Modifier le libellé dans le référentiel et rattacher la fonction à la structure."
        )
        self._btn_fonction_ref.clicked.connect(self._on_manage_fonction)
        fonct_row.addWidget(self._btn_fonction_new)
        fonct_row.addWidget(self._btn_fonction_ref)
        fonct_wrap = QWidget()
        fonct_wrap.setLayout(fonct_row)
        f_af.addRow("Fonction", fonct_wrap)

        g_ad = QGroupBox("Statut & grade")
        f_ad = QFormLayout(g_ad)
        f_ad.addRow("Grade", self._grade)
        f_ad.addRow("Position administrative", self._padmin)
        f_ad.addRow("Date de service", self._date_service)
        f_ad.addRow("N° décision", self._ndecision)
        f_ad.addRow("Matricule militaire", self._mat_mil)
        f_ad.addRow("Matricule solde", self._mat_sol)
        f_ad.addRow("Ordre de tri", self._ordre)
        f_ad.addRow("Préséance", self._presseance)

        g_obs = QGroupBox("Observations")
        fo = QVBoxLayout(g_obs)
        fo.addWidget(self._obs)

        self._situation_host = QWidget()
        self._situation_layout = QVBoxLayout(self._situation_host)
        self._situation_layout.setContentsMargins(0, 0, 0, 0)
        self._situation_layout.setSpacing(0)

        grid = QGridLayout()
        grid.addWidget(self._situation_host, 0, 0, 1, 2)
        grid.addWidget(g_id, 1, 0)
        grid.addWidget(g_af, 1, 1)
        grid.addWidget(g_ad, 2, 0)
        grid.addWidget(g_obs, 2, 1)

        self._padmin.currentTextChanged.connect(self._refresh_situation_en_cours)
        self._structure.currentIndexChanged.connect(self._refresh_situation_en_cours)
        self._sub_arme.currentTextChanged.connect(self._refresh_situation_en_cours)
        self._refresh_situation_en_cours()

        hint = QLabel(
            "Dates : <b>jj/mm/aaaa</b> (chiffres ou calendrier). "
            "Diplômes et passations de grade : fiche dossier."
        )
        hint.setObjectName("PersonnelEditHint")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)

        # Bannière d'erreurs de validation (cachée par défaut)
        self._error_bar = QLabel()
        self._error_bar.setObjectName("PersonnelEditErrorBar")
        self._error_bar.setWordWrap(True)
        self._error_bar.setTextFormat(Qt.TextFormat.RichText)
        self._error_bar.hide()

        # Mapping clé-champ → widget pour le surlignage
        self._field_widgets: dict[str, object] = {
            "noms": self._noms,
            "sexe": self._sexe,
            "telephone": self._telephone,
            "adresse_residence": self._adresse_residence,
            "date_naissance": self._date_n.date_edit(),
            "date_grade": self._date_grade.date_edit(),
            "date_service": self._date_service,
            "date_obtention": self._date_obt.date_edit(),
            "matricule_militaire": self._mat_mil,
            "matricule_solde": self._mat_sol,
            "ordre_tri": self._ordre,
            "presseance": self._presseance,
            "fonction": self._fonction,
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

        self._apply_sensitive_field_locks()

    def _row_preview_for_situation(self) -> ListeGeneraleRow:
        return replace(
            self._original,
            position_admin=_combo_text(self._padmin),
            structure=self._structure_code() or None,
            subdivision_arme=_combo_text(self._sub_arme),
        )

    def _refresh_situation_en_cours(self) -> None:
        from sme_gend.ui.personnel_situation_panel import situation_en_cours_groupbox

        while self._situation_layout.count():
            item = self._situation_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        box = situation_en_cours_groupbox(
            self._row_preview_for_situation(),
            self._settings,
            parent=self._situation_host,
        )
        if box is None:
            self._situation_host.setVisible(False)
            return
        self._situation_layout.addWidget(box)
        self._situation_host.setVisible(True)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_available_screen()

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        margin = 16
        max_w = max(640, avail.width() - 2 * margin)
        max_h = max(480, avail.height() - 2 * margin)
        w = min(max(self.minimumWidth(), self.width()), max_w)
        h = min(max(self.minimumHeight(), self.height()), max_h)
        self.resize(w, h)
        geo = self.geometry()
        geo.moveCenter(avail.center())
        self.move(geo.topLeft())

    def _apply_sensitive_field_locks(self) -> None:
        from sme_gend.services.auth_guard import session_may_edit_age
        from sme_gend.services.sensitive_data_policy import session_may_edit_career_fields

        if not session_may_edit_age():
            self._date_n.setEnabled(False)
            self._date_n.setToolTip(
                "Modification réservée aux administrateurs "
                "(permission « Modifier l'âge / date de naissance »)."
            )
        if not session_may_edit_career_fields():
            self._date_service.setReadOnly(True)
            self._date_grade.setEnabled(False)
            self._date_obt.setEnabled(False)
            self._ndecision.setReadOnly(True)
            self._diplome.setReadOnly(True)

    def _structure_code(self) -> str:
        return (self._structure.currentData() or "").strip()

    def _sub_arme_items_for_structure(self, structure_code: str) -> list[str]:
        """SubArme pour la structure : RSA + carrière + suggestions hiérarchie T."""
        if not structure_code:
            return []
        return self._ref.subdivision_arme_choices_for_structure(structure_code)

    def _fonction_items_for_structure(self, structure_code: str) -> list[str]:
        if not structure_code:
            return []
        return self._ref.fonction_choices_for_structure(structure_code)

    def _fonction_value(self) -> str | None:
        if self._fonction.isEditable():
            text = self._fonction.currentText().strip()
            return text or None
        data = self._fonction.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        text = self._fonction.currentText().strip()
        return text or None

    def _liste_rows(self) -> list:
        if self._settings is None:
            return []
        try:
            from sme_gend.services.liste_generale_cache import cached_liste_rows

            return list(cached_liste_rows(self._settings))
        except Exception:
            return []

    def _occupants_fonction(self, structure_code: str) -> dict:
        from sme_gend.services.fonction_occupancy import occupants_by_fonction
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if not structure_code or structure_fonctions_non_liees(structure_code):
            return {}
        return occupants_by_fonction(
            self._liste_rows(),
            structure=structure_code,
            subdivision=self._subdivision_value(),
            exclude_row_id=self._original.id,
        )

    def _structure_row(self):
        code = self._structure_code()
        if not code:
            return None
        return getattr(self, "_structures_by_code", {}).get(code.casefold())

    def _fonction_structure_ok(self) -> str | None:
        sc = self._structure_code()
        if not sc:
            QMessageBox.information(
                self,
                "Fonction",
                "Choisissez d'abord une structure.",
            )
            return None
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(sc):
            QMessageBox.information(
                self,
                "Fonction",
                "Contrôle spécial et détachés : saisie libre, sans rattachement référentiel.",
            )
            return None
        return sc

    def _on_new_fonction(self) -> None:
        sc = self._fonction_structure_ok()
        if sc is None:
            return
        from sme_gend.ui.fonction_smart_dialog import open_fonction_smart_dialog

        st = self._structure_row()
        saved = open_fonction_smart_dialog(
            self,
            self._ref,
            libelle="",
            structure_code=sc,
            subdivision=self._subdivision_value(),
            structure_designation=st.designation if st else None,
            structure_famille=st.famille_unite if st else None,
        )
        if saved:
            self._fill_fonction_combo(preferred=saved)

    def _on_manage_fonction(self) -> None:
        sc = self._fonction_structure_ok()
        if sc is None:
            return
        from sme_gend.ui.fonction_smart_dialog import open_fonction_smart_dialog

        cur = self._fonction_value() or self._fonction.currentText().strip()
        if not cur:
            QMessageBox.information(self, "Fonction", "Saisissez ou sélectionnez une fonction.")
            return
        st = self._structure_row()
        saved = open_fonction_smart_dialog(
            self,
            self._ref,
            libelle=cur,
            structure_code=sc,
            subdivision=self._subdivision_value(),
            structure_designation=st.designation if st else None,
            structure_famille=st.famille_unite if st else None,
        )
        if saved:
            self._fill_fonction_combo(preferred=saved)

    def _fonction_choice_libelles(self, structure_code: str | None) -> list[str]:
        from sme_gend.services.fonction_structure_choices import build_fonction_choice_libelles

        code = (structure_code or "").strip()
        if not code:
            return []
        st = getattr(self, "_structures_by_code", {}).get(code.casefold())
        return build_fonction_choice_libelles(
            self._fonction_items_for_structure(code),
            structure_code=code,
            designation=st.designation if st else None,
            famille=st.famille_unite if st else None,
            subdivision=self._subdivision_value(),
        )

    def _fill_fonction_combo(self, *, preferred: str | None) -> None:
        from sme_gend.services.fonction_occupancy import fonction_choice_states
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees
        from sme_gend.ui.fonction_combo import fill_fonction_combo

        code = self._structure_code()
        pref = (preferred or "").strip() or None
        if structure_fonctions_non_liees(code):
            self._fonction.setEditable(True)
            self._fonction.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            items = [pref] if pref else []
            fill_fonction_combo(
                self._fonction,
                fonction_choice_states(items, {}, current=pref),
                current=pref,
            )
            self._fonction.setToolTip(
                "Contrôle spécial et détachés : la fonction n'est pas rattachée "
                "à la structure et n'est pas connue à l'avance. Saisie libre."
            )
            self._set_field_validation_state(self._fonction, invalid=False)
            return
        self._fonction.setEditable(False)
        items = self._fonction_choice_libelles(code)
        pref = (preferred or "").strip() or None
        orphan = bool(
            pref
            and not any(pref.casefold() == (x or "").strip().casefold() for x in items)
        )
        if orphan:
            items = [pref, *items]
        occupants = self._occupants_fonction(code)
        states = fonction_choice_states(
            items,
            occupants,
            current=pref,
            structure_code=code,
            subdivision=self._subdivision_value(),
        )
        fill_fonction_combo(self._fonction, states, current=pref)
        taken = next((s for s in states if s.libelle == pref and not s.enabled), None)
        if taken:
            self._fonction.setToolTip(taken.tooltip)
            self._set_field_validation_state(self._fonction, invalid=True)
        elif orphan:
            self._fonction.setToolTip(
                "Fonction hors liste proposée. Utilisez « … » pour rattacher ou choisissez "
                "un poste qualifié (CHEF DE …)."
            )
            self._set_field_validation_state(self._fonction, invalid=True)
        else:
            self._fonction.setToolTip(
                "Postes de tête attendus et fonctions rattachées à la structure. "
                "Une fonction déjà tenue dans la même subdivision n'est pas sélectionnable."
            )
            self._set_field_validation_state(self._fonction, invalid=False)

    def _fill_sub_arme_combo(self, *, preferred: str | None, allow_orphan_on_empty: bool) -> None:
        """
        Remplit SubArme depuis le référentiel (RSA + carrière + hiérarchie T).
        Combo éditable : saisie libre autorisée (résolution FK à l'enregistrement).
        """
        self._syncing_sub = True
        try:
            items = self._sub_arme_items_for_structure(self._structure_code())
            pref = (preferred or "").strip() or None
            if pref and pref not in items:
                items = [pref, *items]
            _fill_combo(self._sub_arme, items, pref)
        finally:
            self._syncing_sub = False

    def _subdivision_value(self) -> str | None:
        box = getattr(self, "_sub_arme", None)
        if box is None:
            return (self._original.subdivision_arme or "").strip() or None
        return _combo_text(box)

    def _on_subdivision_changed(self, _text: str) -> None:
        if self._syncing_sub:
            return
        self._fill_fonction_combo(preferred=self._fonction_value())

    def _on_structure_changed(self) -> None:
        """Structure changée : recharger SubArme et fonctions autorisées."""
        cur = (self._sub_arme.currentText() or "").strip()
        items = self._sub_arme_items_for_structure(self._structure_code())
        keep = cur if cur in items else (cur if cur else None)
        if keep and keep not in items:
            items = [keep, *items]
        self._syncing_sub = True
        try:
            _fill_combo(self._sub_arme, items, keep)
        finally:
            self._syncing_sub = False
        self._fill_fonction_combo(preferred=self._fonction_value())

    def _set_field_validation_state(self, widget: object, *, invalid: bool) -> None:
        from sme_gend.ui.theme import repolish_widget

        if not hasattr(widget, "setProperty"):
            return
        widget.setProperty("validationState", "invalid" if invalid else "")  # type: ignore[union-attr]
        repolish_widget(widget)  # type: ignore[arg-type]

    def _clear_validation(self) -> None:
        for w in self._field_widgets.values():
            self._set_field_validation_state(w, invalid=False)
        self._error_bar.hide()
        self._error_bar.clear()

    def _show_field_errors(self, errors: list[FieldError]) -> None:
        for err in errors:
            w = self._field_widgets.get(err.field)
            if w is not None:
                self._set_field_validation_state(w, invalid=True)
        lines = "".join(f"<li><b>{e.label}</b> : {e.message}</li>" for e in errors)
        self._error_bar.setText(f"<ul style='margin:4px 0 0 0;padding-left:16px;'>{lines}</ul>")
        self._error_bar.show()

    def _on_save(self) -> None:
        self._pending_position_write = None
        self._clear_validation()

        errors = validate_personnel_fields(
            noms=self._noms.text(),
            date_naissance_str=self._date_n.text_for_validation(),
            date_grade_str=self._date_grade.text_for_validation(),
            date_service_str=self._date_service.text(),
            date_obtention_str=self._date_obt.text_for_validation(),
            matricule_militaire=self._mat_mil.text(),
            matricule_solde=normalize_matricule_solde(self._mat_sol.text()) or "",
            ordre_tri_str=self._ordre.text(),
            presseance_str=self._presseance.text(),
        )
        if errors:
            self._show_field_errors(errors)
            return

        # Dates validées → parsing garanti sans erreur
        dn = self._date_n.to_datetime()
        dg = self._date_grade.to_datetime()
        new_grade = _combo_text(self._grade)
        orig_grade = (self._original.grade or "").strip()
        if new_grade and new_grade.casefold() != orig_grade.casefold() and dg is None:
            self._show_field_errors(
                [
                    FieldError(
                        "date_grade",
                        "Date de grade",
                        "La date de nomination au grade est obligatoire lors d'un changement de grade.",
                    ),
                ]
            )
            return
        dob_str = self._date_obt.to_storage_str()
        ot = _parse_int_opt(self._ordre.text())
        pr = _parse_int_opt(self._presseance.text())
        noms = self._noms.text().strip()
        sexe = normalize_sexe(self._sexe.currentData())

        new_padmin = _combo_text(self._padmin)
        sc = self._structure_code()
        fn = self._fonction_value()
        if sc and fn:
            from sme_gend.services.fonction_structure_choices import (
                canonical_fonction_libelle,
                fonction_acceptable_for_assignment,
            )
            from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

            st = self._structure_row()
            sub = self._subdivision_value()
            fn = canonical_fonction_libelle(
                fn,
                structure_code=sc,
                designation=st.designation if st else None,
                famille=st.famille_unite if st else None,
                subdivision=sub,
            )
            orig_fn = (self._original.fonction or "").strip()
            if (
                not structure_fonctions_non_liees(sc)
                and not fonction_acceptable_for_assignment(
                    fn,
                    linked_libelles=self._fonction_items_for_structure(sc),
                    structure_code=sc,
                    designation=st.designation if st else None,
                    famille=st.famille_unite if st else None,
                    subdivision=sub,
                )
                and fn.casefold() != orig_fn.casefold()
            ):
                self._show_field_errors(
                    [
                        FieldError(
                            "fonction",
                            "Fonction",
                            "Cette fonction n'est pas proposée pour cette structure.",
                        ),
                    ]
                )
                return
            from sme_gend.services.fonction_occupancy import (
                occupancy_message,
                other_occupants,
                same_fonction_poste,
            )

            sub = self._subdivision_value()
            if not same_fonction_poste(
                self._original.structure,
                self._original.fonction,
                sc,
                fn,
                self._original.subdivision_arme,
                sub,
            ):
                found = other_occupants(
                    self._liste_rows(),
                    structure=sc,
                    fonction=fn,
                    subdivision=sub,
                    exclude_row_id=self._original.id,
                )
                if found:
                    self._show_field_errors(
                        [
                            FieldError(
                                "fonction",
                                "Fonction",
                                occupancy_message(found, fn),
                            ),
                        ]
                    )
                    return

        self._result = replace(self._original,
            id=self._original.id,
            noms=noms or None,
            date_naissance=dn,
            lieu_naissance=self._lieu_n.text().strip() or None,
            sexe=sexe,
            telephone=self._telephone.text().strip() or None,
            adresse_residence=self._adresse_residence.text().strip() or None,
            grade=_combo_text(self._grade),
            date_grade=dg,
            date_service=self._date_service.text().strip() or None,
            ndecision=self._ndecision.text().strip() or None,
            matricule_militaire=self._mat_mil.text().strip() or None,
            matricule_solde=normalize_matricule_solde(self._mat_sol.text()),
            position_admin=new_padmin,
            fonction=fn,
            subdivision_arme=_combo_text(self._sub_arme),
            diplome=self._diplome.text().strip() or None,
            date_obtention=dob_str,
            structure=self._structure_code() or None,
            obs=self._obs.toPlainText().strip() or None,
            ordre_tri=ot,
            presseance=pr,
        )
        if self._settings is not None:
            from sme_gend.services.padmin_satellite_apply import prepare_position_change
            prepared = prepare_position_change(
                self, self._settings, self._result,
                old_padmin=self._original.position_admin, new_padmin=new_padmin,
            )
            if prepared is False:
                return
            self._pending_position_write = prepared if callable(prepared) else None
        self.accept()

    def apply_prepared_writes(self, saved_row: ListeGeneraleRow) -> None:
        """The owner saves personnel, catalog links and position in one transaction."""
        from sme_gend.repositories.mysql_connection import has_import_transaction
        if self._settings is None or not has_import_transaction(self._settings):
            raise RuntimeError("Enregistrement du formulaire sans transaction partagée.")
        sc, fn = saved_row.structure, saved_row.fonction
        if sc and fn:
            from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees
            if not structure_fonctions_non_liees(sc):
                self._ref.fonction_ensure_catalog_and_link(sc, fn)
        if self._pending_position_write is not None:
            self._pending_position_write(saved_row)

    def result_row(self) -> ListeGeneraleRow:
        assert self._result is not None
        return self._result
