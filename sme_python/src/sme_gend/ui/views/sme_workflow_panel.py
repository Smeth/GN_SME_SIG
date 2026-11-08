"""Panneau de gestion de la période SME (workflow, clôture, réouverture, historique périodes SME)."""

from __future__ import annotations

from sme_gend.config.ui_labels import IMPORT_MONTH_ACCESS_ACTION

from collections.abc import Callable
from datetime import date
from dataclasses import asdict
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
    QGridLayout,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.dashboard_service import DashboardSnapshot
from sme_gend.services.sme_editing_policy import reopen_closed_month_for_corrections
from sme_gend.services.pre_closure_service import finalize_pre_closure_report, run_pre_closure_checks
from sme_gend.services.sme_month_closure import (
    canonical_sme_mois_yyyy_mm,
    extract_yyyy_mm,
    run_piste_a_month_closure,
    sme_archives_dir,
)
from sme_gend.services.workflow_permissions import session_can_perform_action
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.bg_task import run_with_determinate_progress
from sme_gend.services.search_text import search_values, normalize_search_text
from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack, confirm_yes_no
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


def _finalize_workflow_tracking(settings, report, host) -> None:
    try:
        finalize_pre_closure_report(settings, report)
    except Exception:
        QMessageBox.warning(
            host, "Tâches workflow",
            "L'enregistrement des tâches de suivi n'a pas pu être confirmé. "
            "Vérifiez leur état avant de recommencer. Les contrôles de clôture restent disponibles.",
        )


