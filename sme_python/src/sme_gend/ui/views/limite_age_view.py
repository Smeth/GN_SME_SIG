"""Module Limite d'Âge — attestations de limite d'âge et de service."""

from __future__ import annotations

import tempfile
import zipfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSpinBox,
)

from sme_gend.config.settings import Settings
from sme_gend.models.limite_age_config import (
    GradeLimiteEntry,
    LimiteAgeConfig,
    LimiteAgeDocOptions,
)
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.limite_age_service import (
    LimiteAgeService,
    LimiteDateResult,
    _to_date,
)
from sme_gend.services.soit_transmis_service import (
    _grade_rank,
    sort_limite_age_rows,
    sort_persons_by_grade,
)

_TODAY = date.today()

# ---------------------------------------------------------------------------
# Colonnes du tableau personnel
# ---------------------------------------------------------------------------
_COL_CHECK   = 0
_COL_GRADE   = 1
_COL_NOMS    = 2
_COL_DN      = 3
_COL_DS      = 4
_COL_LIMIT   = 5
_COL_TYPE    = 6
_COL_RESTANT = 7

_HEADERS = [
    "✓", "Grade", "Noms & prénoms", "Date naiss.", "Entrée service",
    "Limite calculée", "Type", "Jours restants",
]

_COLOR_DEPASSE  = QColor("#d32f2f")
_COLOR_CRITIQUE = QColor("#e65100")
_COLOR_PROCHE   = QColor("#f57c00")
_COLOR_MOYEN    = QColor("#f9a825")


# ---------------------------------------------------------------------------
# Modèle de table
# ---------------------------------------------------------------------------

def _person_key(p: ListeGeneraleRow) -> tuple:
    """Clé stable pour identifier une personne indépendamment de sa position dans la liste."""
    return (p.matricule_militaire or "", p.matricule_solde or "", p.noms or "")


class _PersonnelModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        # Clé → (person, result) : persiste à travers les changements de filtre
        self._checked: dict[tuple, tuple[ListeGeneraleRow, LimiteDateResult]] = {}
        self._grade_code_map: dict[str, str] = {}
        self._grades: list = []

    def set_grade_code_map(self, m: dict[str, str]) -> None:
        self._grade_code_map = m

    def set_grades(self, grades: list) -> None:
        self._grades = grades or []

    def load(self, rows: list[tuple[ListeGeneraleRow, LimiteDateResult]]) -> None:
        self.beginResetModel()
        self._rows = rows
        # Ne pas effacer _checked : les coches survivent aux changements de filtre
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_HEADERS)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        person, result = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == _COL_CHECK:
            return Qt.CheckState.Checked if _person_key(person) in self._checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(person, result, col)
        if role == Qt.ItemDataRole.UserRole:
            return (person, result)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._fg_color(result, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (_COL_DN, _COL_DS, _COL_LIMIT, _COL_TYPE, _COL_RESTANT):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == _COL_CHECK:
            person, result = self._rows[index.row()]
            key = _person_key(person)
            checked = (value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value)
            if checked:
                self._checked[key] = (person, result)
            else:
                self._checked.pop(key, None)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def _display(self, p: ListeGeneraleRow, r: LimiteDateResult, col: int) -> str:
        if col == _COL_CHECK:   return ""
        if col == _COL_GRADE:
            full = p.grade or ""
            return self._grade_code_map.get(full, full)
        if col == _COL_NOMS:    return p.noms or ""
        if col == _COL_DN:
            d = _to_date(p.date_naissance)
            return d.strftime("%d/%m/%Y") if d else ""
        if col == _COL_DS:
            d = _to_date(p.date_service)
            return d.strftime("%d/%m/%Y") if d else ""
        if col == _COL_LIMIT:
            return r.limit_date.strftime("%d/%m/%Y") if r.limit_date else "—"
        if col == _COL_TYPE:    return r.limit_type
        if col == _COL_RESTANT:
            if r.limit_date is None:
                return "—"
            delta = (r.limit_date - _TODAY).days
            return f"Dépassé ({-delta} j)" if delta < 0 else f"{delta} j"
        return ""

    def _fg_color(self, r: LimiteDateResult, col: int):
        if col not in (_COL_LIMIT, _COL_RESTANT) or r.limit_date is None:
            return None
        delta = (r.limit_date - _TODAY).days
        if delta < 0:        return QBrush(_COLOR_DEPASSE)
        if delta < 180:      return QBrush(_COLOR_CRITIQUE)
        if delta < 365:      return QBrush(_COLOR_PROCHE)
        if delta < 730:      return QBrush(_COLOR_MOYEN)
        return None

    def get_row(self, row: int) -> tuple[ListeGeneraleRow, LimiteDateResult] | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    # ---- Gestion des coches -------------------------------------------------

    def checked_rows(self) -> list[tuple[ListeGeneraleRow, LimiteDateResult]]:
        """Retourne toutes les personnes cochées, triées par grade puis nom."""
        return sort_limite_age_rows(
            list(self._checked.values()),
            grade_code_map=self._grade_code_map,
            grades=self._grades,
        )

    def checked_count(self) -> int:
        return len(self._checked)

    def check_all(self) -> None:
        """Coche toutes les lignes visibles (liste courante après filtre)."""
        for person, result in self._rows:
            self._checked[_person_key(person)] = (person, result)
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole],
            )

    def uncheck_all(self) -> None:
        """Décoche tout (y compris les personnes filtrées hors de la vue)."""
        self._checked.clear()
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole],
            )


