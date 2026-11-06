"""Audit des actions sensibles — consultation admin avec recherche avancée."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
import json

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.ui.no_wheel_widgets import NoWheelComboBox, NoWheelDateEdit, NoWheelSpinBox

from sme_gend.services.sensitive_audit_log import (
    ACTION_LABELS_FR,
    SensitiveAuditQuery,
    format_action_label,
    format_entry_detail,
    entry_payload,
    validate_sensitive_audit_query,
    search_sensitive_audit,
)
from sme_gend.ui.expandable_help import ExpandableHelp
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.simple_table_model import SimpleRowTableModel
from sme_gend.ui.view_lifecycle import ViewDataState


def _fmt_ts(ts_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError, OSError, OverflowError):
        return ts_utc or "—"


_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)
_DATE_EMPTY = QDate(2000, 1, 1)


def _configure_optional_date(widget: NoWheelDateEdit) -> None:
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("dd/MM/yyyy")
    widget.setMinimumDate(_DATE_EMPTY)
    widget.setSpecialValueText("—")
    widget.setDate(_DATE_EMPTY)
    widget.setMinimumWidth(128)
    widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def _read_optional_date(widget: NoWheelDateEdit) -> date | None:
    picked = widget.date()
    if picked <= widget.minimumDate():
        return None
    return picked.toPython()


class SensitiveAuditLogView(QWidget):
    """Journal des modifications sensibles (identité, historique carrière)."""

    def __init__(self, parent: QWidget | None = None, *, global_activity=False) -> None:
        super().__init__(parent)
        self._global_activity = global_activity
        self._page_cursors = [None]
        self._page_snapshot = None
        self._next_cursor = None
        self._export_busy = False

        self._help = ExpandableHelp(
            ("Opérations enregistrées dans tous les modules du journal central : comptes, personnel, documents et opérations métier. "
             "Les anciennes actions non journalisées ne peuvent pas être reconstituées. Les consultations et clics ne sont pas tous enregistrés. "
             "<b>Accès soumis au droit de consultation de l’audit sensible.</b>" if global_activity else
             "Historique des modifications sensibles : identité, carrière, activités, absences et distinctions. "
             "<b>Accès soumis au droit de consultation de l’audit sensible.</b>"),
        )

        g_filters = QGroupBox("Recherche avancée")
        g_filters.setObjectName("auditFiltersGroup")
        filters_outer = QVBoxLayout(g_filters)
        filters_outer.setSpacing(12)
        fl = QGridLayout()
        fl.setHorizontalSpacing(16)
        fl.setVerticalSpacing(10)
        fl.setColumnStretch(1, 1)
        fl.setColumnStretch(3, 1)

        self._filter_action = NoWheelComboBox()
        self._filter_action.addItem("Toutes les actions", "")
        from sme_gend.services.activity_audit_log import OPERATION_LABELS
        labels = OPERATION_LABELS if global_activity else ACTION_LABELS_FR
        for key in sorted(labels):
            self._filter_action.addItem(labels[key], key)
        if global_activity:
            self._filter_action.setEditable(True)
            self._filter_action.setInsertPolicy(NoWheelComboBox.InsertPolicy.NoInsert)
            self._filter_action.setToolTip("Choisir une opération ou saisir son code exact ; recherche libre pour une recherche partielle.")

        self._filter_actor = QLineEdit()
        self._filter_actor.setPlaceholderText("Auteur (compte, nom…)")

        self._filter_personnel_id = QLineEdit()
        self._filter_personnel_id.setPlaceholderText("N° carrière exact / identifiant interne")

        self._filter_personnel_nom = QLineEdit()
        self._filter_personnel_nom.setPlaceholderText("Nom agent")

        self._combo_year = NoWheelComboBox()
        self._combo_year.setMinimumWidth(100)
        self._combo_year.setEditable(True)
        self._combo_year.setInsertPolicy(NoWheelComboBox.InsertPolicy.NoInsert)
        self._combo_year.lineEdit().setValidator(QIntValidator(1, 9999, self._combo_year))
        self._combo_month = NoWheelComboBox()
        self._combo_month.setMinimumWidth(140)
        self._fill_year_combo()
        self._combo_year.currentTextChanged.connect(self._on_year_changed)

        self._filter_date_from = NoWheelDateEdit()
        self._filter_date_to = NoWheelDateEdit()
        _configure_optional_date(self._filter_date_from)
        _configure_optional_date(self._filter_date_to)
        for field in (self._filter_date_from, self._filter_date_to):
            field.setToolTip("Date de l’action en heure locale, borne incluse. Indépendante du mois SME.")

        self._filter_text = QLineEdit()
        self._filter_text.setPlaceholderText("Auteur, agent, action, mois SME ou contenu…")
        self._filter_text.setClearButtonEnabled(True)

        self._filter_limit = NoWheelSpinBox()
        self._filter_limit.setRange(50, 2000)
        self._filter_limit.setValue(500)
        self._filter_limit.setSingleStep(50)
        self._filter_limit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        for combo in (self._filter_action, self._combo_year, self._combo_month):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def _add_row(row: int, label1: str, field1, label2: str, field2) -> None:
            align = Qt.AlignmentFlag.AlignVCenter
            lab1 = QLabel(label1)
            lab2 = QLabel(label2)
            fl.addWidget(lab1, row, 0, align)
            fl.addWidget(field1, row, 1, align)
            fl.addWidget(lab2, row, 2, align)
            fl.addWidget(field2, row, 3, align)

        _add_row(0, "Type d'action", self._filter_action, "Auteur", self._filter_actor)
        _add_row(1, "N° carrière (exact)", self._filter_personnel_id, "Nom agent", self._filter_personnel_nom)
        _add_row(2, "Année SME", self._combo_year, "Mois SME", self._combo_month)
        _add_row(3, "Du", self._filter_date_from, "Au", self._filter_date_to)
        _add_row(4, "Recherche libre", self._filter_text, "Limite", self._filter_limit)

        self._filter_module = NoWheelComboBox()
        self._filter_module.addItem("Tous les modules", "")
        if global_activity:
            from sme_gend.services.activity_audit_log import MODULE_LABELS
            for key, label in MODULE_LABELS.items():
                self._filter_module.addItem(label, key)
            self._filter_module.setEditable(True)
            self._filter_module.setInsertPolicy(NoWheelComboBox.InsertPolicy.NoInsert)
            fl.addWidget(QLabel("Module (nom ou code)"), 5, 0)
            fl.addWidget(self._filter_module, 5, 1, 1, 3)
        else:
            self._filter_module.hide()
        filters_outer.addLayout(fl)

        btn_search = QPushButton("Rechercher")
        btn_search.setObjectName("primaryButton")
        btn_search.clicked.connect(self.refresh)
        btn_reset = QPushButton("Réinitialiser")
        btn_reset.clicked.connect(self._reset_filters)
        for button in (btn_reset, btn_search):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        btn_bar.setSpacing(8)
        btn_bar.addStretch()
        btn_bar.addWidget(btn_reset, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_bar.addWidget(btn_search, 0, Qt.AlignmentFlag.AlignVCenter)
        self._btn_previous = QPushButton("Page précédente")
        self._btn_next = QPushButton("Page suivante")
        self._btn_export_all = QPushButton("Exporter tous les résultats…")
        self._btn_previous.clicked.connect(self._previous_page)
        self._btn_next.clicked.connect(self._next_page)
        self._btn_export_all.clicked.connect(self._export_all)
        for button in (self._btn_previous, self._btn_next, self._btn_export_all):
            button.setVisible(global_activity)
            btn_bar.insertWidget(0, button)
        self._btn_previous.setEnabled(False)
        self._btn_next.setEnabled(False)

        self._table = QTableView()
        self._model = SimpleRowTableModel(
            ["Date & heure", "Action", "Auteur", "Mois SME", "N° / Agent", "Détail", "Données"]
        )
        self._table.setModel(self._model)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Long action labels must not squeeze the detail columns off screen.
        for column, width in ((0, 165), (1, 290), (2, 100), (3, 90), (4, 140)):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._entries: list[dict] = []

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("auditResultCount")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(5)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(self._help)
        # Keep search visible and reserve the remaining height for results.
        fl.removeWidget(self._filter_text)
        text_label = fl.itemAtPosition(4, 0).widget()
        fl.removeWidget(text_label)
        text_label.hide()
        quick = QHBoxLayout()
        quick.addWidget(self._filter_text, 1)
        self._advanced_toggle = QToolButton()
        self._advanced_toggle.setText('Filtres avancés')
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        quick.addWidget(self._advanced_toggle)
        quick.addWidget(self._help.button)
        lay.addLayout(quick)
        self._advanced_scroll = QScrollArea()
        self._advanced_scroll.setWidgetResizable(True)
        self._advanced_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._advanced_scroll.setWidget(g_filters)
        self._advanced_scroll.setMaximumHeight(230)
        self._advanced_scroll.setMinimumHeight(180)
        self._advanced_scroll.hide()
        self._advanced_toggle.toggled.connect(self._advanced_scroll.setVisible)
        self._advanced_toggle.toggled.connect(lambda expanded: self._advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        lay.addWidget(self._advanced_scroll)
        lay.addLayout(btn_bar)
        lay.addWidget(self._lbl_count, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._table, stretch=1)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.addWidget(inner, 1)

        self._view_data = ViewDataState(stale=True)
        self._settings = None
        self._request_serial = 0
        self._loading = False
        self._pending_refresh = False
        self._resetting = False
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self.refresh)
        for field in (self._filter_actor, self._filter_personnel_id, self._filter_personnel_nom, self._filter_text):
            field.textChanged.connect(self._filters_changed)
            field.returnPressed.connect(self.refresh)
        self._filter_action.currentIndexChanged.connect(self._filters_changed)
        self._filter_action.editTextChanged.connect(self._filters_changed)
        self._filter_module.currentTextChanged.connect(self._filters_changed)
        self._combo_year.currentTextChanged.connect(self._filters_changed)
        self._combo_month.currentIndexChanged.connect(self._filters_changed)
        self._filter_date_from.dateChanged.connect(self._filters_changed)
        self._filter_date_to.dateChanged.connect(self._filters_changed)
        self._filter_limit.valueChanged.connect(self._filters_changed)

    def _reset_pages(self):
        self._page_cursors = [None]
        self._page_snapshot = None
        self._next_cursor = None
        self._btn_previous.setEnabled(False)
        self._btn_next.setEnabled(False)

    def _next_page(self):
        if self._next_cursor is not None and not self._loading:
            self._page_cursors.append(self._next_cursor)
            self.refresh(_keep_page=True)

    def _previous_page(self):
        if len(self._page_cursors)>1 and not self._loading:
            self._page_cursors.pop()
            self.refresh(_keep_page=True)

    def _export_all(self):
        from PySide6.QtWidgets import QFileDialog
        from sme_gend.config import get_settings
        from sme_gend.services.auth_guard import session_can_view_sensitive_audit, session_can_export_documents
        from sme_gend.services.activity_audit_log import export_activity_csv, validate_activity_query, sensitive_export_query
        from sme_gend.ui.confirm_dialogs import confirm_overwrite_file
        if self._export_busy or not session_can_view_sensitive_audit() or not session_can_export_documents():
            return
        try:
            query = (validate_activity_query(self._build_query()) if self._global_activity
                     else sensitive_export_query(self._build_query()))
            settings = self._settings if self._settings is not None else get_settings()
            destination, _ = QFileDialog.getSaveFileName(self, "Exporter tous les résultats filtrés", "journal_activite.csv", "CSV UTF-8 (*.csv)")
            if not destination or not confirm_overwrite_file(self, destination):
                return
        except ValueError as exc:
            self._lbl_count.setText(str(exc))
            return
        except Exception:
            self._lbl_count.setText("Export impossible : vérifiez les filtres et la configuration.")
            return
        self._export_busy = True
        self._btn_export_all.setEnabled(False)
        def done(count):
            self._export_busy = False
            self._btn_export_all.setEnabled(True)
            self._lbl_count.setText(f"Export terminé : {count} entrée(s), toutes les pages filtrées.")
        def failed(exc):
            self._export_busy = False
            self._btn_export_all.setEnabled(True)
            self._lbl_count.setText("Export non terminé : vérifiez MySQL, vos droits et le dossier de destination, puis réessayez.")
        run_in_background(self, lambda: export_activity_csv(query, destination, settings=settings), done, on_error=failed)

    def _fill_year_combo(self) -> None:
        self._combo_year.blockSignals(True)
        self._combo_year.clear()
        self._combo_year.addItem("(Toutes)", None)
        current = datetime.now().year
        for year in range(current + 1, current - 12, -1):
            self._combo_year.addItem(str(year), year)
        self._combo_year.blockSignals(False)
        self._rebuild_month_combo()

    def _rebuild_month_combo(self) -> None:
        prev = self._combo_month.currentData()
        try:
            year = self._selected_year()
        except ValueError:
            year = None
        self._combo_month.blockSignals(True)
        self._combo_month.clear()
        self._combo_month.addItem("(Tous)", None)
        if year is None:
            self._combo_month.setEnabled(False)
        else:
            self._combo_month.setEnabled(True)
            for month in range(1, 13):
                self._combo_month.addItem(_MOIS_FR[month - 1], month)
            if prev is not None:
                for i in range(self._combo_month.count()):
                    if self._combo_month.itemData(i) == prev:
                        self._combo_month.setCurrentIndex(i)
                        break
        self._combo_month.blockSignals(False)

    def _on_year_changed(self, _value) -> None:
        self._rebuild_month_combo()

    def _selected_year(self) -> int | None:
        text = self._combo_year.currentText().strip()
        if not text or text == self._combo_year.itemText(0):
            return None
        if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 9999:
            raise ValueError("L’année SME doit être comprise entre 1 et 9999.")
        return int(text)

    def _mois_filter_value(self) -> str:
        year = self._selected_year()
        month = self._combo_month.currentData()
        if year is None:
            return ""
        if month is None:
            return f"{int(year):04d}"
        return f"{int(year):04d}-{int(month):02d}"

    def _reset_filters(self) -> None:
        self._reset_pages()
        self._resetting = True
        try:
            self._filter_action.setCurrentIndex(0)
            self._filter_module.setCurrentIndex(0)
            self._combo_year.setCurrentIndex(0)
            self._combo_year.setCurrentText(self._combo_year.itemText(0))
            self._rebuild_month_combo()
            for widget in (
                self._filter_actor,
                self._filter_personnel_id,
                self._filter_personnel_nom,
                self._filter_text,
            ):
                widget.clear()
            self._filter_date_from.setDate(_DATE_EMPTY)
            self._filter_date_to.setDate(_DATE_EMPTY)
            self._filter_limit.setValue(500)
        finally:
            self._resetting = False
        self.refresh()

    def _build_query(self) -> SensitiveAuditQuery:
        if self._global_activity:
            from sme_gend.services.activity_audit_log import ActivityAuditQuery
            query_type = ActivityAuditQuery
        else:
            query_type = SensitiveAuditQuery
        query = query_type(
            action=(self._filter_action.currentData() if (self._filter_action.currentIndex() >= 0 and self._filter_action.currentText() == self._filter_action.itemText(self._filter_action.currentIndex())) else self._filter_action.currentText()) or "",
            actor=self._filter_actor.text(),
            personnel_id=self._filter_personnel_id.text(),
            personnel_nom=self._filter_personnel_nom.text(),
            sme_mois=self._mois_filter_value(),
            date_from=_read_optional_date(self._filter_date_from),
            date_to=_read_optional_date(self._filter_date_to),
            text=self._filter_text.text(),
            limit=self._filter_limit.value(),
        )

        if self._global_activity:
            query.cursor_before = self._page_cursors[-1]
            query.snapshot_id = self._page_snapshot
            query.module = (self._filter_module.currentData() if (self._filter_module.currentIndex() >= 0 and self._filter_module.currentText() == self._filter_module.itemText(self._filter_module.currentIndex())) else self._filter_module.currentText()) or ""
        return query

    def _search_entries(self, query, settings):
        if self._global_activity:
            from sme_gend.services.activity_audit_log import search_activity_page
            return search_activity_page(query, settings=settings)
        return search_sensitive_audit(query, settings=settings)

    def _action_label(self, entry):
        if self._global_activity:
            from sme_gend.services.activity_audit_log import activity_action_label
            return activity_action_label(entry)
        return format_action_label(str(entry.get("action", "")))

    def _entry_detail(self, entry):
        if self._global_activity:
            from sme_gend.services.activity_audit_log import activity_detail
            return activity_detail(entry)
        return format_entry_detail(entry)

    def _clear_results(self) -> None:
        self._entries = []
        self._model.set_rows([])

    def mark_view_stale(self) -> None:
        self._request_serial += 1
        self._view_data.mark_stale()
        self._clear_results()

    def mark_dirty(self) -> None:
        self.mark_view_stale()

    def apply_runtime_settings(self, settings) -> None:
        self._reset_pages()
        self._settings = settings
        self._filter_timer.stop()
        self.mark_view_stale()
        if self.isVisible():
            self.refresh()

    def _deny_access(self) -> None:
        self._filter_timer.stop()
        self._pending_refresh = False
        self.mark_view_stale()
        self._lbl_count.setText("Accès refusé — audit réservé aux comptes autorisés.")

    def _filters_changed(self, *_args) -> None:
        if self._resetting:
            return
        self._reset_pages()
        self.mark_view_stale()
        self._pending_refresh = False
        self._lbl_count.setText("Filtres modifiés — recherche en attente…")
        self._filter_timer.start()

    def on_page_activated(self) -> None:
        from sme_gend.services.auth_guard import session_can_view_sensitive_audit
        if not session_can_view_sensitive_audit():
            self._deny_access()
        elif self._view_data.needs_load():
            self.refresh()

    def refresh(self, *_args, _keep_page=False) -> None:
        from sme_gend.config import get_settings
        from sme_gend.services.auth_guard import session_can_view_sensitive_audit

        self._filter_timer.stop()
        if self._global_activity and not _keep_page:
            self._reset_pages()
        if not session_can_view_sensitive_audit():
            self._deny_access()
            return
        self.mark_view_stale()
        try:
            query = validate_sensitive_audit_query(self._build_query())
        except ValueError as exc:
            self._pending_refresh = False
            self._lbl_count.setText(str(exc))
            return
        if self._loading:
            self._pending_refresh = True
            self._lbl_count.setText("Recherche en cours — nouveaux filtres en attente…")
            return
        try:
            settings = self._settings if self._settings is not None else get_settings()
        except Exception:
            self._lbl_count.setText("Configuration du journal central indisponible. Réessayez après vérification de MySQL.")
            return
        serial = self._request_serial
        self._loading = True
        self._pending_refresh = False
        self._lbl_count.setText("Recherche dans le journal central…")

        def finish():
            self._loading = False
            if self._pending_refresh:
                self._pending_refresh = False
                self._filter_timer.start(0)

        def loaded(entries):
            from sme_gend.services.auth_guard import session_can_view_sensitive_audit
            try:
                if not session_can_view_sensitive_audit():
                    self._deny_access()
                elif serial == self._request_serial:
                    self._apply_entries(entries, query.limit)
                    self._view_data.finish_load()
            finally:
                finish()

        def failed(_exc):
            from sme_gend.services.auth_guard import session_can_view_sensitive_audit
            if not session_can_view_sensitive_audit():
                self._deny_access()
            elif serial == self._request_serial:
                self._clear_results()
                self._view_data.mark_stale()
                self._lbl_count.setText("Lecture du journal central impossible. Vérifiez la connexion MySQL puis réessayez.")
            finish()

        try:
            run_in_background(self, lambda: self._search_entries(query, settings), loaded, on_error=failed)
        except Exception as exc:
            failed(exc)

    def _apply_entries(self, entries: list[dict], limit: int) -> None:
        if self._global_activity:
            from sme_gend.services.activity_audit_log import MODULE_LABELS, OPERATION_LABELS
            catalog = getattr(entries, 'catalog', [])
            for combo, values, labels in (
                (self._filter_module, {m for m, _ in catalog}, MODULE_LABELS),
                (self._filter_action, {a for _, a in catalog}, OPERATION_LABELS),
            ):
                text = combo.currentText()
                combo.blockSignals(True)
                try:
                    for code in sorted(values):
                        if code and combo.findData(code) < 0:
                            combo.addItem(labels.get(code, code.replace('_', ' ')), code)
                    combo.setCurrentText(text)
                finally:
                    combo.blockSignals(False)
        if self._global_activity:
            self._page_snapshot = getattr(entries, 'snapshot_id', None)
            self._next_cursor = getattr(entries, 'next_cursor', None)
            self._btn_previous.setEnabled(len(self._page_cursors)>1)
            self._btn_next.setEnabled(self._next_cursor is not None)
        self._entries = entries
        rows: list[tuple[str, ...]] = []
        for entry in entries:
            pid = str(entry.get("personnel_id") or "")
            nom = str(entry.get("personnel_nom") or "")
            agent_txt = f"{pid} — {nom}".strip(" —") if nom else (pid or "—")
            payload_preview = ""
            payload = entry_payload(entry)
            if payload:
                payload_preview = json.dumps(payload, ensure_ascii=False, default=str)[:400]
            rows.append(
                (
                    _fmt_ts(str(entry.get("ts_utc", ""))),
                    self._action_label(entry),
                    str(entry.get("actor") or "—"),
                    str(entry.get("sme_mois_context") or "—"),
                    agent_txt,
                    self._entry_detail(entry),
                    payload_preview or "—",
                )
            )
        self._model.set_rows(rows)

        if self._global_activity:
            self._lbl_count.setText(f"Page {len(self._page_cursors)} — {len(entries)} entrée(s). L’export comprend tous les résultats correspondant aux filtres.")
            return
        suffix = " — limite d’affichage atteinte ; affinez les filtres." if len(entries) >= limit else ""
        self._lbl_count.setText(f"{len(entries)} entrée(s) affichée(s) — journal central MySQL" + suffix)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _on_row_double_clicked(self, index) -> None:
        from sme_gend.services.auth_guard import session_can_view_sensitive_audit
        if not session_can_view_sensitive_audit():
            self._deny_access()
            return
        row = index.row()
        if not (0 <= row < len(self._entries)):
            return
        entry = self._entries[row]

        import json

        payload = entry_payload(entry)
        payload_full = json.dumps(payload, ensure_ascii=False, indent=2, default=str) if payload else "—"

        pid = str(entry.get("personnel_id") or "")
        nom = str(entry.get("personnel_nom") or "")
        agent_txt = f"{pid} — {nom}".strip(" —") if nom else (pid or "—")

        dlg = QDialog(self)
        dlg.setWindowTitle("Détail de l'entrée d'audit")
        dlg.setMinimumSize(560, 420)
        lay = QVBoxLayout(dlg)
        fields = (
            ("Date locale", _fmt_ts(str(entry.get("ts_utc", "")))),
            ("Action", self._action_label(entry)),
            ("Auteur", entry.get("actor") or "—"),
            ("Mois SME", entry.get("sme_mois_context") or "—"),
            ("Agent", agent_txt), ("Détail", self._entry_detail(entry)),
        )
        header = QLabel("<br>".join(f"<b>{label} :</b> {escape(str(value))}" for label, value in fields))
        header.setWordWrap(True)
        header.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(header)
        lay.addWidget(QLabel("<b>Données enregistrées complètes (JSON)</b>"))
        payload_view = QPlainTextEdit(payload_full)
        payload_view.setReadOnly(True)
        payload_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(payload_view, stretch=1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Fermer")
        lay.addWidget(bb)
        dlg.exec()
