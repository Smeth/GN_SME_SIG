"""Aperçu HTML — listes détail mission, malade, déserteur, stages (feuilles satellites du classeur)."""

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

from sme_gend.config.settings import Settings, user_paths_config_file_hint
from sme_gend.services.numerique_excel_export import export_numerique_workbook
from sme_gend.services.numerique_preview_service import NumeriquePreviewService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no


class NumeriqueSatelliteView(QWidget):
    def __init__(self, settings: Settings, preview: NumeriquePreviewService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._preview = preview

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)

        self._btn_refresh = QPushButton("Actualiser l'aperçu")
        self._btn_refresh.clicked.connect(lambda: self._refresh(silent=False))

        self._btn_export = QPushButton("Exporter le classeur Excel…")
        self._btn_export.clicked.connect(self._export)

        self._info = QLabel(
            "Aperçu des feuilles <b>MISSION</b>, <b>MALADE</b>, <b>DESERTEUR</b>, <b>STAGE-LOC</b>, "
            "<b>STAGE-EXT</b> : mêmes filtres Padmin, jointures <b>Tab*</b> et option « détail vide » "
            "que l’export Excel. Les effectifs par grade reprennent le bandeau du gabarit lorsqu’il est "
            "renseigné dans les chemins."
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

        self._refresh(silent=True)

    def _refresh(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser l'aperçu",
                "Recharger les listes détail depuis la base Access ?",
                default_no=False,
            ):
                return
        html = run_with_progress(
            self,
            "Situation numérique",
            "Préparation de l'aperçu HTML…",
            self._preview.build_html_satellite_etats,
        )
        if html is None:
            return
        self._browser.setHtml(html)

    def _export(self) -> None:
        if not confirm_yes_no(
            self,
            "Export Excel",
            "Exporter la situation numérique complète (liste, NUMERIQUE, INDISPO, missions, malades, …) "
            "vers un fichier Excel ?",
            default_no=True,
        ):
            return
        tpl = self._settings.template_numerique_path
        if tpl is None or not tpl.is_file():
            QMessageBox.warning(
                self,
                "Gabarit manquant",
                "Indiquez le fichier « numerique.xlsx » (Fichier → Chemins…) ou "
                f"placez-le dans data/templates/.\n\n{user_paths_config_file_hint()}",
            )
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le classeur",
            str(Path.home() / "Situation_numerique.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        dest_p = Path(dest)

        def _work() -> bool:
            export_numerique_workbook(self._settings, tpl, dest_p)
            return True

        if run_with_progress(
            self,
            "Export Excel",
            "Génération du classeur situation numérique…",
            _work,
        ) is None:
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
