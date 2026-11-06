"""Modèle de tableau Qt — affichage performant (pas de QTableWidget avec 800+ cellules pré-allouées)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sexe import sexe_display
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.matricule_format import format_matricule_solde_display
from sme_gend.services.structure_label_format import format_structure_code_display
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

# Colonnes affichées (ordre fixe — col 1 « Nom » et dernière « Actions » toujours visibles).
_COLUMNS = (
    ("id",                  "N°"),              # 0
    ("noms",                "Nom et prénom"),   # 1  — toujours visible
    ("sexe",                "Sexe"),            # 2  — toujours visible
    ("grade",               "Grade"),           # 3
    ("fonction",            "Fonction"),        # 4
    ("subdivision_arme",    "Subdivision"),     # 5
    ("position_admin",      "Position adm."),   # 6
    ("structure",           "Structure"),       # 7
    ("matricule_militaire", "Matricule mil."),  # 8
    # ── colonnes supplémentaires (masquées par défaut) ───────────────────────
    ("matricule_solde",     "Matricule solde"), # 9
    ("date_naissance",      "Date naissance"),  # 10
    ("lieu_naissance",      "Lieu naissance"),  # 11
    ("telephone",           "Téléphone"),       # 12
    ("adresse_residence",   "Adresse résid."),  # 13
    ("date_grade",          "Date de grade"),   # 14
    ("date_service",        "Date de service"), # 15
    ("ndecision",           "N° décision"),     # 16
    ("diplome",             "Diplôme"),         # 17
    ("date_obtention",      "Date diplôme"),    # 18
    ("obs",                 "Observations"),    # 19
    ("presseance",          "Préséance"),       # 20
    ("ordre_tri",           "Ordre tri"),       # 21
    (None,                  "Actions"),         # 22 — toujours visible, peint par PersonnelActionDelegate
)

_HEADER_FILTER_MARK = " ▾"

from sme_gend.ui.row_quality_colors import highlight_fk_unresolved, highlight_sot_gap


def column_field_key(section: int) -> str | None:
    if 0 <= section < len(_COLUMNS):
        return _COLUMNS[section][0]
    return None


def column_title(section: int) -> str:
    if 0 <= section < len(_COLUMNS):
        return _COLUMNS[section][1]
    return ""


class PersonnelTableModel(QAbstractTableModel):
    """Données tabulaires pour QTableView."""

    def __init__(
        self,
        rows: list[ListeGeneraleRow] | None = None,
        parent=None,
        *,
        subdivision_flat: Sequence[SubdivisionFlatRow] | None = None,
        grade_rows: Sequence[GradeRefRow] | None = None,
        structure_rows: Sequence[StructureRow] | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[ListeGeneraleRow] = rows or []
        self._subdivision_flat: Sequence[SubdivisionFlatRow] | None = subdivision_flat
        self._grade_rows: Sequence[GradeRefRow] = grade_rows or ()
        self._structure_rows: Sequence[StructureRow] = structure_rows or ()
        self._header_filtered: set[str] = set()
        self._sot_gap_ids: set[str] = set()
        self._fk_unresolved_ids: set[str] = set()

    def set_rows(self, rows: list[ListeGeneraleRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def set_header_filtered_columns(self, keys: set[str] | frozenset[str]) -> None:
        new = set(keys)
        if new == self._header_filtered:
            return
        self._header_filtered = new
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal,
            0,
            self.columnCount() - 1,
        )

    def set_subdivision_flat(self, subdivision_flat: Sequence[SubdivisionFlatRow] | None) -> None:
        self._subdivision_flat = subdivision_flat
        if not self._rows:
            return
        self.beginResetModel()
        self.endResetModel()

    def set_grade_rows(self, grade_rows: Sequence[GradeRefRow] | None) -> None:
        self._grade_rows = grade_rows or ()
        if not self._rows:
            return
        self.beginResetModel()
        self.endResetModel()

    def set_structure_rows(self, structure_rows: Sequence[StructureRow] | None) -> None:
        self._structure_rows = structure_rows or ()
        if not self._rows:
            return
        self.beginResetModel()
        self.endResetModel()

    def set_quality_ids(
        self,
        *,
        sot_gap_ids: set[str] | None = None,
        fk_unresolved_ids: set[str] | None = None,
    ) -> None:
        new_gap = set(sot_gap_ids or ())
        new_fk = set(fk_unresolved_ids or ())
        if new_gap == self._sot_gap_ids and new_fk == self._fk_unresolved_ids:
            return
        self._sot_gap_ids = new_gap
        self._fk_unresolved_ids = new_fk
        if not self._rows:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._rows) - 1, self.columnCount() - 1)
        self.dataChanged.emit(
            top_left,
            bottom_right,
            [
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.ToolTipRole,
            ],
        )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(_COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or not self._rows:
            return None
        row = self._rows[index.row()]
        key = _COLUMNS[index.column()][0]
        if key is None:
            # Colonne Actions : retourne l'objet ligne pour le délégué
            if role == Qt.ItemDataRole.UserRole:
                return row
            return None
        value = getattr(row, key)
        if role == Qt.ItemDataRole.DisplayRole:
            if key == "grade":
                return format_grade_display(row.grade, self._grade_rows)
            if key == "subdivision_arme" and self._subdivision_flat is not None:
                return format_subdivision_arme_display(
                    row.subdivision_arme,
                    self._subdivision_flat,
                )
            if key == "structure" and self._structure_rows:
                return format_structure_code_display(row.structure, self._structure_rows)
            if key == "matricule_solde":
                return format_matricule_solde_display(row.matricule_solde, empty="")
            if key == "sexe":
                return sexe_display(row.sexe)
            if value is None:
                return ""
            return str(value)
        if role == Qt.ItemDataRole.BackgroundRole:
            if row.id in self._sot_gap_ids:
                return highlight_sot_gap().bg
            if row.id in self._fk_unresolved_ids:
                return highlight_fk_unresolved().bg
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if row.id in self._sot_gap_ids:
                return highlight_sot_gap().fg
            if row.id in self._fk_unresolved_ids:
                return highlight_fk_unresolved().fg
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            tips: list[str] = []
            if row.id in self._sot_gap_ids:
                tips.append(
                    "Grade ou diplôme différent du dossier de l'agent — "
                    "ouvrir « Fiche dossier » pour corriger."
                )
            if row.id in self._fk_unresolved_ids:
                tips.append(
                    "Valeur absente des référentiels (grade, structure, "
                    "position administrative, fonction…)"
                )
            return "\n".join(tips) if tips else None
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            key = _COLUMNS[section][0]
            title = _COLUMNS[section][1]
            if key and key in self._header_filtered:
                return title + _HEADER_FILTER_MARK
            return title
        return str(section + 1)

    def row_at(self, row: int) -> ListeGeneraleRow | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if _COLUMNS[index.column()][0] is None:
            return base
        return base
