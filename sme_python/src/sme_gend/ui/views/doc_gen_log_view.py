"""Historique des documents générés — attestations, soit transmis, exports ZIP/PDF."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
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


def _fmt_ts(ts_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_utc).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return ts_utc or "—"


def _fmt_type(doc_type: str, fmt: str) -> str:
    labels = {
        "attestation":       "Attestation",
        "attestation_batch": "Attestations groupées",
        "soit_transmis":     "Soit transmis",
        "zip":               "Archive ZIP",
    }
    return f"{labels.get(doc_type, doc_type)} ({fmt.upper()})"


class DocGenLogView(QWidget):
    """Vue de l'historique des documents générés."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Historique des documents générés")
        title.setObjectName("dashTitle")

        subtitle = QLabel(
            "Chaque génération d'attestation, de soit transmis ou d'export ZIP/PDF est journalisée ici."
        )
        subtitle.setObjectName("dashSubtitle")
        subtitle.setWordWrap(True)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Date & heure", "Type", "Personnes", "Nombre", "Fichier"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        btn_refresh = QPushButton("Actualiser")
        btn_refresh.clicked.connect(self.refresh)

        btn_clear = QPushButton("Effacer l'historique…")
        btn_clear.clicked.connect(self._on_clear)

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(btn_refresh)
        bar.addWidget(btn_clear)
        bar.addStretch()
        bar.addWidget(self._lbl_count)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)

        self.refresh()

    def refresh(self) -> None:
        from sme_gend.services.doc_gen_log import read_doc_log

        entries = read_doc_log(max_entries=500)
        self._table.setRowCount(0)
        for entry in entries:
            row = self._table.rowCount()
            self._table.insertRow(row)

            ts    = entry.get("ts_utc", "")
            dtype = entry.get("doc_type", "")
            fmt   = entry.get("format", "")
            count = entry.get("count", 1)
            persons = entry.get("persons", [])
            dest   = entry.get("dest", "")

            def _cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                return it

            self._table.setItem(row, 0, _cell(_fmt_ts(ts)))
            self._table.setItem(row, 1, _cell(_fmt_type(dtype, fmt)))
            self._table.setItem(row, 2, _cell(", ".join(persons) if persons else "—"))
            self._table.setItem(row, 3, _cell(str(count)))
            self._table.setItem(row, 4, _cell(dest or "—"))

        n = len(entries)
        self._lbl_count.setText(f"{n} entrée(s)")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not event.spontaneous():
            self.refresh()

    def _on_clear(self) -> None:
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        from sme_gend.services.doc_gen_log import clear_doc_log

        if not confirm_yes_no(
            self, "Effacer l'historique",
            "Supprimer toutes les entrées du journal de génération ?",
            default_no=True,
        ):
            return
        clear_doc_log()
        self.refresh()
        QMessageBox.information(self, "Historique", "Journal effacé.")
