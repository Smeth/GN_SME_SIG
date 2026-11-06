"""Recherche personnel — Ctrl+K (effectif + hors effectif)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.branding import APP_NAME
from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.personnel_lookup import PersonnelSearchHit
from sme_gend.models.structure import StructureRow
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
)
from sme_gend.services.personnel_list_order import GradeSortHelper


class GlobalSearchDialog(QDialog):
    """Recherche un nom ou matricule (effectif actif + hors effectif)."""

    _DEBOUNCE_MS = 350

    def __init__(
        self,
        settings: Settings,
        navigate_fn: Callable[[str], None],
        open_hit_fn: Callable[[PersonnelSearchHit], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._navigate_fn = navigate_fn
        self._open_hit_fn = open_hit_fn
        self._hits: list[PersonnelSearchHit] = []
        self._search_generation = 0
        self._search_worker = None
        self._grade_sort = GradeSortHelper()
        self._structure_rows: list[StructureRow] = []

        self.setWindowTitle(f"Recherche personnel — {APP_NAME}")
        self.setMinimumSize(820, 500)
        self.resize(1100, 600)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )

        lbl = QLabel(
            "Recherchez un personnel par nom, matricule, grade ou structure.\n"
            "Effectif actif : ouverture dans la liste. "
            "Hors effectif (décès, sortie) : dossier historique en lecture seule."
        )
        lbl.setObjectName("dashSubtitle")
        lbl.setWordWrap(True)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Nom, matricule, grade, structure…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumHeight(34)
        self._search.textChanged.connect(self._schedule_search)

        self._lbl_count = QLabel("—")
        self._lbl_count.setObjectName("dashSubtitle")

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(self._search, stretch=1)
        top_row.addWidget(self._lbl_count)

        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["Statut", "Grade", "Noms & prénoms", "Structure", "Mat. militaire", "Sortie",
             "Fiche", "Naissance", "Mat. solde"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(3, 140)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        for column in (6, 7, 8):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        hdr.moveSection(hdr.visualIndex(6), 0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._on_activate)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        self._btn_go = bb.addButton("Ouvrir", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_go.clicked.connect(self._on_activate)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(lbl)
        lay.addLayout(top_row)
        self._consultation_notice = QLabel()
        self._consultation_notice.setWordWrap(True)
        lay.addWidget(self._consultation_notice)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(bb)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_search)

    def show_and_focus(self) -> None:
        self.show()
        self.raise_()
        self._search.setFocus()
        self._search.selectAll()

    def _schedule_search(self) -> None:
        self._search_generation += 1
        self._hits = []
        self._table.setRowCount(0)
        self._lbl_count.setText("Recherche…")
        self._timer.start(self._DEBOUNCE_MS)

    def _do_search(self) -> None:
        term = self._search.text().strip()
        generation = self._search_generation
        if len(term) < 2:
            self._table.setRowCount(0)
            self._lbl_count.setText("—")
            self._hits = []
            return

        self._table.setRowCount(0)
        self._hits = []
        self._lbl_count.setText("Recherche…")
        settings = self._settings

        def do_fetch() -> tuple[
            list[PersonnelSearchHit], list[GradeRefRow], list[StructureRow]
        ]:
            from sme_gend.services.personnel_lookup_service import PersonnelLookupService
            from sme_gend.repositories.grade_repository import GradeRepository
            from sme_gend.repositories.structure_repository import StructureRepository

            hits = PersonnelLookupService(settings).search_extended(term)
            grades = GradeRepository(settings).fetch_all()
            structures = StructureRepository(settings).fetch_all()
            return hits, grades, structures

        def done(
            result: tuple[
                list[PersonnelSearchHit], list[GradeRefRow], list[StructureRow]
            ]
        ) -> None:
            if generation != self._search_generation:
                return
            hits, grades, self._structure_rows = result
            self._hits = hits
            self._grade_sort = GradeSortHelper(grades)
            self._fill_table(hits)
            n_act = sum(1 for h in hits if h.is_active)
            n_hist = len(hits) - n_act
            self._lbl_count.setText(
                f"{len(hits)} résultat(s) — {n_act} effectif, {n_hist} hors effectif"
            )
            from sme_gend.services.consultation_audit import queue_consultation
            queue_consultation(self, settings, action='search_results_consulted',
                section='Recherche globale', result_count=len(hits),
                on_error=lambda _exc: self._consultation_notice.setText("Résultats affichés ; traçabilité indisponible. Vérifiez MySQL ou la maintenance."))

        def failed(exc: BaseException) -> None:
            if generation != self._search_generation:
                return
            self._lbl_count.setText("Erreur")
            from sme_gend.services.mysql_health import format_mysql_error_for_user

            QMessageBox.critical(self, "Recherche", format_mysql_error_for_user(exc))

        from sme_gend.ui.bg_task import run_in_background

        self._search_worker = run_in_background(self, do_fetch, done, on_error=failed)

    def _fill_table(self, hits: list[PersonnelSearchHit]) -> None:
        from sme_gend.services.structure_label_format import format_structure_display

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for hit in hits:
            r = hit.row
            row = self._table.rowCount()
            self._table.insertRow(row)

            def _cell(text: str, *, color: QColor | None = None) -> QTableWidgetItem:
                it = QTableWidgetItem(text or "")
                it.setToolTip(text or "")
                it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if color is not None:
                    it.setForeground(color)
                return it

            status_item = _cell(hit.status_label())
            status_item.setData(Qt.ItemDataRole.UserRole, hit)
            if not hit.is_active:
                status_item.setForeground(QColor("#e65100"))
            self._table.setItem(row, 0, status_item)
            self._table.setItem(row, 1, GradeSortTableWidgetItem(r.grade, self._grade_sort))
            self._table.setItem(row, 2, _cell(r.noms or ""))
            self._table.setItem(row, 3, _cell(
                format_structure_display(r.structure, self._structure_rows) or ""
            ))
            self._table.setItem(row, 4, _cell(r.matricule_militaire or ""))
            sortie_item = _cell(hit.sortie_label())
            if hit.sortie and "DECES" in (hit.sortie.motif + hit.sortie.observ).upper():
                sortie_item.setForeground(QColor("#c62828"))
            self._table.setItem(row, 5, sortie_item)
            self._table.setItem(row, 6, _cell(r.satellite_join_key()))
            self._table.setItem(row, 7, _cell(r.date_naissance.strftime("%d/%m/%Y") if r.date_naissance else "—"))
            self._table.setItem(row, 8, _cell(r.matricule_solde or "—"))
        self._table.setSortingEnabled(True)

    def _selected_hit(self) -> PersonnelSearchHit | None:
        ridx = self._table.currentRow()
        item = self._table.item(ridx, 0) if ridx >= 0 else None
        hit = item.data(Qt.ItemDataRole.UserRole) if item else None
        return hit if isinstance(hit, PersonnelSearchHit) else None

    def _on_activate(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            term = self._search.text().strip()
            if not term:
                return
            self._navigate_fn(term)
            self.accept()
            return
        if self._open_hit_fn is not None:
            self._open_hit_fn(hit)
        elif hit.is_active:
            self._navigate_fn(hit.row.noms or self._search.text().strip())
        self.accept()
