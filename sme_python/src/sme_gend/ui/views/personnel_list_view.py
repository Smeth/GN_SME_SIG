"""Liste générale — grille + filtres (ancienne fenêtre principale)."""

from __future__ import annotations

import csv

from PySide6.QtCore import QSize, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent
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

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog
from sme_gend.ui.personnel_table_model import PersonnelTableModel


class PersonnelListView(QWidget):
    """Consultation de la liste générale (SQLite ou Access selon la configuration)."""

    _DEBOUNCE_MS = 280
    _FOOTER_DEFAULT_HINT = (
        "Ordre : COMGEND → C2 CEMGEND → INSPECTEUR IT → DIRECTEUR DOE → DPF → DSF → DIRTRANS → "
        "DTRJD → DIRMAT → DDT, puis toutes les autres fonctions par grade (PrefOrd) ; "
        "les en-têtes ne réordonnent pas le tableau."
    )

    def __init__(
        self,
        service: PersonnelService,
        referentiel: ReferentielService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._referentiel = referentiel
        self._all_rows: list[ListeGeneraleRow] = []

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
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        # Tri par colonnes désactivé : l'ordre des lignes suit la règle métier (fonctions prioritaires + grade).
        self._table.setSortingEnabled(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.doubleClicked.connect(self._open_dossier)

        hdr = self._table.horizontalHeader()
        ncol = self._model.columnCount()
        for c in range(ncol - 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(ncol - 1, QHeaderView.ResizeMode.Stretch)

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
            "Recherche : nom, prénom, matricule, fonction…"
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
        more_menu.addSeparator()
        more_menu.addAction(
            "Fiche dossier (parcours & traçabilité)…",
            self._open_dossier,
        )
        more_menu.addAction(
            "Modifier la ligne sélectionnée…",
            self._edit_current,
        )
        more_menu.addAction(
            "Exporter CSV…",
            self._export_csv,
        )
        self._menu_btn.setMenu(more_menu)

        top_bar = QFrame()
        top_bar.setObjectName("personnelListTopBar")
        top_row = QHBoxLayout(top_bar)
        top_row.setSpacing(10)
        top_row.addWidget(self._toggle_filters_btn)
        top_row.addWidget(self._counter, alignment=Qt.AlignmentFlag.AlignVCenter)
        top_row.addStretch(1)
        top_row.addWidget(self._search, stretch=2)
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

        layout = QVBoxLayout(self)
        layout.addWidget(filter_panel)
        layout.addWidget(self._stack, stretch=1)
        layout.addWidget(self._footer)

        QShortcut(QKeySequence("Ctrl+R"), self, lambda: self._refresh_all(silent=False))
        QShortcut(QKeySequence(Qt.Key.Key_Return), self._table, self._open_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self._table, self._open_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self._table, self._edit_current)

    def showEvent(self, event: QShowEvent) -> None:
        """Chaque ouverture de l’onglet : rechargement trié depuis la base (ordre métier appliqué)."""
        super().showEvent(event)
        self._refresh_all(silent=True)

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
        self._fill_subarme_combo(preserve_data=None)
        self._apply_filter()

    def _reset_filters(self) -> None:
        if not confirm_yes_no(
            self,
            "Réinitialiser les filtres",
            "Remettre tous les filtres et la recherche à zéro ?",
            default_no=True,
        ):
            return
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
        self._populate_structural_combos()
        self._apply_filter()

    def apply_runtime_after_config_change(self) -> None:
        """Après enregistrement des chemins : recharge la liste et les subdivisions affichées."""
        try:
            rows = self._service.liste_complete()
        except OSError as e:
            QMessageBox.warning(self, "Configuration", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Configuration", str(e))
            return
        self._model.set_subdivision_flat(self._referentiel.subdivisions_flat())
        self._apply_loaded_rows(rows)

    def _refresh_all(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser",
                "Recharger la liste générale complète depuis la base configurée ?",
                default_no=False,
            ):
                return
        rows = run_with_progress(
            self,
            "Liste générale",
            "Chargement depuis la base…",
            self._service.liste_complete,
        )
        if rows is None:
            return
        self._apply_loaded_rows(rows)

    def _show_all(self) -> None:
        if not confirm_yes_no(
            self,
            "Liste complète",
            "Recharger et afficher la liste générale complète depuis la base ?",
            default_no=True,
        ):
            return
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
        if not confirm_yes_no(
            self,
            "Présents seulement",
            "Recharger et n’afficher que les personnels comptés présents (Padmin « présent » ou stage interne) ?",
            default_no=True,
        ):
            return
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
        hdr = self._table.horizontalHeader()
        ncol = self._model.columnCount()
        for c in range(ncol - 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)

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

    def _open_dossier(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(
                self,
                "Personnel",
                "Ligne introuvable en base (identifiant peut-être obsolète). Actualisez la liste.",
            )
            return
        PersonnelDossierDialog(self, fresh, self._referentiel, self._service).exec()
        self._refresh_all(silent=True)

    def _edit_current(self) -> None:
        row_obj = self._current_row_object()
        if row_obj is None:
            QMessageBox.information(self, "Personnel", "Sélectionnez une ligne.")
            return
        fresh = self._service.get_by_id(row_obj.id)
        if fresh is None:
            QMessageBox.warning(
                self,
                "Personnel",
                "Ligne introuvable en base (identifiant peut-être obsolète). Actualisez la liste.",
            )
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
        self._show_status(
            "Modifications enregistrées. Trace ajoutée à data/personnel_audit.jsonl."
        )
        self._refresh_all()

    def _on_table_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        if idx.isValid():
            self._table.setCurrentIndex(idx)
        menu = QMenu(self)
        menu.addAction("Tout afficher (liste complète)", self._show_all)
        menu.addAction("Présents seulement", self._show_presents)
        menu.addSeparator()
        menu.addAction("Fiche dossier…", self._open_dossier)
        menu.addAction("Modifier…", self._edit_current)
        menu.addAction("Exporter CSV…", self._export_csv)
        menu.addSeparator()
        menu.addAction("Actualiser", self._refresh_all)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _export_csv(self) -> None:
        if not confirm_yes_no(
            self,
            "Export CSV",
            "Exporter la liste affichée (filtres appliqués) vers un fichier CSV ?",
            default_no=True,
        ):
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter en CSV",
            "",
            "Fichiers CSV (*.csv);;Tous les fichiers (*)",
        )
        if not path:
            return
        if not confirm_overwrite_file(self, path):
            return
        headers = [
            self._model.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for c in range(self._model.columnCount())
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
                    for c in range(self._model.columnCount()):
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
        row.id,
        row.noms or "",
        row.fonction or "",
        row.matricule_militaire or "",
        row.matricule_solde or "",
        row.diplome or "",
        row.obs or "",
    ]
    blob = " ".join(parts).lower()
    return needle in blob
