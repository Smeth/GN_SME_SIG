"""Liste des présents — aperçu HTML + export Excel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
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
            "Aperçu aligné sur la <b>maquette</b> : 18 colonnes comme "
            "<code>resultat.xlsx</code>, titres de section par structure, "
            "puis sous-blocs par <b>subdivision d'arme</b>. "
            "L’export Excel reprend la même logique (feuille « SME_ListePresents » "
            "si une maquette est copiée)."
        )
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_export)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self._info)
        lay.addLayout(bar)
        lay.addWidget(self._browser, stretch=1)

        self._refresh()

    def _refresh(self) -> None:
        try:
            html = self._service.build_preview_html()
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
            self._service.export_excel(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
