"""Module Soit Transmis — génération de la feuille de transmission des dossiers de limite d'âge."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.ui.shared_config_editor import displayed_config, config_editor_action
from sme_gend.ui.responsive_panels import WrapLayout, ResponsiveSplitter, scroll_panel


from copy import deepcopy
from sme_gend.ui.document_job import preview_document, run_document_job, can_export, require_export
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteDateResult, _int_to_fr
from sme_gend.services.soit_transmis_service import (
    SoitTransmisService,
    sort_persons_by_grade,
)

# ---------------------------------------------------------------------------
# Colonnes du tableau personnel
# ---------------------------------------------------------------------------
_COL_CHECK = 0
_COL_GRADE = 1


def _urgency_matches(limit: date, today: date, code: int) -> bool:
    def anniversary(years):
        try:
            return today.replace(year=today.year + years)
        except ValueError:
            return today.replace(year=today.year + years, day=28)
    if code == -1:
        return limit < today
    if code == 365:
        return today <= limit < anniversary(1)
    if code == 730:
        return anniversary(1) <= limit < anniversary(2)
    return 0 <= (limit - today).days < code
_COL_NOMS  = 2

_HEADERS = ["✓", "Grade", "Noms & prénoms"]


def _person_key(p: ListeGeneraleRow) -> tuple:
    """Clé stable pour identifier une personne indépendamment de sa position dans la liste.

    ``p.id`` (clé primaire ``carriere_situation``) est toujours renseigné et unique —
    contrairement aux matricules, qui peuvent être vides ou partagés (homonymes),
    ce qui provoquait des collisions silencieuses dans ``_checked`` (nombre de
    coché(s) erroné).
    """
    return (p.id,)


# ---------------------------------------------------------------------------
# Modèle de table
# ---------------------------------------------------------------------------

class _Model(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ListeGeneraleRow] = []
        self._checked: dict[tuple, ListeGeneraleRow] = {}
        # grade_code_map : libelle_complet → code_court (pour l'affichage)
        self._grade_code_map: dict[str, str] = {}

    def set_grade_code_map(self, m: dict[str, str]) -> None:
        self._grade_code_map = m

    def load(self, rows: list[ListeGeneraleRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        fresh = {_person_key(p):p for p in rows}
        self._checked.update({key:fresh[key] for key in self._checked if key in fresh})
        self.endResetModel()

    def refresh_checked(self, rows: list[ListeGeneraleRow]) -> None:
        fresh = {_person_key(p):p for p in rows}
        self._checked = {key:fresh[key] for key in self._checked if key in fresh}

    def replace_checked(self, rows: list[ListeGeneraleRow]) -> None:
        self._checked = {_person_key(p):p for p in rows}
        if self._rows:
            self.dataChanged.emit(self.index(0,_COL_CHECK),self.index(len(self._rows)-1,_COL_CHECK),[Qt.ItemDataRole.CheckStateRole])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_HEADERS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        p = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == _COL_CHECK:
            return (
                Qt.CheckState.Checked
                if _person_key(p) in self._checked
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _COL_CHECK:
                return None
            if col == _COL_GRADE:
                full = (p.grade or "").strip()
                return self._grade_code_map.get(full, full)
            if col == _COL_NOMS:
                return (p.noms or "").strip()

        if role == Qt.ItemDataRole.FontRole and col == _COL_NOMS:
            f = QFont()
            f.setBold(True)
            return f

        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == _COL_CHECK:
            p = self._rows[index.row()]
            key = _person_key(p)
            checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value, 2)
            if checked:
                self._checked[key] = p
            else:
                self._checked.pop(key, None)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def checked_rows(self) -> list[ListeGeneraleRow]:
        return list(self._checked.values())

    def checked_count(self) -> int:
        return len(self._checked)

    def check_all(self) -> None:
        for p in self._rows:
            self._checked[_person_key(p)] = p
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole],
            )

    def uncheck_all(self) -> None:
        self._checked.clear()
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole],
            )


# ---------------------------------------------------------------------------
# Vue principale
# ---------------------------------------------------------------------------

class SoitTransmisView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = SoitTransmisService()
        self._la_svc = None  # LimiteAgeService partagé (injecté par shell_window)
        self._grades: list = []
        self._needs_reload = True
        self._fresh, self._loading, self._generation = False, False, 0
        self._loaded_from_attestations = False
        self._all_rows: list[ListeGeneraleRow] = []
        # Personnes originales (grades complets) pour la génération des attestations
        self._key_to_orig: dict[tuple, ListeGeneraleRow] = {}
        # Résultats de limite d'âge (optionnel, transmis depuis Attestations)
        self._results: dict[tuple, LimiteDateResult] = {}

        self._model = _Model()

        # ── Tableau personnel ─────────────────────────────────────────────
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_NOMS, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(_COL_CHECK, 28)
        self._table.setColumnWidth(_COL_GRADE, 80)
        self._model.dataChanged.connect(self._on_check_changed)
        self._model.modelReset.connect(self._on_check_changed)

        # ── Barre de recherche / filtre ───────────────────────────────────
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher (noms, grade…)")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(180)
        self._search.textChanged.connect(self._apply_filter)

        self._combo_grade = NoWheelComboBox()
        self._combo_grade.setMinimumWidth(100)
        self._combo_grade.addItem("(Tous)", None)
        self._combo_grade.currentIndexChanged.connect(self._apply_filter)

        self._combo_urgence = NoWheelComboBox()
        self._combo_urgence.setMinimumWidth(110)
        self._combo_urgence.addItem("(Toutes)", None)
        self._combo_urgence.addItem("Dépassé", -1)
        self._combo_urgence.addItem("< 6 mois", 180)
        self._combo_urgence.addItem("< 1 an", 365)
        self._combo_urgence.addItem("1 - 2 ans", 730)
        self._combo_urgence.currentIndexChanged.connect(self._apply_filter)
        for combo in (self._combo_grade, self._combo_urgence):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        self._combo_urgence.setToolTip(
            "Filtre par date de limite calculée.\n"
            "Disponible après envoi depuis « Attestations et suivi de la retraite » ou chargement BDD "
            "(calcul automatique si le service est actif)."
        )

        filter_row = WrapLayout()
        filter_row.setSpacing(6)
        for label, field in (("Grade :", self._combo_grade), ("Urgence :", self._combo_urgence)):
            group = QWidget()
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(label))
            row.addWidget(field)
            filter_row.addWidget(group)
        filter_row.addWidget(self._search)

        # ── Barre de sélection ────────────────────────────────────────────
        self._btn_toggle = QPushButton("Cocher tout")
        self._btn_toggle.setFixedHeight(26)
        self._btn_toggle.clicked.connect(self._toggle_all)

        self._lbl_checked = QLabel("0 coché(s)")
        self._lbl_checked.setObjectName("dashSubtitle")

        self._btn_refresh = QPushButton("Charger depuis la BDD")
        self._btn_refresh.clicked.connect(self.load_personnel)

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        check_bar = QHBoxLayout()
        check_bar.setSpacing(6)
        check_bar.addWidget(self._btn_toggle)
        check_bar.addWidget(self._lbl_checked)
        check_bar.addStretch()

        footer_bar = QHBoxLayout()
        footer_bar.addWidget(self._btn_refresh)
        footer_bar.addStretch()
        footer_bar.addWidget(self._lbl_count)

        left_panel = QWidget()
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        left_lay.addLayout(filter_row)
        left_lay.addLayout(check_bar)
        left_lay.addWidget(self._table, stretch=1)
        left_lay.addLayout(footer_bar)

        # ── Panneau de configuration ──────────────────────────────────────
        right_panel = self._build_config_panel()

        # ── Splitter ──────────────────────────────────────────────────────
        splitter = ResponsiveSplitter()
        splitter.addWidget(scroll_panel(left_panel))
        splitter.addWidget(scroll_panel(right_panel))
        self._responsive_splitter = splitter
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 320])

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(8)
        self._load_status = QLabel("Personnel non chargé.")
        self._load_status.setWordWrap(True)
        main_lay.addWidget(self._load_status)
        main_lay.addWidget(splitter, stretch=1)

        self._load_config()

    # ── Construction du panneau de config ─────────────────────────────────

    def _build_config_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(0)

        # Destinataire
        self._f_dest_titre = QLineEdit()
        self._f_dest_titre.setPlaceholderText("Titre du destinataire")
        self._f_dest_role = QLineEdit()
        self._f_dest_role.setPlaceholderText("Rôle du destinataire")

        self._f_ville = QLineEdit()
        self._f_ville.setPlaceholderText("Brazzaville")

        self._f_ref_num = QLineEdit()
        self._f_ref_num.setPlaceholderText("Numéro (vide = tirets dans le Word)")

        self._f_ref_suffix = QLineEdit()
        self._f_ref_suffix.setPlaceholderText("/GN/EM/DPF/DGP.-")

        grp_dest = QGroupBox("Destinataire & localisation")
        form_dest = QFormLayout(grp_dest)
        form_dest.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_dest.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form_dest.addRow("Titre :", self._f_dest_titre)
        form_dest.addRow("Rôle :", self._f_dest_role)
        form_dest.addRow("Ville :", self._f_ville)
        form_dest.addRow("N° référence :", self._f_ref_num)
        form_dest.addRow("Suffixe réf. :", self._f_ref_suffix)

        # Aperçu du nombre de dossiers (mise à jour automatique)
        self._lbl_preview = QLabel("— dossiers sélectionnés")
        self._lbl_preview.setWordWrap(True)
        self._lbl_preview.setObjectName("dashSubtitle")
        self._lbl_preview.setStyleSheet("font-style: italic; color: #546e7a; padding: 4px 0;")

        grp_preview = QGroupBox("Aperçu — corps du document")
        lay_preview = QVBoxLayout(grp_preview)
        lay_preview.addWidget(self._lbl_preview)

        # Boutons
        self._btn_save_cfg = QPushButton("Sauvegarder la configuration")
        self._btn_save_cfg.clicked.connect(self._save_config)
        reload_cfg = QPushButton("Recharger la configuration")
        reload_cfg.clicked.connect(self._reload_config_confirmed)

        self._btn_gen = QPushButton("Générer Soit transmis (.docx)")
        self._btn_gen.setEnabled(False)
        self._btn_gen.clicked.connect(self._generate)

        self._btn_open = QPushButton("Ouvrir Soit transmis dans Word")
        self._btn_open.setEnabled(False)
        self._btn_open.clicked.connect(self._open_in_word)

        self._btn_print_all = QPushButton("Ouvrir Soit transmis + Attestations")
        self._btn_print_all.setEnabled(False)
        self._btn_print_all.setToolTip(
            "Génère et ouvre dans Word :\n"
            "• Le document Soit transmis\n"
            "• Le fichier groupé de toutes les attestations cochées"
        )
        self._btn_print_all.clicked.connect(self._open_both)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(grp_dest)
        lay.addWidget(grp_preview)
        lay.addWidget(self._btn_save_cfg)
        lay.addWidget(reload_cfg)
        lay.addSpacing(6)
        lay.addWidget(self._btn_gen)
        lay.addWidget(self._btn_open)
        lay.addWidget(self._btn_print_all)
        lay.addStretch()

        return panel

    # ── Config I/O ────────────────────────────────────────────────────────

    def _reload_config_confirmed(self) -> None:
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        if confirm_yes_no(self,"Recharger","Les modifications de configuration non enregistrées seront perdues. Recharger ?",default_no=True):
            self._load_config()

    @config_editor_action
    def _load_config(self) -> None:
        cfg = self._svc.load()
        self._loaded_config = cfg
        self._f_dest_titre.setText(cfg.destinataire_titre)
        self._f_dest_role.setText(cfg.destinataire_role)
        self._f_ville.setText(cfg.ville)
        self._f_ref_num.setText(cfg.reference_num)
        self._f_ref_suffix.setText(cfg.reference_suffix)
        for field in (self._f_dest_titre, self._f_dest_role, self._f_ville, self._f_ref_num, self._f_ref_suffix):
            field.setCursorPosition(0)
            field.setToolTip(field.text())

    def _read_config(self) -> SoitTransmisConfig:
        cfg = displayed_config(self)
        cfg.destinataire_titre = self._f_dest_titre.text().strip()
        cfg.destinataire_role  = self._f_dest_role.text().strip()
        cfg.ville              = self._f_ville.text().strip() or "Brazzaville"
        cfg.reference_num      = self._f_ref_num.text().strip()
        cfg.reference_suffix   = self._f_ref_suffix.text().strip() or "/GN/EM/DPF/DGP.-"
        return cfg

    @config_editor_action
    def _save_config(self) -> None:
        cfg = self._read_config()
        self._svc.save(cfg)
        self._loaded_config = cfg
        QMessageBox.information(self, "Soit transmis", "Configuration sauvegardée.")

    # ── Chargement du personnel ───────────────────────────────────────────

    def load_personnel(self) -> None:
        from sme_gend.ui.bg_task import run_with_progress

        if self._loading:
            return False
        self._generation += 1
        generation, settings = self._generation, self._settings
        self._loading, self._fresh = True, False
        self._load_status.setText("Chargement du personnel…")
        self._on_check_changed()

        def do_load():
            from dataclasses import replace as dc_replace
            from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
            from sme_gend.repositories.grade_repository import GradeRepository

            grades = GradeRepository(settings).fetch_all()
            rows = ListeGeneraleRepository(settings).fetch_all()
            grade_map = {g.code: g.libelle for g in grades if g.code}
            resolved = []
            for r in rows:
                ng = grade_map.get((r.grade or "").strip()) or r.grade
                resolved.append(dc_replace(r, grade=ng))
            grade_code_map = {v: k for k, v in grade_map.items()}
            ordered = sort_persons_by_grade(
                resolved, grade_code_map=grade_code_map, grades=grades
            )
            return grades, ordered, grade_code_map

        try:
            result = run_with_progress(self, "Soit transmis", "Chargement du personnel…", do_load)
        finally:
            self._loading = False
        if generation != self._generation or settings != self._settings:
            self._on_check_changed()
            return False
        if result is None:
            self._needs_reload = True
            self._load_status.setText("Chargement impossible. Les données précédentes ne sont pas actualisées ; la génération est désactivée. Réessayez avec Charger depuis la BDD.")
            self._on_check_changed()
            return False
        grades, rows, grade_code_map = result

        self._grades = grades
        self._key_to_orig = {_person_key(p): p for p in rows}
        self._loaded_from_attestations = False
        self._results = self._compute_limit_results(rows)
        self._model.set_grade_code_map(grade_code_map)
        self._all_rows = rows
        self._needs_reload = False
        self._fresh = True
        self._model.refresh_checked(rows)
        self._refresh_grade_combo()
        self._update_urgence_combo_state()
        self._apply_filter()
        self._load_status.setText(f"Personnel actualisé : {len(rows)} personne(s).")
        return True

    def load_persons(
        self,
        persons: list[ListeGeneraleRow],
        grade_code_map: dict[str, str] | None = None,
        results: list[LimiteDateResult] | None = None,
    ) -> None:
        """Charge une liste externe (depuis les attestations cochées).

        grade_code_map : {libelle_complet → code_court} transmis par l'onglet Attestations.
        results        : liste des LimiteDateResult correspondant à chaque personne.
        """
        self._generation += 1
        generation, settings = self._generation, self._settings
        self._fresh = False
        self._on_check_changed()
        if not self._grades:
            from sme_gend.repositories.grade_repository import GradeRepository
            from sme_gend.ui.bg_task import run_with_progress
            grades = run_with_progress(self,"Soit transmis","Lecture des grades…",lambda:GradeRepository(settings).fetch_all())
            if grades is None or generation != self._generation or settings != self._settings:
                self.mark_dirty()
                return False
            self._grades = grades
        ordered = sort_persons_by_grade(
            persons,
            grade_code_map=grade_code_map,
            grades=self._grades,
        )
        results_map = {
            _person_key(p): r for p, r in zip(persons, results or [])
        }
        self._key_to_orig = {_person_key(p): p for p in ordered}
        self._model.set_grade_code_map(grade_code_map or {})
        self._results = {
            _person_key(p): results_map[_person_key(p)]
            for p in ordered
            if _person_key(p) in results_map
        }
        self._all_rows = ordered
        self._model.refresh_checked([])
        self._loaded_from_attestations = True
        self._needs_reload = False
        self._fresh = True
        self._refresh_grade_combo()
        self._update_urgence_combo_state()
        self._apply_filter()
        self._model.replace_checked(ordered)
        self._on_check_changed()
        self._load_status.setText(f"Sélection reçue des attestations : {len(ordered)} personne(s).")

    def _compute_limit_results(
        self, persons: list[ListeGeneraleRow]
    ) -> dict[tuple, LimiteDateResult]:
        """Calcule les dates de limite pour activer le filtre urgence."""
        if not self._la_svc or not persons:
            return {}
        try:
            cfg_la = self._la_svc.load(self._settings)
            return {
                _person_key(p): self._la_svc.calculate_limit(p, cfg_la)
                for p in persons
            }
        except Exception:
            return {}

    def set_limite_age_service(self, svc) -> None:
        """Reçoit l'instance LimiteAgeService partagée depuis shell_window."""
        self._la_svc = svc

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._grades = []
        self.mark_dirty()
        self._all_rows, self._results = [], {}
        self._model.refresh_checked([])
        self._model.load([])

    def mark_dirty(self) -> None:
        self._generation += 1
        self._fresh = False
        self._needs_reload = True
        self._loaded_from_attestations = False
        self._load_status.setText("Données non actualisées. Rechargez le personnel avant de générer un document.")
        self._on_check_changed()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():
            return
        if self._needs_reload and not self._loaded_from_attestations:
            self.load_personnel()

    def _has_urgence_data(self) -> bool:
        return bool(self._results)

    def _update_urgence_combo_state(self) -> None:
        has = self._has_urgence_data()
        self._combo_urgence.setEnabled(has)
        if not has and self._combo_urgence.currentData() is not None:
            self._combo_urgence.blockSignals(True)
            self._combo_urgence.setCurrentIndex(0)
            self._combo_urgence.blockSignals(False)

    # ── Filtre / recherche ────────────────────────────────────────────────

    def _refresh_grade_combo(self) -> None:
        """Reconstruit le combo de grades depuis le référentiel + données chargées."""
        from sme_gend.services.grade_label_format import grade_combo_items

        extra: list[str] = []
        for p in self._all_rows:
            g = (p.grade or "").strip()
            if g and g not in extra:
                extra.append(g)

        grade_items = grade_combo_items(self._grades, extra_raw=extra)
        prev = self._combo_grade.currentData()
        self._combo_grade.blockSignals(True)
        self._combo_grade.clear()
        self._combo_grade.addItem("(Tous)", None)
        for label, code in grade_items:
            self._combo_grade.addItem(label, code)
        if prev is not None:
            for i in range(self._combo_grade.count()):
                if self._combo_grade.itemData(i) == prev:
                    self._combo_grade.setCurrentIndex(i)
                    break
        self._combo_grade.blockSignals(False)

    def _apply_filter(self) -> None:
        from datetime import date as _date

        from sme_gend.ui.personnel_list_filters import grade_matches_filter

        search = normalize_search_text(self._search.text().strip().upper())
        grade_filter = self._combo_grade.currentData()
        grade_disp = self._combo_grade.currentText()
        urgence_limit = (
            self._combo_urgence.currentData()
            if self._has_urgence_data()
            else None
        )
        today = _date.today()
        filtered: list[ListeGeneraleRow] = []
        gmap = self._model._grade_code_map
        for p in self._all_rows:
            grade_full = (p.grade or "").strip()
            grade_disp_row = gmap.get(grade_full, grade_full)
            if grade_filter and not grade_matches_filter(
                p.grade,
                grade_filter,
                combo_display=grade_disp,
                grades=self._grades,
            ):
                continue
            if urgence_limit is not None:
                res = self._results.get(_person_key(p))
                if res is None or res.limit_date is None:
                    continue
                if not _urgency_matches(res.limit_date, today, urgence_limit):
                    continue
            if search:
                hay = normalize_search_text(" ".join([
                    (p.noms or "").upper(),
                    grade_disp_row.upper(),
                    grade_full.upper(),
                    (p.matricule_militaire or "").upper(),
                    (p.matricule_solde or "").upper(),
                ]))
                if search not in normalize_search_text(hay):
                    continue
            filtered.append(p)
        filtered = sort_persons_by_grade(
            filtered,
            grade_code_map=self._model._grade_code_map,
            grades=self._grades,
        )
        self._model.load(filtered)
        self._lbl_count.setText(
            f"{len(filtered)} / {len(self._all_rows)} personne(s)"
        )

    # ── Sélection ─────────────────────────────────────────────────────────

    def _toggle_all(self) -> None:
        if self._model.checked_count() > 0:
            self._model.uncheck_all()
        else:
            self._model.check_all()

    def _on_check_changed(self) -> None:
        n = self._model.checked_count()
        self._lbl_checked.setText(f"{n} coché(s)")
        self._btn_toggle.setText("Décocher tout" if n > 0 else "Cocher tout")
        has = n > 0
        ready = has and self._fresh and not self._loading and can_export()
        self._btn_gen.setEnabled(ready)
        self._btn_open.setEnabled(ready)
        self._btn_print_all.setEnabled(ready)
        if has:
            words = _int_to_fr(n)
            self._lbl_preview.setText(
                f"« pour attributions », {words} ({n}) dossiers de limite "
                "d’âge et de service de personnels évoluant dans "
                "différentes structures de la gendarmerie nationale."
            )
            self._btn_gen.setText(f"Générer Soit transmis — {n} dossier(s)")
            self._btn_open.setText(f"Ouvrir Soit transmis — {n} dossier(s)")
            self._btn_print_all.setText(f"Ouvrir Soit transmis + {n} attestation(s)")
        else:
            self._lbl_preview.setText("— dossiers sélectionnés")
            self._btn_gen.setText("Générer Soit transmis (.docx)")
            self._btn_open.setText("Ouvrir Soit transmis dans Word")
            self._btn_print_all.setText("Ouvrir Soit transmis + Attestations")

    # ── Personnes originales (grades complets pour les attestations) ───────

    def _orig_persons_for(self, display_persons: list[ListeGeneraleRow]) -> list[ListeGeneraleRow]:
        """Personnes cochées dans le même ordre (grade / nom) que l'affichage."""
        return display_persons

    # ── Génération ────────────────────────────────────────────────────────

    def _gmap(self) -> dict[str, str]:
        return self._model._grade_code_map

    def _checked_sorted(self) -> list[ListeGeneraleRow]:
        return sort_persons_by_grade(
            self._model.checked_rows(),
            grade_code_map=self._gmap(),
            grades=self._grades,
        )

    def _generate(self) -> None:
        if not require_export(self):
            return
        if not self._fresh or self._loading:
            return
        generation = self._generation
        persons = self._checked_sorted()
        if not persons:
            return
        cfg = self._read_config()
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le soit transmis",
            str(Path.home() / "soit_transmis.docx"),
            "Document Word (*.docx)",
        )
        if not dest:
            return
        if generation != self._generation or not self._fresh:
            return
        if not require_export(self):
            return
        persons, cfg, gmap, grades, settings = deepcopy((persons, cfg, self._gmap(), self._grades, self._settings))
        try:
            if not run_document_job(self, lambda: self._svc.generate_docx(
                persons, cfg, Path(dest), None, gmap, grades, settings=settings
            )):
                return
            QMessageBox.information(self, "Soit transmis", f"Document généré :\n{dest}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur génération", str(e))

    def _open_in_word(self) -> None:
        if not require_export(self):
            return
        if not self._fresh or self._loading:
            return
        import os
        persons = self._checked_sorted()
        if not persons:
            return
        cfg = self._read_config()
        tmp = preview_document("soit_transmis.docx")
        persons, cfg, gmap, grades, settings = deepcopy((persons, cfg, self._gmap(), self._grades, self._settings))
        try:
            if not run_document_job(self, lambda: self._svc.generate_docx(
                persons, cfg, tmp, None, gmap, grades, settings=settings
            )):
                return
            os.startfile(str(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _open_both(self) -> None:
        """Génère et ouvre le Soit transmis + le fichier groupé des attestations."""
        if not require_export(self):
            return
        import os
        if not self._fresh or self._loading:
            return

        persons_display = self._checked_sorted()
        if not persons_display:
            return

        # Récupérer les personnes avec grades complets pour les attestations
        persons_orig = self._orig_persons_for(persons_display)

        cfg_st = self._read_config()
        tmp_st = preview_document("soit_transmis.docx")
        tmp_at = preview_document("attestations.docx")

        persons_display, persons_orig, cfg_st, gmap, grades, settings = deepcopy((
            persons_display, persons_orig, cfg_st, self._gmap(), self._grades, self._settings))
        from sme_gend.services.limite_age_service import LimiteAgeService
        la_svc = self._la_svc or LimiteAgeService()
        def work():
            cfg_la = la_svc.load(settings)
            invalid = [p for p in persons_orig if la_svc.calculate_limit(p, cfg_la).issues]
            if invalid:
                raise ValueError("Les attestations sélectionnées contiennent des dates ou règles invalides.")
            self._svc.generate_docx(persons_display, cfg_st, tmp_st, None, gmap, grades, settings=settings)
            la_svc.generate_docx_batch(persons_orig, cfg_la, tmp_at, None, gmap, grades, settings=settings)
        if not run_document_job(self, work):
            return

        try:
            os.startfile(str(tmp_st))
            os.startfile(str(tmp_at))
        except OSError as exc:
            QMessageBox.critical(self, "Ouverture Word", str(exc))
