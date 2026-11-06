"""QTableWidgetItem pour trier la colonne Grade par ordre de préséance (PrefOrd)."""

from __future__ import annotations

from PySide6.QtWidgets import QTableWidgetItem

from sme_gend.config.settings import Settings
from sme_gend.services.personnel_list_order import GradeSortHelper


def grade_sort_helper_from_settings(settings: Settings) -> GradeSortHelper:
    from sme_gend.repositories.grade_repository import GradeRepository

    try:
        grades = GradeRepository(settings).fetch_all()
    except Exception:
        grades = []
    return GradeSortHelper(grades)


class GradeSortTableWidgetItem(QTableWidgetItem):
    """Item grade : comparaison par PrefOrd TabGrade, pas alphabétique."""

    def __init__(self, grade: str | None, helper: GradeSortHelper) -> None:
        super().__init__(helper.display_label(grade))
        self._sort_key = helper.sort_key(grade)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: N802
        if isinstance(other, GradeSortTableWidgetItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)
