"""Catalogue des archives de clôture, consulté sans modifier les fichiers."""
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget)
from sme_gend.services.archive_catalog import ARCHIVE_TYPES, archive_month, scan_archive_directory
from sme_gend.config.user_paths import resolved_sme_archives_directory as sme_archives_dir
from sme_gend.services.search_text import search_values
from sme_gend.services.auth_guard import session_has_permission, require_permission, session_has_admin_role
from sme_gend.services.workflow_permissions import session_can_perform_action
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.view_lifecycle import ViewDataState

_ROLE_PATH = Qt.ItemDataRole.UserRole
_extract_mois_from_name = archive_month
_MONTHS = ("Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre")


def _fmt_mois(month):
    if month and archive_month(month) == month:
        return f"{_MONTHS[int(month[5:7])-1]} {month[:4]}"
    return month or "Mois non identifié"


def _format_size(size):
    return f"{size} o" if size < 1024 else (f"{size/1024:.1f} Ko" if size < 1024**2 else f"{size/1024**2:.1f} Mo")


class ArchivesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_mois, self._all_entries = None, []
        self._view_data = ViewDataState()
        self._generation, self._busy = 0, False
        self._load_error = ""
        self._catalog_notice = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Fichiers d’archive")
        title.setObjectName("dashPageTitle")
        layout.addWidget(title)
        hint = QLabel("Retrouvez les fichiers produits lors des clôtures. Une mise en page seule ne sauvegarde pas les effectifs.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._path_label, self._filter_label = QLabel(), QLabel()
        self._path_label.setWordWrap(True)
        self._path_label.setTextFormat(Qt.TextFormat.PlainText)
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._path_label)
        layout.addWidget(self._filter_label)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher un fichier, un mois ou un type…")
        self._search.setClearButtonEnabled(True)
        self._month_filter, self._type_filter = QComboBox(), QComboBox()
        self._month_filter.addItem("Tous les mois", None)
        self._type_filter.addItem("Tous les types", "")
        for label in dict.fromkeys(ARCHIVE_TYPES.values()):
            self._type_filter.addItem(label, label)
        row = QHBoxLayout()
        for widget in (self._search, self._month_filter, self._type_filter):
            row.addWidget(widget, 1 if widget is self._search else 0)
        self._refresh = QPushButton("Actualiser")
        self._refresh.clicked.connect(self.refresh_list)
        row.addWidget(self._refresh)
        monthly = QPushButton("Transférer une archive mensuelle…")
        self._monthly_button = monthly
        monthly.clicked.connect(lambda:self._monthly_transfer())
        row.addWidget(monthly)
        layout.addLayout(row)
        self._status = QLabel("Archives non chargées. Ouvrez cet onglet pour les consulter.")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Fichier", "Mois déduit du nom", "Type non vérifié", "Taille", "Modifié le"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        self._table.itemSelectionChanged.connect(self._buttons)
        self._table.cellDoubleClicked.connect(lambda *_: self._open_selected())
        layout.addWidget(self._table, 1)
        actions = QHBoxLayout()
        self._open, self._import = QPushButton("Ouvrir le fichier"), QPushButton("Restaurer une période Access…")
        self._open.clicked.connect(self._open_selected)
        self._import.clicked.connect(self._on_import_liste_generale)
        folder = QPushButton("Ouvrir le dossier")
        folder.clicked.connect(self._open_folder)
        for widget in (self._open, self._import, folder):
            actions.addWidget(widget)
        self._verify = QPushButton("Vérifier le fichier…")
        self._verify.clicked.connect(self._verify_selected)
        actions.addWidget(self._verify)
        actions.addStretch()
        maintenance = QToolButton()
        maintenance.setText("Maintenance…")
        maintenance.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(maintenance)
        self._rebuild = menu.addAction("Reconstruire les historiques des dossiers…", self._on_rebuild_dossiers)
        self._empty = menu.addAction("Retirer les imports / vider un mois…", self._on_undo_month_import)
        maintenance.setMenu(menu)
        actions.addWidget(maintenance)
        layout.addLayout(actions)
        self._search.textChanged.connect(self._fill_table_rows)
        self._month_filter.currentIndexChanged.connect(self._fill_table_rows)
        self._type_filter.currentIndexChanged.connect(self._fill_table_rows)
        self._buttons()

    def mark_dirty(self):
        self._generation += 1
        self._busy = False
        self._view_data.mark_stale()
        self._all_entries = []
        self._fill_table_rows()

    mark_view_stale = mark_dirty

    def on_page_activated(self):
        if self._view_data.needs_load():
            self.refresh_list()
        self._buttons()

    def set_active_mois(self, month):
        self._active_mois = (month or "").strip() or None
        self._filter_label.setText(f"Mois consulté dans l’application : {_fmt_mois(month)}" if month else "")

    def refresh_list(self):
        if self._busy or not require_permission(self, "system.archives"):
            return
        self._generation += 1
        generation, directory = self._generation, sme_archives_dir().resolve()
        self._busy = True
        self._path_label.setText(f"Dossier : {directory}")
        self._status.setText("Lecture des fichiers d’archive…")
        self._buttons()

        def loaded(catalog):
            if generation != self._generation:
                return
            self._busy = False
            if not session_has_permission("system.archives"):
                self._all_entries = []
                self._fill_table_rows()
                self._status.setText("Consultation non autorisée.")
                return
            self._all_entries = list(catalog.entries)
            self._load_error = ""
            self._catalog_notice = ("Aucun dossier d’archive à cet emplacement. Il sera créé lors d’un archivage."
                                    if catalog.missing else f"{catalog.unreadable} fichier(s) illisible(s)." if catalog.unreadable else "")
            selected = self._month_filter.currentData()
            self._month_filter.blockSignals(True)
            self._month_filter.clear()
            self._month_filter.addItem("Tous les mois", None)
            for month in sorted({e[1] for e in catalog.entries if e[1]}, reverse=True):
                self._month_filter.addItem(_fmt_mois(month), month)
            self._month_filter.addItem("Mois non identifié", "unknown")
            self._month_filter.setCurrentIndex(max(0, self._month_filter.findData(selected)))
            self._month_filter.blockSignals(False)
            self._view_data.finish_load()
            self._fill_table_rows()
            self._buttons()

        def failed(error):
            if generation == self._generation:
                self._busy = False
                self._view_data.fail_load()
                self._load_error = "Lecture impossible. Les fichiers précédents ne sont pas actualisés. Vérifiez l’accès au dossier puis réessayez."
                self._status.setText(self._load_error)
                self._buttons()
        run_in_background(self, lambda: scan_archive_directory(directory), loaded, on_error=failed)

    def _fill_table_rows(self, *_):
        selected = self._selected_path()
        month, kind = self._month_filter.currentData(), self._type_filter.currentData()
        entries = [e for e in self._all_entries
                   if (month is None or (month == "unknown" and e[1] is None) or e[1] == month)
                   and (not kind or ARCHIVE_TYPES[e[0].suffix.lower()] == kind)
                   and search_values(self._search.text(), e[0].name, e[1], ARCHIVE_TYPES[e[0].suffix.lower()], _fmt_mois(e[1]))]
        self._table.setRowCount(len(entries))
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        for index, (path, month, size, modified) in enumerate(entries):
            values = (path.name, _fmt_mois(month), ARCHIVE_TYPES[path.suffix.lower()], _format_size(size), datetime.fromtimestamp(modified).strftime("%d/%m/%Y %H:%M"))
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(str(path) if col == 0 else text)
                if col == 0:
                    item.setData(_ROLE_PATH, str(path))
                self._table.setItem(index, col, item)
            if path == selected:
                self._table.selectRow(index)
        self._status.setText(f"{len(entries)} fichier(s) affiché(s) sur {len(self._all_entries)}." if entries else
                             ("Aucun fichier ne correspond aux filtres." if self._all_entries else "Aucun fichier d’archive disponible à cet emplacement."))
        if self._load_error:
            self._status.setText(self._load_error)
        elif self._busy:
            self._status.setText("Lecture en cours. Les résultats précédents ne sont pas encore actualisés.")
        elif self._catalog_notice:
            self._status.setText(self._status.text() + " " + self._catalog_notice)
        self._buttons()

    def _selected_path(self):
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return Path(item.data(_ROLE_PATH)) if item is not None else None

    def _monthly_transfer(self, *, initial_source=None):
        if not require_permission(self,"system.archives"):
            return
        from sme_gend.config.settings import get_settings
        from sme_gend.ui.month_transfer_dialog import MonthTransferDialog
        MonthTransferDialog(self,get_settings(),initial_source=initial_source).exec()

    def _buttons(self):
        path = self._selected_path()
        suffix = path.suffix.lower() if path else ""
        self._open.setText("Accéder à la restauration…" if suffix == ".zip" else "Choisir un mois à restaurer…" if suffix == ".sql" else "Ouvrir le fichier")
        self._refresh.setEnabled(not self._busy)
        self._monthly_button.setEnabled(session_has_permission("system.archives") and
            (session_has_permission("system.backup") or session_has_admin_role() and session_can_perform_action("workflow.import_archive")))
        self._verify.setEnabled(path is not None and not self._busy and not self._load_error and session_has_permission("system.archives"))
        self._open.setEnabled(path is not None and not self._busy and not self._load_error and session_has_permission("system.archives"))
        self._import.setEnabled(bool(path and path.suffix.lower() in (".mdb", ".accdb")) and not self._busy and not self._load_error and session_can_perform_action("workflow.import_archive"))
        self._rebuild.setEnabled(session_can_perform_action("workflow.import_archive"))
        from sme_gend.services.empty_sme_month import can_empty_month
        self._empty.setEnabled(can_empty_month())

    def _on_import_liste_generale(self):
        if self._busy or self._load_error or not session_can_perform_action("workflow.import_archive"):
            return
        from sme_gend.config.settings import get_settings
        from sme_gend.ui.import_liste_generale_wizard import run_import_liste_generale_from_access
        path = self._selected_path()
        if path and path.is_file() and path.suffix.lower() in (".mdb", ".accdb"):
            if run_import_liste_generale_from_access(self, get_settings(), initial_mdb=path, restore_period=True):
                self.mark_dirty()
                self.refresh_list()
                if hasattr(self.window(), "refresh_for_month_change"):
                    self.window().refresh_for_month_change()

    def _on_rebuild_dossiers(self):
        if not session_can_perform_action("workflow.import_archive"):
            return
        from sme_gend.config.settings import get_settings
        from sme_gend.ui.dossier_backfill_dialog import run_backfill_dossier_dialog
        run_backfill_dossier_dialog(self, get_settings())

    def _on_undo_month_import(self):
        from sme_gend.services.empty_sme_month import can_empty_month
        if not can_empty_month():
            return
        from sme_gend.config.settings import get_settings
        from sme_gend.ui.empty_month_dialog import run_empty_month_dialog
        if run_empty_month_dialog(self, get_settings()):
            self.mark_dirty()
            self.refresh_list()

    def _open_folder(self):
        if not require_permission(self, "system.archives"):
            return
        directory = sme_archives_dir().resolve()
        if not directory.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            QMessageBox.warning(self, "Archives", "Le dossier d’archive n’est pas accessible.")

    def _verify_selected(self):
        if self._busy or self._load_error or not require_permission(self, "system.archives"):
            return
        path = self._selected_path()
        if path is None:
            return
        from sme_gend.services.archive_catalog import inspect_archive
        generation = self._generation
        result = run_with_progress(self, "Vérification de l’archive", "Lecture du fichier et contrôle d’intégrité…",
                                   lambda: inspect_archive(path))
        if result is not None and generation == self._generation and session_has_permission("system.archives"):
            QMessageBox.information(self, "Résultat de vérification", result)

    def _open_selected(self):
        if self._busy or self._load_error or not require_permission(self, "system.archives"):
            return
        path = self._selected_path()
        if path is None or not path.is_file():
            QMessageBox.information(self, "Archives", "Sélectionnez un fichier accessible.")
            return
        if path.suffix.lower() == ".zip":
            import zipfile
            try:
                with zipfile.ZipFile(path) as archive:
                    monthly = "month.json" in archive.namelist()
            except (OSError,zipfile.BadZipFile): monthly = False
            if monthly:
                self._monthly_transfer(initial_source=path)
                return
            if not require_permission(self, "system.backup"):
                return
            from sme_gend.ui.navigation_pages import PAGE_BACKUP_RESTORE
            if hasattr(self.window(), "_go_page"):
                self.window()._go_page(PAGE_BACKUP_RESTORE)
                restore = getattr(self.window(), "_backup_restore_view", None)
                if restore is not None:
                    restore.select_archive_for_restore(path)
            else:
                QMessageBox.information(self, "Sauvegarde", "Ouvrez Sauvegarde & restauration et sélectionnez cette archive pour examiner les contrôles de restauration.")
            return
        if path.suffix.lower() == ".sql":
            self._monthly_transfer(initial_source=path)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Archives", "L’ouverture du fichier a échoué. Vérifiez l’application associée à ce type de fichier.")
