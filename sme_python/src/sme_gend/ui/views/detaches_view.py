"""Vue Détachés — aperçu HTML, export Excel (feuille SME_Detaches)."""

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
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_excel_export import export_detaches_workbook, export_detaches_workbook_full
from sme_gend.services.detaches_preview_service import DetachesPreviewService
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no


class DetachesView(QWidget):
    def __init__(
        self,
        settings: Settings,
        preview: DetachesPreviewService,
        detaches_cfg: DetachesConfigService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._preview = preview
        self._detaches_cfg = detaches_cfg

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)

        self._btn_refresh = QPushButton("Actualiser l'aperçu")
        self._btn_refresh.clicked.connect(lambda: self._refresh(silent=False))

        self._btn_export = QPushButton("Exporter vers Excel…")
        self._btn_export.setToolTip(
            "Enregistre un classeur : copie du fichier officiel DETACHES.xlsx (si configuré) "
            "avec une feuille « SME_Detaches » générée depuis la base."
        )
        self._btn_export.clicked.connect(self._export)

        self._btn_export_full = QPushButton("Exporter (feuille DETACHE + SME)…")
        self._btn_export_full.setToolTip(
            "Régénère la grande feuille « DETACHE » selon la configuration (Référentiels) "
            "et ajoute « SME_Detaches ». Nécessite le gabarit DETACHES.xlsx dans les chemins."
        )
        self._btn_export_full.clicked.connect(self._export_full)

        self._btn_official_full = QPushButton("Mettre à jour le fichier officiel (DETACHE + SME)")
        self._btn_official_full.setToolTip(
            "Écrit directement dans le DETACHES.xlsx configuré (Chemins…), sans dialogue Enregistrer sous."
        )
        self._btn_official_full.clicked.connect(self._update_official_full)

        self._btn_official_sme = QPushButton("Mettre à jour le fichier officiel (SME seulement)")
        self._btn_official_sme.setToolTip(
            "Régénère uniquement la feuille « SME_Detaches » dans le fichier officiel ; la feuille « DETACHE » est inchangée."
        )
        self._btn_official_sme.clicked.connect(self._update_official_sme_only)

        self._info = QLabel(
            "<b>Détachés</b> — Les personnels en position <b>Détaché</b> dans Access constituent la "
            "référence pour le fichier officiel <code>DETACHES.xlsx</code>. "
            "Le <b>sigle</b> affiché correspond à la colonne <b>subdivision arme</b>. "
            "Configurez les titres et sigles sous <b>Référentiels → Config. fichier DETACHES</b>. "
            "<b>Fichier → Chemins…</b> : indiquez le <code>DETACHES.xlsx</code> officiel, puis utilisez "
            "<b>Mettre à jour le fichier officiel</b> pour enregistrer sans choisir le dossier à chaque fois."
        )
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)

        self._path_official = QLabel()
        self._path_official.setWordWrap(True)
        self._path_official.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_official.setObjectName("dashSubtitle")

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_export)
        bar.addWidget(self._btn_export_full)
        bar.addStretch()

        bar_off = QHBoxLayout()
        bar_off.addWidget(self._btn_official_full)
        bar_off.addWidget(self._btn_official_sme)
        bar_off.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self._info)
        lay.addWidget(self._path_official)
        lay.addLayout(bar)
        lay.addLayout(bar_off)
        lay.addWidget(self._browser, stretch=1)

        self._refresh(silent=True)
        self._refresh_official_path_label()

    def _refresh(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser l'aperçu",
                "Recharger l'aperçu des détachés depuis la base Access ?",
                default_no=False,
            ):
                return
        try:
            html = self._preview.build_html()
        except Exception as e:
            QMessageBox.critical(self, "Détachés", str(e))
            return
        self._browser.setHtml(html)
        self._refresh_official_path_label()

    def _refresh_official_path_label(self) -> None:
        tpl = self._settings.template_detaches_path
        if tpl is not None and tpl.is_file():
            self._path_official.setText(
                f"<b>Fichier officiel configuré :</b><br><code>{tpl.resolve()}</code>"
            )
            self._btn_official_full.setEnabled(True)
            self._btn_official_sme.setEnabled(True)
        else:
            self._path_official.setText(
                "<b>Aucun fichier officiel</b> — renseignez <code>DETACHES.xlsx</code> dans "
                "<b>Fichier → Chemins des fichiers de données…</b> pour activer la mise à jour directe."
            )
            self._btn_official_full.setEnabled(False)
            self._btn_official_sme.setEnabled(False)

    def _require_official_path(self) -> Path | None:
        tpl = self._settings.template_detaches_path
        if tpl is None or not tpl.is_file():
            QMessageBox.warning(
                self,
                "Fichier officiel",
                "Indiquez le chemin de <b>DETACHES.xlsx</b> dans "
                "<b>Fichier → Chemins des fichiers de données…</b>\n\n"
                f"{user_paths_config_file_hint()}",
            )
            return None
        return Path(tpl).resolve()

    def _update_official_full(self) -> None:
        path = self._require_official_path()
        if path is None:
            return
        if not confirm_yes_no(
            self,
            "Mettre à jour le fichier officiel",
            "Les feuilles DETACHE et SME_Detaches du fichier ci-dessous seront entièrement "
            "régénérées depuis la base Access et la configuration "
            "(Référentiels → Config. fichier DETACHES). Les autres feuilles du classeur sont conservées.\n\n"
            f"{path}\n\n"
            "Pensez à une copie de sauvegarde si besoin. Continuer ?",
            default_no=True,
        ):
            return
        try:
            cfg = self._detaches_cfg.load()
            n_pers, n_blocs = export_detaches_workbook_full(
                self._settings,
                path,
                cfg,
                use_template=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "Mise à jour", str(e))
            return
        QMessageBox.information(
            self,
            "Mise à jour",
            f"Fichier officiel mis à jour :\n{path}\n\n"
            f"Feuille DETACHE : {n_pers} personne(s), {n_blocs} bloc(s).\n"
            "Feuille SME_Detaches : liste plate pour contrôle.",
        )

    def _update_official_sme_only(self) -> None:
        path = self._require_official_path()
        if path is None:
            return
        if not confirm_yes_no(
            self,
            "Mettre à jour SME seulement",
            "Seule la feuille SME_Detaches sera régénérée. La feuille DETACHE ne sera pas modifiée.\n\n"
            f"{path}\n\nContinuer ?",
            default_no=True,
        ):
            return
        try:
            export_detaches_workbook(self._settings, path, use_template=True)
        except Exception as e:
            QMessageBox.critical(self, "Mise à jour", str(e))
            return
        QMessageBox.information(
            self,
            "Mise à jour",
            f"Feuille « SME_Detaches » régénérée dans :\n{path}",
        )

    def _export(self) -> None:
        if not confirm_yes_no(
            self,
            "Export Excel",
            "Exporter la feuille « SME_Detaches » (liste plate des détachés) vers un fichier Excel ?",
            default_no=True,
        ):
            return
        tpl = self._settings.template_detaches_path
        use_tpl = tpl is not None and tpl.is_file()
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le classeur",
            str(Path.home() / "DETACHES_export.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        if not use_tpl:
            if not confirm_yes_no(
                self,
                "Gabarit DETACHES.xlsx",
                "Aucun fichier officiel configuré. Exporter uniquement la feuille « SME_Detaches » "
                f"dans un classeur neuf ?\n\n(Chemins : {user_paths_config_file_hint()})",
                default_no=True,
            ):
                return
        try:
            export_detaches_workbook(self._settings, Path(dest), use_template=use_tpl)
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        msg = (
            f"Fichier enregistré :\n{dest}\n\n"
            "Feuille « SME_Detaches » : effectifs « Détaché » depuis la base."
        )
        if use_tpl:
            msg += " Les autres feuilles du gabarit officiel sont conservées."
        QMessageBox.information(self, "Export", msg)

    def _export_full(self) -> None:
        if not confirm_yes_no(
            self,
            "Export complet",
            "Régénérer les feuilles « DETACHE » (selon la configuration) et « SME_Detaches » "
            "dans un fichier Excel ?",
            default_no=True,
        ):
            return
        tpl = self._settings.template_detaches_path
        use_tpl = tpl is not None and tpl.is_file()
        if not use_tpl:
            if not confirm_yes_no(
                self,
                "Gabarit",
                "Sans fichier DETACHES.xlsx, un classeur minimal avec uniquement les feuilles "
                "« DETACHE » et « SME_Detaches » sera créé. Continuer ?",
                default_no=True,
            ):
                return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le classeur (DETACHE généré)",
            str(Path.home() / "DETACHES_complet.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        try:
            cfg = self._detaches_cfg.load()
            n_pers, n_blocs = export_detaches_workbook_full(
                self._settings,
                Path(dest),
                cfg,
                use_template=use_tpl,
            )
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        QMessageBox.information(
            self,
            "Export",
            f"Fichier enregistré :\n{dest}\n\n"
            f"Feuille DETACHE : {n_pers} personne(s), {n_blocs} bloc(s) de titres.\n"
            "Feuille SME_Detaches : liste plate pour contrôle.",
        )
