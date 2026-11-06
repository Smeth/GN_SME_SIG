"""Avancement au grade — tableau effectif et configuration du barème."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from datetime import date
from html import escape

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.avancement import (
    ACTE_LABELS,
    DIPLOME_LABELS,
    STATUT_ECARTE,
    STATUT_ELIGIBLE,
    STATUT_FENETRE,
    STATUT_HORS,
    STATUT_INCOMPLET,
    STATUT_NOMME,
    STATUT_SORT,
    STATUT_SUIVI,
    AvancementRegle,
    VigilanceRow,
    default_rules,
)

# Masqués par défaut (vue « Pertinent ») : hors barème et suivi lointain.
_VUE_PERTINENT_EXCLURE = frozenset(
    {STATUT_HORS, STATUT_SUIVI, STATUT_NOMME, STATUT_ECARTE}
)
from sme_gend.services.avancement_display import (
    grade_abbr,
    grade_abbr_optional,
    invalidate_grade_refs_cache,
)
from sme_gend.services.avancement_service import AvancementService
from sme_gend.services.avancement_trimestre import trimestre_from_date
from sme_gend.services.date_display import fr_date
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.theme import theme_palette
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.views.avancement.dialogs import edit_regle

_ROLE = Qt.ItemDataRole.UserRole
_HORIZONS = (("6 mois", 183), ("12 mois", 365))
_DOSSIER_OPEN_DEBOUNCE_MS = 80
_TABLE_HEADERS = [
    "Nom et prénom",
    "Matricule",
    "Structure",
    "Grade actuel",
    "Grade suivant",
    "Ancienneté",
    "Services",
    "Diplôme",
    "Prochain galon",
    "Durée restante",
    "Limite art. 98",
    "Statut",
]


def _normalize_lookup(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _can(permission: str) -> bool:
    from sme_gend.services.auth_context import get_app_auth

    auth = get_app_auth()
    if auth is None:
        return True
    return auth.session_has_permission(permission)


class AvancementView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = AvancementService(settings)
        self._rules_snapshot = None
        self._view_data = ViewDataState(stale=True)
        self._rows: list[VigilanceRow] = []
        self._opening_dossier = False
        self._dossier_open_queued = False
        self._pending_vigilance_row: VigilanceRow | None = None
        self._dossier_open_timer = QTimer(self)
        self._dossier_open_timer.setSingleShot(True)
        self._dossier_open_timer.timeout.connect(self._execute_pending_dossier_open)
        self._personnel_service = None
        self._referentiel_service = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        header = QFrame()
        header.setObjectName("avancementHeader")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(10, 8, 10, 8)
        title = QLabel("<h2>Avancement au grade</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        hl.addWidget(title)
        intro = QLabel(
            "Décret n° 2023-1764 — grades en abréviations. "
            "Double-clic sur un agent pour ouvrir sa fiche dossier."
        )
        intro.setWordWrap(True)
        intro.setObjectName("gainPerteSummary")
        hl.addWidget(intro)
        root.addWidget(header)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("avancementTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_tableau_tab(), "Tableau avancement")
        self._tabs.addTab(self._build_config_tab(), "Configuration")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs, stretch=1)

    def _build_tableau_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(4)

        bar = QFrame()
        bar.setObjectName("gainPerteTopBar")
        bar_col = QVBoxLayout(bar)
        bar_col.setContentsMargins(10, 8, 10, 8)
        bar_col.setSpacing(6)
        brow = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setObjectName("gainPerteSearch")
        self._search.setPlaceholderText("Nom, prénom, matricule, structure, grade…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        self._filt_priorite = QComboBox()
        self._filt_priorite.setToolTip(
            "Filtre de travail (la colonne Statut du tableau reste affichée)."
        )
        self._filt_priorite.addItem("Pertinent", "pertinent")
        self._filt_priorite.addItem("Tout l'effectif", "tous")
        self._filt_priorite.addItem("Urgences (limites / dossier)", "urgent")
        self._filt_priorite.addItem("Critères non conformes", "critere_ko")
        self._filt_priorite.addItem("Éligibles au grade suivant", "eligible")
        self._filt_priorite.currentIndexChanged.connect(self._apply_filter)
        self._cible = QComboBox()
        self._cible.addItem("Tous grades suivants", "")
        for regle in default_rules():
            if regle.grade_cible == "MDL":
                continue
            self._cible.addItem(regle.grade_cible, regle.grade_cible)
        self._cible.currentIndexChanged.connect(self._apply_filter)
        self._horizon = QComboBox()
        for label, days in _HORIZONS:
            self._horizon.addItem(label, days)
        self._horizon.setCurrentIndex(1)
        self._horizon.currentIndexChanged.connect(lambda _i: self._reload())
        self._count = QLabel()
        self._count.setObjectName("gainPerteCount")
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self._reload)
        self._btn_dossier = QPushButton("Fiche dossier…")
        self._btn_dossier.setToolTip("Ouvre la fiche de l'agent sélectionné (Entrée ou double-clic).")
        self._btn_dossier.clicked.connect(self._on_open_dossier)
        brow.addWidget(self._search, stretch=1)
        brow.addWidget(QLabel("Priorité"))
        brow.addWidget(self._filt_priorite)
        brow.addWidget(self._cible)
        brow.addWidget(QLabel("Horizon"))
        brow.addWidget(self._horizon)
        brow.addWidget(self._count)
        brow.addWidget(self._btn_dossier)
        brow.addWidget(refresh)
        bar_col.addLayout(brow)

        brow2 = QHBoxLayout()
        self._filt_structure = QComboBox()
        self._filt_structure.setMinimumWidth(160)
        self._filt_structure.addItem("Toutes structures", "")
        self._filt_structure.currentIndexChanged.connect(self._apply_filter)
        self._filt_grade = QComboBox()
        self._filt_grade.addItem("Tous grades actuels", "")
        self._filt_grade.currentIndexChanged.connect(self._apply_filter)
        self._filt_diplome = QComboBox()
        self._filt_diplome.addItem("Diplôme : tous", "")
        self._filt_diplome.addItem("Diplôme non conforme", "ko")
        self._filt_diplome.addItem("Diplôme conforme", "ok")
        self._filt_diplome.currentIndexChanged.connect(self._apply_filter)
        self._filt_echeance = QComboBox()
        self._filt_echeance.addItem("Prochain galon : tous", "")
        self._filt_echeance.addItem("Échu ou aujourd'hui", "echeance_passe")
        self._filt_echeance.addItem("Dans l'horizon", "echeance_horizon")
        self._filt_echeance.currentIndexChanged.connect(self._apply_filter)
        brow2.addWidget(QLabel("Structure"))
        brow2.addWidget(self._filt_structure, stretch=1)
        brow2.addWidget(QLabel("Grade actuel"))
        brow2.addWidget(self._filt_grade)
        brow2.addWidget(self._filt_diplome)
        brow2.addWidget(self._filt_echeance)
        brow2.addStretch()
        bar_col.addLayout(brow2)
        lay.addWidget(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._table = self._make_table(_TABLE_HEADERS, stretch_labels=frozenset({"Structure"}))
        self._table.setColumnWidth(0, 240)
        self._table.setToolTip("Double-clic ou Entrée : ouvrir la fiche dossier personnel.")
        self._table.itemSelectionChanged.connect(self._show_detail)
        self._table.cellDoubleClicked.connect(lambda *_: self._on_open_dossier())
        QShortcut(QKeySequence(Qt.Key.Key_Return), self._table, self._on_open_dossier)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self._table, self._on_open_dossier)
        splitter.addWidget(self._table)
        self._detail = QLabel("Sélectionnez une ligne pour le détail des critères.")
        self._detail.setWordWrap(True)
        self._detail.setTextFormat(Qt.TextFormat.RichText)
        self._detail.setObjectName("avancementLookupCard")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail.setMinimumHeight(56)
        self._detail.setMaximumHeight(110)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 12)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 80])
        lay.addWidget(splitter, stretch=1)
        return page

    def _build_config_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 4)
        codes = ", ".join(f"{k} ({DIPLOME_LABELS[k]})" for k in DIPLOME_LABELS)
        note = QLabel(
            "Règles du barème utilisées pour le tableau (décret n° 2023-1764). "
            "Réservé aux comptes disposant de la permission <b>avancement.regles</b>.<br><br>"
            "<b>Diplômes :</b> les codes saisis sont comparés aux libellés du "
            "<b>dossier agent</b> (diplômes enregistrés + référentiel <i>ref_diplome</i>). "
            "Plusieurs codes séparés par des virgules = condition <b>l'un OU l'autre</b> "
            "(option « ou diplôme supérieur » pour la hiérarchie DQSG/EMS).<br>"
            f"<span style='font-size:11px'>Codes : {escape(codes)}</span>"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setWordWrap(True)
        note.setObjectName("gainPerteSummary")
        lay.addWidget(note)
        self._regles_table = self._make_table(
            [
                "Grade cible",
                "Depuis",
                "Temps au grade",
                "Si école",
                "Services",
                "Âge",
                "Diplôme",
                "Tableau",
                "Acte",
            ],
            stretch_labels=frozenset({"Grade cible"}),
        )
        lay.addWidget(self._regles_table, stretch=1)
        row = QHBoxLayout()
        self._btn_edit_regle = QPushButton("Modifier la règle…")
        self._btn_edit_regle.clicked.connect(self._on_edit_regle)
        self._btn_reset_regles = QPushButton("Rétablir le décret")
        self._btn_reset_regles.clicked.connect(self._on_reset_regles)
        row.addWidget(self._btn_edit_regle)
        row.addWidget(self._btn_reset_regles)
        row.addStretch()
        lay.addLayout(row)
        self._refresh_config_actions()
        return page

    def focus_agent_lookup(self, term: str) -> None:
        needle = (term or "").strip()
        if not needle:
            return
        self._tabs.setCurrentIndex(0)
        self._search.setText(needle)
        self._apply_filter()

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        invalidate_grade_refs_cache()
        self._svc.rebind_settings(settings)
        self._rules_snapshot = None
        self._refresh_config_actions()
        self._view_data.mark_stale()
        if self.isVisible():
            self.on_page_activated()

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        # A shared-data notification must refresh the visible rules table too.
        # Keep the hidden personnel calculation deferred until its tab is opened.
        if self._tabs.currentIndex() == 1:
            self._fill_regles()
        elif self._view_data.needs_load():
            self._reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._fill_regles()
        elif self._view_data.needs_load():
            self._reload()

    def _make_table(
        self,
        headers: list[str],
        *,
        stretch_labels: frozenset[str] | None = None,
    ) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setObjectName("avancementTable")
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setCascadingSectionResizes(True)
        hdr.setSectionsMovable(True)
        hdr.setMinimumSectionSize(48)
        hdr.setDefaultSectionSize(96)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        stretch = stretch_labels or frozenset()
        for label in stretch:
            if label in headers:
                hdr.setSectionResizeMode(headers.index(label), QHeaderView.ResizeMode.Stretch)
        return table

    def _reload(self) -> None:
        today = date.today()
        horizon = int(self._horizon.currentData() or 365)

        def work() -> list[VigilanceRow]:
            return self._svc.vigilance(
                annee=today.year,
                trimestre=trimestre_from_date(today),
                horizon_days=horizon,
            )

        result = run_with_progress(
            self,
            "Avancement",
            "Calcul du tableau…",
            work,
            silent=True,
        )
        if result is None:
            return
        self._rows = result
        self._view_data.finish_load()
        self._refresh_filter_combos()
        self._apply_filter()

    def _agent_blob(self, row: VigilanceRow) -> str:
        gr = grade_abbr(row.agent.grade_raw, settings=self._settings)
        return " ".join(
            (
                row.agent.noms,
                row.agent.matricule or "",
                _normalize_lookup(row.agent.matricule or ""),
                row.agent.structure or "",
                gr,
                row.grade_cible or "",
                row.diplome_label,
                row.statut_label,
            )
        ).casefold()

    def _refresh_filter_combos(self) -> None:
        structures = sorted(
            {(r.agent.structure or "").strip() for r in self._rows if (r.agent.structure or "").strip()},
            key=str.casefold,
        )
        grades = sorted(
            {
                grade_abbr(r.agent.grade_raw, settings=self._settings)
                for r in self._rows
                if (r.agent.grade_raw or "").strip()
            },
            key=str.casefold,
        )

        def refill(combo: QComboBox, placeholder: str, values: list[str], previous: str) -> None:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder, "")
            for val in values:
                combo.addItem(val, val)
            idx = combo.findData(previous)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

        refill(
            self._filt_structure,
            "Toutes structures",
            structures,
            self._filt_structure.currentData() or "",
        )
        refill(
            self._filt_grade,
            "Tous grades actuels",
            grades,
            self._filt_grade.currentData() or "",
        )

    @staticmethod
    def _condition_flag(row: VigilanceRow, code: str) -> bool | None:
        for cond in row.conditions:
            if cond.code == code:
                return cond.ok
        return None

    @staticmethod
    def _has_blocking_criteria(row: VigilanceRow) -> bool:
        if row.statut == STATUT_INCOMPLET:
            return True
        return any(c.ok is False for c in row.conditions)

    def _match_priorite(self, row: VigilanceRow, code: str) -> bool:
        if code in {None, "", "tous"}:
            return True
        if code == "pertinent":
            return row.statut not in _VUE_PERTINENT_EXCLURE
        if code == "urgent":
            return row.statut in {STATUT_FENETRE, STATUT_INCOMPLET}
        if code == "critere_ko":
            return self._has_blocking_criteria(row)
        if code == "eligible":
            return row.statut == STATUT_ELIGIBLE
        return True

    def _apply_filter(self) -> None:
        needle = normalize_search_text(self._search.text().strip().casefold())
        needle_c = _normalize_lookup(self._search.text().strip())
        priorite = self._filt_priorite.currentData()
        cible = self._cible.currentData() or ""
        struct_f = self._filt_structure.currentData() or ""
        grade_f = self._filt_grade.currentData() or ""
        diplome_f = self._filt_diplome.currentData() or ""
        echeance_f = self._filt_echeance.currentData() or ""
        horizon_days = int(self._horizon.currentData() or 365)
        today = date.today()
        search_all = bool(needle or needle_c)
        shown: list[VigilanceRow] = []
        for row in self._rows:
            if cible and row.grade_cible != cible:
                continue
            if struct_f and (row.agent.structure or "").strip() != struct_f:
                continue
            if grade_f:
                gr = grade_abbr(row.agent.grade_raw, settings=self._settings)
                if gr != grade_f:
                    continue
            if diplome_f == "ko" and self._condition_flag(row, "diplome") is not False:
                continue
            if diplome_f == "ok" and self._condition_flag(row, "diplome") is not True:
                continue
            if echeance_f == "echeance_passe":
                if row.echeance is None or row.echeance > today:
                    continue
            elif echeance_f == "echeance_horizon":
                if row.echeance is None:
                    continue
                delta = (row.echeance - today).days
                if delta < 0 or delta > horizon_days:
                    continue
            if not search_all and not self._match_priorite(row, priorite):
                continue
            if needle or needle_c:
                blob = normalize_search_text(self._agent_blob(row))
                mat = _normalize_lookup(row.agent.matricule or "")
                names = _normalize_lookup(row.agent.noms)
                ok = (needle and needle in normalize_search_text(blob)) or (
                    needle_c
                    and (
                        needle_c in mat
                        or needle_c in names
                        or needle_c in _normalize_lookup(blob)
                    )
                )
                if not ok:
                    continue
            shown.append(row)
        shown.sort(
            key=lambda r: (
                STATUT_SORT.get(r.statut, 9),
                r.echeance or date.max,
                r.agent.noms.casefold(),
            )
        )
        self._fill_table(shown)
        self._count.setText(f"{len(shown)} ligne(s)")
        has_sel = self._selected_row() is not None
        self._btn_dossier.setEnabled(has_sel)

    def _days_remaining(self, row: VigilanceRow) -> str:
        if row.echeance is None:
            return "—"
        delta = (row.echeance - date.today()).days
        if delta < 0:
            return f"Échu ({abs(delta)} j.)"
        if delta == 0:
            return "Aujourd'hui"
        return f"{delta} j."

    def _fill_table(self, rows: list[VigilanceRow]) -> None:
        pal = theme_palette()
        colors = {
            "eligible": pal["success"],
            "a_preparer": pal["warning"],
            "fenetre_courte": pal["error"],
            "incomplet": pal["text_dim"],
        }
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = [
                row.agent.noms,
                row.agent.matricule or "—",
                row.agent.structure or "—",
                grade_abbr(row.agent.grade_raw, settings=self._settings),
                grade_abbr_optional(row.grade_cible),
                row.anciennete_label,
                row.services_label,
                row.diplome_label,
                fr_date(row.echeance),
                self._days_remaining(row),
                fr_date(row.limite),
                row.statut_label,
            ]
            name_font = QFont()
            name_font.setWeight(QFont.Weight.DemiBold)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(_ROLE, row)
                    item.setFont(name_font)
                    item.setToolTip(row.agent.noms)
                if col == 11:
                    c = colors.get(row.statut)
                    if c:
                        item.setForeground(QBrush(QColor(c)))
                self._table.setItem(i, col, item)
        self._show_detail()
        self._btn_dossier.setEnabled(self._selected_row() is not None)

    def _ensure_personnel_services(self):
        from sme_gend.services.personnel_service import PersonnelService
        from sme_gend.services.referentiel_service import ReferentielService

        if self._personnel_service is None:
            self._personnel_service = PersonnelService(self._settings)
        if self._referentiel_service is None:
            self._referentiel_service = ReferentielService(self._settings)
        return self._personnel_service, self._referentiel_service

    def _request_open_dossier(self, row: VigilanceRow) -> None:
        if self._opening_dossier or self._dossier_open_queued:
            return
        self._pending_vigilance_row = row
        self._dossier_open_queued = True
        self._dossier_open_timer.start(_DOSSIER_OPEN_DEBOUNCE_MS)

    def _execute_pending_dossier_open(self) -> None:
        row = self._pending_vigilance_row
        self._pending_vigilance_row = None
        self._dossier_open_queued = False
        if row is None or self._opening_dossier:
            return
        carriere_id = (row.agent.carriere_id or "").strip()
        if not carriere_id:
            QMessageBox.information(
                self,
                "Fiche dossier",
                "Identifiant de carrière absent — impossible d'ouvrir le dossier.",
            )
            return
        service, referentiel = self._ensure_personnel_services()
        liste_row = service.get_by_id(carriere_id)
        if liste_row is None:
            QMessageBox.warning(
                self,
                "Fiche dossier",
                "Fiche introuvable en base pour cet agent.",
            )
            return
        from sme_gend.services.auth_guard import session_can_modify_personnel
        from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
        from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog

        st = evaluate_editing_state_for_settings(self._settings)
        self._opening_dossier = True
        try:
            result = open_personnel_dossier_dialog(
                self,
                liste_row,
                referentiel,
                service,
                allow_edit=not st.locked and session_can_modify_personnel(),
                refresh_from_db=True,
                permission_id='avancement.view',
            )
        finally:
            self._opening_dossier = False
        if result.liste_generale_changed:
            self._view_data.mark_stale()
            self._reload()

    def _on_open_dossier(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._request_open_dossier(row)

    def _selected_row(self) -> VigilanceRow | None:
        items = self._table.selectedItems()
        if not items:
            return None
        data = items[0].data(_ROLE)
        return data if isinstance(data, VigilanceRow) else None

    def _show_detail(self) -> None:
        row = self._selected_row()
        if row is None:
            self._detail.setText("Sélectionnez une ligne pour le détail des critères.")
            return
        lines = [
            f"<b>{escape(row.agent.noms)}</b> — {escape(row.message)}",
        ]
        if row.conditions:
            lines.append("<ul>")
            for cond in row.conditions:
                mark = "—" if cond.ok is None else ("oui" if cond.ok else "non")
                lines.append(
                    f"<li><b>{escape(cond.libelle)}</b> ({mark}) — "
                    f"{escape(cond.detail)}</li>"
                )
            lines.append("</ul>")
        self._detail.setText("".join(lines))

    def _fill_regles(self) -> None:
        try:
            book = self._svc.regles()
        except Exception:
            self._rules_snapshot = None
            self._refresh_config_actions()
            QMessageBox.critical(self, "Configuration", "Impossible de charger le barème partagé. "
                "Les lignes affichées sont conservées ; actualisez avant de les modifier.")
            return
        self._rules_snapshot = book.snapshot
        regles = list(book.values())
        regles.sort(key=lambda r: _ladder_index(r.grade_cible))
        self._regles_table.setRowCount(len(regles))
        for index, regle in enumerate(regles):
            age = []
            if regle.age_min is not None:
                age.append(f"min {regle.age_min}")
            if regle.age_max is not None:
                age.append(f"max {regle.age_max}")
            diplomes = " ou ".join(DIPLOME_LABELS.get(c, c) for c in regle.diplomes)
            if regle.diplome_ou_superieur and diplomes:
                diplomes = f"{diplomes} (ou sup.)"
            values = [
                regle.grade_cible,
                grade_abbr_optional(regle.grade_precedent) if regle.grade_precedent else "—",
                "—" if regle.annees_grade is None else f"{regle.annees_grade} ans",
                "—" if regle.annees_grade_ecole is None else f"{regle.annees_grade_ecole} ans",
                "—" if regle.annees_services is None else f"{regle.annees_services} ans",
                ", ".join(age) or "—",
                diplomes or "—",
                "Oui" if regle.inscription_tableau else "Non",
                ACTE_LABELS.get(regle.acte, "—"),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(_ROLE, regle)
                self._regles_table.setItem(index, col, item)
        self._refresh_config_actions()

    def _refresh_config_actions(self) -> None:
        rules = _can("avancement.regles") and self._rules_snapshot is not None
        self._btn_edit_regle.setEnabled(rules)
        self._btn_reset_regles.setEnabled(rules)
        tip = "" if rules else "Permission avancement.regles et chargement du barème requis."
        self._btn_edit_regle.setToolTip(tip)
        self._btn_reset_regles.setToolTip(tip)

    def _on_edit_regle(self) -> None:
        if not _can("avancement.regles") or self._rules_snapshot is None:
            return
        items = self._regles_table.selectedItems()
        if not items:
            QMessageBox.information(self, "Configuration", "Sélectionnez une règle.")
            return
        regle = items[0].data(_ROLE)
        if not isinstance(regle, AvancementRegle):
            return
        draft = regle
        while True:
            edited = edit_regle(self, draft)
            if edited is None:
                return
            outcome = self._run_config_action(lambda: self._svc.save_regle(edited, expected=regle), draft=True)
            if outcome != "conflict":
                return
            # Redisplay the draft for copying/cancelling. Never refresh the
            # expected version implicitly after a conflict.
            draft = edited

    def _on_reset_regles(self) -> None:
        if not _can("avancement.regles") or self._rules_snapshot is None:
            return
        displayed = self._rules_snapshot
        if not confirm_yes_no(
            self,
            "Rétablir le décret",
            "Remplacer les règles enregistrées par le barème du décret n° 2023-1764 ?",
        ):
            return
        self._run_config_action(lambda: self._svc.reset_regles(expected=displayed))

    def _run_config_action(self, fn, *, draft: bool = False) -> str:
        def work():
            try:
                fn()
            except ReferenceConflictError as exc:
                return exc
            # The services return None on success. Use an explicit result so
            # the UI can distinguish it from the progress helper's error None.
            return True

        result = run_with_progress(self, "Avancement", "Enregistrement…", work, silent=True)
        if isinstance(result, ReferenceConflictError):
            show_reference_conflict(self, result, draft=draft)
            return "conflict"
        if result is not True:
            return "failed"
        self._view_data.mark_stale()
        self._fill_regles()
        self._reload()
        return "saved"


AvancementWorkspace = AvancementView


def _ladder_index(code: str) -> int:
    order = [regle.grade_cible for regle in default_rules()]
    try:
        return order.index(code)
    except ValueError:
        return 99