# ---------------------------------------------------------------------------
# Barre de statistiques
# ---------------------------------------------------------------------------

def _stat_chip(label: str, count: int, bg: str, fg: str = "#ffffff") -> QLabel:
    lbl = QLabel(f"  {label} : <b>{count}</b>  ")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:4px;"
        "padding:3px 6px; font-size:12px;"
    )
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return lbl


class _StatsBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lbl_total    = _stat_chip("Total",          0, "#455a64")
        self._lbl_ncfg     = _stat_chip("Non configuré",  0, "#795548")
        self._lbl_depasse  = _stat_chip("Dépassé",        0, "#c62828")
        self._lbl_critique = _stat_chip("< 6 mois",       0, "#e64a19")
        self._lbl_proche   = _stat_chip("< 1 an",         0, "#ef6c00")
        self._lbl_moyen    = _stat_chip("1 - 2 ans",      0, "#f9a825", "#333")
        self._lbl_ok       = _stat_chip("OK (> 2 ans)",   0, "#2e7d32")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 4)
        row.setSpacing(8)
        for w in (self._lbl_total, self._lbl_ncfg, self._lbl_depasse,
                  self._lbl_critique, self._lbl_proche, self._lbl_moyen, self._lbl_ok):
            row.addWidget(w)
        row.addStretch()

    def update_stats(self, rows: list[tuple[ListeGeneraleRow, LimiteDateResult]]) -> None:
        total = len(rows)
        ncfg = depasse = critique = proche = moyen = ok = 0
        for _, r in rows:
            if r.limit_date is None:
                ncfg += 1
                continue
            delta = (r.limit_date - _TODAY).days
            if delta < 0:        depasse  += 1
            elif delta < 180:    critique += 1
            elif delta < 365:    proche   += 1
            elif delta < 730:    moyen    += 1
            else:                ok       += 1

        def _set(lbl, n):
            lbl.setText(lbl.text().split(":")[0] + f" : <b>{n}</b>  ")

        self._lbl_total.setText(f"  Total : <b>{total}</b>  ")
        self._lbl_ncfg.setText(f"  Non configuré : <b>{ncfg}</b>  ")
        self._lbl_depasse.setText(f"  Dépassé : <b>{depasse}</b>  ")
        self._lbl_critique.setText(f"  < 6 mois : <b>{critique}</b>  ")
        self._lbl_proche.setText(f"  < 1 an : <b>{proche}</b>  ")
        self._lbl_moyen.setText(f"  1 - 2 ans : <b>{moyen}</b>  ")
        self._lbl_ok.setText(f"  OK (> 2 ans) : <b>{ok}</b>  ")


# ---------------------------------------------------------------------------
# Panneau détail + actions (côté droit du splitter)
# ---------------------------------------------------------------------------

