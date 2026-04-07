"""Liste générale — grille + filtres (ancienne fenêtre principale)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog
from sme_gend.ui.personnel_table_model import PersonnelTableModel


class PersonnelListView(QWidget):
    """Consultation de la liste générale Access."""

    def __init__(
        self,
        service: PersonnelService,
        referentiel: ReferentielService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._referentiel = referentiel
        self._all_rows: list[ListeGeneraleRow] = []
        self._filter_text = ""

        self._model = PersonnelTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSortingEnabled(False)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrer (nom, grade, structure…) — client")
        self._search.textChanged.connect(self._on_filter_changed)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Recherche :"))
        search_row.addWidget(self._search, stretch=1)

        bar = QToolBar("Liste")
        bar.setMovable(False)
        act_refresh = QAction("Actualiser", self)
        act_refresh.triggered.connect(self._refresh_all)
        bar.addAction(act_refresh)
        act_all = QAction("Tout afficher", self)
        act_all.triggered.connect(self._show_all)
        bar.addAction(act_all)
        act_pres = QAction("Présents seulement", self)
        act_pres.triggered.connect(self._show_presents)
        bar.addAction(act_pres)
        act_edit = QAction("Modifier la ligne…", self)
        act_edit.triggered.connect(self._edit_current)
        bar.addAction(act_edit)

        self._table.doubleClicked.connect(self._edit_current)

        layout = QVBoxLayout(self)
        layout.addWidget(bar)
        layout.addLayout(search_row)
        layout.addWidget(self._table, stretch=1)

        self._refresh_all()

    def _refresh_all(self) -> None:
        try:
            self._all_rows = self._service.liste_complete()
        except Exception as e:
            QMessageBox.critical(self, "Erreur base", str(e))
            return
        self._apply_filter()

    def _show_all(self) -> None:
        try:
            self._all_rows = self._service.liste_complete()
        except Exception as e:
            QMessageBox.critical(self, "Erreur base", str(e))
            return
        self._apply_filter()

    def _show_presents(self) -> None:
        try:
            self._all_rows = self._service.liste_presents()
        except Exception as e:
            QMessageBox.critical(self, "Erreur base", str(e))
            return
        self._apply_filter()

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter_text
        if not needle:
            rows = self._all_rows
        else:
            rows = [r for r in self._all_rows if _row_matches(r, needle)]
        self._model.set_rows(rows)

    def _edit_current(self) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        row_obj = self._model.row_at(idx.row())
        if row_obj is None:
            return
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(
                self,
                "Personnel",
                "Ligne introuvable en base (identifiant peut-être obsolète). Actualisez la liste.",
            )
            return
        dlg = PersonnelEditDialog(self, fresh, self._referentiel)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            self._service.update_personnel(fresh, new_row)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        QMessageBox.information(
            self,
            "Personnel",
            "Modifications enregistrées dans Access. "
            "La trace est ajoutée à data/personnel_audit.jsonl.",
        )
        self._refresh_all()


def _row_matches(row: ListeGeneraleRow, needle: str) -> bool:
    parts = [
        row.id,
        row.noms or "",
        row.grade or "",
        row.position_admin or "",
        row.structure or "",
        row.subdivision_arme or "",
        row.fonction or "",
        row.matricule_militaire or "",
    ]
    blob = " ".join(parts).lower()
    return needle in blob
