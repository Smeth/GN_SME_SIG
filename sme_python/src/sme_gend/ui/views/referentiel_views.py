"""Vues référentiels — lecture / édition Structures et Grades (Access)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.ref_table_model import RefTableModel


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


class _StructureDialog(QDialog):
    def __init__(self, parent, row: StructureRow | None = None) -> None:
        super().__init__(parent)
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
        self._hint.setText(
            "<b>Structures ↔ subdivisions d'arme</b> — couples distincts "
            "(Structure, SubArme) issus de la liste générale. Lecture seule."
        )
        self.reload()

    def reload(self) -> None:
        try:
            pairs = self._service.structure_subdivision_pairs()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [[a, b] for a, b in pairs]
        self._model.set_data(["Structure (code)", "Subdivision d'arme"], data)


class StructuresView(QWidget):
    """TabStructure — INSERT / UPDATE / DELETE."""

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
        self._table.selectionModel().currentRowChanged.connect(
            self._on_structure_selection_changed
        )

        self._hint = QLabel(
            "<b>Structures</b> (TabStructure) — les modifications sont écrites dans la base Access. "
            "Sélectionnez une ligne pour voir les <b>subdivisions d'arme</b> (SubArme) "
            "rattachées dans ListeGenerale."
        )
        self._hint.setWordWrap(True)

        self._sub_hint = QLabel(
            "<b>Subdivisions d'arme</b> pour la structure sélectionnée "
            "(valeurs distinctes SubArme dans ListeGenerale où Structure = code) :"
        )
        self._sub_hint.setWordWrap(True)
        self._sub_list = QListWidget()
        self._sub_list.setAlternatingRowColors(True)

        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouvelle structure…")
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
        lay.addWidget(self._table, stretch=2)
        lay.addWidget(self._sub_hint)
        lay.addWidget(self._sub_list, stretch=1)
        self.reload()

    def reload(self) -> None:
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
        self._load_subdivisions_for_current()

    def _selected_row(self) -> int:
        idx = self._table.selectionModel().currentIndex()
        return idx.row()

    def _on_structure_selection_changed(self, _current, _previous) -> None:
        self._load_subdivisions_for_current()

    def _load_subdivisions_for_current(self) -> None:
        self._sub_list.clear()
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            return
        code = self._rows[row].code
        try:
            subs = self._service.subdivisions_arme_for_structure(code)
        except Exception as e:
            QMessageBox.critical(self, "Subdivisions", str(e))
            return
        for s in subs:
            self._sub_list.addItem(s)

    def _on_add(self) -> None:
        dlg = _StructureDialog(self, None)
        if dlg.exec() != QDialog.Accepted:
            return
        code, designation, ordre = dlg.values()
        if not code or not designation:
            QMessageBox.warning(self, "Structure", "Code et désignation sont obligatoires.")
            return
        try:
            self._service.structure_insert(code, designation, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_edit(self) -> None:
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
        try:
            self._service.structure_update(code, designation, ordre)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Structure", "Sélectionnez une ligne.")
            return
        code = self._rows[row].code
        if (
            QMessageBox.question(
                self,
                "Supprimer",
                f"Supprimer la structure « {code} » ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
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
        self._hint.setText(
            "<b>Subdivisions</b> — hiérarchie Secun → Secdeux → Sectrois (jointure). "
            "L’édition des trois tables liées sera ajoutée dans une version ultérieure."
        )
        self.reload()

    def reload(self) -> None:
        try:
            flat = self._service.subdivisions_flat()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.code_u, r.design_u, r.code_d, r.design_d, r.code_t, r.design_t]
            for r in flat
        ]
        self._model.set_data(
            ["Code U", "Niveau 1", "Code D", "Niveau 2", "Code T", "Niveau 3"],
            data,
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

        self._hint = QLabel(
            "<b>Grades</b> (TabGrade) — ordre de préséance ; enregistrement dans Access."
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

    def _selected_row(self) -> int:
        idx = self._table.selectionModel().currentIndex()
        return idx.row()

    def _on_add(self) -> None:
        dlg = _GradeDialog(self, None)
        if dlg.exec() != QDialog.Accepted:
            return
        code, libelle, pref, eff = dlg.values()
        if not code or not libelle:
            QMessageBox.warning(self, "Grade", "Code et libellé sont obligatoires.")
            return
        try:
            self._service.grade_insert(code, libelle, pref, eff)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_edit(self) -> None:
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
        try:
            self._service.grade_update(code, libelle, pref, eff)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self.reload()

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Grade", "Sélectionnez une ligne.")
            return
        code = self._rows[row].code
        if (
            QMessageBox.question(
                self,
                "Supprimer",
                f"Supprimer le grade « {code} » ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
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
        self._hint.setText(
            "<b>Fonctions</b> — valeurs distinctes dans ListeGenerale.Fonction. "
            "Sans table dédiée, l’édition se fait via la liste générale ou une future table."
        )
        self.reload()

    def reload(self) -> None:
        try:
            fns = self._service.fonctions_distinctes()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [[f] for f in fns]
        self._model.set_data(["Fonction"], data)
