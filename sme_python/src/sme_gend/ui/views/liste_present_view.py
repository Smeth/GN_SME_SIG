"""Liste des présents — aperçu HTML configurable + personnalisation intégrée."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

import html as html_lib
from copy import copy
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_subdiv_tools import (
    dedupe_subdiv_labels,
    merge_subdiv_labels,
    sort_subdiv_labels_alpha,
    sort_subdiv_labels_by_count,
)
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.theme import apply_paper_preview_palette, status_semantic_color, theme_palette


# ─── Zoom steps ────────────────────────────────────────────────────────────────
_ZOOM_STEPS = [0.5, 0.67, 0.75, 0.80, 0.90, 1.0, 1.10, 1.25, 1.50, 1.75, 2.0, 2.50, 3.0]

# Mode fixe : position administrative explicitement Présent, indépendamment
# des autres positions configurées comme présence au corps dans la SME.
_PRESENCES_MODE = "present"


def _zoomed_preview_html(source, factor):
    """Scale explicit preview fonts without editing personnel text or export HTML."""
    if factor == 1:
        return source
    def css(value):
        return re.sub(r'(?i)(font-size\s*:\s*)(\d+(?:\.\d+)?)(pt|px)\b',
            lambda match:match[1]+f'{float(match[2])*factor:g}'+match[3], value)
    source = re.sub(r'(?is)(<style\b[^>]*>)(.*?)(</style\s*>)',
                    lambda match:match[1]+css(match[2])+match[3], source)
    return re.sub(r'''(?is)(\bstyle\s*=\s*)(["'])(.*?)\2''',
                  lambda match:match[1]+match[2]+css(match[3])+match[2], source)


# ─── Pages HTML d'état ─────────────────────────────────────────────────────────

def _error_page(exc_text: str) -> str:
    safe = html_lib.escape(exc_text)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Calibri,Arial,sans-serif;padding:30px;background:#fff8f8;}"
        "h2{color:#b91c1c;margin-top:0;}pre{background:#fef2f2;padding:12px;"
        "border:1px solid #fca5a5;border-radius:6px;white-space:pre-wrap;"
        "word-break:break-word;font-size:12px;}"
        "p{color:#666;font-size:13px;}</style></head><body>"
        "<h2>&#9888; Erreur lors de la génération de l'aperçu</h2>"
        f"<pre>{safe}</pre>"
        "<p>Vérifiez la connexion à la base de données et la configuration "
        "de mise en page. Consultez les journaux pour plus de détails.</p>"
        "</body></html>"
    )


def _minimal_refresh_page() -> str:
    """Fond neutre pendant le refresh : la progression est affichée par la barre Qt."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;height:100%;background:#fff;}</style>"
        "</head><body></body></html>"
    )


