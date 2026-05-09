"""Module Soit Transmis — génération de la feuille de transmission des dossiers de limite d'âge."""

from __future__ import annotations

import tempfile
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
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteDateResult, _int_to_fr
from sme_gend.services.soit_transmis_service import SoitTransmisService

# ---------------------------------------------------------------------------
# Colonnes du tableau personnel
# ---------------------------------------------------------------------------
_COL_CHECK = 0
_COL_GRADE = 1
_COL_NOMS  = 2

_HEADERS = ["✓", "Grade", "Noms & prénoms"]


def _person_key(p: ListeGeneraleRow) -> tuple:
    return (p.matricule_militaire or "", p.matricule_solde or "", p.noms or "")


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
        self.endResetModel()

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
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
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
        self._search.textChanged.connect(self._apply_filter)

        self._combo_grade = QComboBox()
        self._combo_grade.setMinimumWidth(100)
        self._combo_grade.addItem("(Tous)", None)
        self._combo_grade.currentIndexChanged.connect(self._apply_filter)

        self._combo_urgence = QComboBox()
        self._combo_urgence.setMinimumWidth(110)
        self._combo_urgence.addItem("(Toutes)", None)
        self._combo_urgence.addItem("Dépassé", -1)
        self._combo_urgence.addItem("< 6 mois", 180)
        self._combo_urgence.addItem("< 1 an", 365)
        self._combo_urgence.addItem("1 - 2 ans", 730)
        self._combo_urgence.currentIndexChanged.connect(self._apply_filter)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_row.addWidget(QLabel("Grade :"))
        filter_row.addWidget(self._combo_grade)
        filter_row.addWidget(QLabel("Urgence :"))
        filter_row.addWidget(self._combo_urgence)
        filter_row.addStretch()
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
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 320])

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(8)
        main_lay.addWidget(splitter, stretch=1)

        self._load_config()

    # ── Construction du panneau de config ─────────────────────────────────

    def _build_config_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(380)

        # Destinataire
        self._f_dest_titre = QLineEdit()
        self._f_dest_titre.setPlaceholderText("Titre du destinataire")
        self._f_dest_role = QLineEdit()
        self._f_dest_role.setPlaceholderText("Rôle du destinataire")

        self._f_ville = QLineEdit()
        self._f_ville.setPlaceholderText("Brazzaville")

        grp_dest = QGroupBox("Destinataire & localisation")
        form_dest = QFormLayout(grp_dest)
        form_dest.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_dest.addRow("Titre :", self._f_dest_titre)
        form_dest.addRow("Rôle :", self._f_dest_role)
        form_dest.addRow("Ville :", self._f_ville)

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
        lay.addSpacing(6)
        lay.addWidget(self._btn_gen)
        lay.addWidget(self._btn_open)
        lay.addWidget(self._btn_print_all)
        lay.addStretch()

        return panel

    # ── Config I/O ────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        cfg = self._svc.load()
        self._f_dest_titre.setText(cfg.destinataire_titre)
        self._f_dest_role.setText(cfg.destinataire_role)
        self._f_ville.setText(cfg.ville)

    def _read_config(self) -> SoitTransmisConfig:
        cfg = self._svc.load()
        cfg.destinataire_titre = self._f_dest_titre.text().strip()
        cfg.destinataire_role  = self._f_dest_role.text().strip()
        cfg.ville              = self._f_ville.text().strip() or "Brazzaville"
        return cfg

    def _save_config(self) -> None:
        cfg = self._read_config()
        try:
            self._svc.save(cfg)
            QMessageBox.information(self, "Soit transmis", "Configuration sauvegardée.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    # ── Chargement du personnel ───────────────────────────────────────────

    def load_personnel(self) -> None:
        from sme_gend.ui.bg_task import run_with_progress

        settings = self._settings

        def do_load():
            from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
            from sme_gend.repositories.grade_repository import GradeRepository
            from sme_gend.services.personnel_list_order import sort_liste_generale_default_order
            rows   = ListeGeneraleRepository(settings).fetch_all()
            grades = GradeRepository(settings).fetch_all()
            rows   = sort_liste_generale_default_order(rows, grades)
            return rows

        rows = run_with_progress(
            self, "Soit transmis", "Chargement du personnel…", do_load
        )
        if rows is None:
            rows = []

        self._key_to_orig = {_person_key(p): p for p in rows}
        self._results = {}
        self._model.set_grade_code_map({})
        self._all_rows = rows
        self._refresh_grade_combo()
        self._apply_filter()

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
        self._key_to_orig = {_person_key(p): p for p in persons}
        self._model.set_grade_code_map(grade_code_map or {})
        self._results = {
            _person_key(p): r for p, r in zip(persons, results or [])
        }
        self._all_rows = persons
        self._refresh_grade_combo()
        self._apply_filter()
        self._model.check_all()

    def set_limite_age_service(self, svc) -> None:
        """Reçoit l'instance LimiteAgeService partagée depuis shell_window."""
        self._la_svc = svc

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ── Filtre / recherche ────────────────────────────────────────────────

    def _refresh_grade_combo(self) -> None:
        """Reconstruit le combo de grades depuis _all_rows."""
        prev = self._combo_grade.currentData()
        self._combo_grade.blockSignals(True)
        self._combo_grade.clear()
        self._combo_grade.addItem("(Tous)", None)
        seen: list[str] = []
        gmap = self._model._grade_code_map
        for p in self._all_rows:
            g = (p.grade or "").strip()
            if g and g not in seen:
                seen.append(g)
        seen.sort()
        for g in seen:
            label = gmap.get(g, g)
            self._combo_grade.addItem(label, g)
        # Restaurer la sélection précédente si elle existe encore
        for i in range(self._combo_grade.count()):
            if self._combo_grade.itemData(i) == prev:
                self._combo_grade.setCurrentIndex(i)
                break
        self._combo_grade.blockSignals(False)

    def _apply_filter(self) -> None:
        from datetime import date as _date
        search = self._search.text().strip().upper()
        grade_filter  = self._combo_grade.currentData()
        urgence_limit = self._combo_urgence.currentData()  # None | -1 | 180 | 365 | 730
        today = _date.today()
        filtered: list[ListeGeneraleRow] = []
        gmap = self._model._grade_code_map
        for p in self._all_rows:
            grade_full = (p.grade or "").strip()
            grade_disp = gmap.get(grade_full, grade_full)
            if grade_filter and grade_full != grade_filter:
                continue
            if urgence_limit is not None:
                res = self._results.get(_person_key(p))
                if res is None or res.limit_date is None:
                    continue
                delta = (res.limit_date - today).days
                if urgence_limit == -1:
                    if delta >= 0:
                        continue
                else:
                    if not (0 <= delta < urgence_limit):
                        continue
            if search:
                hay = " ".join([
                    (p.noms or "").upper(),
                    grade_disp.upper(),
                    grade_full.upper(),
                    (p.matricule_militaire or "").upper(),
                    (p.matricule_solde or "").upper(),
                ])
                if search not in hay:
                    continue
            filtered.append(p)
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
        self._btn_gen.setEnabled(has)
        self._btn_open.setEnabled(has)
        self._btn_print_all.setEnabled(has)
        if has:
            words = _int_to_fr(n).capitalize()
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
        """Retourne les versions originales (grades non convertis) des personnes cochées."""
        return [self._key_to_orig.get(_person_key(p), p) for p in display_persons]

    # ── Génération ────────────────────────────────────────────────────────

    def _gmap(self) -> dict[str, str]:
        return self._model._grade_code_map

    def _generate(self) -> None:
        persons = self._model.checked_rows()
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
        try:
            self._svc.generate_docx(persons, cfg, Path(dest), date.today(), self._gmap())
            QMessageBox.information(self, "Soit transmis", f"Document généré :\n{dest}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur génération", str(e))

    def _open_in_word(self) -> None:
        import os
        persons = self._model.checked_rows()
        if not persons:
            return
        cfg = self._read_config()
        tmp = Path(tempfile.gettempdir()) / "apercu_soit_transmis.docx"
        try:
            self._svc.generate_docx(persons, cfg, tmp, date.today(), self._gmap())
            os.startfile(str(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _open_both(self) -> None:
        """Génère et ouvre le Soit transmis + le fichier groupé des attestations."""
        import os

        persons_display = self._model.checked_rows()
        if not persons_display:
            return

        # Récupérer les personnes avec grades complets pour les attestations
        persons_orig = self._orig_persons_for(persons_display)

        cfg_st = self._read_config()
        tmp_st = Path(tempfile.gettempdir()) / "apercu_soit_transmis.docx"
        tmp_at = Path(tempfile.gettempdir()) / "apercu_attestations_groupees.docx"

        errors: list[str] = []

        try:
            self._svc.generate_docx(persons_display, cfg_st, tmp_st, date.today(), self._gmap())
        except Exception as e:
            errors.append(f"Soit transmis : {e}")

        try:
            from sme_gend.services.limite_age_service import LimiteAgeService
            la_svc = self._la_svc or LimiteAgeService()
            cfg_la = la_svc.load()
            la_svc.generate_docx_batch(persons_orig, cfg_la, tmp_at, None)
        except Exception as e:
            errors.append(f"Attestations : {e}")

        if errors:
            QMessageBox.critical(self, "Erreur", "\n".join(errors))
            return

        if tmp_st.exists():
            os.startfile(str(tmp_st))
        if tmp_at.exists():
            os.startfile(str(tmp_at))
