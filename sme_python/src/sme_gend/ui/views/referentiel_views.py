"""Vues référentiels — lecture / édition Structures, Grades, Positions administratives."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.ref_table_model import RefTableModel
from sme_gend.ui.structure_subdivisions_dialog import StructureSubdivisionsDialog


class _RefBase(QWidget):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)

        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)

        self._hint = QLabel()
        self._hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addWidget(self._btn)
        lay.addWidget(self._table)

    def reload(self) -> None:
        raise NotImplementedError


def _parse_int_opt(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    return int(s)


def _readonly_detail_dialog(parent: QWidget, title: str, fields: list[tuple[str, str]]) -> None:
    """Petite fenêtre modale lecture seule (détail référentiel)."""
    d = QDialog(parent)
    d.setObjectName("RefEditDialog")
    d.setWindowTitle(title)
    d.setModal(True)
    d.setMinimumWidth(400)
    form = QFormLayout()
    for label, value in fields:
        le = QLineEdit(value)
        le.setReadOnly(True)
        form.addRow(label, le)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    bb.accepted.connect(d.accept)
    lay = QVBoxLayout(d)
    lay.addLayout(form)
    lay.addWidget(bb)
    d.exec()


class _StructureDialog(QDialog):
    def __init__(self, parent, row: StructureRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle("Structure" if row is None else "Modifier la structure")
        self._code = QLineEdit()
        self._designation = QLineEdit()
        self._ordre = QLineEdit()
        self._code.setPlaceholderText("Code unique")
        self._designation.setPlaceholderText("Désignation")
        self._ordre.setPlaceholderText("Ordre de tri (vide = aucun)")
        if row:
            self._code.setText(row.code)
            self._code.setReadOnly(True)
            self._designation.setText(row.designation)
            self._ordre.setText("" if row.ordre_tri is None else str(row.ordre_tri))
        form = QFormLayout()
        form.addRow("Code *", self._code)
        form.addRow("Désignation *", self._designation)
        form.addRow("Ordre tri", self._ordre)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def values(self) -> tuple[str, str, int | None]:
        ordre = self._ordre.text().strip()
        ot: int | None = None
        if ordre:
            ot = int(ordre)
        return (self._code.text().strip(), self._designation.text().strip(), ot)


class _GradeDialog(QDialog):
    def __init__(self, parent, row: GradeRefRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle("Grade" if row is None else "Modifier le grade")
        self._code = QLineEdit()
        self._libelle = QLineEdit()
        self._pref = QLineEdit()
        self._eff = QLineEdit()
        if row:
            self._code.setText(row.code)
            self._code.setReadOnly(True)
            self._libelle.setText(row.libelle)
            self._pref.setText("" if row.pref_ord is None else str(row.pref_ord))
            self._eff.setText("" if row.effectif_g is None else str(row.effectif_g))
        form = QFormLayout()
        form.addRow("Code *", self._code)
        form.addRow("Libellé *", self._libelle)
        form.addRow("Préf. ordre", self._pref)
        form.addRow("EffectifG", self._eff)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def values(self) -> tuple[str, str, int | None, int | None]:
        po = self._pref.text().strip()
        eg = self._eff.text().strip()
        return (
            self._code.text().strip(),
            self._libelle.text().strip(),
            _parse_int_opt(po),
            _parse_int_opt(eg),
        )


class StructureSubdivisionView(_RefBase):
    """Couples (Structure, SubArme) depuis ListeGenerale."""

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._pairs: list[tuple[str, str]] = []
        self._hint.setText(
            "Couples distincts <b>(Structure, SubArme)</b> dans la liste générale — lecture seule. "
            "<b>Double-clic</b> : détail."
        )
        self._table.doubleClicked.connect(self._on_double_click)
        self.reload()

    def reload(self) -> None:
        try:
            pairs = self._service.structure_subdivision_pairs()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        self._pairs = list(pairs)
        data = [[a, b] for a, b in pairs]
        self._model.set_data(["Structure (code)", "Subdivision d'arme"], data)

    def _on_double_click(self) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        if row < 0 or row >= len(self._pairs):
            return
        a, b = self._pairs[row]
        _readonly_detail_dialog(
            self,
            "Couple structure / subdivision d'arme",
            [("Structure (code)", a), ("Subdivision d'arme (SubArme)", b)],
        )


class StructuresView(QWidget):
    """Structures : TabStructure, couples structure/SubArme, hiérarchie subdivisions (tout regroupé)."""

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[StructureRow] = []
        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._open_subdivisions_modal)

        self._hint = QLabel(
            "<b>Référentiel codes structures</b> (table des codes, libellés, ordre). "
            "Onglets : codes officiels ; couples structure / subdivision d’arme dans la liste ; "
            "hiérarchie subdivisions. Pour <b>effectifs par structure</b>, utilisez "
            "<b>Effectifs du mois → Effectifs par structure</b>."
        )
        self._hint.setWordWrap(True)

        self._btn = QPushButton("Actualiser tout")
        self._btn.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouvelle structure…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_sub = QPushButton("Subdivisions d'arme…")
        self._btn_sub.setToolTip("SubArme distincts dans ListeGenerale pour la structure sélectionnée")
        self._btn_sub.clicked.connect(self._open_subdivisions_modal)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        bar = QHBoxLayout()
        bar.addWidget(self._btn)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_sub)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)
        bar.addStretch()

        page_main = QWidget()
        lay_main = QVBoxLayout(page_main)
        lay_main.setContentsMargins(0, 0, 0, 0)
        lay_main.addLayout(bar)
        lay_main.addWidget(self._table, stretch=1)

        self._pairs_panel = StructureSubdivisionView(service, self)
        self._hierarchy_panel = SubdivisionsView(service, self)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(page_main, "Structures (TabStructure)")
        self._tabs.addTab(self._pairs_panel, "Couples structure ↔ SubArme")
        self._tabs.addTab(self._hierarchy_panel, "Hiérarchie subdivisions")

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addWidget(self._tabs, stretch=1)
        self.reload()

    def reload(self) -> None:
        self._reload_main_table()
        self._pairs_panel.reload()
        self._hierarchy_panel.reload()

    def apply_runtime_after_config_change(self) -> None:
        self.reload()

    def _reload_main_table(self) -> None:
        try:
            self._rows = self._service.structures()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.code, r.designation, str(r.ordre_tri if r.ordre_tri is not None else "")]
            for r in self._rows
        ]
        self._model.set_data(["Code", "Désignation", "Ordre tri"], data)
        if self._rows:
            self._table.selectRow(0)

    def _selected_row(self) -> int:
        idx = self._table.selectionModel().currentIndex()
        return idx.row()

    def _open_subdivisions_modal(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Structures", "Sélectionnez une structure.")
            return
        st = evaluate_editing_state(self._service.settings)
        dlg = StructureSubdivisionsDialog(
            self,
            self._service,
            self._rows[row],
            on_structure_changed=self.reload,
            allow_edit=not st.locked,
        )
        dlg.exec()

    def _on_add(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        dlg = _StructureDialog(self, None)
        if dlg.exec() != QDialog.Accepted:
            return
        code, designation, ordre = dlg.values()
        if not code or not designation:
            QMessageBox.warning(self, "Structure", "Code et désignation sont obligatoires.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Créer la structure « {code} » dans TabStructure (Access) ?",
            default_no=True,
        ):
            return
        try:
            self._service.structure_insert(code, designation, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_edit(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Structure", "Sélectionnez une ligne.")
            return
        dlg = _StructureDialog(self, self._rows[row])
        if dlg.exec() != QDialog.Accepted:
            return
        code, designation, ordre = dlg.values()
        if not designation:
            QMessageBox.warning(self, "Structure", "La désignation est obligatoire.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Enregistrer les modifications de la structure « {code} » dans Access ?",
            default_no=True,
        ):
            return
        try:
            self._service.structure_update(code, designation, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_delete(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Structure", "Sélectionnez une ligne.")
            return
        code = self._rows[row].code
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer définitivement la structure « {code} » de TabStructure ?",
            default_no=True,
        ):
            return
        try:
            self._service.structure_delete(code)
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self.reload()


class SubdivisionsView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._flat_rows: list[SubdivisionFlatRow] = []
        self._hint.setText(
            "Référentiel <b>Secun → Secdeux → Sectrois</b> (SQLite ou Access selon le mode). Lecture seule. "
            "<b>Double-clic</b> : détail. L’édition des tables pourra être ajoutée ultérieurement."
        )
        self._table.doubleClicked.connect(self._on_double_click)
        self.reload()

    def reload(self) -> None:
        try:
            flat = self._service.subdivisions_flat()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        self._flat_rows = list(flat)
        data = [
            [r.code_u, r.design_u, r.code_d, r.design_d, r.code_t, r.design_t]
            for r in flat
        ]
        self._model.set_data(
            ["Code U", "Niveau 1", "Code D", "Niveau 2", "Code T", "Niveau 3"],
            data,
        )

    def _on_double_click(self) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        if row < 0 or row >= len(self._flat_rows):
            return
        r = self._flat_rows[row]
        _readonly_detail_dialog(
            self,
            f"Subdivision — {r.code_t}",
            [
                ("Code niveau 1 (U)", r.code_u),
                ("Niveau 1", r.design_u),
                ("Code niveau 2 (D)", r.code_d),
                ("Niveau 2", r.design_d),
                ("Code niveau 3 (T)", r.code_t),
                ("Niveau 3", r.design_t),
            ],
        )


class GradesView(QWidget):
    """TabGrade — INSERT / UPDATE / DELETE."""

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[GradeRefRow] = []
        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._on_edit)

        self._hint = QLabel(
            "<b>Grades</b> (TabGrade) — ordre de préséance ; enregistrement dans Access. "
            "<b>Double-clic</b> : modifier."
        )
        self._hint.setWordWrap(True)

        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouveau grade…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        bar = QHBoxLayout()
        bar.addWidget(self._btn)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addLayout(bar)
        lay.addWidget(self._table)
        self.reload()

    def reload(self) -> None:
        try:
            self._rows = self._service.grades()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.code, r.libelle, str(r.pref_ord if r.pref_ord is not None else ""), str(r.effectif_g or "")]
            for r in self._rows
        ]
        self._model.set_data(["Code", "Grade", "Préf. ordre", "Effectif"], data)

    def apply_runtime_after_config_change(self) -> None:
        self.reload()

    def _selected_row(self) -> int:
        idx = self._table.selectionModel().currentIndex()
        return idx.row()

    def _on_add(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        dlg = _GradeDialog(self, None)
        if dlg.exec() != QDialog.Accepted:
            return
        code, libelle, pref, eff = dlg.values()
        if not code or not libelle:
            QMessageBox.warning(self, "Grade", "Code et libellé sont obligatoires.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Créer le grade « {code} » dans TabGrade (Access) ?",
            default_no=True,
        ):
            return
        try:
            self._service.grade_insert(code, libelle, pref, eff)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_edit(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Grade", "Sélectionnez une ligne.")
            return
        dlg = _GradeDialog(self, self._rows[row])
        if dlg.exec() != QDialog.Accepted:
            return
        code, libelle, pref, eff = dlg.values()
        if not libelle:
            QMessageBox.warning(self, "Grade", "Le libellé est obligatoire.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Enregistrer les modifications du grade « {code} » dans Access ?",
            default_no=True,
        ):
            return
        try:
            self._service.grade_update(code, libelle, pref, eff)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_delete(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Grade", "Sélectionnez une ligne.")
            return
        code = self._rows[row].code
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer définitivement le grade « {code} » de TabGrade ?",
            default_no=True,
        ):
            return
        try:
            self._service.grade_delete(code)
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self.reload()


class FonctionsView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._fnames: list[str] = []
        self._hint.setText(
            "<b>Fonctions</b> — valeurs distinctes dans ListeGenerale.Fonction. "
            "Pas de table dédiée : modifier une affectation depuis la <b>liste générale</b> (fiche personnel). "
            "<b>Double-clic</b> : détail."
        )
        self._table.doubleClicked.connect(self._on_double_click)
        self.reload()

    def reload(self) -> None:
        try:
            fns = self._service.fonctions_distinctes()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        self._fnames = list(fns)
        data = [[f] for f in fns]
        self._model.set_data(["Fonction"], data)

    def apply_runtime_after_config_change(self) -> None:
        self.reload()

    def _on_double_click(self) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        if row < 0 or row >= len(self._fnames):
            return
        fn = self._fnames[row]
        d = QDialog(self)
        d.setObjectName("RefEditDialog")
        d.setWindowTitle("Fonction")
        d.setModal(True)
        d.setMinimumWidth(420)
        lay = QVBoxLayout(d)
        info = QLabel(
            "Cette valeur provient des fiches personnel (ListeGenerale.Fonction). "
            "Pour la modifier sur un individu, ouvrez la <b>liste générale</b> et éditez la ligne concernée."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)
        le = QLineEdit(fn)
        le.setReadOnly(True)
        form = QFormLayout()
        form.addRow("Fonction", le)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(d.accept)
        lay.addWidget(bb)
        d.exec()


# ── Positions administratives ─────────────────────────────────────────────────

class _PadminDialog(QDialog):
    """Dialogue de création / modification d'une position administrative."""

    def __init__(self, parent: QWidget, row: PadminRefRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle(
            "Nouvelle position administrative" if row is None else "Modifier la position"
        )
        self.setMinimumWidth(400)

        self._libelle = QLineEdit()
        self._libelle.setPlaceholderText("Libellé exact (ex : Présent)")
        self._ordre = QLineEdit()
        self._ordre.setPlaceholderText("Entier (ex : 1)")
        self._present = QCheckBox("Compte comme présent (inclus dans les effectifs présents)")

        if row is not None:
            self._libelle.setText(row.libelle)
            self._ordre.setText("" if row.ordre_tri is None else str(row.ordre_tri))
            self._present.setChecked(row.compte_comme_present)

        form = QFormLayout()
        form.addRow("Libellé *", self._libelle)
        form.addRow("Ordre de tri", self._ordre)
        form.addRow("", self._present)

        note = QLabel(
            "<i>Le libellé doit correspondre exactement à la valeur saisie dans les fiches "
            "personnel (respect des majuscules et accents).</i>"
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(bb)

    def values(self) -> tuple[str, bool, int | None]:
        lib = self._libelle.text().strip()
        ord_txt = self._ordre.text().strip()
        ord_val: int | None = None
        if ord_txt:
            try:
                ord_val = int(ord_txt)
            except ValueError:
                pass
        return lib, self._present.isChecked(), ord_val


class PadminView(QWidget):
    """Référentiel des positions administratives (tab_padmin — SQLite uniquement)."""

    def __init__(self, service: ReferentielService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[PadminRefRow] = []

        self._hint = QLabel(
            "<b>Positions administratives</b> — référentiel <code>tab_padmin</code> (mode SQLite). "
            "La colonne <b>Présent</b> détermine quels personnels apparaissent dans la liste des "
            "présents et le numérique. "
            "<b>Double-clic</b> ou bouton Modifier pour éditer. "
            "<i>Disponible uniquement en mode SQLite.</i>"
        )
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)

        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._on_edit)

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouvelle position…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        self.reload()

    def reload(self) -> None:
        try:
            self._rows = self._service.padmins()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.libelle, "Oui" if r.compte_comme_present else "Non",
             str(r.ordre_tri) if r.ordre_tri is not None else ""]
            for r in self._rows
        ]
        self._model.set_data(["Position administrative", "Compte comme présent", "Ordre"], data)

    def apply_runtime_after_config_change(self) -> None:
        self.reload()

    def _selected_row(self) -> int:
        idx = self._table.selectionModel().currentIndex()
        return idx.row()

    def _require_sqlite(self) -> bool:
        if not self._service.settings.use_sqlite_liste_generale:
            QMessageBox.information(
                self,
                "Mode SQLite requis",
                "La gestion des positions administratives nécessite le mode SQLite.\n"
                "Activez-le dans Paramètres & données.",
            )
            return False
        return True

    def _on_add(self) -> None:
        if not self._require_sqlite():
            return
        dlg = _PadminDialog(self, None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        libelle, compte, ordre = dlg.values()
        if not libelle:
            QMessageBox.warning(self, "Position admin.", "Le libellé est obligatoire.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Créer la position « {libelle} » dans le référentiel ?",
            default_no=True,
        ):
            return
        try:
            self._service.padmin_insert(libelle, compte, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_edit(self) -> None:
        if not self._require_sqlite():
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Position admin.", "Sélectionnez une ligne.")
            return
        current = self._rows[row]
        dlg = _PadminDialog(self, current)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_libelle, compte, ordre = dlg.values()
        if not new_libelle:
            QMessageBox.warning(self, "Position admin.", "Le libellé est obligatoire.")
            return
        msg = f"Enregistrer les modifications de « {current.libelle} »"
        if new_libelle != current.libelle:
            msg += (
                f"\n\nAttention : le libellé change de « {current.libelle} » vers « {new_libelle} ».\n"
                "Toutes les fiches personnel utilisant l'ancien libellé seront mises à jour."
            )
        if not confirm_yes_no(self, "Confirmer", msg, default_no=True):
            return
        try:
            self._service.padmin_update(current.libelle, new_libelle, compte, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_delete(self) -> None:
        if not self._require_sqlite():
            return
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Position admin.", "Sélectionnez une ligne.")
            return
        libelle = self._rows[row].libelle
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer « {libelle} » du référentiel ?\n\n"
            "Les fiches personnel conservent leur valeur actuelle mais elle ne sera plus "
            "référencée dans tab_padmin.",
            default_no=True,
        ):
            return
        try:
            self._service.padmin_delete(libelle)
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self.reload()
