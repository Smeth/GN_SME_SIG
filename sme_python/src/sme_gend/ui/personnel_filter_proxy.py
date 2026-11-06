"""Proxy de filtrage liste générale sans reconstruire le modèle."""

from __future__ import annotations

from sme_gend.ui.filter_change import row_filter_change

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.personnel_advanced_filter import (
    AdvancedFilterRule,
    MatchMode,
    rules_match,
)
from sme_gend.ui.personnel_list_filters import (
    free_text_matches,
    grade_matches_filter,
    padmin_matches_filter,
    structure_matches_filter,
)
from sme_gend.services.matricule_format import matricule_solde_is_empty
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.ui.personnel_table_model import PersonnelTableModel


class PersonnelFilterProxy(QSortFilterProxyModel):
    GRADE_COLUMN = 3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grade_sort: GradeSortHelper | None = None
        self._only_empty_structure = False
        self._padmin: str | None = None
        self._grade: str | None = None
        self._grade_combo_display: str | None = None
        self._padmin_rows: list = []
        self._structure: str | None = None
        self._subarme: str | None = None
        self._needle = ""
        self._without_mat_mil = False
        self._without_mat_solde = False
        self._advanced_rules: list[AdvancedFilterRule] = []
        self._advanced_mode: MatchMode = "and"
        self._header_rules: list[AdvancedFilterRule] = []
        self._sot_gap_only = False
        self._fk_unresolved_only = False
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortRole(Qt.ItemDataRole.DisplayRole)

    def set_grade_sort_helper(self, helper: GradeSortHelper | None) -> None:
        self._grade_sort = helper

    def set_padmin_rows(self, rows) -> None:
        self._padmin_rows = list(rows or [])

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        # Qt passe des indices du modèle source (pas du proxy).
        if self.sortColumn() == self.GRADE_COLUMN and self._grade_sort is not None:
            model = self.sourceModel()
            if isinstance(model, PersonnelTableModel):
                lp = model.row_at(left.row())
                rp = model.row_at(right.row())
                if lp is not None and rp is not None:
                    return self._grade_sort.compare_less(lp.grade, rp.grade)
        return super().lessThan(left, right)

    def sort_by_grade(self) -> None:
        """Applique l'ordre métier par préséance, sans tenir compte des fonctions."""
        self.sort(self.GRADE_COLUMN, Qt.SortOrder.AscendingOrder)

    @row_filter_change
    def set_filter_criteria(
        self,
        *,
        only_empty_structure: bool,
        padmin: str | None,
        grade: str | None,
        grade_combo_display: str | None = None,
        structure: str | None,
        subarme: str | None,
        needle: str,
        without_matricule_militaire: bool = False,
        without_matricule_solde: bool = False,
        advanced_rules: list[AdvancedFilterRule] | None = None,
        advanced_match_mode: MatchMode = "and",
        header_rules: list[AdvancedFilterRule] | None = None,
    ) -> None:
        self._only_empty_structure = only_empty_structure
        self._padmin = padmin
        self._grade = grade
        self._grade_combo_display = grade_combo_display
        self._structure = structure
        self._subarme = subarme
        self._needle = (needle or "").strip().lower()
        self._without_mat_mil = without_matricule_militaire
        self._without_mat_solde = without_matricule_solde
        self._advanced_rules = list(advanced_rules or [])
        self._advanced_mode = advanced_match_mode if advanced_match_mode in ("and", "or") else "and"
        self._header_rules = list(header_rules or [])

    @row_filter_change
    def set_sot_gap_only(self, enabled: bool) -> None:
        self._sot_gap_only = bool(enabled)

    @row_filter_change
    def set_fk_unresolved_only(self, enabled: bool) -> None:
        self._fk_unresolved_only = bool(enabled)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        del source_parent
        model = self.sourceModel()
        if not isinstance(model, PersonnelTableModel):
            return True
        row = model.row_at(source_row)
        if row is None:
            return False
        if self._sot_gap_only and row.id not in model._sot_gap_ids:
            return False
        if self._fk_unresolved_only and row.id not in model._fk_unresolved_ids:
            return False
        if not self._structural_matches(row):
            return False
        if self._without_mat_mil and (row.matricule_militaire or "").strip():
            return False
        if self._without_mat_solde and not matricule_solde_is_empty(row.matricule_solde):
            return False
        # Filtres ▾ en-tête : toujours ET entre colonnes, puis ET avec le reste.
        if self._header_rules and not rules_match(row, self._header_rules, mode="and"):
            return False
        if self._advanced_rules and not rules_match(
            row, self._advanced_rules, mode="and"
        ):
            return False
        if self._needle and not free_text_matches(row, self._needle, grades=getattr(model, "_grade_rows", None)):
            return False
        return True

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """Forward UserRole (colonne Actions) — requis pour le délégué boutons."""
        if role == Qt.ItemDataRole.UserRole and index.isValid():
            src = self.mapToSource(index)
            sm = self.sourceModel()
            if src.isValid() and sm is not None:
                return sm.data(src, role)
        return super().data(index, role)

    def _structural_matches(self, row: ListeGeneraleRow) -> bool:
        if self._only_empty_structure and (row.structure or "").strip():
            return False
        if self._padmin is not None and not padmin_matches_filter(
            row.position_admin,
            self._padmin,
            padmins=self._padmin_rows or None,
        ):
            return False
        model = self.sourceModel()
        grade_rows = (
            getattr(model, "_grade_rows", None)
            if isinstance(model, PersonnelTableModel)
            else None
        )
        if self._grade is not None and not grade_matches_filter(
            row.grade,
            self._grade,
            combo_display=self._grade_combo_display,
            grades=grade_rows,
        ):
            return False
        if self._structure is not None and not structure_matches_filter(row.structure, self._structure):
            return False
        if self._subarme is not None:
            row_sub = (row.subdivision_arme or "").strip()
            if row_sub.casefold() != str(self._subarme).strip().casefold():
                return False
        return True
