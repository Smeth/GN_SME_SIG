"""Hub structures : typologie, subdivisions RSA et fonctions par structure."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.structure_famille import (
    STRUCTURE_FAMILLE_CHOICES,
    resolved_structure_famille,
    structure_famille_label,
    structure_matches_hub_filter,
)
from sme_gend.models.structure import StructureRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.services.structure_organigram_service import StructureOrganigramService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.structure_bulk_famille_dialog import StructureBulkFamilleDialog
from sme_gend.ui.structure_edit_dialog import StructureEditDialog
from sme_gend.ui.structure_subdivisions_dialog import StructureSubdivisionsDialog
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.responsive_panels import WrapLayout, ResponsiveSplitter, scroll_panel


class _NumberItem(QTableWidgetItem):
    def __lt__(self, other):
        return int(self.text()) < int(other.text())


class _StructureTable(QTableWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.columnCount() <= 2:
            return
        header = self.horizontalHeader()
        other = sum(self.columnWidth(c) for c in range(self.columnCount()) if c != 2 and not self.isColumnHidden(c))
        compact = self.viewport().width() < other + 200
        mode = QHeaderView.ResizeMode.Interactive if compact else QHeaderView.ResizeMode.Stretch
        if header.sectionResizeMode(2) != mode:
            header.setSectionResizeMode(2, mode)
        if compact and self.columnWidth(2) < 200:
            self.setColumnWidth(2, 260)


class StructureHubView(QWidget):
    """Liste des structures avec détail subdivisions + fonctions."""

    request_open_fonctions = Signal(str)  # code structure → module Fonctions
    structures_changed = Signal()

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._org = StructureOrganigramService(service.settings)
        self._structures: list[StructureRow] = []
        self._org_by_code: dict = {}
        self._fn_counts: dict[str, int] = {}
        self._linked_labels = {}
        self._view_data = ViewDataState(stale=True)
        self._checked_codes = set()
        self._filling = False
        self._status = QLabel("Structures non chargées.")
        self._status.setWordWrap(True)
        self._status.setMinimumWidth(200)


        self._filter_famille = QComboBox()
        self._filter_famille.addItem("Tous les types", "")
        for code, label in STRUCTURE_FAMILLE_CHOICES:
            if code:
                self._filter_famille.addItem(label, code)
        self._filter_famille.currentIndexChanged.connect(self._refill_table)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher code ou désignation…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refill_table)

        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.clicked.connect(lambda: self.reload(force=True))
        self._btn_refresh.hide()  # The parent screen owns the single refresh button.

        self._btn_check_all = QPushButton("Tout cocher")
        self._btn_check_all.clicked.connect(self._check_all_visible)
        self._btn_check_none = QPushButton("Tout décocher")
        self._btn_check_none.clicked.connect(self._uncheck_all_visible)
        self._btn_edit_st = QPushButton("Modifier la structure…")
        self._btn_edit_st.setToolTip("Code, désignation, type d'unité, ordre de tri.")
        self._btn_edit_st.clicked.connect(self._edit_structure_identity)
        self._btn_add = QPushButton("Nouvelle structure…")
        self._btn_add.clicked.connect(self._on_add_structure)
        self._btn_delete = QPushButton("Supprimer")
        self._btn_delete.clicked.connect(self._on_delete_structure)
        self._btn_bulk_type = QPushButton("Type sur cochées…")
        self._btn_bulk_type.setObjectName("primaryButton")
        self._btn_bulk_type.setToolTip("Appliquer un type d'unité à toutes les structures cochées.")
        self._btn_bulk_type.clicked.connect(self._bulk_set_famille)
        self._btn_export = QPushButton("Exporter Excel…")
        self._btn_export.setToolTip(
            "Référentiel structures : code, type d'unité, subdivisions, fonctions, effectifs."
        )
        self._btn_export.clicked.connect(self._export_referentiel_excel)

        bar = QHBoxLayout()
        bar.addWidget(self._filter_famille)
        bar.addWidget(self._search, stretch=1)

        from sme_gend.ui.responsive_panels import WrapLayout
        bulk_bar = WrapLayout()
        bulk_bar.addWidget(self._btn_add)
        bulk_bar.addWidget(self._btn_edit_st)
        bulk_bar.addWidget(self._btn_export)
        self._more_actions = QToolButton()
        self._more_actions.setText('Autres actions')
        self._more_actions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        actions = QMenu(self)
        self._menu_buttons = []
        for button in (self._btn_check_all, self._btn_check_none, self._btn_bulk_type, self._btn_delete):
            action = actions.addAction(button.text(), button.click)
            self._menu_buttons.append((action, button))
            button.hide()
        self._more_actions.setMenu(actions)
        bulk_bar.addWidget(self._more_actions)

        self._table = _StructureTable()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["", "Code", "Désignation", "Type d'unité", "Subdiv.", "Fonct.", "Effectif"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(2, 260)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 110), (3, 130), (4, 65), (5, 65), (6, 75)):
            self._table.setColumnWidth(column, width)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setColumnWidth(0, 36)
        self._table.itemSelectionChanged.connect(self._update_detail)
        self._table.itemChanged.connect(self._on_item_changed)

        self._detail_title = QLabel("Sélectionnez une structure.")
        self._detail_title.setWordWrap(True)

        self._subdiv_list = self._detail_list()
        self._fonct_list = self._detail_list()
        self._fonct_list.setMinimumHeight(120)

        self._btn_subdiv = QPushButton("Subdivisions d'arme…")
        self._btn_subdiv.clicked.connect(self._open_subdivisions)
        self._btn_fonct = QPushButton("Module Fonctions…")
        self._btn_fonct.setToolTip(
            "Ouvre le module Fonctions (référentiel et postes vacants) pour cette structure."
        )
        self._btn_fonct.clicked.connect(self._open_fonctions)

        subdiv_box = QGroupBox("Subdivisions d'arme")
        subdiv_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        subdiv_lay = QVBoxLayout(subdiv_box)
        subdiv_lay.addWidget(self._subdiv_list, stretch=1)
        subdiv_lay.addWidget(self._btn_subdiv)

        fonct_box = QGroupBox("Fonctions rattachées")
        fonct_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        fonct_lay = QVBoxLayout(fonct_box)
        fonct_lay.addWidget(self._fonct_list, stretch=1)
        fonct_lay.addWidget(self._btn_fonct)

        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(8, 0, 4, 0)
        dv.addWidget(self._detail_title)
        dv.addWidget(subdiv_box, stretch=1)
        dv.addWidget(fonct_box, stretch=2)

        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail_scroll.setWidget(detail)

        split = ResponsiveSplitter()
        split.addWidget(self._table)
        split.addWidget(detail_scroll)
        self._split = split
        split.setStretchFactor(0, 7)
        split.setStretchFactor(1, 3)
        split.setSizes([900, 380])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(bar)
        root.addLayout(bulk_bar)
        root.addWidget(self._status)
        root.addWidget(split, stretch=1)
        self._sync_actions()

    @staticmethod
    def _detail_list() -> QListWidget:
        widget = QListWidget()
        widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.setWordWrap(True)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        widget.setMinimumHeight(72)
        return widget

    @staticmethod
    def _fill_detail_list(widget: QListWidget, lines: list[str], empty: str) -> None:
        widget.clear()
        shown = lines or [empty]
        for line in shown:
            item = QListWidgetItem(line)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            widget.addItem(item)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()
        self._status.setText("Données non actualisées. Actualisez avant une modification ou un export.")
        self._sync_actions()

    def _sync_actions(self):
        from sme_gend.ui.check_all import sync_check_all
        visible = [self._table.item(i, 1).text() for i in range(self._table.rowCount())
                   if not self._table.isRowHidden(i) and self._table.item(i, 1)]
        sync_check_all(self._btn_check_all, visible, self._checked_codes)
        ready = self._view_data.is_ready() and self._org.settings == self._service.settings
        try:
            editable = ready and not evaluate_editing_state(self._service.settings).locked
        except Exception:
            editable = False
        selected = self.selected_structure_code() is not None
        for button in (self._btn_edit_st, self._btn_delete):
            button.setEnabled(editable and selected)
        self._btn_add.setEnabled(editable)
        self._btn_bulk_type.setEnabled(editable and bool(self._checked_codes))
        self._btn_export.setEnabled(ready)
        self._btn_subdiv.setEnabled(ready and selected)
        self._btn_fonct.setEnabled(ready and selected)
        for action, button in self._menu_buttons:
            action.setText(button.text())
            action.setEnabled(button.isEnabled())

    def _on_item_changed(self, item):
        if self._filling or item.column() != 0:
            return
        code = self._table.item(item.row(), 1)
        if code is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_codes.add(code.text())
        else:
            self._checked_codes.discard(code.text())
        self._sync_actions()

        if self._view_data.is_ready():
            self._status.setText(f'{self._table.rowCount()} / {len(self._structures)} structures · {len(self._checked_codes)} cochées')

    def _require_fresh(self):
        if self._view_data.is_ready() and self._org.settings == self._service.settings:
            return True
        QMessageBox.warning(self, "Structures", "Données non actualisées. Actualisez avant cette action.")
        return False

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()

    def reload(self, *, force: bool = False) -> None:
        if force:
            self.mark_dirty()
        if not self._view_data.needs_load():
            return
        settings = self._service.settings
        if self._org.settings != settings:
            self._org.rebind_settings(settings)
            self._structures, self._org_by_code, self._fn_counts = [], {}, {}
            self._checked_codes.clear()
            self._refill_table()
        self._status.setText("Chargement des structures…")
        try:
            structures = run_reference_task(self,"Référentiel",lambda: self._service.structures())
            tree = run_reference_task(self,"Référentiel",lambda: self._org.fetch_tree())
            labels = run_reference_task(self,"Référentiel",lambda: self._service.fonction_linked_labels_by_structure())
            counts = {code:len(values) for code,values in labels.items()}
            if settings != self._service.settings:
                raise ValueError("Le contexte a changé pendant le chargement. Actualisez.")
        except Exception as exc:
            self.mark_dirty()
            self._status.setText("Chargement impossible : données non actualisées, actions désactivées.")
            QMessageBox.critical(self, "Structures", str(exc))
            return
        self._structures, self._org_by_code, self._fn_counts = structures, {n.code:n for n in tree}, counts
        self._linked_labels = labels
        self._checked_codes.intersection_update(st.code for st in structures)
        self._view_data.finish_load()
        self._refill_table()

    def selected_structure_code(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 1)
        return item.text().strip() if item else None

    def _checked_structure_codes(self) -> list[str]:
        return sorted(self._checked_codes)

    def _check_all_visible(self) -> None:
        items = [self._table.item(i, 0) for i in range(self._table.rowCount()) if not self._table.isRowHidden(i)]
        if items and all(item is not None and item.checkState() == Qt.CheckState.Checked for item in items):
            self._uncheck_all_visible()
            return
        for ri in range(self._table.rowCount()):
            if self._table.isRowHidden(ri):
                continue
            chk = self._table.item(ri, 0)
            if chk:
                chk.setCheckState(Qt.CheckState.Checked)

    def _uncheck_all_visible(self) -> None:
        for ri in range(self._table.rowCount()):
            if self._table.isRowHidden(ri):
                continue
            chk = self._table.item(ri, 0)
            if chk:
                chk.setCheckState(Qt.CheckState.Unchecked)

    def _export_referentiel_excel(self) -> None:
        if not self._require_fresh():
            return
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from sme_gend.config.liste_generale_scope import build_export_mois_context
        from sme_gend.services.structure_referentiel_export import export_structures_referentiel
        from sme_gend.ui.confirm_dialogs import confirm_overwrite_file

        from sme_gend.services.auth_guard import require_permission
        from sme_gend.ui.export_snapshot import visible_export_rows
        if not require_permission(self, "effectifs.export"):
            return
        if not self._structures:
            self.reload(force=True)
        if not self._structures:
            QMessageBox.information(self, "Export", "Aucune structure à exporter.")
            return

        try:
            structures = visible_export_rows(self._table, self._structures, key=lambda row: row.code, key_column=1)
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        if not structures:
            QMessageBox.information(self, "Export", "Aucune structure ne correspond aux filtres.")
            return
        subdiv: dict[str, int] = {}
        effectif: dict[str, int] = {}
        for st in structures:
            node = self._org_by_code.get(st.code)
            subdiv[st.code] = len(node.subdivisions) if node else 0
            effectif[st.code] = node.agent_count if node else 0
        fonct = {st.code: self._fonction_count(st.code) for st in structures}
        settings = self._service.settings
        ctx = build_export_mois_context(settings)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter le référentiel structures",
            str(Path.home() / "referentiel_structures.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path = f"{path}.xlsx"
        if not confirm_overwrite_file(self, path):
            return
        try:
            run_reference_task(self,"Référentiel",lambda: export_structures_referentiel(
                Path(path),
                structures,
                subdiv_counts=subdiv,
                fonction_counts=fonct,
                effectif_counts=effectif,
                mois_label=ctx.label_short(), settings=settings,
            ))
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        QMessageBox.information(
            self,
            "Export",
            f"{len(structures)} structure(s) enregistrée(s) :\n{path}",
        )

    def _bulk_set_famille(self) -> None:
        if not self._require_fresh():
            return
        codes = self._checked_structure_codes()
        if not codes:
            QMessageBox.information(
                self,
                "Action groupée",
                "Cochez au moins une structure dans la première colonne.",
            )
            return
        locked = evaluate_editing_state(self._service.settings).locked
        if locked:
            QMessageBox.warning(
                self,
                "Édition verrouillée",
                evaluate_editing_state(self._service.settings).detail or "",
            )
            return
        snapshots = [row for row in self._structures if row.code in codes]
        dlg = StructureBulkFamilleDialog(self, count=len(codes))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fam = dlg.famille_code()
        label = structure_famille_label(fam)
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Appliquer le type « {label} » à {len(codes)} structure(s) ?",
            default_no=True,
        ):
            return
        try:
            if not self._require_fresh():return
            n = run_reference_task(self,"Référentiel",lambda: self._service.structure_bulk_set_famille(codes, fam, expected=snapshots))
        except ReferenceConflictError as exc:
            show_reference_conflict(self, exc)
            self.reload(force=True)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        QMessageBox.information(
            self,
            "Action groupée",
            f"{n} structure(s) mises à jour.",
        )
        self.reload(force=True)

    def _selected_structure(self) -> StructureRow | None:
        code = self.selected_structure_code()
        if not code:
            return None
        for st in self._structures:
            if st.code == code:
                return st
        return None

    def _refill_table(self) -> None:
        selected = self.selected_structure_code()
        fam_filter = str(self._filter_famille.currentData() or "")
        q = self._search.text().strip()
        rows: list[tuple[StructureRow, int, int, int]] = []
        for st in self._structures:
            if not structure_matches_hub_filter(
                st.code,
                st.designation,
                st.famille_unite,
                famille_filter=fam_filter,
                query=q,
            ):
                continue
            node = self._org_by_code.get(st.code)
            n_sub = len(node.subdivisions) if node else 0
            n_ag = node.agent_count if node else 0
            rows.append((st, n_sub, self._fonction_count(st.code), n_ag))

        self._table.setSortingEnabled(False)
        self._filling = True
        self._table.blockSignals(True)
        self._table.setUpdatesEnabled(False)
        try:
            self._fill_structure_rows(rows)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
            self._filling = False
        self._table.setSortingEnabled(True)
        if rows:
            selected_row = next((i for i in range(self._table.rowCount()) if self._table.item(i,1).text()==selected),0)
            self._table.selectRow(selected_row)
            self._update_detail()
        else:
            self._update_detail()
        if self._view_data.is_ready():
            self._status.setText(f"{len(rows)} / {len(self._structures)} structures · {len(self._checked_codes)} cochées")
            self._status.setToolTip('Le nombre de structures cochées inclut celles masquées par les filtres.')
        self._sync_actions()

    def _fill_structure_rows(self, rows: list[tuple[StructureRow, int, int, int]]) -> None:
        self._table.setRowCount(len(rows))
        for ri, (st, n_sub, n_fn, n_ag) in enumerate(rows):
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(Qt.CheckState.Checked if st.code in self._checked_codes else Qt.CheckState.Unchecked)
            self._table.setItem(ri, 0, chk)
            code_it = QTableWidgetItem(st.code)
            code_it.setData(Qt.ItemDataRole.UserRole, st.code)
            self._table.setItem(ri, 1, code_it)
            self._table.setItem(ri, 2, QTableWidgetItem(st.designation or ""))
            kind = resolved_structure_famille(st.code, st.designation, st.famille_unite)
            self._table.setItem(ri, 3, QTableWidgetItem(structure_famille_label(kind)))
            for col, val in ((4, n_sub), (5, n_fn), (6, n_ag)):
                it = _NumberItem(str(val))
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(ri, col, it)

    def _fonction_count(self, code: str) -> int:
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(code):
            return 0
        return int(self._fn_counts.get((code or "").strip().casefold(), 0))

    def quality_summary_if_loaded(self):
        if not self._view_data.is_ready():
            return None
        return self._org.quality_summary(list(self._org_by_code.values()))

    def _update_detail(self) -> None:
        self._sync_actions()
        st = self._selected_structure()
        if st is None:
            self._detail_title.setText("Sélectionnez une structure.")
            self._fill_detail_list(self._subdiv_list, [], "—")
            self._fill_detail_list(self._fonct_list, [], "—")
            return
        node = self._org_by_code.get(st.code)
        fam = structure_famille_label(
            resolved_structure_famille(st.code, st.designation, st.famille_unite)
        )
        self._detail_title.setText(
            f"<b>{st.code}</b> — {st.designation or st.code}<br/>"
            f"Type : {fam}"
        )
        self._detail_title.setTextFormat(Qt.TextFormat.RichText)
        if node and node.subdivisions:
            lines = []
            for sub in node.subdivisions:
                tag = "" if sub.from_referentiel else " [donnée seule]"
                eff = f" ({sub.agent_count})" if sub.agent_count else ""
                lines.append(f"{sub.libelle}{eff}{tag}")
            self._fill_detail_list(self._subdiv_list, lines, "")
        else:
            self._fill_detail_list(
                self._subdiv_list,
                [],
                "Aucune subdivision — utilisez « Subdivisions d'arme… » ou le référentiel RSA.",
            )
        if not self._view_data.is_ready():
            self._fill_detail_list(self._fonct_list, [], "Lecture des rattachements impossible. Actualisez après vérification de la connexion.")
            return
        fns=self._linked_labels.get(st.code.casefold(),[])
        self._fill_detail_list(
            self._fonct_list,
            list(fns),
            "Aucune — ouvrez « Fonctions… ».",
        )

    def _edit_structure_identity(self) -> None:
        if not self._require_fresh():
            return
        st = self._selected_structure()
        if st is None:
            QMessageBox.information(self, "Structure", "Sélectionnez une structure.")
            return
        locked = evaluate_editing_state(self._service.settings).locked
        if locked:
            QMessageBox.warning(
                self,
                "Édition verrouillée",
                evaluate_editing_state(self._service.settings).detail or "",
            )
            return
        dlg = StructureEditDialog(self, st)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            code, designation, ordre, famille = dlg.values()
            if not designation:
                QMessageBox.warning(self, "Structure", "La désignation est obligatoire.")
                return
            if not confirm_yes_no(
                self,
                "Enregistrer",
                f"Mettre à jour la structure « {code} » (type : {structure_famille_label(famille)}) ?",
                default_no=False,
            ):
                return
            try:
                if not self._require_fresh():return
                run_reference_task(self,"Référentiel",lambda: self._service.structure_update(
                    code, designation, ordre, famille_unite=famille, expected=st
                ))
            except ReferenceConflictError as exc:
                show_reference_conflict(self, exc, draft=True)
                continue
            except Exception as exc:
                QMessageBox.critical(self, "Structure", str(exc))
                return
            self.reload(force=True)
            self.structures_changed.emit()
            return


    def _on_add_structure(self) -> None:
        if not self._require_fresh():
            return
        locked = evaluate_editing_state(self._service.settings).locked
        if locked:
            QMessageBox.warning(
                self,
                "Édition verrouillée",
                evaluate_editing_state(self._service.settings).detail or "",
            )
            return
        dlg = StructureEditDialog(self, None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, designation, ordre, famille = dlg.values()
        if not code or not designation:
            QMessageBox.warning(self, "Structure", "Code et désignation sont obligatoires.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Créer la structure « {code} » dans ref_structure (MySQL) ?",
            default_no=True,
        ):
            return
        try:
            if not self._require_fresh():return
            run_reference_task(self,"Référentiel",lambda: self._service.structure_insert(
                code, designation, ordre, famille_unite=famille
            ))
        except Exception as exc:
            QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        self.reload(force=True)
        self.structures_changed.emit()

    def _on_delete_structure(self) -> None:
        if not self._require_fresh():
            return
        st = self._selected_structure()
        if st is None:
            QMessageBox.information(self, "Structure", "Sélectionnez une structure.")
            return
        locked = evaluate_editing_state(self._service.settings).locked
        if locked:
            QMessageBox.warning(
                self,
                "Édition verrouillée",
                evaluate_editing_state(self._service.settings).detail or "",
            )
            return
        code = st.code
        try:
            from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository

            u = run_reference_task(self,"Référentiel",lambda: MySQLOrganigramRepository(self._service.settings).count_structure_usage(code))
            usage_msg = f"\n\n{u.total} agent(s) utilisent cette structure." if u.total else ""
        except Exception:
            usage_msg = ""
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer définitivement la structure « {code} » ?{usage_msg}\n"
            "La section sera retirée de la mise en page liste des présents.",
            default_no=True,
        ):
            return
        try:
            if not self._require_fresh():return
            run_reference_task(self,"Référentiel",lambda: self._service.structure_delete(code, expected=st))
        except ReferenceConflictError as exc:
            show_reference_conflict(self, exc)
            self.reload(force=True)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Suppression", str(exc))
            return
        self.reload(force=True)
        self.structures_changed.emit()

    def _open_subdivisions(self) -> None:
        if not self._require_fresh():
            return
        st = self._selected_structure()
        if st is None:
            QMessageBox.information(self, "Structure", "Sélectionnez une structure.")
            return
        st_edit = evaluate_editing_state(self._service.settings)
        dlg = StructureSubdivisionsDialog(
            self,
            self._service,
            st,
            on_structure_changed=lambda: self.reload(force=True),
            allow_edit=not st_edit.locked,
        )
        dlg.exec()
        self.reload(force=True)

    def _open_fonctions(self) -> None:
        st = self._selected_structure()
        if st is None:
            QMessageBox.information(self, "Structure", "Sélectionnez une structure.")
            return
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(st.code) or structure_fonctions_non_liees(st.designation):
            QMessageBox.information(
                self,
                "Fonctions",
                f"« {st.designation or st.code} » (contrôle spécial ou détachés) "
                "n'a pas de fonctions rattachées : elles ne sont pas connues à l'avance "
                "et se saisissent librement sur la fiche.",
            )
            return
        self.request_open_fonctions.emit(st.code)
