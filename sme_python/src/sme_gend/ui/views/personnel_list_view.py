"""Liste générale — grille + filtres (ancienne fenêtre principale)."""

from __future__ import annotations

from copy import deepcopy

import csv
import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QMouseEvent, QPageLayout, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.liste_generale_scope import build_export_mois_context
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context, load_local_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.grade_label_format import grade_combo_items
from sme_gend.services.padmin_label_format import padmin_combo_items
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox
from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
from sme_gend.ui.bg_task import run_with_progress, run_in_background
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.personnel_action_delegate import (
    ACT_DELETE,
    ACT_EDIT,
    ACT_VIEW,
    PersonnelActionDelegate,
)
from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog
from sme_gend.ui.personnel_advanced_filter import AdvancedFilterRule
from sme_gend.ui.personnel_advanced_filter_panel import PersonnelAdvancedFilterPanel
from sme_gend.ui.personnel_column_filter_dialog import PersonnelColumnFilterDialog
from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
from sme_gend.ui.personnel_table_model import (
    PersonnelTableModel,
    column_field_key,
    column_title,
)


# Colonnes par défaut masquables (indice → clé). Col 1 (noms), col 2 (sexe) et col 20 (Actions) toujours visibles.
_HIDEABLE_COLS: dict[int, str] = {
    0: "id",
    3: "grade",
    4: "fonction",
    5: "subdivision_arme",
    6: "position_admin",
    7: "structure",
    8: "matricule_militaire",
}

# Colonnes supplémentaires masquées par défaut — affichées seulement si l'utilisateur les active.
_EXTRA_COLS: dict[int, str] = {
    9:  "matricule_solde",
    10: "date_naissance",
    11: "lieu_naissance",
    12: "date_grade",
    13: "date_service",
    14: "ndecision",
    15: "diplome",
    16: "date_obtention",
    17: "obs",
    18: "presseance",
    19: "ordre_tri",
}

_log = logging.getLogger("sme_gend")


_DOSSIER_OPEN_DEBOUNCE_MS = 80


