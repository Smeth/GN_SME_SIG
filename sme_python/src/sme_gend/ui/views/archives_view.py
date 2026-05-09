"""Archives locales créées lors des clôtures mensuelles SME (snapshots JSON / SQLite)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)

_YYYY_MM_RE = re.compile(r"(\d{4})[-_](\d{2})")


def _fmt_mois(mois: str) -> str:
    try:
        y, m = mois.split("-")
        return f"{_MOIS_FR[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return mois


def _extract_mois_from_name(name: str) -> str | None:
    """Extrait le premier token YYYY-MM ou YYYY_MM d’un nom de fichier."""
    m = _YYYY_MM_RE.search(name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


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
        self._active_mois: str | None = None
        self._all_entries: list[tuple[Path, str | None, int, float]] = []
        root = sme_archives_dir().resolve()
        self._path_label = QLabel(
            f"<b>Dossier</b> : <code>{root}</code><br/>"
            "Les clôtures copient ici la mise en page liste (JSON) et, si configuré, le fichier SQLite du bundle. "
            "Double-cliquez sur une ligne pour ouvrir le fichier avec l’application par défaut de Windows."
        )
        self._path_label.setWordWrap(True)
        self._path_label.setTextFormat(Qt.TextFormat.RichText)
        self._path_label.setObjectName("dashSubtitle")

        self._filter_label = QLabel()
        self._filter_label.setObjectName("dashSubtitle")
        self._filter_label.setTextFormat(Qt.TextFormat.RichText)
        self._filter_label.hide()

        self._month_filter = QComboBox()
        self._month_filter.setMinimumWidth(180)
        self._month_filter.setToolTip("Filtrer les archives par mois de clôture")
        self._month_filter.currentIndexChanged.connect(self._on_month_filter_changed)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Fichier", "Mois SME", "Taille", "Modifié le"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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

        btn_rebuild = QPushButton("Reconstruire l’historique…")
        btn_rebuild.setToolTip(
            "Analyse les archives consécutives disponibles et génère automatiquement\n"
            "l’historique parcours/carrière pour chaque personne dont les données\n"
            "ont changé entre deux mois — sans ré-importer depuis Access."
        )
        btn_rebuild.clicked.connect(self._on_rebuild_history)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Mois :"))
        filter_row.addWidget(self._month_filter)
        filter_row.addStretch()

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(btn_refresh)
        row.addWidget(btn_folder)
        row.addWidget(btn_open)
        row.addWidget(btn_rebuild)
        row.addStretch()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        lay.addWidget(self._path_label)
        lay.addWidget(self._filter_label)
        lay.addLayout(filter_row)
        lay.addLayout(row)
        lay.addWidget(self._table, stretch=1)

        self.refresh_list()

    def set_active_mois(self, mois: str | None) -> None:
        self._active_mois = (mois or "").strip() or None
        if self._active_mois:
            self._filter_label.setText(
                f"▶ Archives surlignées pour <b>{_fmt_mois(self._active_mois)}</b>"
            )
            self._filter_label.show()
        else:
            self._filter_label.hide()
        self.refresh_list()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():
            return
        self.refresh_list()

    def _on_month_filter_changed(self) -> None:
        self._fill_table_rows()

    def refresh_list(self) -> None:
        d = sme_archives_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            raw = [p for p in d.iterdir() if p.is_file()]
        except OSError as e:
            QMessageBox.warning(self, "Archives", str(e))
            raw = []

        self._all_entries: list[tuple[Path, str | None, int, float]] = []
        for p in raw:
            try:
                st = p.stat()
                mois = _extract_mois_from_name(p.name)
                self._all_entries.append((p, mois, st.st_size, st.st_mtime))
            except OSError:
                continue
        self._all_entries.sort(key=lambda t: t[3], reverse=True)

        # Update combo with discovered months (preserve selection)
        prev_mois = self._month_filter.currentData()
        self._month_filter.blockSignals(True)
        self._month_filter.clear()
        self._month_filter.addItem("(Tous les mois)", None)
        seen_months: list[str] = []
        for _, mois, _, _ in self._all_entries:
            if mois and mois not in seen_months:
                seen_months.append(mois)
        seen_months.sort(reverse=True)
        for m in seen_months:
            self._month_filter.addItem(_fmt_mois(m), m)
        # Restore selection: prefer active mois, then previous, else (Tous)
        select_mois = self._active_mois or prev_mois
        if select_mois:
            for i in range(self._month_filter.count()):
                if self._month_filter.itemData(i) == select_mois:
                    self._month_filter.setCurrentIndex(i)
                    break
        self._month_filter.blockSignals(False)

        self._fill_table_rows()

    def _fill_table_rows(self) -> None:
        selected_mois = self._month_filter.currentData()

        entries = self._all_entries if selected_mois is None else [
            e for e in self._all_entries if e[1] == selected_mois
        ]

        # Motif de surlignage : le mois actif sous forme YYYY-MM dans le nom de fichier
        hl_bg = QBrush(QColor("#e8f5e9"))
        hl_fg = QBrush(QColor("#1b5e20"))

        self._table.setRowCount(len(entries))
        for row, (p, mois, size, mtime) in enumerate(entries):
            it0 = QTableWidgetItem(p.name)
            it0.setData(_ROLE_PATH, str(p.resolve()))
            it0.setToolTip(str(p.resolve()))
            self._table.setItem(row, 0, it0)
            mois_lbl = _fmt_mois(mois) if mois else "—"
            self._table.setItem(row, 1, QTableWidgetItem(mois_lbl))
            self._table.setItem(row, 2, QTableWidgetItem(_format_size(size)))
            mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            self._table.setItem(row, 3, QTableWidgetItem(mod))
            if self._active_mois and mois == self._active_mois:
                for col in range(4):
                    item = self._table.item(row, col)
                    if item:
                        item.setBackground(hl_bg)
                        item.setForeground(hl_fg)

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

    def _on_rebuild_history(self) -> None:
        from sme_gend.services.personnel_retroactive_diff import (
            run_bulk_archive_history,
        )
        from sme_gend.ui.bg_task import run_with_progress

        d = sme_archives_dir()
        # Pré-scan rapide pour informer l'utilisateur
        SUPPORTED = {".sqlite", ".db", ".mdb", ".accdb"}
        try:
            candidates = [
                p for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED
                and _extract_mois_from_name(p.name)
            ]
        except OSError:
            candidates = []

        n_archives = len(candidates)
        if n_archives < 2:
            QMessageBox.information(
                self,
                "Reconstruire l'historique",
                "Il faut au moins 2 archives avec YYYY-MM dans leur nom pour générer\n"
                "un historique de transition.\n\n"
                f"Archives trouvées : {n_archives}\n"
                f"Dossier : {d}",
            )
            return

        reply = QMessageBox.question(
            self,
            "Reconstruire l'historique depuis les archives",
            f"<b>{n_archives} archive(s)</b> trouvée(s) dans :<br>"
            f"<code>{d}</code><br><br>"
            "Cette opération compare chaque paire de mois consécutifs et génère "
            "l'historique <b>Parcours professionnel</b> pour les personnes dont "
            "les données ont changé (grade, structure, fonction, position admin…).<br><br>"
            "• Les mois déjà reconstruits sont automatiquement ignorés.<br>"
            "• Les archives et la base courante ne sont pas modifiées.<br><br>"
            "Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def _work():
            return run_bulk_archive_history(
                d,
                career_only=True,
                dry_run=False,
                skip_existing=True,
            )

        result = run_with_progress(
            self,
            "Reconstruction de l'historique",
            "Analyse des archives en cours…",
            _work,
        )
        if result is None:
            return

        lines = result.summary_lines()
        QMessageBox.information(
            self,
            "Historique reconstruit",
            "\n".join(lines),
        )

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
