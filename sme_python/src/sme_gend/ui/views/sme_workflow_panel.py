"""Panneau de gestion de la période SME (workflow, clôture, réouverture, historique TabSme)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.services.dashboard_service import DashboardSnapshot
from sme_gend.services.personnel_retroactive_diff import run_retroactive_diff
from sme_gend.services.sme_editing_policy import reopen_closed_month_for_corrections
from sme_gend.services.sme_month_closure import (
    extract_yyyy_mm,
    import_existing_db_for_month,
    run_piste_a_month_closure,
    sme_archives_dir,
)
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.navigation_pages import PAGE_ARCHIVES_CLOSURE

_SME_MONTHS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


def _sme_month_combo(*, for_dialog: bool = False) -> QComboBox:
    cb = QComboBox()
    cb.setObjectName("smePeriodComboDialog" if for_dialog else "smePeriodCombo")
    for i, name in enumerate(_SME_MONTHS_FR, start=1):
        cb.addItem(name, i)
    cb.setMinimumWidth(170)
    cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    return cb


def _sme_year_spin(*, for_dialog: bool = False) -> QSpinBox:
    sp = QSpinBox()
    sp.setObjectName("smePeriodYearSpinDialog" if for_dialog else "smePeriodYearSpin")
    sp.setRange(2000, 2100)
    sp.setValue(date.today().year)
    sp.setMinimumWidth(96)
    return sp


def _widgets_to_ym(cb: QComboBox, sp: QSpinBox) -> str:
    m = cb.currentData()
    y = sp.value()
    if m is None:
        return ""
    return f"{y:04d}-{int(m):02d}"


def _ym_to_widgets(ym_hint: str, cb: QComboBox, sp: QSpinBox) -> None:
    ym = extract_yyyy_mm(ym_hint)
    if ym:
        y_s, mo_s = ym.split("-")
        sp.setValue(int(y_s))
        cb.setCurrentIndex(int(mo_s) - 1)
    else:
        d = date.today()
        sp.setValue(d.year)
        cb.setCurrentIndex(d.month - 1)


def _next_yyyy_mm(ym: str) -> str:
    s = extract_yyyy_mm(ym)
    if not s:
        return ""
    y, m = map(int, s.split("-"))
    if m == 12:
        return f"{y + 1:04d}-01"
    return f"{y:04d}-{m + 1:02d}"


class SmeWorkflowPanel(QFrame):
    """Panneau SME : sélecteur de période, boutons workflow, historique TabSme."""

    def __init__(
        self,
        settings: Settings,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
        on_after_workflow: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dashSmeFrame")
        self._settings = settings
        self._navigate_to_page = navigate_to_page
        self._on_after_workflow = on_after_workflow

        sme_outer = QVBoxLayout(self)
        sme_outer.setSpacing(12)

        self._sme_hint = QLabel()
        self._sme_hint.setWordWrap(True)
        self._sme_hint.setObjectName("dashSubtitle")
        self._sme_table = QTableWidget()
        self._sme_table.setColumnCount(3)
        self._sme_table.setHorizontalHeaderLabels(["Code SME", "Mois / période", "Statut"])
        self._sme_table.horizontalHeader().setStretchLastSection(True)
        self._sme_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sme_table.setAlternatingRowColors(True)

        self._sme_workflow = QGroupBox("Période SME : travail, clôture, réouverture")
        self._sme_workflow.setObjectName("dashSmeWorkflow")
        sw_lay = QVBoxLayout(self._sme_workflow)
        sw_lay.setSpacing(10)
        sw_hint = QLabel(
            "Définissez ici le <b>mois de travail</b> (tracé dans l'audit des fiches). "
            "<b>Clôturer</b> fige le mois dans la base et crée des copies d'archive ; "
            "<b>Réouvrir (exception)</b> autorise des corrections après clôture, avec motif. "
            "Chemins Access / SQLite : menu Fichier ou section <b>Maintenance & fichiers</b>."
        )
        sw_hint.setWordWrap(True)
        sw_hint.setObjectName("dashSubtitle")
        sw_lay.addWidget(sw_hint)

        self._sme_month = _sme_month_combo()
        self._sme_year = _sme_year_spin()
        self._sme_preview = QLabel()
        self._sme_preview.setObjectName("dashSubtitle")
        self._sme_preview.setStyleSheet("color: #1a237e; font-weight: 600; font-size: 13px;")

        self._sme_month.currentIndexChanged.connect(lambda: self._sync_sme_preview_label())
        self._sme_year.valueChanged.connect(self._sync_sme_preview_label)

        self._sme_jour = QSpinBox()
        self._sme_jour.setObjectName("smePeriodDaySpin")
        self._sme_jour.setRange(0, 31)
        self._sme_jour.setSpecialValueText("—")
        self._sme_jour.setValue(0)
        self._sme_jour.setToolTip("Jour de clôture de référence (optionnel, fichier local uniquement).")

        grid_my = QGridLayout()
        grid_my.setHorizontalSpacing(14)
        grid_my.setVerticalSpacing(8)
        lab_m = QLabel("Mois")
        lab_y = QLabel("Année")
        lab_j = QLabel("Jour")
        for lb in (lab_m, lab_y, lab_j):
            lb.setStyleSheet("color: #424242; font-weight: 500;")
        grid_my.addWidget(lab_m, 0, 0)
        grid_my.addWidget(self._sme_month, 0, 1)
        grid_my.addWidget(lab_y, 0, 2)
        grid_my.addWidget(self._sme_year, 0, 3)
        grid_my.addWidget(lab_j, 0, 4)
        grid_my.addWidget(self._sme_jour, 0, 5)
        grid_my.setColumnStretch(6, 1)
        sw_lay.addLayout(grid_my)
        sw_lay.addWidget(self._sme_preview)

        ed_audit_row = QHBoxLayout()
        ed_audit_row.setSpacing(10)
        ed_audit_row.addWidget(QLabel("Signataire (audit fiches) :"))
        self._editeur_audit = QLineEdit()
        self._editeur_audit.setPlaceholderText(
            "Initiales, poste ou nom — champ « edited_by » dans l'audit JSONL"
        )
        self._editeur_audit.setMinimumWidth(280)
        self._btn_editeur_save = QPushButton("Enregistrer")
        self._btn_editeur_save.setToolTip("Enregistre dans sme_context.json (profil utilisateur).")
        self._btn_editeur_save.clicked.connect(self._on_save_editeur_audit)
        ed_audit_row.addWidget(self._editeur_audit, stretch=1)
        ed_audit_row.addWidget(self._btn_editeur_save)
        sw_lay.addLayout(ed_audit_row)

        sw_actions = QHBoxLayout()
        sw_actions.setSpacing(10)
        self._btn_sme_save = QPushButton("Enregistrer le mois actif")
        self._btn_sme_save.clicked.connect(self._on_sme_save_mois)
        self._btn_sme_close = QPushButton("Clôturer le mois…")
        self._btn_sme_close.setToolTip(
            "Archives locales, mise à jour TabSme (Fermé / Ouvert), nouveau mois actif."
        )
        self._btn_sme_close.clicked.connect(self._on_sme_cloture_dialog)
        self._btn_sme_reopen = QPushButton("Réouvrir (exception)…")
        self._btn_sme_reopen.setToolTip(
            "Pour un mois TabSme au statut « fermé » : passage en « Réouvert » pour corrections "
            "(auteur et motif obligatoires, événement d'audit)."
        )
        self._btn_sme_reopen.clicked.connect(self._on_sme_reopen_dialog)
        self._btn_sme_archives = QPushButton("Dossier archives…")
        self._btn_sme_archives.setToolTip(
            "Ouvre le dossier des copies (SQLite, JSON mise en page) dans l'explorateur. "
            "Pour la liste détaillée : onglet « Archives clôture » dans la barre latérale."
        )
        self._btn_sme_archives.clicked.connect(self._on_open_sme_archives_dir)
        self._btn_sme_archives_page = QPushButton("Onglet Archives…")
        self._btn_sme_archives_page.setToolTip(
            "Affiche la page Archives clôture (liste, ouverture des fichiers)."
        )
        self._btn_sme_archives_page.clicked.connect(self._on_go_archives_page)
        self._btn_sme_import = QPushButton("Importer une base…")
        self._btn_sme_import.setToolTip(
            "Importe un fichier de base existant (SQLite ou Access) comme archive d'un mois passé : "
            "copie vers sme_archives/, TabSme Fermé, mois suivant activé."
        )
        self._btn_sme_import.clicked.connect(self._on_sme_import_dialog)
        self._btn_sme_retroactive = QPushButton("Historique rétroactif…")
        self._btn_sme_retroactive.setToolTip(
            "Compare une base de sauvegarde (mois passé) avec la base courante et injecte "
            "les changements détectés dans le Parcours professionnel de chaque fiche personnel."
        )
        self._btn_sme_retroactive.clicked.connect(self._on_sme_retroactive_diff_dialog)

        sw_actions.addWidget(self._btn_sme_save)
        sw_actions.addWidget(self._btn_sme_close)
        sw_actions.addWidget(self._btn_sme_import)
        sw_actions.addWidget(self._btn_sme_retroactive)
        sw_actions.addWidget(self._btn_sme_reopen)
        sw_actions.addWidget(self._btn_sme_archives)
        sw_actions.addWidget(self._btn_sme_archives_page)
        sw_actions.addStretch()
        sw_lay.addLayout(sw_actions)

        self._sme_fermes_label = QLabel()
        self._sme_fermes_label.setWordWrap(True)
        self._sme_fermes_label.setObjectName("dashSubtitle")
        sw_lay.addWidget(self._sme_fermes_label)

        sme_outer.addWidget(self._sme_workflow)

        sep_sme = QFrame()
        sep_sme.setFrameShape(QFrame.Shape.HLine)
        sep_sme.setFrameShadow(QFrame.Shadow.Sunken)
        sep_sme.setStyleSheet("background: #e1e4e8; max-height: 2px; border: none;")
        sme_outer.addWidget(sep_sme)

        self._sme_tabsme_heading = QLabel(
            "<b>Historique TabSme</b> — lu depuis la base (Access ou SQLite selon vos paramètres)",
            textFormat=Qt.TextFormat.RichText,
        )
        self._sme_tabsme_heading.setObjectName("dashSubtitle")
        sme_outer.addWidget(self._sme_tabsme_heading)
        sme_outer.addWidget(self._sme_hint)
        sme_outer.addWidget(self._sme_table)

        self.refresh_workflow()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _refresh_shell_period_banner(self) -> None:
        win = self.window()
        if hasattr(win, "_refresh_period_banner"):
            getattr(win, "_refresh_period_banner")()

    def _trigger_after_workflow(self) -> None:
        if self._on_after_workflow is not None:
            self._on_after_workflow()

    def _sync_sme_preview_label(self) -> None:
        ym = _widgets_to_ym(self._sme_month, self._sme_year)
        if ym:
            mo_i = int(self._sme_month.currentData() or 1)
            nom = _SME_MONTHS_FR[mo_i - 1].lower()
            self._sme_preview.setText(
                f"Période SME : {ym} ({nom} {self._sme_year.value()})"
            )
        else:
            self._sme_preview.setText("Période SME : —")

    def _refresh_sme_history_label_only(self) -> None:
        ctx = load_sme_context()
        if ctx.mois_fermes:
            tail = ctx.mois_fermes[-12:]
            fermes = ", ".join(tail)
            suffix = " …" if len(ctx.mois_fermes) > 12 else ""
            self._sme_fermes_label.setText(
                f"<b>Mois marqués comme clôturés (historique local)</b> : {fermes}{suffix}"
            )
        else:
            self._sme_fermes_label.setText(
                "<b>Mois clôturés</b> : aucun enregistré dans ce fichier."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh_workflow(self) -> None:
        """Recharge les widgets depuis SmeContext (mois actif, éditeur, etc.)."""
        ctx = load_sme_context()
        _ym_to_widgets(ctx.mois_actif, self._sme_month, self._sme_year)
        self._editeur_audit.setText(ctx.editeur_defaut or "")
        if ctx.jour_cloture is not None:
            self._sme_jour.setValue(ctx.jour_cloture)
        else:
            self._sme_jour.setValue(0)
        self._sync_sme_preview_label()
        self._refresh_sme_history_label_only()

    def update_sme_table(self, s: DashboardSnapshot) -> None:
        """Met à jour le tableau TabSme et l'historique des clôtures depuis un DashboardSnapshot."""
        if s.sme_table_ok:
            self._sme_hint.setText(
                "Historique des périodes SME enregistrées dans <b>TabSme</b>."
            )
            self._sme_table.setRowCount(len(s.sme_rows))
            for row, r in enumerate(s.sme_rows):
                self._sme_table.setItem(row, 0, QTableWidgetItem(r.codsme))
                self._sme_table.setItem(row, 1, QTableWidgetItem(r.mois))
                self._sme_table.setItem(row, 2, QTableWidgetItem(r.statut))
            self._sme_table.resizeColumnsToContents()
        else:
            self._sme_hint.setText(
                "Table <b>TabSme</b> absente ou inaccessible : créez-la dans Access "
                "(colonnes typiques : Codsme, mois, Statut), ou importez le bundle SQLite depuis Access "
                "(page <b>Paramètres</b> ou menu Fichier) pour remplir <b>tab_sme</b> en mode local."
            )
            self._sme_table.setRowCount(0)
        self._refresh_sme_history_label_only()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_save_editeur_audit(self) -> None:
        ctx = load_sme_context()
        ctx.editeur_defaut = self._editeur_audit.text().strip()
        save_sme_context(ctx)
        QMessageBox.information(
            self,
            "Audit",
            "Signataire enregistré. Les prochaines modifications de fiches incluront "
            "« edited_by » dans personnel_audit.jsonl (si le champ est renseigné).",
        )

    def _on_open_sme_archives_dir(self) -> None:
        d = sme_archives_dir().resolve()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(d))):
            QMessageBox.warning(self, "Archives", f"Impossible d'ouvrir l'explorateur. Chemin : \n{d}")

    def _on_go_archives_page(self) -> None:
        if self._navigate_to_page is not None:
            self._navigate_to_page(PAGE_ARCHIVES_CLOSURE)
        else:
            self._on_open_sme_archives_dir()

    def _on_sme_save_mois(self) -> None:
        ym = _widgets_to_ym(self._sme_month, self._sme_year)
        if not ym or extract_yyyy_mm(ym) != ym:
            QMessageBox.warning(self, "Mois SME", "Choisissez un mois et une année valides.")
            return
        ctx = load_sme_context()
        ctx.mois_actif = ym
        jv = self._sme_jour.value()
        ctx.jour_cloture = None if jv == 0 else jv
        save_sme_context(ctx)
        self.refresh_workflow()
        jour_txt = f" Jour de clôture : {jv}." if jv else ""
        QMessageBox.information(
            self,
            "Mois SME",
            f"Le mois actif « {ym} » a été enregistré.{jour_txt} Les prochaines modifications de fiches "
            "personnel l'indiqueront dans le fichier d'audit (champ sme_mois_context).",
        )
        self._refresh_shell_period_banner()

    def _on_sme_cloture_dialog(self) -> None:
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setObjectName("SmeClotureDialog")
        dlg.setWindowTitle("Clôturer un mois SME (Piste A)")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Le mois clôturé est ajouté à l'historique local, le mois suivant devient le mois actif. "
            "Une copie du <b>fichier SQLite</b> (si présent) et du <b>JSON de mise en page liste</b> "
            "est enregistrée sous <code>sme_archives</code>. Les lignes <b>TabSme</b> dont la colonne "
            "<i>mois</i> correspond au mois clôturé passent en statut <b>Fermé</b> ; celles du mois "
            "suivant en <b>Ouvert</b> (ou insertion <code>SME-YYYYMM</code> si besoin). "
            "Confirmez que la base (Access ou SQLite) est accessible en écriture pour TabSme."
        ))

        g1 = QGroupBox("Mois à clôturer")
        g1.setObjectName("dashSmeWorkflow")
        f1 = QFormLayout(g1)
        cb_c = _sme_month_combo(for_dialog=True)
        sp_c = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(ctx0.mois_actif, cb_c, sp_c)
        row_c = QHBoxLayout()
        row_c.setSpacing(10)
        row_c.addWidget(QLabel("Mois :"))
        row_c.addWidget(cb_c)
        row_c.addWidget(QLabel("Année :"))
        row_c.addWidget(sp_c)
        row_c.addStretch()
        f1.addRow(row_c)

        g2 = QGroupBox("Nouveau mois actif (après clôture)")
        g2.setObjectName("dashSmeWorkflow")
        f2 = QFormLayout(g2)
        cb_s = _sme_month_combo(for_dialog=True)
        sp_s = _sme_year_spin(for_dialog=True)
        nxt = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
        _ym_to_widgets(nxt if nxt else "", cb_s, sp_s)

        def _sync_suivant_from_cloture() -> None:
            nn = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
            if nn:
                _ym_to_widgets(nn, cb_s, sp_s)

        btn_fill = QPushButton("Remplir avec le mois suivant (automatique)")
        btn_fill.clicked.connect(_sync_suivant_from_cloture)
        row_s = QHBoxLayout()
        row_s.setSpacing(10)
        row_s.addWidget(QLabel("Mois :"))
        row_s.addWidget(cb_s)
        row_s.addWidget(QLabel("Année :"))
        row_s.addWidget(sp_s)
        row_s.addStretch()
        f2.addRow(row_s)
        f2.addRow(btn_fill)

        lay.addWidget(g1)
        lay.addWidget(g2)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cloture = _widgets_to_ym(cb_c, sp_c)
        suivant = _widgets_to_ym(cb_s, sp_s)
        if (
            not cloture or not suivant
            or extract_yyyy_mm(cloture) != cloture
            or extract_yyyy_mm(suivant) != suivant
        ):
            QMessageBox.warning(
                self, "Clôture",
                "Renseignez le mois à clôturer et le nouveau mois actif (mois et année complets).",
            )
            return
        if not confirm_yes_no(
            self,
            "Confirmer la clôture",
            f"Clôturer « {cloture} » et activer « {suivant} » ?\n\n"
            "Des copies d'archive seront créées ; TabSme sera mise à jour.",
            default_no=True,
        ):
            return
        result = run_with_progress(
            self,
            "Clôture SME",
            f"Archivage du mois « {cloture} » et mise à jour TabSme…",
            lambda: run_piste_a_month_closure(self._settings, cloture, suivant),
        )
        if result is None:
            return
        self.refresh_workflow()
        self._trigger_after_workflow()
        parts = [
            f"Mois clôturé : « {cloture} ». Mois actif : « {suivant} ».",
            f"TabSme : {result.tabsme_rows_closed} ligne(s) → Fermé, "
            f"{result.tabsme_rows_opened} → Ouvert"
            + (" (dont 1 insertion auto)" if result.tabsme_inserted_suivant else "")
            + ".",
        ]
        if result.sqlite_archive:
            parts.append(f"Archive SQLite : {result.sqlite_archive}")
        if result.layout_archive:
            parts.append(f"Archive mise en page : {result.layout_archive}")
        if result.warnings:
            parts.append("Avertissements :\n• " + "\n• ".join(result.warnings))
        QMessageBox.information(self, "Clôture terminée", "\n\n".join(parts))
        self._refresh_shell_period_banner()

    def _on_sme_import_dialog(self) -> None:
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setWindowTitle("Importer une base existante pour un mois passé")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)

        info = QLabel(
            "Sélectionnez le <b>fichier de base</b> (.sqlite ou .mdb) correspondant au mois à archiver. "
            "L'application copiera ce fichier dans <code>sme_archives/</code>, marquera le mois comme "
            "<b>Fermé</b> dans TabSme, et l'ajoutera à l'historique. "
            "Cochez « Activer le mois suivant » pour démarrer automatiquement le mois suivant."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        g_file = QGroupBox("Fichier de base à importer")
        fl = QHBoxLayout(g_file)
        self._import_file_edit = QLineEdit()
        self._import_file_edit.setPlaceholderText("Chemin vers le fichier .sqlite ou .mdb…")
        self._import_file_edit.setReadOnly(True)
        btn_browse = QPushButton("Parcourir…")

        def _browse() -> None:
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Sélectionner la base de données", "",
                "Base de données (*.sqlite *.db *.mdb *.accdb);;Tous les fichiers (*)",
            )
            if path:
                self._import_file_edit.setText(path)

        btn_browse.clicked.connect(_browse)
        fl.addWidget(self._import_file_edit, stretch=1)
        fl.addWidget(btn_browse)
        lay.addWidget(g_file)

        g_mois = QGroupBox("Mois correspondant (à archiver comme « Fermé »)")
        ml = QVBoxLayout(g_mois)
        row_c = QHBoxLayout()
        row_c.addWidget(QLabel("Mois :"))
        cb_c = _sme_month_combo(for_dialog=True)
        sp_c = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(ctx0.mois_actif or "", cb_c, sp_c)
        row_c.addWidget(cb_c)
        row_c.addWidget(QLabel("Année :"))
        row_c.addWidget(sp_c)
        row_c.addStretch()
        ml.addLayout(row_c)
        lay.addWidget(g_mois)

        chk_suivant = QCheckBox("Activer le mois suivant après import")
        chk_suivant.setChecked(True)
        lay.addWidget(chk_suivant)

        g_suivant = QGroupBox("Mois suivant à activer")
        sl2 = QVBoxLayout(g_suivant)
        row_s = QHBoxLayout()
        row_s.addWidget(QLabel("Mois :"))
        cb_s = _sme_month_combo(for_dialog=True)
        sp_s = _sme_year_spin(for_dialog=True)
        _ym_to_widgets("", cb_s, sp_s)
        row_s.addWidget(cb_s)
        row_s.addWidget(QLabel("Année :"))
        row_s.addWidget(sp_s)
        row_s.addStretch()
        sl2.addLayout(row_s)

        btn_auto = QPushButton("Remplir automatiquement (mois + 1)")

        def _fill_suivant() -> None:
            nn = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
            if nn:
                _ym_to_widgets(nn, cb_s, sp_s)

        btn_auto.clicked.connect(_fill_suivant)
        sl2.addWidget(btn_auto)
        lay.addWidget(g_suivant)

        def _toggle_suivant(checked: bool) -> None:
            g_suivant.setEnabled(checked)

        chk_suivant.toggled.connect(_toggle_suivant)
        _fill_suivant()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Importer")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        source = self._import_file_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "Import", "Aucun fichier sélectionné.")
            return
        mois = _widgets_to_ym(cb_c, sp_c)
        if not mois or extract_yyyy_mm(mois) != mois:
            QMessageBox.warning(self, "Import", "Mois invalide.")
            return
        mois_suivant: str | None = None
        if chk_suivant.isChecked():
            ms = _widgets_to_ym(cb_s, sp_s)
            if extract_yyyy_mm(ms) == ms:
                mois_suivant = ms

        if not confirm_yes_no(
            self, "Confirmer l'import",
            f"Importer « {Path(source).name} » comme archive du mois « {mois} » ?\n\n"
            + (f"Le mois suivant « {mois_suivant} » sera activé.\n\n" if mois_suivant else "")
            + "TabSme sera mis à jour. Cette action est consignée dans l'audit.",
            default_no=True,
        ):
            return

        result = run_with_progress(
            self,
            "Import base",
            f"Import de « {Path(source).name} » pour le mois « {mois} »…",
            lambda: import_existing_db_for_month(self._settings, Path(source), mois, mois_suivant),
        )
        if result is None:
            return

        self.refresh_workflow()
        self._trigger_after_workflow()

        parts = [f"Base importée pour le mois « {mois} »."]
        if result.sqlite_archive:
            parts.append(f"Archive : {result.sqlite_archive}")
        parts.append(
            f"TabSme : {result.tabsme_rows_closed} ligne(s) → Fermé, "
            f"{result.tabsme_rows_opened} → Ouvert"
            + (" (dont 1 insertion auto)" if result.tabsme_inserted_suivant else "")
            + "."
        )
        if mois_suivant:
            parts.append(f"Mois actif désormais : « {mois_suivant} ».")
        if result.warnings:
            parts.append("Avertissements :\n• " + "\n• ".join(result.warnings))
        QMessageBox.information(self, "Import terminé", "\n\n".join(parts))
        self._refresh_shell_period_banner()

    def _on_sme_retroactive_diff_dialog(self) -> None:
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setWindowTitle("Historique rétroactif — diff par personne")
        dlg.setMinimumWidth(600)
        lay = QVBoxLayout(dlg)

        info = QLabel(
            "Sélectionnez un <b>fichier de base de sauvegarde</b> d'un mois passé "
            "(.sqlite, .db, .mdb ou .accdb). "
            "L'application compare chaque personne (par matricule militaire) avec la base courante "
            "et injecte les changements détectés comme entrées d'audit datées au dernier jour du mois source. "
            "Ces entrées apparaissent dans le <b>Parcours professionnel</b> de chaque fiche."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        g_file = QGroupBox("Base de sauvegarde (mois passé)")
        fl = QHBoxLayout(g_file)
        file_edit = QLineEdit()
        file_edit.setPlaceholderText("Chemin vers le fichier .sqlite, .db, .mdb ou .accdb…")
        file_edit.setReadOnly(True)
        btn_browse = QPushButton("Parcourir…")

        def _browse() -> None:
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Sélectionner la base source", "",
                "Base de données (*.sqlite *.db *.mdb *.accdb);;Tous les fichiers (*)",
            )
            if path:
                file_edit.setText(path)

        btn_browse.clicked.connect(_browse)
        fl.addWidget(file_edit, stretch=1)
        fl.addWidget(btn_browse)
        lay.addWidget(g_file)

        g_mois = QGroupBox("Mois de la base source")
        ml = QVBoxLayout(g_mois)
        row_m = QHBoxLayout()
        row_m.addWidget(QLabel("Mois :"))
        cb_m = _sme_month_combo(for_dialog=True)
        sp_m = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(ctx0.mois_actif or "", cb_m, sp_m)
        row_m.addWidget(cb_m)
        row_m.addWidget(QLabel("Année :"))
        row_m.addWidget(sp_m)
        row_m.addStretch()
        ml.addLayout(row_m)
        lay.addWidget(g_mois)

        g_opts = QGroupBox("Options")
        ol = QVBoxLayout(g_opts)
        chk_career = QCheckBox(
            "Champs Parcours uniquement (grade, structure, fonction, position, subdivision, dates, décision)"
        )
        chk_career.setChecked(True)
        ol.addWidget(chk_career)
        lay.addWidget(g_opts)

        preview_label = QLabel()
        preview_label.setWordWrap(True)
        preview_label.setObjectName("dashSubtitle")
        preview_label.setVisible(False)
        lay.addWidget(preview_label)

        btn_row_lay = QHBoxLayout()
        btn_preview = QPushButton("Prévisualiser (sans écrire)")
        btn_preview.setToolTip("Calcule les diffs sans créer d'entrées d'audit.")
        btn_import = QPushButton("Importer l'historique")
        btn_import.setToolTip("Crée les entrées d'audit dans personnel_audit.jsonl.")
        btn_cancel = QPushButton("Fermer")
        btn_row_lay.addWidget(btn_preview)
        btn_row_lay.addWidget(btn_import)
        btn_row_lay.addStretch()
        btn_row_lay.addWidget(btn_cancel)
        lay.addLayout(btn_row_lay)

        btn_cancel.clicked.connect(dlg.reject)

        def _get_params() -> tuple[Path | None, str | None]:
            src = file_edit.text().strip()
            if not src:
                QMessageBox.warning(dlg, "Diff rétroactif", "Aucun fichier sélectionné.")
                return None, None
            mois = _widgets_to_ym(cb_m, sp_m)
            if not mois or extract_yyyy_mm(mois) != mois:
                QMessageBox.warning(dlg, "Diff rétroactif", "Mois invalide.")
                return None, None
            return Path(src), mois

        def _run(dry_run: bool) -> None:
            src, mois = _get_params()
            if src is None or mois is None:
                return
            career_only = chk_career.isChecked()
            result = run_with_progress(
                dlg,
                "Diff rétroactif",
                "Comparaison des bases en cours…",
                lambda: run_retroactive_diff(
                    self._settings, src, mois, career_only=career_only, dry_run=dry_run,
                ),
            )
            if result is None:
                return
            lines = result.summary_lines(mois)
            if dry_run:
                lines.insert(0, "PRÉVISUALISATION — aucune écriture effectuée.")
                preview_label.setText("\n".join(lines))
                preview_label.setVisible(True)
            else:
                QMessageBox.information(dlg, "Historique rétroactif importé", "\n\n".join(lines))
                dlg.accept()

        btn_preview.clicked.connect(lambda: _run(dry_run=True))
        btn_import.clicked.connect(lambda: _run(dry_run=False))

        dlg.exec()

    def _on_sme_reopen_dialog(self) -> None:
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setWindowTitle("Réouverture exceptionnelle d'un mois clôturé")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Les lignes <b>TabSme</b> du mois choisi dont le statut est « fermé » passent en "
            "<b>Réouvert</b> pour permettre des corrections. Un événement "
            "<code>sme_reopen_exception</code> est ajouté à l'audit. "
            "<b>Auteur</b> et <b>motif</b> sont obligatoires."
        ))
        g = QGroupBox("Mois à réouvrir")
        f = QFormLayout(g)
        cb = _sme_month_combo(for_dialog=True)
        sp = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(ctx0.mois_actif, cb, sp)
        row_m = QHBoxLayout()
        row_m.addWidget(QLabel("Mois :"))
        row_m.addWidget(cb)
        row_m.addWidget(QLabel("Année :"))
        row_m.addWidget(sp)
        row_m.addStretch()
        f.addRow(row_m)
        auteur = QLineEdit()
        auteur.setText(ctx0.editeur_defaut or "")
        auteur.setPlaceholderText("Poste, initiales ou nom")
        motif = QLineEdit()
        motif.setPlaceholderText("Motif administratif de la réouverture")
        f.addRow("Auteur *", auteur)
        f.addRow("Motif *", motif)
        lay.addWidget(g)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ym = _widgets_to_ym(cb, sp)
        if not ym or extract_yyyy_mm(ym) != ym:
            QMessageBox.warning(self, "Réouverture", "Mois et année invalides.")
            return
        if not confirm_yes_no(
            self, "Confirmer la réouverture",
            f"Réouvrir les périodes « fermées » correspondant à « {ym} » dans TabSme ?",
            default_no=True,
        ):
            return
        try:
            result = reopen_closed_month_for_corrections(
                self._settings, ym, auteur.text(), motif.text(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Réouverture", str(e))
            return
        msg = f"Lignes TabSme passées en « Réouvert » : {result.rows_updated}."
        if result.warnings:
            msg += "\n\n" + "\n".join(result.warnings)
        QMessageBox.information(self, "Réouverture", msg)
        self._trigger_after_workflow()
        self._refresh_shell_period_banner()
