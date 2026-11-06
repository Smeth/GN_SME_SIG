"""Recherche texte sur lignes liste générale / tableaux."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QWidget

from sme_gend.models.liste_generale import ListeGeneraleRow


def row_matches_search(row: ListeGeneraleRow, needle: str) -> bool:
    from sme_gend.ui.personnel_list_filters import free_text_matches
    return free_text_matches(row, needle)


def make_search_bar(
    parent: QWidget,
    *,
    placeholder: str = "Rechercher…",
    on_change,
) -> tuple[QLineEdit, QWidget]:
    """Barre recherche compacte ; ``on_change`` appelé à chaque frappe (débouncé côté appelant si besoin)."""
    le = QLineEdit(parent)
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.textChanged.connect(on_change)
    wrap = QWidget(parent)
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(le, stretch=1)
    return le, wrap


def fill_table_from_rows(
    table: QTableWidget,
    rows: list[ListeGeneraleRow],
    *,
    columns: list[tuple[str, callable]],
) -> None:
    """columns = [(header, getter(row)), ...]"""
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, (hdr, getter) in enumerate(columns):
            if table.columnCount() <= j:
                table.setColumnCount(len(columns))
                table.setHorizontalHeaderLabels([c[0] for c in columns])
            val = getter(row)
            table.setItem(i, j, QTableWidgetItem("" if val is None else str(val)))
