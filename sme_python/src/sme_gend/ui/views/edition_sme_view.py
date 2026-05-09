"""Vue Edition SME — modification rapide en ligne avec filtres, actions et édition en masse."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow

_SETTINGS_ORG = "SME-Gendarmerie"
_SETTINGS_APP = "EditionSME"

# ── Colonnes ──────────────────────────────────────────────────────────────────
_COLS: list[tuple[str, str, str]] = [
    ("id",                  "N°",                "readonly"),
    ("noms",                "Nom et prénom",      "text"),
    ("grade",               "Grade",              "combo"),
    ("structure",           "Structure",          "combo_ed"),
    ("subdivision_arme",    "Subdivision d'arme", "combo_ed"),
    ("position_admin",      "Position admin.",    "combo_ed"),
    ("fonction",            "Fonction",           "text"),
    ("matricule_militaire", "Mat. militaire",     "text"),
    ("matricule_solde",     "Mat. solde",         "text"),
]

_EDITABLE: set[int] = {i for i, c in enumerate(_COLS) if c[2] != "readonly"}
_COL_IDX: dict[str, int] = {c[0]: i for i, c in enumerate(_COLS)}
_COL_STRUCTURE   = _COL_IDX["structure"]
_COL_SUBDIVISION = _COL_IDX["subdivision_arme"]

_MAX_UNDO = 20
_COLOR_MODIFIED   = QColor("#d4edda")  # vert  — modifié ce chargement
_COLOR_INCOMPLETE = QColor("#fff3cd")  # jaune — grade ou structure manquant
_COLOR_DUP_FG     = QColor("#c0392b")  # rouge — matricule en doublon (texte)


# ── Proxy filtrant ────────────────────────────────────────────────────────────

class _EditionFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text            = ""
        self._structure       = ""
        self._grade           = ""
        self._padmin          = ""
        self._duplicates_only = False
        self._incomplete_only = False
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_text(self, v: str)           -> None: self._text            = v.strip().upper(); self.invalidateFilter()
    def set_structure(self, v: str)      -> None: self._structure       = v;                  self.invalidateFilter()
    def set_grade(self, v: str)          -> None: self._grade           = v;                  self.invalidateFilter()
    def set_padmin(self, v: str)         -> None: self._padmin          = v;                  self.invalidateFilter()
    def set_duplicates_only(self, v: bool) -> None: self._duplicates_only = v;                self.invalidateFilter()
    def set_incomplete_only(self, v: bool) -> None: self._incomplete_only = v;                self.invalidateFilter()

    def filterAcceptsRow(self, src_row: int, _src_parent: QModelIndex) -> bool:
        m = self.sourceModel()
        if not isinstance(m, _EditionModel) or src_row >= len(m._rows):
            return False
        row = m._rows[src_row]
        if self._duplicates_only and row.id not in m._duplicate_ids:
            return False
        if self._incomplete_only and row.id not in m._incomplete_ids:
            return False
        if self._structure and (row.structure or "") != self._structure:
            return False
        if self._grade and (row.grade or "") != self._grade:
            return False
        if self._padmin and (row.position_admin or "") != self._padmin:
            return False
        if self._text:
            hay = " ".join(filter(None, [
                row.noms, row.grade, row.structure, row.subdivision_arme,
                row.matricule_militaire, row.matricule_solde,
                row.fonction, row.position_admin,
            ])).upper()
            if self._text not in hay:
                return False
        return True


# ── Modèle ────────────────────────────────────────────────────────────────────

class _EditionModel(QAbstractTableModel):
    save_ok  = Signal(str)
    save_err = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ListeGeneraleRow] = []
        self._repo = None
        self.grades:     list[str] = []
        self.structures: list[str] = []
        self.subarmes:   list[str] = []
        self.padmins:    list[str] = []
        self._undo_stack: list[tuple[str, str, str | None]] = []
        self._modified_ids:   set[str] = set()
        self._incomplete_ids: set[str] = set()
        self._duplicate_ids:  set[str] = set()
        self._dup_mil_ids:    set[str] = set()
        self._dup_sol_ids:    set[str] = set()
        self._checked_ids:    set[str] = set()

    def load(
        self,
        rows: list[ListeGeneraleRow],
        repo,
        grades: list[str],
        structures: list[str],
        subarmes: list[str],
        padmins: list[str],
    ) -> None:
        self.beginResetModel()
        self._rows      = rows
        self._repo      = repo
        self.grades     = grades
        self.structures = structures
        self.subarmes   = subarmes
        self.padmins    = padmins
        self._undo_stack.clear()
        self._modified_ids.clear()
        self._checked_ids.clear()
        self._recompute_status_sets()
        self.endResetModel()

    def _recompute_status_sets(self) -> None:
        self._incomplete_ids = {r.id for r in self._rows if not (r.grade and r.structure)}
        mat_mil = Counter(r.matricule_militaire for r in self._rows if r.matricule_militaire)
        mat_sol = Counter(r.matricule_solde     for r in self._rows if r.matricule_solde)
        dup_mil_vals = {m for m, c in mat_mil.items() if c > 1}
        dup_sol_vals = {m for m, c in mat_sol.items() if c > 1}
        self._dup_mil_ids = {r.id for r in self._rows if r.matricule_militaire in dup_mil_vals}
        self._dup_sol_ids = {r.id for r in self._rows if r.matricule_solde     in dup_sol_vals}
        self._duplicate_ids = self._dup_mil_ids | self._dup_sol_ids

    # ── Qt overrides ─────────────────────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return _COLS[section][1]
        return str(section + 1)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        col = index.column()
        if col == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if col in _EDITABLE:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not self._rows:
            return None
        row   = self._rows[index.row()]
        field = _COLS[index.column()][0]

        if role == Qt.ItemDataRole.CheckStateRole:
            if index.column() == 0:
                return Qt.CheckState.Checked if row.id in self._checked_ids else Qt.CheckState.Unchecked
            return None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            val = getattr(row, field, None)
            return str(val) if val is not None else ""

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.TextAlignmentRole and field == "id":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.BackgroundRole:
            if row.id in self._modified_ids:
                return _COLOR_MODIFIED
            if row.id in self._incomplete_ids:
                return _COLOR_INCOMPLETE
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if field == "matricule_militaire" and row.id in self._dup_mil_ids:
                return _COLOR_DUP_FG
            if field == "matricule_solde" and row.id in self._dup_sol_ids:
                return _COLOR_DUP_FG
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            tips: list[str] = []
            if row.id in self._incomplete_ids:
                missing = [f for f in ("grade", "structure") if not getattr(row, f, None)]
                tips.append(f"Champ(s) critique(s) manquant(s) : {', '.join(missing)}")
            if row.id in self._dup_mil_ids:
                tips.append(f"Mat. militaire en doublon : « {row.matricule_militaire} »")
            if row.id in self._dup_sol_ids:
                tips.append(f"Mat. solde en doublon : « {row.matricule_solde} »")
            return "\n".join(tips) if tips else None

        return None

    def setData(self, index: QModelIndex, value: Any,
                role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            if not self._rows or not (0 <= index.row() < len(self._rows)):
                return False
            row = self._rows[index.row()]
            if int(value) == int(Qt.CheckState.Checked):
                self._checked_ids.add(row.id)
            else:
                self._checked_ids.discard(row.id)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True

        if role != Qt.ItemDataRole.EditRole or index.column() not in _EDITABLE:
            return False
        row   = self._rows[index.row()]
        field = _COLS[index.column()][0]
        old_val = getattr(row, field, None)
        new_val: str | None = (str(value) if value is not None else "").strip() or None
        if old_val == new_val:
            return False
        updated = replace(row, **{field: new_val})
        try:
            self._repo.update_row(updated)
        except Exception as exc:
            self.save_err.emit(f"{row.noms or '?'} — {exc}")
            return False
        self._undo_stack.append((row.id, field, old_val))
        if len(self._undo_stack) > _MAX_UNDO:
            self._undo_stack.pop(0)
        self._rows[index.row()] = updated
        self._modified_ids.add(row.id)
        self._recompute_status_sets()
        self.dataChanged.emit(index, index, [
            role,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.ToolTipRole,
        ])
        self.save_ok.emit(f"Enregistré : {updated.noms or updated.id}")
        return True

    def undo_last(self) -> str | None:
        if not self._undo_stack:
            return None
        row_id, field, old_val = self._undo_stack.pop()
        src_row = next((i for i, r in enumerate(self._rows) if r.id == row_id), None)
        if src_row is None:
            return None
        row = self._rows[src_row]
        reverted = replace(row, **{field: old_val})
        try:
            self._repo.update_row(reverted)
        except Exception as exc:
            return f"Erreur undo : {exc}"
        self._rows[src_row] = reverted
        self._recompute_status_sets()
        col = _COL_IDX.get(field, 0)
        idx = self.index(src_row, col)
        self.dataChanged.emit(idx, idx, [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.ToolTipRole,
        ])
        return f"Annulé : {_COLS[col][1]} — {row.noms or row_id}"

    # ── Opérations en masse ───────────────────────────────────────────────────

    def bulk_set_field(
        self, src_rows: list[int], field: str, value: str | None
    ) -> tuple[int, list[str]]:
        col = _COL_IDX.get(field)
        if col is None:
            return 0, ["Champ inconnu"]
        new_val: str | None = (value or "").strip() or None
        ok = 0
        errors: list[str] = []
        affected: list[int] = []
        for src_row in sorted(src_rows):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            if getattr(row, field, None) == new_val:
                ok += 1
                continue
            updated = replace(row, **{field: new_val})
            try:
                self._repo.update_row(updated)
                self._rows[src_row] = updated
                self._modified_ids.add(row.id)
                affected.append(src_row)
                ok += 1
            except Exception as exc:
                errors.append(f"{row.noms or '?'} : {exc}")
        if affected:
            self._recompute_status_sets()
            tl = self.index(min(affected), col)
            br = self.index(max(affected), col)
            self.dataChanged.emit(tl, br, [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.ToolTipRole,
            ])
        return ok, errors

    def remove_rows(self, src_rows: list[int]) -> tuple[int, list[str]]:
        ok = 0
        errors: list[str] = []
        for src_row in sorted(src_rows, reverse=True):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            try:
                self._repo.delete_row(row.id)
            except Exception as exc:
                errors.append(f"{row.noms or '?'} : {exc}")
                continue
            self._modified_ids.discard(row.id)
            self._checked_ids.discard(row.id)
            self.beginRemoveRows(QModelIndex(), src_row, src_row)
            self._rows.pop(src_row)
            self.endRemoveRows()
            ok += 1
        if ok:
            self._recompute_status_sets()
        return ok, errors

    def check_visible(self, proxy: QSortFilterProxyModel, state: bool) -> None:
        """Coche ou décoche toutes les lignes visibles dans le proxy."""
        changed: list[int] = []
        for pr in range(proxy.rowCount()):
            sr = proxy.mapToSource(proxy.index(pr, 0)).row()
            if 0 <= sr < len(self._rows):
                row = self._rows[sr]
                if state:
                    self._checked_ids.add(row.id)
                else:
                    self._checked_ids.discard(row.id)
                changed.append(sr)
        if changed:
            tl = self.index(min(changed), 0)
            br = self.index(max(changed), 0)
            self.dataChanged.emit(tl, br, [Qt.ItemDataRole.CheckStateRole])

    def uncheck_all(self) -> None:
        if not self._checked_ids:
            return
        self._checked_ids.clear()
        if self._rows:
            tl = self.index(0, 0)
            br = self.index(len(self._rows) - 1, 0)
            self.dataChanged.emit(tl, br, [Qt.ItemDataRole.CheckStateRole])

    def bulk_apply_replacements(
        self, plan: list[tuple[int, str, str | None]]
    ) -> tuple[int, list[str]]:
        """
        Applique un ensemble de remplacements (src_row, field, new_val).
        Plusieurs champs du même src_row sont traités en un seul update_row.
        """
        from collections import defaultdict
        by_row: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
        for src_row, field, new_val in plan:
            by_row[src_row].append((field, new_val))

        ok = 0
        errors: list[str] = []
        affected_rows: list[int] = []
        affected_cols: set[int] = set()

        for src_row in sorted(by_row.keys()):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            changes: dict[str, str | None] = {}
            for field, new_val in by_row[src_row]:
                col = _COL_IDX.get(field)
                if col is None or col not in _EDITABLE:
                    continue
                if getattr(row, field, None) != new_val:
                    changes[field] = new_val
                    affected_cols.add(col)
            if not changes:
                ok += 1
                continue
            updated = replace(row, **changes)
            try:
                self._repo.update_row(updated)
                for field, _ in changes.items():
                    self._undo_stack.append((row.id, field, getattr(row, field, None)))
                    if len(self._undo_stack) > _MAX_UNDO:
                        self._undo_stack.pop(0)
                self._rows[src_row] = updated
                self._modified_ids.add(row.id)
                affected_rows.append(src_row)
                ok += 1
            except Exception as exc:
                errors.append(f"{row.noms or '?'} : {exc}")

        if affected_rows:
            self._recompute_status_sets()
            min_col = min(affected_cols) if affected_cols else 0
            max_col = max(affected_cols) if affected_cols else len(_COLS) - 1
            tl = self.index(min(affected_rows), min_col)
            br = self.index(max(affected_rows), max_col)
            self.dataChanged.emit(tl, br, [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.ToolTipRole,
            ])
        return ok, errors


# ── Délégué ───────────────────────────────────────────────────────────────────

class _EditionDelegate(QStyledItemDelegate):
    def __init__(self, model: _EditionModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._m = model

    def _src_col(self, index: QModelIndex) -> int:
        m = index.model()
        return m.mapToSource(index).column() if isinstance(m, QSortFilterProxyModel) else index.column()

    def _src_row(self, index: QModelIndex) -> int:
        m = index.model()
        return m.mapToSource(index).row() if isinstance(m, QSortFilterProxyModel) else index.row()

    def createEditor(self, parent: QWidget, _opt: QStyleOptionViewItem,
                     index: QModelIndex) -> QWidget | None:
        col = self._src_col(index)
        if col not in _EDITABLE:
            return None
        field, _, etype = _COLS[col]
        if etype == "text":
            return QLineEdit(parent)
        box = QComboBox(parent)
        editable = (etype == "combo_ed")
        box.setEditable(editable)
        if editable:
            box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if field == "grade":
            options = self._m.grades
        elif field == "structure":
            options = self._m.structures
        elif field == "subdivision_arme":
            options = self._subdivision_options_for_row(self._src_row(index))
        else:
            options = self._m.padmins
        box.addItem("")
        for opt in options:
            box.addItem(opt)
        return box

    def _subdivision_options_for_row(self, src_row: int) -> list[str]:
        if not (0 <= src_row < len(self._m._rows)):
            return self._m.subarmes
        structure = (self._m._rows[src_row].structure or "").strip()
        if structure and self._m._repo:
            try:
                opts = self._m._repo.fetch_distinct_subarme_for_structure(structure)
                if opts:
                    return opts
            except Exception:
                pass
        return self._m.subarmes

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        src = self._m.index(self._src_row(index), self._src_col(index))
        val = self._m.data(src, Qt.ItemDataRole.EditRole) or ""
        if isinstance(editor, QLineEdit):
            editor.setText(val)
        elif isinstance(editor, QComboBox):
            i = editor.findText(val)
            if i >= 0:
                editor.setCurrentIndex(i)
            elif editor.isEditable():
                editor.setCurrentText(val)

    def setModelData(self, editor: QWidget, model: Any, index: QModelIndex) -> None:
        val = (editor.text() if isinstance(editor, QLineEdit)
               else editor.currentText() if isinstance(editor, QComboBox) else None)
        if val is not None:
            model.setData(index, val, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem,
                             _idx: QModelIndex) -> None:
        editor.setGeometry(option.rect)


# ── Fiche lecture seule ───────────────────────────────────────────────────────

class _FicheDialog(QDialog):
    def __init__(self, row: ListeGeneraleRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Fiche — {row.noms or '?'}")
        self.setMinimumSize(500, 560)
        self.setModal(True)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        root_lay = QVBoxLayout(inner)
        root_lay.setSpacing(12)

        def section(title: str, fields: list[tuple[str, str | None]]) -> QGroupBox:
            gb = QGroupBox(title)
            fl = QFormLayout(gb)
            fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
            for lbl, val in fields:
                fl.addRow(f"{lbl} :", QLabel(val or "—"))
            return gb

        dn = str(row.date_naissance)[:10] if row.date_naissance else None
        root_lay.addWidget(section("Identité", [
            ("Noms & prénoms",       row.noms),
            ("Grade",                row.grade),
            ("Date de naissance",    dn),
            ("Lieu de naissance",    row.lieu_naissance),
            ("Entrée au service",    row.date_service),
            ("N° décision",          row.ndecision),
            ("Matricule militaire",  row.matricule_militaire),
            ("Matricule solde",      row.matricule_solde),
        ]))
        root_lay.addWidget(section("Position administrative", [
            ("Structure",           row.structure),
            ("Subdivision d'arme",  row.subdivision_arme),
            ("Position admin.",     row.position_admin),
            ("Fonction",            row.fonction),
        ]))
        root_lay.addWidget(section("Formation", [
            ("Diplôme",             row.diplome),
            ("Date obtention",      row.date_obtention),
        ]))
        root_lay.addWidget(section("Notes & tri", [
            ("Observations",        row.obs),
            ("Ordre de tri",        str(row.ordre_tri) if row.ordre_tri is not None else None),
            ("Préséance",           str(row.presseance) if row.presseance is not None else None),
        ]))
        root_lay.addStretch()
        scroll.setWidget(inner)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(scroll, stretch=1)
        lay.addWidget(bb)


# ── Rechercher / Remplacer ────────────────────────────────────────────────────

class _FindReplaceDialog(QDialog):
    def __init__(self, proxy: _EditionFilterProxy, model: _EditionModel,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._proxy = proxy
        self._model = model
        self.setWindowTitle("Rechercher / Remplacer")
        self.setModal(False)
        self.setMinimumWidth(450)

        lay = QVBoxLayout(self)
        form = QFormLayout()

        self._cmb_col = QComboBox()
        self._cmb_col.addItem("(Toutes les colonnes)", "*")
        for i in sorted(_EDITABLE):
            self._cmb_col.addItem(_COLS[i][1], _COLS[i][0])
        form.addRow("Colonne :", self._cmb_col)

        self._txt_find = QLineEdit()
        self._txt_find.setPlaceholderText("Valeur à rechercher…")
        form.addRow("Rechercher :", self._txt_find)

        self._txt_replace = QLineEdit()
        self._txt_replace.setPlaceholderText("Nouvelle valeur (vide = effacer le champ)")
        form.addRow("Remplacer par :", self._txt_replace)

        self._chk_partial = QCheckBox("Correspondance partielle (contient)")
        form.addRow("", self._chk_partial)

        lay.addLayout(form)

        self._lbl_preview = QLabel("—")
        self._lbl_preview.setObjectName("dashSubtitle")
        lay.addWidget(self._lbl_preview)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        self._btn_apply = bb.button(QDialogButtonBox.StandardButton.Apply)
        self._btn_apply.setText("Remplacer")
        self._btn_apply.setEnabled(False)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)
        self._btn_apply.clicked.connect(self._apply)
        lay.addWidget(bb)

        self._txt_find.textChanged.connect(self._update_preview)
        self._cmb_col.currentIndexChanged.connect(self._update_preview)
        self._chk_partial.stateChanged.connect(self._update_preview)

    def _build_plan(self) -> list[tuple[int, str, str | None]]:
        """
        Construit le plan de remplacement : [(src_row, field, new_val), ...].
        En mode 'Toutes les colonnes', génère une entrée par champ touché.
        """
        col_data  = self._cmb_col.currentData()
        find_text = self._txt_find.text()
        if not find_text:
            return []
        partial     = self._chk_partial.isChecked()
        replace_raw = self._txt_replace.text()
        all_columns = (col_data == "*")
        fields = (
            [_COLS[i][0] for i in sorted(_EDITABLE)]
            if all_columns
            else ([col_data] if col_data else [])
        )
        plan: list[tuple[int, str, str | None]] = []
        for pr in range(self._proxy.rowCount()):
            src_idx = self._proxy.mapToSource(self._proxy.index(pr, 0))
            sr = src_idx.row()
            if not (0 <= sr < len(self._model._rows)):
                continue
            row = self._model._rows[sr]
            for field in fields:
                val = getattr(row, field, None) or ""
                if partial:
                    hit = find_text.lower() in val.lower()
                else:
                    hit = val.lower() == find_text.lower()
                if not hit:
                    continue
                if partial:
                    new_field_val: str | None = re.sub(
                        re.escape(find_text), replace_raw, val, flags=re.IGNORECASE
                    ).strip() or None
                else:
                    new_field_val = replace_raw.strip() or None
                plan.append((sr, field, new_field_val))
        return plan

    def _update_preview(self) -> None:
        plan = self._build_plan()
        ft  = self._txt_find.text()
        all_columns = (self._cmb_col.currentData() == "*")
        if not ft:
            self._lbl_preview.setText("—")
            self._btn_apply.setEnabled(False)
        elif plan:
            n_rows = len({sr for sr, _, _ in plan})
            if all_columns:
                self._lbl_preview.setText(
                    f"{n_rows} ligne(s), {len(plan)} champ(s) concerné(s) parmi les lignes visibles"
                )
            else:
                self._lbl_preview.setText(
                    f"{n_rows} ligne(s) concernée(s) parmi les lignes visibles"
                )
            self._btn_apply.setEnabled(True)
        else:
            self._lbl_preview.setText("Aucune correspondance")
            self._btn_apply.setEnabled(False)

    def _apply(self) -> None:
        plan = self._build_plan()
        if not plan:
            return
        col_data    = self._cmb_col.currentData()
        col_label   = self._cmb_col.currentText()
        all_columns = (col_data == "*")
        find_text   = self._txt_find.text()
        replace_text = self._txt_replace.text().strip()
        n_rows  = len({sr for sr, _, _ in plan})
        n_pairs = len(plan)
        if all_columns:
            confirm_msg = (
                f"Remplacer dans toutes les colonnes pour {n_rows} ligne(s) "
                f"({n_pairs} champ(s) au total) ?\n\n"
                f"  Rechercher   : « {find_text} »\n"
                f"  Remplacer par: « {replace_text} »\n\n"
                "Cette opération est immédiatement enregistrée en base."
            )
        else:
            confirm_msg = (
                f"Remplacer dans « {col_label} » pour {n_rows} ligne(s) ?\n\n"
                f"  Rechercher   : « {find_text} »\n"
                f"  Remplacer par: « {replace_text} »\n\n"
                "Cette opération est immédiatement enregistrée en base."
            )
        reply = QMessageBox.question(
            self, "Confirmer le remplacement",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, errors = self._model.bulk_apply_replacements(plan)
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        n_fields_info = f" ({n_pairs} champ(s))" if all_columns else ""
        result_msg = f"Remplacement effectué pour {ok} ligne(s){n_fields_info}."
        remaining = self._build_plan()
        if remaining:
            n_r = len({sr for sr, _, _ in remaining})
            result_msg += f" · {n_r} correspondance(s) restante(s)."
        self._lbl_preview.setText(result_msg)
        self._btn_apply.setEnabled(bool(remaining))


# ── Vue principale ────────────────────────────────────────────────────────────

class EditionSmeView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._find_replace_dlg: _FindReplaceDialog | None = None

        title = QLabel("Edition SME — Mise à jour rapide")
        title.setObjectName("dashTitle")

        subtitle = QLabel(
            "Double-clic pour éditer · Ctrl+Z annule · Ctrl+D copie du dessus · "
            "Clic droit pour filtrer par valeur · "
            "Vert = modifié · Jaune = incomplet · Rouge = matricule en doublon"
        )
        subtitle.setObjectName("dashSubtitle")
        subtitle.setWordWrap(True)

        # ── Barre filtre 1 : recherche + combos ──────────────────────────────
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher (nom, matricule, fonction…)")
        self._search.setClearButtonEnabled(True)

        self._cmb_structure = QComboBox()
        self._cmb_structure.setMinimumWidth(130)
        self._cmb_structure.setToolTip("Filtrer par structure")

        self._cmb_grade = QComboBox()
        self._cmb_grade.setMinimumWidth(110)
        self._cmb_grade.setToolTip("Filtrer par grade")

        self._cmb_padmin = QComboBox()
        self._cmb_padmin.setMinimumWidth(120)
        self._cmb_padmin.setToolTip("Filtrer par position admin.")

        self._btn_reset = QPushButton("Réinitialiser")
        self._btn_reset.setToolTip("Effacer tous les filtres actifs")
        self._btn_reset.clicked.connect(self._reset_filters)

        filter_row1 = QHBoxLayout()
        filter_row1.setSpacing(6)
        filter_row1.addWidget(self._search, stretch=1)
        filter_row1.addWidget(QLabel("Structure :"))
        filter_row1.addWidget(self._cmb_structure)
        filter_row1.addWidget(QLabel("Grade :"))
        filter_row1.addWidget(self._cmb_grade)
        filter_row1.addWidget(QLabel("Position :"))
        filter_row1.addWidget(self._cmb_padmin)
        filter_row1.addWidget(self._btn_reset)

        # ── Barre filtre 2 : outils + statut ─────────────────────────────────
        self._btn_presets = QPushButton("Préréglages ▾")
        self._btn_presets.setToolTip("Sauvegarder / rappeler des combinaisons de filtres")
        self._btn_presets.clicked.connect(self._show_preset_menu)

        self._btn_find_replace = QPushButton("Rech./Remp.…")
        self._btn_find_replace.setToolTip("Rechercher et remplacer une valeur dans une colonne")
        self._btn_find_replace.clicked.connect(self._open_find_replace)

        self._btn_compact = QPushButton("Compact")
        self._btn_compact.setCheckable(True)
        self._btn_compact.setToolTip("Réduire la hauteur des lignes pour afficher plus de données")

        self._btn_show_dups = QPushButton("⚠ Doublons")
        self._btn_show_dups.setCheckable(True)
        self._btn_show_dups.setVisible(False)
        self._btn_show_dups.setToolTip(
            "Afficher uniquement les lignes avec un matricule militaire ou solde en doublon"
        )

        self._btn_show_inc = QPushButton("⚠ Incomplets")
        self._btn_show_inc.setCheckable(True)
        self._btn_show_inc.setVisible(False)
        self._btn_show_inc.setToolTip(
            "Afficher uniquement les lignes avec un champ critique manquant (grade ou structure)"
        )

        self._btn_reload = QPushButton("Recharger")
        self._btn_reload.setToolTip("Rechargement complet depuis la base de données")
        self._btn_reload.clicked.connect(self.load_data)

        self._lbl_status = QLabel("—")
        self._lbl_status.setObjectName("dashSubtitle")
        self._lbl_status.setMinimumWidth(260)

        filter_row2 = QHBoxLayout()
        filter_row2.setSpacing(6)
        filter_row2.addWidget(self._btn_presets)
        filter_row2.addWidget(self._btn_find_replace)
        filter_row2.addWidget(self._btn_compact)
        filter_row2.addStretch()
        filter_row2.addWidget(self._btn_show_dups)
        filter_row2.addWidget(self._btn_show_inc)
        filter_row2.addWidget(self._btn_reload)
        filter_row2.addWidget(self._lbl_status)

        # ── Modèle ────────────────────────────────────────────────────────────
        self._model = _EditionModel(self)
        self._model.save_ok.connect(self._on_saved)
        self._model.save_err.connect(self._on_save_error)
        self._model.dataChanged.connect(self._on_model_data_changed)

        self._proxy = _EditionFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._search.textChanged.connect(self._proxy.set_text)
        self._cmb_structure.currentTextChanged.connect(self._on_structure_filter)
        self._cmb_grade.currentTextChanged.connect(self._on_grade_filter)
        self._cmb_padmin.currentTextChanged.connect(self._on_padmin_filter)
        self._btn_show_dups.toggled.connect(self._proxy.set_duplicates_only)
        self._btn_show_inc.toggled.connect(self._proxy.set_incomplete_only)

        self._proxy.layoutChanged.connect(self._update_count)
        self._proxy.modelReset.connect(self._update_count)

        # ── Tableau ───────────────────────────────────────────────────────────
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setItemDelegate(_EditionDelegate(self._model, self._table))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(True)

        # Largeurs par défaut — remplacées par QSettings si disponible
        self._table.setColumnWidth(0, 40)   # N° + case à cocher
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 130)
        self._table.setColumnWidth(5, 110)
        self._table.setColumnWidth(6, 160)
        self._table.setColumnWidth(7, 110)
        self._table.setColumnWidth(8, 100)

        # Restore header state (widths + order) from QSettings
        self._restore_header_state()
        hdr.sectionResized.connect(lambda *_: self._save_header_state())
        hdr.sectionMoved.connect(lambda *_: self._save_header_state())

        # Init compact mode from QSettings (block signals to avoid double-save)
        _s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        _init_compact = _s.value("edition/compact", False, type=bool)
        self._btn_compact.blockSignals(True)
        self._btn_compact.setChecked(_init_compact)
        self._btn_compact.blockSignals(False)
        self._table.verticalHeader().setDefaultSectionSize(18 if _init_compact else 24)
        self._btn_compact.toggled.connect(self._on_compact_toggled)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # ── Raccourcis clavier ────────────────────────────────────────────────
        QShortcut(QKeySequence.StandardKey.Undo, self).activated.connect(self._action_undo)
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(self._copy_from_above)

        # ── Barre d'actions ───────────────────────────────────────────────────
        self._btn_fiche = QPushButton("Voir fiche")
        self._btn_fiche.setToolTip("Afficher toute la fiche du personnel sélectionné")
        self._btn_fiche.setEnabled(False)
        self._btn_fiche.clicked.connect(self._action_fiche)

        self._btn_delete = QPushButton("Supprimer")
        self._btn_delete.setToolTip("Supprimer le(s) personnel(s) sélectionné(s)")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._action_delete)

        _sep1 = QFrame(); _sep1.setFrameShape(QFrame.Shape.VLine); _sep1.setFrameShadow(QFrame.Shadow.Sunken)

        self._btn_bulk_struct = QPushButton("Changer structure…")
        self._btn_bulk_struct.setToolTip("Affecter la même structure à toute la sélection")
        self._btn_bulk_struct.setEnabled(False)
        self._btn_bulk_struct.clicked.connect(self._action_bulk_structure)

        self._btn_bulk_sub = QPushButton("Changer subdivision…")
        self._btn_bulk_sub.setToolTip("Affecter la même subdivision à toute la sélection")
        self._btn_bulk_sub.setEnabled(False)
        self._btn_bulk_sub.clicked.connect(self._action_bulk_subdivision)

        _sep2 = QFrame(); _sep2.setFrameShape(QFrame.Shape.VLine); _sep2.setFrameShadow(QFrame.Shadow.Sunken)

        self._btn_check_visible = QPushButton("Cocher visibles")
        self._btn_check_visible.setToolTip(
            "Cocher toutes les lignes actuellement visibles (filtrées) — la coche persiste quand vous changez de filtre"
        )
        self._btn_check_visible.clicked.connect(
            lambda: (self._model.check_visible(self._proxy, True), self._update_action_buttons(), self._update_count())
        )

        self._btn_uncheck_all = QPushButton("Décocher tout")
        self._btn_uncheck_all.setToolTip("Supprimer toutes les coches")
        self._btn_uncheck_all.clicked.connect(
            lambda: (self._model.uncheck_all(), self._update_action_buttons(), self._update_count())
        )

        self._btn_select_all = QPushButton("Tout sélectionner")
        self._btn_select_all.setToolTip("Sélectionner toutes les lignes visibles (Qt)")
        self._btn_select_all.clicked.connect(self._table.selectAll)

        self._btn_export = QPushButton("Exporter CSV…")
        self._btn_export.setToolTip(
            "Exporter les lignes sélectionnées en CSV (ou toutes les lignes visibles si rien n'est sélectionné)"
        )
        self._btn_export.clicked.connect(self._action_export_csv)

        self._lbl_sel = QLabel("")
        self._lbl_sel.setObjectName("dashSubtitle")

        _sep3 = QFrame(); _sep3.setFrameShape(QFrame.Shape.VLine); _sep3.setFrameShadow(QFrame.Shadow.Sunken)

        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)
        action_bar.addWidget(self._btn_fiche)
        action_bar.addWidget(self._btn_delete)
        action_bar.addWidget(_sep1)
        action_bar.addWidget(self._btn_bulk_struct)
        action_bar.addWidget(self._btn_bulk_sub)
        action_bar.addWidget(_sep2)
        action_bar.addWidget(self._btn_check_visible)
        action_bar.addWidget(self._btn_uncheck_all)
        action_bar.addWidget(_sep3)
        action_bar.addWidget(self._btn_select_all)
        action_bar.addWidget(self._btn_export)
        action_bar.addStretch()
        action_bar.addWidget(self._lbl_sel)

        # ── Layout principal ──────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(8)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addLayout(filter_row1)
        lay.addLayout(filter_row2)
        lay.addWidget(self._table, stretch=1)
        lay.addLayout(action_bar)

    # ── Settings ──────────────────────────────────────────────────────────────

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ── Persistance en-tête et mode compact ───────────────────────────────────

    def _save_header_state(self) -> None:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(
            "edition/header_state", self._table.horizontalHeader().saveState()
        )

    def _restore_header_state(self) -> None:
        state = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("edition/header_state")
        if state:
            try:
                self._table.horizontalHeader().restoreState(state)
            except Exception:
                pass

    def _on_compact_toggled(self, checked: bool) -> None:
        self._table.verticalHeader().setDefaultSectionSize(18 if checked else 24)
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("edition/compact", checked)

    # ── Préréglages de filtres ────────────────────────────────────────────────

    def _load_presets(self) -> list[dict]:
        raw = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("edition/presets", "[]")
        try:
            return json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            return []

    def _store_presets(self, presets: list[dict]) -> None:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(
            "edition/presets", json.dumps(presets, ensure_ascii=False)
        )

    def _show_preset_menu(self) -> None:
        menu = QMenu(self)
        presets = self._load_presets()
        if presets:
            for p in presets:
                name = p.get("name", "?")
                act = menu.addAction(name)
                act.triggered.connect(lambda _=None, preset=p: self._apply_preset(preset))
            menu.addSeparator()
        menu.addAction("Sauvegarder le filtre actuel…").triggered.connect(self._save_current_preset)
        if presets:
            sub = menu.addMenu("Supprimer…")
            for p in presets:
                name = p.get("name", "?")
                act = sub.addAction(name)
                act.triggered.connect(lambda _=None, n=name: self._delete_preset(n))
        btn = self._btn_presets
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _apply_preset(self, preset: dict) -> None:
        self._search.setText(preset.get("text", ""))
        self._set_filter_combo(self._cmb_structure, preset.get("structure", "(Tous)"))
        self._set_filter_combo(self._cmb_grade,     preset.get("grade",     "(Tous)"))
        self._set_filter_combo(self._cmb_padmin,    preset.get("padmin",    "(Tous)"))

    def _save_current_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Sauvegarder le filtre", "Nom du préréglage :")
        if not ok or not name.strip():
            return
        name = name.strip()
        preset = {
            "name":      name,
            "text":      self._search.text(),
            "structure": self._cmb_structure.currentText(),
            "grade":     self._cmb_grade.currentText(),
            "padmin":    self._cmb_padmin.currentText(),
        }
        presets = [p for p in self._load_presets() if p.get("name") != name]
        presets.append(preset)
        self._store_presets(presets)

    def _delete_preset(self, name: str) -> None:
        self._store_presets([p for p in self._load_presets() if p.get("name") != name])

    # ── Chargement ────────────────────────────────────────────────────────────

    def load_data(self) -> None:
        from sme_gend.repositories.grade_sqlite_repository import SqliteGradeRepository
        from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
        from sme_gend.repositories.structure_sqlite_repository import SqliteStructureRepository
        from sme_gend.ui.bg_task import run_with_progress

        settings = self._settings
        self._btn_reload.setEnabled(False)

        def do_load():
            repo = ListeGeneraleRepository(settings)
            rows = repo.fetch_all()

            grades: list[str] = []
            if settings.use_sqlite_liste_generale and settings.sqlite_liste_path:
                try:
                    grades = [g.libelle for g in SqliteGradeRepository(settings).fetch_all() if g.libelle]
                except Exception:
                    pass
            if not grades:
                seen: set[str] = set()
                for r in rows:
                    if r.grade and r.grade not in seen:
                        grades.append(r.grade); seen.add(r.grade)

            structures: list[str] = []
            if settings.use_sqlite_liste_generale and settings.sqlite_liste_path:
                try:
                    structures = [s.code for s in SqliteStructureRepository(settings).fetch_all() if s.code]
                except Exception:
                    pass
            if not structures:
                structures = sorted({r.structure for r in rows if r.structure})

            try:
                subarmes = repo.fetch_distinct_subarme()
            except Exception:
                subarmes = sorted({r.subdivision_arme for r in rows if r.subdivision_arme})

            try:
                padmins = repo.fetch_distinct_padmin()
            except Exception:
                padmins = sorted({r.position_admin for r in rows if r.position_admin})

            return repo, rows, grades, structures, subarmes, padmins

        result = run_with_progress(self, "Edition SME", "Chargement de la liste…", do_load)
        self._btn_reload.setEnabled(True)
        if result is None:
            return

        repo, rows, grades, structures, subarmes, padmins = result
        self._model.load(rows, repo, grades, structures, subarmes, padmins)
        self._populate_filter_combos(rows, grades, structures, padmins)
        # Sync proxy filter state with combos (combos were reset to "(Tous)" with signals blocked)
        self._proxy.set_structure("")
        self._proxy.set_grade("")
        self._proxy.set_padmin("")
        self._table.sortByColumn(_COL_IDX["noms"], Qt.SortOrder.AscendingOrder)
        self._update_count()

    # ── Combos de filtres ─────────────────────────────────────────────────────

    def _populate_filter_combos(
        self,
        rows: list[ListeGeneraleRow],
        grades: list[str],
        structures: list[str],
        padmins: list[str],
    ) -> None:
        TOUS = "(Tous)"
        for combo, items in [
            (self._cmb_structure, structures or sorted({r.structure for r in rows if r.structure})),
            (self._cmb_grade,     grades),
            (self._cmb_padmin,    padmins),
        ]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(TOUS, "")
            for it in items:
                combo.addItem(it, it)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _update_count(self) -> None:
        total   = self._model.rowCount()
        visible = self._proxy.rowCount()
        n_mod   = len(self._model._modified_ids)
        n_inc   = len(self._model._incomplete_ids)
        n_dup   = len(self._model._duplicate_ids)

        # Boutons filtres spéciaux — visibles uniquement s'il y a des cas
        self._btn_show_dups.setText(f"⚠ {n_dup} doublon(s)")
        self._btn_show_dups.setVisible(n_dup > 0)
        self._btn_show_inc.setText(f"⚠ {n_inc} incomplet(s)")
        self._btn_show_inc.setVisible(n_inc > 0)

        n_chk = len(self._model._checked_ids)
        parts = [f"{visible} / {total}" if visible != total else str(total)]
        parts[0] += " personnel(s)"
        if n_mod:
            parts.append(f"{n_mod} modifié(s)")
        if n_chk:
            parts.append(f"{n_chk} coché(s)")
        self._lbl_status.setText(" · ".join(parts))

    def _on_structure_filter(self, text: str) -> None:
        self._proxy.set_structure("" if text == "(Tous)" else text)

    def _on_grade_filter(self, text: str) -> None:
        self._proxy.set_grade("" if text == "(Tous)" else text)

    def _on_padmin_filter(self, text: str) -> None:
        self._proxy.set_padmin("" if text == "(Tous)" else text)

    def _reset_filters(self) -> None:
        self._search.clear()
        self._cmb_structure.setCurrentIndex(0)
        self._cmb_grade.setCurrentIndex(0)
        self._cmb_padmin.setCurrentIndex(0)
        self._btn_show_dups.setChecked(False)
        self._btn_show_inc.setChecked(False)

    # ── Sélection ─────────────────────────────────────────────────────────────

    def _get_selected_src_rows(self) -> list[int]:
        src_rows: set[int] = set()
        for idx in self._table.selectionModel().selectedRows():
            src_rows.add(self._proxy.mapToSource(idx).row())
        return sorted(src_rows)

    def _get_checked_src_rows(self) -> list[int]:
        checked = self._model._checked_ids
        return [i for i, r in enumerate(self._model._rows) if r.id in checked]

    def _get_active_src_rows(self) -> list[int]:
        """Cochés en priorité ; sinon sélection Qt."""
        checked = self._get_checked_src_rows()
        return checked if checked else self._get_selected_src_rows()

    def _update_action_buttons(self) -> None:
        n_chk = len(self._model._checked_ids)
        n_sel = len(self._get_selected_src_rows())
        n_act = n_chk if n_chk else n_sel
        using_checked = n_chk > 0

        self._btn_fiche.setEnabled(not using_checked and n_sel == 1)
        self._btn_delete.setEnabled(n_act >= 1)
        self._btn_delete.setText(f"Supprimer ({n_act})" if n_act > 1 else "Supprimer")
        self._btn_bulk_struct.setEnabled(n_act >= 2)
        self._btn_bulk_sub.setEnabled(n_act >= 2)
        suffix = f"({n_act} cochés)" if using_checked else (f"({n_act})" if n_act >= 2 else "…")
        self._btn_bulk_struct.setText(f"Changer structure {suffix}")
        self._btn_bulk_sub.setText(f"Changer subdivision {suffix}")

        if using_checked:
            self._lbl_sel.setText(f"{n_chk} coché(s)" + (f" · {n_sel} sélectionné(s)" if n_sel else ""))
        elif n_sel:
            self._lbl_sel.setText(f"{n_sel} sélectionné(s)")
        else:
            self._lbl_sel.setText("")

    def _on_selection_changed(self) -> None:
        self._update_action_buttons()

    # ── Actions clavier ───────────────────────────────────────────────────────

    def _action_undo(self) -> None:
        msg = self._model.undo_last()
        if msg is None:
            self._lbl_status.setText("Rien à annuler")
        else:
            self._lbl_status.setText(msg)

    def _copy_from_above(self) -> None:
        idx = self._table.currentIndex()
        if not idx.isValid() or idx.row() == 0:
            return
        if self._proxy.mapToSource(idx).column() not in _EDITABLE:
            return
        above = self._proxy.index(idx.row() - 1, idx.column())
        value = self._proxy.data(above, Qt.ItemDataRole.EditRole)
        if value is not None:
            self._proxy.setData(idx, value, Qt.ItemDataRole.EditRole)

    # ── Menu contextuel ───────────────────────────────────────────────────────

    def _on_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        menu = QMenu(self._table)

        if idx.isValid():
            src_idx  = self._proxy.mapToSource(idx)
            col      = src_idx.column()
            field    = _COLS[col][0]
            cell_val = (self._model.data(src_idx, Qt.ItemDataRole.DisplayRole) or "").strip()

            if cell_val:
                if field == "structure":
                    act = menu.addAction(f"Filtrer : structure « {cell_val} »")
                    act.triggered.connect(
                        lambda _=None, v=cell_val: self._set_filter_combo(self._cmb_structure, v)
                    )
                elif field == "grade":
                    act = menu.addAction(f"Filtrer : grade « {cell_val} »")
                    act.triggered.connect(
                        lambda _=None, v=cell_val: self._set_filter_combo(self._cmb_grade, v)
                    )
                elif field == "position_admin":
                    act = menu.addAction(f"Filtrer : position « {cell_val} »")
                    act.triggered.connect(
                        lambda _=None, v=cell_val: self._set_filter_combo(self._cmb_padmin, v)
                    )
                else:
                    act = menu.addAction(f"Rechercher « {cell_val} »")
                    act.triggered.connect(lambda _=None, v=cell_val: self._search.setText(v))
                menu.addSeparator()

            act_fiche = menu.addAction("Voir fiche")
            act_fiche.setEnabled(len(self._get_selected_src_rows()) == 1)
            act_fiche.triggered.connect(self._action_fiche)
            menu.addSeparator()

        act_reset = menu.addAction("Réinitialiser tous les filtres")
        act_reset.triggered.connect(self._reset_filters)

        act_undo = menu.addAction("Annuler dernière modification\tCtrl+Z")
        act_undo.setEnabled(bool(self._model._undo_stack))
        act_undo.triggered.connect(self._action_undo)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _set_filter_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # ── Rechercher / Remplacer ────────────────────────────────────────────────

    def _open_find_replace(self) -> None:
        if self._find_replace_dlg and self._find_replace_dlg.isVisible():
            self._find_replace_dlg.raise_()
            self._find_replace_dlg.activateWindow()
            return
        self._find_replace_dlg = _FindReplaceDialog(self._proxy, self._model, self)
        self._find_replace_dlg.show()

    # ── Export CSV ────────────────────────────────────────────────────────────

    def _action_export_csv(self) -> None:
        src_rows = self._get_selected_src_rows()
        if src_rows:
            rows_to_export = [self._model._rows[r] for r in src_rows]
            label = f"sélection ({len(rows_to_export)} lignes)"
        else:
            rows_to_export = []
            for pr in range(self._proxy.rowCount()):
                sr = self._proxy.mapToSource(self._proxy.index(pr, 0)).row()
                if 0 <= sr < len(self._model._rows):
                    rows_to_export.append(self._model._rows[sr])
            label = f"vue filtrée ({len(rows_to_export)} lignes)"

        if not rows_to_export:
            QMessageBox.information(self, "Export CSV", "Aucune ligne à exporter.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, f"Exporter {label}", "", "Fichier CSV (*.csv)"
        )
        if not path:
            return

        headers = [c[1] for c in _COLS]
        fields  = [c[0] for c in _COLS]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(headers)
            for row in rows_to_export:
                w.writerow([(getattr(row, fld, None) or "") for fld in fields])

        self._lbl_status.setText(f"Export : {len(rows_to_export)} ligne(s) → {Path(path).name}")

    # ── Actions tableau ───────────────────────────────────────────────────────

    def _action_fiche(self) -> None:
        src_rows = self._get_selected_src_rows()
        if len(src_rows) != 1:
            return
        _FicheDialog(self._model._rows[src_rows[0]], self).exec()

    def _action_delete(self) -> None:
        src_rows = self._get_active_src_rows()
        if not src_rows:
            return
        names = ", ".join((self._model._rows[r].noms or "?") for r in src_rows[:5])
        if len(src_rows) > 5:
            names += f" … et {len(src_rows) - 5} autre(s)"
        if QMessageBox.question(
            self, "Confirmer la suppression",
            f"Supprimer définitivement {len(src_rows)} personnel(s) ?\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, errors = self._model.remove_rows(src_rows)
        if errors:
            QMessageBox.warning(self, "Erreurs de suppression", "\n".join(errors[:10]))
        self._update_count()

    def _action_bulk_structure(self) -> None:
        src_rows = self._get_active_src_rows()
        if len(src_rows) < 2:
            return
        new_val = self._pick_value(
            f"Changer la structure — {len(src_rows)} personne(s)",
            "Nouvelle structure :", self._model.structures, editable=True,
        )
        if new_val is None:
            return
        if QMessageBox.question(
            self, "Confirmer le changement de structure",
            f"Affecter la structure « {new_val} » à {len(src_rows)} personne(s) ?\n"
            "Cette opération est immédiatement enregistrée en base.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, errors = self._model.bulk_set_field(src_rows, "structure", new_val)
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._update_count()

    def _action_bulk_subdivision(self) -> None:
        src_rows = self._get_active_src_rows()
        if len(src_rows) < 2:
            return
        structures_sel = {(self._model._rows[r].structure or "") for r in src_rows}
        options = self._model.subarmes
        if len(structures_sel) == 1:
            struct = next(iter(structures_sel))
            if struct and self._model._repo:
                try:
                    opts = self._model._repo.fetch_distinct_subarme_for_structure(struct)
                    if opts:
                        options = opts
                except Exception:
                    pass
        new_val = self._pick_value(
            f"Changer la subdivision — {len(src_rows)} personne(s)",
            "Nouvelle subdivision d'arme :", options, editable=True,
        )
        if new_val is None:
            return
        if QMessageBox.question(
            self, "Confirmer le changement de subdivision",
            f"Affecter la subdivision « {new_val} » à {len(src_rows)} personne(s) ?\n"
            "Cette opération est immédiatement enregistrée en base.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, errors = self._model.bulk_set_field(src_rows, "subdivision_arme", new_val)
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._update_count()

    @staticmethod
    def _pick_value(
        title: str, label: str, options: list[str], *, editable: bool = True,
        parent: QWidget | None = None,
    ) -> str | None:
        dlg = QDialog(parent)
        dlg.setWindowTitle(title)
        dlg.setModal(True)
        dlg.setMinimumWidth(360)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(label))
        combo = QComboBox()
        combo.setEditable(editable)
        if editable:
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem("")
        for opt in options:
            combo.addItem(opt)
        lay.addWidget(combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return combo.currentText().strip() or None

    # ── Callbacks modèle ──────────────────────────────────────────────────────

    def _on_model_data_changed(
        self, _tl: QModelIndex, _br: QModelIndex, roles: list[int]
    ) -> None:
        if Qt.ItemDataRole.CheckStateRole in roles:
            self._update_action_buttons()
            self._update_count()

    def _on_saved(self, _msg: str) -> None:
        self._update_count()

    def _on_save_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Erreur de sauvegarde", msg)
