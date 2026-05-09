"""Liste générale — grille + filtres (ancienne fenêtre principale)."""

from __future__ import annotations

import csv
import html as _html
from pathlib import Path

from PySide6.QtCore import QSize, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPageLayout, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
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

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.personnel_action_delegate import (
    ACT_DELETE,
    ACT_EDIT,
    ACT_VIEW,
    PersonnelActionDelegate,
)
from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog
from sme_gend.ui.personnel_table_model import PersonnelTableModel


# Colonnes par défaut masquables (indice → clé). Col 1 (noms) et col 19 (Actions) toujours visibles.
_HIDEABLE_COLS: dict[int, str] = {
    0: "id",
    2: "grade",
    3: "fonction",
    4: "subdivision_arme",
    5: "position_admin",
    6: "structure",
    7: "matricule_militaire",
}

# Colonnes supplémentaires masquées par défaut — affichées seulement si l'utilisateur les active.
_EXTRA_COLS: dict[int, str] = {
    8:  "matricule_solde",
    9:  "date_naissance",
    10: "lieu_naissance",
    11: "date_grade",
    12: "date_service",
    13: "ndecision",
    14: "diplome",
    15: "date_obtention",
    16: "obs",
    17: "presseance",
    18: "ordre_tri",
}


class PersonnelListView(QWidget):
    """Consultation de la liste générale (SQLite ou Access selon la configuration)."""

    _DEBOUNCE_MS = 280
    _FOOTER_DEFAULT_HINT = (
        "Ordre par défaut : COMGEND → C2 CEMGEND → INSPECTEUR IT → DIRECTEUR DOE → DPF → DSF → "
        "DIRTRANS → DTRJD → DIRMAT → DDT, puis toutes les autres fonctions par grade (PrefOrd). "
        "Cliquez sur un en-tête pour trier — utilisez « Réinitialiser l'ordre métier » (menu ⋯) pour restaurer."
    )

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
        self._dirty = True

        self._model = PersonnelTableModel(
            subdivision_flat=self._referentiel.subdivisions_flat(),
        )
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setSortRole(Qt.ItemDataRole.DisplayRole)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        # Tri interactif activé ; _apply_loaded_rows remet l'ordre métier après chaque rechargement.
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.doubleClicked.connect(self._open_dossier)

        # Délégué boutons Actions sur la dernière colonne
        self._action_delegate = PersonnelActionDelegate(self._table)
        self._action_delegate.set_callback(self._on_action_btn)
        actions_col = self._model.columnCount() - 1
        self._table.setItemDelegateForColumn(actions_col, self._action_delegate)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # toutes les colonnes redimensionnables
        hdr.setSectionResizeMode(actions_col, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(actions_col, PersonnelActionDelegate.ACTIONS_COL_WIDTH)
        hdr.setMinimumSectionSize(60)
        hdr.setDefaultSectionSize(120)
        hdr.setStretchLastSection(False)

        self._counter = QLabel()
        self._counter.setObjectName("personnelListCounter")

        self._padmin_combo = QComboBox()
        self._grade_combo = QComboBox()
        self._structure_combo = QComboBox()
        self._subarme_combo = QComboBox()
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
        self._subarme_combo.currentIndexChanged.connect(self._apply_filter)

        filters_form = QFormLayout()
        filters_form.setSpacing(10)
        filters_form.setHorizontalSpacing(16)
        filters_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        filters_form.addRow("Position administrative :", self._padmin_combo)
        filters_form.addRow("Grade :", self._grade_combo)
        filters_form.addRow("Structure :", self._structure_combo)
        filters_form.addRow("Subdivision d'arme :", self._subarme_combo)
        self._subarme_field_label = filters_form.labelForField(self._subarme_combo)

        reset_btn = QPushButton("Réinitialiser les filtres")
        reset_btn.setToolTip(
            "Remettre tous les critères et la recherche libre à « Tous » / vide."
        )
        reset_btn.clicked.connect(self._reset_filters)
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_row.addWidget(reset_btn)

        filters_group = QGroupBox("Critères (Padmin, grade, structure, subdivision)")
        filters_group.setObjectName("personnelFiltersGroup")
        fg_layout = QVBoxLayout(filters_group)
        fg_layout.addLayout(filters_form)
        fg_layout.addLayout(reset_row)

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

        self._filters_body = QWidget()
        self._filters_body.setObjectName("personnelFiltersBody")
        fb_layout = QVBoxLayout(self._filters_body)
        fb_layout.setContentsMargins(0, 0, 0, 0)
        fb_layout.setSpacing(0)
        fb_layout.addWidget(filters_group)

        self._toggle_filters_btn = QToolButton()
        self._toggle_filters_btn.setObjectName("personnelFiltersToggle")
        self._toggle_filters_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self._toggle_filters_btn.setToolTip(
            "Afficher ou masquer les listes de filtre (position administrative, grade, "
            "structure ; la subdivision d'arme apparaît lorsqu'une structure est choisie)."
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

        self._menu_btn = QToolButton()
        self._menu_btn.setObjectName("personnelMoreMenuBtn")
        self._menu_btn.setText("⋯")
        self._menu_btn.setToolTip("Liste complète, présents, modification, export…")
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self)
        more_menu.addAction(
            "Tout afficher (recharger la liste complète)",
            self._show_all,
        )
        more_menu.addAction(
            "Présents seulement",
            self._show_presents,
        )
        more_menu.addAction(
            "Réinitialiser l'ordre métier",
            self._reset_sort_order,
        )
        more_menu.addSeparator()
        more_menu.addAction(
            "Fiche dossier (parcours & traçabilité)…",
            self._open_dossier,
        )
        self._act_edit_row = more_menu.addAction(
            "Modifier la ligne sélectionnée…",
            self._edit_current,
        )
        more_menu.addSeparator()
        self._act_new_row = more_menu.addAction(
            "Nouveau personnel…",
            self._create_new,
        )
        self._act_delete_row = more_menu.addAction(
            "Supprimer la ligne sélectionnée…",
            self._delete_current,
        )
        more_menu.addSeparator()
        more_menu.addAction(
            "Exporter CSV…",
            self._export_csv,
        )
        more_menu.addAction(
            "Exporter Excel (liste filtrée)…",
            self._export_excel_filtered,
        )
        more_menu.addAction(
            "Exporter Excel par position administrative…",
            self._export_excel_by_padmin,
        )
        more_menu.addSeparator()
        more_menu.addAction(
            "Imprimer / Exporter PDF…",
            self._print_preview,
        )
        more_menu.aboutToShow.connect(self._sync_more_menu_edit_enabled)
        self._menu_btn.setMenu(more_menu)

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
        top_row.addWidget(self._search, stretch=2)
        top_row.addWidget(self._btn_columns, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self._menu_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self._btn_refresh, alignment=Qt.AlignmentFlag.AlignVCenter)

        filter_panel = QWidget()
        filter_panel.setObjectName("personnelFilterPanel")
        fp_layout = QVBoxLayout(filter_panel)
        fp_layout.setSpacing(10)
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
        self._footer.setMinimumHeight(22)
        self._footer.setWordWrap(True)
        self._footer.setText(self._FOOTER_DEFAULT_HINT)

        self._lock_banner = QLabel()
        self._lock_banner.setWordWrap(True)
        self._lock_banner.setObjectName("personnelLockBanner")
        self._lock_banner.setVisible(False)
        self._lock_banner.setStyleSheet(
            "background: #fff3e0; color: #e65100; padding: 10px 12px; "
            "border-radius: 6px; border: 1px solid #ffcc80;"
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self._lock_banner)
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
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self._search.setFocus())
        QShortcut(QKeySequence("Ctrl+E"), self, self._export_csv)
        QShortcut(QKeySequence("Ctrl+Shift+E"), self, self._export_excel_filtered)
        QShortcut(QKeySequence("Ctrl+P"), self, self._print_preview)

    def showEvent(self, event: QShowEvent) -> None:
        """Chaque ouverture de l’onglet : rechargement trié depuis la base (ordre métier appliqué)."""
        super().showEvent(event)
        if event.spontaneous():
            return
        self._apply_column_visibility()
        self._refresh_all(silent=True)
        self._refresh_lock_banner()

    def _refresh_lock_banner(self) -> None:
        st = evaluate_editing_state(self._settings)
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
        elif st.detail and "Aucune ligne TabSme" in st.detail:
            self._lock_banner.setText(st.detail)
            self._lock_banner.setTextFormat(Qt.TextFormat.PlainText)
            self._lock_banner.setVisible(True)
        else:
            self._lock_banner.setVisible(False)

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

    def _sync_more_menu_edit_enabled(self) -> None:
        locked = evaluate_editing_state(self._settings).locked
        n_sel = len(self._selected_row_objects())
        if hasattr(self, "_act_edit_row") and self._act_edit_row is not None:
            self._act_edit_row.setEnabled(not locked)
        if hasattr(self, "_act_new_row") and self._act_new_row is not None:
            self._act_new_row.setEnabled(not locked)
        if hasattr(self, "_act_delete_row") and self._act_delete_row is not None:
            self._act_delete_row.setEnabled(not locked)
            if n_sel > 1:
                self._act_delete_row.setText(f"Supprimer {n_sel} lignes sélectionnées…")
            else:
                self._act_delete_row.setText("Supprimer la ligne sélectionnée…")

    def _on_toggle_filters(self) -> None:
        self._filters_body.setVisible(not self._filters_body.isVisible())
        self._sync_filters_toggle_label()

    def _sync_filters_toggle_label(self) -> None:
        expanded = self._filters_body.isVisible()
        self._toggle_filters_btn.setText("▼ Filtres" if expanded else "▶ Filtres")

    def _schedule_filter(self) -> None:
        self._filter_debounce.stop()
        self._filter_debounce.start(self._DEBOUNCE_MS)

    def _show_status(self, message: str, ms: int = 4500) -> None:
        self._footer.setText(message)
        QTimer.singleShot(ms, self._clear_footer)

    def _clear_footer(self) -> None:
        self._footer.setText(self._FOOTER_DEFAULT_HINT)

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

        padmin_items = self._merge_unique_sorted(
            self._referentiel.position_admin_choices(),
            self._distinct_from_rows("position_admin"),
        )
        self._combo_set_items(
            self._padmin_combo,
            [(x, x) for x in padmin_items],
            prev_p,
        )

        grade_map = {g.code: g for g in self._referentiel.grades()}
        grade_codes = self._merge_unique_sorted(
            list(grade_map.keys()),
            self._distinct_from_rows("grade"),
        )
        grade_rows: list[tuple[str, str]] = []
        for code in grade_codes:
            g = grade_map.get(code)
            if g is not None:
                grade_rows.append((f"{g.code} — {g.libelle}", g.code))
            else:
                grade_rows.append((code, code))
        self._combo_set_items(self._grade_combo, grade_rows, prev_g)

        struct_map = {s.code: s for s in self._referentiel.structures()}
        struct_codes = self._merge_unique_sorted(
            list(struct_map.keys()),
            self._distinct_from_rows("structure"),
        )
        struct_rows: list[tuple[str, str]] = []
        for code in struct_codes:
            srow = struct_map.get(code)
            if srow is not None:
                struct_rows.append(
                    (f"{srow.code} — {srow.designation}", srow.code)
                )
            else:
                struct_rows.append((code, code))
        self._combo_set_items(self._structure_combo, struct_rows, prev_s)

        self._fill_subarme_combo(preserve_data=self._subarme_combo.currentData())

    def _subarme_choices_for_structure(self, structure_code: str | None) -> list[str]:
        """Sans structure choisie, pas de liste (le filtre subdivision reste masqué)."""
        if structure_code is None:
            return []
        from_db = self._referentiel.subdivisions_arme_for_structure(structure_code)
        from_data = [
            (r.subdivision_arme or "").strip()
            for r in self._all_rows
            if (r.structure or "").strip() == structure_code
            and (r.subdivision_arme or "").strip()
        ]
        return self._merge_unique_sorted(from_db, from_data)

    def _sync_subarme_row_visibility(self) -> None:
        """Affiche le filtre subdivision uniquement si une structure est sélectionnée (pas « Tous »)."""
        show = self._structure_combo.currentData() is not None
        self._subarme_combo.setVisible(show)
        lab = getattr(self, "_subarme_field_label", None)
        if lab is not None:
            lab.setVisible(show)

    def _fill_subarme_combo(self, preserve_data: object = None) -> None:
        if preserve_data is None:
            preserve_data = self._subarme_combo.currentData()
        struct_code = self._structure_combo.currentData()
        choices = self._subarme_choices_for_structure(struct_code)
        self._combo_set_items(
            self._subarme_combo,
            [(x, x) for x in choices],
            preserve_data,
        )
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
        self._apply_filter()

    def _apply_loaded_rows(self, rows: list[ListeGeneraleRow]) -> None:
        self._all_rows = rows
        self._filter_debounce.stop()
        self._proxy.sort(-1)  # restaure l'ordre métier après chaque rechargement
        self._populate_structural_combos()
        self._apply_filter()
        self._resize_columns_to_content()

    def apply_runtime_after_config_change(self) -> None:
        """Après enregistrement des chemins : recharge la liste et les subdivisions affichées."""
        self._settings = self._service.settings
        rows = run_with_progress(
            self,
            "Rechargement",
            "Lecture de la liste générale depuis la nouvelle configuration…",
            self._service.liste_complete,
        )
        if rows is None:
            return
        self._model.set_subdivision_flat(self._referentiel.subdivisions_flat())
        self._apply_loaded_rows(rows)
        self._refresh_lock_banner()

    def _refresh_all(self, *, silent: bool = False) -> None:
        rows = run_with_progress(
            self,
            "Liste générale",
            "Chargement depuis la base…",
            self._service.liste_complete,
        )
        if rows is None:
            return
        self._apply_loaded_rows(rows)
        self._refresh_lock_banner()

    def _show_all(self) -> None:
        rows = run_with_progress(
            self,
            "Liste générale",
            "Chargement de la liste complète…",
            self._service.liste_complete,
        )
        if rows is None:
            return
        self._apply_loaded_rows(rows)

    def _show_presents(self) -> None:
        rows = run_with_progress(
            self,
            "Liste générale",
            "Chargement des personnels « présents »…",
            self._service.liste_presents,
        )
        if rows is None:
            return
        self._apply_loaded_rows(rows)

    def _structural_filter_matches(self, row: ListeGeneraleRow) -> bool:
        if self._only_empty_structure:
            if (row.structure or "").strip():
                return False
        p = self._padmin_combo.currentData()
        if p is not None and (row.position_admin or "").strip() != p:
            return False
        g = self._grade_combo.currentData()
        if g is not None and (row.grade or "").strip() != g:
            return False
        s = self._structure_combo.currentData()
        if s is not None and (row.structure or "").strip() != s:
            return False
        sub = self._subarme_combo.currentData()
        if sub is not None and (row.subdivision_arme or "").strip() != sub:
            return False
        return True

    def _apply_filter(self) -> None:
        rows = [r for r in self._all_rows if self._structural_filter_matches(r)]
        needle = self._search.text().strip().lower()
        if needle:
            rows = [r for r in rows if _free_text_matches(r, needle)]
        self._model.set_rows(rows)
        self._update_counter_and_empty(len(rows))

    def _update_counter_and_empty(self, displayed: int) -> None:
        total = len(self._all_rows)
        self._counter.setText(
            f"{_fmt_num(displayed)} / {_fmt_num(total)} lignes"
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
        elif q or has_struct:
            self._empty_label.setText(
                "Aucun résultat avec les filtres et la recherche actuels. "
                "Élargissez un critère ou utilisez « Réinitialiser les filtres »."
            )
        else:
            self._empty_label.setText("Aucune ligne à afficher pour cette sélection.")

    def _current_row_object(self) -> ListeGeneraleRow | None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return None
        src = self._proxy.mapToSource(idx)
        if not src.isValid():
            return None
        return self._model.row_at(src.row())

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
            self._open_dossier_for(row)
        elif action == ACT_EDIT:
            self._edit_row(row)
        elif action == ACT_DELETE:
            self._delete_row(row)

    def _open_dossier_for(self, row_obj: ListeGeneraleRow) -> None:
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(self, "Personnel", "Ligne introuvable en base. Actualisez la liste.")
            return
        st = evaluate_editing_state(self._settings)
        PersonnelDossierDialog(
            self, fresh, self._referentiel, self._service, allow_edit=not st.locked,
        ).exec()
        self._refresh_all(silent=True)
        self._refresh_lock_banner()

    def _edit_row(self, row_obj: ListeGeneraleRow) -> None:
        st = evaluate_editing_state(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(self, "Personnel", "Ligne introuvable en base. Actualisez la liste.")
            return
        dlg = PersonnelEditDialog(self, fresh, self._referentiel)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        try:
            self._service.update_personnel(fresh, new_row)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._show_status("Modifications enregistrées. Trace ajoutée à data/personnel_audit.jsonl.")
        self._refresh_all(silent=True)

    def _delete_row(self, row_obj: ListeGeneraleRow) -> None:
        st = evaluate_editing_state(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        name = row_obj.noms or f"Id {row_obj.id}"
        if not confirm_yes_no(
            self, "Supprimer",
            f"Supprimer définitivement la fiche de « {name} » (Id {row_obj.id}) ?\n\n"
            "Cette action est irréversible. Un événement d'audit sera enregistré.",
            default_no=True,
        ):
            return
        try:
            self._service.delete_personnel(row_obj)
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self._show_status(f"Fiche supprimée : {name}. Trace dans data/personnel_audit.jsonl.")
        self._refresh_all(silent=True)

    # ------------------------------------------------------------------ actions via sélection courante

    def _open_dossier(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        self._open_dossier_for(row_obj)

    def _edit_current(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        self._edit_row(row_obj)

    def _create_new(self) -> None:
        st = evaluate_editing_state(self._settings)
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
        dlg = PersonnelEditDialog(self, blank, self._referentiel)
        dlg.setWindowTitle("Nouvelle fiche personnel")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.result_row()
        if not new_row.noms:
            QMessageBox.warning(self, "Création", "Le nom est obligatoire.")
            return
        try:
            new_id = self._service.create_personnel(new_row)
        except Exception as e:
            QMessageBox.critical(self, "Création", str(e))
            return
        self._show_status(f"Personnel créé (id {new_id}). Trace dans data/personnel_audit.jsonl.")
        self._refresh_all(silent=True)

    def _delete_current(self) -> None:
        rows = self._selected_row_objects()
        if not rows:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ou plusieurs lignes.")
            return
        if len(rows) == 1:
            self._delete_row(rows[0])
            return
        st = evaluate_editing_state(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        preview = ", ".join(r.noms or f"Id {r.id}" for r in rows[:3])
        if len(rows) > 3:
            preview += f"… et {len(rows) - 3} autre(s)"
        if not confirm_yes_no(
            self, "Supprimer plusieurs fiches",
            f"Supprimer définitivement {len(rows)} fiche(s) ?\n\n{preview}\n\n"
            "Cette action est irréversible. Un événement d'audit sera enregistré pour chaque fiche.",
            default_no=True,
        ):
            return
        errors: list[str] = []
        for r in rows:
            try:
                self._service.delete_personnel(r)
            except Exception as e:
                errors.append(f"{r.noms or r.id}: {e}")
        if errors:
            QMessageBox.critical(self, "Suppression", "Erreurs :\n" + "\n".join(errors))
        else:
            self._show_status(f"{len(rows)} fiche(s) supprimée(s). Traces dans data/personnel_audit.jsonl.")
        self._refresh_all(silent=True)

    def _on_table_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        has_row = idx.isValid()
        if has_row:
            self._table.setCurrentIndex(idx)
        row_obj = self._current_row_object() if has_row else None
        selected = self._selected_row_objects()
        n_sel = len(selected)
        locked = evaluate_editing_state(self._settings).locked
        menu = QMenu(self)

        act_view = menu.addAction("Voir la fiche…\tEntrée", self._open_dossier)
        act_view.setEnabled(n_sel == 1)
        act_mod = menu.addAction("Modifier…\tF2", self._edit_current)
        act_mod.setEnabled(n_sel == 1 and not locked)
        menu.addSeparator()
        act_new = menu.addAction("Nouveau personnel…\tCtrl+N", self._create_new)
        act_new.setEnabled(not locked)
        del_label = f"Supprimer {n_sel} fiche(s)…\tSuppr" if n_sel > 1 else "Supprimer…\tSuppr"
        act_del = menu.addAction(del_label, self._delete_current)
        act_del.setEnabled(n_sel > 0 and not locked)
        menu.addSeparator()
        act_copy = menu.addAction(
            "Copier le nom",
            lambda: self._copy_name(row_obj),
        )
        act_copy.setEnabled(n_sel == 1 and row_obj is not None and bool(row_obj.noms))
        menu.addSeparator()
        menu.addAction("Tout afficher (liste complète)", self._show_all)
        menu.addAction("Présents seulement", self._show_presents)
        menu.addSeparator()
        menu.addAction("Exporter CSV…\tCtrl+E", self._export_csv)
        menu.addAction("Exporter Excel (liste)…\tCtrl+Maj+E", self._export_excel_filtered)
        menu.addAction("Exporter Excel par position…", self._export_excel_by_padmin)
        menu.addAction("Imprimer / PDF…\tCtrl+P", self._print_preview)
        menu.addAction("Actualiser\tCtrl+R", lambda: self._refresh_all(silent=False))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_name(self, row_obj: ListeGeneraleRow | None) -> None:
        if row_obj and row_obj.noms:
            QApplication.clipboard().setText(row_obj.noms)
            self._show_status(f"Nom copié : {row_obj.noms}")

    def _apply_column_visibility(self) -> None:
        ctx = load_sme_context()
        hidden_set = set(ctx.hidden_columns)
        extra_shown = set(ctx.extra_columns_shown)
        for col_idx, key in _HIDEABLE_COLS.items():
            self._table.setColumnHidden(col_idx, key in hidden_set)
        for col_idx, key in _EXTRA_COLS.items():
            self._table.setColumnHidden(col_idx, key not in extra_shown)

    def _on_columns_dialog(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QVBoxLayout

        from sme_gend.ui.personnel_table_model import _COLUMNS

        ctx = load_sme_context()
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

        ctx = load_sme_context()
        ctx.hidden_columns = [key for key, cb in default_cbs.items() if not cb.isChecked()]
        ctx.extra_columns_shown = [key for key, cb in extra_cbs.items() if cb.isChecked()]
        save_sme_context(ctx)
        self._apply_column_visibility()

    def mark_dirty(self) -> None:
        """Appelé depuis shell_window lors d'un changement de mois actif."""
        self._refresh_lock_banner()

    def notify_settings_changed(self) -> None:
        """Appelé depuis shell_window quand les paramètres changent (mois, chemins, etc.)."""
        self._refresh_lock_banner()

    def _resize_columns_to_content(self) -> None:
        """Redimensionne les colonnes au contenu (sauf Actions). Appelé après chaque rechargement complet."""
        hdr = self._table.horizontalHeader()
        actions_col = self._model.columnCount() - 1
        for c in range(actions_col):
            hdr.resizeSectionToContents(c)

    def _reset_sort_order(self) -> None:
        self._proxy.sort(-1)
        self._show_status("Ordre métier restauré.")

    def _current_filtered_rows(self) -> list[ListeGeneraleRow]:
        """Lignes actuellement affichées (après filtres + recherche), dans l'ordre proxy."""
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

    def _export_excel_filtered(self) -> None:
        from sme_gend.services.personnel_excel_export import export_liste_filtered

        rows = self._current_filtered_rows()
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la liste en Excel",
            str(Path.home() / "liste_generale.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        mois = (load_sme_context().mois_actif or "").strip()
        title = f"Liste générale {mois}".strip()

        def _work() -> int:
            return export_liste_filtered(
                rows,
                Path(path),
                hidden_keys=self._effective_hidden_keys(),
                flat=list(self._referentiel.subdivisions_flat()),
                sheet_title=title,
            )

        n = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if n is None:
            return
        QMessageBox.information(
            self, "Export Excel",
            f"Fichier enregistré ({n} ligne(s)) :\n{path}",
        )

    def _export_excel_by_padmin(self) -> None:
        from sme_gend.services.personnel_excel_export import export_liste_by_padmin

        rows = self._current_filtered_rows()
        if not rows:
            QMessageBox.information(self, "Export Excel", "Aucune ligne à exporter.")
            return
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
                hidden_keys=self._effective_hidden_keys(),
                flat=list(self._referentiel.subdivisions_flat()),
            )

        counts = run_with_progress(self, "Export Excel", "Génération du fichier…", _work)
        if counts is None:
            return
        details = "\n".join(f"  {p} : {n}" for p, n in sorted(counts.items(), key=lambda x: -x[1]))
        QMessageBox.information(
            self, "Export Excel par position",
            f"Fichier enregistré :\n{path}\n\n{len(counts)} feuille(s) :\n{details}",
        )

    def _print_preview(self) -> None:
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter

        rows = self._current_filtered_rows()
        hidden = self._effective_hidden_keys()
        flat = list(self._referentiel.subdivisions_flat())
        mois = (load_sme_context().mois_actif or "").strip()

        from sme_gend.services.personnel_excel_export import EXPORT_COLS
        from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

        cols = [(k, h) for k, h, _ in EXPORT_COLS if k not in hidden]

        # Génération HTML
        html_parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<style>",
            "body{font-family:Calibri,Arial,sans-serif;font-size:9pt;margin:10mm;}",
            "h2{font-size:12pt;color:#1a237e;margin:0 0 6px 0;}",
            ".meta{font-size:8pt;color:#555;margin-bottom:8px;}",
            "table{border-collapse:collapse;width:100%;}",
            "th{background:#1a237e;color:#fff;font-size:8.5pt;padding:4px 6px;text-align:left;}",
            "td{font-size:8pt;padding:3px 6px;border-bottom:1px solid #ddd;}",
            "tr:nth-child(even)td{background:#eef2ff;}",
            "</style></head><body>",
            f"<h2>Liste générale{' — ' + mois if mois else ''}</h2>",
            f"<p class='meta'>{len(rows)} ligne(s) affichée(s)</p>",
            "<table><thead><tr>",
        ]
        for _, header in cols:
            html_parts.append(f"<th>{header}</th>")
        html_parts.append("</tr></thead><tbody>")

        for row in rows:
            html_parts.append("<tr>")
            for key, _ in cols:
                if key == "subdivision_arme":
                    val = format_subdivision_arme_display(row.subdivision_arme, flat) or ""
                else:
                    val = str(getattr(row, key) or "").strip()
                html_parts.append(f"<td>{_html.escape(val)}</td>")
            html_parts.append("</tr>")

        html_parts.append("</tbody></table></body></html>")
        html_content = "".join(html_parts)

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)

        doc = QTextDocument()
        doc.setHtml(html_content)

        dlg = QPrintPreviewDialog(printer, self)
        dlg.setWindowTitle("Aperçu avant impression — Liste générale")
        dlg.paintRequested.connect(lambda p: doc.print_(p))
        dlg.resize(1100, 750)
        dlg.exec()

    def _export_csv(self) -> None:
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


def _free_text_matches(row: ListeGeneraleRow, needle: str) -> bool:
    parts = [
        str(row.id),
        row.noms or "",
        row.grade or "",
        row.fonction or "",
        row.structure or "",
        row.position_admin or "",
        row.subdivision_arme or "",
        row.matricule_militaire or "",
        row.matricule_solde or "",
        row.diplome or "",
        row.obs or "",
    ]
    blob = " ".join(parts).lower()
    return needle in blob
