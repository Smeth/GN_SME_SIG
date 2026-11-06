"""Liste des structures et navigation vers les effectifs d’une structure (liste générale filtrée)."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.expandable_help import ExpandableHelp

_DEFAULT_INTRO = (
    "<b>Parcourir par structure</b> — liste des codes structure avec effectifs ; "
    "double-clic ou bouton pour ouvrir la <b>liste générale</b> déjà filtrée sur cette structure."
)


class StructureEffectifsView(QWidget):
    """
    Colonne des structures (ref_structure + effectifs dans la liste générale).
    Double-clic ou bouton : ouvre la page Liste générale filtrée sur la structure.
    """

    def __init__(
        self,
        settings: Settings,
        personnel: PersonnelService,
        referentiel: ReferentielService,
        open_liste_generale_for_structure: Callable[[str], None],
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._personnel = personnel
        self._referentiel = referentiel
        self._open_liste = open_liste_generale_for_structure

        title = QLabel(_DEFAULT_INTRO)
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setObjectName("dashSubtitle")
        self._help = ExpandableHelp(_DEFAULT_INTRO, self)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Code", "Désignation", "Effectif"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._on_open_selection)

        self._btn_refresh = QPushButton("Actualiser la liste")
        self._btn_refresh.clicked.connect(lambda: self.reload(force=True))
        self._btn_open = QPushButton("Ouvrir les effectifs…")
        self._btn_open.setToolTip("Bascule vers « Liste générale » avec filtre structure.")
        self._btn_open.clicked.connect(self._on_open_selection)
        self._btn_print = QPushButton('Imprimer…')
        self._btn_print.setToolTip('Aperçu et impression des structures affichées, dans l’ordre du tableau.')
        self._btn_print.setEnabled(False)
        self._btn_print.clicked.connect(self._on_print)
        self._loaded_mois = None

        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher structure (code, désignation)…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refill_table_filtered)
        self._rows_data: list[tuple[str, str, int]] = []

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._btn_open)
        bar.addWidget(self._btn_print)
        bar.addWidget(self._help.button)

        split = QSplitter(Qt.Orientation.Horizontal)
        left_wrap = QWidget()
        lv = QVBoxLayout(left_wrap)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self._table)
        split.addWidget(left_wrap)
        split.setStretchFactor(0, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)
        root.addWidget(self._help)
        root.addLayout(bar)
        root.addWidget(split, stretch=1)

        self._view_data = ViewDataState(stale=True)
        self._reload_busy = False

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()
        self._btn_print.setEnabled(False)

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def apply_runtime_after_config_change(self) -> None:
        self._settings = self._personnel.settings
        self.mark_dirty()
        if self.isVisible():
            self.reload()

    def reload(self, *, force: bool = False) -> None:
        if force:
            self._view_data.mark_stale()
        if self._reload_busy:
            return
        if not self._view_data.needs_load():
            return
        self._reload_busy = True
        self._btn_print.setEnabled(False)
        self._btn_refresh.setEnabled(False)
        ref = self._referentiel
        pers = self._personnel

        def work():
            from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
            mois = resolve_liste_view_mois(pers.settings)
            structs = ref.structures()
            counts = pers.count_by_structure_code()
            return structs, counts, mois

        def done(result: tuple) -> None:
            self._reload_busy = False
            self._btn_refresh.setEnabled(True)
            structs, counts, self._loaded_mois = result
            self._fill_table(structs, counts)
            self._view_data.finish_load()
            self._sync_print_state()

        def on_err(exc: BaseException) -> None:
            self._reload_busy = False
            self._btn_refresh.setEnabled(True)
            QMessageBox.critical(self, "Structures", str(exc))

        run_in_background(self, work, done, on_error=on_err)

    def _fill_table(self, structs, counts) -> None:
        rows_data: list[tuple[str, str, int]] = []
        for s in sorted(structs, key=lambda x: (x.ordre_tri is None, x.ordre_tri or 0, x.code)):
            n = int(counts.get(s.code, 0))
            rows_data.append((s.code, s.designation or "", n))
        sans = counts.get("(Sans structure)", 0)
        if sans:
            rows_data.append(("(Sans structure)", "— personnel sans code structure —", int(sans)))

        self._rows_data = rows_data
        self._refill_table_filtered()

    def _refill_table_filtered(self) -> None:
        q = normalize_search_text((self._search.text() if hasattr(self, "_search") else "").strip().casefold())
        rows_data = self._rows_data
        if q:
            rows_data = [
                (c, d, n)
                for c, d, n in rows_data
                if q in normalize_search_text(f"{c} {d}".casefold())
            ]
        prev_code = self._selected_code()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows_data))
        target_row = -1
        for i, (code, des, n) in enumerate(rows_data):
            it_code = QTableWidgetItem(code)
            it_code.setData(Qt.ItemDataRole.UserRole, code)
            self._table.setItem(i, 0, it_code)
            self._table.setItem(i, 1, QTableWidgetItem(des))
            itn = QTableWidgetItem()
            itn.setData(Qt.ItemDataRole.DisplayRole, n)
            itn.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 2, itn)
            if code == prev_code:
                target_row = i
        self._table.setSortingEnabled(True)
        self._table.resizeColumnsToContents()
        if target_row < 0 and self._table.rowCount() > 0:
            target_row = 0
        if target_row >= 0:
            self._table.selectRow(target_row)
        self._sync_print_state()

    def _sync_print_state(self):
        from sme_gend.services.auth_guard import session_can_export_documents
        self._btn_print.setEnabled(not self._reload_busy and self._view_data.is_ready()
                                   and bool(self._loaded_mois) and self._table.rowCount() > 0
                                   and session_can_export_documents())

    def _print_rows(self):
        return [(self._table.item(r, 0).text(), self._table.item(r, 1).text(),
                 int(self._table.item(r, 2).data(Qt.ItemDataRole.DisplayRole)))
                for r in range(self._table.rowCount()) if not self._table.isRowHidden(r)]

    def _on_print(self):
        from sme_gend.ui.document_job import require_export
        if not require_export(self):
            return
        if self._reload_busy or not self._view_data.is_ready() or not self._loaded_mois:
            QMessageBox.information(self, 'Impression', 'Actualisez les données du mois avant d’imprimer.')
            return
        rows = self._print_rows()
        if not rows:
            QMessageBox.information(self, 'Impression', 'Aucune structure ne correspond au filtre.')
            return
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter
        from sme_gend.services.structure_effectifs_print import structure_effectifs_html, print_structure_effectifs_document
        doc = QTextDocument()
        doc.setHtml(structure_effectifs_html(rows, mois=self._loaded_mois, search=self._search.text().strip()))
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(f'Effectifs par structure — {self._loaded_mois}')
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                         QPageLayout.Orientation.Portrait, QMarginsF(10, 10, 10, 10),
                                         QPageLayout.Unit.Millimeter))
        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle(f'Effectifs par structure — {self._loaded_mois}')
        preview.resize(1000, 760)
        preview.paintRequested.connect(lambda device: print_structure_effectifs_document(doc, device))
        preview.exec()

    def _selected_code(self) -> str | None:
        r = self._table.currentRow()
        if r < 0:
            return None
        it = self._table.item(r, 0)
        if it is None:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _on_open_selection(self) -> None:
        code = self._selected_code()
        if code is None:
            QMessageBox.information(self, "Structures", "Sélectionnez une structure.")
            return
        self._open_liste(code)