class _PersonnelListTable(QTableView):
    """Double-clic sur une ligne — délégué au parent (hors boucle souris Qt)."""

    def __init__(
        self,
        on_row_double: Callable[[int], None],
        *,
        actions_column: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_row_double = on_row_double
        self._actions_column = actions_column

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        idx = self.indexAt(pos)
        if idx.isValid() and idx.column() != self._actions_column:
            self._on_row_double(idx.row())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PersonnelListView(QWidget):
    """Consultation de la liste générale (MySQL)."""

    _DEBOUNCE_MS = 280

    def __init__(
        self,
        service: PersonnelService,
        referentiel: ReferentielService,
        settings: Settings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._referentiel = referentiel
        self._settings = settings
        self._all_rows: list[ListeGeneraleRow] = []
        self._only_empty_structure = False
        self._header_filters: dict[str, AdvancedFilterRule] = {}
        self._dirty = True
        self._opening_dossier = False
        self._dossier_open_queued = False
        self._pending_dossier_row: ListeGeneraleRow | None = None

        self._model = PersonnelTableModel(
            subdivision_flat=self._referentiel.subdivisions_flat(),
            grade_rows=self._referentiel.grades(),
            structure_rows=self._referentiel.structures(),
        )
        self._proxy = PersonnelFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._sync_grade_sort_helper()

        actions_col = self._model.columnCount() - 1
        self._table = _PersonnelListTable(
            self._request_open_dossier_at_proxy_row,
            actions_column=actions_col,
            parent=self,
        )
        self._table.setModel(self._proxy)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        # Tri interactif activé ; _apply_loaded_rows remet l'ordre métier après chaque rechargement.
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        # Délégué boutons Actions sur la dernière colonne
        self._action_delegate = PersonnelActionDelegate(self._table)
        self._action_delegate.set_callback(self._on_action_btn)
        self._table.setItemDelegateForColumn(actions_col, self._action_delegate)

        self._dossier_open_timer = QTimer(self)
        self._dossier_open_timer.setSingleShot(True)
        self._dossier_open_timer.timeout.connect(self._execute_pending_dossier_open)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # toutes les colonnes redimensionnables
        hdr.setSectionResizeMode(actions_col, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(actions_col, PersonnelActionDelegate.ACTIONS_COL_WIDTH)
        hdr.setMinimumSectionSize(60)
        hdr.setDefaultSectionSize(120)
        hdr.setStretchLastSection(False)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.setToolTip(
            "Clic droit sur un en-tête : filtre type Excel sur la colonne (▾ = filtre actif). "
            "Clic gauche : tri."
        )
        hdr.customContextMenuRequested.connect(self._on_header_filter_menu)

        self._counter = QLabel()
        self._counter.setObjectName("personnelListCounter")

        self._padmin_combo = NoWheelComboBox()
        self._grade_combo = NoWheelComboBox()
        self._structure_combo = NoWheelComboBox()
        self._subarme_combo = NoWheelComboBox()
        for cb in (
            self._padmin_combo,
            self._grade_combo,
            self._structure_combo,
            self._subarme_combo,
        ):
            cb.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            cb.setMinimumWidth(200)

        self._padmin_combo.currentIndexChanged.connect(self._apply_filter)
        self._grade_combo.currentIndexChanged.connect(self._apply_filter)
        self._structure_combo.currentIndexChanged.connect(
            self._on_structure_filter_changed
        )
        self._structure_combo.activated.connect(self._on_structure_filter_changed)
        self._subarme_combo.currentIndexChanged.connect(self._apply_filter)

        self._chk_no_mat_mil = QCheckBox("Sans matricule militaire")
        self._chk_no_mat_mil.setToolTip(
            "Afficher uniquement les agents sans matricule militaire renseigné."
        )
        self._chk_no_mat_solde = QCheckBox("Sans matricule solde")
        self._chk_no_mat_solde.setToolTip(
            "Afficher uniquement les agents sans matricule solde renseigné."
        )
        self._chk_no_mat_mil.toggled.connect(self._apply_filter)
        self._chk_no_mat_solde.toggled.connect(self._apply_filter)

        self._chk_sot_gap = QCheckBox("Fiches différentes du dossier")
        self._chk_sot_gap.setToolTip(
            "Rose : grade ou diplôme de la fiche différent de celui du dossier "
            "de l'agent (passations, diplômes)."
        )
        self._chk_fk_unresolved = QCheckBox("Valeurs hors référentiels")
        self._chk_fk_unresolved.setToolTip(
            "Bleu : grade, structure, position administrative ou fonction absent "
            "des référentiels."
        )
        self._chk_sot_gap.toggled.connect(self._apply_filter)
        self._chk_fk_unresolved.toggled.connect(self._apply_filter)

        mat_row = QHBoxLayout()
        mat_row.addWidget(self._chk_no_mat_mil)
        mat_row.addWidget(self._chk_no_mat_solde)
        mat_row.addWidget(self._chk_sot_gap)
        mat_row.addWidget(self._chk_fk_unresolved)
        mat_row.addStretch()

        filters_grid = QGridLayout()
        filters_grid.setHorizontalSpacing(12)
        filters_grid.setVerticalSpacing(8)
        filters_grid.addWidget(QLabel("Position administrative"), 0, 0)
        filters_grid.addWidget(self._padmin_combo, 0, 1)
        filters_grid.addWidget(QLabel("Grade"), 0, 2)
        filters_grid.addWidget(self._grade_combo, 0, 3)
        filters_grid.addWidget(QLabel("Structure"), 1, 0)
        filters_grid.addWidget(self._structure_combo, 1, 1)
        filters_grid.addWidget(QLabel("Subdivision d'arme"), 1, 2)
        self._subarme_field_label = filters_grid.itemAtPosition(1, 2).widget()
        filters_grid.addWidget(self._subarme_combo, 1, 3)

        reset_btn = QPushButton("Réinitialiser les filtres")
        reset_btn.setToolTip(
            "Remettre tous les critères et la recherche libre à « Tous » / vide."
        )
        reset_btn.clicked.connect(self._reset_filters)

        bottom_row = QHBoxLayout()
        bottom_row.addLayout(mat_row)
        bottom_row.addStretch(1)
        bottom_row.addWidget(reset_btn)

        filters_group = QGroupBox("Critères")
        filters_group.setObjectName("personnelFiltersGroup")
        fg_layout = QVBoxLayout(filters_group)
        fg_layout.setSpacing(8)
        fg_layout.addLayout(filters_grid)
        fg_layout.addLayout(bottom_row)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Recherche : nom, grade, fonction, structure, matricule…"
        )
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)

        self._filter_debounce = QTimer(self)
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.timeout.connect(self._apply_filter)

        self._search.textChanged.connect(self._schedule_filter)
        self._search.returnPressed.connect(self._flush_search_filter)

        self._filters_body = QWidget()
        self._filters_body.setObjectName("personnelFiltersBody")
        fb_layout = QVBoxLayout(self._filters_body)
        fb_layout.setContentsMargins(0, 0, 0, 0)
        fb_layout.setSpacing(0)
        fb_layout.addWidget(filters_group)

        self._advanced_filters = PersonnelAdvancedFilterPanel(self)
        self._advanced_filters.rules_changed.connect(self._apply_filter)
        fb_layout.addWidget(self._advanced_filters)
        _filter_logic_hint = QLabel(
            "Tous les critères (position, grade, recherche, filtres ▾ colonnes, règles "
            "avancées) s'appliquent ensemble : une ligne doit les satisfaire toutes."
        )
        _filter_logic_hint.setWordWrap(True)
        _filter_logic_hint.setObjectName("personnelFilterLogicHint")
        fb_layout.addWidget(_filter_logic_hint)

        self._toggle_filters_btn = QToolButton()
        self._toggle_filters_btn.setObjectName("personnelFiltersToggle")
        self._toggle_filters_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self._toggle_filters_btn.setToolTip(
            "Afficher ou masquer les critères (grade, structure, subdivision, matricules…). "
            "Le filtre subdivision s'active après choix d'une structure. "
            "Filtre par colonne : clic droit sur l'en-tête du tableau."
        )
        self._toggle_filters_btn.clicked.connect(self._on_toggle_filters)

        self._btn_refresh = QToolButton()
        self._btn_refresh.setObjectName("personnelRefreshBtn")
        _reload_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        if _reload_ico.isNull():
            self._btn_refresh.setText("↻")
        else:
            self._btn_refresh.setIcon(_reload_ico)
        self._btn_refresh.setIconSize(QSize(22, 22))
        self._btn_refresh.setToolTip("Actualiser depuis la base configurée (Ctrl+R)")
        self._btn_refresh.setAutoRaise(True)
        self._btn_refresh.clicked.connect(lambda: self._refresh_all(silent=False))

        self._btn_columns = QToolButton()
        self._btn_columns.setObjectName("personnelColumnsBtn")
        self._btn_columns.setText("Colonnes")
        self._btn_columns.setToolTip(
            "Choisir les colonnes affichées dans le tableau (Ctrl+Maj+C)"
        )
        self._btn_columns.setAutoRaise(True)
        self._btn_columns.clicked.connect(self._on_columns_dialog)

        top_bar = QFrame()
        top_bar.setObjectName("personnelListTopBar")
        top_row = QHBoxLayout(top_bar)
        top_row.setSpacing(10)
        top_row.addWidget(self._toggle_filters_btn)
        top_row.addWidget(self._counter, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addStretch(1)
        self._btn_etat = QToolButton()
        self._btn_etat.setObjectName("personnelColumnsBtn")
        self._btn_etat.setText("Exporter / imprimer")
        self._btn_etat.setToolTip(
            "Excel, PDF ou impression de la liste filtrée, avec choix des colonnes (Ctrl+P)"
        )
        self._btn_etat.setAutoRaise(True)
        self._btn_etat.clicked.connect(self._open_etat_export)

        top_row.addWidget(self._search, stretch=2)
        top_row.addWidget(self._btn_etat, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self._btn_columns, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self._btn_refresh, alignment=Qt.AlignmentFlag.AlignVCenter)

        filter_panel = QWidget()
        filter_panel.setObjectName("personnelFilterPanel")
        filter_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        fp_layout = QVBoxLayout(filter_panel)
        fp_layout.setSpacing(6)
        fp_layout.setContentsMargins(0, 0, 0, 0)
        fp_layout.addWidget(top_bar)
        fp_layout.addWidget(self._filters_body)

        self._filters_body.setVisible(False)
        self._sync_filters_toggle_label()

        self._empty_label = QLabel()
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setObjectName("pageSubtitle")

        empty_page = QWidget()
        empty_lay = QVBoxLayout(empty_page)
        empty_lay.addStretch(1)
        empty_lay.addWidget(self._empty_label)
        empty_lay.addStretch(2)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._table)
        self._stack.addWidget(empty_page)

        self._footer = QLabel()
        self._footer.setObjectName("pageSubtitle")
        self._footer.setMinimumHeight(0)
        self._footer.setWordWrap(True)
        self._footer.hide()

        self._lock_banner = QLabel()
        self._lock_banner.setWordWrap(True)
        self._lock_banner.setObjectName("personnelLockBanner")
        self._lock_banner.setVisible(False)
        self._apply_lock_banner_style()

        self._alerts_banner = QLabel()
        self._alerts_banner.setObjectName("personnelAlertsBanner")
        self._alerts_banner.setVisible(False)
        self._alerts_banner.setWordWrap(True)
        self._alerts_worker: _CareerAlertsScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._lock_banner)
        layout.addWidget(self._alerts_banner)
        layout.addWidget(filter_panel)
        layout.addWidget(self._stack, stretch=1)
        layout.addWidget(self._footer)

        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._on_columns_dialog)
        QShortcut(QKeySequence("Ctrl+R"), self, lambda: self._refresh_all(silent=False))
        QShortcut(QKeySequence(Qt.Key.Key_Return), self._table, self._open_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self._table, self._open_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self._table, self._edit_current)
        QShortcut(QKeySequence("Ins"), self, self._create_new)
        QShortcut(QKeySequence("Ctrl+N"), self, self._create_new)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self._table, self._delete_current)
        QShortcut(QKeySequence("Ctrl+E"), self, self._export_csv)
        QShortcut(QKeySequence("Ctrl+Shift+E"), self, self._open_etat_export)
        QShortcut(QKeySequence("Ctrl+P"), self, self._open_etat_export)

        # Filtres session-only : purge d'anciens états partagés MySQL (multi-poste).
        from sme_gend.config.view_state import clear_view_state_for_view

        clear_view_state_for_view("liste_generale")

        self._fill_subarme_combo(preserve_data=None)
        self._apply_column_visibility()

    @staticmethod
    def _combo_filter_code(combo: QComboBox) -> str | None:
        """Code filtre depuis userData, ou préfixe « CODE — libellé » du texte affiché."""
        data = combo.currentData()
        if data is not None:
            s = str(data).strip()
            if s:
                return s
        text = (combo.currentText() or "").strip()
        if not text or text in {"(Tous)", "Toutes"}:
            return None
        if " — " in text:
            head = text.split(" — ", 1)[0].strip()
            return head or None
        return text

    def _structure_filter_code(self) -> str | None:
        return self._combo_filter_code(self._structure_combo)

    def showEvent(self, event: QShowEvent) -> None:
        """Recharge depuis la base uniquement si les données ont changé (mois, import…)."""
        super().showEvent(event)
        self._apply_column_visibility()
        if event.spontaneous():
            self._refresh_lock_banner()
            return
        if self._dirty:
            QTimer.singleShot(0, self._deferred_first_load)
        else:
            self._refresh_lock_banner()

    def _deferred_first_load(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self._refresh_all(silent=True)
        self._refresh_lock_banner()

    def _apply_lock_banner_style(self) -> None:
        from sme_gend.ui.theme import is_dark, theme_palette

        pal = theme_palette()
        bg = "#3d3520" if is_dark() else "#fff3e0"
        border = "#5c4a2a" if is_dark() else "#ffcc80"
        self._lock_banner.setStyleSheet(
            f"background: {bg}; color: {pal['warning']}; padding: 10px 12px; "
            f"border-radius: 6px; border: 1px solid {border};"
        )

    def _apply_alerts_banner_style(self, *, warning: bool = False, loading: bool = False) -> None:
        from sme_gend.ui.theme import alert_banner_stylesheet

        self._alerts_banner.setStyleSheet(
            alert_banner_stylesheet(loading=loading, warning=warning)
        )

    def refresh_theme_styles(self) -> None:
        self._apply_lock_banner_style()
        if self._alerts_banner.isVisible():
            text = self._alerts_banner.text() or ""
            loading = "en cours" in text.lower()
            warning = "importante" in text.lower()
            self._apply_alerts_banner_style(warning=warning, loading=loading)

    def _refresh_lock_banner(self) -> None:
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            self._lock_banner.setText(
                f"<b>Édition verrouillée</b> — {st.detail}"
            )
            self._lock_banner.setTextFormat(Qt.TextFormat.RichText)
            self._lock_banner.setVisible(True)
        elif st.detail and not st.mois_actif:
            self._lock_banner.setText(st.detail)
            self._lock_banner.setTextFormat(Qt.TextFormat.PlainText)
            self._lock_banner.setVisible(True)
        elif st.detail and "Aucune période SME" in st.detail:
            self._lock_banner.setText(st.detail)
            self._lock_banner.setTextFormat(Qt.TextFormat.PlainText)
            self._lock_banner.setVisible(True)
        else:
            self._lock_banner.setVisible(False)
        self._start_career_alerts_scan()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_career_alerts_worker()
        super().hideEvent(event)

    def _stop_career_alerts_worker(self, *, wait_ms: int = 3000) -> None:
        worker = self._alerts_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.requestInterruption()
        # A query may exceed wait_ms. Its managed lifetime continues until
        # QThread.finished, without blocking navigation or deleting a running thread.
        self._alerts_worker = None

    def _start_career_alerts_scan(self) -> None:
        from sme_gend.services.career_alerts_cache import get_career_alerts_snapshot

        cached = get_career_alerts_snapshot(self._settings)
        if cached.ready:
            self._on_career_alerts_ready(cached)
            return
        if self._alerts_worker is not None and self._alerts_worker.isRunning():
            return
        self._alerts_banner.setText("Analyse des alertes parcours en cours…")
        self._apply_alerts_banner_style(loading=True)
        self._alerts_banner.setVisible(True)
        worker = _CareerAlertsScanWorker(self._settings, parent=self)
        from sme_gend.ui.bg_task import start_managed_thread

        self._alerts_worker = worker
        start_managed_thread(worker, self, signals=(
            (worker.finished_scan, self._on_career_alerts_ready),
            (worker.failed, self._on_career_alerts_failed),
        ))

    def _on_career_alerts_failed(self, _error) -> None:
        self._alerts_worker = None
        self._alerts_banner.setText("Analyse des alertes parcours indisponible. Actualisez pour réessayer.")
        self._apply_alerts_banner_style(warning=True)
        self._alerts_banner.setVisible(True)

    def _on_career_alerts_ready(self, snap) -> None:
        self._alerts_worker = None
        if snap is None or not snap.ready:
            self._alerts_banner.hide()
            return
        if snap.total == 0:
            self._alerts_banner.hide()
            return
        w = snap.warning_count
        info = snap.total - w
        parts = []
        if w:
            parts.append(f"{w} alerte(s) importante(s)")
        if info:
            parts.append(f"{info} info(s)")
        self._alerts_banner.setText(
            f"<b>Alertes parcours</b> — {', '.join(parts)} détectée(s) sur les imports mensuels. "
            "Ouvrez une fiche dossier pour le détail par agent."
        )
        self._alerts_banner.setTextFormat(Qt.TextFormat.RichText)
        self._apply_alerts_banner_style(warning=bool(w))
        self._alerts_banner.setVisible(True)

    def apply_structure_filter(self, structure_code: str) -> None:
        """Filtre la liste sur une structure (appelé depuis « Structures et effectifs »)."""
        code = (structure_code or "").strip()
        if code == "(Sans structure)":
            self._only_empty_structure = True
            self._structure_combo.blockSignals(True)
            try:
                self._structure_combo.setCurrentIndex(0)
            finally:
                self._structure_combo.blockSignals(False)
            self._fill_subarme_combo(preserve_data=None)
            self._apply_filter()
            self._show_status("Filtre : personnel sans code structure.", 5000)
            return
        self._only_empty_structure = False
        self._structure_combo.blockSignals(True)
        try:
            idx = 0
            found = False
            for i in range(self._structure_combo.count()):
                if self._structure_combo.itemData(i) == code:
                    idx = i
                    found = True
                    break
            if not found and code:
                self._structure_combo.addItem(f"{code} — (hors référentiel)", code)
                idx = self._structure_combo.count() - 1
            self._structure_combo.setCurrentIndex(idx)
        finally:
            self._structure_combo.blockSignals(False)
        self._fill_subarme_combo(preserve_data=None)
        self._apply_filter()
        self._show_status(f"Filtre structure : {code}", 5000)

    def _on_toggle_filters(self) -> None:
        self._filters_body.setVisible(not self._filters_body.isVisible())
        self._sync_filters_toggle_label()

    def _sync_filters_toggle_label(self) -> None:
        expanded = self._filters_body.isVisible()
        self._toggle_filters_btn.setText("▼ Filtres" if expanded else "▶ Filtres")

    def _schedule_filter(self) -> None:
        self._filter_debounce.stop()
        self._filter_debounce.start(self._DEBOUNCE_MS)

    def _flush_search_filter(self) -> None:
        if self._filter_debounce.isActive():
            self._filter_debounce.stop()
            self._apply_filter()

    def _show_status(self, message: str, ms: int = 4500) -> None:
        self._footer.setText(message)
        self._footer.setVisible(bool(message))
        QTimer.singleShot(ms, self._clear_footer)

    def _clear_footer(self) -> None:
        self._footer.clear()
        self._footer.hide()

    def _distinct_from_rows(self, attr: str) -> list[str]:
        seen: set[str] = set()
        for r in self._all_rows:
            v = getattr(r, attr, None)
            if v is not None and str(v).strip():
                seen.add(str(v).strip())
        return sorted(seen, key=str.lower)

    def _merge_unique_sorted(self, *lists: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for lst in lists:
            for x in lst:
                x = (x or "").strip()
                if x and x not in seen:
                    seen.add(x)
                    out.append(x)
        out.sort(key=str.lower)
        return out

    def _combo_set_items(
        self,
        combo: QComboBox,
        items: list[tuple[str, str]],
        previous_data: object,
    ) -> None:
        """items = (libellé affiché, valeur stockée). previous_data : valeur à conserver si possible."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(Tous)", None)
        for label, value in items:
            combo.addItem(label, value)
        idx = 0
        if previous_data is not None:
            for i in range(combo.count()):
                if combo.itemData(i) == previous_data:
                    idx = i
                    break
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _populate_structural_combos(self) -> None:
        prev_p = self._padmin_combo.currentData()
        prev_g = self._grade_combo.currentData()
        prev_s = self._structure_combo.currentData()

        padmin_items = padmin_combo_items(
            self._referentiel.padmins(),
            extra_raw=self._distinct_from_rows("position_admin"),
        )
        self._combo_set_items(
            self._padmin_combo,
            padmin_items,
            prev_p,
        )

        try:
            grade_ref = self._referentiel.grades()
        except Exception:
            grade_ref = []
        grade_items = grade_combo_items(
            grade_ref,
            extra_raw=self._distinct_from_rows("grade"),
        )
        self._combo_set_items(self._grade_combo, grade_items, prev_g)

        struct_map = {s.code: s for s in self._referentiel.structures()}
        struct_codes = self._merge_unique_sorted(
            list(struct_map.keys()),
            self._distinct_from_rows("structure"),
        )
        from sme_gend.services.structure_label_format import structure_combo_items

        struct_rows = structure_combo_items(
            self._referentiel.structures(),
            extra_codes=struct_codes,
        )
        self._combo_set_items(self._structure_combo, struct_rows, prev_s)

        self._fill_subarme_combo(preserve_data=self._subarme_combo.currentData())

    def _subarme_choices_for_structure(self, structure_code: str | None) -> list[str]:
        code = (structure_code or "").strip()
        if not code:
            return []
        from sme_gend.ui.personnel_list_filters import structure_matches_filter

        from_data = [
            (r.subdivision_arme or "").strip()
            for r in self._all_rows
            if structure_matches_filter(r.structure, code)
            and (r.subdivision_arme or "").strip()
        ]
        out = self._merge_unique_sorted([], from_data)
        return out

    def _sync_subarme_row_visibility(self) -> None:
        """Infobulle subdivision selon structure et contenu disponible."""
        struct_code = self._structure_filter_code()
        lab = getattr(self, "_subarme_field_label", None)
        n = max(0, self._subarme_combo.count() - 1)
        if struct_code is None:
            tip = "Choisissez d'abord une structure (pas « Tous »)."
        elif n <= 0:
            tip = (
                f"Aucune subdivision trouvée pour « {struct_code} » dans les données chargées. "
                "Actualisez (Ctrl+R) ou vérifiez le référentiel structure / subdivision."
            )
        else:
            tip = f"Filtrer parmi {n} subdivision(s) pour la structure {struct_code}."
        self._subarme_combo.setToolTip(tip)
        if lab is not None:
            lab.setToolTip(tip)

    def _fill_subarme_combo(self, preserve_data: object = None) -> None:
        if preserve_data is None:
            preserve_data = self._subarme_combo.currentData()
        struct_code = self._structure_filter_code()
        flat = list(self._referentiel.subdivisions_flat())
        from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

        self._subarme_combo.blockSignals(True)
        try:
            self._subarme_combo.clear()
            self._subarme_combo.addItem("Toutes", None)
            if struct_code is not None:
                choices = self._subarme_choices_for_structure(struct_code)
                for raw in choices:
                    label = format_subdivision_arme_display(raw, flat) or raw
                    self._subarme_combo.addItem(label, raw)
            idx = 0
            if preserve_data is not None:
                i = self._subarme_combo.findData(preserve_data)
                if i >= 0:
                    idx = i
            self._subarme_combo.setCurrentIndex(idx)
        finally:
            self._subarme_combo.blockSignals(False)
        self._sync_subarme_row_visibility()

    def _on_structure_filter_changed(self) -> None:
        self._only_empty_structure = False
        self._fill_subarme_combo(preserve_data=None)
        self._apply_filter()

    def _reset_filters(self) -> None:
        self._only_empty_structure = False
        self._filter_debounce.stop()
        for cb in (
            self._padmin_combo,
            self._grade_combo,
            self._structure_combo,
            self._subarme_combo,
        ):
            cb.blockSignals(True)
        self._padmin_combo.setCurrentIndex(0)
        self._grade_combo.setCurrentIndex(0)
        self._structure_combo.setCurrentIndex(0)
        self._subarme_combo.setCurrentIndex(0)
        for cb in (
            self._padmin_combo,
            self._grade_combo,
            self._structure_combo,
            self._subarme_combo,
        ):
            cb.blockSignals(False)
        self._fill_subarme_combo(preserve_data=None)
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._chk_no_mat_mil.blockSignals(True)
        self._chk_no_mat_mil.setChecked(False)
        self._chk_no_mat_mil.blockSignals(False)
        self._chk_no_mat_solde.blockSignals(True)
        self._chk_no_mat_solde.setChecked(False)
        self._chk_no_mat_solde.blockSignals(False)
        self._chk_sot_gap.blockSignals(True)
        self._chk_sot_gap.setChecked(False)
        self._chk_sot_gap.blockSignals(False)
        self._chk_fk_unresolved.blockSignals(True)
        self._chk_fk_unresolved.setChecked(False)
        self._chk_fk_unresolved.blockSignals(False)
        self._advanced_filters.clear_rules()
        self._header_filters.clear()
        self._sync_header_filter_marks()
        self._apply_filter()

    def _sync_grade_sort_helper(self) -> None:
        from sme_gend.services.personnel_list_order import GradeSortHelper

        try:
            grades = self._referentiel.grades()
        except Exception:
            grades = []
        self._proxy.set_grade_sort_helper(GradeSortHelper(grades))

    def _apply_loaded_rows(self, rows: list[ListeGeneraleRow]) -> None:
        self._all_rows = rows
        self._filter_debounce.stop()
        self._model.set_rows(rows)
        try:
            self._proxy.set_padmin_rows(self._referentiel.padmins())
        except Exception:
            self._proxy.set_padmin_rows([])
        self._proxy.sort_by_grade()
        self._populate_structural_combos()
        self._apply_filter()
        self._apply_column_visibility()
        QTimer.singleShot(80, self._resize_columns_to_content)
        self._refresh_row_quality()

    def _refresh_row_quality(self) -> None:
        """Discrète coloration des lignes hors dossier — sans bandeau ni blocage."""
        from sme_gend.services.sot_status_service import fetch_row_quality_ids

        self._quality_request_id = getattr(self, "_quality_request_id", 0) + 1
        request = self._quality_request_id
        settings = self._settings

        def loaded(result):
            if request != self._quality_request_id or settings is not self._settings:
                return
            gap_ids, fk_ids = result
            self._model.set_quality_ids(sot_gap_ids=gap_ids, fk_unresolved_ids=fk_ids)
            self._apply_filter()

        def failed(exc):
            if request == self._quality_request_id:
                logging.getLogger(__name__).warning("Contrôle de qualité indisponible : %s", exc)

        self._quality_worker = run_in_background(self, lambda: fetch_row_quality_ids(settings), loaded, on_error=failed)

    def apply_runtime_after_config_change(self) -> None:
        """Après enregistrement des chemins : recharge la liste et les subdivisions affichées."""
        self._settings = self._service.settings
        self._sync_grade_sort_helper()
        rows = run_with_progress(
            self,
            "Rechargement",
            "Lecture de la liste générale depuis la nouvelle configuration…",
            self._service.liste_complete,
        )
        if rows is None:
            return
        self._model.set_subdivision_flat(self._referentiel.subdivisions_flat())
        self._model.set_grade_rows(self._referentiel.grades())
        self._apply_loaded_rows(rows)
        self._refresh_lock_banner()

    def _refresh_all(self, *, silent: bool = False) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        invalidate_liste_cache(self._settings)
        rows = run_with_progress(
            self,
            "Liste générale",
            "Chargement depuis la base…",
            self._service.liste_complete,
            silent=silent,
        )
        if rows is None:
            return
        self._apply_loaded_rows(rows)
        self._refresh_lock_banner()

    def _header_filter_rules(self) -> list[AdvancedFilterRule]:
        return list(self._header_filters.values())

    def _sync_header_filter_marks(self) -> None:
        self._model.set_header_filtered_columns(set(self._header_filters.keys()))

    def _on_header_filter_menu(self, pos) -> None:
        hdr = self._table.horizontalHeader()
        section = hdr.logicalIndexAt(pos)
        key = column_field_key(section)
        if not key:
            return
        label = column_title(section)
        existing = self._header_filters.get(key)
        result = PersonnelColumnFilterDialog.run(
            self,
            column_key=key,
            column_label=label,
            existing=existing,
        )
        if result is False:
            return
        if result is None:
            self._header_filters.pop(key, None)
        else:
            self._header_filters[key] = result
        self._sync_header_filter_marks()
        self._apply_filter()

    def _apply_filter(self) -> None:
        from sme_gend.ui.filter_change import batch_row_filters
        with batch_row_filters(self._proxy):
            self._proxy.set_filter_criteria(
                only_empty_structure=self._only_empty_structure,
                padmin=self._padmin_combo.currentData(),
                grade=self._grade_combo.currentData(),
                grade_combo_display=self._grade_combo.currentText(),
                structure=self._structure_filter_code(),
                subarme=self._subarme_combo.currentData(),
                needle=self._search.text(),
                without_matricule_militaire=self._chk_no_mat_mil.isChecked(),
                without_matricule_solde=self._chk_no_mat_solde.isChecked(),
                advanced_rules=self._advanced_filters.rules(),
                advanced_match_mode=self._advanced_filters.match_mode(),
                header_rules=self._header_filter_rules(),
            )
            self._proxy.set_sot_gap_only(self._chk_sot_gap.isChecked())
            self._proxy.set_fk_unresolved_only(self._chk_fk_unresolved.isChecked())
        self._update_counter_and_empty(self._proxy.rowCount())

    def _update_counter_and_empty(self, displayed: int) -> None:
        total = len(self._all_rows)
        suffix = ""
        padmin = self._padmin_combo.currentData()
        adv = self._advanced_filters.rules()
        if padmin and any(r.column_key == "position_admin" for r in adv):
            suffix = " · attention : filtre Padmin + règle position adm."
        self._counter.setText(
            f"{_fmt_num(displayed)} / {_fmt_num(total)} lignes{suffix}"
        )
        if displayed > 0:
            self._stack.setCurrentWidget(self._table)
            return
        self._stack.setCurrentIndex(1)
        q = self._search.text().strip()
        has_struct = any(
            cb.currentData() is not None
            for cb in (
                self._padmin_combo,
                self._grade_combo,
                self._structure_combo,
                self._subarme_combo,
            )
        )
        if not self._all_rows:
            self._empty_label.setText(
                "Aucune ligne chargée. Cliquez sur le bouton d’actualisation (en haut à droite) "
                "ou Ctrl+R pour relire la base."
            )
        elif (
            q
            or has_struct
            or self._chk_no_mat_mil.isChecked()
            or self._chk_no_mat_solde.isChecked()
            or self._advanced_filters.rules()
            or self._header_filters
        ):
            self._empty_label.setText(
                "Aucun résultat avec les filtres et la recherche actuels. "
                "Élargissez un critère ou utilisez « Réinitialiser les filtres »."
            )
        else:
            self._empty_label.setText("Aucune ligne à afficher pour cette sélection.")

    def _proxy_index(self, index: QModelIndex) -> QModelIndex:
        """Normalise un index (proxy ou source) vers le modèle proxy."""
        if not index.isValid():
            return index
        model = index.model()
        if model is self._proxy:
            return index
        if model is self._model:
            mapped = self._proxy.mapFromSource(index)
            if mapped.isValid():
                return mapped
        return QModelIndex()

    def _row_object_at_proxy_index(self, proxy_index: QModelIndex) -> ListeGeneraleRow | None:
        proxy_index = self._proxy_index(proxy_index)
        if not proxy_index.isValid():
            return None
        src = self._proxy.mapToSource(self._proxy.index(proxy_index.row(), 0))
        if not src.isValid():
            return None
        return self._model.row_at(src.row())

    def _current_row_object(self) -> ListeGeneraleRow | None:
        idx = self._table.selectionModel().currentIndex()
        return self._row_object_at_proxy_index(idx)

    def _selected_row_objects(self) -> list[ListeGeneraleRow]:
        """Toutes les lignes actuellement sélectionnées (multi-select)."""
        result: list[ListeGeneraleRow] = []
        seen: set[int] = set()
        for idx in self._table.selectionModel().selectedRows():
            src = self._proxy.mapToSource(idx)
            if src.isValid() and src.row() not in seen:
                r = self._model.row_at(src.row())
                if r is not None:
                    result.append(r)
                    seen.add(src.row())
        return result

    # ------------------------------------------------------------------ actions boutons / raccourcis

    def _on_action_btn(self, row: ListeGeneraleRow, action: str) -> None:
        """Callback du délégué Actions — dispatche vers les méthodes directes."""
        if action == ACT_VIEW:
            self._request_open_dossier(row)
        elif action == ACT_EDIT:
            self._edit_row(row)
        elif action == ACT_DELETE:
            self._delete_row(row)

    def _row_at_proxy_row(self, proxy_row: int) -> ListeGeneraleRow | None:
        if not (0 <= proxy_row < self._proxy.rowCount()):
            return None
        src = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        if not src.isValid():
            return None
        return self._model.row_at(src.row())

    def _request_open_dossier_at_proxy_row(self, proxy_row: int) -> None:
        row_obj = self._row_at_proxy_row(proxy_row)
        if row_obj is None:
            return
        self._table.selectRow(proxy_row)
        self._request_open_dossier(row_obj)

    def _request_open_dossier(self, row_obj: ListeGeneraleRow) -> None:
        """Point d'entrée unique — différé et anti-rebond (double-clic / bouton Voir)."""
        if self._opening_dossier or self._dossier_open_queued:
            return
        self._pending_dossier_row = row_obj
        self._dossier_open_queued = True
        self._dossier_open_timer.start(_DOSSIER_OPEN_DEBOUNCE_MS)

    def _execute_pending_dossier_open(self) -> None:
        row_obj = self._pending_dossier_row
        self._pending_dossier_row = None
        self._dossier_open_queued = False
        if row_obj is None or self._opening_dossier:
            return
        self._opening_dossier = True
        try:
            from sme_gend.services.auth_guard import session_can_modify_personnel

            st = evaluate_editing_state_for_settings(self._settings)
            result = open_personnel_dossier_dialog(
                self,
                row_obj,
                self._referentiel,
                self._service,
                allow_edit=not st.locked and session_can_modify_personnel(),
            )
        finally:
            self._opening_dossier = False
        if result.liste_generale_changed:
            self._reload_liste_after_dossier()

    def _reload_liste_after_dossier(self) -> None:
        """Recharge la grille sans boîte de progression (évite les mini-fenêtres flash)."""
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        from sme_gend.ui.bg_task import run_in_background
        settings = self._settings
        def load():
            from sme_gend.services.personnel_service import PersonnelService
            invalidate_liste_cache(settings)
            return PersonnelService(settings).liste_complete()
        def loaded(rows):
            if self._settings == settings:
                self._apply_loaded_rows(rows)
        run_in_background(self, load, loaded,
                          on_error=lambda _exc: _log.error('Rechargement de la liste après dossier impossible.'))
        self._refresh_lock_banner()

    def _edit_row(self, row_obj: ListeGeneraleRow) -> None:
        from sme_gend.services.auth_guard import require_permission, session_can_modify_personnel

        if not session_can_modify_personnel():
            require_permission(
                self,
                "effectifs.edition",
                message="Votre compte n'a pas la permission de modifier le personnel.",
            )
            return
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(self, "Personnel", "Ligne introuvable en base. Actualisez la liste.")
            return
        try:
            dlg = PersonnelEditDialog(
                self, fresh, self._referentiel, settings=self._settings
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Modification",
                f"Impossible d'ouvrir la fiche de modification :\n{exc}",
            )
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            from sme_gend.services.personnel_form_service import save_personnel_form
            saved = save_personnel_form(self._service, dlg, before=fresh)
            from sme_gend.services.grade_save_notice import grade_save_notice
            notice = grade_save_notice(new_row, saved)
            if notice:
                QMessageBox.information(self, "Passation enregistrée", notice)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._show_status("Modifications enregistrées (MySQL + journal d'audit).")
        self._refresh_all(silent=True)

    def _delete_row(self, row_obj: ListeGeneraleRow) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self,
            "effectifs.delete",
            message="Votre compte n'a pas la permission de supprimer du personnel.",
        ):
            return
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        from sme_gend.ui.personnel_delete_dialog import pick_personnel_loss_motif

        name = row_obj.noms or f"Id {row_obj.id}"
        motif = pick_personnel_loss_motif(self)
        if not motif:
            return
        if not confirm_yes_no(
            self, "Supprimer",
            f"Sortir « {name} » (Id {row_obj.id}) du mois SME en cours ?\n\n"
            f"Motif de sortie : {motif}\n\n"
            "Une perte sera enregistrée dans Gains & pertes. "
            "L'agent disparaît de la liste du mois ; annulation via « Fiches retirées de la liste ».",
            default_no=True,
        ):
            return
        try:
            self._service.delete_personnel(row_obj, motif=motif)
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._settings)
        self._show_status(f"Sortie enregistrée : {name} (perte + audit).")
        self._refresh_all(silent=True)

    # ------------------------------------------------------------------ actions via sélection courante

    def _open_dossier(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        self._request_open_dossier(row_obj)

    def _edit_current(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        self._edit_row(row_obj)

    def _create_new(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self,
            "effectifs.edition",
            message="Votre compte n'a pas la permission de créer une fiche personnel.",
        ):
            return
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        blank = ListeGeneraleRow(
            id="0", noms=None, date_naissance=None, lieu_naissance=None,
            grade=None, date_grade=None, date_service=None, ndecision=None,
            matricule_militaire=None, matricule_solde=None, position_admin=None,
            fonction=None, subdivision_arme=None, diplome=None, date_obtention=None,
            structure=None, obs=None, ordre_tri=None, presseance=None,
        )
        dlg = PersonnelEditDialog(
            self, blank, self._referentiel, settings=self._settings
        )
        dlg.setWindowTitle("Nouvelle fiche personnel")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        if not new_row.noms:
            QMessageBox.warning(self, "Création", "Le nom est obligatoire.")
            return
        try:
            from sme_gend.services.personnel_form_service import save_personnel_form
            new_id = save_personnel_form(self._service, dlg).id
        except Exception as e:
            QMessageBox.critical(self, "Création", str(e))
            return
        self._show_status(f"Personnel créé (id {new_id}, journal d'audit). Ouvrez la fiche dossier pour les diplômes et passations de grade.")
        self._refresh_all(silent=True)

    def _delete_current(self) -> None:
        rows = self._selected_row_objects()
        if not rows:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ou plusieurs lignes.")
            return
        if len(rows) == 1:
            self._delete_row(rows[0])
            return
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self,
            "effectifs.delete",
            message="Votre compte n'a pas la permission de supprimer du personnel.",
        ):
            return
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        preview = ", ".join(r.noms or f"Id {r.id}" for r in rows[:3])
        if len(rows) > 3:
            preview += f"… et {len(rows) - 3} autre(s)"
        from sme_gend.ui.personnel_delete_dialog import pick_personnel_loss_motif

        motif = pick_personnel_loss_motif(self)
        if not motif:
            return
        if not confirm_yes_no(
            self, "Supprimer plusieurs fiches",
            f"Supprimer {len(rows)} fiche(s) ?\n\n{preview}\n\n"
            f"Motif de sortie (commun) : {motif}\n\n"
            "Pertes enregistrées dans Gains & pertes. "
            "Restauration via « Fiches retirées de la liste ».",
            default_no=True,
        ):
            return
        errors: list[str] = []
        for r in rows:
            try:
                self._service.delete_personnel(r, motif=motif)
            except Exception as e:
                errors.append(f"{r.noms or r.id}: {e}")
        if errors:
            QMessageBox.critical(self, "Suppression", "Erreurs :\n" + "\n".join(errors))
        else:
            self._show_status(f"{len(rows)} fiche(s) supprimée(s) (journal d'audit).")
        self._refresh_all(silent=True)

    def _on_table_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        has_row = idx.isValid()
        if has_row:
            self._table.setCurrentIndex(idx)
        row_obj = self._current_row_object() if has_row else None
        selected = self._selected_row_objects()
        n_sel = len(selected)
        locked = evaluate_editing_state_for_settings(self._settings).locked
        from sme_gend.services.auth_guard import session_can_modify_personnel

        can_edit = not locked and session_can_modify_personnel()
        menu = QMenu(self)

        act_view = menu.addAction("Voir la fiche…\tEntrée", self._open_dossier)
        act_view.setEnabled(n_sel == 1)
        act_mod = menu.addAction("Modifier…\tF2", self._edit_current)
        act_mod.setEnabled(n_sel == 1 and can_edit)
        menu.addSeparator()
        act_new = menu.addAction("Nouveau personnel…\tCtrl+N", self._create_new)
        act_new.setEnabled(can_edit)
        del_label = f"Supprimer {n_sel} fiche(s)…\tSuppr" if n_sel > 1 else "Supprimer…\tSuppr"
        act_del = menu.addAction(del_label, self._delete_current)
        act_del.setEnabled(n_sel > 0 and can_edit)
        menu.addSeparator()
        act_copy = menu.addAction(
            "Copier le nom",
            lambda: self._copy_name(row_obj),
        )
        act_copy.setEnabled(n_sel == 1 and row_obj is not None and bool(row_obj.noms))
        menu.addSeparator()
        menu.addAction("Exporter CSV…\tCtrl+E", self._export_csv)
        menu.addAction("Exporter / imprimer…\tCtrl+P", self._open_etat_export)
        menu.addAction("Exporter Excel par position…", self._export_excel_by_padmin)
        menu.addAction("Actualiser\tCtrl+R", lambda: self._refresh_all(silent=False))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_name(self, row_obj: ListeGeneraleRow | None) -> None:
        if row_obj and row_obj.noms:
            QApplication.clipboard().setText(row_obj.noms)
            self._show_status(f"Nom copié : {row_obj.noms}")

    def _apply_column_visibility(
        self,
        *,
        hidden_columns: list[str] | None = None,
        extra_columns_shown: list[str] | None = None,
    ) -> None:
        if hidden_columns is None or extra_columns_shown is None:
            ctx = load_local_sme_context()
            if hidden_columns is None:
                hidden_columns = ctx.hidden_columns
            if extra_columns_shown is None:
                extra_columns_shown = ctx.extra_columns_shown
        hidden_set = set(hidden_columns)
        extra_shown = set(extra_columns_shown)
        for col_idx, key in _HIDEABLE_COLS.items():
            self._table.setColumnHidden(col_idx, key in hidden_set)
        for col_idx, key in _EXTRA_COLS.items():
            self._table.setColumnHidden(col_idx, key not in extra_shown)

    def _on_columns_dialog(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QVBoxLayout

        from sme_gend.ui.personnel_table_model import _COLUMNS

        ctx = load_local_sme_context()
        hidden_set = set(ctx.hidden_columns)
        extra_shown = set(ctx.extra_columns_shown)

        dlg = QDialog(self)
        dlg.setWindowTitle("Colonnes affichées")
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg)

        # ── groupe 1 : colonnes par défaut ───────────────────────────────────
        g1 = QGroupBox("Colonnes par défaut")
        g1_lay = QVBoxLayout(g1)
        default_cbs: dict[str, QCheckBox] = {}
        for col_idx, key in _HIDEABLE_COLS.items():
            cb = QCheckBox(_COLUMNS[col_idx][1])
            cb.setChecked(key not in hidden_set)
            default_cbs[key] = cb
            g1_lay.addWidget(cb)
        lay.addWidget(g1)

        # ── groupe 2 : colonnes supplémentaires (masquées par défaut) ────────
        g2 = QGroupBox("Colonnes supplémentaires (masquées par défaut)")
        g2_lay = QVBoxLayout(g2)
        extra_cbs: dict[str, QCheckBox] = {}
        for col_idx, key in _EXTRA_COLS.items():
            cb = QCheckBox(_COLUMNS[col_idx][1])
            cb.setChecked(key in extra_shown)
            extra_cbs[key] = cb
            g2_lay.addWidget(cb)
        lay.addWidget(g2)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        hidden_columns = [key for key, cb in default_cbs.items() if not cb.isChecked()]
        extra_columns_shown = [key for key, cb in extra_cbs.items() if cb.isChecked()]
        ctx = load_local_sme_context()
        ctx.hidden_columns = hidden_columns
        ctx.extra_columns_shown = extra_columns_shown
        save_sme_context(ctx, promote_operational_month=False, save_shared_preferences=False)
        self._apply_column_visibility(
            hidden_columns=hidden_columns,
            extra_columns_shown=extra_columns_shown,
        )
        self._resize_columns_to_content()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def refresh_from_database(self) -> None:
        self._refresh_all(silent=False)

    def on_page_activated(self) -> None:
        self._apply_column_visibility()
        if self._dirty:
            QTimer.singleShot(0, self._deferred_first_load)
        else:
            self._refresh_lock_banner()

    def mark_dirty(self) -> None:
        """Appelé depuis shell_window lors d'un changement de mois actif."""
        self._dirty = True
        self._settings = self._service.settings
        self._refresh_lock_banner()

    def notify_settings_changed(self) -> None:
        """Appelé depuis shell_window quand les paramètres changent (mois, chemins, etc.)."""
        self._settings = self._service.settings
        self._dirty = True
        self._refresh_lock_banner()

    def _resize_columns_to_content(self) -> None:
        """Redimensionne les colonnes au contenu (sauf Actions). Appelé après chaque rechargement complet."""
        hdr = self._table.horizontalHeader()
        actions_col = self._model.columnCount() - 1
        for c in range(actions_col):
            hdr.resizeSection(c, hdr.sectionSizeHint(c))

    def _current_filtered_rows(self) -> list[ListeGeneraleRow]:
        """Lignes actuellement affichées (après filtres + recherche), dans l'ordre proxy."""
        self._flush_search_filter()
        rows: list[ListeGeneraleRow] = []
        for pr in range(self._proxy.rowCount()):
            src = self._proxy.mapToSource(self._proxy.index(pr, 0))
            if src.isValid():
                r = self._model.row_at(src.row())
                if r is not None:
                    rows.append(r)
        return rows

    def _effective_hidden_keys(self) -> set[str]:
        ctx = load_sme_context()
        hidden = set(ctx.hidden_columns)
        extra_shown = set(ctx.extra_columns_shown)
        for key in _EXTRA_COLS.values():
            if key not in extra_shown:
                hidden.add(key)
        return hidden

    def _visible_official_keys(self) -> list[str]:
        from sme_gend.services.liste_generale_print import PRINT_COLUMNS
        from sme_gend.ui.personnel_table_model import column_field_key

        visible: set[str] = set()
        for col in range(self._model.columnCount()):
            if self._table.isColumnHidden(col):
                continue
            key = column_field_key(col)
            if key:
                visible.add(key)
        keys: list[str] = []
        for key, _label, _width in PRINT_COLUMNS:
            if key == "idx":
                keys.append(key)
            elif key == "noms":
                keys.append(key)
            elif key == "naissance" and (
                "date_naissance" in visible or "lieu_naissance" in visible
            ):
                keys.append(key)
            elif key in visible:
                keys.append(key)
        return keys

    def _open_etat_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.personnel_list_order import sort_liste_rows_like_ui
        from sme_gend.ui.liste_generale_etat_dialog import ListeGeneraleEtatDialog

        if not require_permission(self, "effectifs.export"):
            return
        rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(
                self,
                "Exporter / imprimer",
                "Aucune ligne ne correspond aux filtres en cours.",
            )
            return
        exp_ctx = build_export_mois_context(self._settings)
        dlg = ListeGeneraleEtatDialog(
            self,
            row_count=len(rows),
            mois_label=exp_ctx.label_short(),
            visible_keys=self._visible_official_keys(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        keys = dlg.selected_keys()
        action = dlg.action()
        try:
            if action == "excel":
                self._export_excel_filtered(rows, keys)
            elif action == "pdf":
                self._print_preview(rows, keys, mode="pdf")
            elif action == "print":
                self._print_preview(rows, keys, mode="print")
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Exporter / imprimer",
                f"L'export n'a pas pu démarrer :\n{exc}",
            )

    def _export_excel_filtered(
        self,
        rows: list | None = None,
        column_keys: list[str] | None = None,
    ) -> None:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.personnel_excel_export import export_liste_filtered

        if not require_permission(self, "effectifs.export"):
            return


        if rows is None:
            rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
        hidden = set(self._effective_hidden_keys())
        flat = deepcopy(list(self._referentiel.subdivisions_flat()))
        grades = deepcopy(list(self._referentiel.grades()))
        settings = self._settings
        exp_ctx = build_export_mois_context(settings)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la liste en Excel",
            str(Path.home() / "liste_generale.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        mois_label = exp_ctx.label_short()

        def _work() -> int:
            return export_liste_filtered(
                rows,
                Path(path),
                hidden_keys=hidden,
                flat=flat,
                grades=grades,
                sheet_title="Situation nominative",
                log_mois=exp_ctx.export_mois, settings=settings,
                mois_label=mois_label,
                column_keys=column_keys,
            )

        n = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if n is None:
            return
        QMessageBox.information(
            self, "Export Excel",
            f"Fichier enregistré ({n} ligne(s)) :\n{path}",
        )

    def _export_excel_by_padmin(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.services.personnel_excel_export import export_liste_by_padmin

        rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
        hidden = set(self._effective_hidden_keys())
        flat = deepcopy(list(self._referentiel.subdivisions_flat()))
        grades = deepcopy(list(self._referentiel.grades()))
        settings = self._settings
        exp_ctx = build_export_mois_context(settings)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter par position administrative",
            str(Path.home() / "liste_par_padmin.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return


        def _work() -> dict:
            return export_liste_by_padmin(
                rows,
                Path(path),
                hidden_keys=hidden,
                flat=flat,
                grades=grades,
                log_mois=exp_ctx.export_mois, settings=settings,
                mois_label=exp_ctx.label_short(),
            )

        counts = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if counts is None:
            return
        details = "\n".join(f"  {p} : {n}" for p, n in sorted(counts.items(), key=lambda x: -x[1]))
        QMessageBox.information(
            self, "Export Excel par position",
            f"Fichier enregistré :\n{path}\n\n{len(counts)} feuille(s) :\n{details}",
        )

    def _print_preview(
        self,
        rows: list | None = None,
        column_keys: list[str] | None = None,
        *,
        mode: str = "",
    ) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return

        from sme_gend.services.liste_generale_print import (
            build_liste_generale_print_html,
            html_to_print_document,
        )
        from sme_gend.services.personnel_list_order import sort_liste_rows_like_ui

        try:
            from PySide6.QtPrintSupport import QPrinter
        except (ImportError, OSError) as exc:
            QMessageBox.critical(
                self,
                "Impression",
                "Le module d'impression Qt (PrintSupport) n'est pas disponible.\n"
                "Réinstallez PySide6 complet ou utilisez l'export Excel.\n\n"
                f"Détail : {exc}",
            )
            return
        _ = QPrinter

        if rows is None:
            rows = deepcopy(self._current_filtered_rows())
        if not rows:
            QMessageBox.information(self, "Impression", "Aucune ligne à imprimer.")
            return
        exp_ctx = build_export_mois_context(self._settings)
        try:
            html_content = build_liste_generale_print_html(
                rows,
                mois_label=exp_ctx.label_short(),
                flat=list(self._referentiel.subdivisions_flat()),
                grades=self._referentiel.grades(),
                columns=column_keys,
            )
            doc = html_to_print_document(html_content)
            pdf_context = dict(count=len(rows), mois=exp_ctx.export_mois, settings=self._settings)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Impression",
                f"Impossible de préparer le document :\n{e}",
            )
            return

        if mode == "pdf":
            self._save_liste_generale_pdf(doc, **pdf_context)
            return
        if mode == "print":
            self._open_print_preview(doc)
            return

        chooser = QMessageBox(self)
        chooser.setIcon(QMessageBox.Icon.Question)
        chooser.setWindowTitle("Impression — Situation nominative")
        chooser.setText(
            f"{len(rows)} agent(s) — format <b>A4 paysage</b> "
            f"({len(column_keys) if column_keys else 16} colonne(s)).\n"
            "Choisissez le mode de sortie :"
        )
        chooser.setTextFormat(Qt.TextFormat.RichText)
        btn_preview = chooser.addButton("Aperçu / imprimer…", QMessageBox.ButtonRole.AcceptRole)
        btn_pdf = chooser.addButton("Enregistrer en PDF…", QMessageBox.ButtonRole.ActionRole)
        chooser.addButton(QMessageBox.StandardButton.Cancel)
        chooser.exec()
        clicked = chooser.clickedButton()
        if clicked is None or clicked == chooser.button(QMessageBox.StandardButton.Cancel):
            return

        if clicked == btn_pdf:
            self._save_liste_generale_pdf(doc, **pdf_context)
            return

        self._open_print_preview(doc)

    def _open_print_preview(self, doc) -> None:
        from sme_gend.services.liste_generale_print import (
            apply_liste_generale_printer_layout,
            sync_document_to_printer,
        )

        try:
            from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter
        except (ImportError, OSError) as exc:
            QMessageBox.critical(self, "Impression", str(exc))
            return
        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            apply_liste_generale_printer_layout(printer)
            sync_document_to_printer(doc, printer)

            def _render(printer_obj) -> None:
                sync_document_to_printer(doc, printer_obj)
                doc.print_(printer_obj)

            dlg = QPrintPreviewDialog(printer, self)
            dlg.setWindowTitle("Aperçu — Situation nominative des effectifs (A4 paysage)")
            dlg.paintRequested.connect(_render)
            dlg.setWindowState(dlg.windowState() | Qt.WindowState.WindowActive)
            dlg.resize(1200, 820)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(
                self,
                "Impression",
                "L'aperçu d'impression n'a pas pu s'ouvrir (pilote d'impression absent ou bloqué).\n"
                "Utilisez « PDF… » puis ouvrez le fichier.\n\n"
                f"Détail : {e}",
            )

    def _save_liste_generale_pdf(self, doc, *, count=0, mois=None, settings=None) -> None:
        from sme_gend.services.liste_generale_print import sync_document_to_printer

        try:
            from PySide6.QtPrintSupport import QPrinter
        except (ImportError, OSError) as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la situation nominative en PDF",
            str(Path.home() / "situation_nominative.pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path = f"{path}.pdf"
        from sme_gend.ui.confirm_dialogs import confirm_overwrite_file

        if not confirm_overwrite_file(self, path):
            return
        try:
            from sme_gend.services.document_pdf_output import save_document_pdf
            save_document_pdf(doc, path, configure=sync_document_to_printer,
                doc_type="liste_personnel", count=count, mois=mois, settings=settings)
        except Exception as e:
            QMessageBox.critical(self, "PDF", f"Enregistrement impossible :\n{e}")
            return
        QMessageBox.information(self, "PDF", f"Fichier enregistré (A4 paysage) :\n{path}")

    def _export_csv(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en CSV",
            str(Path.home() / "liste_generale.csv"),
            "Fichiers CSV (*.csv);;Tous les fichiers (*)",
        )
        if not path:
            return
        data_col_count = self._model.columnCount() - 1  # exclut la colonne Actions
        headers = [
            self._model.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for c in range(data_col_count)
        ]
        nrows = self._proxy.rowCount()
        dlg = QProgressDialog("Export CSV en cours…", None, 0, max(1, nrows), self)
        dlg.setWindowTitle("Export CSV")
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(320)
        dlg.show()
        QApplication.processEvents()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(headers)
                for pr in range(nrows):
                    if pr % 25 == 0:
                        dlg.setValue(pr)
                        QApplication.processEvents()
                    src = self._proxy.mapToSource(self._proxy.index(pr, 0))
                    if not src.isValid():
                        continue
                    row_vals = []
                    for c in range(data_col_count):
                        mi = self._model.index(src.row(), c)
                        v = self._model.data(mi, Qt.ItemDataRole.DisplayRole)
                        row_vals.append(v if v is not None else "")
                    w.writerow(row_vals)
                dlg.setValue(nrows)
        except OSError as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        finally:
            dlg.close()
        self._show_status(f"Export enregistré : {path}")


def _fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


class _CareerAlertsScanWorker(QThread):
    finished_scan = Signal(object)
    failed = Signal(object)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        from sme_gend.services.career_alerts_cache import scan_and_cache_career_alerts

        try:
            snap = scan_and_cache_career_alerts(self._settings)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Analyse des alertes parcours impossible.")
            if not self.isInterruptionRequested():
                self.failed.emit(None)
            return
        if not self.isInterruptionRequested():
            self.finished_scan.emit(snap)

