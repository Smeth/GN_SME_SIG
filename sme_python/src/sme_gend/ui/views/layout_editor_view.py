"""Éditeur de mise en page : ordre des sections, subdivisions, renommage, rechercher/remplacer."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.detaches_file_config import DetachesStructureEntry, _norm_sigle
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.ui.confirm_dialogs import confirm_yes_no


def _parse_int_opt(s: str) -> int | None:
    s = (s or "").strip()
    return None if not s else int(s)


# ---------------------------------------------------------------------------
# Dialogue Rechercher / Remplacer SubArme
# ---------------------------------------------------------------------------

class _SearchReplaceDialog(QDialog):
    """Renommage global ou par structure d'un libellé SubArme."""

    def __init__(
        self,
        svc: LayoutConfigService,
        cfg: ListeLayoutConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rechercher / Remplacer — Subdivision d'arme")
        self.setMinimumWidth(480)
        self._svc = svc
        self._cfg = cfg
        self._confirmed_count = 0

        self._le_search = QLineEdit()
        self._le_search.setPlaceholderText("SubArme exact à rechercher (sensible à la casse)…")
        self._le_replace = QLineEdit()
        self._le_replace.setPlaceholderText("Nouveau libellé…")

        self._combo = QComboBox()
        self._combo.addItem("Toutes les structures", None)
        for sec in sorted(cfg.sections, key=lambda s: s.order):
            self._combo.addItem(sec.structure_code, sec.structure_code)

        self._lbl_preview = QLabel("Saisissez un libellé à rechercher, puis cliquez « Prévisualiser ».")
        self._lbl_preview.setWordWrap(True)

        self._btn_preview = QPushButton("Prévisualiser")
        self._btn_preview.clicked.connect(self._do_preview)
        self._le_search.returnPressed.connect(self._do_preview)

        bb = QDialogButtonBox()
        self._btn_ok = bb.addButton("Remplacer tout", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_ok.setEnabled(False)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Rechercher :", self._le_search)
        form.addRow("Remplacer par :", self._le_replace)
        form.addRow("Structure :", self._combo)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self._btn_preview)
        lay.addWidget(self._lbl_preview)
        lay.addWidget(bb)

    def _do_preview(self) -> None:
        search = self._le_search.text().strip()
        if not search:
            self._lbl_preview.setText("Le terme à rechercher ne peut pas être vide.")
            self._btn_ok.setEnabled(False)
            return
        struct_code: str | None = self._combo.currentData()
        n = self._svc.count_subarme_occurrences(search, struct_code)
        self._confirmed_count = n
        if n == 0:
            self._lbl_preview.setText("Aucune correspondance trouvée.")
            self._btn_ok.setEnabled(False)
        else:
            scope = struct_code or "toutes les structures"
            self._lbl_preview.setText(
                f"<b>{n} ligne(s)</b> seront mises à jour dans ListeGenerale ({scope})."
            )
            self._btn_ok.setEnabled(True)

    @property
    def search_text(self) -> str:
        return self._le_search.text().strip()

    @property
    def replace_text(self) -> str:
        return self._le_replace.text().strip()

    @property
    def target_structure(self) -> str | None:
        return self._combo.currentData()


# ---------------------------------------------------------------------------
# Vue principale
# ---------------------------------------------------------------------------

class LayoutEditorView(QWidget):
    """Configuration dynamique de la liste des présents (sections, subdivisions, renommage)."""

    def __init__(
        self,
        layout_service: LayoutConfigService,
        detaches_service: DetachesConfigService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._svc = layout_service
        self._detaches_svc = detaches_service
        self._cfg: ListeLayoutConfig = self._svc.load()
        self._on_saved: Callable[[], None] | None = None
        self._loading = False

        # Caches désignations et effectifs (rechargés à l'affichage)
        self._desig_cache: dict[str, str] = {}
        self._count_cache: dict[str, int] = {}

        # Debounce auto-sauvegarde (1 500 ms d'inactivité)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1500)
        self._autosave_timer.timeout.connect(self._auto_save)

        self._build_ui()
        self._connect_signals()
        self._populate_list()
        self._update_detail_enabled()
        self._auto_synced = False

    # ── API publique ────────────────────────────────────────────────────────

    def set_on_saved_callback(self, fn: Callable[[], None] | None) -> None:
        """Callback appelé après chaque sauvegarde (ex. déclenche l'aperçu de la liste)."""
        self._on_saved = fn

    # ── Construction UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Panneau gauche : liste des sections ────────────────────────────
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumWidth(220)

        lbl_sections = QLabel("<b>Sections</b> (glisser-déposer pour réordonner)")
        lbl_sections.setTextFormat(Qt.TextFormat.RichText)

        # boutons déplacement section
        self._btn_sec_up = QPushButton("↑")
        self._btn_sec_up.setToolTip("Monter d'une position (Ctrl+↑)")
        self._btn_sec_up.setFixedWidth(32)
        self._btn_sec_dn = QPushButton("↓")
        self._btn_sec_dn.setToolTip("Descendre d'une position (Ctrl+↓)")
        self._btn_sec_dn.setFixedWidth(32)
        self._btn_sec_top = QPushButton("⇑")
        self._btn_sec_top.setToolTip("Déplacer en première position")
        self._btn_sec_top.setFixedWidth(32)
        self._btn_sec_bot = QPushButton("⇓")
        self._btn_sec_bot.setToolTip("Déplacer en dernière position")
        self._btn_sec_bot.setFixedWidth(32)

        move_row = QHBoxLayout()
        for b in (self._btn_sec_up, self._btn_sec_dn, self._btn_sec_top, self._btn_sec_bot):
            move_row.addWidget(b)
        move_row.addStretch()

        self._btn_remove = QPushButton("Supprimer la section")
        self._btn_merge = QPushButton("Ajouter structures manquantes")
        self._btn_merge.setToolTip(
            "Ajoute en fin de liste les structures présentes en base mais absentes de la configuration."
        )
        self._btn_reset = QPushButton("Régénérer depuis TabStructure")
        self._btn_reset.setToolTip(
            "Reconstruit toute la liste à partir de TabStructure (écrase l'ordre actuel)."
        )

        mgmt_row = QHBoxLayout()
        mgmt_row.addWidget(self._btn_merge)
        mgmt_row.addWidget(self._btn_reset)
        mgmt_row.addWidget(self._btn_remove)
        mgmt_row.addStretch()

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 6, 0)
        left_lay.addWidget(lbl_sections)
        left_lay.addWidget(self._list, stretch=1)
        left_lay.addLayout(move_row)
        left_lay.addLayout(mgmt_row)

        # ── Panneau droit : détail section ─────────────────────────────────
        self._code = QLineEdit()
        self._code.setReadOnly(True)
        self._title_main = QLineEdit()
        self._title_main.setPlaceholderText("Vide = désignation TabStructure (titre principal)")
        self._title_sub = QLineEdit()
        self._title_sub.setPlaceholderText("Vide = code structure (sous-titre)")
        self._grp_sub = QCheckBox("Regrouper par subdivision d'arme")
        self._grp_sub.setChecked(True)

        self._btn_struct_db = QPushButton("Code & référentiel…")
        self._btn_struct_db.setToolTip(
            "Modifier la désignation, l'ordre de tri ou le code dans TabStructure + ListeGenerale."
        )

        row_code = QHBoxLayout()
        row_code.addWidget(self._code, stretch=1)
        row_code.addWidget(self._btn_struct_db)

        form = QFormLayout()
        form.addRow("Structure (code) :", row_code)
        form.addRow("Titre principal :", self._title_main)
        form.addRow("Sous-titre :", self._title_sub)
        form.addRow(self._grp_sub)

        # ── Bloc subdivisions ──────────────────────────────────────────────
        self._sub_list = QListWidget()
        self._sub_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._sub_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._sub_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._sub_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._sub_filter = QLineEdit()
        self._sub_filter.setPlaceholderText("Filtrer les subdivisions…")
        self._sub_filter.setClearButtonEnabled(True)
        self._sub_count = QLabel()

        self._btn_sub_up = QPushButton("↑")
        self._btn_sub_up.setFixedWidth(32)
        self._btn_sub_up.setToolTip("Monter (Ctrl+↑)")
        self._btn_sub_dn = QPushButton("↓")
        self._btn_sub_dn.setFixedWidth(32)
        self._btn_sub_dn.setToolTip("Descendre (Ctrl+↓)")
        self._btn_sub_sort = QPushButton("A→Z")
        self._btn_sub_sort.setToolTip("Tri alphabétique (« (Sans subdivision) » en dernier)")
        self._btn_fill_sub = QPushButton("Remplir depuis la base")
        self._btn_fill_sub.setToolTip("Importe les libellés SubArme distincts depuis ListeGenerale.")
        self._btn_sub_add = QPushButton("+")
        self._btn_sub_add.setFixedWidth(32)
        self._btn_sub_add.setToolTip("Ajouter un libellé manuellement")
        self._btn_sub_del = QPushButton("−")
        self._btn_sub_del.setFixedWidth(32)
        self._btn_sub_del.setToolTip("Supprimer la ligne sélectionnée")
        self._btn_sub_rename_db = QPushButton("Renommer en base…")
        self._btn_sub_rename_db.setToolTip(
            "Met à jour SubArme dans ListeGenerale pour la structure sélectionnée + met à jour la config."
        )

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtre :"))
        filter_row.addWidget(self._sub_filter, stretch=1)
        filter_row.addWidget(self._sub_count)

        sub_btn_row = QHBoxLayout()
        for b in (
            self._btn_sub_up, self._btn_sub_dn, self._btn_sub_sort,
            self._btn_fill_sub, self._btn_sub_add, self._btn_sub_del,
            self._btn_sub_rename_db,
        ):
            sub_btn_row.addWidget(b)
        sub_btn_row.addStretch()

        self._sub_block = QGroupBox("Ordre des subdivisions d'arme")
        sub_lay = QVBoxLayout(self._sub_block)
        sub_lay.addLayout(filter_row)
        sub_lay.addWidget(self._sub_list, stretch=1)
        sub_lay.addLayout(sub_btn_row)

        detail_box = QGroupBox("Section sélectionnée")
        detail_lay = QVBoxLayout(detail_box)
        detail_lay.addLayout(form)
        detail_lay.addWidget(self._sub_block, stretch=1)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.addWidget(detail_box, stretch=1)

        # ── Splitter ────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # ── Barre du bas ────────────────────────────────────────────────────
        self._btn_save = QPushButton("💾  Enregistrer")
        self._btn_save.setToolTip("Sauvegarder immédiatement (Ctrl+S)")
        font_bold = QFont()
        font_bold.setBold(True)
        self._btn_save.setFont(font_bold)
        self._btn_reload = QPushButton("↩  Recharger depuis le disque")
        self._btn_reload.setToolTip("Annule les modifications non sauvegardées.")
        self._btn_search_replace = QPushButton("🔍  Rech. / Rempl. SubArme…")
        self._btn_search_replace.setToolTip(
            "Renomme un libellé SubArme dans ListeGenerale (une ou toutes les structures)."
        )
        self._status_lbl = QLabel()
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        bottom = QHBoxLayout()
        bottom.addWidget(self._btn_save)
        bottom.addWidget(self._btn_reload)
        bottom.addWidget(self._btn_search_replace)
        bottom.addStretch()
        bottom.addWidget(self._status_lbl)

        # ── Layout principal ────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.addWidget(splitter, stretch=1)
        lay.addLayout(bottom)

    def _connect_signals(self) -> None:
        # Section list
        self._list.currentItemChanged.connect(self._on_select)
        self._list.model().rowsMoved.connect(self._on_section_dragged)

        # Section move buttons
        self._btn_sec_up.clicked.connect(lambda: self._move_section(-1))
        self._btn_sec_dn.clicked.connect(lambda: self._move_section(1))
        self._btn_sec_top.clicked.connect(self._move_section_top)
        self._btn_sec_bot.clicked.connect(self._move_section_bottom)

        # Section management
        self._btn_remove.clicked.connect(self._remove_section)
        self._btn_merge.clicked.connect(self._merge_missing)
        self._btn_reset.clicked.connect(self._reset_db)

        # Section detail fields — auto-save on change
        self._title_main.textChanged.connect(self._on_field_changed)
        self._title_sub.textChanged.connect(self._on_field_changed)
        self._grp_sub.stateChanged.connect(self._on_grp_sub_changed)

        # Structure rename
        self._btn_struct_db.clicked.connect(self._edit_structure_in_db)

        # Subdivision list
        self._sub_list.model().rowsMoved.connect(self._on_sub_dragged)
        self._sub_filter.textChanged.connect(self._apply_sub_filter)
        self._btn_sub_up.clicked.connect(lambda: self._move_sub_item(-1))
        self._btn_sub_dn.clicked.connect(lambda: self._move_sub_item(1))
        self._btn_sub_sort.clicked.connect(self._sort_sub_list_alpha)
        self._btn_fill_sub.clicked.connect(self._fill_sub_from_db)
        self._btn_sub_add.clicked.connect(self._add_sub_line)
        self._btn_sub_del.clicked.connect(self._remove_sub_line)
        self._btn_sub_rename_db.clicked.connect(self._rename_subarme_in_db)

        # Bottom bar
        self._btn_save.clicked.connect(self._manual_save)
        self._btn_reload.clicked.connect(self._reload)
        self._btn_search_replace.clicked.connect(self._open_search_replace)

        # Keyboard shortcuts
        sc_save = QShortcut(QKeySequence("Ctrl+S"), self)
        sc_save.activated.connect(self._manual_save)
        sc_up = QShortcut(QKeySequence("Ctrl+Up"), self)
        sc_up.activated.connect(lambda: self._move_sub_item(-1))
        sc_dn = QShortcut(QKeySequence("Ctrl+Down"), self)
        sc_dn.activated.connect(lambda: self._move_sub_item(1))

    # ── Cycle de vie ────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._auto_synced:
            self._auto_synced = True
            self._reload_caches()
            self._silent_sync_from_db()

    def _reload_caches(self) -> None:
        """Charge désignations et effectifs présents (une fois par affichage)."""
        try:
            self._desig_cache = self._svc.all_structure_designations()
        except Exception:
            self._desig_cache = {}
        try:
            self._count_cache = self._svc.present_counts_by_structure()
        except Exception:
            self._count_cache = {}

    def _silent_sync_from_db(self) -> None:
        """Fusionne silencieusement les structures et subdivisions manquantes depuis la base."""
        try:
            cfg, new_structs, new_subs = self._svc.sync_all_from_database(self._cfg)
        except Exception:
            return
        if not new_structs and not new_subs:
            return
        try:
            self._svc.save(cfg)
        except OSError:
            return
        self._cfg = cfg
        sel_id = self._current_section_id()
        self._populate_list(select_section_id=sel_id)
        parts: list[str] = []
        if new_structs:
            parts.append(f"{len(new_structs)} structure(s) ajoutée(s) : {', '.join(new_structs)}")
        if new_subs:
            parts.append(f"{len(new_subs)} subdivision(s) ajoutée(s)")
        self._set_status("🔄 Synchronisé : " + " — ".join(parts), color="#1565c0")

    # ── Auto-sauvegarde ─────────────────────────────────────────────────────

    def _mark_pending(self) -> None:
        self._autosave_timer.stop()
        self._set_status("● Modification en attente…", color="#b45309")

    def _on_field_changed(self) -> None:
        if self._loading:
            return
        self._mark_pending()
        self._autosave_timer.start()

    def _on_grp_sub_changed(self) -> None:
        if self._loading:
            return
        self._update_detail_enabled()
        self._mark_pending()
        self._autosave_timer.start()

    def _on_section_dragged(self) -> None:
        self._mark_pending()
        self._autosave_timer.start()

    def _on_sub_dragged(self) -> None:
        if self._loading:
            return
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _auto_save(self) -> None:
        """Auto-sauvegarde déclenchée par le timer de debounce."""
        self._sync_current_to_cfg()
        self._reorder_sections_from_ui()
        self._do_save()

    def _manual_save(self) -> None:
        """Sauvegarde immédiate (bouton ou Ctrl+S)."""
        self._autosave_timer.stop()
        self._sync_current_to_cfg()
        self._reorder_sections_from_ui()
        self._do_save()

    def _do_save(self) -> None:
        try:
            self._svc.save(self._cfg)
        except OSError as e:
            self._set_status(f"❌ Erreur : {e}", color="#b91c1c")
            QMessageBox.critical(self, "Erreur", f"Sauvegarde impossible :\n{e}")
            return
        sel_id = self._current_section_id()
        self._populate_list(select_section_id=sel_id)
        self._set_status("✓ Enregistré", color="#15803d")
        if self._on_saved:
            self._on_saved()

    def _set_status(self, msg: str, *, color: str = "") -> None:
        style = f"color:{color};" if color else ""
        self._status_lbl.setStyleSheet(style)
        self._status_lbl.setText(msg)

    # ── Section list ────────────────────────────────────────────────────────

    def _section_label(self, sec: SectionBlock, pos: int) -> str:
        code = sec.structure_code
        des = self._desig_cache.get(code, "")
        count = self._count_cache.get(code, 0)
        label = f"{pos + 1:02d}  {code}"
        if des and des.strip().lower() != code.strip().lower():
            label += f" — {des}"
        if count:
            label += f"  [{count}]"
        return label

    def _populate_list(self, select_section_id: str | None = None) -> None:
        self._loading = True
        self._list.clear()
        for pos, sec in enumerate(sorted(self._cfg.sections, key=lambda s: s.order)):
            it = QListWidgetItem(self._section_label(sec, pos))
            it.setData(Qt.ItemDataRole.UserRole, sec.id)
            self._list.addItem(it)
        self._loading = False
        if not self._list.count():
            return
        row = 0
        if select_section_id:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == select_section_id:
                    row = i
                    break
        self._list.setCurrentRow(row)

    def _current_section_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _current_section(self) -> SectionBlock | None:
        sid = self._current_section_id()
        if not sid:
            return None
        return next((s for s in self._cfg.sections if s.id == sid), None)

    def _on_select(self) -> None:
        sec = self._current_section()
        self._loading = True
        if not sec:
            self._code.clear()
            self._title_main.clear()
            self._title_sub.clear()
            self._sub_filter.clear()
            self._set_sub_list_labels([])
        else:
            self._code.setText(sec.structure_code)
            self._title_main.setText(sec.title_main_override or "")
            self._title_sub.setText(sec.title_sub_override or "")
            self._grp_sub.setChecked(sec.group_by_subdivision)
            self._sub_filter.clear()
            self._set_sub_list_labels(list(sec.subdivision_order) if sec.subdivision_order else [])
        self._loading = False
        self._update_detail_enabled()

    def _move_section(self, delta: int) -> None:
        row = self._list.currentRow()
        new_row = row + delta
        if new_row < 0 or new_row >= self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(new_row, item)
        self._list.setCurrentRow(new_row)
        self._mark_pending()
        self._autosave_timer.start()

    def _move_section_top(self) -> None:
        row = self._list.currentRow()
        if row <= 0:
            return
        item = self._list.takeItem(row)
        self._list.insertItem(0, item)
        self._list.setCurrentRow(0)
        self._mark_pending()
        self._autosave_timer.start()

    def _move_section_bottom(self) -> None:
        row = self._list.currentRow()
        last = self._list.count() - 1
        if row < 0 or row >= last:
            return
        item = self._list.takeItem(row)
        self._list.addItem(item)
        self._list.setCurrentRow(self._list.count() - 1)
        self._mark_pending()
        self._autosave_timer.start()

    def _reorder_sections_from_ui(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            sec = next((s for s in self._cfg.sections if s.id == sid), None)
            if sec:
                sec.order = i

    # ── Détail section ──────────────────────────────────────────────────────

    def _update_detail_enabled(self) -> None:
        has_sec = self._current_section() is not None
        ok = has_sec and self._grp_sub.isChecked()
        self._sub_block.setEnabled(ok)
        for w in (
            self._code, self._title_main, self._title_sub, self._grp_sub,
            self._btn_struct_db,
        ):
            w.setEnabled(has_sec)

    def _sync_current_to_cfg(self) -> None:
        """Recopie les widgets du panneau droit dans la section courante de _cfg."""
        sec = self._current_section()
        if not sec:
            return
        sec.title_main_override = self._title_main.text().strip() or None
        sec.title_sub_override = self._title_sub.text().strip() or None
        sec.group_by_subdivision = self._grp_sub.isChecked()
        if self._grp_sub.isChecked():
            labels = self._sub_labels_from_list()
            sec.subdivision_order = labels if labels else None
        else:
            sec.subdivision_order = None

    # ── Subdivisions ────────────────────────────────────────────────────────

    def _sub_labels_from_list(self) -> list[str]:
        return [
            self._sub_list.item(i).text().strip()
            for i in range(self._sub_list.count())
            if self._sub_list.item(i) and self._sub_list.item(i).text().strip()
        ]

    def _set_sub_list_labels(self, labels: list[str]) -> None:
        self._sub_list.clear()
        for lb in labels:
            s = (lb or "").strip()
            if s:
                self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()

    def _apply_sub_filter(self) -> None:
        needle = self._sub_filter.text().strip().lower()
        n_vis = 0
        for i in range(self._sub_list.count()):
            it = self._sub_list.item(i)
            match = not needle or needle in (it.text() if it else "").lower()
            self._sub_list.setRowHidden(i, not match)
            if match:
                n_vis += 1
        total = self._sub_list.count()
        if total == 0:
            self._sub_count.setText("")
        elif needle:
            self._sub_count.setText(f"{n_vis}/{total}")
        else:
            self._sub_count.setText(f"{total}")

    def _move_sub_item(self, delta: int) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        row = self._sub_list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._sub_list.count():
            return
        item = self._sub_list.takeItem(row)
        self._sub_list.insertItem(new_row, item)
        self._sub_list.setCurrentRow(new_row)
        self._apply_sub_filter()
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _sort_sub_list_alpha(self) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        labels = self._sub_labels_from_list()
        if len(labels) < 2:
            return
        sans = "(Sans subdivision)"
        rest = sorted((x for x in labels if x != sans), key=str.lower)
        out = rest + ([sans] if sans in labels else [])
        self._set_sub_list_labels(out)
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _fill_sub_from_db(self) -> None:
        sec = self._current_section()
        if not sec or not self._grp_sub.isChecked():
            return
        code = (sec.structure_code or "").strip()
        if not code:
            return
        try:
            db_labels = self._svc.subarme_labels_for_structure(code)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        merged = self._merge_sub_labels_db(sec, db_labels)
        self._set_sub_list_labels(merged)
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _merge_sub_labels_db(self, sec: SectionBlock, db_labels: list[str]) -> list[str]:
        db_set = {(x or "").strip() for x in db_labels if (x or "").strip()}
        pref = self._sub_labels_from_list() or list(sec.subdivision_order or [])
        sans = "(Sans subdivision)"
        out: list[str] = []
        seen: set[str] = set()
        for p in pref:
            p = (p or "").strip()
            if not p:
                continue
            if p == sans:
                if p not in seen:
                    out.append(p)
                    seen.add(p)
            elif p in db_set:
                out.append(p)
                seen.add(p)
        for x in sorted(db_set - seen):
            out.append(x)
        return out

    def _add_sub_line(self) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        text, ok = QInputDialog.getText(
            self, "Ajouter une subdivision",
            "Libellé (exactement comme en base, ou « (Sans subdivision) ») :",
        )
        if not ok:
            return
        s = (text or "").strip()
        if not s:
            return
        self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _remove_sub_line(self) -> None:
        row = self._sub_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Subdivision", "Sélectionnez une ligne à supprimer.")
            return
        self._sub_list.takeItem(row)
        self._apply_sub_filter()
        self._sync_current_to_cfg()
        self._mark_pending()
        self._autosave_timer.start()

    def _rename_subarme_in_db(self) -> None:
        sec = self._current_section()
        if not sec or not self._grp_sub.isChecked():
            return
        item = self._sub_list.currentItem()
        if not item:
            QMessageBox.information(self, "Subdivision", "Sélectionnez une ligne à renommer.")
            return
        old_label = item.text().strip()
        if not old_label:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Renommer SubArme en base")
        le_old = QLineEdit(old_label)
        le_old.setReadOnly(True)
        le_new = QLineEdit(old_label)
        form = QFormLayout()
        form.addRow("Libellé actuel :", le_old)
        form.addRow("Nouveau libellé :", le_new)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addLayout(form)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_label = le_new.text().strip()
        if not new_label or new_label == old_label:
            return
        if not confirm_yes_no(
            self, "Confirmer",
            f"Mettre à jour ListeGenerale : SubArme « {old_label} » → « {new_label} » "
            f"pour la structure « {sec.structure_code.strip()} » ?\n\n"
            "La mise en page JSON sera enregistrée automatiquement.",
            default_no=True,
        ):
            return
        try:
            n, self._cfg = self._svc.rename_subarme_in_database(
                self._cfg, sec.structure_code, old_label, new_label
            )
            self._svc.save(self._cfg)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        self._patch_detaches_sigle(old_label, new_label)
        item.setText(new_label)
        self._apply_sub_filter()
        self._set_status("✓ Enregistré", color="#15803d")
        if self._on_saved:
            self._on_saved()
        QMessageBox.information(self, "OK", f"{n} ligne(s) mise(s) à jour dans ListeGenerale.")

    # ── Structure ────────────────────────────────────────────────────────────

    def _edit_structure_in_db(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        code_cur = sec.structure_code.strip()
        row = self._svc.structure_row(code_cur)
        if row is None:
            QMessageBox.warning(
                self, "Référentiel",
                f"Aucune entrée dans TabStructure pour le code « {code_cur} ».\n\n"
                "Ajoutez-la depuis Référentiels → Structures.",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Structure — référentiel")
        le_code_new = QLineEdit(code_cur)
        le_des = QLineEdit(row.designation or "")
        le_ordre = QLineEdit("" if row.ordre_tri is None else str(row.ordre_tri))
        hint = QLabel(
            "Si vous changez le <b>code</b>, toutes les lignes ListeGenerale concernées sont mises "
            "à jour, ainsi que TabStructure et cette configuration JSON."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        form = QFormLayout()
        form.addRow("Nouveau code (vide = inchangé) :", le_code_new)
        form.addRow("Désignation :", le_des)
        form.addRow("Ordre tri :", le_ordre)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addWidget(hint)
        v.addLayout(form)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_code = le_code_new.text().strip() or code_cur
        des = le_des.text().strip()
        ordre = _parse_int_opt(le_ordre.text())
        if not des:
            QMessageBox.warning(self, "Structure", "La désignation ne peut pas être vide.")
            return
        if not confirm_yes_no(
            self, "Confirmer",
            "Enregistrer dans la base (TabStructure, ListeGenerale si changement de code) "
            "et sauvegarder la mise en page JSON ?",
            default_no=True,
        ):
            return
        try:
            if new_code != code_cur:
                self._cfg = self._svc.rename_structure_code(self._cfg, code_cur, new_code)
                code_cur = new_code
            self._svc.update_structure_referentiel(code_cur, des, ordre)
            self._svc.save(self._cfg)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        self._code.setText(code_cur)
        self._desig_cache[code_cur] = des
        self._populate_list(select_section_id=sec.id)
        self._set_status("✓ Enregistré", color="#15803d")
        if self._on_saved:
            self._on_saved()

    # ── Rechercher / Remplacer ───────────────────────────────────────────────

    def _open_search_replace(self) -> None:
        self._sync_current_to_cfg()
        dlg = _SearchReplaceDialog(self._svc, self._cfg, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        search = dlg.search_text
        replace = dlg.replace_text
        struct = dlg.target_structure
        if not search:
            return
        if not confirm_yes_no(
            self, "Confirmer le remplacement",
            f"Remplacer « {search} » par « {replace} » "
            + (f"dans la structure « {struct} »" if struct else "dans toutes les structures")
            + " ?\n\nCette opération sera appliquée directement dans ListeGenerale.",
            default_no=True,
        ):
            return
        try:
            n, self._cfg = self._svc.rename_subarme_global(self._cfg, search, replace, struct)
            self._svc.save(self._cfg)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        sel_id = self._current_section_id()
        self._populate_list(select_section_id=sel_id)
        self._on_select()
        self._set_status("✓ Enregistré", color="#15803d")
        if self._on_saved:
            self._on_saved()
        QMessageBox.information(self, "Remplacement", f"{n} ligne(s) mise(s) à jour dans ListeGenerale.")

    # ── Actions de gestion ──────────────────────────────────────────────────

    def _remove_section(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        if not confirm_yes_no(
            self, "Supprimer la section",
            f"Supprimer la section « {sec.structure_code} » de la configuration ?\n"
            "La modification sera sauvegardée immédiatement.",
            default_no=True,
        ):
            return
        self._cfg.sections = [s for s in self._cfg.sections if s.id != sec.id]
        for i, s in enumerate(sorted(self._cfg.sections, key=lambda x: x.order)):
            s.order = i
        self._do_save()

    def _merge_missing(self) -> None:
        self._reorder_sections_from_ui()
        self._cfg = self._svc.merge_missing_structures(self._cfg)
        self._do_save()

    def _reset_db(self) -> None:
        if not confirm_yes_no(
            self, "Régénérer",
            "Reconstruire toute la liste des sections à partir de TabStructure ?\n"
            "La configuration actuelle sera écrasée et sauvegardée.",
            default_no=True,
        ):
            return
        self._cfg = self._svc.build_default_from_database()
        self._do_save()

    def _reload(self) -> None:
        if not confirm_yes_no(
            self, "Recharger",
            "Recharger depuis le disque ? Les modifications non enregistrées seront perdues.",
            default_no=True,
        ):
            return
        self._autosave_timer.stop()
        sel_id = self._current_section_id()
        self._cfg = self._svc.load()
        self._populate_list(select_section_id=sel_id)
        self._set_status("", color="")

    # ── Utilitaires ─────────────────────────────────────────────────────────

    def _patch_detaches_sigle(self, old_label: str, new_label: str) -> None:
        if self._detaches_svc is None:
            return
        o = _norm_sigle(old_label)
        n_raw = (new_label or "").strip()
        if not o or not n_raw:
            return
        cfg = self._detaches_svc.load()
        new_list: list[DetachesStructureEntry] = []
        changed = False
        for ent in cfg.structures:
            if ent.sigle == o:
                new_list.append(
                    DetachesStructureEntry(sigle=n_raw, ordre=ent.ordre, titres_lignes=list(ent.titres_lignes))
                )
                changed = True
            else:
                new_list.append(ent)
        if changed:
            cfg.structures = new_list
            try:
                self._detaches_svc.save(cfg)
            except OSError as e:
                QMessageBox.warning(self, "Config. DÉTACHÉS", f"SubArme mis à jour, mais enregistrement detaches_config.json échoué :\n{e}")
