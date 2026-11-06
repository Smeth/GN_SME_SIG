"""Congés — suivi positions CongéAdmin / CongéExp et alertes durée > 30 jours."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.conge_duration_alerts import (
    CONGE_ALERT_MIN_DAYS,
    CongeMovementRow,
    build_conge_history_rows,
    build_conge_movement_rows,
    reference_date_for_conge_alert,
)

_HISTORIQUE_TOOLTIP = (
    "Affiche aussi les fiches congé enregistrées alors que le padmin "
    "n'est plus « CongéAdmin » ou « CongéExp »."
)
from sme_gend.services.agent_conge_service import (
    CONGE_TYPE_ADMIN,
    CONGE_TYPE_AUTRE,
    CONGE_TYPE_EXPECTATIVE,
    CONGE_TYPE_LABELS,
    quota_days_between,
)
from sme_gend.services.conges_list_filter import row_matches_conge_search
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.ui.widgets.date_input import FrenchDateEdit
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
    grade_sort_helper_from_settings,
)
from sme_gend.ui.view_lifecycle import ViewDataState

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


class CongesView(QWidget):
    _pending_alerts_tab = False

    @classmethod
    def request_alerts_tab(cls) -> None:
        cls._pending_alerts_tab = True

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._view_mois: str | None = None
        self._mois_catalog: list[str] = []
        self._scoped_settings: Settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._view_data = ViewDataState(stale=True)
        self._conges_by_legacy: dict[str, AgentCongeRow] = {}
        self._all_rows: list[CongeMovementRow] = []
        self._history_rows: list[CongeMovementRow] = []
        self._display_rows: list[CongeMovementRow] = []
        self._grade_sort: GradeSortHelper | None = None
        self._can_edit = True
        self._alerts_only = False
        self._checked: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)

        self._summary = QLabel()
        self._summary.setObjectName("gainPerteSummary")
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self._summary)
        self._summary.hide()

        self._lock_banner = QLabel()
        self._lock_banner.setObjectName("gainPerteLockBanner")
        self._lock_banner.setWordWrap(True)
        self._lock_banner.hide()
        root.addWidget(self._lock_banner)

        top_bar = QFrame()
        top_bar.setObjectName("gainPerteTopBar")
        top_lay = QVBoxLayout(top_bar)
        top_lay.setContentsMargins(8, 6, 8, 6)
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("Période"))
        self._combo_annee = QComboBox()
        self._combo_annee.setMinimumWidth(92)
        self._combo_annee.currentIndexChanged.connect(self._on_annee_changed)
        period_row.addWidget(self._combo_annee)
        period_row.addWidget(QLabel("Mois"))
        self._combo_mois = QComboBox()
        self._combo_mois.setMinimumWidth(128)
        self._combo_mois.currentIndexChanged.connect(self._on_mois_changed)
        period_row.addWidget(self._combo_mois)
        self._lbl_mois_actif_ref = QLabel()
        self._lbl_mois_actif_ref.setObjectName("gainPerteCount")
        period_row.addWidget(self._lbl_mois_actif_ref)
        period_row.addSpacing(12)
        self._chip_conges = QLabel()
        self._chip_alerts = QLabel()
        for chip in (self._chip_conges, self._chip_alerts):
            chip.setObjectName("gainPerteStatChip")
            chip.setTextFormat(Qt.TextFormat.RichText)
            period_row.addWidget(chip)
        period_row.addStretch()
        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("gainPerteCount")
        period_row.addWidget(self._count_lbl)
        top_lay.addLayout(period_row)
        root.addWidget(top_bar)

        self._mode_tabs = QTabBar()
        self._mode_tabs.setDocumentMode(True)
        self._mode_tabs.addTab("Congés")
        self._mode_tabs.addTab(f"Alertes (> {CONGE_ALERT_MIN_DAYS} j.)")
        self._mode_tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._mode_tabs)
        root.addWidget(self._build_tab(), stretch=1)

    def _build_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        action_bar = QFrame()
        action_bar.setObjectName("gainPerteToolbar")
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(8, 6, 8, 6)
        self._search = QLineEdit()
        self._search.setObjectName("gainPerteSearch")
        self._search.setPlaceholderText("Nom, grade, type, dates…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filters)
        self._chk_history = QCheckBox("Historique")
        self._chk_history.setToolTip(_HISTORIQUE_TOOLTIP)
        self._chk_history.toggled.connect(lambda _v: self._apply_filters())
        self._lbl_checked = QLabel("0 coché(s)")
        self._lbl_checked.setObjectName("gainPerteCount")
        self._btn_select_all = QPushButton("Tout cocher")
        self._btn_select_all.setToolTip("Cocher toutes les lignes actuellement affichées.")
        self._btn_select_all.clicked.connect(self._toggle_select_all_visible)
        self._btn_present = QPushButton("Remettre à Présent (cochés)")
        self._btn_present.setToolTip("Repasser en « Présent » toutes les lignes cochées.")
        self._btn_present.clicked.connect(self._reset_checked)
        self._btn_refresh = QPushButton("Rafraîchir")
        self._btn_refresh.clicked.connect(self._reload_lists)
        self._btn_export = QPushButton("Exporter…")
        self._btn_export.clicked.connect(self._on_export)
        ab.addWidget(QLabel("Rechercher"))
        ab.addWidget(self._search, stretch=1)
        ab.addWidget(self._chk_history)
        ab.addWidget(self._lbl_checked)
        ab.addWidget(self._btn_select_all)
        ab.addWidget(self._btn_present)
        ab.addWidget(self._btn_refresh)
        ab.addWidget(self._btn_export)
        lay.addWidget(action_bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self._tbl = QTableWidget()
        self._tbl.setObjectName("gainPerteTable")
        self._tbl.setColumnCount(10)
        self._tbl.setHorizontalHeaderLabels(
            [
                "✓",
                "Nom",
                "Grade",
                "Structure",
                "Type",
                "Date début",
                "Date fin",
                "Jours",
                "Durée (j)",
                "Padmin",
            ]
        )
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSortingEnabled(True)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tbl.setMinimumHeight(280)
        self._tbl.itemSelectionChanged.connect(self._on_select)
        self._tbl.itemChanged.connect(self._on_item_changed)
        split.addWidget(self._tbl)

        form_box = QGroupBox("Fiche congé")
        form_box.setObjectName("limiteAgeDetailGroup")
        form_box.setMaximumWidth(280)
        fl = QFormLayout(form_box)
        self._lbl_agent = QLabel("Aucune ligne sélectionnée")
        self._lbl_agent.setObjectName("gainPerteCount")
        self._lbl_agent.setWordWrap(True)
        fl.addRow("Agent :", self._lbl_agent)
        self._lbl_grade = QLabel("—")
        self._lbl_structure = QLabel("—")
        self._lbl_structure.setWordWrap(True)
        fl.addRow("Grade :", self._lbl_grade)
        fl.addRow("Structure :", self._lbl_structure)
        self._f_type = QComboBox()
        for code in (CONGE_TYPE_ADMIN, CONGE_TYPE_EXPECTATIVE, CONGE_TYPE_AUTRE):
            self._f_type.addItem(CONGE_TYPE_LABELS[code], code)
        self._f_debut = FrenchDateEdit(optional=False)
        self._f_fin = FrenchDateEdit(optional=False)
        self._f_debut.changed.connect(self._sync_quota_label)
        self._f_fin.changed.connect(self._sync_quota_label)
        self._lbl_quota = QLabel("—")
        self._lbl_elapsed = QLabel("—")
        self._f_obs = QLineEdit()
        self._f_obs.setPlaceholderText("Observation")
        fl.addRow("Type :", self._f_type)
        fl.addRow("Date début :", self._f_debut)
        fl.addRow("Date fin :", self._f_fin)
        fl.addRow("Quota jours :", self._lbl_quota)
        fl.addRow("Jours écoulés :", self._lbl_elapsed)
        fl.addRow("Observation :", self._f_obs)
        self._btn_save = QPushButton("Enregistrer la fiche")
        self._btn_save.setObjectName("gainPerteBtnPrimary")
        self._btn_save.setToolTip("Enregistre le congé de la ligne sélectionnée.")
        self._btn_save.clicked.connect(self._save_conge_fiche)
        fl.addRow("", self._btn_save)
        split.addWidget(form_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 280])
        lay.addWidget(split, stretch=1)
        return w

    def _on_tab_changed(self, index: int) -> None:
        self._alerts_only = index == 1
        self._apply_filters()
        self._update_summary()

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if CongesView._pending_alerts_tab:
            self._mode_tabs.setCurrentIndex(1)
            self._alerts_only = True
            CongesView._pending_alerts_tab = False
        self._refresh_action_state()
        if self._view_data.needs_load():
            self._reload_lists_async()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._view_mois = None
        self._rebind_repositories()
        self._grade_sort = None
        self._view_data.mark_stale()
        if self.isVisible():
            self._reload_lists_async()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _rebind_repositories(self) -> None:
        from sme_gend.config.liste_generale_scope import settings_for_chosen_export_mois

        self._scoped_settings = settings_for_chosen_export_mois(self._settings, self._view_mois)
        self._liste = ListeGeneraleRepository(self._scoped_settings)
        self._sat = NumeriqueSatelliteRepository(self._scoped_settings)

    def _stat_chip(self, label: str, count: int, bg: str) -> str:
        return (
            f"<span style='background:{bg};color:#fff;padding:3px 10px;"
            f"border-radius:10px;font-size:11px;font-weight:600;'>{label} : {count}</span>"
        )

    def _update_summary(self) -> None:
        self._summary.hide()
        ref = reference_date_for_conge_alert(self._scoped_settings)
        n_alert = sum(1 for r in self._all_rows if r.is_long_alert)
        tab = "Alertes" if self._alerts_only else "Congés"
        self._summary.setText(
            f"<b>{self._period_label()}</b> — {len(self._all_rows)} en congé — "
            f"<b>{n_alert}</b> alerte(s) &gt; {CONGE_ALERT_MIN_DAYS} j. "
            f"(réf. {ref.strftime('%d/%m/%Y')}) — affichés ({tab}) : {len(self._display_rows)}"
        )
        self._chip_conges.setText(self._stat_chip("Congés", len(self._all_rows), "#5e35b1"))
        self._chip_alerts.setText(self._stat_chip("Alertes", n_alert, "#e65100"))
        self._count_lbl.setText(f"{len(self._display_rows)} ligne(s) visibles")

    def _parse_ym(self, ym: str) -> tuple[int, int] | None:
        try:
            y_s, m_s = (ym or "").strip().split("-", 1)
            y, m = int(y_s), int(m_s)
            if 1 <= m <= 12:
                return y, m
        except (ValueError, TypeError):
            pass
        return None

    def _period_label(self) -> str:
        if self._view_mois:
            p = self._parse_ym(self._view_mois)
            if p:
                return f"{_MOIS_FR[p[1] - 1]} {p[0]}"
            return self._view_mois
        mois_actif = (load_sme_context().mois_actif or "").strip()
        if mois_actif and (p := self._parse_ym(mois_actif)):
            return f"{_MOIS_FR[p[1] - 1]} {p[0]} (mois actif)"
        return "Mois actif"

    def _on_annee_changed(self, _index: int) -> None:
        prev = self._combo_mois.currentData()
        self._combo_mois.blockSignals(True)
        self._combo_mois.clear()
        self._fill_mois_combo(prev)
        self._combo_mois.blockSignals(False)
        self._on_period_changed()

    def _on_mois_changed(self, _index: int) -> None:
        self._on_period_changed()

    def _on_period_changed(self) -> None:
        year = self._combo_annee.currentData()
        month = self._combo_mois.currentData()
        self._view_mois = f"{int(year):04d}-{int(month):02d}" if year and month else None
        self._rebind_repositories()
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _fill_mois_combo(self, target_month: int | None) -> None:
        year = self._combo_annee.currentData()
        for m in range(1, 13):
            ym = f"{int(year):04d}-{m:02d}" if year else ""
            label = _MOIS_FR[m - 1]
            if ym and ym in self._mois_catalog:
                label = f"{label} •"
            self._combo_mois.addItem(label, m)
        for i in range(self._combo_mois.count()):
            if self._combo_mois.itemData(i) == target_month:
                self._combo_mois.setCurrentIndex(i)
                return
        if self._combo_mois.count():
            self._combo_mois.setCurrentIndex(0)

    def _rebuild_period_combos(self) -> None:
        mois_actif = (load_sme_context().mois_actif or "").strip()
        years = sorted({p[0] for ym in self._mois_catalog if (p := self._parse_ym(ym))}, reverse=True)
        target = self._view_mois or mois_actif or None
        tp = self._parse_ym(target) if target else None
        if tp and tp[0] not in years:
            years = sorted(set(years) | {tp[0]}, reverse=True)
        if not years and tp:
            years = [tp[0]]
        self._combo_annee.blockSignals(True)
        self._combo_mois.blockSignals(True)
        self._combo_annee.clear()
        for y in years:
            self._combo_annee.addItem(str(y), y)
        if tp:
            for i in range(self._combo_annee.count()):
                if self._combo_annee.itemData(i) == tp[0]:
                    self._combo_annee.setCurrentIndex(i)
                    break
        self._combo_mois.clear()
        self._fill_mois_combo(tp[1] if tp else None)
        self._combo_annee.blockSignals(False)
        self._combo_mois.blockSignals(False)
        if mois_actif and (p := self._parse_ym(mois_actif)):
            self._lbl_mois_actif_ref.setText(f"(mois actif : {_MOIS_FR[p[1] - 1]} {p[0]})")
        else:
            self._lbl_mois_actif_ref.setText("")

    def _reload_lists_async(self) -> None:
        if not self._view_data.needs_load():
            return
        self._request_generation = getattr(self, "_request_generation", 0) + 1
        generation, revision = self._request_generation, self._view_data.revision
        liste, sat, base = self._liste, self._sat, self._settings
        month, scoped_settings = self._view_mois, self._scoped_settings

        def is_current():
            return (generation == self._request_generation and revision == self._view_data.revision
                    and base is self._settings and month == self._view_mois)

        self._all_rows, self._history_rows, self._conges_by_legacy = [], [], {}
        self._checked.clear()
        self._apply_filters()
        self._refresh_action_state()
        self._summary.setText("Chargement des congés du mois sélectionné…")
        self._summary.show()

        def work():
            rows = liste.fetch_all()
            conges = sat.load_latest_conges_for_period()
            try:
                from sme_gend.config.liste_generale_scope import list_available_export_mois

                catalog = list_available_export_mois(base)
            except Exception:
                catalog = []
            return rows, conges, catalog

        def done(result: tuple) -> None:
            if not is_current():
                return
            liste_rows, conges, catalog = result
            self._mois_catalog = catalog
            self._rebuild_period_combos()
            self._conges_by_legacy = conges
            self._all_rows = build_conge_movement_rows(
                liste_rows, conges, scoped_settings
            )
            self._history_rows = build_conge_history_rows(
                liste_rows, conges, scoped_settings
            )
            self._apply_filters()
            self._view_data.finish_load()
            self._refresh_action_state()
            self._update_summary()

        def on_err(exc: BaseException) -> None:
            if not is_current():
                return
            self._view_data.fail_load()
            self._summary.setText("Lecture impossible. Actualisez pour réessayer.")
            self._summary.show()
            QMessageBox.warning(self, "Congés", str(exc))

        run_in_background(self, work, done, on_error=on_err)

    def _reload_lists(self) -> None:
        if not confirm_yes_no(self, "Rafraîchir", "Recharger depuis la base ?", default_no=True):
            return
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _apply_filters(self) -> None:
        q = self._search.text()
        base = list(self._all_rows)
        if self._chk_history.isChecked():
            seen = {self._movement_key(m) for m in base}
            for m in self._history_rows:
                k = self._movement_key(m)
                if k and k not in seen:
                    base.append(m)
                    seen.add(k)
        if self._alerts_only:
            base = [r for r in base if r.is_long_alert]
        self._display_rows = [
            r
            for r in base
            if row_matches_conge_search(r.liste, q, r.conge)
        ]
        self._fill_table()
        self._update_summary()

    def _ensure_grade_sort(self) -> GradeSortHelper:
        if self._grade_sort is None:
            self._grade_sort = grade_sort_helper_from_settings(self._settings)
        return self._grade_sort

    def _movement_key(self, m: CongeMovementRow) -> str:
        return m.liste.satellite_join_key() or m.liste.id or ""

    def _fill_table(self) -> None:
        gh = self._ensure_grade_sort()
        self._tbl.setSortingEnabled(False)
        self._tbl.blockSignals(True)
        self._tbl.setRowCount(len(self._display_rows))
        visible_keys = {self._movement_key(m) for m in self._display_rows}
        self._checked &= visible_keys
        for i, m in enumerate(self._display_rows):
            r, c = m.liste, m.conge
            key = self._movement_key(m)
            elapsed_s = str(m.elapsed_days) if m.elapsed_days is not None else "—"
            if m.is_long_alert:
                elapsed_s = f"⚠ {elapsed_s}"
            key_item = QTableWidgetItem(key)
            key_item.setData(Qt.ItemDataRole.UserRole, key)
            key_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            key_item.setCheckState(
                Qt.CheckState.Checked if key in self._checked else Qt.CheckState.Unchecked
            )
            self._tbl.setItem(i, 0, key_item)
            for col, text in (
                (1, r.noms or ""),
                (2, r.grade or ""),
                (3, r.structure or ""),
                (4, (c.type_label if c else "") or ""),
                (5, (c.date_debut if c else "") or ""),
                (6, (c.date_fin if c else "") or ""),
                (7, (c.quota_jours if c else "") or ""),
                (8, elapsed_s),
                (9, r.position_admin or ""),
            ):
                cell = GradeSortTableWidgetItem(text, gh) if col == 2 else QTableWidgetItem(text)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._tbl.setItem(i, col, cell)
        self._tbl.blockSignals(False)
        self._tbl.setSortingEnabled(True)
        self._on_select()
        self._update_checked_label()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._update_checked_label()

    def _update_checked_label(self) -> None:
        from sme_gend.ui.check_all import sync_check_all
        sync_check_all(self._btn_select_all, (self._movement_key(r) for r in self._display_rows), self._checked)
        n = len(self._checked)
        self._lbl_checked.setText(f"{n} coché(s) · {len(self._display_rows)} affiché(s)")
        self._btn_present.setEnabled(self._can_edit and n > 0)

    def _toggle_select_all_visible(self) -> None:
        if not self._display_rows:
            return
        keys = {self._movement_key(m) for m in self._display_rows}
        if keys <= self._checked:
            self._checked -= keys
        else:
            self._checked |= keys
        self._fill_table()

    def _selected_movement(self) -> CongeMovementRow | None:
        sel = self._tbl.selectionModel().selectedRows()
        if not sel:
            return None
        item = self._tbl.item(sel[0].row(), 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not key:
            return None
        for m in self._display_rows:
            if self._movement_key(m) == key:
                return m
        return None

    def _conge_dates(self) -> tuple[date, date] | None:
        d0 = self._f_debut.to_datetime()
        d1 = self._f_fin.to_datetime()
        if d0 is None or d1 is None:
            return None
        return d0.date(), d1.date()

    def _sync_quota_label(self) -> None:
        pair = self._conge_dates()
        if pair is None:
            self._lbl_quota.setText("—")
            return
        d0, d1 = pair
        quota = quota_days_between(d0, d1)
        if quota is None:
            self._lbl_quota.setText("—")
            return
        n = int(quota) if quota == int(quota) else quota
        self._lbl_quota.setText(f"{n} jour" if n == 1 else f"{n} jours")

    def _on_select(self) -> None:
        m = self._selected_movement()
        if m is None:
            self._lbl_agent.setText("Aucune ligne sélectionnée")
            self._lbl_grade.setText("—")
            self._lbl_structure.setText("—")
            self._f_type.setCurrentIndex(0)
            self._f_debut.set_empty()
            self._f_fin.set_empty()
            self._lbl_quota.setText("—")
            self._lbl_elapsed.setText("—")
            self._f_obs.clear()
            return
        r, c = m.liste, m.conge
        self._lbl_agent.setText(r.noms or self._movement_key(m))
        self._lbl_grade.setText(self._ensure_grade_sort().display_label(r.grade) or "—")
        self._lbl_structure.setText(r.structure or "—")
        if c and c.type_conge:
            idx = self._f_type.findData(c.type_conge)
            if idx >= 0:
                self._f_type.setCurrentIndex(idx)
        if c and c.date_debut:
            self._f_debut.set_from_text(c.date_debut)
        else:
            self._f_debut.set_empty()
        if c and c.date_fin:
            self._f_fin.set_from_text(c.date_fin)
        else:
            self._f_fin.set_empty()
        self._sync_quota_label()
        elapsed = str(m.elapsed_days) if m.elapsed_days is not None else "—"
        if m.is_long_alert:
            elapsed = f"⚠ {elapsed}"
        self._lbl_elapsed.setText(elapsed)
        self._f_obs.setText((c.observation if c else "") or "")

    def _refresh_action_state(self) -> None:
        from sme_gend.services.auth_guard import session_can_edit_effectifs
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        self._can_edit = session_can_edit_effectifs() and not state.locked and self._view_data.is_ready()
        if state.locked:
            self._lock_banner.setText(f"🔒 {state.detail}")
            self._lock_banner.show()
        else:
            self._lock_banner.hide()
        self._btn_save.setEnabled(self._can_edit)
        self._update_checked_label()

    def _check_can_edit(self) -> bool:
        if not self._view_data.is_ready():
            QMessageBox.information(self, "Congés", "Attendez le chargement ou actualisez les données du mois sélectionné.")
            return False
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        if not require_permission(self, "effectifs.edition", message="Permission refusée."):
            return False
        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        if state.locked:
            QMessageBox.warning(self, "Congés", state.detail)
            return False
        return True

    def _save_conge_fiche(self) -> None:
        if not self._check_can_edit():
            return
        m = self._selected_movement()
        if m is None:
            QMessageBox.information(self, "Congés", "Sélectionnez une ligne.")
            return
        key = m.liste.satellite_join_key() or m.liste.id
        if not key:
            QMessageBox.warning(self, "Congés", "Identifiant agent manquant.")
            return
        pair = self._conge_dates()
        if pair is None:
            QMessageBox.warning(
                self,
                "Congés",
                "Renseignez la date de début et la date de fin (jj/mm/aaaa).",
            )
            return
        d0, d1 = pair
        if d1 < d0:
            QMessageBox.warning(self, "Congés", "La date de fin doit être ≥ à la date de début.")
            return
        payload = {
            "type_code": str(self._f_type.currentData()),
            "date_debut": d0,
            "date_fin": d1,
            "quota_jours": quota_days_between(d0, d1),
            "observation": self._f_obs.text().strip() or None,
            "sme_mois": self._view_mois,
        }
        if not confirm_yes_no(
            self,
            "Enregistrer",
            f"Enregistrer le congé pour {m.liste.noms or key} ?",
            default_no=True,
        ):
            return
        from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService

        svc = AgentDossierEventsService(self._scoped_settings)
        try:
            if m.conge and m.conge.id:
                svc.update_conge(key, m.conge.id, expected=m.conge, **payload)
            else:
                svc.add_conge(key, **payload)
        except Exception as exc:
            QMessageBox.critical(self, "Congés", str(exc))
            return
        self._view_data.mark_stale()
        self._reload_lists_async()
        QMessageBox.information(self, "Congés", "Fiche enregistrée.")

    def _reset_checked(self) -> None:
        if not self._check_can_edit():
            return
        keys = sorted(self._checked)
        if not keys:
            QMessageBox.information(self, "Congés", "Cochez au moins une ligne.")
            return
        if not confirm_yes_no(
            self,
            "Remettre à Présent",
            f"Repasser {len(keys)} personne(s) en « Présent » ?",
            default_no=True,
        ):
            return
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        from sme_gend.services.personnel_service import PersonnelService

        personnel = PersonnelService(self._scoped_settings)
        by_id = {r.id: r for r in self._liste.fetch_all() if r.id}
        n_ok = 0
        for key in keys:
            m = next((x for x in self._display_rows if self._movement_key(x) == key), None)
            if m is None or not m.liste.id:
                continue
            cur = by_id.get(m.liste.id)
            if cur is None:
                continue
            try:
                personnel.update_personnel(
                    cur,
                    replace(cur, position_admin="Présent"),
                    edit_motif="Fin de congé depuis module Congés",
                )
                n_ok += 1
            except Exception:
                pass
        self._checked.clear()
        invalidate_liste_cache(self._scoped_settings)
        self._view_data.mark_stale()
        self._reload_lists_async()
        QMessageBox.information(self, "Congés", f"{n_ok} ligne(s) mise(s) à jour.")

    def _on_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell, configure_table_print

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.ui.export_snapshot import visible_export_rows
        from copy import deepcopy
        try:
            rows = visible_export_rows(self._tbl, self._display_rows, key=lambda movement: movement.liste.satellite_join_key() or movement.liste.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        if not rows:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        settings = self._scoped_settings
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter congés", str(Path.home() / "Conges.xlsx"), "Excel (*.xlsx)",
        )
        if not dest or not confirm_overwrite_file(self, dest):
            return
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Congés"
            headers = (
                "Nom", "Grade", "Structure", "Type", "Début", "Fin", "Jours", "Écoulés", "Alerte", "Padmin",
            )
            for col, h in enumerate(headers, 1):
                write_literal_cell_value(ws, 1, col, h)
                writable_cell(ws, 1, col).font = Font(bold=True)
            for i, m in enumerate(rows, 2):
                r, c = m.liste, m.conge
                write_literal_cell_value(ws, i, 1, r.noms or "")
                write_literal_cell_value(ws, i, 2, r.grade or "")
                write_literal_cell_value(ws, i, 3, r.structure or "")
                write_literal_cell_value(ws, i, 4, (c.type_label if c else "") or "")
                write_literal_cell_value(ws, i, 5, (c.date_debut if c else "") or "")
                write_literal_cell_value(ws, i, 6, (c.date_fin if c else "") or "")
                write_literal_cell_value(ws, i, 7, (c.quota_jours if c else "") or "")
                write_literal_cell_value(ws, i, 8, m.elapsed_days if m.elapsed_days is not None else "")
                write_literal_cell_value(ws, i, 9, "Oui" if m.is_long_alert else "")
                write_literal_cell_value(ws, i, 10, r.position_admin or "")
            from sme_gend.services.malade_punis_export import _autosize
            _autosize(ws, headers, 2, max(1, len(rows) + 1))
            wb.save(dest)
            from sme_gend.services.doc_gen_log import log_saved_list
            log_saved_list(dest, rows, doc_type="conges", settings=settings)
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