class _DetailPanel(QWidget):
    """Affiche la fiche de la personne sélectionnée + date + boutons génération."""

    def __init__(self, svc: LimiteAgeService, parent=None):
        super().__init__(parent)
        self._svc = svc
        self._person: ListeGeneraleRow | None = None
        self._result: LimiteDateResult | None = None

        self.setMinimumWidth(280)
        self.setMaximumWidth(360)

        # Page 0 : invite
        self._page_empty = QWidget()
        lbl_empty = QLabel("Sélectionnez une personne\ndans la liste pour voir\nsa fiche et générer\nl'attestation.")
        lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty.setObjectName("dashSubtitle")
        lbl_empty.setWordWrap(True)
        lay_empty = QVBoxLayout(self._page_empty)
        lay_empty.addStretch()
        lay_empty.addWidget(lbl_empty)
        lay_empty.addStretch()

        # Page 1 : fiche
        self._page_detail = QWidget()
        self._f_grade  = QLabel()
        self._f_noms   = QLabel()
        self._f_noms.setWordWrap(True)
        self._f_dn     = QLabel()
        self._f_ds     = QLabel()
        self._f_matmil = QLabel()
        self._f_matsol = QLabel()
        self._f_struct = QLabel()
        self._f_struct.setWordWrap(True)

        for w in (self._f_grade, self._f_noms, self._f_dn, self._f_ds,
                  self._f_matmil, self._f_matsol, self._f_struct):
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        grp_pers = QGroupBox("Personnel sélectionné")
        form_pers = QFormLayout(grp_pers)
        form_pers.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_pers.addRow("Grade :",            self._f_grade)
        form_pers.addRow("Noms :",             self._f_noms)
        form_pers.addRow("Date de naissance :", self._f_dn)
        form_pers.addRow("Entrée au service :", self._f_ds)
        form_pers.addRow("Mat. militaire :",    self._f_matmil)
        form_pers.addRow("Mat. solde :",        self._f_matsol)
        form_pers.addRow("Structure :",         self._f_struct)

        # Résultats calcul
        self._r_age_lim  = QLabel()
        self._r_svc_lim  = QLabel()
        self._r_retenue  = QLabel()
        font_bold = self._r_retenue.font()
        font_bold.setBold(True)
        self._r_retenue.setFont(font_bold)

        grp_calc = QGroupBox("Limite calculée")
        form_calc = QFormLayout(grp_calc)
        form_calc.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_calc.addRow("Limite d'âge :",      self._r_age_lim)
        form_calc.addRow("Limite service :",     self._r_svc_lim)
        form_calc.addRow("→ Retenue :",          self._r_retenue)

        # Boutons de génération
        self._btn_docx = QPushButton("Générer Word (.docx)")
        self._btn_docx.clicked.connect(self._gen_docx)

        self._btn_pdf  = QPushButton("Générer PDF")
        self._btn_pdf.clicked.connect(self._gen_pdf)

        self._btn_print = QPushButton("Ouvrir / Imprimer")
        self._btn_print.setToolTip(
            "Génère l'attestation Word et l'ouvre dans Word/LibreOffice\n"
            "pour aperçu et impression avec la mise en forme exacte."
        )
        self._btn_print.clicked.connect(self._print_attest)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        self._btn_zip = QPushButton("Exporter tout (ZIP Word)")
        self._btn_zip.setToolTip(
            "Génère une attestation Word pour chaque personne\n"
            "de la liste filtrée et les regroupe dans un ZIP."
        )
        self._btn_zip.clicked.connect(self._export_zip_requested)

        lay_detail = QVBoxLayout(self._page_detail)
        lay_detail.setSpacing(8)
        lay_detail.setContentsMargins(0, 0, 0, 0)
        lay_detail.addWidget(grp_pers)
        lay_detail.addWidget(grp_calc)
        lay_detail.addWidget(self._btn_docx)
        lay_detail.addWidget(self._btn_pdf)
        lay_detail.addWidget(self._btn_print)
        lay_detail.addWidget(sep)
        lay_detail.addWidget(self._btn_zip)
        lay_detail.addStretch()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_empty)
        self._stack.addWidget(self._page_detail)
        self._stack.setCurrentIndex(0)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(8, 0, 0, 0)
        main_lay.addWidget(self._stack)

    # ---- API publique -------------------------------------------------------

    def set_get_all_rows_fn(self, fn) -> None:
        """Callback pour récupérer toutes les lignes filtrées (export ZIP)."""
        self._get_all_rows = fn

    def show_person(
        self,
        person: ListeGeneraleRow,
        result: LimiteDateResult,
    ) -> None:
        self._person = person
        self._result = result

        self._f_grade.setText(person.grade or "—")
        self._f_noms.setText(person.noms or "—")
        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)
        self._f_dn.setText(dn.strftime("%d/%m/%Y") if dn else "—")
        self._f_ds.setText(ds.strftime("%d/%m/%Y") if ds else "—")
        self._f_matmil.setText(person.matricule_militaire or "—")
        self._f_matsol.setText(person.matricule_solde or "—")
        self._f_struct.setText(person.structure or "—")

        entry = result.grade_entry

        if result.age_limit_date:
            ans = (entry.limite_age if entry else "?")
            self._r_age_lim.setText(
                f"{result.age_limit_date.strftime('%d/%m/%Y')}  ({ans} ans)"
            )
        else:
            self._r_age_lim.setText("—  (non calculé)")

        if result.service_limit_date:
            ans = (entry.limite_service if entry else "?")
            self._r_svc_lim.setText(
                f"{result.service_limit_date.strftime('%d/%m/%Y')}  ({ans} ans)"
            )
        else:
            self._r_svc_lim.setText("—  (non calculé)")

        if result.limit_date:
            delta = (result.limit_date - _TODAY).days
            if delta < 0:
                badge = f"  ⚠ Dépassé de {-delta} j"
            elif delta < 365:
                badge = f"  ⚠ Dans {delta} j"
            else:
                badge = ""
            self._r_retenue.setText(
                f"{result.limit_date.strftime('%d/%m/%Y')}  ({result.limit_type}){badge}"
            )
        else:
            self._r_retenue.setText("Indéterminé — grade non configuré")

        self._stack.setCurrentIndex(1)

    def clear(self) -> None:
        self._person = None
        self._result = None
        self._stack.setCurrentIndex(0)

    # ---- Actions ------------------------------------------------------------

    def _gen_docx(self) -> None:
        if self._person is None:
            return
        noms_safe = (self._person.noms or "inconnu").replace(" ", "_").replace("/", "-")
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer l'attestation Word",
            f"attestation_limite_age_{noms_safe}.docx",
            "Documents Word (*.docx)",
        )
        if not dest:
            return
        try:
            path = self._svc.generate_docx(
                self._person, self._svc.load(), Path(dest), None
            )
            QMessageBox.information(self, "Attestation générée",
                                    f"Document Word enregistré :\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _gen_pdf(self) -> None:
        if self._person is None:
            return
        noms_safe = (self._person.noms or "inconnu").replace(" ", "_").replace("/", "-")
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer l'attestation PDF",
            f"attestation_limite_age_{noms_safe}.pdf",
            "Fichiers PDF (*.pdf)",
        )
        if not dest:
            return
        try:
            path = self._svc.generate_pdf(
                self._person, self._svc.load(), Path(dest), None
            )
            QMessageBox.information(self, "PDF généré", f"PDF enregistré :\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _print_attest(self) -> None:
        if self._person is None:
            return
        import os, tempfile
        cfg = self._svc.load()
        ns  = (self._person.noms or "attestation").replace(" ", "_").replace("/", "-")
        tmp = Path(tempfile.gettempdir()) / f"apercu_{ns}.docx"
        try:
            self._svc.generate_docx(self._person, cfg, tmp, None)
            os.startfile(str(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Erreur impression", str(e))

    def _export_zip_requested(self) -> None:
        import zipfile as _zipfile
        import tempfile as _tempfile
        from sme_gend.ui.bg_task import run_with_progress

        rows_fn = getattr(self, "_get_all_rows", None)
        if rows_fn is None:
            return
        rows = rows_fn()
        if not rows:
            QMessageBox.information(self, "Export ZIP",
                                    "Aucune personne dans la liste filtrée.")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le ZIP",
            "attestations_limite_age.zip", "Archives ZIP (*.zip)",
        )
        if not dest:
            return
        cfg = self._svc.load()
        svc = self._svc

        def do_zip():
            errors: list[str] = []
            ok = 0
            with _tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                with _zipfile.ZipFile(dest, "w", compression=_zipfile.ZIP_DEFLATED) as zf:
                    for person, _ in rows:
                        ns = (person.noms or "p").replace(" ", "_").replace("/", "-")
                        out_path = tmp / f"attestation_{ns}.docx"
                        try:
                            svc.generate_docx(person, cfg, out_path, None)
                            zf.write(str(out_path), out_path.name)
                            ok += 1
                        except Exception as e:
                            errors.append(f"{person.noms or '?'}: {e}")
            return ok, errors

        result = run_with_progress(
            self, "Export ZIP", f"Génération de {len(rows)} attestation(s)…", do_zip
        )
        if result is None:
            return
        ok, errors = result
        msg = f"{ok} attestation(s) exportée(s) dans :\n{dest}"
        if errors:
            msg += f"\n\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Export ZIP terminé", msg)


# ---------------------------------------------------------------------------
# Onglet 1 — Attestations (layout splitter)
# ---------------------------------------------------------------------------

class _AttestationsTab(QWidget):
    def __init__(self, settings: Settings, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = svc
        self._all_rows: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        self._filtered: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        self._grade_code_map: dict[str, str] = {}   # libelle_complet → code_court
        self._grades: list = []
        self._send_to_soit_transmis_fn = None  # callback(persons: list[ListeGeneraleRow])

        # Stats bar
        self._stats = _StatsBar()

        # Filtres
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher (noms, grade, matricule…)")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._combo_grade = QComboBox()
        self._combo_grade.setMinimumWidth(150)
        self._combo_grade.addItem("(Tous les grades)", None)
        self._combo_grade.currentIndexChanged.connect(self._apply_filter)

        self._combo_struct = QComboBox()
        self._combo_struct.setMinimumWidth(160)
        self._combo_struct.addItem("(Toutes les structures)", None)
        self._combo_struct.currentIndexChanged.connect(self._apply_filter)

        self._chk_proche = QCheckBox("Moins de 2 ans")
        self._chk_proche.stateChanged.connect(self._apply_filter)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Grade :"))
        filter_row.addWidget(self._combo_grade)
        filter_row.addWidget(QLabel("Structure :"))
        filter_row.addWidget(self._combo_struct)
        filter_row.addWidget(self._chk_proche)
        filter_row.addStretch()
        filter_row.addWidget(self._search)

        # Tableau
        self._model = _PersonnelModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_NOMS, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(_COL_CHECK,    28)
        self._table.setColumnWidth(_COL_GRADE,   100)
        self._table.setColumnWidth(_COL_DN,       90)
        self._table.setColumnWidth(_COL_DS,       90)
        self._table.setColumnWidth(_COL_LIMIT,   105)
        self._table.setColumnWidth(_COL_TYPE,     80)
        self._table.setColumnWidth(_COL_RESTANT, 110)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._model.dataChanged.connect(self._on_check_changed)
        self._model.modelReset.connect(self._on_check_changed)

        # Barre de sélection en lot
        self._btn_toggle_all = QPushButton("Cocher tout")
        self._btn_toggle_all.setFixedHeight(26)
        self._btn_toggle_all.clicked.connect(self._toggle_check_all)

        self._lbl_checked = QLabel("0 coché(s)")
        self._lbl_checked.setObjectName("dashSubtitle")

        self._btn_gen_checked = QPushButton("Générer sélection (ZIP)")
        self._btn_gen_checked.setEnabled(False)
        self._btn_gen_checked.clicked.connect(self._export_checked_zip)

        self._btn_print_checked = QPushButton("Imprimer les cochés")
        self._btn_print_checked.setEnabled(False)
        self._btn_print_checked.setToolTip(
            "Ouvre chaque attestation cochée dans Word/LibreOffice pour impression."
        )
        self._btn_print_checked.clicked.connect(self._print_checked)

        self._btn_soit_transmis = QPushButton("→ Soit transmis")
        self._btn_soit_transmis.setEnabled(False)
        self._btn_soit_transmis.setToolTip(
            "Envoie les personnes cochées dans l'onglet Soit transmis pour générer la feuille de transmission."
        )
        self._btn_soit_transmis.clicked.connect(self._send_checked_to_soit_transmis)

        check_bar = QHBoxLayout()
        check_bar.setSpacing(6)
        check_bar.addWidget(self._btn_toggle_all)
        check_bar.addWidget(self._lbl_checked)
        check_bar.addStretch()
        check_bar.addWidget(self._btn_soit_transmis)
        check_bar.addWidget(self._btn_print_checked)
        check_bar.addWidget(self._btn_gen_checked)

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        self._btn_refresh = QPushButton("Actualiser la liste")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.clicked.connect(self.load_personnel)

        footer = QHBoxLayout()
        footer.addWidget(self._btn_refresh)
        footer.addStretch()
        footer.addWidget(self._lbl_count)

        # Panneau gauche
        left_panel = QWidget()
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        left_lay.addLayout(filter_row)
        left_lay.addLayout(check_bar)
        left_lay.addWidget(self._table, stretch=1)
        left_lay.addLayout(footer)

        # Panneau détail (droite)
        self._detail = _DetailPanel(svc)
        self._detail.set_get_all_rows_fn(lambda: self._filtered)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 300])

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(6)
        main_lay.addWidget(self._stats)
        main_lay.addWidget(splitter, stretch=1)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ---- Chargement --------------------------------------------------------

    def load_personnel(self) -> None:
        from sme_gend.ui.bg_task import run_with_progress

        settings = self._settings
        svc = self._svc

        def do_load():
            from dataclasses import replace as dc_replace
            from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
            from sme_gend.repositories.grade_repository import GradeRepository
            from sme_gend.repositories.structure_repository import StructureRepository
            cfg     = svc.load()
            rows    = ListeGeneraleRepository(settings).fetch_all()
            grades  = GradeRepository(settings).fetch_all()
            structs = StructureRepository(settings).fetch_all()

            grade_map  = {g.code: g.libelle     for g in grades  if g.code}
            struct_map = {s.code: s.designation for s in structs if s.code}
            resolved: list = []
            for r in rows:
                ng = grade_map.get((r.grade     or "").strip()) or r.grade
                ns = struct_map.get((r.structure or "").strip()) or r.structure
                resolved.append(dc_replace(r, grade=ng, structure=ns))

            grade_code_map = {v: k for k, v in grade_map.items()}
            resolved = sort_persons_by_grade(
                resolved, grade_code_map=grade_code_map, grades=grades
            )
            all_rows = sort_limite_age_rows(
                [(p, svc.calculate_limit(p, cfg)) for p in resolved],
                grade_code_map=grade_code_map,
                grades=grades,
            )
            return all_rows, grade_code_map, grades

        result = run_with_progress(
            self, "Limite d'âge", "Chargement du personnel…", do_load
        )
        if result is None:
            return

        all_rows, grade_code_map, grades = result
        self._grades = grades
        self._grade_code_map = grade_code_map
        self._model.set_grade_code_map(grade_code_map)
        self._model.set_grades(grades)
        self._all_rows = all_rows
        self._stats.update_stats(all_rows)
        self._refresh_combos()
        self._apply_filter()
        self._detail.clear()

    def _refresh_combos(self) -> None:
        grades: list[str] = []
        structs: list[str] = []
        for p, _ in self._all_rows:
            g = (p.grade or "").strip()
            if g and g not in grades: grades.append(g)
            s = (p.structure or "").strip()
            if s and s not in structs: structs.append(s)
        grades.sort(key=lambda g: (_grade_rank(g), g))
        structs.sort()

        prev_g = self._combo_grade.currentData()
        self._combo_grade.blockSignals(True)
        self._combo_grade.clear()
        self._combo_grade.addItem("(Tous les grades)", None)
        for g in grades:
            label = self._grade_code_map.get(g, g)   # affiche le code, filtre sur le libelle
            self._combo_grade.addItem(label, g)
        if prev_g:
            for i in range(self._combo_grade.count()):
                if self._combo_grade.itemData(i) == prev_g:
                    self._combo_grade.setCurrentIndex(i); break
        self._combo_grade.blockSignals(False)

        prev_s = self._combo_struct.currentData()
        self._combo_struct.blockSignals(True)
        self._combo_struct.clear()
        self._combo_struct.addItem("(Toutes les structures)", None)
        for s in structs: self._combo_struct.addItem(s, s)
        if prev_s:
            for i in range(self._combo_struct.count()):
                if self._combo_struct.itemData(i) == prev_s:
                    self._combo_struct.setCurrentIndex(i); break
        self._combo_struct.blockSignals(False)

    def _apply_filter(self) -> None:
        search   = self._search.text().strip().lower()
        grade_f  = self._combo_grade.currentData()
        struct_f = self._combo_struct.currentData()
        proche   = self._chk_proche.isChecked()

        filtered = []
        for p, r in self._all_rows:
            if grade_f  and (p.grade     or "").strip() != grade_f:  continue
            if struct_f and (p.structure or "").strip() != struct_f: continue
            if proche and (r.limit_date is None or (r.limit_date - _TODAY).days > 730):
                continue
            if search:
                hay = " ".join(filter(None, [
                    p.noms, p.grade, p.matricule_solde, p.matricule_militaire, p.structure,
                ])).lower()
                if search not in hay: continue
            filtered.append((p, r))

        filtered = sort_limite_age_rows(
            filtered,
            grade_code_map=self._grade_code_map,
            grades=self._grades,
        )
        self._filtered = filtered
        self._model.load(filtered)
        self._lbl_count.setText(
            f"{len(filtered)} / {len(self._all_rows)} personne(s)"
        )

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._detail.clear()
            return
        data = self._model.get_row(rows[0].row())
        if data:
            self._detail.show_person(*data)

    def set_send_to_soit_transmis_fn(self, fn) -> None:
        """Définit le callback appelé lors du clic sur '→ Soit transmis'."""
        self._send_to_soit_transmis_fn = fn

    def _send_checked_to_soit_transmis(self) -> None:
        if self._send_to_soit_transmis_fn is None:
            return
        rows = self._model.checked_rows()
        if not rows:
            return
        persons = [p for p, _ in rows]
        results = [r for _, r in rows]
        self._send_to_soit_transmis_fn(persons, self._grade_code_map, results)

    def _on_check_changed(self) -> None:
        n = self._model.checked_count()
        visible = len(self._filtered)
        self._lbl_checked.setText(f"{n} coché(s)")
        has = n > 0
        self._btn_gen_checked.setEnabled(has)
        self._btn_print_checked.setEnabled(has)
        self._btn_soit_transmis.setEnabled(has and self._send_to_soit_transmis_fn is not None)
        if has:
            self._btn_toggle_all.setText(f"Décocher tout ({n})")
            self._btn_gen_checked.setText(f"Générer {n} sélectionné(s) (ZIP)")
            self._btn_print_checked.setText(f"Imprimer {n} cochés")
        else:
            self._btn_toggle_all.setText(f"Cocher filtre ({visible})")
            self._btn_gen_checked.setText("Générer sélection (ZIP)")
            self._btn_print_checked.setText("Imprimer les cochés")

    def _toggle_check_all(self) -> None:
        if self._model.checked_count() > 0:
            self._model.uncheck_all()
        else:
            self._model.check_all()

    def _print_checked(self) -> None:
        import os, tempfile
        rows = self._model.checked_rows()
        if not rows:
            return
        n = len(rows)
        cfg = self._svc.load()
        tmp = Path(tempfile.gettempdir()) / "attestations_groupees.docx"
        try:
            persons = [p for p, _ in rows]
            self._svc.generate_docx_batch(
                persons, cfg, tmp, None, self._grade_code_map, self._grades
            )
            os.startfile(str(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _export_checked_zip(self) -> None:
        from sme_gend.ui.bg_task import run_with_progress

        rows = self._model.checked_rows()
        if not rows:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le ZIP",
            "attestations_selection.zip", "Archives ZIP (*.zip)",
        )
        if not dest:
            return
        cfg = self._svc.load()
        svc = self._svc

        def do_zip():
            errors: list[str] = []
            ok = 0
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for person, _ in rows:
                        ns = (person.noms or "p").replace(" ", "_").replace("/", "-")
                        out_path = tmp / f"attestation_{ns}.docx"
                        try:
                            svc.generate_docx(person, cfg, out_path, None)
                            zf.write(str(out_path), out_path.name)
                            ok += 1
                        except Exception as e:
                            errors.append(f"{person.noms or '?'}: {e}")
            return ok, errors

        result = run_with_progress(
            self, "Export ZIP", f"Génération de {len(rows)} attestation(s)…", do_zip
        )
        if result is None:
            return
        ok, errors = result
        msg = f"{ok} attestation(s) exportée(s) dans :\n{dest}"
        if errors:
            msg += f"\n\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Export ZIP terminé", msg)


