"""Historique des documents générés — attestations, exports Excel/PDF."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
    QDialog,
    QTextEdit,
    QDialogButtonBox,
)

from sme_gend.ui.simple_table_model import SimpleRowTableModel
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.document_job import can_export, require_export

_DOC_TYPE_LABELS: dict[str, str] = {
    "harmonisation_fonctions": "Harmonisation des fonctions",
    "attestation": "Attestation",
    "attestation_batch": "Attestations groupées",
    "soit_transmis": "Soit transmis",
    "zip": "Archive ZIP",
    "numerique": "Situation numérique",
    "period_activity": "Mouvements sur une période",
    "detaches": "Détachés",
    "liste_personnel": "Liste personnel",
    "liste_padmin": "Liste par position adm.",
    "liste_present": "Liste des présents",
    "sme_dpf": "SME DPF",
    "dossier_pdf": "Dossier agent PDF",
    "missions": "Missions", "desertions": "Désertions", "stages": "Stages",
    "conges": "Congés", "malades": "Malades", "punis": "Punis et détention",
    "gains_pertes": "Gains et pertes", "distinctions": "Attributions de distinctions",
    "distinction_template": "Modèle d’import des distinctions",
    "referentiel_structures": "Référentiel des structures",
}


def _incomplete_persons(entry: dict) -> bool:
    try:
        return int(entry.get("count") or 0) > len(entry.get("persons") or [])
    except (ValueError, TypeError):
        return False


def _entry_details(entry: dict) -> str:
    persons = entry.get("persons") or []
    if not isinstance(persons, list):
        persons = [str(persons)]
    lines = [f"Type : {_fmt_type(str(entry.get('doc_type') or ''), str(entry.get('format') or ''))}",
             f"Effectif total : {entry.get('count', 1)}", f"Fichier : {entry.get('dest') or '—'}",
             "", "Personnes enregistrées :"]
    meta = entry.get("meta") or {}
    if isinstance(meta, dict) and meta.get("revision_id"):
        lines[3:3] = ["", f"Version SME : {meta['revision_id']}",
                      f"Capture UTC : {meta.get('captured_at_utc') or '—'}",
                      f"Dossier archivé : {meta.get('revision_folder') or '—'}",
                      f"SHA-256 classeur : {meta.get('workbook_sha256') or '—'}",
                      f"SHA-256 modèle : {meta.get('template_sha256') or '—'}",
                      f"SHA-256 données : {meta.get('dataset_sha256') or '—'}",
                      "Export versionné ; validation officielle distincte."]
    lines.extend(f"{i}. {person}" for i, person in enumerate(persons, 1))
    if _incomplete_persons(entry):
        lines.extend(["", "Liste nominative non enregistrée ou incomplète pour cette génération. Les noms manquants ne sont pas disponibles dans cette trace."])
    return "\n".join(lines)


def _fmt_ts(ts_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return ts_utc or "—"


def _fmt_type(doc_type: str, fmt: str) -> str:
    label = _DOC_TYPE_LABELS.get(doc_type, doc_type.replace("_", " ").title())
    f = (fmt or "").strip().upper()
    return f"{label} ({f})" if f else label


_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


def _parse_ym(ym: str) -> tuple[int, int] | None:
    raw = (ym or "").strip()
    if len(raw) < 7 or raw[4:5] != "-":
        return None
    try:
        y, m = int(raw[:4]), int(raw[5:7])
    except ValueError:
        return None
    if 1 <= m <= 12:
        return y, m
    return None


def _years_from_mois(values: list[str]) -> list[int]:
    years: set[int] = set()
    for raw in values:
        parsed = _parse_ym(raw)
        if parsed is not None:
            years.add(parsed[0])
    if not years:
        years.add(datetime.now().year)
    return sorted(years, reverse=True)


def _fmt_meta(meta: dict | None) -> str:
    if not isinstance(meta, dict) or not meta:
        return ""
    if meta.get("revision_id"):
        return "Version SME : " + str(meta["revision_id"])
    parts: list[str] = []
    for key, val in meta.items():
        if val is None:
            continue
        parts.append(f"{key}={val}")
    return ", ".join(parts[:6])


class DocGenLogView(QWidget):
    """Vue de l'historique des documents générés."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._title = QLabel("Historique des documents générés")
        self._title.setObjectName("dashTitle")

        self._subtitle = QLabel()
        self._subtitle.setObjectName("dashSubtitle")
        self._subtitle.setWordWrap(True)
        from sme_gend.ui.expandable_help import ExpandableHelp
        self._help = ExpandableHelp('', self)

        self._filter_type = QComboBox()
        self._filter_type.currentIndexChanged.connect(self._apply_rows)

        self._combo_year = QComboBox()
        self._combo_year.setMinimumWidth(92)
        self._combo_year.currentIndexChanged.connect(self._on_year_changed)
        self._combo_month = QComboBox()
        self._combo_month.setMinimumWidth(128)
        self._combo_month.currentIndexChanged.connect(self._on_month_changed)

        self._filter_search = QLineEdit()
        self._filter_search.setPlaceholderText(
            "Nom, fichier, type, auteur, date jj/mm/aaaa…"
        )
        self._filter_search.setClearButtonEnabled(True)
        self._filter_search.setMinimumWidth(180)
        self._filter_search.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._filter_search.textChanged.connect(self._apply_rows)

        self._table = QTableView()
        self._model = SimpleRowTableModel(
            ["Date & heure", "Type", "Mois SME", "Personnes / effectif", "Détail", "Fichier", "Auteur"]
        )
        self._table.setModel(self._model)
        self._table.activated.connect(self._show_entry_details)
        self._table.setToolTip("Double-cliquez ou appuyez sur Entrée pour consulter la liste nominative complète disponible.")
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        btn_refresh = QPushButton("Actualiser")
        btn_refresh.clicked.connect(self.refresh)

        btn_export = QPushButton("Exporter CSV…")
        btn_export.setObjectName("docLogExportCsvBtn")
        btn_export.setToolTip(
            "Exporte les lignes affichées (filtres compris) vers un CSV réimportable."
        )
        btn_export.clicked.connect(self._on_export_csv)
        self._btn_export = btn_export

        btn_import = QPushButton("Importer CSV…")
        btn_import.setObjectName("docLogImportCsvBtn")
        btn_import.setToolTip(
            "Ajoute les traces du fichier à l'historique. "
            "Les entrées déjà présentes sont conservées et ne sont pas dupliquées."
        )
        btn_import.clicked.connect(self._on_import_csv)
        self._btn_import = btn_import

        btn_clear = QPushButton("Effacer l'historique local…")
        btn_clear.clicked.connect(self._on_clear)
        self._btn_clear = btn_clear

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        for combo in (self._filter_type, self._combo_year, self._combo_month):
            combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        for button in (btn_refresh, btn_export, btn_import, btn_clear):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        from sme_gend.ui.responsive_panels import WrapLayout
        bar = WrapLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        align = Qt.AlignmentFlag.AlignVCenter
        bar.addWidget(QLabel("Type :"))
        bar.addWidget(self._filter_type)
        bar.addWidget(QLabel("Année SME :"))
        bar.addWidget(self._combo_year)
        bar.addWidget(QLabel("Mois SME :"))
        bar.addWidget(self._combo_month)
        bar.addWidget(QLabel("Recherche :"))
        bar.addWidget(self._filter_search)
        bar.addWidget(btn_refresh)
        bar.addWidget(btn_export)
        bar.addWidget(btn_import)
        bar.addWidget(btn_clear)
        bar.addWidget(self._lbl_count)
        bar.addWidget(self._help.button)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(5)
        lay.addWidget(self._subtitle)
        self._subtitle.hide()
        lay.addWidget(self._help)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        self._page_offset = 0
        self._previous_page = QPushButton("Page précédente")
        self._next_page = QPushButton("Page suivante")
        self._previous_page.clicked.connect(lambda: self._change_page(-1))
        self._next_page.clicked.connect(lambda: self._change_page(1))
        self._previous_page.setEnabled(False)
        self._next_page.setEnabled(False)
        pages = QHBoxLayout()
        pages.addWidget(self._previous_page)
        pages.addWidget(self._next_page)
        pages.addStretch()
        lay.addLayout(pages)

        self._view_data = ViewDataState(stale=True)
        self._entries: list[dict] = []
        self._period_catalog = []
        self._settings = None
        self._request_serial = 0
        self._busy = False
        self._pending = False
        self._access_scope = None
        self._revision_token = None
        self._revision_busy = False
        self._revision_timer = QTimer(self)
        self._revision_timer.setInterval(5000)
        self._revision_timer.timeout.connect(self._poll_document_revision)
        self._revision_timer.start()
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self.refresh)
        self._filter_search.returnPressed.connect(self.refresh)
        self._configure_access_ui()
        self._rebuild_period_combos()
        self._lbl_count.setText("Chargement à la première visite…")

    def _full_doc_log_access(self) -> bool:
        from sme_gend.services.auth_guard import session_can_view_doc_log

        return session_can_view_doc_log()

    def _attestation_history_access(self) -> bool:
        from sme_gend.services.auth_guard import session_can_view_attestation_history

        return session_can_view_attestation_history()

    def _history_only_mode(self) -> bool:
        return self._attestation_history_access() and not self._full_doc_log_access()

    def _configure_access_ui(self) -> None:
        full = self._full_doc_log_access()
        history_only = self._history_only_mode()
        scope = (full, history_only)
        if scope != self._access_scope:
            self.mark_dirty()
            self._access_scope = scope
        previous_type = self._filter_type.currentData()

        if full:
            self._title.setText("Historique des documents générés")
            self._subtitle.setText(
                "Attestations, soit transmis, exports Excel (numérique, détachés, personnel, présents) "
                "et PDF dossier agent — journalisés à chaque génération. "
                "<b>Accès administrateur (journal complet).</b>"
            )
        elif history_only:
            self._title.setText("Historique des attestations")
            self._subtitle.setText(
                "Attestations limite d'âge, soit transmis et archives ZIP — "
                "Consultation limitée à ces documents ; export CSV soumis au droit d’export, sans purge."
            )
        else:
            self._subtitle.setText(
                "Accès refusé — permission « Historique attestations » ou administrateur requise."
            )

        self._btn_clear.setVisible(full)
        self._help.text.setText(self._subtitle.text())
        self._subtitle.setVisible(not full)
        if history_only and not full:
            self._subtitle.setText('Historique des attestations — accès limité.')
        self._btn_clear.setEnabled(full)
        self._btn_export.setVisible(full or history_only)
        self._btn_export.setEnabled((full or history_only) and can_export())
        self._btn_import.setVisible(full)
        self._btn_import.setEnabled(full)

        self._filter_type.blockSignals(True)
        self._filter_type.clear()
        self._filter_type.addItem("Tous les types", "")
        if full:
            type_keys = sorted(_DOC_TYPE_LABELS)
        elif history_only:
            from sme_gend.services.doc_gen_log import attestation_history_doc_types

            type_keys = sorted(attestation_history_doc_types())
        else:
            type_keys = []
        for key in type_keys:
            self._filter_type.addItem(_DOC_TYPE_LABELS.get(key, key), key)
        previous_index = self._filter_type.findData(previous_type)
        self._filter_type.setCurrentIndex(max(0, previous_index))
        self._filter_type.blockSignals(False)

    def mark_dirty(self) -> None:
        self._request_serial += 1
        self._view_data.mark_stale()
        self._entries = []
        self._model.set_rows([])
        self._btn_export.setEnabled(False)
        self._previous_page.setEnabled(False)
        self._next_page.setEnabled(False)

    def apply_runtime_settings(self, settings) -> None:
        self._page_offset = 0
        self._settings = settings
        self._revision_token = None
        self._period_catalog = []
        self.mark_dirty()
        if self.isVisible():
            self.refresh()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        self._configure_access_ui()
        if not self._attestation_history_access():
            self._lbl_count.setText(
                "Accès refusé — historique réservé aux comptes autorisés."
            )
            self.mark_dirty()
            return
        if self._view_data.needs_load():
            self.refresh()
        else:
            self._poll_document_revision()

    def _poll_document_revision(self) -> None:
        if self._revision_busy or self._busy or not self.isVisible() or not self._attestation_history_access():
            return
        from sme_gend.config import get_settings
        from sme_gend.repositories.mysql_connection import mysql_database_key
        from sme_gend.services.doc_gen_log import read_document_revision
        try:
            settings = self._settings if self._settings is not None else get_settings()
            database = mysql_database_key(settings)
        except Exception:
            self.mark_dirty()
            self._lbl_count.setText("Configuration du journal central indisponible.")
            return
        self._revision_busy = True
        def current():
            try:
                active = self._settings if self._settings is not None else get_settings()
                return database == mysql_database_key(active) and self._attestation_history_access()
            except Exception:
                return False
        def done(token):
            self._revision_busy = False
            if not current():
                return
            changed = self._revision_token != token
            self._revision_token = token
            if changed or self._view_data.needs_load():
                self.refresh()
        def fail(_error):
            self._revision_busy = False
            if current():
                self.mark_dirty()
                self._lbl_count.setText("Synchronisation du journal indisponible. Vérifiez MySQL ; nouvelle tentative automatique.")
        try:
            run_in_background(self, lambda: read_document_revision(settings=settings), done, on_error=fail)
        except Exception as exc:
            fail(exc)

    def _passes_filters(self, entry: dict) -> bool:
        from sme_gend.services.doc_gen_log import (
            doc_entry_is_attestation_scope,
            doc_entry_passes_filters,
        )

        if self._history_only_mode() and not doc_entry_is_attestation_scope(entry):
            return False
        return doc_entry_passes_filters(
            entry,
            type_key=self._filter_type.currentData() or "",
            mois_filter=self._mois_filter_value(),
            search_query=self._filter_search.text(),
            type_labels=_DOC_TYPE_LABELS,
        )

    def _mois_filter_value(self) -> str:
        """'' = toutes les périodes, '2026' = l'année, '2026-08' = le mois."""
        year = self._combo_year.currentData()
        month = self._combo_month.currentData()
        if year is None:
            return ""
        if month is None:
            return f"{int(year):04d}"
        return f"{int(year):04d}-{int(month):02d}"

    def _catalog_mois(self) -> list[str]:
        return list(self._period_catalog)

    def _rebuild_period_combos(self) -> None:
        prev_year = self._combo_year.currentData()
        prev_month = self._combo_month.currentData()
        years = _years_from_mois(self._catalog_mois())
        if prev_year is not None and prev_year not in years:
            years = sorted([*years, prev_year], reverse=True)

        self._combo_year.blockSignals(True)
        self._combo_year.clear()
        self._combo_year.addItem("(Toutes)", None)
        for year in years:
            self._combo_year.addItem(str(year), year)
        if prev_year is not None:
            for i in range(self._combo_year.count()):
                if self._combo_year.itemData(i) == prev_year:
                    self._combo_year.setCurrentIndex(i)
                    break
        self._combo_year.blockSignals(False)
        self._rebuild_month_combo(prev_month)

    def _rebuild_month_combo(self, prev_month: int | None = None) -> None:
        year = self._combo_year.currentData()
        if prev_month is None and self._combo_month.count():
            prev_month = self._combo_month.currentData()
        self._combo_month.blockSignals(True)
        self._combo_month.clear()
        self._combo_month.addItem("(Tous)", None)
        if year is None:
            self._combo_month.setEnabled(False)
        else:
            self._combo_month.setEnabled(True)
            for month in range(1, 13):
                self._combo_month.addItem(_MOIS_FR[month - 1], month)
            if prev_month is not None:
                for i in range(self._combo_month.count()):
                    if self._combo_month.itemData(i) == prev_month:
                        self._combo_month.setCurrentIndex(i)
                        break
        self._combo_month.blockSignals(False)

    def _on_year_changed(self, _index: int) -> None:
        self._rebuild_month_combo(None)
        self._apply_rows()

    def _on_month_changed(self, _index: int) -> None:
        self._apply_rows()

    def refresh(self) -> None:
        from sme_gend.config import get_settings
        from sme_gend.services.doc_gen_log import read_doc_log, doc_log_months
        self._filter_timer.stop()
        self.mark_dirty()
        if not self._attestation_history_access():
            self._pending = False
            self._lbl_count.setText("Accès refusé au journal des documents.")
            return
        if self._busy:
            self._pending = True
            return
        try:
            settings = self._settings if self._settings is not None else get_settings()
        except Exception:
            self._lbl_count.setText("Configuration du journal central indisponible.")
            return
        query = dict(type_key=self._filter_type.currentData() or "",
                     mois_filter=self._mois_filter_value(), search_query=self._filter_search.text(),
                     type_labels=_DOC_TYPE_LABELS, settings=settings)
        if self._page_offset:
            query["offset"] = self._page_offset
        serial = self._request_serial
        scope = (self._full_doc_log_access(), self._history_only_mode())
        self._busy = True
        self._pending = False
        self._lbl_count.setText("Recherche dans le journal central…")
        def work():
            return read_doc_log(max_entries=2000, **query), doc_log_months(settings=settings)
        def finish():
            self._busy = False
            if self._pending:
                self._pending = False
                self._filter_timer.start(0)
        def done(result):
            try:
                if not self._attestation_history_access():
                    self.mark_dirty()
                    self._lbl_count.setText("Accès refusé au journal des documents.")
                elif scope != (self._full_doc_log_access(), self._history_only_mode()):
                    self.mark_dirty()
                    self._pending = True
                elif serial == self._request_serial:
                    self._entries, self._period_catalog = result
                    self._rebuild_period_combos()
                    self._render_entries()
                    self._view_data.finish_load()
                    self._btn_export.setEnabled(can_export())
            finally:
                finish()
        def fail(_exc):
            if serial == self._request_serial:
                self.mark_dirty()
                self._lbl_count.setText("Lecture du journal central impossible. Vérifiez MySQL puis réessayez.")
            finish()
        try:
            run_in_background(self, work, done, on_error=fail)
        except Exception as exc:
            fail(exc)

    def _apply_rows(self, *_args) -> None:
        self._page_offset = 0
        self.mark_dirty()
        self._pending = False
        self._lbl_count.setText("Filtres modifiés — recherche en attente…")
        self._filter_timer.start()

    def _change_page(self, direction: int) -> None:
        if self._busy or not self._view_data.is_ready() or not self._attestation_history_access():
            return
        self._page_offset = max(0, self._page_offset + direction * 2000)
        self.refresh()

    def _render_entries(self) -> None:
        if not self._attestation_history_access():
            self.mark_dirty()
            return
        rows: list[tuple[str, ...]] = []
        for entry in self._entries:
            ts = entry.get("ts_utc", "")
            dtype = str(entry.get("doc_type") or "")
            fmt = str(entry.get("format") or "")
            count = entry.get("count", 1)
            persons = entry.get("persons", [])
            dest = entry.get("dest", "")
            mois = entry.get("mois", "")
            meta = entry.get("meta")

            person_txt = ", ".join(str(person) for person in persons) if isinstance(persons, list) else str(persons or "")
            person_txt = f"{person_txt} — effectif : {count}" if person_txt else str(count)
            if isinstance(persons, list) and _incomplete_persons(entry):
                person_txt += " — ancienne trace : liste nominative incomplète"

            rows.append(
                (
                    _fmt_ts(ts),
                    _fmt_type(dtype, fmt),
                    str(mois or "—"),
                    person_txt,
                    _fmt_meta(meta) or str(count),
                    str(dest or "—"),
                    str(entry.get("actor") or "—"),
                )
            )
        self._model.set_rows(rows)
        self._previous_page.setEnabled(self._page_offset > 0)
        self._next_page.setEnabled(len(rows) == 2000)

        scope = "attestations" if self._history_only_mode() else "toutes"
        self._lbl_count.setText(
            f"Page {self._page_offset // 2000 + 1} · {len(rows)} entrée(s) {scope} affichée(s) — journal central MySQL"
        )

    def _show_entry_details(self, index) -> None:
        if not self._attestation_history_access() or not self._view_data.is_ready():
            return
        if not index.isValid() or index.row() >= len(self._entries):
            return
        entry = self._entries[index.row()]
        dialog = QDialog(self)
        dialog.setWindowTitle("Détail du document généré")
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        content = QTextEdit()
        content.setReadOnly(True)
        content.setPlainText(_entry_details(entry))
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        meta = entry.get("meta") or {}
        if entry.get("doc_type") == "numerique" and isinstance(meta, dict) and meta.get("revision_folder"):
            retrieve = buttons.addButton("Récupérer le classeur archivé…", QDialogButtonBox.ButtonRole.ActionRole)
            retrieve.setEnabled(can_export())
            retrieve.clicked.connect(lambda: self._retrieve_sme_revision(entry))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _retrieve_sme_revision(self, entry) -> None:
        if not self._attestation_history_access() or not require_export(self):
            return
        from pathlib import Path
        from sme_gend.ui.confirm_dialogs import confirm_overwrite_file
        from sme_gend.ui.bg_task import run_with_progress
        from sme_gend.services.sme_export_revision import retrieve_export_revision
        from sme_gend.services.sme_publication_backup import locate_revision
        meta = entry.get("meta") or {}
        if not meta.get("revision_folder"):
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Récupérer la version SME",
            f"SME_{entry.get('mois') or 'archive'}_{meta['revision_id']}.xlsx", "Excel (*.xlsx)")
        if not destination or not confirm_overwrite_file(self, destination):
            return
        def work():
            folder = locate_revision(meta, str(entry.get("mois") or ""))
            retrieve_export_revision(folder, Path(destination), expected=meta)
            return True
        if run_with_progress(self, "Version SME", "Vérification et copie du classeur archivé…", work) is not None:
            QMessageBox.information(self, "Version SME récupérée", f"Copie exacte enregistrée :\n{destination}")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _on_clear(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        from sme_gend.services.doc_gen_log import clear_doc_log

        if not require_permission(
            self,
            "attestations.doc_log",
            message="Seuls les administrateurs peuvent effacer l'historique local.",
        ):
            return

        if not confirm_yes_no(
            self, "Effacer l'historique",
            "Supprimer toutes les entrées du journal local (JSONL) ?\n"
            "Le journal MySQL est conservé.",
            default_no=True,
        ):
            return
        clear_doc_log()
        self.refresh()
        QMessageBox.information(self, "Historique", "Journal local effacé.")

    def _filtered_entries(self) -> list[dict]:
        return list(self._entries) if self._view_data.is_ready() else []

    def _on_export_csv(self) -> None:
        if not require_export(self):
            return
        from sme_gend.services.doc_gen_log import export_doc_log_csv

        if not self._attestation_history_access():
            QMessageBox.warning(
                self,
                "Export",
                "Accès refusé — historique réservé aux comptes autorisés.",
            )
            return
        from copy import deepcopy
        entries = deepcopy(self._filtered_entries())
        if not entries:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter l'historique des documents",
            "historique_documents.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(export_doc_log_csv(entries))
        except OSError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        QMessageBox.information(
            self,
            "Export",
            f"{len(entries)} ligne(s) exportée(s) :\n{path}",
        )

    def _on_import_csv(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.doc_gen_log import merge_doc_log_from_csv
        from sme_gend.ui.bg_task import run_with_progress
        from sme_gend.ui.confirm_dialogs import confirm_yes_no

        if not require_permission(
            self,
            "attestations.doc_log",
            message="Seuls les administrateurs peuvent importer l'historique.",
        ):
            return
        if not confirm_yes_no(
            self,
            "Importer l'historique",
            "Les lignes du fichier seront ajoutées à l'historique actuel.\n"
            "Aucune trace existante ne sera effacée.\n"
            "Une ligne déjà présente n'est pas dupliquée.",
            default_no=True,
        ):
            return
        settings = self._settings
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer un historique CSV",
            "",
            "CSV (*.csv)",
        )
        if not path:
            return

        def work():
            return merge_doc_log_from_csv(path, settings=settings)

        result = run_with_progress(
            self,
            "Import historique",
            "Ajout des traces au journal…",
            work,
        )
        if result is None:
            return
        self.refresh()
        QMessageBox.information(
            self,
            "Import",
            f"{result.added} trace(s) ajoutée(s).\n"
            f"{result.already_present} déjà présente(s), conservée(s) sans doublon.\n"
            f"{result.invalid} ligne(s) ignorée(s) (date ou type manquant).",
        )
