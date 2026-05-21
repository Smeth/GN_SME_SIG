"""Vue Détachés — aperçu HTML, export Excel (feuille SME_Detaches)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.detaches_file_config import DetachesStructureEntry

from sme_gend.config.settings import Settings, user_paths_config_file_hint
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_excel_export import export_detaches_workbook, export_detaches_workbook_full
from sme_gend.services.detaches_preview_service import DetachesPreviewService
from sme_gend.ui.bg_task import run_with_progress
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

        self._btn_quick_sigle = QPushButton("Nouveau sigle (config)…")
        self._btn_quick_sigle.setToolTip(
            "Ajoute une entrée dans detaches_config.json (sigle = subdivision arme, titres = en-têtes Excel). "
            "Enregistre puis régénérez l'aperçu ou le fichier officiel."
        )
        self._btn_quick_sigle.clicked.connect(self._quick_add_sigle_config)

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
            "<b>Détachés</b> — Périmètre : personnels avec <b>position administrative « Détaché »</b> "
            "<i>ou</i> colonne <b>Structure</b> = <code>DETACHE</code> (casse ignorée). "
            "Les écarts (<code>DETACHE</code> sans Padmin détaché) apparaissent en fin de liste avec une colonne "
            "<b>Contrôle</b> dans l’export <code>SME_Detaches</code>. "
            "Le <b>sigle</b> affiché correspond à la <b>subdivision arme</b> (lieu de détachement dans la feuille Excel). "
            "Un tableau <b>Référentiels → Config. fichier DETACHES</b> lie chaque <b>sigle</b> "
            "(subdivision arme en base) aux <b>titres</b> imprimés dans la feuille Excel DETACHE. "
            "Bouton <b>Nouveau sigle (config)</b> pour ajouter une ligne sans ouvrir tout le référentiel. "
            "Les <b>nouveaux détachés</b> (par rapport au mois précédent) s’affichent en bas de l’aperçu "
            "si une archive existe dans <code>sme_archives/</code> pour ce mois. "
            "<b>Fichier → Chemins…</b> : indiquez le <code>DETACHES.xlsx</code> officiel, puis "
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
        bar.addWidget(self._btn_quick_sigle)
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

        self._loaded = False
        self._refresh_official_path_label()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loaded:
            self._do_refresh()

    def mark_dirty(self) -> None:
        self._loaded = False

    def _quick_add_sigle_config(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Nouveau sigle — configuration DETACHES")
        dlg.setMinimumWidth(420)
        le_sigle = QLineEdit()
        le_sigle.setPlaceholderText("Ex. MAIRIE PNR (subdivision arme en base, sera normalisé en majuscules)")
        le_t1 = QLineEdit()
        le_t1.setPlaceholderText("Titre ligne 1 (ex. nom complet dans Excel)")
        le_t2 = QLineEdit()
        le_t2.setPlaceholderText("Titre ligne 2 (optionnel)")
        le_t3 = QLineEdit()
        le_t3.setPlaceholderText("Titre ligne 3 (optionnel)")
        form = QFormLayout()
        form.addRow("Sigle (subdivision arme) :", le_sigle)
        form.addRow("Titre 1 :", le_t1)
        form.addRow("Titre 2 :", le_t2)
        form.addRow("Titre 3 :", le_t3)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addLayout(form)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw = le_sigle.text().strip()
        if not raw:
            QMessageBox.warning(self, "Sigle", "Indiquez le sigle (subdivision arme).")
            return
        ent_norm = DetachesStructureEntry(sigle=raw, ordre=0, titres_lignes=[])
        sig_key = ent_norm.sigle
        titres: list[str] = []
        for le in (le_t1, le_t2, le_t3):
            t = le.text().strip()
            if t:
                titres.append(t)
        if not titres:
            titres = [sig_key]
        cfg = self._detaches_cfg.load()
        known = {e.sigle for e in cfg.structures}
        if sig_key in known:
            QMessageBox.warning(self, "Sigle", f"Le sigle « {sig_key} » existe déjà dans la configuration.")
            return
        max_ord = max((e.ordre for e in cfg.structures), default=-1)
        cfg.structures.append(
            DetachesStructureEntry(sigle=sig_key, ordre=max_ord + 1, titres_lignes=titres),
        )
        try:
            self._detaches_cfg.save(cfg)
        except OSError as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        QMessageBox.information(
            self,
            "Configuration",
            f"Sigle « {sig_key} » ajouté dans :\n{self._detaches_cfg.path()}\n\n"
            "Actualisez l'aperçu ou régénérez le fichier Excel pour appliquer.",
        )
        self._do_refresh()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._loaded = False

    def _do_refresh(self) -> None:
        html = run_with_progress(
            self,
            "Détachés",
            "Préparation de l'aperçu HTML…",
            self._preview.build_html,
        )
        if html is None:
            return
        self._browser.setHtml(html)
        self._loaded = True
        self._refresh_official_path_label()

    def _refresh(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser l'aperçu",
                "Recharger l'aperçu des détachés depuis la base configurée ?",
                default_no=False,
            ):
                return
        self._do_refresh()

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
        def _work() -> tuple[int, int]:
            cfg = self._detaches_cfg.load()
            return export_detaches_workbook_full(
                self._settings,
                path,
                cfg,
                use_template=True,
            )

        out = run_with_progress(
            self,
            "Mise à jour",
            "Écriture du classeur Excel…",
            _work,
        )
        if out is None:
            return
        n_pers, n_blocs = out
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
        def _work() -> bool:
            export_detaches_workbook(self._settings, path, use_template=True)
            return True

        if run_with_progress(
            self,
            "Mise à jour",
            "Régénération de la feuille SME…",
            _work,
        ) is None:
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
        dest_p = Path(dest)

        def _work() -> bool:
            export_detaches_workbook(self._settings, dest_p, use_template=use_tpl)
            return True

        if run_with_progress(self, "Export Excel", "Génération du classeur…", _work) is None:
            return
        msg = (
            f"Fichier enregistré :\n{dest}\n\n"
            "Feuille « SME_Detaches » : périmètre détachés (Padmin et/ou structure) depuis la base."
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
        dest_p = Path(dest)

        def _work() -> tuple[int, int]:
            cfg = self._detaches_cfg.load()
            return export_detaches_workbook_full(
                self._settings,
                dest_p,
                cfg,
                use_template=use_tpl,
            )

        out = run_with_progress(
            self,
            "Export Excel",
            "Génération des feuilles DETACHE et SME…",
            _work,
        )
        if out is None:
            return
        n_pers, n_blocs = out
        QMessageBox.information(
            self,
            "Export",
            f"Fichier enregistré :\n{dest}\n\n"
            f"Feuille DETACHE : {n_pers} personne(s), {n_blocs} bloc(s) de titres.\n"
            "Feuille SME_Detaches : liste plate pour contrôle.",
        )
