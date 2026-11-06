"""Référentiel des fonctions — libellés, rattachements structures, ordre de liste."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from sme_gend.services.search_text import normalize_search_text

from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.fonction_structures_dialog import FonctionStructuresDialog
from sme_gend.ui.page_header import PageHeader
from sme_gend.ui.theme import theme_palette
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.responsive_panels import WrapLayout,scroll_panel,tab_content
from sme_gend.ui.reference_plan_preview import confirm_reference_plan

_FILTER_ALL = "all"
_FILTER_ORPHAN = "orphan"


class _FonctionTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._headers: list[str] = []
        self._rows: list[list[str]] = []
        self._orphan: list[bool] = []

    def set_data(self, headers: list[str], rows: list[list[str]], orphan: list[bool]) -> None:
        self.beginResetModel()
        self._headers = headers
        self._rows = rows
        self._orphan = orphan
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if not (0 <= r < len(self._rows) and 0 <= c < len(self._rows[r])):
            return None
        orphan = bool(r < len(self._orphan) and self._orphan[r])
        if role == Qt.ItemDataRole.DisplayRole:
            return self._rows[r][c]
        if role == Qt.ItemDataRole.ForegroundRole and orphan:
            return QColor(theme_palette()["warning"])
        if role == Qt.ItemDataRole.ToolTipRole:
            if orphan:
                return self._rows[r][c] + "\n" + (
                    "Pas encore rattachée à la structure : "
                    "Modifier / rattacher… pour l'harmoniser."
                )
            if c == 1:
                return self._rows[r][c]+"\nDouble-clic : modifier les structures."
            return self._rows[r][c]
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        return str(section + 1)


class FonctionEditDialog(QDialog):
    def __init__(self, parent: QWidget, row: FonctionRefRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle("Nouvelle fonction" if row is None else "Modifier la fonction")
        self.setMinimumWidth(440)
        self._libelle = QLineEdit()
        self._libelle.setPlaceholderText("Ex. COMGEND")
        if row is not None:
            self._libelle.setText(row.libelle)
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Libellé *", self._libelle)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(bb)

    def libelle(self) -> str:
        return self._libelle.text().strip()


class FonctionsView(QWidget):
    """Catalogue des fonctions, rattachements par structure et ordre de la liste générale."""

    def __init__(self, service: ReferentielService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[FonctionRefRow] = []
        self._links: dict[int, list[str]] = {}
        self._usage: dict[int, int] = {}
        self._observed_by_structure: dict[str, dict[str, tuple[str, int]]] = {}
        self._filtered: list[FonctionRefRow] = []
        self._view_data = ViewDataState(stale=True)
        self._pending_focus_structure: str | None = None
        self._generation=0
        self._loading=False

        from sme_gend.ui.expandable_help import ExpandableHelp
        self._help = ExpandableHelp(
            'Postes vacants du mois et catalogue des fonctions. Créez, modifiez et rattachez '
            'les fonctions aux structures depuis « Nouvelle fonction… ».', self)

        self._lock_banner = QLabel()
        self._lock_banner.setObjectName("usersAuthWarnBanner")
        self._lock_banner.setWordWrap(True)
        self._lock_banner.hide()

        self._status = QLabel()
        self._status.setObjectName("settingsPathsHint")
        self._status.setWordWrap(True)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._filter_scope = QComboBox()
        self._filter_scope.setMinimumWidth(140)
        self._filter_scope.addItem("Toutes les fonctions", _FILTER_ALL)
        self._filter_scope.addItem("Sans aucun rattachement", _FILTER_ORPHAN)
        self._filter_scope.setToolTip("Ce critère recherche les fonctions du catalogue sans lien avec aucune structure, même lorsqu'une structure est filtrée.")
        self._filter_scope.currentIndexChanged.connect(self._apply_filter)

        self._filter_structure = QComboBox()
        self._filter_structure.setMinimumWidth(200)
        self._filter_structure.addItem("Toutes les structures", None)
        self._filter_structure.currentIndexChanged.connect(self._apply_filter)

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouvelle fonction…")
        self._btn_add.setObjectName("primaryButton")
        self._btn_add.setToolTip(
            "Créer un libellé dans le référentiel et le rattacher aux structures."
        )
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_smart = QPushButton("Modifier / rattacher…")
        self._btn_smart.setToolTip(
            "Renommer la fonction et choisir manuellement ses structures autorisées. Décocher retire un lien."
        )
        self._btn_smart.clicked.connect(self._on_smart_link)
        self._btn_structures = QPushButton("Structures…")
        self._btn_structures.setToolTip("Structures où cette fonction peut être saisie.")
        self._btn_structures.clicked.connect(self._on_structures)
        self._btn_order = QPushButton("Ordre…")
        self._btn_order.setToolTip("Fonctions affichées en tête de la liste Effectifs.")
        self._btn_order.clicked.connect(self._on_priority_order)
        self._btn_backfill = QPushButton("Reprendre…")
        self._btn_backfill.setToolTip("Créer les rattachements d’après les fiches du mois.")
        self._btn_backfill.clicked.connect(self._on_backfill)
        self._btn_harmon_export = QPushButton("Export harmonisation…")
        self._btn_harmon_export.setToolTip(
            "Excel : libellés attendus (CHEF DE … + subdivision), écarts et titulaires."
        )
        self._btn_harmon_export.clicked.connect(self._on_harmon_export)
        self._btn_harmon_apply = QPushButton("Créer les manquants…")
        self._btn_harmon_apply.setToolTip(
            "Ajoute au référentiel les fonctions attendues et rattache les structures."
        )
        self._btn_harmon_apply.clicked.connect(self._on_harmon_apply)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        bar = WrapLayout()
        bar.setSpacing(8)
        bar.addWidget(self._search)
        bar.addWidget(self._filter_scope)
        bar.addWidget(self._filter_structure)
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._help.button)

        actions = WrapLayout()
        actions.setSpacing(8)
        actions.addWidget(self._btn_add)
        actions.addWidget(self._btn_edit)
        actions.addWidget(self._btn_smart)
        actions.addWidget(self._btn_structures)
        actions.addWidget(self._btn_order)
        actions.addWidget(self._btn_backfill)
        actions.addWidget(self._btn_harmon_export)
        actions.addWidget(self._btn_harmon_apply)
        actions.addWidget(self._btn_del)

        self._model = _FonctionTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_table_double_clicked)
        self._table.selectionModel().selectionChanged.connect(lambda *_: self._sync_actions())
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(True)

        self._empty = QLabel("Aucune fonction. Ajoutez-en une ou cliquez sur Reprendre…")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("settingsPathsHint")
        self._empty.setWordWrap(True)
        self._empty.hide()

        referentiel = QWidget()
        lay = QVBoxLayout(referentiel)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(5)
        lay.addWidget(self._help)
        lay.addWidget(self._lock_banner)
        lay.addWidget(self._status)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(self._empty, stretch=1)
        lay.addLayout(actions)

        self._vacants = _PostesVacantsTab(service, owner=self)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("settingsTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(scroll_panel(self._vacants), "Postes de tête du mois")
        self._tabs.addTab(scroll_panel(referentiel), "Référentiel")
        self._tabs.currentChanged.connect(self._on_main_tab)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)
        root.addWidget(self._tabs, stretch=1)
        self._sync_actions()

    def mark_dirty(self) -> None:
        self._generation+=1
        self._view_data.mark_stale()
        self._status.setText("Données non actualisées. Rechargez avant une action.")
        self._sync_actions()
        self._vacants.mark_stale()
        if self.isVisible() and tab_content(self._tabs) is self._vacants:
            self._vacants.ensure_loaded()

    def _on_main_tab(self, _index: int) -> None:
        if tab_content(self._tabs) is self._vacants:
            self._vacants.ensure_loaded()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def focus_structure(self, structure_code: str) -> None:
        """Depuis le hub Structures : filtre référentiel + postes vacants sur la structure."""
        code = (structure_code or "").strip()
        if not code:
            return
        self._pending_focus_structure = code
        self._tabs.setCurrentIndex(1)
        if not self._view_data.needs_load():
            self._apply_pending_structure_focus()
        if tab_content(self._tabs) is self._vacants:
            self._vacants.ensure_loaded()

    def _apply_pending_structure_focus(self) -> None:
        code = self._pending_focus_structure
        if not code:
            return
        self._search.clear()
        self._set_structure_filter(code)
        self._apply_filter()
        self._vacants.prepare_structure_filter(code)
        self._pending_focus_structure = None

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()
        else:
            self._sync_actions()
            if self._pending_focus_structure:
                self._apply_pending_structure_focus()
        if tab_content(self._tabs) is self._vacants:
            self._vacants.ensure_loaded()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def apply_runtime_after_config_change(self) -> None:
        self._view_data.mark_stale()
        self._vacants.mark_stale()
        if self.isVisible():
            self.reload()
            if tab_content(self._tabs) is self._vacants:
                self._vacants.ensure_loaded()
        else:
            self._sync_actions()

    def _require_unlocked(self) -> bool:
        if not self._view_data.is_ready() or self._loading:
            QMessageBox.warning(self,"Fonctions","Données non actualisées. Rechargez avant cette action.")
            return False
        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return False
        return True

    def _sync_actions(self) -> None:
        st = evaluate_editing_state(self._service.settings)
        locked = bool(st.locked) or not self._view_data.is_ready() or self._loading
        self._lock_banner.setVisible(bool(st.locked))
        if st.locked:
            self._lock_banner.setText(st.detail)
        has = self._selected_row() is not None
        self._btn_add.setEnabled(not locked)
        self._btn_edit.setEnabled(not locked and has)
        self._btn_smart.setEnabled(not locked and has)
        self._btn_structures.setEnabled(not locked and has)
        self._btn_del.setEnabled(not locked and has)
        self._btn_backfill.setEnabled(not locked)
        from sme_gend.services.auth_guard import session_can_export_documents
        self._btn_harmon_export.setEnabled(self._view_data.is_ready() and not self._loading and session_can_export_documents())
        self._btn_order.setEnabled(not locked)
        self._btn_harmon_apply.setEnabled(not locked)

    def reload(self) -> None:
        if self._loading:return
        prev_id=self._selected_row().id if self._selected_row() else None
        self._generation+=1
        generation,settings=self._generation,self._service.settings
        self._view_data.mark_stale();self._loading=True;self._sync_actions()
        self._status.setText("Chargement du catalogue et des usages du mois…")
        service=self._service
        def work():
            from sme_gend.repositories.structure_repository import StructureRepository
            return (service.fonctions_all(),service.fonction_links_summary(),service.fonction_usage_counts(),
                    service.fonction_observed_usage(),StructureRepository(settings).fetch_all())
        try:
            result=run_with_progress(self,"Fonctions","Chargement du référentiel…",work,silent=True)
        finally:self._loading=False
        if result is None or generation!=self._generation or settings!=self._service.settings:
            self._view_data.mark_stale();self._sync_actions()
            self._status.setText("Chargement impossible ou contexte modifié : données non actualisées, actions désactivées.")
            return
        self._rows,self._links,self._usage,observed,structures=result
        self._observed_by_structure={}
        for code,libelle,count in observed:
            bucket=self._observed_by_structure.setdefault(code.casefold(),{})
            key=libelle.casefold();previous=bucket.get(key)
            bucket[key]=(libelle,int(count)+(previous[1] if previous else 0))
        self._refill_structure_filter_combo(self._pending_focus_structure or self._filter_structure.currentData(),structures)
        self._view_data.finish_load();self._apply_filter()
        if prev_id is not None:
            for i,row in enumerate(self._filtered):
                if row.id==prev_id:self._table.selectRow(i);break
        elif self._filtered:self._table.selectRow(0)
        if self._pending_focus_structure:self._apply_pending_structure_focus()
        self._sync_actions()

    def _refill_structure_filter_combo(self, previous: str | None = None, structures=None) -> None:
        from sme_gend.repositories.structure_repository import StructureRepository
        from sme_gend.services.structure_label_format import structure_combo_label

        prev = previous
        self._filter_structure.blockSignals(True)
        self._filter_structure.clear()
        self._filter_structure.addItem("Toutes les structures", None)
        for st in (structures if structures is not None else StructureRepository(self._service.settings).fetch_all()):
            code = (st.code or "").strip()
            if not code:
                continue
            self._filter_structure.addItem(
                structure_combo_label(code, st.designation),
                code,
            )
        if prev is not None:
            for i in range(self._filter_structure.count()):
                if self._filter_structure.itemData(i) == prev:
                    self._filter_structure.setCurrentIndex(i)
                    break
        self._filter_structure.blockSignals(False)

    def _set_structure_filter(self, structure_code: str) -> None:
        code = (structure_code or "").strip()
        if not code:
            return
        for i in range(self._filter_scope.count()):
            if self._filter_scope.itemData(i) == _FILTER_ALL:
                self._filter_scope.setCurrentIndex(i)
                break
        for i in range(self._filter_structure.count()):
            if self._filter_structure.itemData(i) == code:
                self._filter_structure.setCurrentIndex(i)
                return
        from sme_gend.repositories.structure_repository import StructureRepository
        from sme_gend.services.structure_label_format import structure_combo_label

        st = StructureRepository(self._service.settings).fetch_by_code(code)
        label = structure_combo_label(code, st.designation if st else None)
        self._filter_structure.addItem(label, code)
        self._filter_structure.setCurrentIndex(self._filter_structure.count() - 1)

    def _apply_filter(self) -> None:
        q = normalize_search_text(self._search.text().strip().casefold())
        only_orphan = self._filter_scope.currentData() == _FILTER_ORPHAN
        struct_code = self._filter_structure.currentData()
        struct_key = str(struct_code).casefold() if struct_code else ""
        observed = self._observed_by_structure.get(struct_key, {}) if struct_key else {}
        by_libelle = {row.libelle.casefold(): row for row in self._rows}
        shown: list[FonctionRefRow] = []
        data: list[list[str]] = []
        orphan_flags: list[bool] = []
        orphan_n = 0
        seen_libs: set[str] = set()
        next_synthetic = -1

        def linked_to_structure(codes: list[str]) -> bool:
            return any(c.casefold() == struct_key for c in codes)

        candidates: list[tuple[FonctionRefRow, list[str], int, str]] = []
        if struct_key:
            for row in self._rows:
                codes = self._links.get(row.id) or []
                if not codes:
                    orphan_n += 1
                hit = observed.get(row.libelle.casefold())
                linked = linked_to_structure(codes)
                if not hit and not linked:
                    continue
                if only_orphan and codes:
                    continue
                count = hit[1] if hit else 0
                if linked:
                    state = "Rattachée"
                else:
                    state = "Sur les fiches"
                candidates.append((row, codes, count, state))
                seen_libs.add(row.libelle.casefold())
            for lib_key, (libelle, count) in observed.items():
                if lib_key in seen_libs or lib_key in by_libelle:
                    continue
                if only_orphan:
                    continue
                row = FonctionRefRow(id=next_synthetic, libelle=libelle)
                next_synthetic -= 1
                candidates.append((row, [], count, "Hors catalogue"))
            candidates.sort(key=lambda item: item[0].libelle.casefold())
        else:
            for row in self._rows:
                codes = self._links.get(row.id) or []
                if not codes:
                    orphan_n += 1
                if only_orphan and codes:
                    continue
                state = "Sans structure" if not codes else "OK"
                candidates.append(
                    (row, codes, int(self._usage.get(row.id, 0)), state)
                )

        for row, codes, usage, state in candidates:
            blob = normalize_search_text(f"{row.libelle} {' '.join(codes)} {state}".casefold())
            if q and q not in normalize_search_text(blob):
                continue
            shown.append(row)
            needs_work = state in {"Sans structure", "Sur les fiches", "Hors catalogue"}
            orphan_flags.append(needs_work)
            structure_label = ", ".join(codes) if codes else (str(struct_code) if struct_key else "—")
            if state == "Sur les fiches" and struct_code:
                structure_label = str(struct_code)
            data.append(
                [
                    row.libelle,
                    structure_label,
                    str(usage),
                    state,
                ]
            )
        self._filtered = shown
        self._model.set_data(["Fonction", "Structures", "Fiches", "État"], data, orphan_flags)
        hdr = self._table.horizontalHeader()
        if self._model.columnCount() >= 4:
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(0,300)
            self._table.setColumnWidth(1,260)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        total = len(self._rows)
        n = len(shown)
        empty = n == 0
        self._table.setVisible(not empty)
        if empty:
            if not self._view_data.is_ready():
                self._empty.setText("Données non actualisées. Rechargez le catalogue.")
            elif q or only_orphan or struct_code:
                self._empty.setText("Aucun résultat pour les filtres sélectionnés.")
            else:
                self._empty.setText("Catalogue vide. Ajoutez une fonction ou préparez une reprise depuis les fiches.")
        self._empty.setVisible(empty)
        if struct_code:
            ratt = sum(1 for line in data if line[3] == "Rattachée")
            fiches = sum(1 for line in data if line[3] == "Sur les fiches")
            hors = sum(1 for line in data if line[3] == "Hors catalogue")
            parts = [f"{n} fonction(s) pour {struct_code}"]
            if ratt:
                parts.append(f"{ratt} rattachée(s)")
            if fiches:
                parts.append(f"{fiches} sur les fiches")
            if hors:
                parts.append(f"{hors} hors catalogue")
        else:
            parts = [f"{total} fonction(s)"]
            if orphan_n:
                parts.append(f"{orphan_n} sans structure")
            if q or only_orphan:
                parts.append(f"{n} affichée(s)")
        if self._view_data.is_ready():self._status.setText(" · ".join(parts) + ".")
        self._sync_actions()

    def _selected_row(self) -> FonctionRefRow | None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return None
        row = idx.row()
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid() and index.column() == 1:
            self._on_structures()
            return
        self._on_edit()

    def _on_add(self) -> None:
        if not self._require_unlocked():
            return
        from sme_gend.ui.fonction_smart_dialog import open_fonction_smart_dialog

        saved = open_fonction_smart_dialog(self, self._service, libelle="", structure_code=self._filter_structure.currentData())
        if not saved:
            return
        self.reload()
        for i, row in enumerate(self._filtered):
            if row.libelle.casefold() == saved.casefold():
                self._table.selectRow(i)
                break

    def _on_edit(self) -> None:
        if not self._require_unlocked():
            return
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Fonction", "Sélectionnez une fonction.")
            return
        if row.id <= 0:
            self._harmonize_row(row)
            return
        dlg = FonctionEditDialog(self, row)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            lib = dlg.libelle()
            if not lib:
                QMessageBox.warning(self, "Fonction", "Indiquez un libellé.")
                return
            try:
                if lib != row.libelle:
                    from sme_gend.ui.fonction_rename_confirmation import confirm_global_rename
                    impact=confirm_global_rename(self,self._service,row,lib)
                    if impact is None:return
                    run_reference_task(self,"Référentiel",lambda: self._service.fonction_rename_confirmed(row,lib,impact))
                else:
                    run_reference_task(self,"Référentiel",lambda: self._service.fonction_update(row.id,row.libelle,lib,expected=row))
            except ReferenceConflictError as exc:
                show_reference_conflict(self, exc, draft=True)
                continue
            except ValueError as e:
                QMessageBox.warning(self, "Fonction", str(e))
                return
            except Exception as e:
                QMessageBox.critical(self, "Enregistrement", str(e))
                return
            self.reload()
            return


    def _harmonize_row(self, row: FonctionRefRow) -> None:
        from sme_gend.ui.fonction_smart_dialog import open_fonction_smart_dialog

        saved = open_fonction_smart_dialog(
            self,
            self._service,
            libelle=row.libelle,
            structure_code=self._filter_structure.currentData(),
            fonction_id=row.id if row.id > 0 else None,
        )
        if saved:
            self.reload()

    def _on_smart_link(self) -> None:
        if not self._require_unlocked():
            return
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Fonction", "Sélectionnez une fonction.")
            return
        self._harmonize_row(row)

    def _on_structures(self) -> None:
        if not self._require_unlocked():
            return
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Fonction", "Sélectionnez une fonction.")
            return
        if row.id <= 0:
            self._harmonize_row(row)
            return
        dlg = FonctionStructuresDialog(self._service, row.id, row.libelle, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload()

    def _on_priority_order(self) -> None:
        if not self._require_unlocked():return
        from sme_gend.ui.fonction_priority_dialog import FonctionPriorityDialog

        dlg = FonctionPriorityDialog(self._service.settings, self._service, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Ordre", "Enregistré. Actualisez la liste Effectifs.")

    def _on_harmon_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.fonction_referentiel_harmonize import collect_harmonize_rows,export_harmonize_workbook
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path
        if not self._view_data.is_ready() or self._loading:return
        if not require_permission(self,"effectifs.export"):return
        settings=self._service.settings;generation=self._generation
        structure=self._filter_structure.currentData()
        query=normalize_search_text(self._search.text())
        orphan=self._filter_scope.currentData()==_FILTER_ORPHAN
        allowed={normalize_search_text(r.libelle) for r in self._filtered}
        def collect():
            return [r for r in collect_harmonize_rows(settings)
                if (not structure or r.structure_code.casefold()==str(structure).casefold())
                and (not query or query in normalize_search_text(' '.join((r.structure_label,r.subdivision,r.libelle_attendu,r.libelles_observes,r.titulaires,r.statut))))
                and (not orphan or normalize_search_text(r.libelle_attendu) in allowed)]
        rows=run_with_progress(self,"Export","Préparation de l'export filtré…",collect)
        if rows is None:return
        if settings!=self._service.settings or generation!=self._generation or not self._view_data.is_ready():
            QMessageBox.warning(self,"Export","Le contexte a changé. Rechargez puis préparez à nouveau l'export.")
            return
        if not rows:
            QMessageBox.information(self,"Export","Aucune ligne d'harmonisation pour ces filtres.");return
        if not confirm_reference_plan(self,"Vérifier l'export",("Structure","Subdivision","Fonction attendue","État"),
            [(r.structure_code,r.subdivision,r.libelle_attendu,r.statut) for r in rows],
            f"{len(rows)} ligne(s), selon les filtres structure, recherche et rattachement courants. Aucun référentiel ne sera modifié."):return
        path,_=QFileDialog.getSaveFileName(self,"Exporter l'harmonisation filtrée","harmonisation_fonctions.xlsx","Excel (*.xlsx)")
        if not path:return
        if not path.lower().endswith('.xlsx'):path+='.xlsx'
        if settings!=self._service.settings or generation!=self._generation or not self._view_data.is_ready():return
        if not require_permission(self,"effectifs.export"):return
        result=run_with_progress(self,"Export","Écriture du classeur…",lambda:export_harmonize_workbook(Path(path),settings,rows=rows))
        if result is not None:QMessageBox.information(self,"Export",f"{result} ligne(s) enregistrée(s) :\n{path}")

    def _on_harmon_apply(self) -> None:
        if not self._require_unlocked():
            return
        if not confirm_yes_no(
            self,
            "Harmonisation",
            "Créer les fonctions manquantes et rattacher les structures "
            "selon les libellés attendus (CHEF DE … + subdivision) ?\n\n"
            "Les libellés déjà saisis ne sont pas renommés automatiquement.",
            default_no=True,
        ):
            return

        def work():
            from sme_gend.services.fonction_referentiel_harmonize import (
                apply_missing_harmonization,
            )

            return apply_missing_harmonization(self._service.settings)

        result = run_with_progress(
            self,
            "Fonctions",
            "Harmonisation du référentiel…",
            work,
            silent=True,
        )
        if result is None:
            return
        QMessageBox.information(
            self,
            "Harmonisation",
            f"Créées : {result.created}\n"
            f"Rattachements ajoutés : {result.linked}\n"
            f"Ignorées (déjà OK) : {result.skipped}",
        )
        self.reload()

    def _on_backfill(self) -> None:
        from sme_gend.services.fonction_backfill import prepare_backfill,apply_backfill
        if not self._require_unlocked():return
        service=self._service
        plan=run_with_progress(self,"Reprise","Préparation des changements du mois…",lambda:prepare_backfill(service))
        if plan is None:return
        if not plan.items:
            QMessageBox.information(self,"Reprise","Aucun changement à proposer.");return
        created=len({r.libelle.casefold() for r in plan.items if r.create})
        if not confirm_reference_plan(self,"Vérifier la reprise",("Structure","Fonction","Catalogue","Rattachement"),
            [(r.structure,r.libelle,"Créer si absente" if r.create else "Existante","Ajouter") for r in plan.items],
            f"{created} fonction(s) distincte(s) à créer et {len(plan.items)} rattachement(s) à ajouter d'après les fiches du mois consulté. Les libellés erronés peuvent être propagés : vérifiez chaque ligne."):return
        result=run_with_progress(self,"Reprise","Application de la reprise vérifiée…",lambda:apply_backfill(service,plan))
        if result is not None:
            self.reload();self._refresh_vacants()
            QMessageBox.information(self,"Reprise",f"{result} rattachement(s) ajouté(s).")

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Fonction", "Sélectionnez une fonction.")
            return
        if row.id <= 0:
            QMessageBox.information(
                self,
                "Suppression",
                f"« {row.libelle} » n'est pas au catalogue : elle est seulement saisie sur les fiches.\n\n"
                "Utilisez « Modifier / rattacher… » pour la reprendre et l'harmoniser.",
            )
            return
        self._delete_fonction_row(row)

    def delete_or_dismiss_poste(self, poste) -> None:
        """Retire uniquement le siège attendu, jamais la fonction partagée."""
        lib = (getattr(poste, "poste", None) or "").strip()
        if not lib:
            QMessageBox.information(self, "Fonction", "Aucun libellé de fonction sur ce poste.")
            return
        self._dismiss_expected_poste(poste,lib)

    def _find_fonction(self, libelle: str) -> FonctionRefRow | None:
        rows = self._service.fonctions_all() if self._view_data.needs_load() else self._rows
        key = libelle.casefold()
        for candidate in rows:
            if candidate.libelle.casefold() == key:
                return candidate
        return None

    def _dismiss_expected_poste(self, poste, libelle: str) -> None:
        if not self._require_unlocked():
            return
        unite = (getattr(poste, "subdivision", None) or "").strip()
        ou = f" — {unite}" if unite else ""
        structure = (getattr(poste, "structure_label", None) or getattr(poste, "structure_code", "") or "").strip()
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Retirer « {libelle} » des postes attendus ({structure}{ou}), toutes périodes confondues ?\n\n"
            "La fonction reste au catalogue et conserve ses rattachements aux autres structures. Aucun dossier n'est modifié.",
            default_no=True,
        ):
            return
        from sme_gend.services.postes_tete_exclusions import exclude_expected_poste

        try:
            exclude_expected_poste(
                self._service.settings,
                getattr(poste, "structure_code", "") or "",
                unite,
                libelle,
            )
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self._refresh_vacants()

    def _refresh_vacants(self) -> None:
        self._vacants.mark_stale()
        if tab_content(self._tabs) is self._vacants:
            self._vacants.reload()

    def _delete_fonction_row(self, row: FonctionRefRow) -> bool:
        if not self._require_unlocked():
            return False
        try:
            usage = run_reference_task(self,"Référentiel",lambda: self._service.fonction_count_usage(row.libelle, row.id))
        except Exception as e:
            QMessageBox.critical(self, "Fonction", str(e))
            return False
        if usage:
            QMessageBox.warning(
                self,
                "Suppression impossible",
                f"« {row.libelle} » est utilisée par {usage} fiche(s).",
            )
            return False
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer « {row.libelle} » du référentiel ?",
            default_no=True,
        ):
            return False
        try:
            run_reference_task(self,"Référentiel",lambda: self._service.fonction_delete(row.id, expected=row))
        except ReferenceConflictError as exc:
            show_reference_conflict(self, exc)
            self.reload()
            return False
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return False
        self.reload()
        self._vacants.mark_stale()
        return True


class _VacantTableModel(QAbstractTableModel):
    _HEADERS = ("Structure", "Unité", "Type", "Poste de tête", "Titulaire", "Effectif", "État")

    def __init__(self) -> None:
        super().__init__()
        self._rows: list = []

    def set_rows(self, rows: list) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            unite = row.subdivision or "Commandement"
            values = (
                row.structure_label,
                unite,
                row.type_label,
                row.intitule,
                row.titulaire_label,
                str(row.effectif),
                row.etat,
            )
            return values[col] if 0 <= col < len(values) else None
        if role == Qt.ItemDataRole.ForegroundRole and col == 4 and row.vacant:
            return QColor(theme_palette()["warning"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{row.structure_label}\n{row.subdivision or 'Commandement'}\n{row.intitule}\n{row.titulaire_label}\n{row.etat}"
        if role == Qt.ItemDataRole.ForegroundRole and row.multiple_titulaires:
            return QColor(theme_palette()["warning"])
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 5:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        if 0 <= section < len(self._HEADERS):
            return self._HEADERS[section]
        return None


class _PostesVacantsTab(QWidget):
    """Postes de tête du mois SME, vacants ou pourvus."""

    def __init__(
        self,
        service: ReferentielService,
        parent: QWidget | None = None,
        *,
        owner: FonctionsView | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._owner = owner
        self._stale = True
        self._generation=0
        self._all: list = []
        self._loading = False
        self._pending_structure_code: str | None = None

        self._mois_label = ""
        self._status = QLabel("Postes de tête du mois SME.")
        self._status.setObjectName("settingsPathsHint")
        self._status.setWordWrap(True)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Structure, unité, poste, titulaire…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._filter = QComboBox()
        self._filter.setMinimumWidth(180)
        self._filter.addItem("Toutes les structures", None)
        self._filter.currentIndexChanged.connect(self._apply_filter)

        self._filter_type = QComboBox()
        self._filter_type.setMinimumWidth(160)
        self._filter_type.addItem("Tous les types", None)
        self._filter_type.currentIndexChanged.connect(self._apply_filter)

        self._vacants_only = QCheckBox("Vacants seulement")
        self._vacants_only.setChecked(True)
        self._vacants_only.toggled.connect(self._apply_filter)
        self._conflicts_only=QCheckBox("Plusieurs titulaires")
        self._conflicts_only.toggled.connect(self._on_conflicts_toggled)
        self._vacants_only.toggled.connect(lambda checked:self._conflicts_only.setChecked(False) if checked else None)

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.clicked.connect(self.reload)
        self._btn_new = QPushButton("Nouvelle fonction…")
        self._btn_new.setObjectName("primaryButton")
        self._btn_new.setToolTip(
            "Ajouter une fonction au référentiel (structure du filtre si sélectionnée)."
        )
        self._btn_new.clicked.connect(self._on_new_fonction)
        self._btn_assign = QPushButton("Attribuer un agent…")
        self._btn_assign.setObjectName("primaryButton")
        self._btn_assign.setToolTip(
            "Choisir un agent de la structure et lui affecter le poste de tête sélectionné."
        )
        self._btn_assign.clicked.connect(self._on_assign_agent)
        self._btn_assign.setEnabled(False)
        self._btn_del = QPushButton("Retirer ce poste attendu…")
        self._btn_del.setToolTip(
            "Masque uniquement ce siège attendu, toutes périodes confondues. Ne supprime pas la fonction du catalogue."
        )
        self._btn_del.clicked.connect(self._on_delete_fonction)

        bar = WrapLayout()
        bar.setSpacing(8)
        bar.addWidget(self._search)
        bar.addWidget(self._filter)
        bar.addWidget(self._filter_type)
        bar.addWidget(self._vacants_only)
        bar.addWidget(self._conflicts_only)
        bar.addWidget(self._btn_reload)

        self._model = _VacantTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        self._table.setTextElideMode(Qt.TextElideMode.ElideRight)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        for column,width in enumerate((220,240,160,280,360,80,240)):
            hdr.setSectionResizeMode(column,QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column,width)
        self._table.selectionModel().selectionChanged.connect(self._sync_poste_actions)
        self._table.doubleClicked.connect(lambda index:QTimer.singleShot(0,self._on_titulaires))

        self._empty = QLabel("Aucun poste de tête.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("settingsPathsHint")
        self._empty.setWordWrap(True)
        self._empty.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(5)
        lay.addWidget(self._status)
        from sme_gend.ui.expandable_help import ExpandableHelp
        self._help = ExpandableHelp('Périmètre : postes de tête calculés pour les structures et subdivisions ayant du personnel dans le mois consulté. Les unités sans effectif et les fonctions ordinaires du catalogue ne sont pas recensées ici.', self)
        bar.addWidget(self._help.button)
        lay.addWidget(self._help)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(self._empty, stretch=1)
        poste_actions = WrapLayout()
        poste_actions.addWidget(self._btn_new)
        poste_actions.addWidget(self._btn_assign)
        self._btn_titulaires=QPushButton("Titulaires / dossiers…")
        self._btn_titulaires.clicked.connect(self._on_titulaires)
        poste_actions.addWidget(self._btn_titulaires)
        poste_actions.addWidget(self._btn_del)
        lay.addLayout(poste_actions)
        self._sync_poste_actions()

    def _sync_poste_actions(self) -> None:
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        locked = bool(evaluate_editing_state(self._service.settings).locked) or self._stale or self._loading
        self._btn_new.setEnabled(not locked)
        has_poste = self._selected_poste() is not None
        poste=self._selected_poste()
        self._btn_assign.setEnabled(not locked and has_poste and not poste.multiple_titulaires)
        self._btn_del.setEnabled(not locked and has_poste)
        self._btn_titulaires.setEnabled(not self._stale and not self._loading and poste is not None and not poste.vacant)

    def _on_conflicts_toggled(self, checked):
        if checked:self._vacants_only.setChecked(False)
        self._apply_filter()

    def _on_titulaires(self):
        from sme_gend.services.auth_guard import require_permission
        if self._stale or self._loading:return
        if not require_permission(self,"effectifs.liste"):return
        poste=self._selected_poste()
        if poste is None or poste.vacant:return
        settings=self._service.settings
        dialog=QDialog(self);dialog.setWindowTitle("Vérifier les titulaires du poste");dialog.resize(760,460)
        layout=QVBoxLayout(dialog)
        hint=QLabel(f"{poste.structure_label} — {poste.intitule}\n{poste.etat}. Comparez les identifiants et les dossiers avant de modifier une attribution.")
        hint.setWordWrap(True);layout.addWidget(hint)
        roster=QListWidget()
        for holder in poste.titulaires_details:
            item=QListWidgetItem(holder.label);item.setData(Qt.ItemDataRole.UserRole,holder.id);roster.addItem(item)
        if not poste.titulaires_details:
            for name in poste.titulaires:roster.addItem(name)
        layout.addWidget(roster)
        open_button=QPushButton("Ouvrir le dossier sélectionné (consultation)");layout.addWidget(open_button)
        def open_dossier():
            if settings!=self._service.settings or self._stale:
                QMessageBox.warning(dialog,"Contexte modifié","Fermez et rechargez les postes.");return
            item=roster.currentItem();identifier=item.data(Qt.ItemDataRole.UserRole) if item else None
            if not identifier:
                QMessageBox.information(dialog,"Dossier","Sélectionnez un titulaire avec un identifiant de fiche. Rechargez les postes si cet identifiant manque.");return
            if not require_permission(dialog,"effectifs.liste"):return
            from sme_gend.services.personnel_service import PersonnelService
            from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog
            def read_dossier():
                service=PersonnelService(settings)
                return service,service.get_by_id(str(identifier))
            result=run_with_progress(dialog,"Dossier","Lecture du dossier…",read_dossier)
            if result is None:return
            service,row=result
            if row is None:
                QMessageBox.information(dialog,"Dossier","Ce dossier n'est plus disponible pour la période consultée. Rechargez les postes.")
                return
            if settings!=self._service.settings or self._stale:return
            open_personnel_dossier_dialog(dialog,row,self._service,service,allow_edit=False,refresh_from_db=False)
        open_button.clicked.connect(open_dossier)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        dialog.exec()

    def _on_delete_fonction(self) -> None:
        if self._stale or self._loading:
            QMessageBox.warning(self,"Postes","Données non actualisées. Rechargez les postes.")
            return
        poste = self._selected_poste()
        if poste is None:
            QMessageBox.information(self, "Poste de tête", "Sélectionnez une ligne.")
            return
        owner = self._owner
        if owner is None:
            QMessageBox.warning(self, "Suppression", "Module Fonctions indisponible.")
            return
        owner.delete_or_dismiss_poste(poste)

    def _on_assign_agent(self) -> None:
        if self._stale or self._loading:
            QMessageBox.warning(self,"Postes","Données non actualisées. Rechargez les postes.")
            return
        poste = self._selected_poste()
        if poste is None:
            QMessageBox.information(self, "Poste de tête", "Sélectionnez une ligne.")
            return
        if poste.multiple_titulaires:
            QMessageBox.warning(self,"Plusieurs titulaires","Comparez les dossiers et corrigez les affectations existantes avant une nouvelle attribution.")
            return
        from sme_gend.services.sme_editing_policy import evaluate_editing_state
        from sme_gend.ui.fonction_assign_agent_dialog import open_assign_poste_dialog

        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        try:
            changed=open_assign_poste_dialog(self,settings=self._service.settings,service=self._service,poste=poste)
        except Exception as exc:
            self.mark_stale()
            QMessageBox.critical(self,"Attribution impossible",str(exc))
            return
        if changed:
            self.reload()
            parent = self.parent()
            while parent is not None:
                if hasattr(parent, "mark_stale") and parent is not self:
                    try:
                        parent.mark_stale()
                    except Exception:
                        pass
                parent = parent.parent()

    def _on_new_fonction(self) -> None:
        if self._stale or self._loading:
            QMessageBox.warning(self,"Postes","Données non actualisées. Rechargez les postes.")
            return
        from sme_gend.services.sme_editing_policy import evaluate_editing_state
        from sme_gend.ui.fonction_smart_dialog import open_fonction_smart_dialog

        st = evaluate_editing_state(self._service.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return
        structure_code = self._filter.currentData()
        saved = open_fonction_smart_dialog(
            self,
            self._service,
            libelle="",
            structure_code=structure_code,
        )
        if saved:
            self.reload()

    def _selected_poste(self):
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        data = self._model.data(indexes[0], Qt.ItemDataRole.UserRole)
        return data

    def prepare_structure_filter(self, structure_code: str) -> None:
        """Prépare le filtre structure (appliqué au prochain chargement ou immédiatement)."""
        code = (structure_code or "").strip()
        if not code:
            return
        self._pending_structure_code = code
        if not self._stale and not self._loading:
            self._apply_pending_structure_filter()

    def _apply_pending_structure_filter(self) -> None:
        code = self._pending_structure_code
        if not code:
            return
        if self._select_structure_in_filter(code):
            self._apply_filter()
            self._pending_structure_code = None

    def _select_structure_in_filter(self, code: str) -> bool:
        for i in range(self._filter.count()):
            if self._filter.itemData(i) == code:
                self._filter.blockSignals(True)
                self._filter.setCurrentIndex(i)
                self._filter.blockSignals(False)
                return True
        from sme_gend.repositories.structure_repository import StructureRepository

        st = StructureRepository(self._service.settings).fetch_by_code(code)
        label = (st.designation or st.code) if st else code
        self._filter.blockSignals(True)
        self._filter.addItem(label, code)
        self._filter.setCurrentIndex(self._filter.count() - 1)
        self._filter.blockSignals(False)
        return True

    def mark_stale(self) -> None:
        self._generation+=1
        self._stale = True
        self._status.setText("Données non actualisées. Rechargez les postes avant une action.")
        self._sync_poste_actions()

    def ensure_loaded(self) -> None:
        if self._stale and not self._loading:
            self.reload()

    def reload(self) -> None:
        from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
        from sme_gend.repositories.structure_repository import StructureRepository
        from sme_gend.config.liste_generale_scope import resolve_carriere_scope_mois
        from sme_gend.services.postes_vacants import lister_postes_tete
        from sme_gend.ui.context_banner import _format_mois_fr

        if self._loading:
            return
        self._loading = True
        self._stale=True;self._generation+=1
        generation=self._generation
        self._sync_poste_actions()
        service = self._service
        settings = service.settings
        prev = self._pending_structure_code or self._filter.currentData()
        prev_type = self._filter_type.currentData()

        def work():
            mois = resolve_carriere_scope_mois(settings) or ""
            rows = lister_postes_tete(
                StructureRepository(settings).fetch_all(),
                [],
                ListeGeneraleRepository(settings).fetch_all(),
            )
            from sme_gend.services.postes_tete_exclusions import filter_excluded_postes

            return filter_excluded_postes(settings, rows), _format_mois_fr(mois)

        try:
            result = run_with_progress(
                self,
                "Postes de tête",
                "Lecture des postes de tête du mois…",
                work,
                silent=True,
            )
        finally:
            self._loading = False
        if result is None or generation!=self._generation or settings!=self._service.settings:
            self._stale=True;self._sync_poste_actions()
            self._status.setText("Chargement impossible ou contexte modifié : données non actualisées, actions désactivées.")
            return
        self._all, self._mois_label = result
        self._stale = False
        self._refill_structures(prev)
        self._refill_types(prev_type)
        self._apply_pending_structure_filter()
        self._apply_filter()

    def _refill_structures(self, previous: str | None) -> None:
        codes: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in self._all:
            key = row.structure_code.casefold()
            if key in seen:
                continue
            seen.add(key)
            codes.append((row.structure_label, row.structure_code))
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.addItem("Toutes les structures", None)
        for label, code in codes:
            self._filter.addItem(label, code)
        if previous is not None:
            for i in range(self._filter.count()):
                if self._filter.itemData(i) == previous:
                    self._filter.setCurrentIndex(i)
                    break
        self._filter.blockSignals(False)

    def _refill_types(self, previous: str | None) -> None:
        types: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in self._all:
            if not row.type_code or row.type_code in seen:
                continue
            seen.add(row.type_code)
            types.append((row.type_label, row.type_code))
        types.sort(key=lambda item: item[0].casefold())
        self._filter_type.blockSignals(True)
        self._filter_type.clear()
        self._filter_type.addItem("Tous les types", None)
        for label, code in types:
            self._filter_type.addItem(label, code)
        if previous is not None:
            for i in range(self._filter_type.count()):
                if self._filter_type.itemData(i) == previous:
                    self._filter_type.setCurrentIndex(i)
                    break
        self._filter_type.blockSignals(False)

    def _apply_filter(self) -> None:
        query = normalize_search_text(self._search.text().strip().casefold())
        code = self._filter.currentData()
        type_code = self._filter_type.currentData()
        vacants_only = self._vacants_only.isChecked()
        conflicts_only=self._conflicts_only.isChecked()
        matched = []
        for row in self._all:
            if code and row.structure_code != code:
                continue
            if type_code and row.type_code != type_code:
                continue
            unite = row.subdivision or "Commandement"
            blob = normalize_search_text(" ".join(
                (
                    row.structure_label,
                    unite,
                    row.type_label,
                    row.poste,
                    row.intitule,
                    row.titulaire_label,
                )
            ).casefold())
            if query and query not in normalize_search_text(blob):
                continue
            matched.append(row)
        vacants = sum(1 for row in matched if row.vacant)
        pourvus = len(matched) - vacants
        conflicts=sum(1 for row in matched if row.multiple_titulaires)
        shown = [row for row in matched if row.multiple_titulaires] if conflicts_only else [row for row in matched if row.vacant] if vacants_only else matched
        self._model.set_rows(shown)
        empty = not shown
        self._table.setVisible(not empty)
        self._empty.setVisible(empty)
        if empty:
            self._empty.setText(
                "Aucun poste de tête pour ce filtre."
                if self._all
                else "Aucun poste de tête dans le mois."
            )
        mois = self._mois_label or "mois SME"
        if not self._stale:self._status.setText(
            f"Postes de tête — {mois} · {vacants} vacant(s) · {pourvus} pourvu(s) · {conflicts} à plusieurs titulaires."
        )
        self._sync_poste_actions()
