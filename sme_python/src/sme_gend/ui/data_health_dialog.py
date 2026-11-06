"""Journal des erreurs ETL / import (table etl_erreur)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.etl_erreur_service import count_etl_erreurs, fetch_etl_erreurs


class DataHealthDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Santé des données — journal ETL")
        self.setMinimumSize(820, 420)

        hint = QLabel(
            "Anomalies enregistrées lors des imports et réconciliations "
            "(clés étrangères non résolues, libellés inconnus, etc.). "
            "Corrigez le référentiel ou la source, puis relancez la réconciliation."
        )
        hint.setWordWrap(True)
        hint.setObjectName("dashSubtitle")

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Table", "Clé legacy", "Message", "Lot import"]
        )
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        btn_refresh = QPushButton("Actualiser")
        btn_refresh.clicked.connect(self._reload)

        bar = QHBoxLayout()
        bar.addWidget(self._lbl_count)
        bar.addStretch()
        bar.addWidget(btn_refresh)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(bb)

        self._reload()

    def _reload(self) -> None:
        try:
            rows = fetch_etl_erreurs(self._settings, limit=500)
            total = count_etl_erreurs(self._settings)
        except Exception as exc:
            QMessageBox.critical(self, "Santé des données", str(exc))
            return

        self._lbl_count.setText(
            f"{total} erreur(s) en base — affichage des {len(rows)} plus récentes."
            if total
            else "Aucune erreur ETL enregistrée."
        )
        self._table.setRowCount(0)
        for er in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            vals = (
                er.cree_le or "—",
                er.table_cible or "—",
                er.cle_legacy or "—",
                er.message or "—",
                str(er.import_lot_id) if er.import_lot_id is not None else "—",
            )
            for c, txt in enumerate(vals):
                it = QTableWidgetItem(txt)
                if c == 3:
                    it.setToolTip(txt)
                self._table.setItem(r, c, it)
