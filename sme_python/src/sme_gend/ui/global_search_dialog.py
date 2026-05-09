"""Recherche globale multi-vues — Ctrl+K."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings


class GlobalSearchDialog(QDialog):
    """Recherche un nom ou matricule dans toute la liste générale.

    navigate_fn(term) : appelée quand l'utilisateur valide un résultat ;
    doit naviguer vers la liste générale et y appliquer le filtre.
    """

    _DEBOUNCE_MS = 350

    def __init__(
        self,
        settings: Settings,
        navigate_fn: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._navigate_fn = navigate_fn
        self._rows: list[dict] = []

        self.setWindowTitle("Recherche globale — SME")
        self.setMinimumSize(720, 480)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )

        lbl = QLabel(
            "Recherchez un personnel par nom, matricule, grade ou structure.\n"
            "Double-cliquez sur un résultat pour l'afficher dans la liste générale."
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

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Grade", "Noms & prénoms", "Structure", "Mat. militaire", "Mat. solde"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_activate)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_go = bb.addButton("Aller à ce personnel", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_go.clicked.connect(self._on_activate)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(lbl)
        lay.addLayout(top_row)
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
        self._timer.start(self._DEBOUNCE_MS)

    def _do_search(self) -> None:
        term = self._search.text().strip()
        if len(term) < 2:
            self._table.setRowCount(0)
            self._lbl_count.setText("—")
            self._rows = []
            return

        self._lbl_count.setText("Recherche…")
        from sme_gend.ui.bg_task import run_with_progress

        settings = self._settings
        needle = term.upper()

        def do_fetch():
            from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
            all_rows = ListeGeneraleRepository(settings).fetch_all()
            results = []
            for r in all_rows:
                hay = " ".join(filter(None, [
                    r.noms, r.grade, r.structure,
                    r.matricule_militaire, r.matricule_solde,
                    r.fonction, r.position_admin,
                ])).upper()
                if needle in hay:
                    results.append(r)
            return results

        rows = run_with_progress(
            self, "Recherche", f"Recherche de « {term} »…", do_fetch,
            minimum_duration=200,
        )
        if rows is None:
            self._lbl_count.setText("Erreur")
            return

        self._rows = rows
        self._fill_table(rows)
        self._lbl_count.setText(f"{len(rows)} résultat(s)")

    def _fill_table(self, rows) -> None:
        self._table.setRowCount(0)
        for r in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)

            def _cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text or "")
                it.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                return it

            self._table.setItem(row, 0, _cell(r.grade or ""))
            self._table.setItem(row, 1, _cell(r.noms or ""))
            self._table.setItem(row, 2, _cell(r.structure or ""))
            self._table.setItem(row, 3, _cell(r.matricule_militaire or ""))
            self._table.setItem(row, 4, _cell(r.matricule_solde or ""))

    def _on_activate(self) -> None:
        term = self._search.text().strip()
        if not term:
            return
        self._navigate_fn(term)
        self.accept()