# ---------------------------------------------------------------------------
# Onglet 2 — Configuration des limites par grade
# ---------------------------------------------------------------------------

class _ConfigLimitesTab(QWidget):
    def __init__(self, settings: Settings, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = svc
        self._dirty = False

        hint = QLabel(
            "Les grades affichés proviennent de la base de données (TabGrade). "
            "Définissez la <b>limite d'âge</b> (années depuis la date de naissance) "
            "et la <b>limite de service</b> (années depuis l'entrée au service). "
            "La date effective est le <b>31 décembre</b> de la plus proche des deux années. "
            "Utilisez <b>Synchroniser</b> pour ajouter les grades manquants."
        )
        hint.setWordWrap(True)
        hint.setObjectName("dashSubtitle")
        hint.setTextFormat(Qt.TextFormat.RichText)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ["Grade", "Limite d'âge (ans)", "Limite de service (ans)"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.itemChanged.connect(lambda _: self._touch())

        self._btn_sync   = QPushButton("Synchroniser depuis la base")
        self._btn_sync.setToolTip("Lit TabGrade et ajoute les grades absents de la configuration")
        self._btn_sync.clicked.connect(self._sync_from_db)
        self._btn_reload = QPushButton("Recharger")
        self._btn_reload.clicked.connect(self._reload)
        self._btn_save   = QPushButton("Enregistrer")
        self._btn_save.clicked.connect(self._save)
        self._btn_add    = QPushButton("Ajouter une ligne")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_del    = QPushButton("Supprimer la sélection")
        self._btn_del.clicked.connect(self._del_rows)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self._btn_sync)
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._btn_save)
        bar.addStretch()
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_del)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(hint)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)

        self._load_table()

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    def _touch(self) -> None:
        self._dirty = True

    def load_from_disk(self) -> None:
        self._load_table()

    def _load_table(self) -> None:
        cfg = self._svc.load()
        self._fill_from_cfg(cfg)
        self._dirty = False

    def _fill_from_cfg(self, cfg: LimiteAgeConfig) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for e in cfg.grades:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(e.grade))
            self._table.setItem(r, 1, QTableWidgetItem(str(e.limite_age)))
            self._table.setItem(r, 2, QTableWidgetItem(str(e.limite_service)))
        self._table.blockSignals(False)

    def _gather(self) -> list[GradeLimiteEntry]:
        entries: list[GradeLimiteEntry] = []
        for r in range(self._table.rowCount()):
            grade = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            if not grade: continue
            try:
                la = int((self._table.item(r, 1).text() if self._table.item(r, 1) else "0").strip() or "0")
                ls = int((self._table.item(r, 2).text() if self._table.item(r, 2) else "0").strip() or "0")
            except ValueError:
                la = ls = 0
            entries.append(GradeLimiteEntry(grade=grade, limite_age=la, limite_service=ls))
        return entries

    def _sync_from_db(self) -> None:
        try:
            cfg, added = self._svc.sync_grades_from_referentiel(self._settings)
        except Exception as e:
            QMessageBox.critical(self, "Synchronisation", str(e))
            return
        if not added:
            QMessageBox.information(self, "Synchronisation",
                "Tous les grades de la base sont déjà dans la configuration.")
            return
        self._fill_from_cfg(cfg)
        self._dirty = False
        QMessageBox.information(self, "Grades ajoutés",
            f"{len(added)} grade(s) ajouté(s) depuis la base :\n"
            + "\n".join(f"  • {g}" for g in added)
            + "\n\nAjustez les limites si nécessaire puis enregistrez.")

    def _reload(self) -> None:
        if self._dirty:
            from sme_gend.ui.confirm_dialogs import confirm_yes_no
            if not confirm_yes_no(self, "Recharger",
                                   "Des modifications non enregistrées seront perdues. Recharger ?",
                                   default_no=True):
                return
        self._load_table()

    def _save(self) -> None:
        cfg = self._svc.load()
        cfg.grades = self._gather()
        if len({e.grade for e in cfg.grades}) != len(cfg.grades):
            QMessageBox.warning(self, "Configuration", "Chaque grade doit être unique.")
            return
        try:
            self._svc.save(cfg)
        except OSError as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._dirty = False
        QMessageBox.information(self, "Enregistré",
                                "Configuration des limites d'âge enregistrée.")

    def _add_row(self) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem("Nouveau grade"))
        self._table.setItem(r, 1, QTableWidgetItem("55"))
        self._table.setItem(r, 2, QTableWidgetItem("35"))
        self._table.editItem(self._table.item(r, 0))
        self._dirty = True

    def _del_rows(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Suppression", "Sélectionnez une ou plusieurs lignes.")
            return
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        if not confirm_yes_no(self, "Supprimer", f"Supprimer {len(rows)} ligne(s) ?", default_no=True):
            return
        for r in rows:
            self._table.removeRow(r)
        self._dirty = True


# ---------------------------------------------------------------------------
# Onglet 3 — Options du document
# ---------------------------------------------------------------------------

class _OptionsDocTab(QWidget):
    def __init__(self, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        self._svc = svc
        self._dirty = False

        self._signataire     = QLineEdit()
        self._signataire_nom = QLineEdit()
        self._ville          = QLineEdit()
        self._validite       = QSpinBox()
        self._validite.setRange(1, 12)
        self._validite.setSuffix(" mois")

        for w in (self._signataire, self._signataire_nom, self._ville):
            w.textChanged.connect(self._touch)
        self._validite.valueChanged.connect(self._touch)

        grp = QGroupBox("En-tête et validité de l'attestation")
        form = QFormLayout(grp)
        form.addRow("Signataire (titre complet) :", self._signataire)
        form.addRow("Nom du signataire :", self._signataire_nom)
        form.addRow("Ville de signature :", self._ville)
        form.addRow("Validité :", self._validite)

        self._lois_table = QTableWidget(0, 1)
        self._lois_table.setHorizontalHeaderLabels(["Référence légale"])
        self._lois_table.horizontalHeader().setStretchLastSection(True)
        self._lois_table.setAlternatingRowColors(True)
        self._lois_table.itemChanged.connect(lambda _: self._touch())

        btn_add    = QPushButton("Ajouter une référence")
        btn_add.clicked.connect(self._add_loi)
        btn_del    = QPushButton("Supprimer")
        btn_del.clicked.connect(self._del_loi)
        btn_save   = QPushButton("Enregistrer")
        btn_save.clicked.connect(self._save)
        btn_reload = QPushButton("Recharger")
        btn_reload.clicked.connect(self._reload)

        loi_bar = QHBoxLayout()
        loi_bar.setSpacing(8)
        loi_bar.addWidget(btn_add)
        loi_bar.addWidget(btn_del)
        loi_bar.addStretch()
        loi_bar.addWidget(btn_reload)
        loi_bar.addWidget(btn_save)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(grp)
        lay.addWidget(QLabel("<b>Références légales</b> (imprimées dans l'attestation) :"))
        lay.addLayout(loi_bar)
        lay.addWidget(self._lois_table, stretch=1)

        self._load()

    def _touch(self) -> None:
        self._dirty = True

    def _load(self) -> None:
        o = self._svc.load().options
        self._signataire.blockSignals(True)
        self._signataire_nom.blockSignals(True)
        self._ville.blockSignals(True)
        self._validite.blockSignals(True)
        self._lois_table.blockSignals(True)
        self._signataire.setText(o.signataire_titre)
        self._signataire_nom.setText(o.signataire_nom)
        self._ville.setText(o.ville)
        self._validite.setValue(o.validite_mois)
        self._lois_table.setRowCount(0)
        for loi in o.lois_refs:
            r = self._lois_table.rowCount()
            self._lois_table.insertRow(r)
            self._lois_table.setItem(r, 0, QTableWidgetItem(loi))
        self._signataire.blockSignals(False)
        self._signataire_nom.blockSignals(False)
        self._ville.blockSignals(False)
        self._validite.blockSignals(False)
        self._lois_table.blockSignals(False)
        self._dirty = False

    def _reload(self) -> None:
        if self._dirty:
            from sme_gend.ui.confirm_dialogs import confirm_yes_no
            if not confirm_yes_no(self, "Recharger",
                                   "Des modifications non enregistrées seront perdues. Recharger ?",
                                   default_no=True):
                return
        self._load()

    def _gather_opts(self) -> LimiteAgeDocOptions:
        lois = []
        for r in range(self._lois_table.rowCount()):
            it = self._lois_table.item(r, 0)
            txt = (it.text() if it else "").strip()
            if txt: lois.append(txt)
        return LimiteAgeDocOptions(
            signataire_titre=self._signataire.text().strip(),
            signataire_nom=self._signataire_nom.text().strip(),
            ville=self._ville.text().strip(),
            lois_refs=lois,
            validite_mois=self._validite.value(),
        )

    def _save(self) -> None:
        cfg = self._svc.load()
        cfg.options = self._gather_opts()
        try:
            self._svc.save(cfg)
        except OSError as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._dirty = False
        QMessageBox.information(self, "Enregistré", "Options du document enregistrées.")

    def _add_loi(self) -> None:
        r = self._lois_table.rowCount()
        self._lois_table.insertRow(r)
        self._lois_table.setItem(r, 0, QTableWidgetItem("Loi n°…"))
        self._lois_table.editItem(self._lois_table.item(r, 0))
        self._dirty = True

    def _del_loi(self) -> None:
        rows = sorted({i.row() for i in self._lois_table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Suppression", "Sélectionnez une référence à supprimer.")
            return
        from sme_gend.ui.confirm_dialogs import confirm_yes_no
        if not confirm_yes_no(
            self, "Supprimer",
            f"Supprimer {len(rows)} référence(s) légale(s) ?",
            default_no=True,
        ):
            return
        for r in rows:
            self._lois_table.removeRow(r)
        self._dirty = True


# ---------------------------------------------------------------------------
# Vue principale
# ---------------------------------------------------------------------------

class LimiteAgeView(QWidget):
    """Module Limite d'âge et de service — attestations par grade."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = LimiteAgeService()
        self._grades_synced = False
        self._needs_reload  = True

        title = QLabel("Limite d'Âge et de Service")
        title.setObjectName("dashTitle")

        subtitle = QLabel(
            "Calcul automatique de la date de départ selon la limite d'âge ou de service par grade. "
            "Sélectionnez une personne pour voir sa fiche et générer son attestation."
        )
        subtitle.setObjectName("dashSubtitle")
        subtitle.setWordWrap(True)

        self._tabs = QTabWidget()
        self._tab_attest  = _AttestationsTab(settings, self._svc)
        self._tab_config  = _ConfigLimitesTab(settings, self._svc)
        self._tab_options = _OptionsDocTab(self._svc)

        self._tabs.addTab(self._tab_attest,  "Attestations")
        self._tabs.addTab(self._tab_config,  "Configuration des limites")
        self._tabs.addTab(self._tab_options, "Options du document")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addWidget(self._tabs, stretch=1)

    def set_send_to_soit_transmis_fn(self, fn) -> None:
        self._tab_attest.set_send_to_soit_transmis_fn(fn)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._tab_attest.rebind_settings(settings)
        self._tab_config.rebind_settings(settings)
        self._needs_reload = True

    @property
    def limite_age_service(self):
        """Service LimiteAge partagé (injecté dans SoitTransmisView par shell_window)."""
        return self._svc

    def mark_dirty(self) -> None:
        self._needs_reload = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():
            return
        if not self._grades_synced:
            self._grades_synced = True
            try:
                self._svc.sync_grades_from_referentiel(self._settings)
                self._tab_config.load_from_disk()
            except Exception:
                pass
        if self._needs_reload:
            self._needs_reload = False
            self._tab_attest.load_personnel()
