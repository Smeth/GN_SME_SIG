"""Vue Edition SME — modification rapide en ligne avec filtres, actions et édition en masse."""

from __future__ import annotations

from copy import deepcopy

from sme_gend.ui.filter_change import row_filter_change
from sme_gend.ui.personnel_advanced_filter import advanced_rules_match
from sme_gend.ui.personnel_advanced_filter_panel import PersonnelAdvancedFilterPanel
from sme_gend.ui.personnel_list_filters import (
    free_text_matches, grade_matches_filter, padmin_matches_filter, structure_matches_filter,
)
from sme_gend.services.matricule_format import matricule_solde_is_empty
from sme_gend.services.grade_nomination import GradeNomination

import csv
import html
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSettings,
    QSortFilterProxyModel,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QShowEvent
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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.edition_sme_support import (
    CareerAlertsScanWorker,
    OPTIONAL_COLUMN_DEFS,
    import_csv_updates,
)
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox

from sme_gend.config.branding import APP_NAME, APP_SUBTITLE, QSETTINGS_ORG
_SETTINGS_APP = "EditionSME"

_LOG = logging.getLogger(__name__)

# ── Colonnes ──────────────────────────────────────────────────────────────────
_CORE_COLS: list[tuple[str, str, str]] = [
    ("id",                  "N°",                "readonly"),
    ("noms",                "Nom et prénom",      "text"),
    ("grade",               "Grade",              "combo"),
    ("structure",           "Structure",          "combo_ed"),
    ("subdivision_arme",    "Subdivision d'arme", "combo_ed"),
    ("position_admin",      "Position admin.",    "combo_ed"),
    ("fonction",            "Fonction",           "combo_ed"),
    ("matricule_militaire", "Mat. militaire",     "text"),
    ("matricule_solde",     "Mat. solde",         "text"),
]
_COLS: list[tuple[str, str, str]] = _CORE_COLS + list(OPTIONAL_COLUMN_DEFS)
_CORE_COL_COUNT = len(_CORE_COLS)

_EDITABLE: set[int] = {i for i, c in enumerate(_COLS) if c[2] != "readonly"}
_COL_IDX: dict[str, int] = {c[0]: i for i, c in enumerate(_COLS)}
_COL_STRUCTURE   = _COL_IDX["structure"]
_COL_SUBDIVISION = _COL_IDX["subdivision_arme"]

_MAX_UNDO = 20
from sme_gend.ui.row_quality_colors import (
    highlight_find_current,
    highlight_find_match,
    highlight_incomplete,
    highlight_modified,
)

_COLOR_DUP_FG     = QColor("#c0392b")  # rouge — matricule en doublon (texte)


@dataclass
class _EditionUndoEntry:
    """Trace d'une modification de cellule — undo et journal de session."""

    seq: int
    row_id: str
    row_label: str
    field: str
    old_val: str | None
    new_val: str | None
    ts_utc: str
    active: bool = True
    extra_old: dict[str, Any] = field(default_factory=dict)
    extra_new: dict[str, Any] = field(default_factory=dict)

    @property
    def field_label(self) -> str:
        from sme_gend.services.personnel_dossier_history import field_label

        base = field_label(self.field)
        if not self.extra_old:
            return base
        extras = ", ".join(field_label(f) for f in self.extra_old)
        return f"{base} (+ {extras})"

    def format_old(self) -> str:
        return _format_undo_bundle_display(self.field, self.old_val, self.extra_old)

    def format_new(self) -> str:
        return _format_undo_bundle_display(self.field, self.new_val, self.extra_new)

    def ts_display(self) -> str:
        from sme_gend.services.personnel_dossier_history import format_ts_display

        return format_ts_display(self.ts_utc)


def _format_undo_display(val: str | None) -> str:
    if val is None:
        return "—"
    s = str(val).strip()
    return s if s else "—"


def _undo_scalar_display(val: Any) -> str:
    if val is None:
        return "—"
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%d/%m/%Y")
        except Exception:
            pass
    if isinstance(val, datetime):
        try:
            return val.strftime("%d/%m/%Y")
        except Exception:
            pass
    s = str(val).strip()
    return s if s else "—"


def _format_undo_bundle_display(
    main_field: str,
    main_val: Any,
    extra: dict[str, Any],
) -> str:
    from sme_gend.services.personnel_dossier_history import field_label

    parts = [f"{field_label(main_field)}: {_undo_scalar_display(main_val)}"]
    for f, v in extra.items():
        parts.append(f"{field_label(f)}: {_undo_scalar_display(v)}")
    return " · ".join(parts)


def _table_sort_order_code(order: Qt.SortOrder) -> int:
    """0 = ascendant, 1 = descendant (sérialisation sans int() sur l'énum Qt)."""
    return 1 if order == Qt.SortOrder.DescendingOrder else 0


def _table_sort_order_from_code(code: object) -> Qt.SortOrder:
    if code == Qt.SortOrder.DescendingOrder or code == 1:
        return Qt.SortOrder.DescendingOrder
    return Qt.SortOrder.AscendingOrder


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_edition_num(n: int) -> str:
    """Nombre avec séparateur de milliers insécable (espace fine)."""
    return f"{n:,}".replace(",", "\u202f")


def _edition_count_label(visible: int, total: int) -> str:
    """Libellé compteur — visible = lignes filtrées, total = liste chargée."""
    if total <= 0:
        return "Aucun personnel chargé"
    if visible != total:
        return (
            f"{_fmt_edition_num(visible)} affiché(s) sur "
            f"{_fmt_edition_num(total)} personnels"
        )
    return f"{_fmt_edition_num(total)} personnel(s)"



def _format_cell_value(val: Any) -> str:
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%d/%m/%Y")
        except (TypeError, ValueError, AttributeError):
            pass
    return str(val)


# ── Proxy filtrant ────────────────────────────────────────────────────────────

class _EditionFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grade_sort = None
        self._grade_column = _COL_IDX["grade"]
        self._text            = ""
        self._structure       = ""
        self._grade           = ""
        self._grade_display   = ""
        self._padmin          = ""
        self._subarme         = ""
        self._duplicates_only = False
        self._incomplete_only = False
        self._modified_only = False
        self._no_mat_mil = False
        self._no_mat_solde = False
        self._presents_only = False
        self._advanced_rules = []
        self._advanced_match_mode = 'and'
        self._structure_view_filter: Callable[[ListeGeneraleRow, str], bool] | None = None
        self._custom_structure_view = False
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortRole(Qt.ItemDataRole.DisplayRole)

    def set_grade_sort_helper(self, helper, grade_column: int | None = None) -> None:
        self._grade_sort = helper
        if grade_column is not None:
            self._grade_column = grade_column

    @row_filter_change
    def set_text(self, v: str)           -> None: self._text            = v.strip().upper()
    @row_filter_change
    def set_structure(self, v: str)      -> None: self._structure       = v
    @row_filter_change
    def set_grade(self, v: str, *, combo_display: str = "") -> None:
        self._grade = v
        self._grade_display = combo_display or v
    @row_filter_change
    def set_padmin(self, v: str)         -> None: self._padmin          = v
    @row_filter_change
    def set_subarme(self, v: str)        -> None: self._subarme         = v
    @row_filter_change
    def set_duplicates_only(self, v: bool) -> None: self._duplicates_only = v
    @row_filter_change
    def set_incomplete_only(self, v: bool) -> None: self._incomplete_only = v
    @row_filter_change
    def set_modified_only(self, v: bool) -> None: self._modified_only = v
    @row_filter_change
    def set_no_mat_mil(self, v: bool) -> None: self._no_mat_mil = v
    @row_filter_change
    def set_no_mat_solde(self, v: bool) -> None: self._no_mat_solde = v
    @row_filter_change
    def set_presents_only(self, v: bool) -> None: self._presents_only = v

    @row_filter_change
    def set_advanced_rules(self, rules, mode='and'):
        self._advanced_rules = list(rules)
        self._advanced_match_mode = mode

    @row_filter_change
    def set_structure_view_filter(
        self,
        fn: Callable[[ListeGeneraleRow, str], bool] | None,
    ) -> None:
        self._structure_view_filter = fn
        self._custom_structure_view = fn is not None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return super().headerData(section, orientation, role)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        # Qt passe des indices du modèle source (pas du proxy).
        m = self.sourceModel()
        if isinstance(m, _EditionModel):
            col = self.sortColumn()
            sl, sr = left.row(), right.row()
            if col == self._grade_column and self._grade_sort is not None:
                if 0 <= sl < len(m._rows) and 0 <= sr < len(m._rows):
                    return self._grade_sort.compare_less(
                        m._rows[sl].grade, m._rows[sr].grade
                    )
            if col == 0:
                if 0 <= sl < len(m._rows) and 0 <= sr < len(m._rows):
                    try:
                        return int(m._rows[sl].id) < int(m._rows[sr].id)
                    except (TypeError, ValueError):
                        pass
        return super().lessThan(left, right)

    def filterAcceptsRow(self, src_row: int, _src_parent: QModelIndex) -> bool:
        m = self.sourceModel()
        if not isinstance(m, _EditionModel) or src_row >= len(m._rows):
            return False
        row = m._rows[src_row]
        if self._modified_only and row.id not in m._modified_ids:
            return False
        if self._duplicates_only and row.id not in m._duplicate_ids:
            return False
        if self._incomplete_only and row.id not in m._incomplete_ids:
            return False
        if self._no_mat_mil and (row.matricule_militaire or "").strip():
            return False
        if self._no_mat_solde and not matricule_solde_is_empty(row.matricule_solde):
            return False
        if self._presents_only:
            from sme_gend.padmin_present import padmin_counts_as_present

            if not padmin_counts_as_present(row.position_admin, "present"):
                return False
        if self._custom_structure_view and self._structure_view_filter is not None:
            if not self._structure_view_filter(row, self._structure):
                return False
        elif self._structure and not structure_matches_filter(row.structure, self._structure):
            return False
        if self._grade and not grade_matches_filter(
            row.grade, self._grade, combo_display=self._grade_display, grades=m._grade_rows
        ):
            return False
        if self._padmin and not padmin_matches_filter(row.position_admin, self._padmin):
            return False
        if self._subarme:
            row_sub = (row.subdivision_arme or "").strip()
            if row_sub.casefold() != self._subarme.strip().casefold():
                return False
        if self._text:
            if not free_text_matches(row, self._text, grades=m._grade_rows):
                return False
        return advanced_rules_match(row, self._advanced_rules, mode=self._advanced_match_mode)


# ── Modèle ────────────────────────────────────────────────────────────────────

