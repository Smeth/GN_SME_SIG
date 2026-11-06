"""Situation numérique — trois aperçus en onglets (Matrice, Indispo, Listes détail)."""

from __future__ import annotations

import logging
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

from sme_gend.config.liste_generale_scope import build_export_mois_context
from sme_gend.config.settings import Settings, user_paths_config_file_hint
from sme_gend.services.numerique_excel_export import export_numerique_workbook, month_title_de_lowercase
from sme_gend.services.numerique_preview_service import NumeriquePreviewService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file
from sme_gend.ui.numerique_export_dialog import NumeriqueExportDialog
from sme_gend.ui.theme import apply_paper_preview_palette, theme_palette

_LOGGER = logging.getLogger(__name__)


def _make_preview_tab(info_html: str) -> tuple[QWidget, QTextBrowser, QPushButton]:
    """Retourne (widget onglet, browser, bouton actualiser)."""
    browser = QTextBrowser()
    browser.setObjectName("paperPreview")
    browser.setOpenExternalLinks(False)
    apply_paper_preview_palette(browser)

    btn_refresh = QPushButton("Actualiser l'aperçu")

    info = QLabel(info_html)
    info.setWordWrap(True)
    info.setTextFormat(Qt.TextFormat.RichText)
    info.setObjectName("numeriquePreviewInfo")

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
            "Matrice des effectifs. Rubrique Présents : positions « présent » ou « stage interne »."
        )
        btn_m.clicked.connect(lambda: self._refresh_matrice(refresh_cache=True))
        self._tabs.addTab(tab_m, "Synthèse des effectifs")

        tab_i, self._browser_i, btn_i = _make_preview_tab(
            "Indisponibilités par structure (selon le gabarit Excel)."
        )
        btn_i.clicked.connect(lambda: self._refresh_indispo(refresh_cache=True))
        self._tabs.addTab(tab_i, "Indisponibilités par structure")

        tab_s, self._browser_s, btn_s = _make_preview_tab(
            "Listes : mission, malade, déserteur, stages, congé, détention, non-rejoint."
        )
        btn_s.clicked.connect(lambda: self._refresh_satellite(refresh_cache=True))
        self._tabs.addTab(tab_s, "Listes détaillées")

        btn_export = QPushButton("Exporter le classeur Excel complet…")
        btn_export.clicked.connect(self._export)

        self._export_banner = QLabel()
        self._export_banner.setWordWrap(True)
        self._export_banner.setTextFormat(Qt.TextFormat.RichText)
        self._refresh_export_banner()

        top = QVBoxLayout()
        top.setSpacing(8)
        top.addWidget(self._export_banner)
        bar = QHBoxLayout()
        bar.addWidget(btn_export)
        bar.addStretch()
        top.addLayout(bar)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(self._tabs, stretch=1)

        self._loaded = False

    def refresh_theme_styles(self) -> None:
        pal = theme_palette()
        self._export_banner.setStyleSheet(
            f"color: {pal['text_muted']}; padding: 4px 0;"
        )
        for browser in (self._browser_m, self._browser_i, self._browser_s):
            apply_paper_preview_palette(browser)
        for info in self.findChildren(QLabel, "numeriquePreviewInfo"):
            info.setStyleSheet(f"color: {pal['text_muted']};")

    def _refresh_export_banner(self) -> None:
        ctx = build_export_mois_context(self._preview.liste_settings)
        self._export_banner.setText(ctx.banner_html())
        self.refresh_theme_styles()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._refresh_all_previews(silent=True)

    def mark_dirty(self) -> None:
        self._loaded = False

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if not self._loaded:
            self._refresh_all_previews(silent=True)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._loaded = False
        self._refresh_export_banner()
        if self.isVisible():
            self._refresh_all_previews(silent=True)

    def _refresh_all_previews(self, *, silent: bool = False) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._settings)
        preview = self._preview

        def _build_one(label: str, build) -> str:
            try:
                return build()
            except Exception:
                _LOGGER.exception("Impossible de préparer l'aperçu numérique « %s ».", label)
                return (
                    "<!DOCTYPE html><html><body>"
                    f"<h2>{label}</h2>"
                    "<p style='color:#b71c1c;'>"
                    "Impossible de charger cet aperçu. Vérifiez la connexion puis "
                    "utilisez « Actualiser l’aperçu »."
                    "</p></body></html>"
                )

        def _build_all() -> tuple[str, str, str]:
            return (
                _build_one("Synthèse des effectifs", preview.build_html_matrice),
                _build_one("Indisponibilités par structure", preview.build_html_indispo),
                _build_one("Listes détaillées", preview.build_html_satellite_etats),
            )

        result = run_with_progress(
            self,
            "Situation numérique",
            "Actualisation des trois aperçus…",
            _build_all,
            silent=silent,
        )
        if result is None:
            self._loaded = False
            return
        self._browser_m.setHtml(result[0])
        self._browser_i.setHtml(result[1])
        self._browser_s.setHtml(result[2])
        self._loaded = True

    def _refresh_matrice(
        self, *, silent: bool = False, refresh_cache: bool = False
    ) -> None:
        if refresh_cache:
            from sme_gend.services.liste_generale_cache import invalidate_liste_cache

            invalidate_liste_cache(self._settings)
        html = run_with_progress(
            self, "Situation numérique", "Préparation de la matrice…",
            self._preview.build_html_matrice,
            silent=silent,
        )
        if html is not None:
            self._browser_m.setHtml(html)

    def _refresh_indispo(
        self, *, silent: bool = False, refresh_cache: bool = False
    ) -> None:
        if refresh_cache:
            from sme_gend.services.liste_generale_cache import invalidate_liste_cache

            invalidate_liste_cache(self._settings)
        html = run_with_progress(
            self, "Situation numérique", "Préparation indispo…",
            self._preview.build_html_indispo,
            silent=silent,
        )
        if html is not None:
            self._browser_i.setHtml(html)

    def _refresh_satellite(
        self, *, silent: bool = False, refresh_cache: bool = False
    ) -> None:
        if refresh_cache:
            from sme_gend.services.liste_generale_cache import invalidate_liste_cache

            invalidate_liste_cache(self._settings)
        html = run_with_progress(
            self, "Situation numérique", "Préparation des listes détail…",
            self._preview.build_html_satellite_etats,
            silent=silent,
        )
        if html is not None:
            self._browser_s.setHtml(html)

    def _export(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        tpl = self._settings.template_numerique_path
        if tpl is None or not tpl.is_file():
            QMessageBox.warning(
                self, "Gabarit manquant",
                "Indiquez le fichier « numerique.xlsx » (Fichier → Chemins…) ou "
                f"placez-le dans le dossier modeles/ à côté de l'application.\n\n"
                f"{user_paths_config_file_hint()}",
            )
            return

        dlg = NumeriqueExportDialog(self._preview.liste_settings, self)
        if dlg.exec() != NumeriqueExportDialog.DialogCode.Accepted:
            return
        export_mois = dlg.chosen_export_mois()
        mois_slug = (export_mois or "export").replace("-", "_")
        default_name = f"Situation_numerique_{mois_slug}.xlsx"

        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le classeur",
            str(Path.home() / default_name),
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
                export_mois=export_mois,
                gain_perte_settings=self._preview.gain_perte_settings,
            )
            return True

        if run_with_progress(self, "Export Excel", "Génération du classeur…", _work) is None:
            return
        month_lbl = month_title_de_lowercase(export_mois) or export_mois or "—"
        QMessageBox.information(
            self,
            "Export",
            f"Fichier enregistré :\n{dest}\n\nMois SME : {month_lbl}",
        )
