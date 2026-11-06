"""Vue Détachés — aperçu HTML, export Excel (feuille SME_Detaches)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QToolButton,
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
from sme_gend.ui.theme import apply_paper_preview_palette, is_dark, theme_palette

_MOIS_FR = (
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
)


def _parse_ym(ym: str) -> tuple[int, int] | None:
    try:
        y_s, m_s = (ym or "").strip().split("-", 1)
        y, m = int(y_s), int(m_s)
        if 1 <= m <= 12:
            return y, m
    except (ValueError, TypeError):
        pass
    return None


def _idle_html() -> str:
    bg = "#252636" if is_dark() else "#fafafa"
    box_bg = "#1a1b2e" if is_dark() else "#fff"
    border = "#3d3e5c" if is_dark() else "#cbd5e1"
    title = "#e8eaf6" if is_dark() else "#1e293b"
    text = "#9fa8da" if is_dark() else "#64748b"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>body{{font-family:Calibri,Arial,sans-serif;margin:0;display:flex;"
        f"align-items:center;justify-content:center;min-height:240px;background:{bg};color:{text};}}"
        f".box{{text-align:center;padding:32px 40px;border:1px dashed {border};border-radius:12px;"
        f"background:{box_bg};}}"
        f"h2{{margin:0 0 10px;font-size:16pt;color:{title};}}p{{margin:0;color:{text};line-height:1.5;}}</style>"
        "</head><body><div class='box'><h2>Détachés</h2>"
        "<p>Cliquez sur <b>Actualiser</b> pour charger l'aperçu depuis la base.</p>"
        "</div></body></html>"
    )


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

        self._browser_recap = QTextBrowser()
        self._browser_detail = QTextBrowser()
        self._browser_anomalies = QTextBrowser()
        self._browser_compare = QTextBrowser()
        for br in (
            self._browser_recap,
            self._browser_detail,
            self._browser_anomalies,
            self._browser_compare,
        ):
            br.setObjectName("paperPreview")
            br.setOpenExternalLinks(False)
            apply_paper_preview_palette(br)
            br.setHtml(_idle_html())

        self._cmp_ref_annee = QComboBox()
        self._cmp_ref_annee.setObjectName("gainPertePeriodCombo")
        self._cmp_ref_annee.setMinimumWidth(72)
        self._cmp_ref_annee.setToolTip("Année du mois de référence")
        self._cmp_ref_mois = QComboBox()
        self._cmp_ref_mois.setObjectName("gainPertePeriodCombo")
        self._cmp_ref_mois.setMinimumWidth(108)
        self._cmp_ref_mois.setToolTip("Mois de référence (point de départ du comparatif)")
        self._cmp_cible_annee = QComboBox()
        self._cmp_cible_annee.setObjectName("gainPertePeriodCombo")
        self._cmp_cible_annee.setMinimumWidth(72)
        self._cmp_cible_annee.setToolTip("Année du mois comparé")
        self._cmp_cible_mois = QComboBox()
        self._cmp_cible_mois.setObjectName("gainPertePeriodCombo")
        self._cmp_cible_mois.setMinimumWidth(108)
        self._cmp_cible_mois.setToolTip("Mois comparé (devenus / ne sont plus détachés…)")

        self._cmp_ref_annee.currentIndexChanged.connect(self._on_compare_ref_annee_changed)
        self._cmp_ref_mois.currentIndexChanged.connect(self._on_compare_mois_changed)
        self._cmp_cible_annee.currentIndexChanged.connect(self._on_compare_cible_annee_changed)
        self._cmp_cible_mois.currentIndexChanged.connect(self._on_compare_mois_changed)

        compare_bar = QHBoxLayout()
        compare_bar.addWidget(QLabel("Mois référence :"))
        compare_bar.addWidget(QLabel("Année"))
        compare_bar.addWidget(self._cmp_ref_annee)
        compare_bar.addWidget(QLabel("Mois"))
        compare_bar.addWidget(self._cmp_ref_mois)
        compare_bar.addSpacing(16)
        compare_bar.addWidget(QLabel("Comparer à :"))
        compare_bar.addWidget(QLabel("Année"))
        compare_bar.addWidget(self._cmp_cible_annee)
        compare_bar.addWidget(QLabel("Mois"))
        compare_bar.addWidget(self._cmp_cible_mois)
        compare_bar.addStretch(1)

        self._compare_panel = QWidget()
        compare_lay = QVBoxLayout(self._compare_panel)
        compare_lay.setContentsMargins(0, 0, 0, 0)
        compare_lay.setSpacing(6)
        compare_lay.addLayout(compare_bar)
        compare_lay.addWidget(self._browser_compare, stretch=1)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._browser_detail, "Personnes")
        self._tabs.addTab(self._browser_anomalies, "Contrôle Structure")
        self._tabs.addTab(self._browser_recap, "Récap sigles")
        self._tabs.addTab(self._compare_panel, "Écarts entre mois")

        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher nom, grade, sigle, structure, réf. note…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.returnPressed.connect(self._do_refresh)
        self._search.textChanged.connect(self._on_search_debounce)

        self._filter_sigle = QComboBox()
        self._filter_sigle.setMinimumWidth(120)
        self._filter_sigle.addItem("Tous les sigles", "__all__")
        self._filter_sigle.setToolTip("Filtrer par subdivision d'arme (sigle)")
        self._filter_sigle.currentIndexChanged.connect(self._on_display_filter_changed)

        self._filter_structure = QComboBox()
        self._filter_structure.setMinimumWidth(130)
        self._filter_structure.addItem("Toutes les structures", "__all__")
        self._filter_structure.setToolTip("Filtrer par colonne Structure (liste générale)")
        self._filter_structure.currentIndexChanged.connect(self._on_display_filter_changed)

        self._filter_padmin = QComboBox()
        self._filter_padmin.setMinimumWidth(130)
        self._filter_padmin.addItem("Toutes les positions admin.", "__all__")
        self._filter_padmin.setToolTip("Filtrer par position administrative (Padmin)")
        self._filter_padmin.currentIndexChanged.connect(self._on_display_filter_changed)

        self._filter_note = QComboBox()
        self._filter_note.addItem("Réf. note : toutes", "all")
        self._filter_note.addItem("Sans réf. note", "sans")
        self._filter_note.addItem("Avec réf. note", "avec")
        self._filter_note.setToolTip(
            "Filtre l'aperçu et les exports « Enregistrer sous… » (pas la mise à jour du fichier officiel)."
        )
        self._filter_note.currentIndexChanged.connect(self._on_display_filter_changed)

        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.setDefault(True)
        self._btn_refresh.setToolTip("Charger les détachés depuis la base (F5)")
        self._btn_refresh.clicked.connect(self._do_refresh)

        self._btn_reset_filters = QPushButton("Réinitialiser filtres")
        self._btn_reset_filters.setToolTip(
            "Efface la recherche, remet sigle / structure / position admin. / réf. note "
            "sur « toutes », et rétablit le comparatif mois (mois consulté vs mois précédent)."
        )
        self._btn_reset_filters.clicked.connect(self._reset_display_filters)

        self._btn_help = QToolButton()
        self._btn_help.setText("?")
        self._btn_help.setToolTip("Périmètre et critères « détaché »")
        self._btn_help.clicked.connect(self._show_help)

        self._btn_quick_sigle = QPushButton("Nouveau sigle (config)…")
        self._btn_quick_sigle.setToolTip(
            "Ajoute une entrée dans detaches_config.json (sigle = subdivision arme, titres = en-têtes Excel). "
            "Enregistre puis régénérez l'aperçu ou le fichier officiel."
        )
        self._btn_quick_sigle.clicked.connect(self._quick_add_sigle_config)

        self._btn_export_menu = QToolButton()
        self._btn_export_menu.setText("Exporter ▾")
        self._btn_export_menu.setToolTip("Enregistrer un classeur Excel des détachés")
        self._btn_export_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self)
        act_sme = export_menu.addAction("Enregistrer sous… (feuille SME_Detaches)")
        act_sme.setToolTip(
            "Copie du gabarit DETACHES.xlsx avec une feuille « SME_Detaches » générée depuis la base."
        )
        act_sme.triggered.connect(self._export)
        act_full = export_menu.addAction("Enregistrer sous… (DETACHE + SME)")
        act_full.setToolTip(
            "Régénère la feuille « DETACHE » (configuration Référentiels) et « SME_Detaches »."
        )
        act_full.triggered.connect(self._export_full)
        export_menu.addSeparator()
        act_sans = export_menu.addAction("Enregistrer sous… (sans réf. note uniquement)")
        act_sans.setToolTip(
            "Exporte uniquement les détachés dont la réf. note de service (N° décision) est vide."
        )
        act_sans.triggered.connect(lambda: self._export(sans_note_only=True))
        self._btn_export_menu.setMenu(export_menu)

        self._btn_official_menu = QToolButton()
        self._btn_official_menu.setText("⚠ Fichier officiel ▾")
        self._btn_official_menu.setToolTip(
            "Écrase le DETACHES.xlsx officiel configuré dans Fichier → Chemins… "
            "(contrairement à « Exporter », qui enregistre toujours une nouvelle copie)."
        )
        self._btn_official_menu.setProperty("destructive", True)
        self._btn_official_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        official_menu = QMenu(self)
        act_off_full = official_menu.addAction("Mettre à jour (DETACHE + SME)")
        act_off_full.triggered.connect(self._update_official_full)
        act_off_sme = official_menu.addAction("Mettre à jour (SME seulement)")
        act_off_sme.triggered.connect(self._update_official_sme_only)
        self._btn_official_menu.setMenu(official_menu)
        self._official_menu_actions = (act_off_full, act_off_sme)

        self._status = QLabel("Cliquez sur Actualiser pour charger les détachés du mois consulté.")
        self._status.setObjectName("settingsPathsHint")
        self._status.setWordWrap(True)

        self._warnings = QLabel()
        self._warnings.setWordWrap(True)
        self._warnings.setTextFormat(Qt.TextFormat.RichText)
        self._warnings.setObjectName("dashSubtitle")
        self._warnings.hide()

        from sme_gend.ui.responsive_panels import WrapLayout
        bar = WrapLayout()
        bar.addWidget(self._search)
        bar.addWidget(self._filter_sigle)
        bar.addWidget(self._filter_structure)
        bar.addWidget(self._filter_padmin)
        bar.addWidget(self._filter_note)
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_reset_filters)
        bar.addWidget(self._btn_help)
        bar.addWidget(self._btn_quick_sigle)
        bar.addWidget(self._btn_export_menu)
        bar.addWidget(self._btn_official_menu)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(bar)
        lay.addWidget(self._status)
        lay.addWidget(self._warnings)
        lay.addWidget(self._tabs, stretch=1)

        self._loaded = False
        self._bundle = None
        self._search_timer_id = 0
        self._compare_refresh_pending = False
        self._compare_mois_catalog: frozenset[str] = frozenset()
        self._reset_compare_on_next_refresh = False
        self._refresh_official_path_label()

    def refresh_theme_styles(self) -> None:
        for br in (
            self._browser_recap,
            self._browser_detail,
            self._browser_anomalies,
            self._browser_compare,
        ):
            apply_paper_preview_palette(br)
        if not self._loaded:
            idle = _idle_html()
            self._browser_recap.setHtml(idle)
            self._browser_detail.setHtml(idle)
            self._browser_anomalies.setHtml(idle)
            self._browser_compare.setHtml(idle)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)

    def mark_dirty(self) -> None:
        self._loaded = False
        self._bundle = None
        if self.isVisible():
            idle = _idle_html()
            self._browser_recap.setHtml(idle)
            self._browser_detail.setHtml(idle)
            self._browser_anomalies.setHtml(idle)
            self._browser_compare.setHtml(idle)

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if not self._loaded:
            idle = _idle_html()
            self._browser_recap.setHtml(idle)
            self._browser_detail.setHtml(idle)
            self._browser_anomalies.setHtml(idle)
            self._browser_compare.setHtml(idle)

    def _show_help(self) -> None:
        tpl = self._settings.template_detaches_path
        off = f"\n\nFichier officiel :\n{tpl.resolve()}" if tpl and tpl.is_file() else ""
        QMessageBox.information(
            self,
            "Périmètre détachés",
            "Personnel retenu si position administrative « Détaché » (situation numérique) "
            "ou colonne Structure = DETACHE.\n\n"
            "Sigle = subdivision d'arme ; titres Excel = Référentiels → Config. DETACHES.\n"
            "Les fiches Structure DETACHE sans Padmin « Détaché » sont listées dans l’onglet "
            "« Contrôle Structure ».\n\n"
            "L’onglet « Écarts entre mois » compare deux mois SME en base (entrées, sorties, "
            "modifications de fiches toujours détachées).\n\n"
            "« Réinitialiser filtres » remet la barre de filtres et le comparatif "
            "sur les valeurs par défaut."
            + off,
        )

    def _on_compare_ref_annee_changed(self, _index: int) -> None:
        prev_m = self._cmp_ref_mois.currentData()
        self._cmp_ref_mois.blockSignals(True)
        self._cmp_ref_mois.clear()
        self._fill_compare_mois_combo(self._cmp_ref_mois, self._cmp_ref_annee, prev_m)
        self._cmp_ref_mois.blockSignals(False)
        self._on_compare_mois_changed()

    def _on_compare_cible_annee_changed(self, _index: int) -> None:
        prev_m = self._cmp_cible_mois.currentData()
        self._cmp_cible_mois.blockSignals(True)
        self._cmp_cible_mois.clear()
        self._fill_compare_mois_combo(self._cmp_cible_mois, self._cmp_cible_annee, prev_m)
        self._cmp_cible_mois.blockSignals(False)
        self._on_compare_mois_changed()

    def _on_compare_mois_changed(self) -> None:
        if not self._loaded:
            return
        self._compare_refresh_pending = True
        self._do_refresh()

    def _ym_from_compare_combos(self, annee_cb: QComboBox, mois_cb: QComboBox) -> str:
        year = annee_cb.currentData()
        month = mois_cb.currentData()
        if year is None or month is None:
            return ""
        return f"{int(year):04d}-{int(month):02d}"

    def _compare_month_values(self) -> tuple[str, str]:
        return (
            self._ym_from_compare_combos(self._cmp_ref_annee, self._cmp_ref_mois),
            self._ym_from_compare_combos(self._cmp_cible_annee, self._cmp_cible_mois),
        )

    def _fill_compare_mois_combo(
        self,
        mois_cb: QComboBox,
        annee_cb: QComboBox,
        target_month: int | None,
    ) -> None:
        year = annee_cb.currentData()
        if year is None:
            return
        y = int(year)
        months = sorted(
            p[1]
            for ym in self._compare_mois_catalog
            if (p := _parse_ym(ym)) and p[0] == y
        )
        for m in months:
            mois_cb.addItem(_MOIS_FR[m - 1], m)
        if target_month is not None:
            for i in range(mois_cb.count()):
                if mois_cb.itemData(i) == target_month:
                    mois_cb.setCurrentIndex(i)
                    return
        if mois_cb.count():
            mois_cb.setCurrentIndex(mois_cb.count() - 1)

    def _rebuild_compare_side(
        self,
        annee_cb: QComboBox,
        mois_cb: QComboBox,
        target_ym: str | None,
    ) -> None:
        catalog = self._compare_mois_catalog
        years = sorted({p[0] for ym in catalog if (p := _parse_ym(ym))}, reverse=True)
        tp = _parse_ym(target_ym) if target_ym else None
        if tp and tp[0] not in years:
            years = sorted(set(years) | {tp[0]}, reverse=True)

        annee_cb.blockSignals(True)
        mois_cb.blockSignals(True)
        annee_cb.clear()
        for y in years:
            annee_cb.addItem(str(y), y)
        if tp:
            for i in range(annee_cb.count()):
                if annee_cb.itemData(i) == tp[0]:
                    annee_cb.setCurrentIndex(i)
                    break
        mois_cb.clear()
        self._fill_compare_mois_combo(mois_cb, annee_cb, tp[1] if tp else None)
        annee_cb.blockSignals(False)
        mois_cb.blockSignals(False)

    def _on_search_debounce(self) -> None:
        if not self._loaded:
            return
        if self._search_timer_id:
            self.killTimer(self._search_timer_id)
        self._search_timer_id = self.startTimer(350)

    def timerEvent(self, event) -> None:  # noqa: N802
        if event.timerId() == self._search_timer_id:
            self.killTimer(self._search_timer_id)
            self._search_timer_id = 0
            self._do_refresh()
            return
        super().timerEvent(event)

    def _on_display_filter_changed(self) -> None:
        if self._loaded:
            self._do_refresh()

    def _reset_display_filters(self) -> None:
        """Barre d’outils : filtres d’aperçu + comparatif mois par défaut."""
        self._search.clear()
        for combo in (
            self._filter_sigle,
            self._filter_structure,
            self._filter_padmin,
        ):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._filter_note.blockSignals(True)
        self._filter_note.setCurrentIndex(0)
        self._filter_note.blockSignals(False)
        self._reset_compare_on_next_refresh = True
        self._do_refresh()

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

    def _note_filter_flags(self) -> tuple[bool, bool]:
        key = self._filter_note.currentData()
        return key == "sans", key == "avec"

    def _do_refresh(self) -> None:
        sans, avec = self._note_filter_flags()
        search = self._search.text().strip()
        sigle = str(self._filter_sigle.currentData() or "__all__")
        structure = str(self._filter_structure.currentData() or "__all__")
        padmin = str(self._filter_padmin.currentData() or "__all__")

        if self._reset_compare_on_next_refresh:
            cmp_ref, cmp_cible = "", ""
        else:
            cmp_ref, cmp_cible = self._compare_month_values()

        def _work():
            return self._preview.build_bundle(
                sans_note_only=sans,
                avec_note_only=avec,
                search=search,
                sigle=sigle,
                structure=structure,
                padmin=padmin,
                compare_mois_ref=cmp_ref,
                compare_mois_cible=cmp_cible,
            )

        bundle = run_with_progress(
            self,
            "Détachés",
            "Chargement…",
            _work,
        )
        if bundle is None:
            return
        self._bundle = bundle
        self._browser_recap.setHtml(bundle.html_recap)
        self._browser_detail.setHtml(bundle.html_detail)
        self._browser_anomalies.setHtml(bundle.html_anomalies)
        self._browser_compare.setHtml(bundle.html_compare)
        self._loaded = True
        meta = bundle.meta
        self._repopulate_compare_month_combos(meta)
        self._status.setToolTip(meta.status_line)
        self._status.setText(f'{meta.total_display} affiché(s) / {meta.total_scope} au périmètre')
        if meta.warnings:
            self._warnings.setText(
                "<br/>".join(f"<span style='color:#e65100;'>{w}</span>" for w in meta.warnings)
            )
            self._warnings.show()
        else:
            self._warnings.hide()
        self._repopulate_sigle_combo(meta.sigles)
        self._repopulate_field_combo(self._filter_structure, "Toutes les structures", meta.structures)
        self._repopulate_field_combo(self._filter_padmin, "Toutes les positions admin.", meta.padmins)
        self._refresh_official_path_label()
        self._compare_refresh_pending = False

    def _repopulate_field_combo(
        self,
        combo: QComboBox,
        all_label: str,
        values: list[tuple[str, int]],
    ) -> None:
        prev = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "__all__")
        for label, n in values:
            combo.addItem(f"{label} ({n})", label)
        if prev:
            i = combo.findData(prev)
            if i >= 0:
                combo.setCurrentIndex(i)
        combo.blockSignals(False)

    def _repopulate_compare_month_combos(self, meta) -> None:
        if self._reset_compare_on_next_refresh:
            prev_ref = (meta.compare_mois_ref or "").strip()
            prev_cible = (meta.compare_mois_cible or "").strip()
            self._reset_compare_on_next_refresh = False
        else:
            prev_ref = self._ym_from_compare_combos(self._cmp_ref_annee, self._cmp_ref_mois)
            prev_cible = self._ym_from_compare_combos(
                self._cmp_cible_annee, self._cmp_cible_mois
            )
            if not prev_ref:
                prev_ref = (meta.compare_mois_ref or "").strip()
            if not prev_cible:
                prev_cible = (meta.compare_mois_cible or "").strip()

        self._compare_mois_catalog = frozenset(meta.available_months or [])
        self._rebuild_compare_side(self._cmp_ref_annee, self._cmp_ref_mois, prev_ref or None)
        self._rebuild_compare_side(
            self._cmp_cible_annee, self._cmp_cible_mois, prev_cible or None
        )

    def _repopulate_sigle_combo(self, sigles: list[tuple[str, int]]) -> None:
        prev = self._filter_sigle.currentData()
        self._filter_sigle.blockSignals(True)
        self._filter_sigle.clear()
        self._filter_sigle.addItem("Tous les sigles", "__all__")
        for sig, n in sigles:
            self._filter_sigle.addItem(f"{sig} ({n})", sig)
        if prev:
            i = self._filter_sigle.findData(prev)
            if i >= 0:
                self._filter_sigle.setCurrentIndex(i)
        self._filter_sigle.blockSignals(False)

    def _refresh_official_path_label(self) -> None:
        tpl = self._settings.template_detaches_path
        enabled = tpl is not None and tpl.is_file()
        tip = (
            f"Écrase le fichier configuré :\n{tpl.resolve()}"
            if enabled
            else "Configurez DETACHES.xlsx dans Fichier → Chemins…"
        )
        self._btn_official_menu.setToolTip(tip)
        self._btn_official_menu.setEnabled(enabled)
        for act in self._official_menu_actions:
            act.setEnabled(enabled)

    def _require_official_path(self) -> Path | None:
        tpl = self._settings.template_detaches_path
        if tpl is None or not tpl.is_file():
            QMessageBox.warning(
                self,
                "Fichier officiel",
                "Indiquez le chemin de <b>DETACHES.xlsx</b> dans "
                "<b>Fichier → Configuration MySQL et chemins…</b>\n\n"
                f"{user_paths_config_file_hint()}",
            )
            return None
        return Path(tpl).resolve()

    def _update_official_full(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "effectifs.export"):
            return
        path = self._require_official_path()
        if path is None:
            return
        if not confirm_yes_no(
            self,
            "Mettre à jour le fichier officiel",
            "Les feuilles DETACHE et SME_Detaches du fichier ci-dessous seront entièrement "
            "régénérées depuis la liste générale MySQL et la configuration "
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
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "effectifs.export"):
            return
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

    def _export_preview_snapshot(self):
        from copy import deepcopy
        from sme_gend.services.detaches_preview_service import detaches_filter_key
        sans, avec = self._note_filter_flags()
        current = detaches_filter_key(sans, avec, self._search.text().strip(),
            self._filter_sigle.currentData(), self._filter_structure.currentData(), self._filter_padmin.currentData())
        bundle = self._bundle
        if not self._loaded or bundle is None or bundle.filters != current:
            QMessageBox.information(self, "Export", "Actualisez l’aperçu avec les filtres souhaités avant l’export.")
            return None
        if not bundle.rows:
            QMessageBox.information(self, "Export", "Aucune ligne ne correspond aux filtres.")
            return None
        return deepcopy(bundle)

    def _export(self, *, sans_note_only: bool = False) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        snapshot = self._export_preview_snapshot()
        if snapshot is None:
            return
        settings = snapshot.source_settings
        sans, avec = self._note_filter_flags()
        if sans_note_only:
            sans, avec = True, False
        if not confirm_yes_no(
            self,
            "Export Excel",
            "Exporter la feuille « SME_Detaches » (liste plate des détachés) vers un fichier Excel ?",
            default_no=True,
        ):
            return
        tpl = settings.template_detaches_path
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
            export_detaches_workbook(
                settings,
                dest_p,
                use_template=use_tpl,
                sans_note_only=sans,
                avec_note_only=avec,
                rows=snapshot.rows,
                configuration=snapshot.configuration,
            )
            return True

        if run_with_progress(self, "Export Excel", "Génération du classeur…", _work) is None:
            return
        filtre = ""
        if sans:
            filtre = "\n(Filtre : sans réf. note de service.)"
        elif avec:
            filtre = "\n(Filtre : avec réf. note de service.)"
        msg = (
            f"Fichier enregistré :\n{dest}\n\n"
            "Feuille « SME_Detaches » : périmètre détachés (Padmin et/ou structure) affiché avec les filtres actifs."
            f"{filtre}"
        )
        if use_tpl:
            msg += " Les autres feuilles du gabarit officiel sont conservées."
        QMessageBox.information(self, "Export", msg)

    def _export_full(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        snapshot = self._export_preview_snapshot()
        if snapshot is None:
            return
        settings = snapshot.source_settings
        if not confirm_yes_no(
            self,
            "Export complet",
            "Régénérer les feuilles « DETACHE » (selon la configuration) et « SME_Detaches » "
            "dans un fichier Excel ?",
            default_no=True,
        ):
            return
        tpl = settings.template_detaches_path
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
        sans, avec = self._note_filter_flags()

        def _work() -> tuple[int, int]:
            cfg = snapshot.configuration
            return export_detaches_workbook_full(
                settings,
                dest_p,
                cfg,
                use_template=use_tpl,
                sans_note_only=sans,
                avec_note_only=avec,
                rows=snapshot.rows,
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
        filtre = ""
        if sans:
            filtre = "\n(Filtre : sans réf. note de service.)"
        elif avec:
            filtre = "\n(Filtre : avec réf. note de service.)"
        QMessageBox.information(
            self,
            "Export",
            f"Fichier enregistré :\n{dest}\n\n"
            f"Feuille DETACHE : {n_pers} personne(s), {n_blocs} bloc(s) de titres.\n"
            f"Feuille SME_Detaches : liste plate pour contrôle.{filtre}",
        )
