"""Liste des présents — aperçu HTML configurable + personnalisation intégrée."""

from __future__ import annotations

import html as html_lib
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.sme_context import SmeContext, load_sme_context, save_sme_context
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no


# ─── Zoom steps ────────────────────────────────────────────────────────────────
_ZOOM_STEPS = [0.5, 0.67, 0.75, 0.80, 0.90, 1.0, 1.10, 1.25, 1.50, 1.75, 2.0, 2.50, 3.0]

# Modes disponibles pour le filtre Padmin (défaut métier : présents uniquement)
_PRESENCES_MODES: list[tuple[str, str]] = [
    ("present",       "Présents uniquement"),
    ("les_deux",      "Présents + Stage interne"),
    ("stage_interne", "Stage interne uniquement"),
]


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
        self._svc = layout_svc
        self._on_saved = on_saved
        self._cfg: ListeLayoutConfig | None = None
        self._loading = False
        self._desig_cache: dict[str, str] = {}
        self._count_cache: dict[str, int] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._auto_save)

        self._build_ui()
        self._safe_reload()

    # ── API publique ──────────────────────────────────────────────────────────

    def reload_from_disk(self) -> None:
        """Resynchronise depuis le disque."""
        self._safe_reload(preserve_selection=True)

    def _safe_reload(self, preserve_selection: bool = False) -> None:
        sel_id = self._current_id() if preserve_selection else None
        try:
            self._cfg = self._svc.load()
        except Exception as exc:
            self._set_status(f"❌ Erreur chargement : {exc}", "#b91c1c")
            return
        self._reload_caches()
        self._populate(select_id=sel_id)

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setMinimumWidth(270)
        self.setMaximumWidth(320)

        lbl_sec = QLabel("<b>Sections</b>")
        lbl_sec.setTextFormat(Qt.TextFormat.RichText)

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
        self._lbl_code.setStyleSheet("font-weight:bold; color:#1e3a5f;")
        self._le_titre1 = QLineEdit()
        self._le_titre1.setPlaceholderText("(désignation TabStructure)")
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
        self._sub_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._sub_list.model().rowsMoved.connect(self._on_sub_dragged)
        self._sub_list.itemDoubleClicked.connect(self._rename_sub_inline)
        self._sub_list.setToolTip("Double-cliquez sur une subdivision pour la renommer en base.")

        self._btn_sub_up   = self._mkbtn("↑", "Monter (Ctrl+↑)")
        self._btn_sub_dn   = self._mkbtn("↓", "Descendre (Ctrl+↓)")
        self._btn_fill_sub = QPushButton("Remplir depuis la base")
        self._btn_fill_sub.setToolTip("Importe les SubArme distincts depuis la base de données.")
        self._btn_sub_up.clicked.connect(lambda: self._move_sub(-1))
        self._btn_sub_dn.clicked.connect(lambda: self._move_sub(1))
        self._btn_fill_sub.clicked.connect(self._fill_sub_from_db)

        QShortcut(QKeySequence("Ctrl+Up"),   self._sub_list).activated.connect(lambda: self._move_sub(-1))
        QShortcut(QKeySequence("Ctrl+Down"), self._sub_list).activated.connect(lambda: self._move_sub(1))

        sub_btns = QHBoxLayout()
        sub_btns.addWidget(self._btn_sub_up)
        sub_btns.addWidget(self._btn_sub_dn)
        sub_btns.addWidget(self._btn_fill_sub)
        sub_btns.addStretch()

        self._sub_grp = QGroupBox("Subdivisions d'arme  (double-clic = renommer)")
        sub_lay = QVBoxLayout(self._sub_grp)
        sub_lay.setContentsMargins(4, 4, 4, 4)
        sub_lay.addWidget(self._sub_list, stretch=1)
        sub_lay.addLayout(sub_btns)

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

    # ── Caches ───────────────────────────────────────────────────────────────

    def _reload_caches(self) -> None:
        try:
            self._desig_cache = self._svc.all_structure_designations()
        except Exception:
            self._desig_cache = {}
        try:
            self._count_cache = self._svc.present_counts_by_structure()
        except Exception:
            self._count_cache = {}

    # ── Population ────────────────────────────────────────────────────────────

    def _sec_label(self, sec: SectionBlock, pos: int) -> str:
        code = sec.structure_code
        des  = self._desig_cache.get(code, "")
        n    = self._count_cache.get(code, 0)
        lbl  = f"{pos + 1:02d}  {code}"
        if des and des.strip().casefold() != code.strip().casefold():
            lbl += f" — {des}"
        if n:
            lbl += f"  [{n}]"
        return lbl

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
            self._sub_list.clear()
            for lbl in (sec.subdivision_order or []):
                s = (lbl or "").strip()
                if s:
                    self._sub_list.addItem(QListWidgetItem(s))
        else:
            self._lbl_code.clear()
            self._le_titre1.clear()
            self._le_titre2.clear()
            self._sub_list.clear()
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

    def _fill_sub_from_db(self) -> None:
        sec = self._current_sec()
        if not sec:
            return
        try:
            db_labels = self._svc.subarme_labels_for_structure(sec.structure_code)
        except Exception as exc:
            QMessageBox.critical(self, "Base de données", str(exc))
            return
        existing = {self._sub_list.item(i).text().strip() for i in range(self._sub_list.count())}
        added = 0
        for lbl in db_labels:
            s = (lbl or "").strip()
            if s and s not in existing:
                self._sub_list.addItem(QListWidgetItem(s))
                existing.add(s)
                added += 1
        self._sync_sub_to_sec()
        self._trigger()
        if added:
            self._set_status(f"✓ {added} subdivision(s) ajoutée(s)", "#15803d")

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
            n, self._cfg = self._svc.rename_subarme_in_database(
                self._cfg, sec.structure_code, old_name, new_name
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur base de données", str(exc))
            return
        item.setText(new_name)
        self._sync_sub_to_sec()
        self._set_status(f"✓ Renommé — {n} fiche(s) mise(s) à jour", "#15803d")
        self._do_save()

    # ── Signaux champs ────────────────────────────────────────────────────────

    def _on_field_changed(self) -> None:
        if self._loading:
            return
        self._trigger()

    def _on_chk_changed(self) -> None:
        if self._loading:
            return
        self._sub_grp.setEnabled(self._chk_sub.isChecked())
        self._trigger()

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _trigger(self) -> None:
        self._set_status("● Modification en attente…", "#b45309")
        self._timer.start()

    def _sync_sub_to_sec(self) -> None:
        sec = self._current_sec()
        if not sec:
            return
        labels = [
            self._sub_list.item(i).text().strip()
            for i in range(self._sub_list.count())
            if self._sub_list.item(i) and self._sub_list.item(i).text().strip()
        ]
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
        try:
            self._svc.save(self._cfg)
        except OSError as exc:
            self._set_status(f"❌ Erreur : {exc}", "#b91c1c")
            return
        sel = self._current_id()
        self._populate(select_id=sel)
        self._set_status("✓ Enregistré", "#15803d")
        self._on_saved()

    def _set_status(self, msg: str, color: str = "") -> None:
        self._status_lbl.setStyleSheet(f"color:{color};" if color else "")
        self._status_lbl.setText(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Vue principale
# ─────────────────────────────────────────────────────────────────────────────

class ListePresentView(QWidget):
    def __init__(
        self,
        service: ListePresentService,
        layout_cfg: LayoutConfigService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service    = service
        self._layout_cfg = layout_cfg
        self._open_layout: Callable[[], None] | None = None
        self._loaded = False

        # Contexte persisté (mode Padmin)
        self._ctx: SmeContext = load_sme_context()

        # Actualisation sur le thread UI uniquement : le pilote ODBC Access/Jet
        # échoue souvent hors thread principal (ex. « The driver did not supply an error! »).
        self._refresh_busy: bool = False
        self._refresh_token: int = 0
        self._refresh_pending: bool = False
        self._last_html: str = ""
        self._zoom_factor: float = 1.0

        # ── Panneau de personnalisation ──────────────────────────────────────
        self._panel: _QuickEditPanel | None = None
        if layout_cfg is not None:
            try:
                self._panel = _QuickEditPanel(layout_cfg, self._do_refresh, parent=self)
            except Exception:
                self._panel = None

        # ── Aperçu HTML (QTextBrowser : pas de Qt WebEngine, affichage fiable sous Windows)
        self._preview = QTextBrowser()
        self._preview.setOpenLinks(False)
        self._preview.setOpenExternalLinks(False)
        self._preview.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._apply_preview_font()
        self._preview.setHtml(_minimal_refresh_page())

        self._refresh_progress = QProgressBar()
        self._refresh_progress.setRange(0, 100)
        self._refresh_progress.setTextVisible(True)
        self._refresh_progress.setFormat("Chargement : %p %%")
        self._refresh_progress.setVisible(False)

        # ── Sélecteur de mode Padmin ─────────────────────────────────────────
        self._mode_combo = QComboBox()
        self._mode_combo.setToolTip(
            "Choisir quelles positions administratives afficher dans la liste"
        )
        for key, label in _PRESENCES_MODES:
            self._mode_combo.addItem(label, key)
        # Initialiser sur la valeur persistée
        self._set_combo_mode(self._ctx.presences_mode)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        # ── Toolbar ──────────────────────────────────────────────────────────
        self._btn_refresh = QPushButton("↺  Actualiser")
        self._btn_refresh.setToolTip("Régénère l'aperçu depuis la base de données (F5)")
        self._btn_refresh.clicked.connect(self._do_refresh)
        QShortcut(QKeySequence("F5"), self).activated.connect(self._do_refresh)

        self._btn_toggle = QPushButton("⚙  Personnaliser ▶")
        self._btn_toggle.setCheckable(True)
        self._btn_toggle.setVisible(self._panel is not None)
        self._btn_toggle.toggled.connect(self._on_toggle_panel)

        self._btn_pdf = QPushButton("🖨  PDF")
        self._btn_pdf.setToolTip("Exporter l'aperçu courant en PDF (Ctrl+P)")
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
        self._search_timer.timeout.connect(self._do_refresh)
        self._search.textChanged.connect(lambda: self._search_timer.start())

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
        toolbar.addWidget(QLabel("Afficher :"))
        toolbar.addWidget(self._mode_combo)
        toolbar.addWidget(_vsep())
        toolbar.addWidget(self._btn_pdf)
        toolbar.addWidget(self._btn_export)
        toolbar.addWidget(self._btn_layout)
        toolbar.addWidget(_vsep())
        toolbar.addWidget(self._btn_zoom_out)
        toolbar.addWidget(self._lbl_zoom)
        toolbar.addWidget(self._btn_zoom_in)
        toolbar.addWidget(_vsep())
        toolbar.addWidget(QLabel("Recherche :"))
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._status_lbl)

        # ── Zone de contenu (panneau gauche + aperçu HTML droite) ────────────
        self._content_row = QHBoxLayout()
        self._content_row.setSpacing(0)
        self._content_row.setContentsMargins(0, 0, 0, 0)
        if self._panel is not None:
            self._panel.setVisible(False)
            self._content_row.addWidget(self._panel)
        self._content_row.addWidget(self._preview, stretch=1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(toolbar)
        lay.addWidget(self._refresh_progress)
        lay.addLayout(self._content_row, stretch=1)

    # ── Helpers mode ──────────────────────────────────────────────────────────

    def _set_combo_mode(self, mode: str) -> None:
        """Sélectionne le bon item dans la combobox sans déclencher de signal."""
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode:
                self._mode_combo.blockSignals(True)
                self._mode_combo.setCurrentIndex(i)
                self._mode_combo.blockSignals(False)
                return

    def _current_mode(self) -> str:
        data = self._mode_combo.currentData()
        return data if isinstance(data, str) else "present"

    def _on_mode_changed(self) -> None:
        """Persiste le nouveau mode et rafraîchit."""
        mode = self._current_mode()
        self._ctx.presences_mode = mode
        try:
            save_sme_context(self._ctx)
        except Exception:
            pass
        self._do_refresh()

    # ── Événements ───────────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loaded:
            self._do_refresh()

    # ── API publique ──────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        """Force un rechargement au prochain affichage."""
        self._loaded = False

    def apply_runtime_after_config_change(self) -> None:
        """Appelé quand la config ou la source de données change."""
        self._ctx = load_sme_context()
        self._set_combo_mode(self._ctx.presences_mode)
        if self._panel is not None:
            self._panel.reload_from_disk()
        self._loaded = False
        if self.isVisible():
            self._do_refresh()

    def set_open_layout_editor(self, fn: Callable[[], None] | None) -> None:
        self._open_layout = fn
        self._btn_layout.setVisible(fn is not None)

    # ── Bascule panneau ───────────────────────────────────────────────────────

    def _on_toggle_panel(self, checked: bool) -> None:
        if self._panel is None:
            return
        self._panel.setVisible(checked)
        self._btn_toggle.setText("⚙  Personnaliser ◀" if checked else "⚙  Personnaliser ▶")

    def _on_open_layout(self) -> None:
        if self._open_layout:
            self._open_layout()

    # ── Actualisation (thread UI — ODBC Access) ───────────────────────────────

    def _do_refresh(self) -> None:
        """Lit la base et génère l’HTML sur le thread UI (requis par le pilote Access)."""
        if self._refresh_busy:
            self._refresh_pending = True
            return

        self._refresh_token += 1
        token = self._refresh_token

        needle = self._search.text().strip() or None
        mode   = self._current_mode()

        self._set_status("⏳ Chargement…", "#555")
        self._btn_refresh.setEnabled(False)
        self._refresh_progress.setVisible(True)
        self._refresh_progress.setValue(0)
        self._preview.setHtml(_minimal_refresh_page())

        self._refresh_busy = True
        try:

            def on_prog(p: int) -> None:
                if token != self._refresh_token:
                    return
                self._refresh_progress.setValue(p)

            html = self._service.build_preview_html(
                needle,
                mode,
                on_progress=on_prog,
            )
        except Exception as exc:  # noqa: BLE001
            self._on_refresh_error(str(exc), token)
        else:
            self._on_refresh_done(html, token)
        finally:
            self._refresh_busy = False
            self._refresh_progress.setVisible(False)
            self._btn_refresh.setEnabled(True)
            if self._refresh_pending:
                self._refresh_pending = False
                QTimer.singleShot(0, self._do_refresh)

    def _on_refresh_done(self, html_content: str, token: int) -> None:
        if token != self._refresh_token:
            return
        self._last_html = html_content
        self._preview.setHtml(html_content)
        self._apply_preview_font()
        self._loaded = True
        self._set_status("✓ Mis à jour", "#15803d")

    def _on_refresh_error(self, err: str, token: int) -> None:
        if token != self._refresh_token:
            return
        err_html = _error_page(err)
        self._last_html = err_html
        self._preview.setHtml(err_html)
        self._apply_preview_font()
        self._set_status("❌ Erreur", "#b91c1c")

    def _set_status(self, msg: str, color: str = "") -> None:
        self._status_lbl.setStyleSheet(f"color:{color};font-size:11px;" if color else "font-size:11px;")
        self._status_lbl.setText(msg)

    def _apply_preview_font(self) -> None:
        """Zoom = taille de police (QTextBrowser n’a pas zoomFactor comme WebEngine)."""
        f = QFont(self._preview.font())
        f.setFamily("Calibri")
        f.setPointSizeF(10.0 * self._zoom_factor)
        self._preview.setFont(f)

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

    def _export_pdf(self) -> None:
        if not self._loaded:
            QMessageBox.information(self, "PDF", "Générez d'abord l'aperçu (Actualiser).")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer en PDF",
            str(Path.home() / "liste_presents.pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return
        self._set_status("⏳ Génération PDF…", "#555")
        try:
            # Import tardif : sur certaines installs / bundles, QtPrintSupport peut
            # manquer ou échouer au chargement natif — ne pas bloquer le démarrage de l’app.
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrinter
        except (ImportError, OSError) as exc:
            self._set_status("❌ PDF indisponible", "#b91c1c")
            QMessageBox.critical(
                self,
                "Export PDF",
                "Le module d’impression Qt (PrintSupport) n’est pas disponible.\n"
                "Réinstallez PySide6 complet ou utilisez l’export Excel.\n\n"
                f"Détail : {exc}",
            )
            return
        try:
            doc = QTextDocument()
            doc.setHtml(self._last_html)
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            doc.print(printer)
            self._set_status("✓ PDF enregistré", "#15803d")
            QMessageBox.information(self, "PDF", f"Fichier enregistré :\n{path}")
        except Exception as exc:  # noqa: BLE001
            self._set_status("❌ Échec PDF", "#b91c1c")
            QMessageBox.critical(self, "PDF", str(exc))

    # ── Export Excel ──────────────────────────────────────────────────────────

    def _export_excel(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la liste des présents",
            str(Path.home() / "liste_presents_sme.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not confirm_overwrite_file(self, path):
            return
        needle = self._search.text().strip() or None
        mode   = self._current_mode()
        dest   = Path(path)

        def _run() -> bool:
            self._service.export_excel(dest, search_filter=needle, presences_mode=mode)
            return True

        self._set_status("⏳ Export Excel…", "#555")
        ok = run_with_progress(self, "Export Excel", "Génération du classeur…", _run)
        if ok is not None:
            self._set_status("✓ Excel enregistré", "#15803d")
            QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
        else:
            self._set_status("❌ Erreur export", "#b91c1c")