def _idle_page() -> str:
    """Écran d'attente : l'utilisateur déclenche le chargement via Actualiser."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>"
        "body{font-family:Calibri,'Segoe UI',Arial,sans-serif;margin:0;"
        "display:flex;align-items:center;justify-content:center;min-height:280px;"
        "background:#fafafa;color:#374151;}"
        ".box{text-align:center;padding:36px 44px;max-width:540px;"
        "border:1px dashed #cbd5e1;border-radius:12px;background:#fff;}"
        "h2{margin:0 0 10px;font-size:16pt;font-weight:600;color:#1e293b;}"
        "p{margin:0;font-size:12pt;line-height:1.55;color:#64748b;}"
        "</style></head><body>"
        "<div class='box'>"
        "<h2>Liste des présents</h2>"
        "<p>Cliquez sur <b>&#8635; Actualiser</b> (ou <b>F5</b>) pour afficher la liste.<br>"
        "Le chargement peut prendre quelques secondes selon l'effectif du mois.</p>"
        "</div></body></html>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Panneau de personnalisation rapide (côté gauche)
# ─────────────────────────────────────────────────────────────────────────────

class _QuickEditPanel(QWidget):
    """
    Panneau latéral compact : réordonner sections/subdivisions, ajuster titres,
    renommer subdivisions en base (double-clic). Auto-sauvegarde 1 200 ms.
    """

    def __init__(
        self,
        layout_svc: LayoutConfigService,
        on_saved: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("listePresentQuickPanel")
        self._svc = layout_svc
        self._on_saved = on_saved
        self._cfg: ListeLayoutConfig | None = None
        self._loading = False
        self._desig_cache: dict[str, str] = {}
        self._count_cache: dict[str, int] = {}
        self._db_codes: set[str] = set()
        self._auto_synced = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._auto_save)

        self._build_ui()
        self._load_config_only()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.isVisible():
            self._ensure_data_caches()
            if not self._auto_synced:
                self._auto_synced = True
                self._sync_from_db(silent=True)

    # ── API publique ──────────────────────────────────────────────────────────

    def reload_from_disk(self) -> None:
        """Resynchronise depuis le disque."""
        self._safe_reload(preserve_selection=True)

    def _discard_and_reload(self) -> None:
        if self.has_unsaved_changes() and not confirm_yes_no(
            self, "Recharger la mise en page",
            "Abandonner le brouillon et recharger la mise en page enregistrée ?",
            default_no=True,
        ):
            return
        try:
            candidate = self._svc.load()
        except Exception as exc:
            self._set_status(f"Chargement impossible ; brouillon conservé : {exc}", "error")
            return
        self._timer.stop()
        self._cfg = candidate
        self._draft_dirty = False
        self._count_cache.clear()
        self._desig_cache.clear()
        self._populate()
        self._set_status("Mise en page rechargée", "success")

    def _load_config_only(self) -> None:
        try:
            self._cfg = self._svc.load()
        except Exception as exc:
            self._set_status(f"❌ Erreur chargement : {exc}", "error")
            self._cfg = None

    def _ensure_data_caches(self) -> None:
        if self._count_cache:
            return
        self._reload_caches()
        self._populate(select_id=self._current_id())

    def has_unsaved_changes(self) -> bool:
        return bool(getattr(self, "_draft_dirty", False))

    def _safe_reload(self, preserve_selection: bool = False) -> None:
        if self.has_unsaved_changes():
            self._set_status("Brouillon conservé : enregistrez avant l’actualisation.", "warning")
            return
        sel_id = self._current_id() if preserve_selection else None
        self._load_config_only()
        if self._cfg is None:
            return
        self._count_cache.clear()
        self._desig_cache.clear()
        self._ensure_data_caches()
        self._populate(select_id=sel_id)

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)

        lbl_sec = QLabel("<b>Sections</b>")
        lbl_sec.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_sec = lbl_sec

        self._sec_list = QListWidget()
        self._sec_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._sec_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._sec_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._sec_list.setMaximumHeight(180)
        self._sec_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sec_list.currentItemChanged.connect(self._on_sec_select)
        self._sec_list.model().rowsMoved.connect(self._on_sec_dragged)

        self._btn_up   = self._mkbtn("↑", "Monter (Alt+↑)")
        self._btn_dn   = self._mkbtn("↓", "Descendre (Alt+↓)")
        self._btn_top  = self._mkbtn("⇑", "Mettre en premier")
        self._btn_bot  = self._mkbtn("⇓", "Mettre en dernier")
        self._btn_up.clicked.connect(lambda: self._move_sec(-1))
        self._btn_dn.clicked.connect(lambda: self._move_sec(1))
        self._btn_top.clicked.connect(self._sec_to_top)
        self._btn_bot.clicked.connect(self._sec_to_bottom)

        QShortcut(QKeySequence("Alt+Up"),   self._sec_list).activated.connect(lambda: self._move_sec(-1))
        QShortcut(QKeySequence("Alt+Down"), self._sec_list).activated.connect(lambda: self._move_sec(1))

        sec_move_row = QHBoxLayout()
        for b in (self._btn_up, self._btn_dn, self._btn_top, self._btn_bot):
            sec_move_row.addWidget(b)
        sec_move_row.addStretch()

        self._lbl_code = QLabel()
        self._apply_theme_inline_styles()
        self._le_titre1 = QLineEdit()
        self._le_titre1.setPlaceholderText("(désignation structure)")
        self._le_titre1.textChanged.connect(self._on_field_changed)
        self._le_titre2 = QLineEdit()
        self._le_titre2.setPlaceholderText("(code structure)")
        self._le_titre2.textChanged.connect(self._on_field_changed)
        self._chk_sub = QCheckBox("Grouper par subdivision d'arme")
        self._chk_sub.stateChanged.connect(self._on_chk_changed)

        det_form = QFormLayout()
        det_form.setContentsMargins(4, 4, 4, 4)
        det_form.setSpacing(4)
        det_form.addRow("Code :", self._lbl_code)
        det_form.addRow("Titre principal :", self._le_titre1)
        det_form.addRow("Titre secondaire :", self._le_titre2)
        det_form.addRow(self._chk_sub)
        det_grp = QGroupBox("Section sélectionnée")
        det_grp.setLayout(det_form)

        self._sub_list = QListWidget()
        self._sub_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._sub_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._sub_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._sub_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._sub_list.model().rowsMoved.connect(self._on_sub_dragged)
        self._sub_list.itemDoubleClicked.connect(self._rename_sub_inline)
        self._sub_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._sub_list.customContextMenuRequested.connect(self._sub_context_menu)
        self._sub_list.setToolTip(
            "Glisser-déposer pour réordonner.\n"
            "Ctrl+clic / Maj+clic : sélection multiple (fusion).\n"
            "Double-clic : renommer en base."
        )

        self._sub_filter = QLineEdit()
        self._sub_filter.setPlaceholderText("Filtrer…")
        self._sub_filter.setClearButtonEnabled(True)
        self._sub_filter.textChanged.connect(self._apply_sub_filter)
        self._sub_count = QLabel()
        self._sub_count.setStyleSheet("font-size:10px;")

        self._btn_sub_up   = self._mkbtn("↑", "Monter (Ctrl+↑)")
        self._btn_sub_dn   = self._mkbtn("↓", "Descendre (Ctrl+↓)")
        self._btn_sub_sort = QPushButton("A→Z")
        self._btn_sub_sort.setToolTip("Tri alphabétique")
        self._btn_sub_sort_eff = QPushButton("Eff↓")
        self._btn_sub_sort_eff.setToolTip("Tri par effectif (base) décroissant")
        self._btn_sub_add = self._mkbtn("+", "Ajouter une rubrique à la mise en page")
        self._btn_sub_del = self._mkbtn("−", "Retirer de la mise en page")
        self._btn_sub_merge = QPushButton("Fusionner")
        self._btn_sub_merge.setToolTip(
            "Fusionne les subdivisions sélectionnées en un seul libellé "
            "(mise en page ; modification de la base sur choix explicite)."
        )
        self._btn_sub_dedupe = QPushButton("Dédoubl.")
        self._btn_sub_dedupe.setToolTip("Retire les libellés en double dans cette liste (insensible à la casse).")
        self._btn_sub_rename = QPushButton("Renommer en base…")
        self._btn_sub_rename.setToolTip("Renommer la subdivision dans les situations du mois choisi, puis enregistrer la mise en page.")
        self._btn_sub_more = QToolButton()
        self._btn_sub_more.setText("⋮")
        self._btn_sub_more.setToolTip("Autres actions subdivisions")
        self._btn_sub_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_sub_more_menu()

        self._btn_fill_sub = QPushButton("Remplir base")
        self._btn_fill_sub.setToolTip(
            "Complète l'ordre avec les libellés présents en base (sans écraser l'ordre configuré)."
        )
        self._btn_sync_db = QPushButton("↻ Sync")
        self._btn_sync_db.setToolTip(
            "Aligne structures et subdivisions sur ref_structure / carrière."
        )
        self._btn_sync_db.clicked.connect(self._sync_from_db)
        self._btn_reload_draft = QPushButton("Recharger…")
        self._btn_reload_draft.setToolTip("Recharger la mise en page enregistrée ; confirmation avant d’abandonner un brouillon.")
        self._btn_reload_draft.clicked.connect(self._discard_and_reload)
        self._btn_sub_up.clicked.connect(lambda: self._move_sub(-1))
        self._btn_sub_dn.clicked.connect(lambda: self._move_sub(1))
        self._btn_sub_sort.clicked.connect(self._sort_sub_alpha)
        self._btn_sub_sort_eff.clicked.connect(self._sort_sub_by_effectif)
        self._btn_sub_add.clicked.connect(self._add_sub_line)
        self._btn_sub_del.clicked.connect(self._remove_sub_line)
        self._btn_sub_merge.clicked.connect(self._merge_sub_selection)
        self._btn_sub_dedupe.clicked.connect(self._dedupe_sub_layout)
        self._btn_sub_rename.clicked.connect(self._rename_sub_dialog)
        self._btn_fill_sub.clicked.connect(self._fill_sub_from_db)

        QShortcut(QKeySequence("Ctrl+Up"),   self._sub_list).activated.connect(lambda: self._move_sub(-1))
        QShortcut(QKeySequence("Ctrl+Down"), self._sub_list).activated.connect(lambda: self._move_sub(1))

        sub_filter_row = QHBoxLayout()
        sub_filter_row.addWidget(self._sub_filter, stretch=1)
        sub_filter_row.addWidget(self._sub_count)

        sub_row1 = QHBoxLayout()
        for b in (
            self._btn_sub_up, self._btn_sub_dn, self._btn_sub_sort,
            self._btn_sub_sort_eff, self._btn_sub_add, self._btn_sub_del,
        ):
            sub_row1.addWidget(b)
        sub_row1.addStretch()

        sub_row2 = QHBoxLayout()
        sub_row2.addWidget(self._btn_sub_merge)
        sub_row2.addWidget(self._btn_sub_dedupe)
        sub_row2.addWidget(self._btn_sub_rename)
        sub_row2.addWidget(self._btn_sub_more)
        sub_row2.addStretch()

        sub_row3 = QHBoxLayout()
        sub_row3.addWidget(self._btn_fill_sub)
        sub_row3.addWidget(self._btn_sync_db)
        sub_row3.addWidget(self._btn_reload_draft)
        sub_row3.addStretch()

        self._sub_grp = QGroupBox("Subdivisions d'arme")
        sub_lay = QVBoxLayout(self._sub_grp)
        sub_lay.setContentsMargins(4, 4, 4, 4)
        sub_lay.addLayout(sub_filter_row)
        sub_lay.addWidget(self._sub_list, stretch=1)
        sub_lay.addLayout(sub_row1)
        sub_lay.addLayout(sub_row2)
        sub_lay.addLayout(sub_row3)

        self._status_lbl = QLabel()
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("font-size:11px;")
        self._btn_save = QPushButton("💾  Enregistrer")
        self._btn_save.setToolTip("Ctrl+S")
        self._btn_save.clicked.connect(self._manual_save)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._manual_save)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        bot = QHBoxLayout()
        bot.addWidget(self._btn_save)
        bot.addWidget(self._status_lbl, stretch=1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addWidget(lbl_sec)
        lay.addWidget(self._sec_list)
        lay.addLayout(sec_move_row)
        lay.addWidget(det_grp)
        lay.addWidget(self._sub_grp, stretch=1)
        lay.addWidget(sep)
        lay.addLayout(bot)

    @staticmethod
    def _mkbtn(text: str, tooltip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setFixedWidth(30)
        if tooltip:
            b.setToolTip(tooltip)
        return b

    def _apply_theme_inline_styles(self) -> None:
        pal = theme_palette()
        self._lbl_code.setStyleSheet(f"font-weight:bold; color:{pal['accent']};")
        self._lbl_sec.setText(
            f"<span style='color:{pal['text']};'><b>Sections</b></span>"
        )

    def refresh_theme_styles(self) -> None:
        self._apply_theme_inline_styles()

    # ── Caches ───────────────────────────────────────────────────────────────

    def _reload_caches(self) -> None:
        self._cache_errors = []
        try:
            self._desig_cache = self._svc.all_structure_designations()
        except Exception:
            self._desig_cache = {}
            self._cache_errors.append("désignations")
        try:
            self._count_cache = self._svc.present_counts_by_structure()
        except Exception:
            self._count_cache = {}
            self._cache_errors.append("effectifs")
        try:
            self._db_codes = self._svc.database_structure_codes()
        except Exception:
            self._db_codes = set()
            self._cache_errors.append("structures")

    def _sync_from_db(self, *, silent: bool = False) -> None:
        if silent and self.has_unsaved_changes():
            return
        if self._cfg is None:
            return
        try:
            from copy import deepcopy
            cfg, report = self._svc.reconcile_with_database(deepcopy(self._cfg))
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Synchronisation", str(exc))
            return
        if not report.changed:
            if not silent:
                self._set_status("✓ Déjà aligné sur la base", "success")
            return
        self._draft_dirty = True
        try:
            self._svc.save(cfg)
        except Exception as exc:
            self._set_status(f"Non enregistré — brouillon conservé : {exc}", "error")
            if not silent:
                QMessageBox.critical(self, "Synchronisation", str(exc))
            return
        self._cfg = cfg
        self._draft_dirty = False
        self._reload_caches()
        self._populate(select_id=self._current_id())
        parts: list[str] = []
        if report.removed_structure_codes:
            parts.append(
                f"{len(report.removed_structure_codes)} retirée(s) : "
                f"{', '.join(report.removed_structure_codes[:4])}"
            )
        if report.added_structure_codes:
            parts.append(
                f"{len(report.added_structure_codes)} ajoutée(s) : "
                f"{', '.join(report.added_structure_codes[:4])}"
            )
        if report.subdivisions_appended:
            parts.append(f"{len(report.subdivisions_appended)} subdivision(s) complétée(s)")
        msg = "🔄 Sync base : " + " — ".join(parts) if parts else "✓ Sync base"
        self._set_status(msg, "info" if silent else "success")
        self._on_saved()

    # ── Population ────────────────────────────────────────────────────────────

    def _sec_label(self, sec: SectionBlock, pos: int) -> str:
        code = sec.structure_code
        des  = self._desig_cache.get(code, "")
        n    = self._count_cache.get(code, 0)
        invalid = bool(self._db_codes) and code not in self._db_codes
        lbl  = f"{pos + 1:02d}  {code}"
        if invalid:
            lbl = f"⚠ {lbl}"
        if des and des.strip().casefold() != code.strip().casefold():
            lbl += f" — {des}"
        elif invalid:
            lbl += " — (absent ref_structure)"
        if n:
            lbl += f"  [{n}]"
        label = lbl
        if getattr(self,"_cache_errors",[]):
            label += " [lecture incomplète : " + ', '.join(self._cache_errors) + "]"
        return label

    def _populate(self, select_id: str | None = None) -> None:
        if self._cfg is None:
            return
        self._loading = True
        self._sec_list.clear()
        for pos, sec in enumerate(sorted(self._cfg.sections, key=lambda s: s.order)):
            it = QListWidgetItem(self._sec_label(sec, pos))
            it.setData(Qt.ItemDataRole.UserRole, sec.id)
            self._sec_list.addItem(it)
        self._loading = False
        if not self._sec_list.count():
            return
        row = 0
        if select_id:
            for i in range(self._sec_list.count()):
                it = self._sec_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == select_id:
                    row = i
                    break
        self._sec_list.setCurrentRow(row)

    def _current_id(self) -> str | None:
        it = self._sec_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _current_sec(self) -> SectionBlock | None:
        if self._cfg is None:
            return None
        sid = self._current_id()
        if not sid:
            return None
        return next((s for s in self._cfg.sections if s.id == sid), None)

    def _on_sec_select(self) -> None:
        sec = self._current_sec()
        self._loading = True
        if sec:
            self._lbl_code.setText(sec.structure_code)
            self._le_titre1.setText(sec.title_main_override or "")
            self._le_titre2.setText(sec.title_sub_override or "")
            self._chk_sub.setChecked(sec.group_by_subdivision)
            self._sub_filter.clear()
            self._set_sub_list_labels(list(sec.subdivision_order or []))
        else:
            self._lbl_code.clear()
            self._le_titre1.clear()
            self._le_titre2.clear()
            self._sub_filter.clear()
            self._set_sub_list_labels([])
        self._loading = False
        self._sub_grp.setEnabled(bool(sec and self._chk_sub.isChecked()))

    # ── Déplacements sections ─────────────────────────────────────────────────

    def _move_sec(self, delta: int) -> None:
        row = self._sec_list.currentRow()
        new = row + delta
        if new < 0 or new >= self._sec_list.count():
            return
        it = self._sec_list.takeItem(row)
        self._sec_list.insertItem(new, it)
        self._sec_list.setCurrentRow(new)
        self._trigger()

    def _sec_to_top(self) -> None:
        row = self._sec_list.currentRow()
        if row <= 0:
            return
        it = self._sec_list.takeItem(row)
        self._sec_list.insertItem(0, it)
        self._sec_list.setCurrentRow(0)
        self._trigger()

    def _sec_to_bottom(self) -> None:
        row  = self._sec_list.currentRow()
        last = self._sec_list.count() - 1
        if row < 0 or row >= last:
            return
        it = self._sec_list.takeItem(row)
        self._sec_list.addItem(it)
        self._sec_list.setCurrentRow(self._sec_list.count() - 1)
        self._trigger()

    def _on_sec_dragged(self) -> None:
        self._trigger()

    # ── Déplacements subdivisions ─────────────────────────────────────────────

    def _move_sub(self, delta: int) -> None:
        row = self._sub_list.currentRow()
        new = row + delta
        if new < 0 or new >= self._sub_list.count():
            return
        it = self._sub_list.takeItem(row)
        self._sub_list.insertItem(new, it)
        self._sub_list.setCurrentRow(new)
        self._sync_sub_to_sec()
        self._trigger()

    def _on_sub_dragged(self) -> None:
        if self._loading:
            return
        self._sync_sub_to_sec()
        self._trigger()

    # ── Subdivisions : outils ─────────────────────────────────────────────────

    def _build_sub_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Monter", lambda: self._move_sub(-1))
        menu.addAction("Descendre", lambda: self._move_sub(1))
        menu.addSeparator()
        menu.addAction("Tri A → Z", self._sort_sub_alpha)
        menu.addAction("Tri par effectif ↓", self._sort_sub_by_effectif)
        menu.addAction("Dédoublonner les rubriques", self._dedupe_sub_layout)
        menu.addSeparator()
        menu.addAction("Ajouter rubrique…", self._add_sub_line)
        menu.addAction("Retirer de la mise en page", self._remove_sub_line)
        menu.addAction("Fusionner sélection…", self._merge_sub_selection)
        menu.addAction("Renommer en base…", self._rename_sub_dialog)
        menu.addSeparator()
        menu.addAction("Remplir depuis la base", self._fill_sub_from_db)
        menu.addAction("Synchroniser avec la base", self._sync_from_db)
        self._btn_sub_more.setMenu(menu)

    def _sub_context_menu(self, pos) -> None:
        menu = self._btn_sub_more.menu()
        if menu is not None:
            menu.exec(self._sub_list.mapToGlobal(pos))

    def _sub_labels_from_list(self) -> list[str]:
        return [
            self._sub_list.item(i).text().strip()
            for i in range(self._sub_list.count())
            if self._sub_list.item(i) and self._sub_list.item(i).text().strip()
        ]

    def _set_sub_list_labels(self, labels: list[str]) -> None:
        self._sub_list.clear()
        for lbl in labels:
            s = (lbl or "").strip()
            if s:
                self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()

    def _apply_sub_filter(self) -> None:
        needle = normalize_search_text(self._sub_filter.text().strip().casefold())
        visible = 0
        for i in range(self._sub_list.count()):
            it = self._sub_list.item(i)
            if it is None:
                continue
            match = not needle or needle in normalize_search_text(it.text().casefold())
            it.setHidden(not match)
            if match:
                visible += 1
        total = self._sub_list.count()
        if needle:
            self._sub_count.setText(f"{visible}/{total}")
        elif total:
            self._sub_count.setText(str(total))
        else:
            self._sub_count.clear()

    def _selected_sub_labels(self) -> list[str]:
        return [
            it.text().strip()
            for it in self._sub_list.selectedItems()
            if it.text().strip()
        ]

    def _sort_sub_alpha(self) -> None:
        labels = self._sub_labels_from_list()
        if len(labels) < 2:
            return
        self._set_sub_list_labels(sort_subdiv_labels_alpha(labels))
        self._sync_sub_to_sec()
        self._trigger()

    def _sort_sub_by_effectif(self) -> None:
        sec = self._current_sec()
        labels = self._sub_labels_from_list()
        if not sec or len(labels) < 2:
            return
        try:
            counts = self._svc.present_counts_by_subdivision(sec.structure_code, labels)
        except Exception as exc:
            QMessageBox.warning(self, "Effectifs", str(exc))
            return
        self._set_sub_list_labels(sort_subdiv_labels_by_count(labels, counts))
        self._sync_sub_to_sec()
        self._trigger()

    def _add_sub_line(self) -> None:
        sec = self._current_sec()
        if not sec or not self._chk_sub.isChecked():
            return
        text, ok = QInputDialog.getText(
            self,
            "Ajouter subdivision",
            f"Libellé rubrique (structure {sec.structure_code}) :",
        )
        if not ok:
            return
        s = (text or "").strip()
        if not s:
            return
        self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()
        self._sync_sub_to_sec()
        self._trigger()

    def _remove_sub_line(self) -> None:
        items = self._sub_list.selectedItems()
        if not items:
            QMessageBox.information(
                self, "Subdivision", "Sélectionnez une ou plusieurs rubriques à retirer de la mise en page."
            )
            return
        if not confirm_yes_no(
            self,
            "Retirer de la mise en page",
            f"Retirer {len(items)} rubrique(s) de la mise en page ?\n"
            "(Les fiches en base ne sont pas modifiées.)",
            default_no=True,
        ):
            return
        for it in items:
            row = self._sub_list.row(it)
            if row >= 0:
                self._sub_list.takeItem(row)
        self._apply_sub_filter()
        self._sync_sub_to_sec()
        self._trigger()

    def _dedupe_sub_layout(self) -> None:
        labels = self._sub_labels_from_list()
        deduped = dedupe_subdiv_labels(labels)
        if deduped == labels:
            self._set_status("Aucun doublon dans la mise en page", "info")
            return
        self._set_sub_list_labels(deduped)
        self._sync_sub_to_sec()
        self._trigger()
        self._set_status(
            f"✓ {len(labels) - len(deduped)} doublon(s) retiré(s) de la mise en page",
            "success",
        )

    def _merge_sub_selection(self) -> None:
        sec = self._current_sec()
        selected = self._selected_sub_labels()
        if not sec:
            return
        if len(selected) < 2:
            QMessageBox.information(
                self,
                "Fusionner",
                "Sélectionnez au moins 2 subdivisions (Ctrl+clic ou Maj+clic).",
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Fusionner subdivisions")
        dlg.setMinimumWidth(420)
        info = QLabel(
            f"<b>{len(selected)}</b> rubrique(s) sélectionnée(s) pour la structure "
            f"<b>{html_lib.escape(sec.structure_code)}</b> :"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lst = QListWidget()
        for s in selected:
            lst.addItem(s)
        lst.setMaximumHeight(100)
        combo = QComboBox()
        combo.setEditable(True)
        for s in selected:
            combo.addItem(s)
        combo.setCurrentIndex(0)
        chk_db = QCheckBox("Renommer aussi les subdivisions dans les situations du mois")
        chk_db.setChecked(False)
        chk_db.setToolTip(
            "Chaque libellé source sera renommé vers le libellé cible en base, "
            "puis la mise en page sera enregistrée dans la même opération."
        )
        form = QFormLayout()
        form.addRow("Libellé cible :", combo)
        form.addRow("", chk_db)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(info)
        lay.addWidget(lst)
        lay.addLayout(form)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target = combo.currentText().strip()
        if not target:
            return
        sources = [s for s in selected if s.casefold() != target.casefold()]
        if not sources and target in selected:
            sources = [s for s in selected if s != target]
        if len(selected) >= 2 and not sources:
            sources = selected[1:]

        msg = (
            f"Fusion de rubriques : {len(selected)} rubrique(s) → « {target} »"
        )
        if chk_db.isChecked():
            try:
                month, count = self._svc.subdivision_write_preview(sec.structure_code, sources)
            except Exception as exc:
                QMessageBox.critical(self, "Périmètre de fusion", str(exc))
                return
            msg += f"\nMois : {month or 'contexte courant'} — {count} situation(s) concernée(s).\n"
            msg += (
                f"\n\nEn base, renommer :\n"
                + "\n".join(f"  • « {s} » → « {target} »" for s in sources if s != target)
            )
        if not confirm_yes_no(self, "Confirmer fusion", msg, default_no=True):
            return

        self._timer.stop()
        self._sync_current_to_cfg()
        if chk_db.isChecked():
            try:
                n, candidate = self._svc.merge_subarmes_in_database(
                    self._cfg, sec.structure_code, sources, target)
            except Exception as exc:
                self._set_status(f"Non enregistré — brouillon conservé : {exc}", "error")
                QMessageBox.critical(self, "Fusion annulée", str(exc))
                return
            self._cfg = candidate
            self._populate(select_id=sec.id)
            self._on_saved()
            self._set_status(f"Fusion enregistrée — {n} situation(s) modifiée(s)", "success")
        else:
            labels = self._sub_labels_from_list()
            merged = merge_subdiv_labels(labels, selected, target)
            self._set_sub_list_labels(merged)
            self._sync_sub_to_sec()
            if self._do_save():
                self._set_status(f"Rubriques fusionnées vers « {target} » — données du personnel inchangées", "success")

    def _rename_sub_dialog(self) -> None:
        item = self._sub_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Renommer", "Sélectionnez une subdivision.")
            return
        self._rename_sub_inline(item)

    def _fill_sub_from_db(self) -> None:
        sec = self._current_sec()
        if not sec:
            return
        try:
            merged = self._svc.effective_subdivisions_for_section(sec)
        except Exception as exc:
            QMessageBox.critical(self, "Base de données", str(exc))
            return
        before = list(sec.subdivision_order or [])
        self._set_sub_list_labels(merged)
        self._sync_sub_to_sec()
        self._trigger()
        added = len(merged) - len(before)
        if added > 0:
            self._set_status(f"✓ {added} subdivision(s) complétée(s) depuis la base", "success")
        else:
            self._set_status("✓ Subdivisions déjà alignées", "success")

    def _rename_sub_inline(self, item: QListWidgetItem) -> None:
        """Double-clic : renomme la SubArme directement en base de données."""
        old_name = item.text().strip()
        sec = self._current_sec()
        if not sec or not old_name:
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Renommer la subdivision d'arme",
            f"Nouveau libellé pour « {old_name} »\n(structure : {sec.structure_code}) :",
            text=old_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        try:
            month, count = self._svc.subdivision_write_preview(sec.structure_code, [old_name])
        except Exception as exc:
            QMessageBox.critical(self, "Périmètre du renommage", str(exc))
            return
        if not confirm_yes_no(self, "Renommer les situations du mois",
                f"Mois : {month or 'contexte courant'} — structure : {sec.structure_code}.\n"
                f"{count} situation(s) concernée(s), y compris hors de la liste des présents.\n"
                f"Remplacer « {old_name} » par « {new_name} » et enregistrer la mise en page ?",
                default_no=True):
            return
        self._timer.stop()
        self._sync_current_to_cfg()
        try:
            n, candidate = self._svc.rename_subarme_in_database(
                self._cfg, sec.structure_code, old_name, new_name)
        except Exception as exc:
            self._set_status(f"Non enregistré — brouillon conservé : {exc}", "error")
            QMessageBox.critical(self, "Renommage annulé", str(exc))
            return
        self._cfg = candidate
        self._populate(select_id=sec.id)
        self._on_saved()
        self._set_status(f"Renommé — {n} situation(s) mise(s) à jour", "success")

    # ── Signaux champs ────────────────────────────────────────────────────────

    def _on_field_changed(self) -> None:
        if self._loading:
            return
        self._sync_current_to_cfg()
        self._trigger()

    def _on_chk_changed(self) -> None:
        if self._loading:
            return
        self._sub_grp.setEnabled(self._chk_sub.isChecked())
        self._sync_current_to_cfg()
        self._trigger()

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _trigger(self) -> None:
        self._draft_dirty = True
        self._set_status("● Modification en attente…", "warning")
        self._timer.start()

    def _sync_sub_to_sec(self) -> None:
        sec = self._current_sec()
        if not sec:
            return
        labels = self._sub_labels_from_list()
        sec.subdivision_order = labels or None

    def _sync_current_to_cfg(self) -> None:
        sec = self._current_sec()
        if sec:
            sec.title_main_override = self._le_titre1.text().strip() or None
            sec.title_sub_override  = self._le_titre2.text().strip() or None
            sec.group_by_subdivision = self._chk_sub.isChecked()
            self._sync_sub_to_sec()
        if self._cfg is None:
            return
        for i in range(self._sec_list.count()):
            it  = self._sec_list.item(i)
            sid = it.data(Qt.ItemDataRole.UserRole)
            s   = next((x for x in self._cfg.sections if x.id == sid), None)
            if s:
                s.order = i

    def _auto_save(self) -> None:
        self._sync_current_to_cfg()
        self._do_save()

    def _manual_save(self) -> None:
        self._timer.stop()
        self._sync_current_to_cfg()
        self._do_save()

    def _do_save(self) -> None:
        if self._cfg is None:
            return
        self._draft_dirty = True
        from sme_gend.services.auth_guard import session_has_permission
        if not session_has_permission('effectifs.layout'):
            self._timer.stop()
            self._set_status('Accès refusé — droit de modification de la mise en page requis.', 'error')
            return False
        try:
            self._svc.save(self._cfg)
        except Exception as exc:
            self._timer.stop()
            self._set_status(f"❌ Non enregistré — brouillon conservé : {exc}", "error")
            return False
        self._draft_dirty = False
        sel = self._current_id()
        self._populate(select_id=sel)
        self._set_status("✓ Enregistré", "success")
        self._on_saved()
        return True

    def _set_status(self, msg: str, color: str = "") -> None:
        fg = status_semantic_color(color) if color in ("muted", "success", "warning", "error", "info") else color
        self._status_lbl.setStyleSheet(
            f"color:{fg}; font-size:11px;" if fg else "font-size:11px;"
        )
        self._status_lbl.setText(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Vue principale
# ─────────────────────────────────────────────────────────────────────────────

class ListePresentView(QWidget):
    def has_unsaved_changes(self) -> bool:
        panel = getattr(self, "_panel", None)
        return panel is not None and panel.has_unsaved_changes()

    def __init__(
        self,
        service: ListePresentService,
        layout_cfg: LayoutConfigService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("listePresentView")
        self._service    = service
        self._layout_cfg = layout_cfg
        self._open_layout: Callable[[], None] | None = None
        self._loaded = False

        # MySQL reads/rendering run in a retained worker; Qt receives only results.
        self._refresh_busy: bool = False
        self._refresh_token: int = 0
        self._refresh_pending: bool = False
        self._preview_worker: _ListePresentPreviewWorker | None = None
        self._last_html: str = ""
        self._preview_snapshot = None
        self._preview_generation = 0
        self._render_generation = 0
        self._auto_refresh_requested = False
        self._zoom_factor: float = 1.0
        self._panel: _QuickEditPanel | None = None

        # ── Aperçu HTML (QTextBrowser : pas de Qt WebEngine, affichage fiable sous Windows)
        self._preview = QTextBrowser()
        self._preview.setObjectName("listePresentPreview")
        self._preview.setOpenLinks(False)
        self._preview.setOpenExternalLinks(False)
        self._preview.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._refresh_progress = QProgressBar()
        self._refresh_progress.setRange(0, 100)
        self._refresh_progress.setTextVisible(True)
        self._refresh_progress.setFormat("Chargement : %p %%")
        self._refresh_progress.setVisible(False)

        # ── Toolbar ──────────────────────────────────────────────────────────
        self._btn_refresh = QPushButton("↺  Actualiser")
        self._btn_refresh.setObjectName("listePresentRefreshBtn")
        self._btn_refresh.setDefault(True)
        self._btn_refresh.setToolTip(
            "Affiche uniquement les agents dont la position administrative est Présent (F5). "
            "À utiliser après ouverture de l'écran ou changement de mois."
        )
        self._btn_refresh.clicked.connect(self._do_refresh)
        # F5 is owned by ShellWindow, shared with the status-bar refresh action.

        self._btn_toggle = QPushButton("⚙  Personnaliser ▶")
        self._btn_toggle.setCheckable(True)
        self._btn_toggle.setToolTip('Personnaliser la mise en page de la liste')
        self._btn_toggle.setVisible(layout_cfg is not None)
        self._btn_toggle.toggled.connect(self._on_toggle_panel)

        self._btn_pdf = QPushButton("🖨  PDF")
        self._btn_pdf.setToolTip("Exporter la liste détaillée correspondant aux filtres de l’aperçu (Ctrl+P)")
        self._btn_pdf.clicked.connect(self._export_pdf)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._export_pdf)

        self._btn_export = QPushButton("📥  Excel")
        self._btn_export.setToolTip("Exporter vers un fichier Excel (.xlsx)")
        self._btn_export.clicked.connect(self._export_excel)

        self._btn_layout = QPushButton("⚙  Éditeur avancé…")
        self._btn_layout.setToolTip(
            "Ouvre l'éditeur complet : renommer en base, rechercher/remplacer, "
            "configurer toutes les sections."
        )
        self._btn_layout.clicked.connect(self._on_open_layout)
        self._btn_layout.setVisible(False)

        self._btn_export_menu = QToolButton()
        self._btn_export_menu.setText("Exporter ▾")
        self._btn_export_menu.setToolTip("PDF, Excel, éditeur avancé de la mise en page")
        self._btn_export_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self)
        export_menu.addAction("🖨  PDF (Ctrl+P)", self._export_pdf)
        export_menu.addAction("📥  Excel", self._export_excel)
        export_menu.addSeparator()
        self._act_export_layout = export_menu.addAction("⚙  Éditeur avancé…", self._on_open_layout)
        self._act_export_layout.setVisible(False)
        self._btn_export_menu.setMenu(export_menu)

        # Zoom
        self._btn_zoom_out = QPushButton("−")
        self._btn_zoom_out.setFixedWidth(28)
        self._btn_zoom_out.setToolTip("Réduire (Ctrl+−)")
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)

        self._lbl_zoom = QLabel("100%")
        self._lbl_zoom.setFixedWidth(42)
        self._lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_zoom.setToolTip("Cliquez pour revenir à 100%")
        self._lbl_zoom.mousePressEvent = lambda _: self._zoom_reset()
        self._lbl_zoom.setCursor(Qt.CursorShape.PointingHandCursor)

        self._btn_zoom_in = QPushButton("+")
        self._btn_zoom_in.setFixedWidth(28)
        self._btn_zoom_in.setToolTip("Agrandir (Ctrl++)")
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)

        # Recherche
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrer (nom, grade, matricule, structure…)")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(450)
        self._search_timer.timeout.connect(self._refresh_if_loaded)
        self._search.textChanged.connect(self._on_search_changed)

        # Status
        self._status_lbl = QLabel()
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_lbl.setMinimumWidth(150)
        self._status_lbl.setStyleSheet("font-size:11px;")

        def _vsep() -> QFrame:
            s = QFrame()
            s.setFrameShape(QFrame.Shape.VLine)
            s.setFrameShadow(QFrame.Shadow.Sunken)
            return s

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(self._btn_refresh)
        toolbar.addWidget(self._btn_toggle)
        toolbar.addWidget(_vsep())
        toolbar.addWidget(self._btn_pdf)
        toolbar.addWidget(self._btn_export)
        toolbar.addWidget(self._btn_layout)
        toolbar.addWidget(self._btn_export_menu)
        toolbar.addWidget(_vsep())
        toolbar.addWidget(self._btn_zoom_out)
        toolbar.addWidget(self._lbl_zoom)
        toolbar.addWidget(self._btn_zoom_in)
        toolbar.addWidget(_vsep())
        self._search_label = QLabel("Recherche :")
        toolbar.addWidget(self._search_label)
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._status_lbl)
        self._toolbar_layout = toolbar

        # ── Zone de contenu (panneau gauche + aperçu HTML droite) ────────────
        self._content_row = QHBoxLayout()
        self._content_row.setSpacing(0)
        self._content_row.setContentsMargins(0, 0, 0, 0)
        self._content_row.addWidget(self._preview, stretch=1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(toolbar)
        lay.addWidget(self._refresh_progress)
        lay.addLayout(self._content_row, stretch=1)
        self._apply_preview_font()
        self._apply_preview_chrome()
        self._show_idle_state()
        self._update_toolbar_density()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_toolbar_layout'):
            self._update_toolbar_density()

    def _update_toolbar_density(self):
        compact = self.width() < 1100
        self._btn_pdf.setVisible(not compact)
        self._btn_export.setVisible(not compact)
        self._btn_layout.setVisible(not compact and self._open_layout is not None)
        self._btn_export_menu.setVisible(compact)
        self._act_export_layout.setVisible(self._open_layout is not None)
        self._search_label.setVisible(not compact)
        self._search.setMinimumWidth(140 if compact else 220)
        self._status_lbl.setMinimumWidth(120 if compact else 150)
        self._status_lbl.setMaximumWidth(120 if compact else 220)
        self._btn_toggle.setMaximumWidth(44 if compact else 16777215)
        self._btn_toggle.setText('⚙' if compact else
            ('⚙  Personnaliser ◀' if self._btn_toggle.isChecked() else '⚙  Personnaliser ▶'))
        message = self._status_lbl.toolTip()
        if message:
            self._status_lbl.setText(message.split(' — ')[0] if compact else message)

    def refresh_theme_styles(self) -> None:
        """Rafraîchit l'aperçu papier et le panneau latéral après changement de thème."""
        self._apply_preview_chrome()
        if self._panel is not None:
            self._panel.refresh_theme_styles()
        if self._last_html:
            self._set_preview_html(self._last_html)

    def _apply_preview_chrome(self) -> None:
        apply_paper_preview_palette(self._preview)

    def _set_preview_html(self, html_content: str) -> None:
        self._display_html_source = html_content
        self._apply_preview_font()
        self._apply_preview_chrome()

    # ── Recherche / actualisation ─────────────────────────────────────────────

    def _on_search_changed(self) -> None:
        refresh = self._loaded or self._refresh_busy or getattr(self, "_auto_refresh_requested", False)
        self._preview_generation = getattr(self, "_preview_generation", 0) + 1
        self._loaded = False
        self._last_html = ""
        self._preview_snapshot = None
        self._auto_refresh_requested = refresh
        if refresh:
            self._search_timer.start()
            if self._refresh_busy:
                self._refresh_pending = True

    def _refresh_if_loaded(self) -> None:
        if self._loaded or self._auto_refresh_requested:
            self._auto_refresh_requested = False
            self._do_refresh(skip_confirm=True)

    def _show_idle_state(self) -> None:
        self._loaded = False
        self._last_html = ""
        self._set_preview_html(_idle_page())
        self._set_status("En attente — cliquez Actualiser", "muted")

    def _on_layout_panel_saved(self) -> None:
        refresh = self._loaded or self._refresh_busy
        self._preview_generation += 1
        self._loaded = False
        self._last_html = ""
        self._preview_snapshot = None
        if refresh:
            self._do_refresh(skip_confirm=True)
        else:
            self._set_status("Mise en page enregistrée — cliquez Actualiser", "info")

    # ── Événements ───────────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)

    # ── API publique ──────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        """Invalide l'aperçu : regénération manuelle via Actualiser."""
        self._search_timer.stop()
        self._auto_refresh_requested = False
        self._refresh_pending = False
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.requestInterruption()
        self._preview_generation = getattr(self, "_preview_generation", 0) + 1
        self._preview_snapshot = None
        self._last_html = ""
        self._loaded = False
        if self.isVisible():
            self._show_idle_state()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        """Ouverture de l'onglet : proposition de chargement avec avertissement."""
        if not self._loaded and not self._refresh_busy:
            self._prompt_load_on_open()

    def _prompt_load_on_open(self) -> None:
        if getattr(self, "_load_prompt_open", False):
            return
        self._load_prompt_open = True
        try:
            accepted = confirm_yes_no(
                self,
                "Charger la liste des présents",
                "Voulez-vous charger la liste des présents maintenant ?\n\n"
                "Le chargement peut prendre quelques secondes selon l'effectif du mois.\n"
                "Si vous choisissez Non, vous pourrez lancer le chargement avec Actualiser.",
                default_no=True,
            )
            if accepted:
                self._do_refresh(skip_confirm=True)
            else:
                self._show_idle_state()
        finally:
            self._load_prompt_open = False

    def apply_runtime_after_config_change(self) -> None:
        """Appelé quand la config ou la source de données change."""
        if self._panel is not None:
            self._panel.reload_from_disk()
        self.mark_dirty()

    def set_open_layout_editor(self, fn: Callable[[], None] | None) -> None:
        self._open_layout = fn
        self._update_toolbar_density()

    # ── Bascule panneau ───────────────────────────────────────────────────────

    def _ensure_panel(self) -> _QuickEditPanel | None:
        if self._panel is not None:
            return self._panel
        if self._layout_cfg is None:
            return None
        try:
            panel = _QuickEditPanel(
                self._layout_cfg,
                self._on_layout_panel_saved,
                parent=self,
            )
            panel.setVisible(False)
            self._content_row.insertWidget(0, panel)
            self._panel = panel
        except Exception as exc:
            self._panel = None
            QMessageBox.critical(self, "Personnaliser la liste", f"Ouverture du panneau impossible : {exc}")
        return self._panel

    def _on_toggle_panel(self, checked: bool) -> None:
        if checked:
            from sme_gend.services.auth_guard import require_permission
            if not require_permission(self, 'effectifs.layout'):
                self._btn_toggle.setChecked(False)
                return
        panel = self._ensure_panel() if checked else self._panel
        if panel is None:
            return
        panel.setVisible(checked)
        if checked:
            panel._ensure_data_caches()
        self._update_toolbar_density()

    def _on_open_layout(self) -> None:
        if self._open_layout:
            from sme_gend.services.auth_guard import require_permission
            if require_permission(self, 'effectifs.layout'):
                self._open_layout()

    # ── Actualisation (MySQL : thread secondaire ; HTML appliqué sur le thread UI) ──

    def _do_refresh(self, *, skip_confirm: bool = False) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, 'effectifs.presents'):
            self.mark_dirty()
            self._set_status('Accès refusé — droit de consultation des présents requis.', 'error')
            return
        if self._refresh_busy:
            self._refresh_pending = True
            return

        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.requestInterruption()

        self._refresh_token += 1
        token = self._refresh_token
        self._render_generation = self._preview_generation
        self._loaded = False
        self._last_html = ""
        self._preview_snapshot = None
        needle = self._search.text().strip() or None

        self._set_status("⏳ Chargement…", "muted")
        self._btn_refresh.setEnabled(False)
        self._refresh_progress.setVisible(True)
        self._refresh_progress.setValue(0)
        self._set_preview_html(_minimal_refresh_page())
        self._refresh_busy = True

        worker = _ListePresentPreviewWorker(
            self._service,
            needle,
            token,
            parent=self,
        )
        from sme_gend.ui.bg_task import start_managed_thread

        self._preview_worker = worker
        outcome = {}
        def finished():
            self._finish_refresh(outcome.get('html', ''), outcome.get('error') if outcome
                else RuntimeError('Le chargement a été interrompu. Actualisez la liste.'), token)
        start_managed_thread(worker, self, signals=(
            (worker.progress, lambda p, t=token: self._refresh_progress.setValue(p)
                if t == self._refresh_token else None),
            (worker.succeeded, lambda html: outcome.update(html=html)),
            (worker.failed, lambda err: outcome.update(error=err)),
        ), on_finished=finished)

    def refresh_from_database(self) -> None:
        self._do_refresh(skip_confirm=True)

    def _finish_refresh(
        self,
        html: str,
        err: BaseException | str | None,
        token: int,
    ) -> None:
        if token != self._refresh_token:
            return
        self._refresh_busy = False
        self._refresh_progress.setVisible(False)
        self._btn_refresh.setEnabled(True)
        from sme_gend.services.auth_guard import session_has_permission
        if not session_has_permission('effectifs.presents'):
            self._loaded = False
            self._last_html = ''
            self._preview_snapshot = None
            self._refresh_pending = False
            self._preview_worker = None
            self._set_preview_html(_idle_page())
            self._set_status('Accès refusé — droit de consultation des présents requis.', 'error')
            return
        if getattr(self, '_render_generation', 0) == getattr(self, '_preview_generation', 0):
            if err is not None:
                self._on_refresh_error(str(err), token)
            else:
                self._refresh_progress.setValue(100)
                self._on_refresh_done(html, token)
        self._preview_worker = None
        if self._refresh_pending:
            self._refresh_pending = False
            QTimer.singleShot(0, lambda: self._do_refresh(skip_confirm=True))

    def _on_refresh_done(self, html_content: str, token: int) -> None:
        if token != self._refresh_token:
            return
        if getattr(self, "_render_generation", 0) != getattr(self, "_preview_generation", 0):
            return
        self._preview_snapshot = getattr(getattr(self, "_preview_worker", None), "_export_snapshot", None)
        self._last_html = html_content
        self._set_preview_html(html_content)
        self._loaded = True
        self._set_status("✓ Mis à jour", "success")

    def _on_refresh_error(self, err: str, token: int) -> None:
        if token != self._refresh_token:
            return
        err_html = _error_page(err)
        self._loaded = False
        self._preview_snapshot = None
        self._last_html = ""
        self._set_preview_html(err_html)
        self._set_status("❌ Erreur", "error")

    def _set_status(self, msg: str, color: str = "") -> None:
        lbl = getattr(self, "_status_lbl", None)
        if lbl is None:
            return
        fg = status_semantic_color(color) if color in ("muted", "success", "warning", "error", "info") else color
        lbl.setStyleSheet(
            f"color:{fg};font-size:11px;" if fg else "font-size:11px;"
        )
        lbl.setText(msg.split(' — ')[0] if self.width()<1100 else msg)
        lbl.setToolTip(msg)

    def _apply_preview_font(self) -> None:
        """Zoom = taille de police (QTextBrowser n’a pas zoomFactor comme WebEngine)."""
        f = QFont(self._preview.font())
        f.setFamily("Calibri")
        f.setPointSizeF(10.0 * self._zoom_factor)
        self._preview.setFont(f)
        source = getattr(self, '_display_html_source', None)
        if source is not None:
            vertical, horizontal = self._preview.verticalScrollBar(), self._preview.horizontalScrollBar()
            relative_y = vertical.value()/max(1, vertical.maximum())
            relative_x = horizontal.value()/max(1, horizontal.maximum())
            self._preview.setHtml(_zoomed_preview_html(source, self._zoom_factor))
            vertical.setValue(round(relative_y*vertical.maximum()))
            horizontal.setValue(round(relative_x*horizontal.maximum()))

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def _zoom_in(self) -> None:
        z = self._zoom_factor
        for step in _ZOOM_STEPS:
            if step > z + 0.01:
                self._zoom_factor = step
                self._apply_preview_font()
                self._update_zoom_lbl()
                return

    def _zoom_out(self) -> None:
        z = self._zoom_factor
        for step in reversed(_ZOOM_STEPS):
            if step < z - 0.01:
                self._zoom_factor = step
                self._apply_preview_font()
                self._update_zoom_lbl()
                return

    def _zoom_reset(self) -> None:
        self._zoom_factor = 1.0
        self._apply_preview_font()
        self._update_zoom_lbl()

    def _update_zoom_lbl(self) -> None:
        self._lbl_zoom.setText(f"{round(self._zoom_factor * 100)}%")

    # ── Export PDF ────────────────────────────────────────────────────────────

    def _export_snapshot_current(self, snapshot):
        return self._loaded and not self._refresh_busy and self._preview_snapshot is snapshot

    def _export_pdf(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        if not self._loaded or self._refresh_busy:
            QMessageBox.information(self, "PDF", "Générez d'abord l'aperçu (Actualiser).")
            return
        html_content = self._last_html
        snapshot = self._preview_snapshot
        if snapshot is None:
            QMessageBox.information(self, "PDF", "Actualisez d’abord l’aperçu.")
            return
        count = sum(len(people) for _, _, _, blocks in snapshot.sections for _, people in blocks)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer en PDF",
            str(Path.home() / "liste_presents.pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        if not confirm_overwrite_file(self, path):
            return
        if not require_permission(self, 'effectifs.export'):
            return
        if not ListePresentView._export_snapshot_current(self, snapshot):
            QMessageBox.information(self, 'PDF', 'Le contexte a changé. Actualisez la liste avant de l’exporter.')
            return
        self._set_status("⏳ Génération PDF…", "muted")
        try:
            # Import tardif : sur certaines installs / bundles, QtPrintSupport peut
            # manquer ou échouer au chargement natif — ne pas bloquer le démarrage de l’app.
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrinter
        except (ImportError, OSError) as exc:
            self._set_status("❌ PDF indisponible", "error")
            QMessageBox.critical(
                self,
                "Export PDF",
                "Le module d’impression Qt (PrintSupport) n’est pas disponible.\n"
                "Réinstallez PySide6 complet ou utilisez l’export Excel.\n\n"
                f"Détail : {exc}",
            )
            return
        try:
            self._service.validate_snapshot(snapshot, search_filter=snapshot.search,
                                            presences_mode=_PRESENCES_MODE)
            html_content = run_with_progress(
                self, "PDF détaillé", "Préparation de la liste complète…",
                lambda: self._service.render_snapshot_html(snapshot, detailed=True, for_print=True))
            if html_content is None:
                return
            if not require_permission(self, 'effectifs.export'):
                return
            if not ListePresentView._export_snapshot_current(self, snapshot):
                QMessageBox.information(self, 'PDF', 'Le contexte a changé. Actualisez la liste avant de l’exporter.')
                return
            self._service.validate_snapshot(snapshot, search_filter=snapshot.search,
                                            presences_mode=_PRESENCES_MODE)
            doc = QTextDocument()
            doc.setHtml(html_content)
            from sme_gend.services.liste_generale_print import sync_document_to_printer
            from sme_gend.services.document_pdf_output import save_document_pdf
            save_document_pdf(doc, path, configure=sync_document_to_printer,
                doc_type="liste_present", count=count, settings=snapshot.settings, mois=snapshot.month,
                persons=[f"{r.id} — {r.noms or ''}" for _, _, _, blocks in snapshot.sections for _, people in blocks for r in people],
                meta={"personnel_ids": [str(r.id) for _, _, _, blocks in snapshot.sections for _, people in blocks for r in people], "contenu": "liste détaillée"})
            self._set_status("✓ PDF enregistré", "success")
            QMessageBox.information(self, "PDF", f"Fichier enregistré :\n{path}")
        except Exception as exc:  # noqa: BLE001
            self._set_status("❌ Échec PDF", "error")
            QMessageBox.critical(self, "PDF", str(exc))

    # ── Export Excel ──────────────────────────────────────────────────────────

    def _export_excel(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from copy import deepcopy
        source_snapshot = self._preview_snapshot
        snapshot = deepcopy(source_snapshot)
        if not self._loaded or self._refresh_busy or snapshot is None:
            QMessageBox.information(self, "Export Excel", "Actualisez d’abord l’aperçu avec les filtres souhaités.")
            return
        service = self._service
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la liste des présents",
            str(Path.home() / "liste_presents_sme.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        if not confirm_overwrite_file(self, path):
            return
        if not require_permission(self, 'effectifs.export'):
            return
        if not ListePresentView._export_snapshot_current(self, source_snapshot):
            QMessageBox.information(self, 'Export Excel', 'Le contexte a changé. Actualisez la liste avant de l’exporter.')
            return
        needle = snapshot.search or None
        dest   = Path(path)

        def _run() -> bool:
            service.export_excel(
                dest, search_filter=needle, presences_mode=_PRESENCES_MODE, snapshot=snapshot
            )
            return True

        self._set_status("⏳ Export Excel…", "muted")
        ok = run_with_progress(self, "Export Excel", "Génération du classeur…", _run)
        if ok is not None:
            self._set_status("✓ Excel enregistré", "success")
            QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
        else:
            self._set_status("❌ Erreur export", "error")


class _PreviewInterrupted(Exception):
    """Levée depuis le callback on_progress pour interrompre un aperçu superflu."""


class _ListePresentPreviewWorker(QThread):
    progress = Signal(int)
    succeeded = Signal(str)
    failed = Signal(object)

    def __init__(
        self,
        service: ListePresentService,
        search: str | None,
        token: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = copy(service) if isinstance(service, ListePresentService) else service
        if isinstance(service, ListePresentService):
            self._service._layout_config = copy(service._layout_config)
        self._search = search
        self._token = token
        self._export_snapshot = None

    def run(self) -> None:
        def _on_progress(p: int) -> None:
            # Vérifié à chaque étape de build_preview_html : sans ça,
            # requestInterruption() dans _do_refresh() était un no-op — le thread
            # précédent continuait sa requête MySQL jusqu'au bout en arrière-plan
            # (et bloquait le thread UI jusqu'à 200 ms sur wait()) même après le
            # lancement d'un nouveau rafraîchissement.
            if self.isInterruptionRequested():
                raise _PreviewInterrupted()
            self.progress.emit(min(99, p))

        try:
            options = {"on_progress": _on_progress}
            if isinstance(self._service, ListePresentService):
                options["on_snapshot"] = lambda snapshot: setattr(self, "_export_snapshot", snapshot)
            html = self._service.build_preview_html(self._search, _PRESENCES_MODE, **options)
        except _PreviewInterrupted:
            return
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(exc)
        else:
            self.succeeded.emit(html)
