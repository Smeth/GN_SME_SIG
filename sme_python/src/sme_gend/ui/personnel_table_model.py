"""Modèle de tableau Qt — affichage performant (pas de QTableWidget avec 800+ cellules pré-allouées)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

# Colonnes affichées (ordre fixe — col 1 « Nom » et dernière « Actions » toujours visibles).
_COLUMNS = (
    ("id",                  "N°"),              # 0
    ("noms",                "Nom et prénom"),   # 1  — toujours visible
    ("grade",               "Grade"),           # 2
    ("fonction",            "Fonction"),        # 3
    ("subdivision_arme",    "Subdivision"),     # 4
    ("position_admin",      "Position adm."),   # 5
    ("structure",           "Structure"),       # 6
    ("matricule_militaire", "Matricule mil."),  # 7
    # ── colonnes supplémentaires (masquées par défaut) ───────────────────────
    ("matricule_solde",     "Matricule solde"), # 8
    ("date_naissance",      "Date naissance"),  # 9
    ("lieu_naissance",      "Lieu naissance"),  # 10
    ("date_grade",          "Date de grade"),   # 11
    ("date_service",        "Date de service"), # 12
    ("ndecision",           "N° décision"),     # 13
    ("diplome",             "Diplôme"),         # 14
    ("date_obtention",      "Date diplôme"),    # 15
    ("obs",                 "Observations"),    # 16
    ("presseance",          "Préséance"),       # 17
    ("ordre_tri",           "Ordre tri"),       # 18
    (None,                  "Actions"),         # 19 — toujours visible, peint par PersonnelActionDelegate
)


class PersonnelTableModel(QAbstractTableModel):
    """Données tabulaires pour QTableView."""

    def __init__(
        self,
        rows: list[ListeGeneraleRow] | None = None,
        parent=None,
        *,
        subdivision_flat: Sequence[SubdivisionFlatRow] | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[ListeGeneraleRow] = rows or []
        self._subdivision_flat: Sequence[SubdivisionFlatRow] | None = subdivision_flat

    def set_rows(self, rows: list[ListeGeneraleRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def set_subdivision_flat(self, subdivision_flat: Sequence[SubdivisionFlatRow] | None) -> None:
        self._subdivision_flat = subdivision_flat
        if not self._rows:
            return
        self.beginResetModel()
        self.endResetModel()

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
            if key == "subdivision_arme" and self._subdivision_flat is not None:
                return format_subdivision_arme_display(
                    row.subdivision_arme,
                    self._subdivision_flat,
                )
            if value is None:
                return ""
            return str(value)
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
            return _COLUMNS[section][1]
        return str(section + 1)

    def row_at(self, row: int) -> ListeGeneraleRow | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
