"""Situation numérique — trois aperçus en onglets (Matrice, Indispo, Listes détail)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings, user_paths_config_file_hint
from sme_gend.services.numerique_excel_export import export_numerique_workbook
from sme_gend.services.numerique_preview_service import NumeriquePreviewService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file


def _make_preview_tab(info_html: str) -> tuple[QWidget, QTextBrowser, QPushButton]:
    """Retourne (widget onglet, browser, bouton actualiser)."""
    browser = QTextBrowser()
    browser.setOpenExternalLinks(False)

    btn_refresh = QPushButton("Actualiser l'aperçu")

    info = QLabel(info_html)
    info.setWordWrap(True)
    info.setTextFormat(Qt.TextFormat.RichText)

    bar = QHBoxLayout()
    bar.addWidget(btn_refresh)
    bar.addStretch()

    tab = QWidget()
    lay = QVBoxLayout(tab)
    lay.addWidget(info)
    lay.addLayout(bar)
    lay.addWidget(browser, stretch=1)
    return tab, browser, btn_refresh


class NumeriqueCombinedView(QWidget):
    """Onglets : Matrice NUMERIQUE · Indispo par structure · Listes détail."""

    def __init__(self, settings: Settings, preview: NumeriquePreviewService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._preview = preview

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        tab_m, self._browser_m, btn_m = _make_preview_tab(
            "Feuille <b>NUMERIQUE</b> — matrice centrale, agrégats par catégorie U/AY, "
            "synthèse AD, détail présents BH, récap variation CB. "
            "Rubrique <b>Présents</b> : Padmin contenant « présent » ou « stage interne »."
        )
        btn_m.clicked.connect(lambda: self._refresh_matrice())
        self._tabs.addTab(tab_m, "Matrice")

        tab_i, self._browser_i, btn_i = _make_preview_tab(
            "Feuille <b>INDISPO PAR STRUCTURE bis</b> — tableau structure × catégorie d'indispo. "
            "Les lignes structures proviennent du gabarit Excel."
        )
        btn_i.clicked.connect(lambda: self._refresh_indispo())
        self._tabs.addTab(tab_i, "Indispo / structure")

        tab_s, self._browser_s, btn_s = _make_preview_tab(
            "Feuilles <b>MISSION</b>, <b>MALADE</b>, <b>DESERTEUR</b>, <b>STAGE-LOC</b>, "
            "<b>STAGE-EXT</b> — listes détail avec filtres Padmin et jointures Tab*."
        )
        btn_s.clicked.connect(lambda: self._refresh_satellite())
        self._tabs.addTab(tab_s, "Listes détail")

        btn_export = QPushButton("Exporter le classeur Excel complet…")
        btn_export.clicked.connect(self._export)

        top = QHBoxLayout()
        top.addWidget(btn_export)
        top.addStretch()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(self._tabs, stretch=1)

        self._loaded = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._refresh_matrice(silent=True)

    def mark_dirty(self) -> None:
        self._loaded = False

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._loaded = False
        self._refresh_matrice(silent=True)

    def _refresh_matrice(self, *, silent: bool = False) -> None:
        del silent
        html = run_with_progress(
            self, "Situation numérique", "Préparation de la matrice…",
            self._preview.build_html_matrice,
        )
        if html is not None:
            self._browser_m.setHtml(html)
            self._loaded = True

    def _refresh_indispo(self, *, silent: bool = False) -> None:
        del silent
        html = run_with_progress(
            self, "Situation numérique", "Préparation indispo…",
            self._preview.build_html_indispo,
        )
        if html is not None:
            self._browser_i.setHtml(html)

    def _refresh_satellite(self, *, silent: bool = False) -> None:
        del silent
        html = run_with_progress(
            self, "Situation numérique", "Préparation des listes détail…",
            self._preview.build_html_satellite_etats,
        )
        if html is not None:
            self._browser_s.setHtml(html)

    def _export(self) -> None:
        tpl = self._settings.template_numerique_path
        if tpl is None or not tpl.is_file():
            QMessageBox.warning(
                self, "Gabarit manquant",
                "Indiquez le fichier « numerique.xlsx » (Fichier → Chemins…) ou "
                f"placez-le dans data/templates/.\n\n{user_paths_config_file_hint()}",
            )
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le classeur",
            str(Path.home() / "Situation_numerique.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        dest_p = Path(dest)

        def _work() -> bool:
            export_numerique_workbook(
                self._preview.liste_settings,
                tpl,
                dest_p,
                gain_perte_settings=self._preview.gain_perte_settings,
            )
            return True

        if run_with_progress(self, "Export Excel", "Génération du classeur…", _work) is None:
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
