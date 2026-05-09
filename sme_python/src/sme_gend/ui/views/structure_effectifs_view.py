"""Liste des structures et navigation vers les effectifs d’une structure (liste générale filtrée)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService


class StructureEffectifsView(QWidget):
    """
    Colonne des structures (TabStructure + effectifs dans la liste générale).
    Double-clic ou bouton : ouvre la page Liste générale filtrée sur la structure.
    """

    def __init__(
        self,
        settings: Settings,
        personnel: PersonnelService,
        referentiel: ReferentielService,
        open_liste_generale_for_structure: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._personnel = personnel
        self._referentiel = referentiel
        self._open_liste = open_liste_generale_for_structure

        title = QLabel(
            "<b>Parcourir par structure</b> — liste des codes structure avec effectifs ; "
            "double-clic ou bouton pour ouvrir la <b>liste générale</b> déjà filtrée sur cette structure."
        )
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setObjectName("dashSubtitle")

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Code", "Désignation", "Effectif"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_open_selection)

        self._btn_refresh = QPushButton("Actualiser la liste")
        self._btn_refresh.clicked.connect(self.reload)
        self._btn_open = QPushButton("Ouvrir les effectifs de la structure sélectionnée…")
        self._btn_open.setToolTip("Bascule vers « Liste générale » avec filtre structure.")
        self._btn_open.clicked.connect(self._on_open_selection)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addStretch()
        bar.addWidget(self._btn_open)

        split = QSplitter(Qt.Orientation.Horizontal)
        left_wrap = QWidget()
        lv = QVBoxLayout(left_wrap)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Structures</b> (référentiel + effectifs liste générale)"))
        lv.addWidget(self._table)
        split.addWidget(left_wrap)
        split.setStretchFactor(0, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(title)
        root.addLayout(bar)
        root.addWidget(split, stretch=1)

        self._codes_by_row: list[str] = []
        self._dirty = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._dirty:
            self._dirty = False
            self.reload()

    def mark_dirty(self) -> None:
        self._dirty = True

    def apply_runtime_after_config_change(self) -> None:
        self._settings = self._personnel.settings
        self.reload()

    def reload(self) -> None:
        try:
            structs = self._referentiel.structures()
            counts = self._personnel.count_by_structure_code()
        except Exception as e:
            QMessageBox.critical(self, "Structures", str(e))
            return
        rows_data: list[tuple[str, str, int]] = []
        for s in sorted(structs, key=lambda x: (x.ordre_tri is None, x.ordre_tri or 0, x.code)):
            n = int(counts.get(s.code, 0))
            rows_data.append((s.code, s.designation or "", n))
        sans = counts.get("(Sans structure)", 0)
        if sans:
            rows_data.append(("(Sans structure)", "— personnel sans code structure —", int(sans)))

        self._table.setRowCount(len(rows_data))
        self._codes_by_row = []
        for i, (code, des, n) in enumerate(rows_data):
            self._codes_by_row.append(code)
            self._table.setItem(i, 0, QTableWidgetItem(code))
            self._table.setItem(i, 1, QTableWidgetItem(des))
            itn = QTableWidgetItem(str(n))
            itn.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self._table.setItem(i, 2, itn)
        self._table.resizeColumnsToContents()
        if self._table.rowCount() > 0:
            self._table.selectRow(0)

    def _selected_code(self) -> str | None:
        r = self._table.currentRow()
        if r < 0 or r >= len(self._codes_by_row):
            return None
        return self._codes_by_row[r]

    def _on_open_selection(self) -> None:
        code = self._selected_code()
        if code is None:
            QMessageBox.information(self, "Structures", "Sélectionnez une structure.")
            return
        self._open_liste(code)
