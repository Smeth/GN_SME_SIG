"""Désertions — fiches agent_desertion et position « Déserteur »."""

from __future__ import annotations

from dataclasses import replace
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import DeserteurDetail
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.desertion_list_filter import desertion_detail_for_row, row_matches_desertion_search
from sme_gend.services.missions_desertion_export import export_desertions_workbook
from sme_gend.services.numerique_padmin import PADMIN_DESERTEUR
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
    grade_sort_helper_from_settings,
)
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.widgets.date_input import FrenchDateEdit

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
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


def _is_deserteur_row(r: ListeGeneraleRow) -> bool:
    return (r.position_admin or "").strip().casefold() == PADMIN_DESERTEUR.casefold()


def _stat_chip_html(label: str, count: int, *, bg: str) -> str:
    return (
        f"<span style='background:{bg};color:#ffffff;padding:3px 10px;"
        f"border-radius:10px;font-size:11px;font-weight:600;'>"
        f"{label} : {count}</span>"
    )


class DesertionView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._view_mois: str | None = None
        self._mois_catalog: list[str] = []
        self._scoped_settings: Settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._view_data = ViewDataState(stale=True)
        self._rows_all: list[ListeGeneraleRow] = []
        self._rows_history: list[ListeGeneraleRow] = []
        self._rows: list[ListeGeneraleRow] = []
        self._details: dict[str, DeserteurDetail] = {}
        self._checked: set[str] = set()
        self._grade_sort: GradeSortHelper | None = None
        self._can_edit = True

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
        self._chip = QLabel()
        self._chip.setObjectName("gainPerteStatChip")
        self._chip.setTextFormat(Qt.TextFormat.RichText)
        period_row.addWidget(self._chip)
        period_row.addStretch()
        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("gainPerteCount")
        period_row.addWidget(self._count_lbl)
        top_lay.addLayout(period_row)
        root.addWidget(top_bar)

        action_bar = QFrame()
        action_bar.setObjectName("gainPerteToolbar")
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(8, 6, 8, 6)
        self._search = QLineEdit()
        self._search.setObjectName("gainPerteSearch")
        self._search.setPlaceholderText("Nom, grade, structure, date désertion…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filters)
        self._chk_history = QCheckBox("Historique")
        self._chk_history.setToolTip(
            "Affiche aussi les fiches désertion enregistrées alors que le padmin "
            "n'est plus « Déserteur »."
        )
        self._chk_history.toggled.connect(lambda _v: self._apply_filters())
        self._lbl_checked = QLabel("0 coché(s)")
        self._lbl_checked.setObjectName("gainPerteCount")
        self._btn_select_all = QPushButton("Tout cocher")
        self._btn_select_all.setToolTip("Cocher toutes les lignes actuellement affichées.")
        self._btn_select_all.clicked.connect(self._toggle_select_all_visible)
        self._btn_reset = QPushButton("Remettre à Présent (cochés)")
        self._btn_reset.setToolTip("Repasser en « Présent » toutes les lignes cochées.")
        self._btn_reset.clicked.connect(self._reset_checked)
        self._btn_del = QPushButton("Supprimer les fiches cochées")
        self._btn_del.setToolTip("Supprime les fiches désertion des lignes cochées.")
        self._btn_del.clicked.connect(self._delete_checked_fiches)
        self._btn_refresh = QPushButton("Rafraîchir")
        self._btn_refresh.clicked.connect(self._reload_lists)
        self._btn_export = QPushButton("Exporter…")
        self._btn_export.clicked.connect(self._on_export)
        ab.addWidget(QLabel("Rechercher"))
        ab.addWidget(self._search, stretch=1)
        ab.addWidget(self._chk_history)
        ab.addWidget(self._lbl_checked)
        ab.addWidget(self._btn_select_all)
        ab.addWidget(self._btn_reset)
        ab.addWidget(self._btn_del)
        ab.addWidget(self._btn_refresh)
        ab.addWidget(self._btn_export)
        root.addWidget(action_bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self._tbl = QTableWidget()
        self._tbl.setObjectName("gainPerteTable")
        self._tbl.setColumnCount(8)
        self._tbl.setHorizontalHeaderLabels(
            [
                "✓",
                "Nom",
                "Grade",
                "Structure",
                "Date désertion",
                "Padmin",
                "Id liaison",
                'Réf/note',
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

        form_box = QGroupBox("Fiche désertion")
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
        self._f_date = FrenchDateEdit()
        self._f_date.setToolTip("Date de désertion (jj/mm/aaaa)")
        fl.addRow("Date désertion :", self._f_date)
        self._f_reference = QLineEdit()
        self._f_reference.setMaxLength(256)
        self._f_reference.setPlaceholderText('Facultatif')
        fl.addRow('Réf/note :', self._f_reference)
        self._btn_save = QPushButton("Enregistrer la fiche")
        self._btn_save.setObjectName("gainPerteBtnPrimary")
        self._btn_save.setToolTip("Enregistre la fiche de la ligne sélectionnée.")
        self._btn_save.clicked.connect(self._save_fiche)
        fl.addRow("", self._btn_save)
        split.addWidget(form_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 280])
        root.addWidget(split, stretch=1)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
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

    def _period_label(self) -> str:
        if self._view_mois:
            p = _parse_ym(self._view_mois)
            if p:
                return f"{_MOIS_FR[p[1] - 1]} {p[0]}"
            return self._view_mois
        mois_actif = (load_sme_context().mois_actif or "").strip()
        if mois_actif and (p := _parse_ym(mois_actif)):
            return f"{_MOIS_FR[p[1] - 1]} {p[0]} (mois actif)"
        return "Mois actif"

    def _update_summary(self) -> None:
        self._summary.hide()
        period = self._period_label()
        self._summary.setText(
            f"<b>{period}</b> — {len(self._rows_all)} déserteur(s) — "
            f"affichés : {len(self._rows)}"
        )
        self._chip.setText(_stat_chip_html("Déserteurs", len(self._rows_all), bg="#b71c1c"))
        self._count_lbl.setText(f"{len(self._rows)} ligne(s) visibles")

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
        self._sync_view_mois()
        self._rebind_repositories()
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _sync_view_mois(self) -> None:
        year = self._combo_annee.currentData()
        month = self._combo_mois.currentData()
        self._view_mois = f"{int(year):04d}-{int(month):02d}" if year and month else None

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
        years = sorted({p[0] for ym in self._mois_catalog if (p := _parse_ym(ym))}, reverse=True)
        target = self._view_mois or mois_actif or None
        tp = _parse_ym(target) if target else None
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
        self._sync_view_mois()
        if mois_actif and (p := _parse_ym(mois_actif)):
            self._lbl_mois_actif_ref.setText(f"(mois actif : {_MOIS_FR[p[1] - 1]} {p[0]})")
        else:
            self._lbl_mois_actif_ref.setText("")

    def _reload_lists_async(self) -> None:
        if not self._view_data.needs_load():
            return
        self._request_generation = getattr(self, "_request_generation", 0) + 1
        generation, revision = self._request_generation, self._view_data.revision
        liste, sat, base = self._liste, self._sat, self._settings
        month = self._view_mois

        def is_current():
            return (generation == self._request_generation and revision == self._view_data.revision
                    and base is self._settings and month == self._view_mois)

        self._rows_all, self._rows_history, self._details = [], [], {}
        self._checked.clear()
        self._apply_filters()
        self._refresh_action_state()
        self._summary.setText("Chargement des désertions du mois sélectionné…")
        self._summary.show()

        def work():
            rows = liste.fetch_all()
            details = sat.load_deserteurs()
            try:
                from sme_gend.config.liste_generale_scope import list_available_export_mois

                catalog = list_available_export_mois(base)
            except Exception:
                catalog = []
            return rows, details, catalog

        def done(result: tuple) -> None:
            if not is_current():
                return
            rows, details, catalog = result
            self._mois_catalog = catalog
            self._rebuild_period_combos()
            self._details = details
            self._rows_all = [r for r in rows if _is_deserteur_row(r)]
            keys = {r.satellite_join_key() for r in self._rows_all}
            self._rows_history = [
                r for r in rows
                if r.satellite_join_key() in details and r.satellite_join_key() not in keys
            ]
            self._apply_filters()
            self._view_data.finish_load()
            self._refresh_action_state()
            self._update_summary()

        def failed(error):
            if is_current():
                self._view_data.fail_load()
                self._summary.setText("Lecture impossible. Actualisez pour réessayer.")
                self._summary.show()
                QMessageBox.warning(self, "Désertions", str(error))

        run_in_background(self, work, done, on_error=failed)

    def _reload_lists(self) -> None:
        if not confirm_yes_no(self, "Rafraîchir", "Recharger depuis la base ?", default_no=True):
            return
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _apply_filters(self) -> None:
        q = self._search.text()
        base = self._rows_all
        if self._chk_history.isChecked():
            base = base + self._rows_history
        self._rows = [
            r
            for r in base
            if row_matches_desertion_search(r, q, desertion_detail_for_row(r, self._details))
        ]
        self._fill_table()
        self._update_summary()

    def _ensure_grade_sort(self) -> GradeSortHelper:
        if self._grade_sort is None:
            self._grade_sort = grade_sort_helper_from_settings(self._settings)
        return self._grade_sort

    def _row_key(self, r: ListeGeneraleRow) -> str:
        return r.satellite_join_key() or r.id or ""

    def _fill_table(self) -> None:
        gh = self._ensure_grade_sort()
        self._tbl.setSortingEnabled(False)
        self._tbl.blockSignals(True)
        self._tbl.setRowCount(len(self._rows))
        visible_keys = {self._row_key(r) for r in self._rows}
        self._checked &= visible_keys
        for i, r in enumerate(self._rows):
            key = self._row_key(r)
            d = desertion_detail_for_row(r, self._details)
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
                (4, (d.dat_desertion if d else "") or ""),
                (5, r.position_admin or ""),
                (6, key),
                (7, (d.reference_dossier if d else '') or ''),
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
        sync_check_all(self._btn_select_all, (self._row_key(r) for r in self._rows), self._checked)
        n = len(self._checked)
        self._lbl_checked.setText(f"{n} coché(s) · {len(self._rows)} affiché(s)")
        self._btn_reset.setEnabled(self._can_edit and n > 0)
        self._btn_del.setEnabled(self._can_edit and n > 0)

    def _toggle_select_all_visible(self) -> None:
        if not self._rows:
            return
        keys = {self._row_key(r) for r in self._rows}
        if keys <= self._checked:
            self._checked -= keys
        else:
            self._checked |= keys
        self._fill_table()

    def _selected_key(self) -> str | None:
        sel = self._tbl.selectionModel().selectedRows()
        if not sel:
            return None
        item = self._tbl.item(sel[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _current_row(self) -> ListeGeneraleRow | None:
        key = self._selected_key()
        if not key:
            return None
        for r in self._rows:
            if self._row_key(r) == key:
                return r
        return None

    def _on_select(self) -> None:
        row = self._current_row()
        if row is None:
            self._lbl_agent.setText("Aucune ligne sélectionnée")
            self._lbl_grade.setText("—")
            self._lbl_structure.setText("—")
            self._f_date.set_empty()
            self._f_reference.clear()
            return
        self._lbl_agent.setText(row.noms or self._row_key(row))
        self._lbl_grade.setText(self._ensure_grade_sort().display_label(row.grade) or "—")
        self._lbl_structure.setText(row.structure or "—")
        d = desertion_detail_for_row(row, self._details)
        self._f_date.set_from_text(d.dat_desertion or "" if d else "")
        self._f_reference.setText(d.reference_dossier or '' if d else '')

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
            QMessageBox.information(self, "Désertions", "Attendez le chargement ou actualisez les données du mois sélectionné.")
            return False
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        if not require_permission(self, "effectifs.edition", message="Permission refusée."):
            return False
        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        if state.locked:
            QMessageBox.warning(self, "Désertions", state.detail)
            return False
        return True

    def _save_fiche(self) -> None:
        if not self._check_can_edit():
            return
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Désertions", "Sélectionnez une ligne.")
            return
        key = self._row_key(row)
        detail = DeserteurDetail(
            dat_desertion=self._f_date.text_for_validation().strip() or None,
            reference_dossier=self._f_reference.text().strip() or None,
        )
        snapshots = dict(self._details)
        if not confirm_yes_no(
            self,
            "Enregistrer",
            f"Enregistrer la fiche désertion pour {row.noms or key} ?",
            default_no=True,
        ):
            return
        try:
            self._sat.upsert_desertion(key, detail, expected=snapshots.get(key))
        except Exception as exc:
            QMessageBox.critical(self, "Désertions", str(exc))
            return
        try:
            self._details = self._sat.load_deserteurs()
        except Exception as exc:
            QMessageBox.warning(self, "Relecture des données",
                f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
            return
        self._apply_filters()
        QMessageBox.information(self, "Désertions", "Fiche enregistrée.")

    def _delete_checked_fiches(self) -> None:
        if not self._check_can_edit():
            return
        keys = sorted(self._checked)
        if not keys:
            QMessageBox.information(self, "Désertions", "Cochez au moins une ligne.")
            return
        snapshots = dict(self._details)
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer {len(keys)} fiche(s) désertion ?\n\n"
            "Les positions « Déserteur » en liste ne sont pas modifiées.",
            default_no=True,
        ):
            return
        n_ok = 0
        errors: list[str] = []
        for key in keys:
            try:
                self._sat.delete_desertion(key, expected=snapshots.get(key))
                self._details.pop(key, None)
                n_ok += 1
            except Exception as exc:
                errors.append(f"{key}: {exc}")
        self._checked.clear()
        self._apply_filters()
        if errors:
            QMessageBox.warning(self, "Désertions", "Échecs :\n" + "\n".join(errors[:8]))
        QMessageBox.information(self, "Désertions", f"{n_ok} fiche(s) supprimée(s).")

    def _reset_checked(self) -> None:
        if not self._check_can_edit():
            return
        keys = sorted(self._checked)
        if not keys:
            QMessageBox.information(self, "Désertions", "Cochez au moins une ligne.")
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
        errors: list[str] = []
        for key in keys:
            row = next((r for r in self._rows if self._row_key(r) == key), None)
            if row is None or not row.id:
                continue
            cur = by_id.get(row.id)
            if cur is None:
                continue
            try:
                personnel.update_personnel(
                    cur,
                    replace(cur, position_admin="Présent"),
                    edit_motif="Retour présent depuis module Désertions",
                )
                n_ok += 1
            except Exception as exc:
                errors.append(f"{key}: {exc}")
        self._checked.clear()
        invalidate_liste_cache(self._scoped_settings)
        self._view_data.mark_stale()
        self._reload_lists_async()
        if errors:
            QMessageBox.warning(self, "Désertions", "Échecs :\n" + "\n".join(errors[:8]))
        QMessageBox.information(self, "Désertions", f"{n_ok} ligne(s) mise(s) à jour.")

    def _on_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.ui.export_snapshot import visible_export_rows
        from copy import deepcopy
        try:
            rows = visible_export_rows(self._tbl, self._rows, key=lambda row: row.satellite_join_key() or row.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        details = deepcopy(self._details)
        if not rows:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        settings = getattr(self, "_scoped_settings", getattr(self, "_settings", None))
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter désertions", str(Path.home() / "Desertions.xlsx"), "Excel (*.xlsx)",
        )
        if not dest or not confirm_overwrite_file(self, dest):
            return
        try:
            export_desertions_workbook(Path(dest), rows=rows, details=details, settings=settings)
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