class SmeWorkflowPanel(QFrame):
    """Panneau SME : sélecteur de période, boutons workflow, historique périodes SME."""

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
        self._sme_table.setHorizontalHeaderLabels(["Référence", "Mois / période", "Statut"])
        self._sme_table.horizontalHeader().setStretchLastSection(True)
        self._sme_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sme_table.setAlternatingRowColors(True)
        self._sme_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sme_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._period_rows = []

        self._sme_workflow = QGroupBox("Actions sur le mois de travail")
        self._sme_workflow.setObjectName("dashSmeWorkflow")
        sw_lay = QVBoxLayout(self._sme_workflow)
        sw_lay.setSpacing(8)
        self._cycle_mode = None
        mode_bar = QHBoxLayout()
        self._mode_label = QLabel("Mode de clôture : à charger")
        self._btn_document_mode = QPushButton("Activer le cycle documentaire…")
        self._btn_document_mode.clicked.connect(self._activate_document_mode)
        self._btn_publications = QPushButton("Versions et validations SME…")
        self._btn_publications.clicked.connect(self._open_publications)
        mode_bar.addWidget(self._mode_label, 1)
        mode_bar.addWidget(self._btn_publications)
        mode_bar.addWidget(self._btn_document_mode)
        sw_lay.addLayout(mode_bar)

        row_primary = QGridLayout()
        row_primary.setSpacing(8)
        self._btn_sme_close = QPushButton("Clôturer le mois…")
        self._btn_sme_close.setObjectName("dashRefresh")
        self._btn_sme_close.setToolTip(
            "Contrôle et archive le mois de travail, puis ouvre le mois suivant. La confirmation indique précisément les deux mois."
        )
        self._btn_sme_close.clicked.connect(self._on_sme_cloture_dialog)
        self._btn_sme_reopen = QPushButton("Rouvrir une période…")
        self._btn_sme_reopen.setToolTip("Corrections après clôture — motif obligatoire.")
        self._btn_sme_reopen.clicked.connect(self._on_sme_reopen_dialog)
        self._btn_sme_archives = QPushButton("Dossier archives…")
        self._btn_sme_archives.clicked.connect(self._on_open_sme_archives_dir)
        self._btn_sme_archives_page = QPushButton("Consulter les fichiers d’archive")
        self._btn_sme_archives_page.clicked.connect(self._on_go_archives_page)
        row_primary.addWidget(self._btn_sme_close, 0, 0)
        row_primary.addWidget(self._btn_sme_reopen, 0, 1)
        row_primary.addWidget(self._btn_sme_archives, 1, 0)
        row_primary.addWidget(self._btn_sme_archives_page, 1, 1)
        self._btn_sme_fill = QPushButton("Remplir un mois à partir d’un autre…")
        self._btn_sme_fill.setToolTip(
            "Reprend les effectifs dans un mois vide, sans import ni clôture du mois source. Aperçu avant enregistrement."
        )
        self._btn_sme_fill.clicked.connect(self._on_fill_month_dialog)
        row_primary.addWidget(self._btn_sme_fill, 2, 0, 1, 2)
        sw_lay.addLayout(row_primary)

        row_import = QHBoxLayout()
        row_import.setSpacing(8)
        self._btn_sme_export_access = QPushButton("Exporter MySQL → Access…")
        self._btn_sme_export_access.clicked.connect(self._on_sme_export_access_dialog)
        self._btn_sme_import = QPushButton(IMPORT_MONTH_ACCESS_ACTION)
        self._btn_sme_import.setToolTip(
            "Charge liste, carrière et satellites depuis une archive .mdb. "
            "Archivage et clôture du mois optionnels (case à cocher dans l'assistant)."
        )
        self._btn_sme_import.clicked.connect(self._on_sme_import_dialog)
        row_import.addWidget(self._btn_sme_export_access)
        row_import.addWidget(self._btn_sme_import)
        row_import.addStretch()
        sw_lay.addLayout(row_import)

        sme_outer.addWidget(self._sme_workflow)

        sep_sme = QFrame()
        sep_sme.setFrameShape(QFrame.Shape.HLine)
        sep_sme.setFrameShadow(QFrame.Shadow.Sunken)
        self._sep_sme = sep_sme
        sme_outer.addWidget(sep_sme)

        self._sme_tabsme_heading = QLabel("Historique des périodes SME")
        self._sme_tabsme_heading.setObjectName("dashSubtitle")
        sme_outer.addWidget(self._sme_tabsme_heading)
        filters = QHBoxLayout()
        self._period_search = QLineEdit()
        self._period_search.setPlaceholderText("Rechercher une période…")
        self._period_search.setClearButtonEnabled(True)
        self._period_filter = QComboBox()
        for label, value in (("Tous les statuts", ""), ("Ouvert", "ouvert"), ("Fermé", "ferme"), ("Réouvert", "reouvert")):
            self._period_filter.addItem(label, value)
        self._btn_consult_period = QPushButton("Consulter la période sélectionnée")
        self._btn_consult_period.clicked.connect(self._consult_period)
        filters.addWidget(self._period_search, 1)
        filters.addWidget(self._period_filter)
        filters.addWidget(self._btn_consult_period)
        self._btn_period_record = QPushButton("Dossier de clôture…")
        self._btn_period_record.setEnabled(False)
        self._btn_period_record.clicked.connect(self._open_period_record)
        filters.addWidget(self._btn_period_record)
        sme_outer.addLayout(filters)
        self._period_search.textChanged.connect(self._fill_periods)
        self._period_filter.currentIndexChanged.connect(self._fill_periods)
        self._sme_table.itemSelectionChanged.connect(self._period_selection_changed)
        self._sme_table.cellDoubleClicked.connect(lambda *_: self._consult_period())
        sme_outer.addWidget(self._sme_hint)
        sme_outer.addWidget(self._sme_table)

        self._apply_workflow_permissions()
        self._period_selection_changed()
        self.refresh_theme_styles()

    def refresh_theme_styles(self) -> None:
        from sme_gend.ui.theme import theme_palette

        pal = theme_palette()
        sep = getattr(self, "_sep_sme", None)
        if sep is not None:
            sep.setStyleSheet(
                f"background: {pal['border']}; max-height: 2px; border: none;"
            )

    def _apply_workflow_permissions(self) -> None:
        from sme_gend.services.auth_guard import session_has_permission, session_has_admin_role
        from sme_gend.services.month_fill import can_fill_month
        self._btn_sme_fill.setEnabled(can_fill_month())
        self._btn_publications.setEnabled(session_has_permission("publications.view"))
        self._btn_document_mode.setEnabled(session_has_admin_role() and session_has_permission("workflow.cycle_mode") and self._cycle_mode != "publications")
        can_close = session_can_perform_action("workflow.close_month")
        if self._cycle_mode == "publications":
            can_close = can_close and session_has_permission("publications.view") and session_has_permission("publications.verify")
        self._btn_sme_close.setEnabled(can_close)
        self._btn_sme_reopen.setEnabled(session_can_perform_action("workflow.reopen_month"))
        self._btn_sme_import.setEnabled(session_can_perform_action("workflow.import_archive"))
        self._btn_sme_export_access.setEnabled(
            session_can_perform_action("workflow.import_archive")
        )
        self._btn_sme_archives.setEnabled(session_has_permission("system.archives"))
        self._btn_sme_archives_page.setEnabled(session_has_permission("system.archives"))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _on_fill_month_dialog(self) -> None:
        from sme_gend.ui.month_fill_dialog import run_month_fill_dialog
        if run_month_fill_dialog(self, self._settings):
            self._trigger_after_workflow()

    def _refresh_shell_period_banner(self) -> None:
        """Rebinde la source de données du mois actif et rafraîchit toute l'UI."""
        win = self.window()
        if hasattr(win, "refresh_for_month_change"):
            getattr(win, "refresh_for_month_change")()
        elif hasattr(win, "_refresh_period_banner"):
            getattr(win, "_refresh_period_banner")()

    def _trigger_after_workflow(self) -> None:
        if self._on_after_workflow is not None:
            self._on_after_workflow()

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh_workflow(self) -> None:
        """Actualise les droits des actions, sans formulaire d'identité manuelle."""
        self._apply_workflow_permissions()

    def update_sme_table(self, s: DashboardSnapshot) -> None:
        """Met à jour le tableau périodes SME depuis un DashboardSnapshot."""
        from sme_gend.services.dashboard_snapshot_compat import coerce_dashboard_snapshot

        mode = getattr(s, "document_mode", None)
        if mode is not None:
            self._cycle_mode = mode
            self._mode_label.setText("Mode : cycle documentaire" if mode == "publications" else "Mode : cycle historique avec archives")
            self._btn_document_mode.setVisible(mode != "publications")
            self._apply_workflow_permissions()
            self._btn_sme_close.setText("Clôturer avec la version validée…" if mode == "publications" else "Clôturer le mois…")
        s = coerce_dashboard_snapshot(s)
        if s.sme_table_ok:
            self._period_rows = list(s.sme_rows)
            self._fill_periods()
        else:
            self._period_rows = []
            self._fill_periods()
            self._sme_hint.setText("Historique indisponible. Vérifiez la connexion puis actualisez.")

    def _fill_periods(self, *_):
        selected = self._selected_period()
        state = self._period_filter.currentData()
        rows = [row for row in self._period_rows
                if (not state or normalize_search_text(row.statut) == state)
                and search_values(self._period_search.text(), row.codsme, row.mois, row.statut)]
        self._sme_table.setRowCount(len(rows))
        self._sme_table.clearSelection()
        self._sme_table.setCurrentCell(-1, -1)
        for index, row in enumerate(rows):
            for col, value in enumerate((row.codsme, row.mois, row.statut)):
                item = QTableWidgetItem(str(value or "—"))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, canonical_sme_mois_yyyy_mm(row.mois))
                self._sme_table.setItem(index, col, item)
            if canonical_sme_mois_yyyy_mm(row.mois) == selected:
                self._sme_table.selectRow(index)
        self._sme_table.resizeColumnsToContents()
        self._sme_hint.setText(f"{len(rows)} période(s) affichée(s) sur {len(self._period_rows)}." if self._period_rows else "Aucune période enregistrée.")
        self._period_selection_changed()

    def _selected_period(self):
        row = self._sme_table.currentRow()
        item = self._sme_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _period_selection_changed(self):
        from sme_gend.services.auth_guard import session_has_permission
        self._btn_consult_period.setEnabled(bool(self._selected_period()) and session_has_permission("workflow.pilotage"))
        self._btn_period_record.setEnabled(bool(self._selected_period()))

    def _consult_period(self):
        from sme_gend.services.auth_guard import require_permission
        month = self._selected_period()
        if not month or not require_permission(self, "workflow.pilotage"):
            return
        window = self.window()
        if hasattr(window, "_activate_period"):
            window._activate_period(month)
        else:
            QMessageBox.information(self, "Consulter une période", "Utilisez Changer de période dans la fenêtre principale pour consulter ce mois.")

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._period_rows = []
        self._fill_periods()

    def _open_period_record(self):
        month = self._selected_period()
        if not month:
            QMessageBox.information(self, "Dossier de clôture", "Sélectionnez une période.")
            return
        from sme_gend.ui.closure_record_dialog import ClosureRecordDialog
        ClosureRecordDialog(self, self._settings, month).exec()

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_open_sme_archives_dir(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "system.archives"):
            return
        d = sme_archives_dir().resolve()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(d))):
            QMessageBox.warning(self, "Archives", f"Impossible d'ouvrir l'explorateur. Chemin : \n{d}")

    def _on_go_archives_page(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "system.archives"):
            return
        if hasattr(self.window(), "_cycle_archives_view"):
            self.window()._cycle_archives_view.show_archives_tab()
            return
        if self._navigate_to_page is not None:
            self._navigate_to_page(PAGE_ARCHIVES_CLOSURE)
        else:
            self._on_open_sme_archives_dir()

    def _on_sme_cloture_dialog(self) -> None:
        self.open_closure_dialog()

    def _open_publications(self) -> None:
        from sme_gend.ui.publication_registry_dialog import open_publication_registry
        open_publication_registry(self, self._settings, month=self._selected_period())

    def _document_context(self, host):
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService
        from sme_gend.services.sme_publication_registry import list_publications
        def work():
            snapshot = SmeWorkspaceService(self._settings).load_active_month_snapshot()
            if not snapshot.active_month:
                raise ValueError("Définissez un mois de travail unique avant de continuer.")
            versions = list_publications(self._settings, mois=snapshot.active_month, status="valide", limit=2)
            if len(versions) != 1:
                raise ValueError("Générez puis validez une version SME du mois de travail dans « Versions et validations SME ».")
            return snapshot, versions[0]
        return run_with_progress(host, "Cycle documentaire", "Lecture du mois de travail et de sa version validée…", work)

    def _activate_document_mode(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "workflow.cycle_mode"):
            return
        context = self._document_context(self)
        if context is None:
            return
        snapshot, publication = context
        dialog = QDialog(self)
        dialog.setWindowTitle("Activer le cycle documentaire")
        dialog.setMinimumWidth(540)
        layout = QVBoxLayout(dialog)
        message = QLabel(f"Mois de travail : {snapshot.active_month} · version validée {publication.revision_no}.\n"
            "Les prochaines clôtures s’appuieront sur une version SME validée. Le mois suivant sera projeté "
            "depuis la situation et les événements datés ; les anciennes archives restent conservées.")
        message.setWordWrap(True)
        layout.addWidget(message)
        clients_updated = QCheckBox("Tous les postes utilisent la version 0.2 ou ultérieure et les anciens exécutables sont fermés")
        clients_updated.setObjectName("clientsUpdatedConfirmation")
        clients_updated.setToolTip("N’utilisez pas simultanément les anciens exécutables 0.1.x et le nouveau cycle documentaire.")
        layout.addWidget(clients_updated)
        reason = QLineEdit()
        reason.setPlaceholderText("Motif de l’activation *")
        layout.addWidget(reason)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Activer ce cycle")
        def accept():
            if not clients_updated.isChecked():
                QMessageBox.warning(dialog, "Mise à jour des postes obligatoire", "Mettez tous les postes à jour et fermez les anciens exécutables avant d’activer ce cycle.")
                return
            if not reason.text().strip():
                QMessageBox.warning(dialog, "Motif obligatoire", "Indiquez le motif d’activation.")
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from sme_gend.services.document_month_cycle import activate_document_cycle
        result = run_with_progress(self, "Activation du cycle", "Contrôle de la version validée et activation…",
            lambda: activate_document_cycle(self._settings, expected_month=snapshot, publication_id=publication.id,
                expected_publication_version=publication.version, reason=reason.text(),
                clients_updated_confirmed=clients_updated.isChecked()))
        if result is not None:
            self._cycle_mode = result.mode
            self._mode_label.setText("Mode : cycle documentaire")
            self._btn_document_mode.hide()
            self._trigger_after_workflow()
            if result.operation_warning:
                QMessageBox.warning(self, "Cycle activé", result.operation_warning)

    def _open_document_closure(self, host):
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(host, "workflow.close_month"):
            return
        context = self._document_context(host)
        if context is None:
            return
        snapshot, publication = context
        source = snapshot.active_month
        target = _next_yyyy_mm(source)
        if not confirm_destructive_with_ack(host, "Clôture documentaire",
            f"Clôturer {source} avec sa version SME validée {publication.revision_no} et ouvrir {target} ?\n\n"
            "Les données du mois et les fichiers validés seront contrôlés avant la clôture. "
            "Le mois suivant recevra la situation projetée ; une période déjà remplie ou archivée ne sera pas remplacée.",
            ack_label="J’ai vérifié la version validée du mois", confirm_label="Clôturer et ouvrir le mois suivant"):
            return
        from sme_gend.services.document_month_cycle import close_document_month
        result = run_with_progress(host, "Clôture documentaire", "Contrôle de la version et préparation du mois suivant…",
            lambda: close_document_month(self._settings, source, target, expected_month=snapshot,
                publication_id=publication.id, expected_publication_version=publication.version))
        if result is None:
            return
        self._trigger_after_workflow()
        self._refresh_shell_period_banner()
        message = (f"{source} clôturé · {target} ouvert.\n"
                   f"{result.projected_agents} agent(s) projeté(s). Version validée conservée : {result.revision_id}.")
        if result.operation_warning:
            QMessageBox.warning(host, "Mois clôturé", message + "\n\n" + result.operation_warning)
        else:
            QMessageBox.information(host, "Mois clôturé", message)

    def open_closure_dialog(self, parent: QWidget | None = None) -> None:
        """Assistant clôture + activation du mois suivant (Pilotage ou raccourci sidebar)."""
        host = parent or self
        if not session_can_perform_action("workflow.close_month"):
            QMessageBox.warning(host, "Droits", "Vous n'avez pas la permission de clôturer un mois.")
            return
        from sme_gend.services.document_month_cycle import get_cycle_mode
        mode = run_with_progress(host, "Cycle mensuel", "Vérification du mode de clôture…",
                                 lambda: get_cycle_mode(self._settings))
        if mode is None:
            return
        if mode == "publications":
            self._open_document_closure(host)
            return
        ctx0 = load_sme_context()
        if ctx0._month_snapshot is None:
            QMessageBox.warning(host,"Clôture","Rechargez les périodes avant de préparer une clôture.")
            return
        dlg = QDialog(host)
        dlg.setObjectName("SmeClotureDialog")
        dlg.setWindowTitle("Clôturer un mois SME")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        intro = QLabel(
            "Cycle historique : conserve la référence Access configurée et une sauvegarde SQL de sécurité avant transition, "
            "puis ferme le mois et ouvre le suivant. La référence Access ne représente pas nécessairement l’état MySQL du mois."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

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
        cb_c.currentIndexChanged.connect(_sync_suivant_from_cloture)
        sp_c.valueChanged.connect(_sync_suivant_from_cloture)
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
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Vérifier les contrôles")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
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
                host, "Clôture",
                "Renseignez le mois à clôturer et le nouveau mois actif (mois et année complets).",
            )
            return
        settings = self._settings
        report = run_with_progress(host, "Contrôles avant clôture", "Vérification des données et des périodes…",
                                   lambda: run_pre_closure_checks(settings, cloture, suivant))
        if report is None:
            return
        lines = [f"• [{ {'ok': 'Conforme', 'warn': 'À vérifier', 'block': 'Bloquant'}.get(c.level, c.level) }] {c.title} : {c.detail}" for c in report.checks]
        pre_msg = "Contrôles avant clôture :\n\n" + "\n".join(lines)
        if report.blocking:
            QMessageBox.critical(
                host,
                "Clôture impossible",
                pre_msg + "\n\nCorrigez les points bloquants (niveau BLOCK).",
            )
            _finalize_workflow_tracking(self._settings, report, host)
            return
        _finalize_workflow_tracking(self._settings, report, host)
        if not confirm_destructive_with_ack(
            host,
            "Confirmer la clôture",
            pre_msg + "\n\n"
            f"Clôturer « {cloture} » et activer « {suivant} » ?\n\n"
            "Archive de mise en page, sauvegarde SQL de sécurité avant transition "
            "et référence source Access si configurée ; "
            "mise à jour sme_periode (Fermé / Ouvert) en MySQL.",
            ack_label="J'ai vérifié les contrôles ci-dessus",
            confirm_label="Clôturer le mois",
        ):
            return
        result = run_with_progress(
            host,
            "Clôture SME",
            f"Archivage du mois « {cloture} » et mise à jour périodes SME…",
            lambda: run_piste_a_month_closure(self._settings, cloture, suivant, expected=ctx0._month_snapshot,
                                            precheck_report=asdict(report)),
        )
        if result is None:
            return
        self.refresh_workflow()
        self._trigger_after_workflow()
        parts = [
            f"Mois clôturé : « {cloture} ». Mois actif : « {suivant} ».",
            f"périodes SME : {result.tabsme_rows_closed} ligne(s) → Fermé, "
            f"{result.tabsme_rows_opened} → Ouvert"
            + (" (dont 1 insertion auto)" if result.tabsme_inserted_suivant else "")
            + ".",
        ]
        if result.data_archive:
            parts.append(f"Référence source Access conservée : {result.data_archive}")
        if result.layout_archive:
            parts.append(f"Archive mise en page : {result.layout_archive}")
        if result.mysql_archive:
            parts.append(f"Sauvegarde MySQL : {result.mysql_archive}")
        if result.warnings:
            parts.append("Avertissements :\n• " + "\n• ".join(result.warnings))
        QMessageBox.information(host, "Clôture terminée", "\n\n".join(parts))
        self._refresh_shell_period_banner()

    def _on_sme_export_access_dialog(self) -> None:
        if not session_can_perform_action("workflow.import_archive"):
            QMessageBox.warning(
                self,
                "Droits",
                "Vous n'avez pas la permission d'exporter un bundle Access.",
            )
            return

        from sme_gend.services.export_report import (
            default_export_directory_hint,
            format_export_result_html,
        )
        from sme_gend.services.mysql_bundle_export_to_access import export_mysql_bundle_to_access

        dlg = QDialog(self)
        dlg.setWindowTitle("Exporter bundle Access (MySQL → .mdb)")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)

        info = QLabel(
            "<b>Export inverse</b> — reconstruit un fichier Access complet depuis MySQL "
            "pour alimenter temporairement l'ancien SME Access.<br><br>"
            "Contenu : ListeGenerale (effectif du mois choisi), référentiels, TabSme, "
            "missions, malades, désertions, stages, gains/pertes, congés (si TabConge existe).<br>"
            "<b>MySQL reste la source de vérité</b> — n'éditez pas le .mdb exporté en parallèle."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        g_mois = QGroupBox("Mois à exporter")
        mois_lay = QHBoxLayout(g_mois)
        exp_month = _sme_month_combo(for_dialog=True)
        exp_year = _sme_year_spin(for_dialog=True)
        ctx = load_sme_context()
        _ym_to_widgets(ctx.mois_actif, exp_month, exp_year)
        mois_lay.addWidget(QLabel("Mois"))
        mois_lay.addWidget(exp_month)
        mois_lay.addWidget(QLabel("Année"))
        mois_lay.addWidget(exp_year)
        mois_lay.addStretch()
        lay.addWidget(g_mois)

        g_dir = QGroupBox("Dossier d'exportation")
        dir_lay = QHBoxLayout(g_dir)
        dir_edit = QLineEdit()
        dir_edit.setPlaceholderText("Dossier de destination du fichier .mdb…")
        default_dir = default_export_directory_hint()
        dir_edit.setText(default_dir)
        btn_browse = QPushButton("Parcourir…")

        def _pick_dir() -> None:
            start = dir_edit.text().strip() or default_dir
            chosen = QFileDialog.getExistingDirectory(
                dlg,
                "Choisir le dossier d'exportation Access",
                start,
            )
            if chosen:
                dir_edit.setText(chosen)

        btn_browse.clicked.connect(_pick_dir)
        dir_lay.addWidget(dir_edit, stretch=1)
        dir_lay.addWidget(btn_browse)
        lay.addWidget(g_dir)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Exporter")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        ym = _widgets_to_ym(exp_month, exp_year)
        if not ym or extract_yyyy_mm(ym) != ym:
            QMessageBox.warning(self, "Export Access", "Choisissez un mois et une année valides.")
            return

        export_dir_text = dir_edit.text().strip()
        if not export_dir_text:
            QMessageBox.warning(self, "Export Access", "Indiquez le dossier d'exportation.")
            return

        export_dir = Path(export_dir_text).expanduser()

        def _task(report) -> dict:
            report(f"Export bundle Access pour {ym}…")
            result = export_mysql_bundle_to_access(
                self._settings,
                mois=ym,
                export_dir=export_dir,
                progress=report,
            )
            return result.to_dict()

        try:
            payload = run_with_determinate_progress(self, f"Export Access — {ym}", 5, _task)
        except FileNotFoundError as e:
            QMessageBox.critical(
                self,
                "Export Access",
                f"{e}\n\nVérifiez que smegend.mdb est présent dans le dossier modeles/.",
            )
            return
        except FileExistsError as e:
            QMessageBox.warning(self, "Export Access", str(e))
            return
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Access",
                f"Échec de l'export :\n{e}\n\n"
                "Vérifiez : pilote ODBC Access 64 bits, connexion MySQL, droits d'écriture.",
            )
            return

        if payload is None:
            return
        out_path = Path(str(payload.get("output_path") or ""))
        msg = QMessageBox(self)
        msg.setWindowTitle("Export Access terminé")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(format_export_result_html(payload))
        open_btn = msg.addButton("Ouvrir le dossier", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()
        if msg.clickedButton() == open_btn and out_path.parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_path.parent.resolve())))

        self._trigger_after_workflow()

    def _on_sme_import_dialog(self) -> None:
        if not session_can_perform_action("workflow.import_archive"):
            QMessageBox.warning(self, "Droits", "Vous n'avez pas la permission d'importer une archive.")
            return
        from sme_gend.ui.import_liste_generale_wizard import (
            run_import_liste_generale_from_access,
        )

        if not run_import_liste_generale_from_access(self, self._settings):
            return
        self.refresh_workflow()
        self._trigger_after_workflow()
        self._refresh_shell_period_banner()

    def _on_sme_reopen_dialog(self) -> None:
        if not session_can_perform_action("workflow.reopen_month"):
            QMessageBox.warning(
                self, "Droits", "Vous n'avez pas la permission de réouvrir un mois clôturé."
            )
            return
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setWindowTitle("Réouverture exceptionnelle d'un mois clôturé")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        intro = QLabel("La période choisie passe de Fermé à Réouvert pour autoriser des corrections. "
                       "La demande et son motif sont journalisés sous votre compte. Cette action ne remplace pas les données du mois.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        g = QGroupBox("Mois à réouvrir")
        f = QFormLayout(g)
        cb = _sme_month_combo(for_dialog=True)
        sp = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(self._selected_period() or ctx0.mois_actif, cb, sp)
        row_m = QHBoxLayout()
        row_m.addWidget(QLabel("Mois :"))
        row_m.addWidget(cb)
        row_m.addWidget(QLabel("Année :"))
        row_m.addWidget(sp)
        row_m.addStretch()
        f.addRow(row_m)
        auteur = QLineEdit()
        from sme_gend.services.auth_context import resolve_audit_actor
        auteur.setText(resolve_audit_actor())
        auteur.setReadOnly(True)
        motif = QLineEdit()
        motif.setPlaceholderText("Motif administratif de la réouverture")
        f.addRow("Auteur *", auteur)
        f.addRow("Motif *", motif)
        lay.addWidget(g)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        def accept_request():
            if not auteur.text().strip() or not motif.text().strip():
                QMessageBox.warning(dlg, "Réouverture", "Renseignez le motif de la réouverture.")
                motif.setFocus()
                return
            dlg.accept()
        bb.accepted.connect(accept_request)
        bb.rejected.connect(dlg.reject)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Continuer")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ym = _widgets_to_ym(cb, sp)
        if not ym or extract_yyyy_mm(ym) != ym:
            QMessageBox.warning(self, "Réouverture", "Mois et année invalides.")
            return
        if not confirm_yes_no(
            self, "Confirmer la réouverture",
            f"Réouvrir les périodes « fermées » correspondant à « {ym} » dans périodes SME ?",
            default_no=True,
        ):
            return
        try:
            if not auteur.text().strip() or not motif.text().strip():
                QMessageBox.warning(self, "Réouverture", "Renseignez l’auteur et le motif avant de continuer.")
                return
            settings, actor, reason = self._settings, auteur.text(), motif.text()
            result = run_with_progress(self, "Réouverture", "Vérification et réouverture de la période…",
                lambda: reopen_closed_month_for_corrections(settings, ym, actor, reason))
        except Exception as e:
            QMessageBox.critical(self, "Réouverture", str(e))
            return
        if result is None:
            return
        msg = f"Période {ym} : {result.rows_updated} ligne(s) passée(s) en « Réouvert »."
        if result.warnings:
            msg += "\n\n" + "\n".join(result.warnings)
        QMessageBox.information(self, "Réouverture", msg)
        self._trigger_after_workflow()
        self._refresh_shell_period_banner()