class _EditionModel(QAbstractTableModel):
    save_ok  = Signal(str)
    save_err = Signal(str)
    padmin_changed = Signal(object, object, object)  # row, old_padmin, new_padmin

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ListeGeneraleRow] = []
        self._repo = None
        self._personnel = None
        self.grades:     list[str] = []
        self.structures: list[str] = []
        self.subarmes:   list[str] = []
        self.padmins:    list[str] = []
        self._grade_rows: list = []
        self._subdivision_flat: list = []
        self._undo_stack: list[_EditionUndoEntry] = []
        self._session_log: list[_EditionUndoEntry] = []
        self._cell_change_hint: dict[tuple[str, str], _EditionUndoEntry] = {}
        self._undo_seq = 0
        self._modified_ids:   set[str] = set()
        self._incomplete_ids: set[str] = set()
        self._duplicate_ids:  set[str] = set()
        self._dup_mil_ids:    set[str] = set()
        self._dup_sol_ids:    set[str] = set()
        self._checked_ids:    set[str] = set()
        self._find_hit_ids: set[str] = set()
        self._find_current_id: str | None = None
        self._settings = None
        self._read_only = False
        self.position_change_prompt: Callable | None = None
        self.grade_nomination_prompt: Callable[
            [ListeGeneraleRow, str | None, str | None], datetime | GradeNomination | None
        ] | None = None

    def set_read_only(self, read_only: bool) -> None:
        if self._read_only == read_only:
            return
        self._read_only = read_only
        if self._rows:
            self.layoutChanged.emit()

    def _after_db_mutation(self) -> None:
        if self._personnel is not None:
            return
        if self._settings is not None:
            from sme_gend.services.liste_generale_cache import invalidate_liste_cache

            invalidate_liste_cache(self._settings)

    # ── Journal de session / undo ─────────────────────────────────────────────

    def _record_change(
        self,
        row: ListeGeneraleRow,
        field: str,
        old_val: str | None,
        new_val: str | None,
        *,
        extra: dict[str, tuple[Any, Any]] | None = None,
        undoable: bool = True,
    ) -> _EditionUndoEntry:
        """Enregistre une modification pour l'undo et le journal de session."""
        extra_old: dict[str, Any] = {}
        extra_new: dict[str, Any] = {}
        if extra:
            for k, (o, n) in extra.items():
                extra_old[k] = o
                extra_new[k] = n
        self._undo_seq += 1
        entry = _EditionUndoEntry(
            seq=self._undo_seq,
            row_id=row.id,
            row_label=(row.noms or row.id or "?").strip(),
            field=field,
            old_val=old_val,
            new_val=new_val,
            ts_utc=_utc_now_iso(),
            extra_old=extra_old,
            extra_new=extra_new,
        )
        entry.active = undoable
        if undoable:
            self._undo_stack.append(entry)
        if len(self._undo_stack) > _MAX_UNDO:
            self._undo_stack.pop(0)
        self._session_log.append(entry)
        self._cell_change_hint[(row.id, field)] = entry
        return entry

    def _refresh_cell_hint(self, row_id: str, field: str) -> None:
        """Recale l'infobulle « valeur précédente » sur la dernière trace active."""
        key = (row_id, field)
        for entry in reversed(self._undo_stack):
            if entry.row_id == row_id and entry.field == field and entry.active:
                self._cell_change_hint[key] = entry
                return
        self._cell_change_hint.pop(key, None)

    def peek_undo(self) -> _EditionUndoEntry | None:
        """Dernière modification annulable, sans la dépiler."""
        return self._undo_stack[-1] if self._undo_stack else None

    def _undo_revert_kwargs(self, entry: _EditionUndoEntry) -> dict[str, Any]:
        kw: dict[str, Any] = {entry.field: entry.old_val}
        kw.update(entry.extra_old)
        return kw

    def _emit_row_fields_changed(self, src_row: int, fields: set[str]) -> None:
        roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.ToolTipRole,
        ]
        cols = {_COL_IDX[f] for f in fields if f in _COL_IDX}
        if not cols:
            return
        for col in cols:
            idx = self.index(src_row, col)
            self.dataChanged.emit(idx, idx, roles)

    def active_undo_count(self) -> int:
        return len(self._undo_stack)

    def session_log_entries(self) -> list[_EditionUndoEntry]:
        """Journal de session — plus récent en premier."""
        return list(reversed(self._session_log))

    def _persist_update(self, before: ListeGeneraleRow, after: ListeGeneraleRow) -> None:
        self._last_grade_history_notice = None
        self._last_position_notice = None
        if self._personnel is not None:
            self._personnel.update_personnel(before, after)
        elif self._repo is not None:
            if self._settings is not None:
                from sme_gend.services.agent_dossier_sync import sync_dossier_after_personnel_save
                from sme_gend.services.personnel_service import _personnel_write
                with _personnel_write(self._settings):
                    self._repo.update_row(after, expected=before)
                    sync_dossier_after_personnel_save(self._settings, before, after)
            else:
                self._repo.update_row(after)
        else:
            raise RuntimeError("Aucun service de persistance configuré.")
        grade_changed = any(getattr(before, name) != getattr(after, name)
                            for name in ('grade', 'date_grade', 'ndecision'))
        if grade_changed:
            reader = getattr(self._personnel, 'get_by_id', None) if self._personnel is not None else getattr(self._repo, 'fetch_by_id', None)
            saved = reader(after.id) if callable(reader) else None
            if isinstance(saved, ListeGeneraleRow):
                from sme_gend.services.grade_save_notice import grade_save_notice
                self._last_grade_history_notice = grade_save_notice(after, saved)
                for name in ('grade', 'date_grade', 'ndecision'):
                    setattr(after, name, getattr(saved, name))
        sc = (after.structure or "").strip()
        fn = (after.fonction or "").strip()
        if sc and fn and self._settings is not None:
            try:
                from sme_gend.services.referentiel_service import ReferentielService

                ReferentielService(self._settings).fonction_ensure_structure_link(sc, fn)
            except Exception:
                # Rattachement auxiliaire : ne bloque pas la saisie, mais on trace
                # l'échec au lieu de le laisser disparaître sans trace.
                _LOG.warning(
                    "fonction_ensure_structure_link(%r, %r) a échoué", sc, fn, exc_info=True
                )

    def _persist_requested_update(self, before, after) -> bool:
        write_position = True
        if before.position_admin != after.position_admin and callable(self.position_change_prompt):
            write_position = self.position_change_prompt(after, before.position_admin, after.position_admin)
            if write_position is False or write_position is None:
                return False
        if callable(write_position):
            if self._settings is None:
                raise RuntimeError("Configuration manquante pour enregistrer la fiche de position.")
            from sme_gend.services.personnel_service import _personnel_write
            from sme_gend.services.temporal_events import position_edit
            with _personnel_write(self._settings), position_edit(self._settings,before):
                self._persist_update(before, after)
                write_position()
        else:
            self._persist_update(before, after)
        if before.position_admin != after.position_admin and self._personnel is not None:
            reader=getattr(self._personnel,'get_by_id',None)
            saved=reader(after.id) if callable(reader) else None
            if isinstance(saved,ListeGeneraleRow):
                self._last_position_notice=("Événement enregistré selon ses dates ; la position affichée correspond au mois consulté."
                    if after.position_admin!=saved.position_admin else None)
                after.position_admin=saved.position_admin
        return True

    def subdivision_choices(self, structure: str | None) -> list[str]:
        """Subdivisions distinctes observées dans le mois affiché (optionnellement par structure)."""
        seen: set[str] = set()
        out: list[str] = []
        struct_key = (structure or "").strip() if structure is not None else None
        for r in self._rows:
            if struct_key is not None and (r.structure or "").strip() != struct_key:
                continue
            sub = (r.subdivision_arme or "").strip()
            if not sub or sub in seen:
                continue
            seen.add(sub)
            out.append(sub)
        out.sort()
        return out

    def register_subdivision_label(self, label: str) -> None:
        """Ajoute un libellé SubArme aux listes du modèle (création / action groupée)."""
        lib = (label or "").strip()
        if not lib:
            return
        key = lib.casefold()
        if not any((s or "").strip().casefold() == key for s in self.subarmes):
            self.subarmes = sorted({*self.subarmes, lib}, key=str.casefold)

    def load(
        self,
        rows: list[ListeGeneraleRow],
        repo,
        grades: list[str],
        structures: list[str],
        subarmes: list[str],
        padmins: list[str],
        *,
        settings=None,
        personnel=None,
        grade_rows=None,
        subdivision_flat=None,
    ) -> None:
        self.beginResetModel()
        self._rows      = rows
        self._repo      = repo
        self._personnel = personnel
        self._settings  = settings
        self.grades     = grades
        self.structures = structures
        self.subarmes   = subarmes
        self.padmins    = padmins
        self._grade_rows = list(grade_rows or [])
        self._subdivision_flat = list(subdivision_flat or [])
        self._undo_stack.clear()
        self._session_log.clear()
        self._cell_change_hint.clear()
        self._undo_seq = 0
        self._modified_ids.clear()
        self._checked_ids.clear()
        self._recompute_status_sets()
        self.endResetModel()

    def set_find_highlights(
        self,
        *,
        current_id: str | None,
        hit_ids: set[str] | frozenset[str],
    ) -> None:
        new_hits = set(hit_ids)
        if current_id == self._find_current_id and new_hits == self._find_hit_ids:
            return
        self._find_current_id = current_id
        self._find_hit_ids = new_hits
        if not self._rows:
            return
        tl = self.index(0, 0)
        br = self.index(len(self._rows) - 1, len(_COLS) - 1)
        self.dataChanged.emit(
            tl,
            br,
            [
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ForegroundRole,
            ],
        )

    def clear_find_highlights(self) -> None:
        self.set_find_highlights(current_id=None, hit_ids=set())

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
        if orientation == Qt.Orientation.Horizontal:
            if section == 0 and role == Qt.ItemDataRole.ToolTipRole:
                return "Cocher pour les actions groupées · Numéro agent"
            if role == Qt.ItemDataRole.DisplayRole:
                return _COLS[section][1]
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = super().flags(index)
        col = index.column()
        if col == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if col in _EDITABLE:
            if self._read_only:
                return base
            field = _COLS[col][0]
            from sme_gend.services.auth_guard import session_has_admin_role
            from sme_gend.services.sensitive_data_policy import (
                CAREER_SENSITIVE_FIELDS,
                IDENTITY_ONLY_PERSONNEL_FIELDS,
                session_may_edit_career_fields,
            )

            if field in IDENTITY_ONLY_PERSONNEL_FIELDS and not session_has_admin_role():
                return base
            if field in CAREER_SENSITIVE_FIELDS and not session_may_edit_career_fields():
                return base
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

        if role == Qt.ItemDataRole.DisplayRole:
            val = getattr(row, field, None)
            if field == "grade":
                from sme_gend.services.grade_label_format import format_grade_display

                return format_grade_display(val, self._grade_rows)
            if field == "subdivision_arme" and self._subdivision_flat:
                from sme_gend.services.subdivision_label_format import (
                    format_subdivision_arme_display,
                )

                return format_subdivision_arme_display(val, self._subdivision_flat)
            return _format_cell_value(val)
        if role == Qt.ItemDataRole.EditRole:
            val = getattr(row, field, None)
            return _format_cell_value(val)

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.TextAlignmentRole and field == "id":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.BackgroundRole:
            if self._find_current_id and row.id == self._find_current_id:
                return highlight_find_current().bg
            if row.id in self._find_hit_ids:
                return highlight_find_match().bg
            if row.id in self._modified_ids:
                return highlight_modified().bg
            if row.id in self._incomplete_ids:
                return highlight_incomplete().bg
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if self._find_current_id and row.id == self._find_current_id:
                return highlight_find_current().fg
            if row.id in self._find_hit_ids:
                return highlight_find_match().fg
            if row.id in self._modified_ids:
                return highlight_modified().fg
            if row.id in self._incomplete_ids:
                return highlight_incomplete().fg
            if field == "matricule_militaire" and row.id in self._dup_mil_ids:
                return _COLOR_DUP_FG
            if field == "matricule_solde" and row.id in self._dup_sol_ids:
                return _COLOR_DUP_FG
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            tips: list[str] = []
            hint = self._cell_change_hint.get((row.id, field))
            if hint is not None and hint.active:
                tips.append(
                    f"Modification session : {hint.format_old()} → {hint.format_new()}\n"
                    "Ctrl+Z annule la dernière étape."
                )
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
            checked = (
                value == Qt.CheckState.Checked
                or value == Qt.CheckState.Checked.value
            )
            if checked:
                self._checked_ids.add(row.id)
            else:
                self._checked_ids.discard(row.id)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True

        if role != Qt.ItemDataRole.EditRole or index.column() not in _EDITABLE:
            return False
        if self._read_only:
            return False
        row   = self._rows[index.row()]
        field = _COLS[index.column()][0]
        old_val = getattr(row, field, None)
        new_val: str | None = (str(value) if value is not None else "").strip() or None
        if old_val == new_val:
            return False
        nomination_dt: datetime | None = None
        nomination_reference: str | None = None
        reference_provided = False
        if field == "grade":
            old_g = (old_val or "").strip() if isinstance(old_val, str) else str(old_val or "").strip()
            new_g = (new_val or "").strip()
            prompt = self.grade_nomination_prompt
            if callable(prompt) and new_g and new_g.casefold() != old_g.casefold():
                choice = prompt(row, old_g or None, new_g)
                if choice is None:
                    return False
                if isinstance(choice, GradeNomination):
                    nomination_dt = choice.date_nomination
                    nomination_reference = choice.reference
                    reference_provided = True
                else:
                    nomination_dt = choice
        extra: dict[str, Any] = {}
        if nomination_dt is not None:
            extra["date_grade"] = nomination_dt
        if reference_provided:
            extra['ndecision'] = nomination_reference
        updated = replace(row, **{field: new_val}, **extra)
        try:
            if not self._persist_requested_update(row, updated):
                return False
        except Exception as exc:
            self.save_err.emit(f"{row.noms or '?'} — {exc}")
            return False
        bundle: dict[str, tuple[Any, Any]] | None = None
        if nomination_dt is not None:
            bundle = {"date_grade": (row.date_grade, nomination_dt)}
            if reference_provided:
                bundle['ndecision'] = (row.ndecision, nomination_reference)
        self._record_change(row, field, old_val, new_val, extra=bundle,
                            undoable=not bool(getattr(self, '_last_grade_history_notice', None)))
        self._rows[index.row()] = updated
        self._modified_ids.add(row.id)
        self._recompute_status_sets()
        self._after_db_mutation()
        self.dataChanged.emit(index, index, [
            role,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.ToolTipRole,
        ])
        self.save_ok.emit(getattr(self, '_last_position_notice', None) or getattr(self, '_last_grade_history_notice', None) or f"Enregistré : {updated.noms or updated.id}")
        if field == "position_admin":
            self.padmin_changed.emit(updated, old_val, new_val)
        return True

    def undo_last(self) -> str | None:
        if self._read_only:
            return "Modification impossible — période fermée."
        if not self._undo_stack:
            return None
        entry = self._undo_stack.pop()
        entry.active = False
        src_row = next((i for i, r in enumerate(self._rows) if r.id == entry.row_id), None)
        if src_row is None:
            self._refresh_cell_hint(entry.row_id, entry.field)
            return None
        row = self._rows[src_row]
        reverted = replace(row, **self._undo_revert_kwargs(entry))
        try:
            self._persist_update(row, reverted)
        except Exception as exc:
            entry.active = True
            self._undo_stack.append(entry)
            return f"Erreur undo : {exc}"
        self._rows[src_row] = reverted
        self._recompute_status_sets()
        self._after_db_mutation()
        touched = {entry.field, *entry.extra_old}
        for f in touched:
            self._refresh_cell_hint(entry.row_id, f)
        self._emit_row_fields_changed(src_row, touched)
        return (
            f"Annulé : {entry.field_label} — {entry.row_label} "
            f"({entry.format_new()} → {entry.format_old()})"
        )

    def undo_all_session(self) -> tuple[int, list[str]]:
        """Annule toutes les modifications de la session (et les persiste)."""
        if self._read_only:
            return 0, ["Modification impossible — période fermée."]
        n_undo = len(self._undo_stack)
        if not n_undo:
            return 0, []
        ok = 0
        errors: list[str] = []
        while self._undo_stack:
            entry = self._undo_stack.pop()
            entry.active = False
            src_row = next(
                (i for i, r in enumerate(self._rows) if r.id == entry.row_id), None
            )
            if src_row is None:
                self._refresh_cell_hint(entry.row_id, entry.field)
                continue
            row = self._rows[src_row]
            reverted = replace(row, **self._undo_revert_kwargs(entry))
            try:
                self._persist_update(row, reverted)
            except Exception as exc:
                entry.active = True
                self._undo_stack.append(entry)
                errors.append(f"{entry.row_label} ({entry.field_label}) : {exc}")
                break
            self._rows[src_row] = reverted
            touched = {entry.field, *entry.extra_old}
            for f in touched:
                self._refresh_cell_hint(entry.row_id, f)
            ok += 1
        if ok:
            self._recompute_status_sets()
            self._after_db_mutation()
            if self._rows:
                tl = self.index(0, 0)
                br = self.index(len(self._rows) - 1, len(_COLS) - 1)
                self.dataChanged.emit(tl, br, [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.BackgroundRole,
                    Qt.ItemDataRole.ForegroundRole,
                    Qt.ItemDataRole.ToolTipRole,
                ])
        return ok, errors

    # ── Opérations en masse ───────────────────────────────────────────────────

    def bulk_set_field(
        self, src_rows: list[int], field: str, value: str | None, *, reference_note: str | None = None
    ) -> tuple[int, int, list[str]]:
        """Retourne (modifiés, déjà conformes, erreurs)."""
        if reference_note is not None:
            from sme_gend.services.agent_dossier_sync import affectation_note_scope
            with affectation_note_scope(reference_note):
                return self.bulk_set_field(src_rows, field, value)
        if self._read_only:
            return 0, 0, ["Modification impossible — période fermée."]
        col = _COL_IDX.get(field)
        if col is None:
            return 0, 0, ["Champ inconnu"]
        new_val: str | None = (value or "").strip() or None
        updated_n = 0
        unchanged_n = 0
        errors: list[str] = []
        affected: list[int] = []
        for src_row in sorted(src_rows):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            if getattr(row, field, None) == new_val:
                unchanged_n += 1
                continue
            updated = replace(row, **{field: new_val})
            old_val = getattr(row, field, None)
            try:
                if not self._persist_requested_update(row, updated):
                    errors.append(f"{row.noms or row.id}: fiche complémentaire annulée.")
                    continue
                self._record_change(row, field, old_val, new_val,
                                    undoable=not bool(getattr(self, '_last_grade_history_notice', None)))
                self._rows[src_row] = updated
                self._modified_ids.add(row.id)
                affected.append(src_row)
                updated_n += 1
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
        if updated_n:
            self._after_db_mutation()
        return updated_n, unchanged_n, errors

    def bulk_set_grade(
        self,
        src_rows: list[int],
        grade: str,
        date_nomination: datetime,
        reference: str | None = None,
    ) -> tuple[int, int, list[str]]:
        """Enregistre une passation commune, avec référence facultative."""
        if self._read_only:
            return 0, 0, ["Modification impossible — période fermée."]
        new_grade = (grade or "").strip() or None
        if not new_grade:
            return 0, 0, ["Grade requis."]
        col = _COL_IDX.get("grade")
        if col is None:
            return 0, 0, ["Champ grade inconnu."]
        updated_n = 0
        unchanged_n = 0
        errors: list[str] = []
        affected: list[int] = []
        for src_row in sorted(src_rows):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            same_grade = (row.grade or "").strip() == new_grade
            old_dg = row.date_grade
            same_date = old_dg == date_nomination or (
                old_dg is not None
                and hasattr(old_dg, "date")
                and old_dg.date() == date_nomination.date()
            )
            new_reference = (reference or '').strip() or None
            same_reference = new_reference is None or (row.ndecision or '').strip() == new_reference
            if same_grade and same_date and same_reference:
                unchanged_n += 1
                continue
            updated = replace(row, grade=new_grade, date_grade=date_nomination,
                              ndecision=new_reference if new_reference is not None else row.ndecision)
            try:
                self._persist_update(row, updated)
                primary = 'grade' if not same_grade else 'date_grade' if not same_date else 'ndecision'
                requested = {'grade': new_grade, 'date_grade': date_nomination,
                             'ndecision': new_reference if new_reference is not None else row.ndecision}
                extra = {field: (getattr(row, field), value) for field, value in requested.items()
                         if field != primary and getattr(row, field) != value}
                self._record_change(row, primary, getattr(row, primary), requested[primary],
                                    extra=extra or None,
                                    undoable=not bool(getattr(self, '_last_grade_history_notice', None)))
                self._rows[src_row] = updated
                self._modified_ids.add(row.id)
                affected.append(src_row)
                updated_n += 1
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
        if updated_n:
            self._after_db_mutation()
        return updated_n, unchanged_n, errors

    def remove_rows(self, src_rows: list[int], *, motif: str = "") -> tuple[int, list[str]]:
        if self._read_only:
            return 0, ["Suppression impossible — période fermée."]
        loss_reason = (motif or "").strip()
        if not loss_reason:
            return 0, ["Suppression impossible — le motif de perte est obligatoire."]
        personnel = self._personnel
        delete_fn = getattr(personnel, "delete_personnel", None) if personnel is not None else None
        if not callable(delete_fn):
            return 0, ["Suppression impossible — service personnel indisponible."]
        ok = 0
        errors: list[str] = []
        for src_row in sorted(src_rows, reverse=True):
            if not (0 <= src_row < len(self._rows)):
                continue
            row = self._rows[src_row]
            try:
                delete_fn(row, motif=loss_reason)
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
            self._after_db_mutation()
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
        if self._read_only:
            return 0, ["Modification impossible — période fermée."]
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
                if not self._persist_requested_update(row, updated):
                    errors.append(f"{row.noms or row.id}: fiche complémentaire annulée.")
                    continue
                for field in changes:
                    self._record_change(row, field, getattr(row, field, None), changes[field],
                                        undoable=not bool(getattr(self, '_last_grade_history_notice', None)))
                self._rows[src_row] = updated
                self._modified_ids.add(row.id)
                affected_rows.append(src_row)
                ok += 1
            except Exception as exc:
                errors.append(f"{row.noms or '?'} : {exc}")

        if affected_rows:
            self._recompute_status_sets()
            self._after_db_mutation()
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

    def _style_inline_editor(self, editor: QWidget) -> None:
        """Éditeur pleine cellule — évite le QLineEdit global (padding / min-height)."""
        editor.setProperty("inlineTableEdit", True)
        editor.setAutoFillBackground(True)
        editor.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        editor.style().unpolish(editor)
        editor.style().polish(editor)

    def createEditor(self, parent: QWidget, _opt: QStyleOptionViewItem,
                     index: QModelIndex) -> QWidget | None:
        col = self._src_col(index)
        if col not in _EDITABLE:
            return None
        field, _, etype = _COLS[col]
        if etype == "text":
            editor = QLineEdit(parent)
            self._style_inline_editor(editor)
            return editor
        box = NoWheelComboBox(parent)
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
        elif field == "fonction":
            options = self._fonction_options_for_row(self._src_row(index))
        else:
            options = self._m.padmins
        box.addItem("")
        for opt in options:
            box.addItem(opt)
        if field == 'position_admin':
            box.activated.connect(lambda _index, editor=box: self._commit_position_choice(editor))
        self._style_inline_editor(box)
        return box

    def _commit_position_choice(self, editor):
        if editor.property('positionCommitQueued'):
            return
        editor.setProperty('positionCommitQueued', True)
        def commit():
            from shiboken6 import isValid
            if not isValid(self) or not isValid(editor):
                return
            self.commitData.emit(editor)
            if isValid(editor):
                self.closeEditor.emit(editor)
        QTimer.singleShot(0, commit)

    def _subdivision_options_for_row(self, src_row: int) -> list[str]:
        if not (0 <= src_row < len(self._m._rows)):
            return self._m.subarmes
        row = self._m._rows[src_row]
        structure = (row.structure or "").strip()
        current = (row.subdivision_arme or "").strip()
        from_month = (
            self._m.subdivision_choices(structure)
            if structure
            else self._m.subdivision_choices(None)
        )
        if from_month:
            options = list(from_month)
            if current and current not in options:
                options.append(current)
                options.sort()
            return options
        if structure and self._m._settings:
            try:
                from sme_gend.services.referentiel_service import ReferentielService

                return ReferentielService(self._m._settings).subdivision_arme_choices_for_structure(
                    structure
                )
            except Exception:
                pass
        return self._m.subarmes

    def _structure_meta(self, code: str) -> tuple[str | None, str | None]:
        cache = getattr(self, "_structure_meta_cache", None)
        if cache is None:
            cache = {}
            self._structure_meta_cache = cache
        if code in cache:
            return cache[code]
        designation = None
        famille = None
        if code and self._m._settings:
            try:
                from sme_gend.repositories.structure_repository import StructureRepository

                st = StructureRepository(self._m._settings).fetch_by_code(code)
                if st is not None:
                    designation = st.designation
                    famille = st.famille_unite
            except Exception:
                pass
        cache[code] = (designation, famille)
        return designation, famille

    def _fonction_options_for_row(self, src_row: int) -> list[str]:
        if not (0 <= src_row < len(self._m._rows)):
            return []
        row = self._m._rows[src_row]
        structure = (row.structure or "").strip()
        subdivision = (row.subdivision_arme or "").strip()
        current = (row.fonction or "").strip()
        same_sub: dict[str, str] = {}
        same_struct: dict[str, str] = {}
        for other in self._m._rows:
            if (other.structure or "").strip() != structure:
                continue
            fn = (other.fonction or "").strip()
            if not fn:
                continue
            key = fn.casefold()
            bucket = (
                same_sub
                if (other.subdivision_arme or "").strip() == subdivision
                else same_struct
            )
            bucket.setdefault(key, fn)
        linked: list[str] = []
        if structure and self._m._settings:
            try:
                from sme_gend.services.referentiel_service import ReferentielService

                linked = ReferentielService(self._m._settings).fonction_choices_for_structure(
                    structure
                )
            except Exception:
                linked = []
        designation, famille = self._structure_meta(structure) if structure else (None, None)
        from sme_gend.services.fonction_structure_choices import (
            build_fonction_choice_libelles,
            merge_fonction_suggestions,
        )

        expected = build_fonction_choice_libelles(
            linked,
            structure_code=structure or None,
            designation=designation,
            famille=famille,
            subdivision=subdivision or None,
        )
        return merge_fonction_suggestions(
            current=current,
            used_same_subdivision=sorted(same_sub.values(), key=str.casefold),
            used_same_structure=sorted(same_struct.values(), key=str.casefold),
            linked_and_expected=expected,
        )

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
            else:
                # Valeur absente des choix (grade hors référentiel, libellé
                # historique…). Sans ce repli le combo restait sur l'entrée vide
                # « » et la validation effaçait le champ (perte de donnée).
                editor.addItem(val)
                editor.setCurrentIndex(editor.count() - 1)

    def setModelData(self, editor: QWidget, model: Any, index: QModelIndex) -> None:
        if _COLS[self._src_col(index)][0] == 'position_admin':
            if editor.property('positionCommitDone'):
                return
            editor.setProperty('positionCommitDone', True)
        val = (editor.text() if isinstance(editor, QLineEdit)
               else editor.currentText() if isinstance(editor, QComboBox) else None)
        if val is not None:
            model.setData(index, val, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(
        self,
        editor: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        view = self.parent()
        if isinstance(view, QAbstractItemView):
            rect = view.visualRect(index)
        else:
            rect = option.rect
        editor.setGeometry(rect)


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

def _field_matches_find(
    val: str,
    *,
    find_text: str,
    partial: bool,
) -> bool:
    if partial:
        return find_text.lower() in val.lower()
    return val.lower() == find_text.lower()


def _replacement_value(
    val: str,
    *,
    find_text: str,
    partial: bool,
    replace_raw: str,
) -> str | None:
    if partial:
        out = re.sub(
            re.escape(find_text), replace_raw, val, flags=re.IGNORECASE
        ).strip()
    else:
        out = replace_raw.strip()
    return out or None


class _FindReplaceDialog(QDialog):
    """Recherche type Excel : parcourir les occurrences, surligner, remplacer une ou toutes."""

    def __init__(self, view: EditionSmeView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._proxy = view._proxy
        self._model = view._model
        self.setWindowTitle("Rechercher / Remplacer")
        self.setModal(False)
        self.setMinimumWidth(520)

        self._hits: list[tuple[int, str]] = []
        self._hit_index = -1

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
        self._chk_partial.setToolTip(
            "Décoché : correspondance exacte (recommandé). Coché : la valeur contient le texte recherché."
        )
        form.addRow("", self._chk_partial)

        lay.addLayout(form)

        self._lbl_preview = QLabel("Saisissez un texte puis cliquez « Suivant ».")
        self._lbl_preview.setObjectName("dashSubtitle")
        self._lbl_preview.setWordWrap(True)
        lay.addWidget(self._lbl_preview)

        btn_row = QHBoxLayout()
        self._btn_prev = QPushButton("◀ Précédent")
        self._btn_prev.setToolTip("Occurrence précédente (Maj+F3)")
        self._btn_prev.clicked.connect(self._find_previous)
        self._btn_next = QPushButton("Suivant ▶")
        self._btn_next.setToolTip("Occurrence suivante (F3)")
        self._btn_next.setDefault(True)
        self._btn_next.clicked.connect(self._find_next)
        self._btn_replace = QPushButton("Remplacer")
        self._btn_replace.setToolTip("Remplacer l'occurrence courante puis passer à la suivante")
        self._btn_replace.clicked.connect(self._replace_current)
        self._btn_replace_all = QPushButton("Tout remplacer…")
        self._btn_replace_all.setToolTip("Remplacer toutes les occurrences visibles")
        self._btn_replace_all.clicked.connect(self._replace_all)
        self._btn_close = QPushButton("Fermer")
        self._btn_close.clicked.connect(self.close)
        for b in (
            self._btn_prev,
            self._btn_next,
            self._btn_replace,
            self._btn_replace_all,
            self._btn_close,
        ):
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._find_debounce = QTimer(self)
        self._find_debounce.setSingleShot(True)
        self._find_debounce.setInterval(180)
        self._find_debounce.timeout.connect(self._apply_find_search)
        self._txt_find.textChanged.connect(self._on_search_changed)
        self._cmb_col.currentIndexChanged.connect(self._on_search_changed)
        self._chk_partial.stateChanged.connect(self._on_search_changed)
        self._txt_find.returnPressed.connect(self._find_next)

        QShortcut(QKeySequence("F3"), self, self._find_next)
        QShortcut(QKeySequence("Shift+F3"), self, self._find_previous)

        self._sync_buttons()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._find_debounce.stop()
        self._model.clear_find_highlights()
        super().closeEvent(event)

    def _fields_to_search(self) -> list[str]:
        col_data = self._cmb_col.currentData()
        if col_data == "*":
            return [_COLS[i][0] for i in sorted(_EDITABLE)]
        return [col_data] if col_data else []

    def _rebuild_hits(self) -> None:
        self._find_debounce.stop()
        find_text = self._txt_find.text()
        if not find_text:
            self._hits = []
            self._hit_index = -1
            self._model.clear_find_highlights()
            return
        partial = self._chk_partial.isChecked()
        fields = self._fields_to_search()
        hits: list[tuple[int, str]] = []
        for pr in range(self._proxy.rowCount()):
            src_idx = self._proxy.mapToSource(self._proxy.index(pr, 0))
            sr = src_idx.row()
            if not (0 <= sr < len(self._model._rows)):
                continue
            row = self._model._rows[sr]
            for field in fields:
                val = getattr(row, field, None) or ""
                if _field_matches_find(val, find_text=find_text, partial=partial):
                    hits.append((sr, field))
        self._hits = hits
        if self._hit_index >= len(self._hits):
            self._hit_index = len(self._hits) - 1
        if self._hits and self._hit_index < 0:
            self._hit_index = 0
        self._apply_highlights()

    def _apply_highlights(self) -> None:
        if not self._hits:
            self._model.clear_find_highlights()
            return
        hit_ids = {self._model._rows[sr].id for sr, _ in self._hits if 0 <= sr < len(self._model._rows)}
        current_id = None
        if 0 <= self._hit_index < len(self._hits):
            sr, _ = self._hits[self._hit_index]
            if 0 <= sr < len(self._model._rows):
                current_id = self._model._rows[sr].id
        self._model.set_find_highlights(current_id=current_id, hit_ids=hit_ids)

    def _hit_description(self, index: int) -> str:
        if not (0 <= index < len(self._hits)):
            return "—"
        sr, field = self._hits[index]
        if not (0 <= sr < len(self._model._rows)):
            return "—"
        row = self._model._rows[sr]
        col_label = next((lbl for key, lbl, _ in _COLS if key == field), field)
        val = _format_cell_value(getattr(row, field, None))
        nom = (row.noms or "—").strip()
        return (
            f"Occurrence {index + 1} / {len(self._hits)} — "
            f"N° {row.id} — {nom} — {col_label} : « {val} »"
        )

    def _sync_buttons(self) -> None:
        has_find = bool(self._txt_find.text().strip())
        has_hits = bool(self._hits)
        self._btn_prev.setEnabled(has_hits)
        self._btn_next.setEnabled(has_find)
        self._btn_replace.setEnabled(has_hits and 0 <= self._hit_index < len(self._hits))
        self._btn_replace_all.setEnabled(has_hits)
        if not has_find:
            self._lbl_preview.setText("Saisissez un texte puis cliquez « Suivant ».")
        elif not has_hits:
            self._lbl_preview.setText("Aucune correspondance dans les lignes visibles.")
        elif 0 <= self._hit_index < len(self._hits):
            self._lbl_preview.setText(self._hit_description(self._hit_index))
        else:
            self._lbl_preview.setText(f"{len(self._hits)} occurrence(s) — cliquez « Suivant ».")

    def _on_search_changed(self) -> None:
        self._hits = []
        self._hit_index = -1
        self._find_debounce.start()
        self._sync_buttons()

    def _apply_find_search(self) -> None:
        self._rebuild_hits()
        self._sync_buttons()

    def _focus_hit(self, index: int) -> None:
        if not (0 <= index < len(self._hits)):
            return
        self._hit_index = index
        sr, field = self._hits[index]
        self._apply_highlights()
        self._view.focus_find_hit(sr, field)
        self._sync_buttons()

    def _find_next(self) -> None:
        find_text = self._txt_find.text().strip()
        if not find_text:
            return
        if not self._hits:
            self._rebuild_hits()
        if not self._hits:
            self._sync_buttons()
            return
        next_idx = (self._hit_index + 1) % len(self._hits) if self._hit_index >= 0 else 0
        self._focus_hit(next_idx)

    def _find_previous(self) -> None:
        if self._find_debounce.isActive():
            self._apply_find_search()
        if not self._hits:
            return
        prev_idx = (self._hit_index - 1) % len(self._hits)
        self._focus_hit(prev_idx)

    def _build_replacement_plan(self) -> list[tuple[int, str, str | None]]:
        if self._find_debounce.isActive():
            self._apply_find_search()
        find_text = self._txt_find.text()
        if not find_text:
            return []
        partial = self._chk_partial.isChecked()
        replace_raw = self._txt_replace.text()
        plan: list[tuple[int, str, str | None]] = []
        for sr, field in self._hits:
            if not (0 <= sr < len(self._model._rows)):
                continue
            row = self._model._rows[sr]
            val = getattr(row, field, None) or ""
            if not _field_matches_find(val, find_text=find_text, partial=partial):
                continue
            new_val = _replacement_value(
                val,
                find_text=find_text,
                partial=partial,
                replace_raw=replace_raw,
            )
            plan.append((sr, field, new_val))
        return plan

    def _replace_current(self) -> None:
        if not (0 <= self._hit_index < len(self._hits)):
            return
        sr, field = self._hits[self._hit_index]
        find_text = self._txt_find.text()
        if not find_text or not (0 <= sr < len(self._model._rows)):
            return
        row = self._model._rows[sr]
        val = getattr(row, field, None) or ""
        partial = self._chk_partial.isChecked()
        if not _field_matches_find(val, find_text=find_text, partial=partial):
            self._rebuild_hits()
            self._sync_buttons()
            return
        new_val = _replacement_value(
            val,
            find_text=find_text,
            partial=partial,
            replace_raw=self._txt_replace.text(),
        )
        ok, errors = self._model.bulk_apply_replacements([(sr, field, new_val)])
        if errors:
            QMessageBox.warning(self, "Erreur", errors[0])
            return
        if ok:
            self._rebuild_hits()
            if self._hits:
                idx = min(self._hit_index, len(self._hits) - 1)
                self._focus_hit(max(idx, 0))
            else:
                self._hit_index = -1
                self._model.clear_find_highlights()
            self._sync_buttons()

    def _replace_all(self) -> None:
        plan = self._build_replacement_plan()
        if not plan:
            return
        find_text = self._txt_find.text()
        replace_text = self._txt_replace.text().strip()
        col_label = self._cmb_col.currentText()
        all_columns = self._cmb_col.currentData() == "*"
        n_rows = len({sr for sr, _, _ in plan})
        if all_columns:
            confirm_msg = (
                f"Remplacer dans toutes les colonnes pour {n_rows} ligne(s) "
                f"({len(plan)} champ(s)) ?\n\n"
                f"  Rechercher   : « {find_text} »\n"
                f"  Remplacer par: « {replace_text} »\n\n"
                "Enregistrement immédiat en base."
            )
        else:
            confirm_msg = (
                f"Remplacer dans « {col_label} » pour {n_rows} ligne(s) ?\n\n"
                f"  Rechercher   : « {find_text} »\n"
                f"  Remplacer par: « {replace_text} »\n\n"
                "Enregistrement immédiat en base."
            )
        reply = QMessageBox.question(
            self,
            "Confirmer le remplacement",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, errors = self._model.bulk_apply_replacements(plan)
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._rebuild_hits()
        self._hit_index = -1
        if self._hits:
            self._focus_hit(0)
        else:
            self._model.clear_find_highlights()
        msg = f"Remplacement effectué — {ok} champ(s) modifié(s)."
        if self._hits:
            msg += f" · {len(self._hits)} occurrence(s) restante(s)."
        self._lbl_preview.setText(msg)
        self._sync_buttons()


# ── Historique de session ──────────────────────────────────────────────────────


class _SessionHistoryDialog(QDialog):
    """Journal des modifications de la session (plus récente en tête)."""

    def __init__(self, model: _EditionModel, view: EditionSmeView) -> None:
        super().__init__(view)
        self._model = model
        self._view = view
        self.setWindowTitle("Historique de la session")
        self.resize(860, 470)
        self.setModal(True)

        hint = QLabel(
            "Journal de la session (lecture + annulation). Chaque ligne = une action enregistrée "
            "en base.\n"
            "Après annulation, la ligne reste affichée avec l'état « Annulé » : c'est la trace "
            "de ce qui a été fait, pas une action encore en cours.\n"
            "Une seule annulation à la fois, la plus récente encore « Actif » (comme Ctrl+Z). "
            "Journal effacé au prochain lancement."
        )
        hint.setWordWrap(True)
        hint.setObjectName("dashSubtitle")

        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels(
            ["Heure", "État", "Champ", "Agent", "Avant", "Après"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._btn_undo_last = QPushButton("Annuler la dernière modification encore active (Ctrl+Z)")
        self._btn_undo_last.clicked.connect(self._on_undo_last)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_undo_last)
        btn_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = btns.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Fermer")
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(btn_row)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(btns)

        self._reload_table()
        self._sync_undo_button()

    def _entries(self) -> list[_EditionUndoEntry]:
        return self._model.session_log_entries()

    def _reload_table(self) -> None:
        entries = self._entries()
        self._table.setRowCount(0)
        for i, e in enumerate(entries):
            self._table.insertRow(i)
            cells = (
                e.ts_display(),
                "Actif" if e.active else "Annulé",
                e.field_label,
                e.row_label,
                e.format_old(),
                e.format_new(),
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, e.seq)
                if col == 1 and not e.active:
                    item.setForeground(QColor("#6b7280"))
                self._table.setItem(i, col, item)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.resizeColumnsToContents()

    def _sync_undo_button(self) -> None:
        n = self._model.active_undo_count()
        locked = self._model._read_only
        self._btn_undo_last.setEnabled(not locked and n > 0)

    def _apply_undo_message(self, msg: str | None) -> None:
        if msg:
            self._view._lbl_status.setText(msg)
        self._reload_table()
        self._sync_undo_button()

    def _on_undo_last(self) -> None:
        msg = self._model.undo_last()
        if msg is None:
            QMessageBox.information(self, "Annulation", "Rien à annuler.")
        self._apply_undo_message(msg)


# ── Vue principale ────────────────────────────────────────────────────────────

class EditionSmeView(QWidget):
    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
        view_title: str = "Édition SME — Mise à jour rapide",
        row_scope: Callable[[ListeGeneraleRow], bool] | None = None,
        edit_permission: str = "effectifs.edition",
        settings_app: str = _SETTINGS_APP,
        qsettings_prefix: str = "edition",
        progress_title: str = "Edition SME",
        default_new_structure: str | None = None,
        structure_filter_codes: frozenset[str] | None = None,
        structure_combo_mode: str = "structures",
        structure_view_filter: Callable[[ListeGeneraleRow, str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._navigate_to_page = navigate_to_page
        self._view_title = view_title
        self._row_scope = row_scope
        self._edit_permission = edit_permission
        self._settings_app = settings_app
        self._qsettings_prefix = qsettings_prefix
        self._progress_title = progress_title
        self._default_new_structure = default_new_structure
        self._structure_filter_codes = structure_filter_codes
        self._structure_combo_mode = structure_combo_mode
        self._find_replace_dlg: _FindReplaceDialog | None = None
        self._month_reload_pending = False
        self._dirty = True
        self._editing_locked = False
        self._auth_read_only = False
        self._sme_period_locked = False
        self._personnel_service = None
        self._referentiel_service = None
        self._alerts_worker: CareerAlertsScanWorker | None = None
        self._opening_dossier = False

        title = QLabel(view_title)
        title.setObjectName("dashTitle")

        self._lbl_month = QLabel("—")
        self._lbl_month.setObjectName("dashSubtitle")
        self._lbl_month.setWordWrap(True)

        # ── Barre filtre 1 : recherche + combos ──────────────────────────────
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher (nom, matricule, fonction…)")
        self._search.setClearButtonEnabled(True)

        self._cmb_structure = NoWheelComboBox()
        self._cmb_structure.setMinimumWidth(130)
        self._cmb_structure.setToolTip("Filtrer par structure")

        self._cmb_subarme = NoWheelComboBox()
        self._cmb_subarme.setMinimumWidth(130)
        self._cmb_subarme.setToolTip(
            "Filtrer par subdivision d'arme pour la structure choisie"
        )

        self._cmb_grade = NoWheelComboBox()
        self._cmb_grade.setMinimumWidth(110)
        self._cmb_grade.setToolTip("Filtrer par grade — molette désactivée")

        self._cmb_padmin = NoWheelComboBox()
        self._cmb_padmin.setMinimumWidth(120)
        self._cmb_padmin.setToolTip("Filtrer par position admin.")

        self._btn_reset = QPushButton("Réinitialiser")
        self._btn_reset.setToolTip("Effacer tous les filtres actifs")
        self._btn_reset.clicked.connect(self._reset_filters)

        filter_row1 = QHBoxLayout()
        filter_row1.setSpacing(6)
        filter_row1.addWidget(self._search, stretch=1)
        self._lbl_structure_filter = QLabel("Structure :")
        filter_row1.addWidget(self._lbl_structure_filter)
        filter_row1.addWidget(self._cmb_structure)
        self._lbl_subarme_filter = QLabel("Subdivision :")
        filter_row1.addWidget(self._lbl_subarme_filter)
        filter_row1.addWidget(self._cmb_subarme)
        filter_row1.addWidget(QLabel("Grade :"))
        filter_row1.addWidget(self._cmb_grade)
        filter_row1.addWidget(QLabel("Position :"))
        filter_row1.addWidget(self._cmb_padmin)
        filter_row1.addWidget(self._btn_reset)

        self._chk_no_mat_mil = QCheckBox("Sans mat. mil.")
        self._chk_no_mat_mil.setToolTip("Afficher uniquement les fiches sans matricule militaire")
        self._chk_no_mat_solde = QCheckBox("Sans mat. solde")
        self._chk_no_mat_solde.setToolTip("Afficher uniquement les fiches sans matricule solde")
        self._chk_presents = QCheckBox("Présents seulement")
        self._chk_presents.setToolTip("Filtrer sur les positions admin. comptées « présent »")

        filter_row_mat = QHBoxLayout()
        filter_row_mat.setSpacing(12)
        filter_row_mat.addWidget(self._chk_no_mat_mil)
        filter_row_mat.addWidget(self._chk_no_mat_solde)
        filter_row_mat.addWidget(self._chk_presents)
        filter_row_mat.addStretch()

        # ── Barre filtre 2 : outils + statut ─────────────────────────────────
        self._btn_presets = QPushButton("Préréglages ▾")
        self._btn_presets.setToolTip("Sauvegarder / rappeler des combinaisons de filtres")
        self._btn_presets.clicked.connect(self._show_preset_menu)

        self._btn_find_replace = QPushButton("Rech./Remp.…")
        self._btn_find_replace.setToolTip(
            "Rechercher et remplacer — Suivant / Précédent, surlignage des lignes (F3)"
        )
        self._btn_find_replace.clicked.connect(self._open_find_replace)

        self._btn_history = QPushButton("Historique session")
        self._btn_history.setToolTip(
            "Ouvrir le journal des modifications enregistrées et annulées "
            "pendant cette session"
        )
        self._btn_history.clicked.connect(self._show_session_log)

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

        self._btn_show_mod = QPushButton("Modifiés")
        self._btn_show_mod.setCheckable(True)
        self._btn_show_mod.setVisible(False)
        self._btn_show_mod.setToolTip(
            "Afficher uniquement les lignes modifiées depuis le dernier rechargement"
        )

        self._btn_columns = QPushButton("Colonnes…")
        self._btn_columns.setToolTip("Afficher ou masquer les colonnes supplémentaires")

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.setToolTip("Relire les données enregistrées dans la base de données")
        self._btn_reload.clicked.connect(self._actualiser)

        self._lbl_status = QLabel("—")
        self._lbl_status.setObjectName("dashSubtitle")
        self._lbl_status.setMinimumWidth(260)

        filter_row2 = QHBoxLayout()
        filter_row2.setSpacing(6)
        filter_row2.addWidget(self._btn_presets)
        filter_row2.addWidget(self._btn_find_replace)
        filter_row2.addWidget(self._btn_history)
        filter_row2.addWidget(self._btn_compact)
        filter_row2.addStretch()
        filter_row2.addWidget(self._btn_show_dups)
        filter_row2.addWidget(self._btn_show_inc)
        filter_row2.addWidget(self._btn_show_mod)
        filter_row2.addWidget(self._btn_columns)
        filter_row2.addWidget(self._btn_reload)
        filter_row2.addWidget(self._lbl_status)

        # ── Modèle ────────────────────────────────────────────────────────────
        self._model = _EditionModel(self)
        self._model.grade_nomination_prompt = self._prompt_grade_nomination_date
        self._model.position_change_prompt = self._prepare_padmin_change
        self._model.save_ok.connect(self._on_saved)
        self._model.save_err.connect(self._on_save_error)
        self._model.dataChanged.connect(self._on_model_data_changed)

        self._proxy = _EditionFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.set_structure_view_filter(structure_view_filter)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(lambda: self._proxy.set_text(self._search.text()))
        self._search.textChanged.connect(lambda *_: self._search_debounce.start())
        self._search.returnPressed.connect(self._flush_search_filter)
        self._cmb_structure.currentIndexChanged.connect(self._on_structure_filter_changed)
        self._cmb_structure.activated.connect(self._on_structure_filter_changed)
        self._cmb_subarme.currentIndexChanged.connect(self._on_subarme_filter_changed)
        self._cmb_grade.currentIndexChanged.connect(self._on_grade_filter)
        self._cmb_grade.currentTextChanged.connect(self._on_grade_filter)
        self._cmb_padmin.currentIndexChanged.connect(self._on_padmin_filter)
        self._cmb_padmin.currentTextChanged.connect(self._on_padmin_filter)
        self._btn_show_dups.toggled.connect(self._proxy.set_duplicates_only)
        self._btn_show_inc.toggled.connect(self._proxy.set_incomplete_only)
        self._btn_show_mod.toggled.connect(self._proxy.set_modified_only)
        self._chk_no_mat_mil.toggled.connect(self._proxy.set_no_mat_mil)
        self._chk_no_mat_solde.toggled.connect(self._proxy.set_no_mat_solde)
        self._chk_presents.toggled.connect(self._proxy.set_presents_only)
        self._btn_columns.clicked.connect(self._on_columns_dialog)

        self._count_debounce = QTimer(self)
        self._count_debounce.setSingleShot(True)
        self._count_debounce.timeout.connect(self._update_count)
        self._proxy.layoutChanged.connect(self._schedule_count_update)
        self._proxy.modelReset.connect(self._schedule_count_update)
        self._proxy.rowsInserted.connect(self._schedule_count_update)
        self._proxy.rowsRemoved.connect(self._schedule_count_update)

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
        _s = self._qsettings()
        _init_compact = _s.value(self._qsettings_key("compact"), False, type=bool)
        self._btn_compact.blockSignals(True)
        self._btn_compact.setChecked(_init_compact)
        self._btn_compact.blockSignals(False)
        self._table.verticalHeader().setDefaultSectionSize(18 if _init_compact else 24)
        self._btn_compact.toggled.connect(self._on_compact_toggled)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.clicked.connect(self._on_table_click)
        self._table.doubleClicked.connect(self._on_table_double_click)

        # ── Raccourcis clavier ────────────────────────────────────────────────
        _undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self._table)
        _undo_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        _undo_sc.activated.connect(self._action_undo)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self._table, self._open_full_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self._table, self._open_full_dossier)
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(self._copy_from_above)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.load_data)
        QShortcut(QKeySequence("F2"), self._table, self._edit_current)
        QShortcut(QKeySequence("Ctrl+N"), self, self._create_new)
        QShortcut(QKeySequence("Ins"), self, self._create_new)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._on_columns_dialog)

        # ── Barre d'actions ───────────────────────────────────────────────────
        self._btn_new = QPushButton("Nouveau…")
        self._btn_new.setToolTip("Créer une fiche personnel (Ctrl+N)")
        self._btn_new.clicked.connect(self._create_new)

        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.setToolTip("Ouvrir la fiche en édition complète (F2)")
        self._btn_edit.setEnabled(False)
        self._btn_edit.clicked.connect(self._edit_current)

        self._btn_fiche = QPushButton("Dossier complet…")
        self._btn_fiche.setToolTip(
            "Ouvrir la fiche dossier (parcours, congés, historique) — une ligne sélectionnée"
        )
        self._btn_fiche.setEnabled(False)
        self._btn_fiche.clicked.connect(self._open_full_dossier)

        self._btn_liste = QPushButton("Liste générale")
        self._btn_liste.setToolTip("Aller à la liste générale du personnel")
        self._btn_liste.clicked.connect(self._go_to_liste_generale)
        if self._navigate_to_page is None:
            self._btn_liste.setVisible(False)

        self._btn_delete = QPushButton("Supprimer")
        self._btn_delete.setToolTip("Supprimer le(s) personnel(s) sélectionné(s)")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._action_delete)

        self._lbl_sel = QLabel("")
        self._lbl_sel.setObjectName("dashSubtitle")

        # Boutons logiques (menus déroulants) — conservés pour _update_action_buttons
        self._btn_bulk_struct = QPushButton("Changer structure…")
        self._btn_bulk_struct.setToolTip("Affecter la même structure aux lignes cochées ou sélectionnées")
        self._btn_bulk_struct.clicked.connect(self._action_bulk_structure)
        self._btn_bulk_sub = QPushButton("Changer subdivision…")
        self._btn_bulk_sub.setToolTip("Affecter la même subdivision aux lignes cochées ou sélectionnées")
        self._btn_bulk_sub.clicked.connect(self._action_bulk_subdivision)
        self._btn_bulk_grade = QPushButton("Changer grade…")
        self._btn_bulk_grade.setToolTip("Affecter le même grade aux lignes cochées ou sélectionnées")
        self._btn_bulk_grade.clicked.connect(self._action_bulk_grade)
        self._btn_bulk_sat = QPushButton("Mission / stage / malade…")
        self._btn_bulk_sat.setToolTip(
            "Affecter mission, stage (local, extérieur, interne) ou maladie aux lignes cochées"
        )
        self._btn_bulk_sat.clicked.connect(self._action_bulk_mission_stage)

        self._btn_check_visible = QPushButton("Cocher visibles")
        self._btn_check_visible.setToolTip(
            "Cocher toutes les lignes filtrées (la coche persiste si vous changez de filtre)"
        )
        self._btn_check_visible.clicked.connect(
            lambda: (self._model.check_visible(self._proxy, True), self._update_action_buttons(), self._update_count())
        )
        self._btn_uncheck_all = QPushButton("Décocher tout")
        self._btn_uncheck_all.setToolTip("Supprimer toutes les coches")
        self._btn_uncheck_all.clicked.connect(
            lambda: (self._model.uncheck_all(), self._update_action_buttons(), self._update_count())
        )
        self._btn_select_all = QPushButton("Sél. lignes visibles")
        self._btn_select_all.setToolTip(
            "Sélection bleue pour export / dossier (distinct des cases ☑ actions groupées)"
        )
        self._btn_select_all.clicked.connect(self._table.selectAll)

        _sep1 = QFrame(); _sep1.setFrameShape(QFrame.Shape.VLine); _sep1.setFrameShadow(QFrame.Shadow.Sunken)

        self._btn_bulk_menu = QToolButton()
        self._btn_bulk_menu.setText("Actions groupées ▾")
        self._btn_bulk_menu.setToolTip(
            "Modifier structure, subdivision, grade ou mission/stage (lignes cochées ☑)"
        )
        self._btn_bulk_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bulk_menu = QMenu(self)
        act_bulk_struct = bulk_menu.addAction("Changer structure…")
        act_bulk_struct.setToolTip(self._btn_bulk_struct.toolTip())
        act_bulk_struct.triggered.connect(self._action_bulk_structure)
        act_bulk_sub = bulk_menu.addAction("Changer subdivision…")
        act_bulk_sub.setToolTip(self._btn_bulk_sub.toolTip())
        act_bulk_sub.triggered.connect(self._action_bulk_subdivision)
        act_bulk_grade = bulk_menu.addAction("Changer grade…")
        act_bulk_grade.setToolTip(self._btn_bulk_grade.toolTip())
        act_bulk_grade.triggered.connect(self._action_bulk_grade)
        act_bulk_sat = bulk_menu.addAction("Mission / stage / malade…")
        act_bulk_sat.setToolTip(self._btn_bulk_sat.toolTip())
        act_bulk_sat.triggered.connect(self._action_bulk_mission_stage)
        act_bulk_diploma = bulk_menu.addAction("Ajouter un diplôme…")
        act_bulk_diploma.triggered.connect(self._action_bulk_diploma)
        self._btn_bulk_menu.setMenu(bulk_menu)
        self._bulk_menu_actions = (act_bulk_struct, act_bulk_sub, act_bulk_grade, act_bulk_sat, act_bulk_diploma)

        _sep2 = QFrame(); _sep2.setFrameShape(QFrame.Shape.VLine); _sep2.setFrameShadow(QFrame.Shadow.Sunken)

        self._btn_sel_menu = QToolButton()
        self._btn_sel_menu.setText("Sélection ▾")
        self._btn_sel_menu.setToolTip("Cocher / décocher / sélectionner les lignes visibles")
        self._btn_sel_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sel_menu = QMenu(self)
        sel_menu.addAction("Cocher visibles", self._btn_check_visible.click)
        sel_menu.addAction("Décocher tout", self._btn_uncheck_all.click)
        sel_menu.addSeparator()
        sel_menu.addAction("Sél. lignes visibles", self._btn_select_all.click)
        self._btn_sel_menu.setMenu(sel_menu)

        self._btn_export_menu = QToolButton()
        self._btn_export_menu.setText("Exporter ▾")
        self._btn_export_menu.setToolTip("Exports CSV / Excel et import CSV de mise à jour")
        self._btn_export_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self)
        export_menu.addAction("Exporter CSV…", self._action_export_csv)
        export_menu.addAction("Excel (liste)…", self._export_excel_filtered)
        export_menu.addAction("Excel par position…", self._export_excel_by_padmin)
        export_menu.addSeparator()
        self._act_import_csv = export_menu.addAction(
            "Importer CSV…", self._import_csv_updates
        )
        self._btn_export_menu.setMenu(export_menu)

        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)
        action_bar.addWidget(self._btn_new)
        action_bar.addWidget(self._btn_edit)
        action_bar.addWidget(self._btn_fiche)
        action_bar.addWidget(self._btn_liste)
        action_bar.addWidget(self._btn_delete)
        action_bar.addWidget(_sep1)
        action_bar.addWidget(self._btn_bulk_menu)
        action_bar.addWidget(_sep2)
        action_bar.addWidget(self._btn_sel_menu)
        action_bar.addWidget(self._btn_export_menu)
        action_bar.addStretch()
        action_bar.addWidget(self._lbl_sel)

        self._alerts_banner = QLabel()
        self._alerts_banner.setObjectName("personnelAlertsBanner")
        self._alerts_banner.setVisible(False)
        self._alerts_banner.setWordWrap(True)
        self._advanced_filters = PersonnelAdvancedFilterPanel(self)
        self._advanced_filters.rules_changed.connect(self._apply_advanced_filters)

        # ── Layout principal ──────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(5)
        self.setAccessibleName(view_title)
        self._lbl_month.hide()
        lay.addWidget(self._lbl_month)
        lay.addWidget(self._alerts_banner)
        lay.addLayout(filter_row1)
        lay.addLayout(filter_row_mat)
        lay.addLayout(filter_row2)
        lay.addWidget(self._advanced_filters)
        lay.addWidget(self._table, stretch=1)
        lay.addLayout(action_bar)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if event.spontaneous():
            return
        if self._dirty:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._deferred_first_load)
            return
        self._apply_editing_lock()
        self._start_career_alerts_scan()
        if self._month_reload_pending:
            from PySide6.QtCore import QTimer

            self._month_reload_pending = False
            QTimer.singleShot(0, self.load_data)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_career_alerts_worker()
        super().hideEvent(event)

    def on_page_activated(self) -> None:
        if self._dirty:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._deferred_first_load)
        else:
            self._apply_editing_lock()

    def _deferred_first_load(self) -> None:
        self._dirty = False
        self.load_data()

    def _apply_editing_lock(self) -> None:
        from sme_gend.services.auth_guard import session_has_permission
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings

        st = evaluate_editing_state_for_settings(self._settings)
        self._auth_read_only = not session_has_permission(self._edit_permission)
        self._sme_period_locked = st.locked
        self._editing_locked = self._sme_period_locked or self._auth_read_only
        self._update_month_banner()
        self._model.set_read_only(self._editing_locked)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if self._editing_locked
            else (
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.AnyKeyPressed
            )
        )
        self._btn_find_replace.setEnabled(not self._editing_locked)
        self._btn_new.setEnabled(not self._editing_locked)
        if getattr(self, "_act_import_csv", None) is not None:
            self._act_import_csv.setEnabled(not self._editing_locked)
        self._update_action_buttons()

    def _qsettings(self) -> QSettings:
        return QSettings(QSETTINGS_ORG, self._settings_app)

    def _qsettings_key(self, suffix: str) -> str:
        return f"{self._qsettings_prefix}/{suffix}"

    def _require_editable(self) -> bool:
        from sme_gend.services.auth_guard import require_permission

        if getattr(self, "_auth_read_only", False):
            require_permission(
                self,
                self._edit_permission,
                message="Votre compte n'a pas la permission d'éditer les effectifs.",
            )
            return False
        if not self._editing_locked:
            return True
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings

        st = evaluate_editing_state_for_settings(self._settings)
        QMessageBox.warning(
            self,
            "Modification impossible",
            st.detail or "La période SME est fermée (lecture seule).",
        )
        return False

    def _update_month_banner(self) -> None:
        from sme_gend.config.liste_generale_scope import build_export_mois_context
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings

        exp_ctx = build_export_mois_context(self._settings)
        st = evaluate_editing_state_for_settings(self._settings)
        parts = [exp_ctx.banner_html()]
        if st.tabsme_statut:
            parts.append(f"statut : {st.tabsme_statut}")
        if st.locked:
            parts.append("<span style='color:#c0392b'>lecture seule (période fermée)</span>")
        elif self._auth_read_only:
            parts.append("<span style='color:#c0392b'>lecture seule (droits insuffisants)</span>")
        else:
            parts.append("modifications enregistrées immédiatement en MySQL")
        self._lbl_month.setText(" · ".join(parts))
        self._lbl_month.setToolTip(self._lbl_month.text())
        read_only = st.locked or self._auth_read_only
        self._lbl_month.setVisible(read_only)
        if read_only:
            self._lbl_month.setText('<b>Lecture seule</b> — ' + (
                'période fermée' if st.locked else 'droits de modification insuffisants'))
        self._lbl_month.setTextFormat(Qt.TextFormat.RichText)

    def _ensure_personnel_services(self) -> tuple[Any, Any]:
        if self._personnel_service is None:
            from sme_gend.services.personnel_service import PersonnelService

            self._personnel_service = PersonnelService(self._settings)
        if self._referentiel_service is None:
            from sme_gend.services.referentiel_service import ReferentielService

            self._referentiel_service = ReferentielService(self._settings)
        return self._personnel_service, self._referentiel_service

    def _go_to_liste_generale(self) -> None:
        if self._navigate_to_page is None:
            return
        from sme_gend.ui.navigation_pages import PAGE_LISTE_GENERALE

        self._navigate_to_page(PAGE_LISTE_GENERALE)

    # ── Colonnes optionnelles ─────────────────────────────────────────────────

    def _extra_columns_shown(self) -> set[str]:
        raw = self._qsettings().value(
            self._qsettings_key("extra_columns_shown"), "[]"
        )
        try:
            items = json.loads(raw) if isinstance(raw, str) else []
            return {str(x) for x in items}
        except Exception:
            return set()

    def _save_extra_columns_shown(self, keys: set[str]) -> None:
        self._qsettings().setValue(
            self._qsettings_key("extra_columns_shown"),
            json.dumps(sorted(keys), ensure_ascii=False),
        )

    def _apply_column_visibility(self) -> None:
        shown = self._extra_columns_shown()
        for i in range(_CORE_COL_COUNT, len(_COLS)):
            key = _COLS[i][0]
            self._table.setColumnHidden(i, key not in shown)

    def _on_columns_dialog(self) -> None:
        shown = self._extra_columns_shown()
        dlg = QDialog(self)
        dlg.setWindowTitle("Colonnes affichées")
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg)
        gb = QGroupBox("Colonnes supplémentaires")
        gb_lay = QVBoxLayout(gb)
        checkboxes: dict[str, QCheckBox] = {}
        for key, label, _ in OPTIONAL_COLUMN_DEFS:
            cb = QCheckBox(label)
            cb.setChecked(key in shown)
            checkboxes[key] = cb
            gb_lay.addWidget(cb)
        lay.addWidget(gb)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_shown = {key for key, cb in checkboxes.items() if cb.isChecked()}
        self._save_extra_columns_shown(new_shown)
        self._apply_column_visibility()

    def _visible_export_cols(self) -> list[tuple[str, str, str]]:
        shown = self._extra_columns_shown()
        cols = list(_CORE_COLS)
        for col in OPTIONAL_COLUMN_DEFS:
            if col[0] in shown:
                cols.append(col)
        return cols

    def _current_filtered_rows(self) -> list[ListeGeneraleRow]:
        self._flush_search_filter()
        rows: list[ListeGeneraleRow] = []
        for pr in range(self._proxy.rowCount()):
            sr = self._proxy.mapToSource(self._proxy.index(pr, 0)).row()
            if 0 <= sr < len(self._model._rows):
                rows.append(self._model._rows[sr])
        return rows

    # ── Création / édition fiche ──────────────────────────────────────────────

    def _create_new(self) -> None:
        if not self._require_editable():
            return
        from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog

        service, referentiel = self._ensure_personnel_services()
        blank = ListeGeneraleRow(
            id="0",
            noms=None,
            date_naissance=None,
            lieu_naissance=None,
            grade=None,
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin=None,
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure=self._default_new_structure,
            obs=None,
            ordre_tri=None,
            presseance=None,
        )
        from sme_gend.ui.personnel_dossier_open import dialog_host

        dlg = PersonnelEditDialog(
            dialog_host(self), blank, referentiel, settings=self._settings
        )
        dlg.setWindowTitle("Nouvelle fiche personnel")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        if not new_row.noms:
            QMessageBox.warning(self, "Création", "Le nom est obligatoire.")
            return
        try:
            from sme_gend.services.personnel_form_service import save_personnel_form
            new_id = save_personnel_form(service, dlg).id
        except Exception as exc:
            QMessageBox.critical(self, "Création", str(exc))
            return
        self._lbl_status.setText(
            f"Personnel créé (id {new_id}). Rechargez ou complétez le dossier agent si besoin."
        )
        self.load_data(preserve_ui=True)

    def _edit_current(self) -> None:
        src_rows = self._get_selected_src_rows()
        if len(src_rows) != 1:
            QMessageBox.information(
                self,
                "Modification",
                "Sélectionnez une seule ligne (sélection bleue).",
            )
            return
        self._edit_row(self._model._rows[src_rows[0]])

    def _edit_row(self, row_obj: ListeGeneraleRow) -> None:
        if not self._require_editable():
            return
        from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog

        service, referentiel = self._ensure_personnel_services()
        fresh = service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(
                self,
                "Personnel",
                "Ligne introuvable en base. Actualisez la liste (F5).",
            )
            return
        from sme_gend.ui.personnel_dossier_open import dialog_host

        dlg = PersonnelEditDialog(
            dialog_host(self), fresh, referentiel, settings=self._settings
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            from sme_gend.services.personnel_form_service import save_personnel_form
            save_personnel_form(service, dlg, before=fresh)
        except Exception as exc:
            QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        self._lbl_status.setText("Modifications enregistrées (MySQL + journal d'audit).")
        self._sync_liste_rows_from_db([new_row.id])

    # ── Exports Excel ─────────────────────────────────────────────────────────

    def _export_excel_filtered(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.config.liste_generale_scope import build_export_mois_context
        from sme_gend.services.personnel_excel_export import export_liste_filtered
        from sme_gend.ui.bg_task import run_with_progress

        _, referentiel = self._ensure_personnel_services()
        rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
        flat = deepcopy(list(referentiel.subdivisions_flat()))
        grades = deepcopy(list(referentiel.grades()))
        settings = self._settings
        exp_ctx = build_export_mois_context(settings)
        hidden = {c[0] for c in OPTIONAL_COLUMN_DEFS if c[0] not in self._extra_columns_shown()}
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la vue filtrée en Excel",
            str(Path.home() / "edition_sme.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        title = f"Edition SME {exp_ctx.label_short()}".strip()

        def _work() -> int:
            return export_liste_filtered(
                rows,
                Path(path),
                hidden_keys=hidden,
                flat=flat,
                grades=grades,
                sheet_title=title,
                log_mois=exp_ctx.export_mois, settings=settings,
            )

        n = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if n is None:
            return
        QMessageBox.information(
            self,
            "Export Excel",
            f"Fichier enregistré ({n} ligne(s)) :\n{path}",
        )

    def _export_excel_by_padmin(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.config.liste_generale_scope import build_export_mois_context
        from sme_gend.services.personnel_excel_export import export_liste_by_padmin
        from sme_gend.ui.bg_task import run_with_progress

        _, referentiel = self._ensure_personnel_services()
        rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
        flat = deepcopy(list(referentiel.subdivisions_flat()))
        grades = deepcopy(list(referentiel.grades()))
        settings = self._settings
        exp_ctx = build_export_mois_context(settings)
        hidden = {c[0] for c in OPTIONAL_COLUMN_DEFS if c[0] not in self._extra_columns_shown()}
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter par position administrative",
            str(Path.home() / "edition_sme_par_padmin.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return

        def _work() -> dict:
            return export_liste_by_padmin(
                rows,
                Path(path),
                hidden_keys=hidden,
                flat=flat,
                grades=grades,
                log_mois=exp_ctx.export_mois, settings=settings,
            )

        counts = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if counts is None:
            return
        details = "\n".join(f"  · {k} : {v}" for k, v in sorted(counts.items()))
        QMessageBox.information(
            self,
            "Export Excel",
            f"Fichier enregistré :\n{path}\n\n{len(counts)} feuille(s) :\n{details}",
        )

    def _import_csv_updates(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "system.import"):
            return
        if not self._require_editable():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer des mises à jour CSV",
            "",
            "Fichier CSV (*.csv)",
        )
        if not path:
            return
        choices = ["N° (id carrière)", "Matricule militaire"]
        choice, ok = QInputDialog.getItem(
            self,
            "Correspondance CSV",
            "Colonne de correspondance avec les fiches existantes :",
            choices,
            0,
            False,
        )
        if not ok:
            return
        match_field = "id" if choice.startswith("N°") else "matricule_militaire"
        service, _ = self._ensure_personnel_services()
        rows_by_id = {r.id: r for r in self._model._rows}
        result = import_csv_updates(service, rows_by_id, Path(path), match_field=match_field)
        msg_parts = [
            f"{result.updated} fiche(s) mise(s) à jour",
            f"{result.skipped} ligne(s) ignorée(s)",
        ]
        if result.errors:
            msg_parts.append("\n".join(result.errors[:15]))
        QMessageBox.information(self, "Import CSV", "\n".join(msg_parts))
        if result.updated:
            self.load_data(preserve_ui=True)

    # ── Alertes parcours ──────────────────────────────────────────────────────

    def _stop_career_alerts_worker(self, *, wait_ms: int = 3000) -> None:
        worker = self._alerts_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.requestInterruption()
            worker.quit()
            worker.wait(wait_ms)
        worker.deleteLater()
        self._alerts_worker = None

    def _start_career_alerts_scan(self) -> None:
        from sme_gend.services.career_alerts_cache import get_career_alerts_snapshot

        cached = get_career_alerts_snapshot(self._settings)
        if cached.ready:
            self._on_career_alerts_ready(cached)
            return
        if self._alerts_worker is not None and self._alerts_worker.isRunning():
            return
        self._alerts_banner.setText("Analyse des alertes parcours en cours…")
        from sme_gend.ui.theme import alert_banner_stylesheet

        self._alerts_banner.setStyleSheet(alert_banner_stylesheet(loading=True))
        self._alerts_banner.setVisible(True)
        worker = CareerAlertsScanWorker(self._settings, parent=self)
        worker.finished_scan.connect(self._on_career_alerts_ready)
        self._alerts_worker = worker
        worker.start()

    def _on_career_alerts_ready(self, snap) -> None:
        self._alerts_worker = None
        if snap is None or not snap.ready or snap.total == 0:
            self._alerts_banner.hide()
            return
        w = snap.warning_count
        info = snap.total - w
        parts = []
        if w:
            parts.append(f"{w} alerte(s) importante(s)")
        if info:
            parts.append(f"{info} info(s)")
        self._alerts_banner.setText(
            f"<b>Alertes parcours</b> — {', '.join(parts)}"
        )
        self._alerts_banner.setToolTip('Alertes détectées sur les imports mensuels. Ouvrez une fiche dossier pour le détail par agent.')
        self._alerts_banner.setTextFormat(Qt.TextFormat.RichText)
        from sme_gend.ui.theme import alert_banner_stylesheet

        self._alerts_banner.setStyleSheet(alert_banner_stylesheet(warning=bool(w)))
        self._alerts_banner.setVisible(True)

    def refresh_theme_styles(self) -> None:
        """Réaligne bandeaux après changement de thème."""
        if not self._alerts_banner.isVisible():
            return
        from sme_gend.ui.theme import alert_banner_stylesheet

        text = (self._alerts_banner.text() or "").lower()
        loading = "en cours" in text
        warning = "importante" in text
        self._alerts_banner.setStyleSheet(
            alert_banner_stylesheet(loading=loading, warning=warning and not loading)
        )

    def mark_dirty(self) -> None:
        """Changement de mois actif : recharger depuis la source (base live ou archive)."""
        self._month_reload_pending = True
        if self.isVisible():
            self.load_data()

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._personnel_service = None
        self._referentiel_service = None
        self._dirty = True
        if self.isVisible():
            self.load_data(preserve_ui=self._model.rowCount() > 0)

    # ── Persistance en-tête et mode compact ───────────────────────────────────

    def _save_header_state(self) -> None:
        settings = self._qsettings()
        settings.setValue(
            self._qsettings_key("header_state"), self._table.horizontalHeader().saveState()
        )
        settings.setValue(self._qsettings_key("header_col_count"), len(_COLS))

    def _restore_header_state(self) -> None:
        settings = self._qsettings()
        state = settings.value(self._qsettings_key("header_state"))
        saved_cols = settings.value(self._qsettings_key("header_col_count"), 0, type=int)
        if state and saved_cols == len(_COLS):
            try:
                self._table.horizontalHeader().restoreState(state)
            except Exception:
                pass
        self._ensure_core_columns_visible()

    def _ensure_core_columns_visible(self) -> None:
        """Les colonnes métier de base restent toujours visibles."""
        for i in range(_CORE_COL_COUNT):
            self._table.setColumnHidden(i, False)
        self._apply_column_visibility()

    def _on_compact_toggled(self, checked: bool) -> None:
        self._table.verticalHeader().setDefaultSectionSize(18 if checked else 24)
        self._qsettings().setValue(self._qsettings_key("compact"), checked)

    # ── Préréglages de filtres ────────────────────────────────────────────────

    def _load_presets(self) -> list[dict]:
        raw = self._qsettings().value(self._qsettings_key("presets"), "[]")
        try:
            return json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            return []

    def _store_presets(self, presets: list[dict]) -> None:
        self._qsettings().setValue(
            self._qsettings_key("presets"), json.dumps(presets, ensure_ascii=False)
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
        self._advanced_filters.load_state(preset.get('advanced_filters'))
        self._apply_advanced_filters()
        self._search.setText(preset.get("text", ""))
        self._set_filter_combo(self._cmb_structure, preset.get("structure", "(Tous)"))
        self._on_structure_filter_changed()
        sub = preset.get("subarme", "Toutes")
        idx = self._cmb_subarme.findText(sub)
        if idx >= 0:
            self._cmb_subarme.setCurrentIndex(idx)
        self._on_subarme_filter_changed()
        self._set_filter_combo(self._cmb_grade,     preset.get("grade",     "(Tous)"))
        self._set_filter_combo(self._cmb_padmin,    preset.get("padmin",    "(Tous)"))
        self._chk_no_mat_mil.setChecked(bool(preset.get("no_mat_mil")))
        self._chk_no_mat_solde.setChecked(bool(preset.get("no_mat_solde")))
        self._chk_presents.setChecked(bool(preset.get("presents")))

    def _save_current_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Sauvegarder le filtre", "Nom du préréglage :")
        if not ok or not name.strip():
            return
        name = name.strip()
        preset = {
            "name":      name,
            "text":      self._search.text(),
            "structure": self._cmb_structure.currentText(),
            "subarme": self._subarme_filter_label(),
            "grade":     self._cmb_grade.currentText(),
            "padmin":    self._cmb_padmin.currentText(),
            "no_mat_mil": self._chk_no_mat_mil.isChecked(),
            "no_mat_solde": self._chk_no_mat_solde.isChecked(),
            "presents": self._chk_presents.isChecked(),
            'advanced_filters': self._advanced_filters.export_state(),
        }
        presets = [p for p in self._load_presets() if p.get("name") != name]
        presets.append(preset)
        self._store_presets(presets)

    def _delete_preset(self, name: str) -> None:
        self._store_presets([p for p in self._load_presets() if p.get("name") != name])

    # ── Chargement ────────────────────────────────────────────────────────────

    def _capture_ui_state(self) -> dict[str, Any]:
        hdr = self._table.horizontalHeader()
        sort_col = hdr.sortIndicatorSection()
        sort_order = _table_sort_order_code(hdr.sortIndicatorOrder())
        if sort_col < 0:
            sort_col = _COL_IDX["noms"]
            sort_order = 0
        selected_ids = [
            self._model._rows[sr].id
            for sr in self._get_selected_src_rows()
            if 0 <= sr < len(self._model._rows)
        ]
        return {
            "search": self._search.text(),
            "structure": self._cmb_structure.currentText(),
            "subarme": self._subarme_filter_label(),
            "grade": self._cmb_grade.currentText(),
            "padmin": self._cmb_padmin.currentText(),
            "dups": self._btn_show_dups.isChecked(),
            "inc": self._btn_show_inc.isChecked(),
            "mod": self._btn_show_mod.isChecked(),
            "no_mat_mil": self._chk_no_mat_mil.isChecked(),
            "no_mat_solde": self._chk_no_mat_solde.isChecked(),
            "presents": self._chk_presents.isChecked(),
            "checked": set(self._model._checked_ids),
            "sort_col": sort_col,
            "sort_order": sort_order,
            "selected_ids": selected_ids,
            "scroll_v": self._table.verticalScrollBar().value(),
        }

    def _restore_ui_state(self, state: dict[str, Any]) -> None:
        self._search.setText(state.get("search", ""))
        self._flush_search_filter()
        self._set_filter_combo(self._cmb_structure, state.get("structure", "(Tous)"))
        self._on_structure_filter_changed()
        sub = state.get("subarme", "Toutes")
        idx = self._cmb_subarme.findText(sub)
        if idx >= 0:
            self._cmb_subarme.setCurrentIndex(idx)
        self._on_subarme_filter_changed()
        self._set_filter_combo(self._cmb_grade, state.get("grade", "(Tous)"))
        self._set_filter_combo(self._cmb_padmin, state.get("padmin", "(Tous)"))
        self._on_grade_filter()
        self._on_padmin_filter()
        self._btn_show_dups.setChecked(bool(state.get("dups")))
        self._btn_show_inc.setChecked(bool(state.get("inc")))
        self._btn_show_mod.setChecked(bool(state.get("mod")))
        self._chk_no_mat_mil.setChecked(bool(state.get("no_mat_mil")))
        self._chk_no_mat_solde.setChecked(bool(state.get("no_mat_solde")))
        self._chk_presents.setChecked(bool(state.get("presents")))
        restored = state.get("checked")
        if isinstance(restored, set):
            self._model._checked_ids = set(restored)
            if self._model._rows:
                tl = self._model.index(0, 0)
                br = self._model.index(len(self._model._rows) - 1, 0)
                self._model.dataChanged.emit(tl, br, [Qt.ItemDataRole.CheckStateRole])
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda st=state: self._apply_table_view_state(st))
        self._update_action_buttons()
        self._update_count()

    def _apply_table_view_state(self, state: dict[str, Any]) -> None:
        """Tri / sélection / scroll après rechargement (filtres déjà appliqués)."""
        sort_col = state.get("sort_col", -1)
        if isinstance(sort_col, int) and sort_col >= 0:
            order = _table_sort_order_from_code(state.get("sort_order"))
            self._table.sortByColumn(sort_col, order)
        self._reselect_row_ids(
            state.get("selected_ids") or [],
            scroll_to_selection=not isinstance(state.get("scroll_v"), int),
        )
        scroll_v = state.get("scroll_v")
        if isinstance(scroll_v, int):
            self._table.verticalScrollBar().setValue(scroll_v)

    def _sync_liste_rows_from_db(self, row_ids: list[str]) -> None:
        """Met à jour des lignes en place — conserve tri, filtres et pile d'annulation."""
        if not row_ids:
            return
        service, _ = self._ensure_personnel_services()
        want = {str(rid) for rid in row_ids}
        touched: list[int] = []
        for i, row in enumerate(self._model._rows):
            if row.id not in want:
                continue
            fresh = service.get_by_id(row.id)
            if fresh is None:
                continue
            self._model._rows[i] = fresh
            touched.append(i)
        if not touched:
            return
        self._model._recompute_status_sets()
        self._model._after_db_mutation()
        lo, hi = min(touched), max(touched)
        tl = self._model.index(lo, 0)
        br = self._model.index(hi, len(_COLS) - 1)
        self._model.dataChanged.emit(
            tl,
            br,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.CheckStateRole,
            ],
        )
        self._update_count()

    def _reselect_row_ids(
        self,
        row_ids: list[str],
        *,
        scroll_to_selection: bool = True,
    ) -> None:
        if not row_ids or not self._model._rows:
            return
        want = {str(rid) for rid in row_ids}
        sm = self._table.selectionModel()
        if sm is None:
            return
        sm.blockSignals(True)
        sm.clearSelection()
        first_proxy_row: int | None = None
        for pr in range(self._proxy.rowCount()):
            src = self._proxy.mapToSource(self._proxy.index(pr, 0)).row()
            if 0 <= src < len(self._model._rows) and self._model._rows[src].id in want:
                self._table.selectRow(pr)
                if first_proxy_row is None:
                    first_proxy_row = pr
        sm.blockSignals(False)
        if scroll_to_selection and first_proxy_row is not None:
            self._table.scrollTo(
                self._proxy.index(first_proxy_row, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
        self._update_action_buttons()

    def _actualiser(self) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        invalidate_liste_cache(self._settings)
        self.load_data(preserve_ui=True)

    def refresh_from_database(self) -> None:
        self._actualiser()

    def load_data(self, *, preserve_ui: bool = False) -> None:
        from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
        from sme_gend.ui.bg_task import run_with_progress

        ui_state = self._capture_ui_state() if preserve_ui and self._model.rowCount() else None
        settings = self._settings
        self._btn_reload.setEnabled(False)
        self._update_month_banner()

        def do_load():
            from sme_gend.services.liste_generale_cache import get_liste_rows

            repo = ListeGeneraleRepository(settings)
            rows = get_liste_rows(settings, repo.fetch_all)

            grades: list[str] = []
            grade_rows = []
            try:
                from sme_gend.repositories.grade_repository import GradeRepository
                from sme_gend.services.personnel_list_order import sort_liste_generale_for_settings

                grade_rows = GradeRepository(settings).fetch_all()
                rows = sort_liste_generale_for_settings(rows, grade_rows, settings)
                grades = [g.code for g in grade_rows if g.code]
            except Exception as exc:
                _LOG.warning("Grades Edition SME : %s", exc)
            if not grades:
                seen: set[str] = set()
                for r in rows:
                    if r.grade and r.grade not in seen:
                        grades.append(r.grade); seen.add(r.grade)

            structures: list[str] = []
            try:
                from sme_gend.repositories.structure_repository import StructureRepository
                structures = [s.code for s in StructureRepository(settings).fetch_all() if s.code]
            except Exception as exc:
                _LOG.warning("Structures Edition SME : %s", exc)
            if not structures:
                structures = sorted({r.structure for r in rows if r.structure})

            ref_svc = None
            try:
                from sme_gend.services.referentiel_service import ReferentielService

                ref_svc = ReferentielService(settings)
                subarmes = ref_svc.subdivision_arme_combo_choices()
            except Exception as exc:
                _LOG.warning("Subdivisions Edition SME : %s", exc)
                subarmes = sorted({r.subdivision_arme for r in rows if r.subdivision_arme})

            try:
                if ref_svc is not None:
                    padmins = ref_svc.position_admin_choices()
                else:
                    padmins = repo.fetch_distinct_padmin()
            except Exception as exc:
                _LOG.warning("Padmin Edition SME : %s", exc)
                padmins = sorted({r.position_admin for r in rows if r.position_admin})

            return repo, rows, grades, structures, subarmes, padmins, grade_rows

        result = run_with_progress(
            self,
            self._progress_title,
            "Chargement de la liste…",
            do_load,
            silent=True,
        )
        self._btn_reload.setEnabled(True)
        if result is None:
            return

        repo, rows, grades, structures, subarmes, padmins, grade_rows = result
        if self._row_scope is not None:
            rows = [r for r in rows if self._row_scope(r)]
        personnel, referentiel = self._ensure_personnel_services()
        from sme_gend.services.personnel_list_order import GradeSortHelper

        self._proxy.set_grade_sort_helper(GradeSortHelper(grade_rows), _COL_IDX["grade"])
        self._model.load(
            rows, repo, grades, structures, subarmes, padmins,
            settings=settings, personnel=personnel,
            grade_rows=grade_rows,
            subdivision_flat=list(referentiel.subdivisions_flat()),
        )
        self._model.clear_find_highlights()
        self._month_reload_pending = False
        self._populate_filter_combos(rows, grades, structures, padmins)
        self._fill_subarme_combo(preserve_data=None)
        self._ensure_core_columns_visible()
        self._start_career_alerts_scan()
        # Sync proxy filter state with combos (combos were reset to "(Tous)" with signals blocked)
        if ui_state:
            self._restore_ui_state(ui_state)
        else:
            self._proxy.set_structure("")
            self._proxy.set_grade("", combo_display="")
            self._proxy.set_padmin("")
            self._proxy.set_subarme("")
            self._fill_subarme_combo(preserve_data=None)
            self._table.sortByColumn(_COL_IDX["noms"], Qt.SortOrder.AscendingOrder)
        self._fill_subarme_combo(preserve_data=None)
        self._update_count()
        self._apply_editing_lock()

    # ── Combos de filtres ─────────────────────────────────────────────────────

    def _populate_filter_combos(
        self,
        rows: list[ListeGeneraleRow],
        grades: list[str],
        structures: list[str],
        padmins: list[str],
    ) -> None:
        TOUS = "(Tous)"
        struct_items: list[tuple[str, str]] = []
        if self._structure_combo_mode == "dpf_vue":
            from sme_gend.services.sme_dpf_scope import DPF_VIEW_FILTER_ITEMS

            struct_items = list(DPF_VIEW_FILTER_ITEMS)
        else:
            try:
                from sme_gend.repositories.structure_repository import StructureRepository

                struct_map = {
                    s.code: s
                    for s in StructureRepository(self._settings).fetch_all()
                    if s.code
                }
                codes = structures or sorted({r.structure for r in rows if r.structure})
                if self._structure_filter_codes:
                    codes = [
                        c
                        for c in codes
                        if (c or "").strip().upper() in self._structure_filter_codes
                    ]
                seen: set[str] = set()
                for code in codes:
                    c = (code or "").strip()
                    if not c or c.casefold() in seen:
                        continue
                    seen.add(c.casefold())
                    srow = struct_map.get(c)
                    if srow is not None and (srow.designation or "").strip():
                        struct_items.append(
                            (f"{srow.code} — {srow.designation.strip()}", srow.code)
                        )
                    else:
                        struct_items.append((c, c))
            except Exception:
                struct_items = [
                    (s, s)
                    for s in (
                        structures or sorted({r.structure for r in rows if r.structure})
                    )
                ]
                if self._structure_filter_codes:
                    struct_items = [
                        (lbl, val)
                        for lbl, val in struct_items
                        if (val or "").strip().upper() in self._structure_filter_codes
                    ]

        grade_items: list[tuple[str, str]] = []
        try:
            from sme_gend.repositories.grade_repository import GradeRepository

            grade_map = {g.code: g for g in GradeRepository(self._settings).fetch_all() if g.code}
            grade_codes = grades or sorted({r.grade for r in rows if r.grade})
            seen_g: set[str] = set()
            for code in grade_codes:
                c = (code or "").strip()
                if not c or c.casefold() in seen_g:
                    continue
                seen_g.add(c.casefold())
                grow = grade_map.get(c)
                if grow is not None and grow.code:
                    grade_items.append((grow.code, grow.code))
                elif c in grade_map and grade_map[c].code:
                    grade_items.append((grade_map[c].code, grade_map[c].code))
                else:
                    grade_items.append((c, c))
        except Exception:
            grade_items = [(g, g) for g in grades]

        for combo, items in [
            (self._cmb_structure, struct_items),
            (self._cmb_grade, grade_items),
            (self._cmb_padmin, [(p, p) for p in padmins]),
        ]:
            combo.blockSignals(True)
            combo.clear()
            if combo is self._cmb_structure and self._structure_combo_mode == "dpf_vue":
                for label, value in items:
                    combo.addItem(label, value if value is not None else "")
            else:
                combo.addItem(TOUS, "")
                for label, value in items:
                    combo.addItem(label, value)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _flush_search_filter(self) -> None:
        if self._search_debounce.isActive() or self._proxy._text != self._search.text().strip().upper():
            self._search_debounce.stop()
            self._proxy.set_text(self._search.text())

    def _schedule_count_update(self, *_args) -> None:
        if not self._count_debounce.isActive():
            self._count_debounce.start(0)

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
        self._btn_show_mod.setText(f"{n_mod} modifié(s)")
        self._btn_show_mod.setVisible(n_mod > 0)

        n_chk = len(self._model._checked_ids)
        parts = [_edition_count_label(visible, total)]
        if n_mod:
            parts.append(f"{n_mod} modifié(s)")
        if n_chk:
            parts.append(f"{n_chk} coché(s)")
        self._lbl_status.setText(" · ".join(parts))

    def _structure_filter_code(self) -> str | None:
        from sme_gend.ui.personnel_list_filters import combo_filter_value

        return combo_filter_value(self._cmb_structure)

    def _grade_filter_code(self) -> str | None:
        from sme_gend.ui.personnel_list_filters import combo_filter_value

        return combo_filter_value(self._cmb_grade)

    def _padmin_filter_code(self) -> str | None:
        from sme_gend.ui.personnel_list_filters import combo_filter_value

        return combo_filter_value(self._cmb_padmin)

    def _subarme_filter_label(self) -> str:
        return (self._cmb_subarme.currentText() or "Toutes").strip() or "Toutes"

    def _subarme_choices_for_structure(self, structure_code: str | None) -> list[str]:
        code = (structure_code or "").strip()
        if not code:
            return []
        from sme_gend.ui.personnel_list_filters import structure_matches_filter

        from_data = [
            (r.subdivision_arme or "").strip()
            for r in self._model._rows
            if structure_matches_filter(r.structure, code)
            and (r.subdivision_arme or "").strip()
        ]
        seen: set[str] = set()
        out: list[str] = []
        for raw in from_data:
            key = raw.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(raw)
        out.sort(key=str.casefold)
        return out

    def _fill_subarme_combo(self, *, preserve_data: object = None) -> None:
        if preserve_data is None:
            preserve_data = self._cmb_subarme.currentData()
        struct_code = self._structure_filter_code()
        flat: list = []
        try:
            _, ref = self._ensure_personnel_services()
            flat = list(ref.subdivisions_flat())
        except Exception:
            pass
        from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

        self._cmb_subarme.blockSignals(True)
        try:
            self._cmb_subarme.clear()
            self._cmb_subarme.addItem("Toutes", None)
            if struct_code is not None:
                for raw in self._subarme_choices_for_structure(struct_code):
                    label = format_subdivision_arme_display(raw, flat) or raw
                    self._cmb_subarme.addItem(label, raw)
            idx = 0
            if preserve_data is not None:
                i = self._cmb_subarme.findData(preserve_data)
                if i >= 0:
                    idx = i
            self._cmb_subarme.setCurrentIndex(idx)
        finally:
            self._cmb_subarme.blockSignals(False)
        n = max(0, self._cmb_subarme.count() - 1)
        if struct_code is None:
            tip = "Choisissez d'abord une structure (pas « Tous »)."
        elif n <= 0:
            tip = (
                f"Aucune subdivision pour « {struct_code} » — actualisez (F5) "
                "ou vérifiez les données."
            )
        else:
            tip = f"{n} subdivision(s) pour {struct_code}."
        self._cmb_subarme.setToolTip(tip)

    def _on_structure_filter_changed(self, *_args) -> None:
        from sme_gend.ui.personnel_list_filters import combo_filter_value

        code = combo_filter_value(self._cmb_structure)
        if code is None and self._structure_combo_mode == "dpf_vue":
            code = self._cmb_structure.currentData()
            if code is not None:
                code = str(code)
        self._proxy.set_structure(code or "")
        self._fill_subarme_combo(preserve_data=None)
        self._on_subarme_filter_changed()

    def _on_subarme_filter_changed(self, *_args) -> None:
        data = self._cmb_subarme.currentData()
        self._proxy.set_subarme("" if data is None else str(data))
        self._update_count()

    def focus_find_hit(self, src_row: int, field: str) -> None:
        col = _COL_IDX.get(field, 1)
        src_index = self._model.index(src_row, col)
        proxy_index = self._proxy.mapFromSource(src_index)
        if not proxy_index.isValid():
            proxy_index = self._proxy.mapFromSource(self._model.index(src_row, 0))
        if proxy_index.isValid():
            self._table.selectRow(proxy_index.row())
            self._table.setCurrentIndex(proxy_index)
            self._table.scrollTo(
                proxy_index,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def _on_grade_filter(self, _text: str = "") -> None:
        code = self._grade_filter_code()
        disp = (self._cmb_grade.currentText() or "").strip() if code else ""
        self._proxy.set_grade(code or "", combo_display=disp)
        self._update_count()

    def _on_padmin_filter(self, _text: str = "") -> None:
        code = self._padmin_filter_code()
        self._proxy.set_padmin(code or "")
        self._update_count()

    def _reset_filters(self) -> None:
        self._advanced_filters.clear_rules()
        self._search.clear()
        self._flush_search_filter()
        self._cmb_structure.setCurrentIndex(0)
        self._fill_subarme_combo(preserve_data=None)
        self._cmb_grade.setCurrentIndex(0)
        self._cmb_padmin.setCurrentIndex(0)
        self._proxy.set_subarme("")
        self._btn_show_dups.setChecked(False)
        self._btn_show_inc.setChecked(False)
        self._btn_show_mod.setChecked(False)
        self._chk_no_mat_mil.setChecked(False)
        self._chk_no_mat_solde.setChecked(False)
        self._chk_presents.setChecked(False)

    def _apply_advanced_filters(self):
        self._proxy.set_advanced_rules(self._advanced_filters.rules(), self._advanced_filters.match_mode())
        self._update_count()

    def _bulk_result_message(self, updated: int, unchanged: int, errors: list[str]) -> str:
        parts: list[str] = []
        if updated:
            parts.append(f"{updated} modifié(s)")
        if unchanged:
            parts.append(f"{unchanged} déjà conforme(s)")
        if errors:
            parts.append(f"{len(errors)} erreur(s)")
        return " · ".join(parts) if parts else "Aucun changement"

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
        self._flush_search_filter()
        checked = self._get_checked_src_rows()
        return checked if checked else self._get_selected_src_rows()

    def _update_action_buttons(self) -> None:
        n_chk = len(self._model._checked_ids)
        n_sel = len(self._get_selected_src_rows())
        n_act = n_chk if n_chk else n_sel
        using_checked = n_chk > 0
        locked = self._editing_locked

        self._btn_fiche.setEnabled(not using_checked and n_sel == 1)
        self._btn_edit.setEnabled(not locked and not using_checked and n_sel == 1)
        self._btn_delete.setEnabled(not locked and n_act >= 1)
        self._btn_delete.setText(f"Supprimer ({n_act})" if n_act > 1 else "Supprimer")
        self._btn_bulk_struct.setEnabled(not locked and n_act >= 2)
        self._btn_bulk_sub.setEnabled(not locked and n_act >= 2)
        self._btn_bulk_grade.setEnabled(not locked and n_act >= 2)
        self._btn_bulk_sat.setEnabled(not locked and n_act >= 1)
        self._btn_bulk_menu.setEnabled(not locked and n_act >= 1)
        for act, min_n in zip(
            self._bulk_menu_actions,
            (2, 2, 2, 1, 1),
            strict=True,
        ):
            act.setEnabled(not locked and n_act >= min_n)
        suffix = f"({n_act} cochés)" if using_checked else (f"({n_act})" if n_act >= 2 else "…")
        bulk_label = f"Actions groupées {suffix}" if n_act >= 1 else "Actions groupées ▾"
        self._btn_bulk_menu.setText(bulk_label)
        act_bulk_struct, act_bulk_sub, act_bulk_grade, _act_bulk_sat, act_bulk_diploma = self._bulk_menu_actions
        act_bulk_struct.setText(f"Changer structure {suffix}")
        act_bulk_sub.setText(f"Changer subdivision {suffix}")
        act_bulk_grade.setText(f"Changer grade {suffix}")
        act_bulk_diploma.setText(f"Ajouter un diplôme {suffix}")

        if using_checked:
            self._lbl_sel.setText(f"{n_chk} coché(s)" + (f" · {n_sel} sélectionné(s)" if n_sel else ""))
        elif n_sel:
            self._lbl_sel.setText(f"{n_sel} sélectionné(s)")
        else:
            self._lbl_sel.setText("")

    def _on_selection_changed(self) -> None:
        self._update_action_buttons()

    def _rows_for_active_indices(self) -> list:
        from sme_gend.models.liste_generale import ListeGeneraleRow

        idxs = self._get_active_src_rows()
        out: list[ListeGeneraleRow] = []
        for i in idxs:
            if 0 <= i < len(self._model._rows):
                out.append(self._model._rows[i])
        return out

    def _prepare_padmin_change(self, row, old_padmin, new_padmin):
        from sme_gend.services.padmin_satellite_apply import prepare_position_change
        return prepare_position_change(self, self._settings, row,
                                       old_padmin=old_padmin, new_padmin=new_padmin)

    def _on_padmin_changed(self, row, old_padmin, new_padmin) -> None:
        from sme_gend.services.padmin_satellite_apply import (
            additional_position_kind,
            prompt_additional_position_for_row,
        )

        kind = additional_position_kind(new_padmin)
        if kind is not None:
            if kind == additional_position_kind(old_padmin):
                return
            if not prompt_additional_position_for_row(self, self._settings, row, kind=kind):
                message = self._model.undo_last()
                self._lbl_status.setText(
                    message or "Changement annulé : fiche complémentaire non enregistrée."
                )
            return

        from sme_gend.services.padmin_satellite_apply import prompt_satellite_for_row

        saved = prompt_satellite_for_row(
            self,
            self._settings,
            row,
            old_padmin=old_padmin,
            new_padmin=new_padmin,
        )
        from sme_gend.services.padmin_satellite import satellite_kind

        if not saved and satellite_kind(new_padmin) is not None:
            message = self._model.undo_last()
            self._lbl_status.setText(
                message or "Changement annulé : fiche complémentaire non enregistrée."
            )

    def _action_bulk_mission_stage(self) -> None:
        if not self._require_editable():
            return
        from sme_gend.services.padmin_satellite_apply import bulk_apply_mission_or_stage

        rows = self._rows_for_active_indices()
        if not rows:
            QMessageBox.information(
                self,
                "Affectation",
                "Cochez ou sélectionnez au moins une ligne.",
            )
            return
        result = bulk_apply_mission_or_stage(self, self._settings, rows)
        if result is None:
            return
        ok, errs = result
        if ok or errs:
            msg = f"{ok} agent(s) mis à jour."
            if errs:
                msg += "\n\n" + "\n".join(errs[:12])
            QMessageBox.information(self, "Affectation", msg)
        self.load_data(preserve_ui=True)

    # ── Actions clavier ───────────────────────────────────────────────────────

    def _action_undo(self) -> None:
        if self._editing_locked:
            self._lbl_status.setText("Annulation impossible — période fermée.")
            return
        msg = self._model.undo_last()
        if msg is None:
            self._lbl_status.setText("Rien à annuler")
        else:
            self._lbl_status.setText(msg)

    def _action_undo_all_session(self) -> None:
        if self._editing_locked:
            self._lbl_status.setText("Annulation impossible — période fermée.")
            return
        n = self._model.active_undo_count()
        if not n:
            self._lbl_status.setText("Rien à annuler")
            return
        reply = QMessageBox.question(
            self,
            "Annuler la session",
            f"Annuler les {n} modification(s) de cette session ?\n"
            "Les valeurs d'origine seront réécrites en base.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        reverted, errors = self._model.undo_all_session()
        self._update_count()
        if errors:
            QMessageBox.warning(
                self,
                "Annulation partielle",
                f"{reverted} modification(s) annulée(s).\n\n"
                + "\n".join(errors[:10]),
            )
        else:
            self._lbl_status.setText(f"{reverted} modification(s) annulée(s).")

    def _show_session_log(self) -> None:
        """Ouvre la fenêtre d'historique des modifications de la session."""
        if not self._model.session_log_entries():
            QMessageBox.information(
                self,
                "Historique de la session",
                "Aucune modification enregistrée pendant cette session.",
            )
            return
        _SessionHistoryDialog(self._model, self).exec()

    def _on_table_click(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        source = self._proxy.mapToSource(index)
        if source.column() == _COL_IDX["diplome"]:
            self._add_diplome(self._model._rows[source.row()])

    def _action_bulk_diploma(self) -> None:
        from copy import deepcopy
        from sme_gend.services.sensitive_data_policy import assert_may_add_diplome
        from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        from sme_gend.ui.bg_task import run_with_determinate_progress
        from sme_gend.services.bulk_diploma import add_bulk_diploma
        self._apply_editing_lock()
        if not self._require_editable():
            return
        rows = deepcopy([self._model._rows[i] for i in self._get_active_src_rows()])
        if not rows:
            QMessageBox.information(self, "Diplôme", "Cochez ou sélectionnez les agents concernés.")
            return
        try:
            assert_may_add_diplome()
        except PermissionError as exc:
            QMessageBox.warning(self, "Diplôme", str(exc))
            return
        dialog = DiplomeEditDialog(self)
        dialog.setWindowTitle(f"Ajouter un diplôme — {len(rows)} agent(s)")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = deepcopy(dialog.values())
        if not confirm_yes_no(self, "Ajout groupé de diplôme",
                f"Ajouter « {values['libelle']} » au dossier des {len(rows)} agent(s) concernés ?\n"
                "La catégorie, la date et les autres informations seront identiques pour tous.\n"
                "Les diplômes identiques déjà présents seront conservés sans doublon.", default_no=True):
            return
        self._apply_editing_lock()
        if not self._require_editable():
            return
        settings = self._settings
        permission = self._edit_permission
        result = run_with_determinate_progress(self, "Ajout groupé de diplôme", len(rows),
            lambda report: add_bulk_diploma(settings, rows, values, report, permission=permission))
        if result is None:
            return
        self.load_data(preserve_ui=True)
        message = f"{result.added} diplôme(s) ajouté(s), {result.existing} déjà présent(s), {len(result.errors)} échec(s)."
        if result.duplicate_agents:
            message += f"\n{result.duplicate_agents} ligne(s) du même agent ignorée(s)."
        if result.errors:
            message += "\n\nÉchecs :\n" + "\n".join(result.errors[:12])
        if result.details:
            message += "\n\nRésultat :\n" + "\n".join(result.details[:12])
        QMessageBox.information(self, "Diplôme", message)

    def _add_diplome(self, row: ListeGeneraleRow) -> None:
        from sme_gend.services.agent_dossier_service import AgentDossierService
        from sme_gend.services.sensitive_data_policy import assert_may_add_diplome
        from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog

        self._apply_editing_lock()
        if not self._require_editable():
            return
        try:
            assert_may_add_diplome()
        except PermissionError as exc:
            QMessageBox.warning(self, "Diplôme", str(exc))
            return
        dlg = DiplomeEditDialog(self)
        dlg.setWindowTitle(f"Ajouter un diplôme — {row.noms or row.id}")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Le mois ou les droits peuvent avoir changé pendant la saisie.
        self._apply_editing_lock()
        if not self._require_editable():
            return
        try:
            AgentDossierService(self._settings).add_diplome(row, **dlg.values())
        except Exception as exc:
            QMessageBox.critical(self, "Diplôme", str(exc))
            return
        self._sync_liste_rows_from_db([row.id])

    def _on_table_double_click(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        if self._proxy.mapToSource(index).column() == _COL_IDX["diplome"]:
            return
        if self._proxy.mapToSource(index).column() in _EDITABLE:
            return
        if len(self._get_selected_src_rows()) == 1:
            self._open_full_dossier()

    def _copy_from_above(self) -> None:
        if not self._require_editable():
            return
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

            act_dossier = menu.addAction("Dossier complet…\tEntrée")
            act_dossier.setEnabled(len(self._get_selected_src_rows()) == 1)
            act_dossier.triggered.connect(self._open_full_dossier)
            act_edit = menu.addAction("Modifier…\tF2")
            act_edit.setEnabled(
                len(self._get_selected_src_rows()) == 1 and not self._editing_locked
            )
            act_edit.triggered.connect(self._edit_current)
            act_resume = menu.addAction("Résumé rapide")
            act_resume.setEnabled(len(self._get_selected_src_rows()) == 1)
            act_resume.triggered.connect(self._action_fiche_summary)
            menu.addSeparator()

        act_reset = menu.addAction("Réinitialiser tous les filtres")
        act_reset.triggered.connect(self._reset_filters)

        if not self._editing_locked:
            menu.addSeparator()
            menu.addAction("Nouveau personnel…\tCtrl+N", self._create_new)
            menu.addAction("Importer CSV…", self._import_csv_updates)

        act_undo = menu.addAction("Annuler dernière modification\tCtrl+Z")
        act_undo.setEnabled(self._model.active_undo_count() > 0)
        act_undo.triggered.connect(self._action_undo)
        act_undo_all = menu.addAction("Annuler toute la session")
        act_undo_all.setEnabled(self._model.active_undo_count() > 0)
        act_undo_all.triggered.connect(self._action_undo_all_session)
        act_hist = menu.addAction("Journal de la session…")
        act_hist.setEnabled(bool(self._model.session_log_entries()))
        act_hist.triggered.connect(self._show_session_log)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _set_filter_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # ── Rechercher / Remplacer ────────────────────────────────────────────────

    def _open_find_replace(self) -> None:
        if not self._require_editable():
            return
        if self._find_replace_dlg and self._find_replace_dlg.isVisible():
            self._find_replace_dlg.raise_()
            self._find_replace_dlg.activateWindow()
            return
        self._find_replace_dlg = _FindReplaceDialog(self)
        self._find_replace_dlg.show()

    # ── Export CSV ────────────────────────────────────────────────────────────

    def _rows_for_export_scope(self, scope: str) -> tuple[list[ListeGeneraleRow], str]:
        if scope == "checked":
            src_rows = self._get_checked_src_rows()
            rows = [self._model._rows[r] for r in src_rows if 0 <= r < len(self._model._rows)]
            return rows, f"lignes cochées ({len(rows)})"
        if scope == "selection":
            src_rows = self._get_selected_src_rows()
            rows = [self._model._rows[r] for r in src_rows if 0 <= r < len(self._model._rows)]
            return rows, f"sélection ({len(rows)})"
        rows = []
        for pr in range(self._proxy.rowCount()):
            sr = self._proxy.mapToSource(self._proxy.index(pr, 0)).row()
            if 0 <= sr < len(self._model._rows):
                rows.append(self._model._rows[sr])
        return rows, f"vue filtrée ({len(rows)})"

    def _action_export_csv(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        choices = ["Vue filtrée (toutes les lignes visibles)"]
        default_idx = 0
        n_chk = len(self._model._checked_ids)
        n_sel = len(self._get_selected_src_rows())
        if n_chk:
            choices.insert(0, f"Lignes cochées ({n_chk})")
            default_idx = 0
        if n_sel:
            choices.insert(0, f"Sélection bleue ({n_sel})")
            default_idx = 0
        choice, ok = QInputDialog.getItem(
            self,
            "Exporter CSV",
            "Quelles lignes exporter ?",
            choices,
            default_idx,
            False,
        )
        if not ok:
            return
        if choice.startswith("Lignes cochées"):
            scope = "checked"
        elif choice.startswith("Sélection"):
            scope = "selection"
        else:
            scope = "filtered"

        rows_to_export, label = self._rows_for_export_scope(scope)
        if not rows_to_export:
            QMessageBox.information(self, "Export CSV", "Aucune ligne à exporter.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, f"Exporter {label}", "", "Fichier CSV (*.csv)"
        )
        if not path:
            return

        headers = [c[1] for c in self._visible_export_cols()]
        fields  = [c[0] for c in self._visible_export_cols()]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(headers)
            for row in rows_to_export:
                w.writerow([_format_cell_value(getattr(row, fld, None)) for fld in fields])

        self._lbl_status.setText(f"Export : {len(rows_to_export)} ligne(s) → {Path(path).name}")

    # ── Actions tableau ───────────────────────────────────────────────────────

    def _open_full_dossier(self) -> None:
        from sme_gend.services.auth_guard import session_can_modify_personnel
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
        from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog

        if self._opening_dossier:
            return
        src_rows = self._get_selected_src_rows()
        if len(src_rows) != 1:
            QMessageBox.information(
                self,
                "Dossier",
                "Sélectionnez une seule ligne (sélection bleue, pas les coches).",
            )
            return
        row_obj = self._model._rows[src_rows[0]]
        service, referentiel = self._ensure_personnel_services()
        st = evaluate_editing_state_for_settings(self._settings)
        self._opening_dossier = True
        allow_edit = not st.locked and session_can_modify_personnel()

        def _run_open() -> None:
            try:
                result = open_personnel_dossier_dialog(
                    self,
                    row_obj,
                    referentiel,
                    service,
                    allow_edit=allow_edit,
                    refresh_from_db=True,
                    permission_id='effectifs.edition',
                )
            finally:
                self._opening_dossier = False
            if result.liste_generale_changed:
                self._sync_liste_rows_from_db([row_obj.id])

        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, _run_open)

    def _action_fiche_summary(self) -> None:
        src_rows = self._get_selected_src_rows()
        if len(src_rows) != 1:
            return
        _FicheDialog(self._model._rows[src_rows[0]], self).exec()

    def _action_delete(self) -> None:
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
        from sme_gend.ui.confirm_dialogs import confirm_yes_no

        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Suppression impossible", st.detail)
            return
        src_rows = self._get_active_src_rows()
        if not src_rows:
            return
        names = ", ".join((self._model._rows[r].noms or "?") for r in src_rows[:5])
        if len(src_rows) > 5:
            names += f" … et {len(src_rows) - 5} autre(s)"
        if not confirm_yes_no(
            self,
            "Supprimer définitivement",
            f"Supprimer {len(src_rows)} fiche(s) en base MySQL ?\n\n{names}\n\n"
            "Une perte sera enregistrée (motif à l'étape suivante). "
            "Restauration possible via « Fiches retirées de la liste ».",
            default_no=True,
        ):
            return
        from sme_gend.ui.personnel_delete_dialog import pick_personnel_loss_motif

        perte_motif = pick_personnel_loss_motif(self, title="Motif de sortie")
        if not perte_motif:
            return
        ok, errors = self._model.remove_rows(src_rows, motif=perte_motif)
        if errors:
            QMessageBox.warning(self, "Erreurs de suppression", "\n".join(errors[:10]))
        if ok:
            self._lbl_status.setText(f"{ok} fiche(s) supprimée(s)")
        self._update_count()

    def _action_bulk_structure(self) -> None:
        if not self._require_editable():
            return
        src_rows = self._get_active_src_rows()
        if len(src_rows) < 2:
            return
        if not self._settings:
            return
        structures_sel = {
            (self._model._rows[r].structure or "").strip()
            for r in src_rows
            if (self._model._rows[r].structure or "").strip()
        }
        initial = next(iter(structures_sel)) if len(structures_sel) == 1 else None
        from sme_gend.ui.edition_sme_bulk_pick_dialog import run_bulk_structure_pick

        new_val = run_bulk_structure_pick(
            self,
            self._settings,
            person_count=len(src_rows),
            initial_code=initial,
            extra_codes=list(structures_sel),
            with_reference=True,
        )
        if new_val is None:
            return
        new_val, reference_note = new_val if isinstance(new_val, tuple) else (new_val, '')
        if new_val not in self._model.structures:
            self._model.structures = sorted(
                set(self._model.structures) | {new_val}, key=str.casefold
            )
        if QMessageBox.question(
            self, "Confirmer le changement de structure",
            f"Affecter la structure « {new_val} » à {len(src_rows)} personne(s) ?\n"
            "Cette opération est immédiatement enregistrée en base.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        updated, unchanged, errors = self._model.bulk_set_field(
            src_rows, "structure", new_val, reference_note=reference_note
        )
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._lbl_status.setText(self._bulk_result_message(updated, unchanged, errors))
        if updated:
            self._offer_subdivision_reset_after_structure(src_rows, new_val)
            self._offer_fonction_reset_after_structure(src_rows, new_val)
        self._update_count()

    def _offer_fonction_reset_after_structure(
        self, src_rows: list[int], new_structure: str | None
    ) -> None:
        struct = (new_structure or "").strip()
        if not struct or not self._settings:
            return
        try:
            from sme_gend.services.referentiel_service import ReferentielService

            allowed = set(ReferentielService(self._settings).fonction_choices_for_structure(struct))
        except Exception:
            return
        if not allowed:
            return
        to_fix: list[int] = []
        for r in src_rows:
            if not (0 <= r < len(self._model._rows)):
                continue
            fn = (self._model._rows[r].fonction or "").strip()
            if fn and fn not in allowed:
                to_fix.append(r)
        if not to_fix:
            return
        reply = QMessageBox.question(
            self,
            "Fonction incohérente",
            f"{len(to_fix)} personne(s) ont une fonction non rattachée "
            f"à la structure « {struct} ».\n\nEffacer la fonction pour ces lignes ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        u, _uc, errs = self._model.bulk_set_field(to_fix, "fonction", None)
        if errs:
            QMessageBox.warning(self, "Erreurs", "\n".join(errs[:10]))
        if u:
            self._lbl_status.setText(
                self._lbl_status.text() + f" · {u} fonction(s) effacée(s)"
            )

    def _offer_subdivision_reset_after_structure(
        self, src_rows: list[int], new_structure: str | None
    ) -> None:
        if not (new_structure or "").strip() or not self._model._repo:
            return
        to_fix: list[int] = []
        for r in src_rows:
            if not (0 <= r < len(self._model._rows)):
                continue
            row = self._model._rows[r]
            sub = (row.subdivision_arme or "").strip()
            if not sub:
                continue
            try:
                opts = self._model._repo.fetch_distinct_subarme_for_structure(
                    new_structure.strip()
                )
            except Exception:
                opts = []
            if opts and sub not in opts:
                to_fix.append(r)
        if not to_fix:
            return
        reply = QMessageBox.question(
            self,
            "Subdivision incohérente",
            f"{len(to_fix)} personne(s) ont une subdivision qui ne correspond peut-être plus "
            f"à la structure « {new_structure} ».\n\nEffacer la subdivision pour ces lignes ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        u, uc, errs = self._model.bulk_set_field(to_fix, "subdivision_arme", None)
        if errs:
            QMessageBox.warning(self, "Erreurs", "\n".join(errs[:10]))
        if u:
            self._lbl_status.setText(
                self._lbl_status.text()
                + f" · {u} subdivision(s) effacée(s)"
            )

    def _action_bulk_subdivision(self) -> None:
        if not self._require_editable():
            return
        src_rows = self._get_active_src_rows()
        if len(src_rows) < 2:
            return
        if not self._settings:
            return
        structures_sel = {
            (self._model._rows[r].structure or "").strip()
            for r in src_rows
            if (self._model._rows[r].structure or "").strip()
        }
        initial_struct = next(iter(structures_sel)) if len(structures_sel) == 1 else None
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        from sme_gend.ui.edition_sme_bulk_pick_dialog import run_bulk_subdivision_pick

        mois_aff = (resolve_liste_view_mois(self._settings) or "").strip()

        picked = run_bulk_subdivision_pick(
            self,
            self._settings,
            person_count=len(src_rows),
            initial_structure=initial_struct,
            extra_structure_codes=list(structures_sel),
            subdivisions_for_structure=self._model.subdivision_choices,
            scope_mois_label=mois_aff or None,
        )
        if picked is None:
            return
        struct_code, new_val = picked
        mismatch = sum(
            1
            for r in src_rows
            if (self._model._rows[r].structure or "").strip().casefold()
            != struct_code.casefold()
        )
        confirm_lines = [
            f"Structure : {struct_code}",
            f"Subdivision : {new_val}",
            f"{len(src_rows)} personne(s) concernée(s).",
        ]
        if mismatch:
            confirm_lines.append(
                f"{mismatch} fiche(s) seront aussi passées sur la structure « {struct_code} »."
            )
        confirm_lines.append("Enregistrement immédiat en base.")
        if QMessageBox.question(
            self,
            "Confirmer le changement",
            "\n".join(confirm_lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        errors: list[str] = []
        updated = 0
        unchanged = 0
        if mismatch:
            u_s, uc_s, err_s = self._model.bulk_set_field(src_rows, "structure", struct_code)
            updated += u_s
            unchanged += uc_s
            errors.extend(err_s)
            if struct_code not in self._model.structures:
                self._model.structures = sorted(
                    set(self._model.structures) | {struct_code}, key=str.casefold
                )
        self._model.register_subdivision_label(new_val)
        u, uc, err = self._model.bulk_set_field(src_rows, "subdivision_arme", new_val)
        updated += u
        unchanged += uc
        errors.extend(err)
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._fill_subarme_combo(preserve_data=new_val)
        col = _COL_SUBDIVISION
        for src_row in src_rows:
            if 0 <= src_row < self._model.rowCount():
                self._view.update(self._model.index(src_row, col))
        self._lbl_status.setText(self._bulk_result_message(updated, unchanged, errors))
        self._update_count()

    def _prompt_grade_nomination_date(
        self,
        row: ListeGeneraleRow,
        _old_grade: str | None,
        new_grade: str,
    ) -> GradeNomination | None:
        from sme_gend.services.grade_label_format import format_grade_display
        from sme_gend.ui.widgets.date_input import FrenchDateEdit

        grade_rows = self._model._grade_rows
        label = format_grade_display(new_grade, grade_rows) or new_grade
        who = (row.noms or row.id or "?").strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("Nomination au grade — Date et référence")
        dlg.setModal(True)
        dlg.setMinimumWidth(380)
        lay = QVBoxLayout(dlg)
        intro = QLabel(
            f"<b>{html.escape(who)}</b><br>"
            f"Nouveau grade : <b>{html.escape(label)}</b><br><br>"
            "Indiquez la <b>date de nomination</b> à ce grade."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        lay.addWidget(QLabel("Date de nomination :"))
        date_edit = FrenchDateEdit(optional=False)
        if row.date_grade is not None:
            date_edit.set_from_datetime(row.date_grade)
        lay.addWidget(date_edit)
        lay.addWidget(QLabel("Ref/OG/Decret (facultatif) :"))
        reference_edit = QLineEdit()
        reference_edit.setMaxLength(128)
        reference_edit.setPlaceholderText("Référence de l’OG ou du décret")
        lay.addWidget(reference_edit)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Valider")
        cancel_btn = bb.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Annuler")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        dt = date_edit.to_datetime()
        if dt is None:
            QMessageBox.warning(
                self,
                "Date de nomination",
                "La date de nomination au grade est obligatoire.",
            )
            return None
        return GradeNomination(dt, reference_edit.text().strip() or None)

    def _action_bulk_grade(self) -> None:
        if not self._require_editable():
            return
        src_rows = self._get_active_src_rows()
        if len(src_rows) < 2:
            return
        from sme_gend.services.grade_label_format import format_grade_display
        from sme_gend.ui.widgets.date_input import FrenchDateEdit

        grade_rows = self._model._grade_rows
        options = self._model.grades or sorted(
            {(self._model._rows[r].grade or "").strip() for r in src_rows}
            - {""}
        )
        display_options = [
            format_grade_display(code, grade_rows) or code for code in options
        ]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Changer le grade — {len(src_rows)} personne(s)")
        dlg.setModal(True)
        dlg.setMinimumWidth(400)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Nouveau grade :"))
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem("")
        for opt in display_options or options:
            combo.addItem(opt)
        lay.addWidget(combo)
        lay.addWidget(QLabel("Date de nomination au grade :"))
        date_edit = FrenchDateEdit(optional=False)
        lay.addWidget(date_edit)
        lay.addWidget(QLabel("Ref/OG/Decret (facultatif) :"))
        reference_edit = QLineEdit()
        reference_edit.setMaxLength(128)
        reference_edit.setPlaceholderText("Référence de l’OG ou du décret")
        lay.addWidget(reference_edit)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Valider")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = combo.currentText().strip()
        if not picked:
            QMessageBox.warning(self, "Grade", "Choisissez un grade.")
            return
        new_val = picked
        for code in options:
            label = format_grade_display(code, grade_rows) or code
            if label.casefold() == picked.casefold() or code.casefold() == picked.casefold():
                new_val = code
                break
        nomination = date_edit.to_datetime()
        if nomination is None:
            QMessageBox.warning(
                self,
                "Date de nomination",
                "La date de nomination au grade est obligatoire.",
            )
            return
        if QMessageBox.question(
            self,
            "Confirmer le changement de grade",
            f"Affecter le grade « {format_grade_display(new_val, grade_rows) or new_val} » "
            f"(nomination le {nomination.strftime('%d/%m/%Y')}) "
            f"à {len(src_rows)} personne(s) ?\n"
            "Cette opération est immédiatement enregistrée en base.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        updated, unchanged, errors = self._model.bulk_set_grade(
            src_rows, new_val, nomination, reference=reference_edit.text().strip() or None
        )
        if updated:
            QMessageBox.information(self, "Passations enregistrées",
                f"{updated} passation(s) enregistrée(s) au {nomination.strftime('%d/%m/%Y')} "
                f"pour le mois {nomination.strftime('%Y-%m')}. Les situations existantes sont recalculées "
                "à partir de cette date ; le mois affiché conserve le grade applicable à son historique.")
        if errors:
            QMessageBox.warning(self, "Erreurs", "\n".join(errors[:10]))
        self._lbl_status.setText(self._bulk_result_message(updated, unchanged, errors))
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
        if _msg.startswith(('Passation enregistrée','Événement enregistré')):
            QMessageBox.information(self, "Passation enregistrée", _msg)

    def _on_save_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Erreur de sauvegarde", msg)
