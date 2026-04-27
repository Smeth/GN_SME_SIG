"""Archives locales créées lors des clôtures mensuelles SME (snapshots JSON / SQLite)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.sme_month_closure import sme_archives_dir

_ROLE_PATH = Qt.ItemDataRole.UserRole


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} Ko"
    return f"{n / (1024 * 1024):.1f} Mo"


class ArchivesView(QWidget):
    """Liste des fichiers dans ``%LOCALAPPDATA%\\SME_Gendarmerie\\sme_archives``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = sme_archives_dir().resolve()
        self._path_label = QLabel(
            f"<b>Dossier</b> : <code>{root}</code><br/>"
            "Les clôtures copient ici la mise en page liste (JSON) et, si configuré, le fichier SQLite du bundle. "
            "Double-cliquez sur une ligne pour ouvrir le fichier avec l’application par défaut de Windows."
        )
        self._path_label.setWordWrap(True)
        self._path_label.setTextFormat(Qt.TextFormat.RichText)
        self._path_label.setObjectName("dashSubtitle")

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Fichier", "Taille", "Modifié le"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(lambda _idx: self._open_selected())

        btn_refresh = QPushButton("Actualiser la liste")
        btn_refresh.setObjectName("dashRefresh")
        btn_refresh.clicked.connect(self.refresh_list)

        btn_folder = QPushButton("Ouvrir le dossier dans l’explorateur")
        btn_folder.clicked.connect(self._open_folder)

        btn_open = QPushButton("Ouvrir le fichier sélectionné")
        btn_open.clicked.connect(self._open_selected)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(btn_refresh)
        row.addWidget(btn_folder)
        row.addWidget(btn_open)
        row.addStretch()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        lay.addWidget(self._path_label)
        lay.addLayout(row)
        lay.addWidget(self._table, stretch=1)

        self.refresh_list()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_list()

    def refresh_list(self) -> None:
        d = sme_archives_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            raw = [p for p in d.iterdir() if p.is_file()]
        except OSError as e:
            QMessageBox.warning(self, "Archives", str(e))
            raw = []

        entries: list[tuple[Path, int, float]] = []
        for p in raw:
            try:
                st = p.stat()
                entries.append((p, st.st_size, st.st_mtime))
            except OSError:
                continue
        entries.sort(key=lambda t: t[2], reverse=True)

        self._table.setRowCount(len(entries))
        for row, (p, size, mtime) in enumerate(entries):
            it0 = QTableWidgetItem(p.name)
            it0.setData(_ROLE_PATH, str(p.resolve()))
            it0.setToolTip(str(p.resolve()))
            self._table.setItem(row, 0, it0)
            self._table.setItem(row, 1, QTableWidgetItem(_format_size(size)))
            mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            self._table.setItem(row, 2, QTableWidgetItem(mod))

    def _selected_path(self) -> Path | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        it = self._table.item(r, 0)
        if it is None:
            return None
        s = it.data(_ROLE_PATH)
        return Path(s) if s else None

    def _open_folder(self) -> None:
        root = sme_archives_dir().resolve()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))):
            QMessageBox.warning(self, "Archives", f"Impossible d’ouvrir le dossier :\n{root}")

    def _open_selected(self) -> None:
        p = self._selected_path()
        if p is None or not p.is_file():
            QMessageBox.information(
                self,
                "Archives",
                "Sélectionnez un fichier dans le tableau.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))):
            QMessageBox.warning(self, "Archives", f"Impossible d’ouvrir :\n{p}")
