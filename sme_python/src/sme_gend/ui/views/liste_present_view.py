"""Liste des présents — aperçu HTML + export Excel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.liste_present_service import ListePresentService


class ListePresentView(QWidget):
    def __init__(self, service: ListePresentService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)

        self._btn_refresh = QPushButton("Actualiser l'aperçu")
        self._btn_refresh.clicked.connect(self._refresh)

        self._btn_export = QPushButton("Exporter vers Excel…")
        self._btn_export.clicked.connect(self._export)

        self._info = QLabel(
            "Données : table Access <b>ListeGenerale</b>, lignes dont la position "
            "administrative (Padmin) est « présent », ordre des sections selon "
            "<code>liste_present_layout.json</code>. "
            "Aperçu aligné sur la <b>maquette</b> (18 colonnes comme "
            "<code>resultat.xlsx</code>), titres par structure puis sous-blocs "
            "par <b>subdivision d'arme</b>. La recherche filtre les personnes "
            "affichées sans modifier la base."
        )
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Filtrer (nom, grade, structure, matricule…) — sur les présents uniquement"
        )
        self._search.textChanged.connect(self._refresh)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_export)
        bar.addStretch()

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Recherche :"))
        search_row.addWidget(self._search, stretch=1)

        lay = QVBoxLayout(self)
        lay.addWidget(self._info)
        lay.addLayout(bar)
        lay.addLayout(search_row)
        lay.addWidget(self._browser, stretch=1)

        self._refresh()

    def _refresh(self) -> None:
        try:
            html = self._service.build_preview_html(self._search.text().strip() or None)
        except Exception as e:
            QMessageBox.critical(self, "Aperçu", str(e))
            return
        self._browser.setHtml(html)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la liste des présents",
            str(Path.home() / "liste_presents_sme.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            self._service.export_excel(
                Path(path), search_filter=self._search.text().strip() or None
            )
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
