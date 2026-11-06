"""Module Limite d'Âge — attestations de limite d'âge et de service."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text
from sme_gend.services.person_name_display import format_nom_prenoms, format_person_name

from sme_gend.ui.shared_config_editor import displayed_config, config_editor_action
from sme_gend.ui.responsive_panels import WrapLayout, ResponsiveSplitter, scroll_panel, tab_content


from copy import deepcopy
from sme_gend.ui.document_job import preview_document, run_document_job, can_export, require_export
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QStyledItemDelegate,
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
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox, NoWheelSpinBox
from sme_gend.models.limite_age_config import (
    GradeLimiteEntry,
    LimiteAgeConfig,
    LimiteAgeDocOptions,
    StructureAttestationOverride,
)
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.limite_age_service import (
    LimiteAgeService,
    LimiteDateResult,
    _to_date,
    current_age_years,
    default_attestation_label_for_structure_text,
    match_age_filter,
    preview_structure_attestation,
    sync_structure_format_from_referentiel,
)
from sme_gend.services.soit_transmis_service import (
    sort_limite_age_rows,
    sort_persons_by_grade,
)

def _limite_age_today() -> date:
    return date.today()


def _add_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _is_within_next_two_years(limit: date | None, today: date) -> bool:
    if limit is None or limit < today:
        return False
    return limit <= _add_calendar_years(today, 2)


def _echeance_kwargs(code: object) -> dict:
    from sme_gend.services.retraite_liste import (
        ECHEANCE_ANNEE,
        ECHEANCE_DANS,
        echeance_spec,
    )

    kind, mois, annee = echeance_spec(str(code or ""))
    if kind == ECHEANCE_DANS and mois is not None:
        return {"echeance": ECHEANCE_DANS, "mois": mois}
    if kind == ECHEANCE_ANNEE and annee is not None:
        return {"echeance": ECHEANCE_ANNEE, "annee": annee}
    return {"echeance": kind}


def _print_filtered_personnel_table(
    parent: QWidget,
    title: str,
    headers: tuple[str, ...] | list[str],
    rows: tuple[tuple[str, ...], ...] | list[tuple[str, ...]],
) -> None:
    """Aperçu d'impression d'un tableau (liste filtrée attestations / retraite)."""
    from PySide6.QtGui import QTextDocument
    from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter

    hdr = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
    html = (
        f"<h2>{title}</h2>"
        f"<table border='1' cellspacing='0' cellpadding='4' width='100%'>"
        f"<tr>{hdr}</tr>{body}</table>"
    )
    doc = QTextDocument()
    doc.setHtml(html)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dlg = QPrintPreviewDialog(printer, parent)
    dlg.setWindowTitle(title)
    dlg.paintRequested.connect(doc.print_)
    dlg.exec()


# ---------------------------------------------------------------------------
# Colonnes du tableau personnel
# ---------------------------------------------------------------------------
_COL_CHECK   = 0
_COL_GRADE   = 1
_COL_NOMS    = 2
_COL_DN      = 3
_COL_AGE     = 4
_COL_DS      = 5
_COL_LIMIT   = 6
_COL_TYPE    = 7
_COL_RESTANT = 8

_HEADERS = [
    "✓", "Grade", "Noms & prénoms", "Date naiss.", "Âge actuel", "Entrée service",
    "Limite calculée", "Type", "Jours restants",
]

_COLOR_DEPASSE  = QColor("#d32f2f")
_COLOR_CRITIQUE = QColor("#e65100")
_COLOR_PROCHE   = QColor("#f57c00")
_COLOR_MOYEN    = QColor("#f9a825")


def _export_attestations_zip(
    parent: QWidget,
    svc: LimiteAgeService,
    rows: list[tuple[ListeGeneraleRow, LimiteDateResult]],
    settings: Settings,
    *,
    dialog_title: str,
    default_name: str,
) -> None:
    """Génère un ZIP Word pour une liste (personne, résultat limite)."""
    import zipfile as _zipfile
    import tempfile as _tempfile

    from sme_gend.ui.bg_task import run_with_progress

    if not rows:
        QMessageBox.information(parent, "Export ZIP", "Aucune attestation à exporter.")
        return
    dest, _ = QFileDialog.getSaveFileName(
        parent,
        dialog_title,
        default_name,
        "Archives ZIP (*.zip)",
    )
    if not dest:
        return
    cfg = svc.load()
    from copy import deepcopy
    rows = deepcopy(rows)

    def do_zip():
        errors: list[str] = []
        ok = 0
        from sme_gend.services.safe_paths import atomic_destination
        with atomic_destination(Path(dest), temporary_suffix=".zip") as staged:
            with _tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                with _zipfile.ZipFile(staged, "w", compression=_zipfile.ZIP_DEFLATED) as zf:
                    for index, (person, _result) in enumerate(rows, 1):
                        import re
                        ns = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', person.noms or 'p').strip(' .')[:120]
                        out_path = tmp / f"attestation_{index:05d}_{ns}.docx"
                        try:
                            svc.generate_docx(person, cfg, out_path, None, settings, log_generation=False)
                            zf.write(str(out_path), out_path.name)
                            ok += 1
                        except Exception as exc:
                            errors.append(f"{person.noms or '?'}: {exc}")
        from sme_gend.services.doc_gen_log import log_saved_list
        log_saved_list(dest, [None] * ok, doc_type="zip", settings=settings,
            meta={"demandes": len(rows), "echecs": len(errors)})
        return ok, errors

    result = run_with_progress(
        parent,
        "Export ZIP",
        f"Génération de {len(rows)} attestation(s)…",
        do_zip,
    )
    if result is None:
        return
    ok, errors = result
    msg = f"{ok} attestation(s) exportée(s) dans :\n{dest}"
    if errors:
        msg += f"\n\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
    QMessageBox.information(parent, "Export ZIP terminé", msg)


def resolve_zip_export_rows(
    checked: list[tuple[ListeGeneraleRow, LimiteDateResult]],
    filtered: list[tuple[ListeGeneraleRow, LimiteDateResult]],
) -> tuple[list[tuple[ListeGeneraleRow, LimiteDateResult]], str]:
    """Retourne (lignes, mode) — cochés prioritaires, sinon filtre."""
    if checked:
        return checked, "checked"
    return filtered, "filtered"


# ---------------------------------------------------------------------------
# Modèle de table
# ---------------------------------------------------------------------------

def _person_key(p: ListeGeneraleRow) -> tuple:
    """Clé stable pour identifier une personne indépendamment de sa position dans la liste.

    ``p.id`` (clé primaire ``carriere_situation``) est toujours renseigné et unique —
    contrairement aux matricules, qui peuvent être vides ou partagés (homonymes),
    ce qui provoquait des collisions silencieuses dans ``_checked`` (nombre de
    coché(s) erroné).
    """
    return (p.id,)


class _PersonnelModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        # Clé → (person, result) : persiste à travers les changements de filtre
        self._checked: dict[tuple, tuple[ListeGeneraleRow, LimiteDateResult]] = {}
        self._grade_code_map: dict[str, str] = {}
        self._grades: list = []
        self._name_config = None

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
        if role == Qt.ItemDataRole.ToolTipRole and result.issues:
            return "\n".join(result.issues)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._fg_color(result, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (_COL_DN, _COL_AGE, _COL_DS, _COL_LIMIT, _COL_TYPE, _COL_RESTANT):
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
        if col == _COL_NOMS:
            return format_person_name(p, self._name_config) if self._name_config else format_nom_prenoms(p.noms)
        if col == _COL_DN:
            d = _to_date(p.date_naissance)
            return d.strftime("%d/%m/%Y") if d else ""
        if col == _COL_AGE:
            age = current_age_years(p.date_naissance, on=_limite_age_today())
            return f"{age} ans" if age is not None else "—"
        if col == _COL_DS:
            d = _to_date(p.date_service)
            return d.strftime("%d/%m/%Y") if d else ""
        if col == _COL_LIMIT:
            return r.limit_date.strftime("%d/%m/%Y") if r.limit_date else "—"
        if col == _COL_TYPE:    return r.limit_type
        if col == _COL_RESTANT:
            if r.limit_date is None:
                return "—"
            delta = (r.limit_date - _limite_age_today()).days
            return f"Dépassé ({-delta} j)" if delta < 0 else f"{delta} j"
        return ""

    def _fg_color(self, r: LimiteDateResult, col: int):
        if col not in (_COL_LIMIT, _COL_RESTANT) or r.limit_date is None:
            return None
        delta = (r.limit_date - _limite_age_today()).days
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

    def refresh_checked(
        self, fresh_rows: list[tuple[ListeGeneraleRow, LimiteDateResult]]
    ) -> None:
        """Met à jour les fiches cochées après rechargement ; retire les absents."""
        by_key = {_person_key(p): (p, r) for p, r in fresh_rows}
        self._checked = {
            key: by_key[key] for key in self._checked if key in by_key
        }
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole],
            )

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

def _stat_chip(label: str, bg: str, fg: str = "#ffffff", *, tooltip: str = "") -> QLabel:
    lbl = QLabel()
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setObjectName("limiteAgeStatChip")
    lbl.setProperty("statLabel", label)
    lbl.setProperty("statBg", bg)
    lbl.setProperty("statFg", fg)
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lbl.setMinimumHeight(24)
    if tooltip:
        lbl.setToolTip(tooltip)
    _stat_chip_set_count(lbl, 0)
    return lbl


def _stat_chip_set_count(lbl: QLabel, count: int) -> None:
    from html import escape
    label = lbl.property("statLabel") or ""
    bg = lbl.property("statBg") or "#455a64"
    fg = lbl.property("statFg") or "#ffffff"
    lbl.setText(f"{escape(str(label))} <b>{count}</b>")
    lbl.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:4px;"
        "padding:2px 7px; font-size:11px;"
    )


def _compact_filter_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setFixedWidth(64)
    lbl.setObjectName("limiteAgeFilterLabel")
    return lbl


_DETAIL_LABEL_WIDTH = 132


def _detail_form_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setFixedWidth(_DETAIL_LABEL_WIDTH)
    lbl.setObjectName("limiteAgeFormLabel")
    return lbl


def _detail_value_label() -> QLabel:
    lbl = QLabel("—")
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setObjectName("limiteAgeFormValue")
    return lbl


def _configure_detail_form(form: QFormLayout) -> None:
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(6)
    form.setContentsMargins(12, 10, 12, 10)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class _StatsBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lbl_total = _stat_chip("Total", "#455a64")
        self._lbl_ncfg = _stat_chip(
            "N/C", "#795548", tooltip="Non configuré (grade sans limite)"
        )
        self._lbl_depasse = _stat_chip("Dépassé", "#c62828")
        self._lbl_critique = _stat_chip("< 6 m", "#e64a19", tooltip="Moins de 6 mois")
        self._lbl_proche = _stat_chip("< 1 a", "#ef6c00", tooltip="Moins de 1 an")
        self._lbl_moyen = _stat_chip("1-2 a", "#f9a825", "#333", tooltip="Entre 1 et 2 ans")
        self._lbl_ok = _stat_chip("OK", "#2e7d32", tooltip="Plus de 2 ans")

        row = WrapLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for w in (
            self._lbl_total,
            self._lbl_ncfg,
            self._lbl_depasse,
            self._lbl_critique,
            self._lbl_proche,
            self._lbl_moyen,
            self._lbl_ok,
        ):
            row.addWidget(w)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def update_stats(self, rows: list[tuple[ListeGeneraleRow, LimiteDateResult]]) -> None:
        total = len(rows)
        ncfg = depasse = critique = proche = moyen = ok = 0
        for _, r in rows:
            if r.limit_date is None:
                ncfg += 1
                continue
            delta = (r.limit_date - _limite_age_today()).days
            if delta < 0:
                depasse += 1
            elif delta < 180:
                critique += 1
            elif delta < 365:
                proche += 1
            elif delta < 730:
                moyen += 1
            else:
                ok += 1

        _stat_chip_set_count(self._lbl_total, total)
        _stat_chip_set_count(self._lbl_ncfg, ncfg)
        _stat_chip_set_count(self._lbl_depasse, depasse)
        _stat_chip_set_count(self._lbl_critique, critique)
        _stat_chip_set_count(self._lbl_proche, proche)
        _stat_chip_set_count(self._lbl_moyen, moyen)
        _stat_chip_set_count(self._lbl_ok, ok)


# ---------------------------------------------------------------------------
# Panneau détail + actions (côté droit du splitter)
# ---------------------------------------------------------------------------

class _DetailPanel(QWidget):
    """Affiche la fiche de la personne sélectionnée + date + boutons génération."""
    name_format_saved = Signal()

    def __init__(self, svc: LimiteAgeService, settings: Settings, parent=None):
        super().__init__(parent)
        self._svc = svc
        self._settings = settings
        self._person: ListeGeneraleRow | None = None
        self._result: LimiteDateResult | None = None
        self._structure_rows: list = []
        self._name_config = None

        self.setMinimumWidth(0)

        # Page 0 : invite
        self._page_empty = QWidget()
        lbl_empty = QLabel("Sélectionnez une personne dans la liste.")
        lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty.setObjectName("dashSubtitle")
        lbl_empty.setWordWrap(True)
        lay_empty = QVBoxLayout(self._page_empty)
        lay_empty.addStretch()
        lay_empty.addWidget(lbl_empty)
        lay_empty.addStretch()

        # Page 1 : fiche
        self._page_detail = QWidget()
        self._f_grade = _detail_value_label()
        self._f_noms = _detail_value_label()
        self._f_dn = _detail_value_label()
        self._f_age = _detail_value_label()
        self._f_ds = _detail_value_label()
        self._f_matmil = _detail_value_label()
        self._f_matsol = _detail_value_label()
        self._f_struct = _detail_value_label()

        grp_pers = QGroupBox("Personnel sélectionné")
        grp_pers.setObjectName("limiteAgeDetailGroup")
        form_pers = QFormLayout(grp_pers)
        _configure_detail_form(form_pers)
        form_pers.addRow(_detail_form_label("Grade"), self._f_grade)
        form_pers.addRow(_detail_form_label("Noms"), self._f_noms)
        self._btn_name_format = QPushButton("Distinguer nom et prénoms…")
        self._btn_name_format.setToolTip("Préciser les noms de famille composés pour les tableaux et attestations, sur tous les postes.")
        self._btn_name_format.clicked.connect(self._edit_name_format)
        form_pers.addRow('', self._btn_name_format)
        form_pers.addRow(_detail_form_label("Date de naissance"), self._f_dn)
        form_pers.addRow(_detail_form_label("Âge actuel"), self._f_age)
        form_pers.addRow(_detail_form_label("Entrée au service"), self._f_ds)
        form_pers.addRow(_detail_form_label("Mat. militaire"), self._f_matmil)
        form_pers.addRow(_detail_form_label("Mat. solde"), self._f_matsol)
        form_pers.addRow(_detail_form_label("Structure"), self._f_struct)

        # Résultats calcul
        self._r_age_lim = _detail_value_label()
        self._r_svc_lim = _detail_value_label()
        self._r_retenue = _detail_value_label()
        font_bold = self._r_retenue.font()
        font_bold.setBold(True)
        self._r_retenue.setFont(font_bold)

        grp_calc = QGroupBox("Limite calculée")
        grp_calc.setObjectName("limiteAgeDetailGroup")
        form_calc = QFormLayout(grp_calc)
        _configure_detail_form(form_calc)
        form_calc.addRow(_detail_form_label("Limite d'âge"), self._r_age_lim)
        form_calc.addRow(_detail_form_label("Limite service"), self._r_svc_lim)
        form_calc.addRow(_detail_form_label("Retenue"), self._r_retenue)

        # Boutons de génération
        self._btn_docx = QPushButton("Générer Word (.docx)")
        self._btn_docx.clicked.connect(self._gen_docx)

        self._btn_pdf = QPushButton("Générer PDF")
        self._btn_pdf.clicked.connect(self._gen_pdf)

        self._btn_print = QPushButton("Aperçu Word (cette personne)")
        self._btn_print.setToolTip(
            "Génère l'attestation Word de la personne sélectionnée et l'ouvre "
            "dans Word/LibreOffice pour aperçu ou impression unitaire."
        )
        self._btn_print.clicked.connect(self._print_attest)

        btn_box = QVBoxLayout()
        btn_box.setSpacing(6)
        for btn in (self._btn_docx, self._btn_pdf, self._btn_print):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn_box.addWidget(btn)

        lay_detail = QVBoxLayout(self._page_detail)
        lay_detail.setSpacing(10)
        lay_detail.setContentsMargins(0, 0, 0, 0)
        lay_detail.addWidget(grp_pers)
        lay_detail.addWidget(grp_calc)
        lay_detail.addLayout(btn_box)
        lay_detail.addStretch()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_empty)
        self._stack.addWidget(self._page_detail)
        self._stack.setCurrentIndex(0)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.addWidget(self._stack)

    # ---- API publique -------------------------------------------------------

    def _edit_name_format(self):
        if self._person is None:
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.ui.bg_task import run_with_progress
        if not require_permission(self, 'attestations.limite_age'):
            return
        person, settings = self._person, self._settings
        cfg = run_with_progress(self, 'Nom et prénoms', 'Chargement…', lambda: self._svc.load(settings))
        if cfg is None:
            return
        formatted = format_person_name(person, cfg).split()
        boundary = next((i for i, word in enumerate(formatted) if not word.isupper()), len(formatted))
        dialog = QDialog(self)
        dialog.setWindowTitle('Présentation du nom et des prénoms')
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        info = QLabel("Répartissez les mots sans changer l'identité. Exemple : nom « MBOUNGOU MAKITA », prénoms « Jean Paul ». Ce choix est partagé entre les postes.")
        info.setWordWrap(True)
        layout.addWidget(info)
        family = QLineEdit(' '.join(formatted[:boundary]))
        given = QLineEdit(' '.join(formatted[boundary:]))
        form = QFormLayout()
        form.addRow('Nom de famille *', family)
        form.addRow('Prénoms', given)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def save():
            # A failed/conflicting save leaves both fields open for correction.
            from sme_gend.services.person_name_display import set_name_boundary
            draft = deepcopy(cfg)
            try:
                set_name_boundary(draft, person, family.text(), given.text())
            except ValueError as exc:
                QMessageBox.warning(dialog, 'Nom et prénoms', str(exc))
                return
            family_text, given_text = family.text(), given.text()
            result = run_with_progress(dialog, 'Nom et prénoms', 'Enregistrement…',
                lambda: self._svc.save_name_format(cfg, person, family_text, given_text, settings))
            if result is not None:
                dialog.accept()
                self.name_format_saved.emit()
        buttons.accepted.connect(save)
        dialog.exec()

    def set_structure_rows(self, rows: list) -> None:
        self._structure_rows = list(rows or [])

    def show_person(
        self,
        person: ListeGeneraleRow,
        result: LimiteDateResult,
    ) -> None:
        self._person = person
        self._result = result

        self._f_grade.setText(person.grade or "—")
        from sme_gend.services.auth_guard import session_has_permission
        self._btn_name_format.setEnabled(session_has_permission('attestations.limite_age'))
        self._f_noms.setText((format_person_name(person, self._name_config) if self._name_config else format_nom_prenoms(person.noms)) or "—")
        dn = _to_date(person.date_naissance)
        ds = _to_date(person.date_service)
        self._f_dn.setText(dn.strftime("%d/%m/%Y") if dn else "—")
        age = current_age_years(person.date_naissance, on=_limite_age_today())
        self._f_age.setText(f"{age} ans" if age is not None else "—")
        self._f_ds.setText(ds.strftime("%d/%m/%Y") if ds else "—")
        self._f_matmil.setText(person.matricule_militaire or "—")
        self._f_matsol.setText(person.matricule_solde or "—")
        from sme_gend.services.structure_label_format import format_structure_display

        self._f_struct.setText(
            format_structure_display(person.structure, self._structure_rows) or "—"
        )

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
            delta = (result.limit_date - _limite_age_today()).days
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
            self._r_retenue.setText("\n".join(result.issues) or "Indéterminé — grade non configuré")
        for button in (self._btn_docx,self._btn_pdf,self._btn_print):
            button.setEnabled(result.limit_date is not None and not result.issues and can_export())

        self._stack.setCurrentIndex(1)

    def clear(self) -> None:
        self._person = None
        self._result = None
        self._stack.setCurrentIndex(0)

    # ---- Actions ------------------------------------------------------------

    def _gen_docx(self) -> None:
        if not require_export(self):
            return
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
        if not require_export(self):
            return
        try:
            path = self._svc.generate_docx(
                self._person, self._svc.load(self._settings), Path(dest), None, self._settings
            )
            QMessageBox.information(self, "Attestation générée",
                                    f"Document Word enregistré :\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _gen_pdf(self) -> None:
        if not require_export(self):
            return
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
        if not require_export(self):
            return
        try:
            path = self._svc.generate_pdf(
                self._person, self._svc.load(self._settings), Path(dest), None, self._settings
            )
            QMessageBox.information(self, "PDF généré", f"PDF enregistré :\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _print_attest(self) -> None:
        if not require_export(self):
            return
        if self._person is None:
            return
        import os, tempfile
        cfg = self._svc.load(self._settings)
        ns  = (self._person.noms or "attestation").replace(" ", "_").replace("/", "-")
        tmp = preview_document("attestation.docx")
        try:
            self._svc.generate_docx(self._person, cfg, tmp, None, self._settings)
            os.startfile(str(tmp))
        except Exception as e:
            QMessageBox.critical(self, "Erreur impression", str(e))


# ---------------------------------------------------------------------------
# Onglet 1 — Attestations (layout splitter)
# ---------------------------------------------------------------------------

class _AttestationsTab(QWidget):
    def __init__(self, settings: Settings, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = svc
        self._fresh, self._loading, self._generation = False, False, 0
        self._load_status = QLabel("Personnel non chargé.")
        self._load_status.setWordWrap(True)
        self._all_rows: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        self._filtered: list[tuple[ListeGeneraleRow, LimiteDateResult]] = []
        self._grade_code_map: dict[str, str] = {}   # libelle_complet → code_court
        self._grades: list = []
        self._send_to_soit_transmis_fn = None  # callback(persons: list[ListeGeneraleRow])

        # Stats bar
        self._stats = _StatsBar()

        # Filtres
        self._search = QLineEdit()
        self._search.setPlaceholderText("Noms, grade, matricule…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumHeight(30)
        self._search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search.textChanged.connect(self._apply_filter)

        self._combo_grade = NoWheelComboBox()
        self._combo_grade.setMinimumHeight(30)
        self._combo_grade.setMinimumWidth(120)
        self._combo_grade.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo_grade.addItem("(Tous les grades)", None)
        self._combo_grade.currentIndexChanged.connect(self._apply_filter)

        self._combo_struct = NoWheelComboBox()
        self._combo_struct.setMinimumHeight(30)
        self._combo_struct.setMinimumWidth(120)
        self._combo_struct.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo_struct.addItem("(Toutes les structures)", None)
        self._combo_struct.currentIndexChanged.connect(self._apply_filter)
        for combo in (self._combo_grade, self._combo_struct):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self._search.setMinimumWidth(180)

        self._chk_proche = QCheckBox("Moins de 2 ans")
        self._chk_proche.stateChanged.connect(self._apply_filter)

        self._chk_age = QCheckBox("Âge")
        self._chk_age.setToolTip("Filtrer par l'âge actuel (années révolues)")
        self._chk_age.stateChanged.connect(self._apply_filter)
        self._combo_age_op = NoWheelComboBox()
        self._combo_age_op.addItems([">", "<", "="])
        self._combo_age_op.setMinimumWidth(64)
        self._combo_age_op.setMinimumHeight(30)
        self._combo_age_op.currentIndexChanged.connect(self._apply_filter)
        self._spin_age = NoWheelSpinBox()
        self._spin_age.setRange(18, 70)
        self._spin_age.setValue(55)
        self._spin_age.setMinimumSize(68, 30)
        self._spin_age.setToolTip("Âge révolu en années")
        self._spin_age.valueChanged.connect(self._apply_filter)

        self._chk_checked_only = QCheckBox("Cochés seulement")
        self._chk_checked_only.setToolTip(
            "N'affiche que les lignes cochées (après les autres filtres). "
            "Cochez des personnes dans la colonne ✓ ou via « Cocher filtre »."
        )
        self._chk_checked_only.toggled.connect(self._on_checked_only_toggled)

        filter_row1 = WrapLayout()
        filter_row1.setContentsMargins(0, 0, 0, 0)
        filter_row1.setSpacing(8)
        for label, field in (("Grade", self._combo_grade), ("Structure", self._combo_struct), ("Recherche", self._search)):
            group = QWidget()
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(_compact_filter_label(label))
            row.addWidget(field)
            filter_row1.addWidget(group)

        filter_row2 = WrapLayout()
        filter_row2.setContentsMargins(0, 0, 0, 0)
        filter_row2.setSpacing(10)
        age_group = QWidget()
        age_row = QHBoxLayout(age_group)
        age_row.setContentsMargins(0, 0, 0, 0)
        for field in (self._chk_age, self._combo_age_op, self._spin_age):
            age_row.addWidget(field)
        filter_row2.addWidget(age_group)
        filter_row2.addWidget(self._chk_proche)
        filter_row2.addWidget(self._chk_checked_only)

        filters_group = QGroupBox("Critères")
        filters_group.setObjectName("limiteAgeFiltersGroup")
        filters_group_lay = QVBoxLayout(filters_group)
        filters_group_lay.setContentsMargins(10, 8, 10, 8)
        filters_group_lay.setSpacing(6)
        filters_group_lay.addLayout(filter_row1)
        filters_group_lay.addLayout(filter_row2)

        # Tableau
        self._model = _PersonnelModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_NOMS, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(_COL_NOMS, 220)
        self._table.setColumnWidth(_COL_CHECK,    28)
        self._table.setColumnWidth(_COL_GRADE,   100)
        self._table.setColumnWidth(_COL_DN,       90)
        self._table.setColumnWidth(_COL_AGE,      72)
        self._table.setColumnWidth(_COL_DS,       90)
        self._table.setColumnWidth(_COL_LIMIT,   105)
        self._table.setColumnWidth(_COL_TYPE,     80)
        self._table.setColumnWidth(_COL_RESTANT, 110)
        for column in range(1, len(_HEADERS)):
            width = self._table.fontMetrics().horizontalAdvance(_HEADERS[column]) + 36
            self._table.setColumnWidth(column, max(self._table.columnWidth(column), width))
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._model.dataChanged.connect(self._on_check_changed)
        self._model.modelReset.connect(self._on_check_changed)

        self._btn_toggle_all = QPushButton("Cocher tout")
        self._btn_toggle_all.setMinimumHeight(30)
        self._btn_toggle_all.clicked.connect(self._toggle_check_all)

        self._lbl_checked = QLabel("0 coché(s)")
        self._lbl_checked.setObjectName("dashSubtitle")

        self._btn_print_filtered = QPushButton("Imprimer liste")
        self._btn_print_filtered.setMinimumHeight(30)
        self._btn_print_filtered.setToolTip("Imprime les lignes actuellement visibles (filtres appliqués).")
        self._btn_print_filtered.clicked.connect(self._print_filtered_list)

        self._btn_zip = QPushButton("ZIP…")
        self._btn_zip.setEnabled(False)
        self._btn_zip.setMinimumHeight(30)
        self._btn_zip.setToolTip(
            "Exporte les lignes cochées en ZIP Word ; "
            "sans coche, exporte toute la liste filtrée."
        )
        self._btn_zip.clicked.connect(self._export_zip)

        self._btn_print_checked = QPushButton("Imprimer cochés")
        self._btn_print_checked.setEnabled(False)
        self._btn_print_checked.setMinimumHeight(30)
        self._btn_print_checked.setToolTip(
            "Ouvre un document Word unique regroupant toutes les attestations cochées."
        )
        self._btn_print_checked.clicked.connect(self._print_checked)

        self._btn_soit_transmis = QPushButton("→ Soit transmis")
        self._btn_soit_transmis.setEnabled(False)
        self._btn_soit_transmis.setMinimumHeight(30)
        self._btn_soit_transmis.setToolTip(
            "Envoie les personnes cochées dans l'onglet Soit transmis."
        )
        self._btn_soit_transmis.clicked.connect(self._send_checked_to_soit_transmis)

        self._lbl_count = QLabel()
        self._lbl_count.setObjectName("dashSubtitle")

        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.setMinimumHeight(30)
        self._btn_refresh.clicked.connect(self.load_personnel)

        footer = WrapLayout()
        footer.setSpacing(6)
        footer.addWidget(self._btn_refresh)
        footer.addWidget(self._btn_toggle_all)
        footer.addWidget(self._lbl_checked)
        footer.addWidget(self._btn_soit_transmis)
        footer.addWidget(self._btn_print_filtered)
        footer.addWidget(self._btn_print_checked)
        footer.addWidget(self._btn_zip)
        footer.addWidget(self._lbl_count)

        # Panneau gauche
        left_panel = QWidget()
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)
        left_lay.addWidget(filters_group)
        left_lay.addWidget(self._table, stretch=1)
        left_lay.addLayout(footer)

        # Panneau détail (droite)
        self._detail = _DetailPanel(svc, settings)
        self._detail.name_format_saved.connect(self.load_personnel)

        # Splitter
        splitter = ResponsiveSplitter()
        splitter.addWidget(scroll_panel(left_panel))
        splitter.addWidget(scroll_panel(self._detail))
        self._responsive_splitter = splitter
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([820, 280])

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(4)
        main_lay.addWidget(self._load_status)
        main_lay.addWidget(self._stats)
        main_lay.addWidget(splitter, stretch=1)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.mark_stale()
        self._all_rows, self._filtered = [], []
        self._model.refresh_checked([])
        self._model.load([])
        self._detail.clear()

    def mark_stale(self):
        self._generation += 1
        self._fresh = False
        self._detail.clear()
        self._load_status.setText("Données non actualisées. Rechargez avant de générer les attestations.")
        self._on_check_changed()

    # ---- Chargement --------------------------------------------------------

    def load_personnel(self) -> None:
        from sme_gend.ui.bg_task import run_with_progress

        if self._loading:
            return False
        self._generation += 1
        generation, settings = self._generation, self._settings
        self._loading, self._fresh = True, False
        self._detail.clear()
        self._load_status.setText("Chargement du personnel…")
        self._on_check_changed()
        svc = self._svc

        def do_load():
            from dataclasses import replace as dc_replace
            from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
            from sme_gend.repositories.grade_repository import GradeRepository
            from sme_gend.repositories.structure_repository import StructureRepository
            cfg     = svc.load(settings)
            rows    = ListeGeneraleRepository(settings).fetch_all()
            grades  = GradeRepository(settings).fetch_all()
            structs = StructureRepository(settings).fetch_all()

            grade_map  = {g.code: g.libelle     for g in grades  if g.code}
            resolved: list = []
            for r in rows:
                ng = grade_map.get((r.grade or "").strip()) or r.grade
                resolved.append(dc_replace(r, grade=ng, noms=format_person_name(r, cfg)))

            grade_code_map = {v: k for k, v in grade_map.items()}
            resolved = sort_persons_by_grade(
                resolved, grade_code_map=grade_code_map, grades=grades
            )
            all_rows = sort_limite_age_rows(
                [(p, svc.calculate_limit(p, cfg)) for p in resolved],
                grade_code_map=grade_code_map,
                grades=grades,
            )
            return all_rows, grade_code_map, grades, structs, cfg

        try:
            result = run_with_progress(self, "Attestations et suivi de la retraite", "Chargement du personnel…", do_load)
        finally:
            self._loading = False
        if generation != self._generation or settings != self._settings:
            return False
        if result is None:
            self._load_status.setText("Chargement impossible. Les données précédentes ne sont pas actualisées ; la génération est désactivée. Cliquez sur Actualiser pour réessayer.")
            self._on_check_changed()
            return False

        all_rows, grade_code_map, grades, structs, cfg = result
        self._model._name_config = cfg
        self._detail._name_config = cfg
        self._structure_rows = structs
        self._detail.set_structure_rows(structs)
        self._grades = grades
        self._grade_code_map = grade_code_map
        self._model.set_grade_code_map(grade_code_map)
        self._model.set_grades(grades)
        self._model.refresh_checked(all_rows)
        self._all_rows = all_rows
        self._fresh = True
        self._stats.update_stats(all_rows)
        self._refresh_combos()
        self._apply_filter()
        self._detail.clear()
        self._load_status.setText(f"Personnel actualisé : {len(all_rows)} personne(s).")
        return True

    def _refresh_combos(self) -> None:
        from sme_gend.services.grade_label_format import grade_combo_items
        from sme_gend.services.structure_label_format import structure_combo_items

        extra_grades: list[str] = []
        extra_structs: list[str] = []
        for p, _ in self._all_rows:
            g = (p.grade or "").strip()
            if g and g not in extra_grades:
                extra_grades.append(g)
            s = (p.structure or "").strip()
            if s and s not in extra_structs:
                extra_structs.append(s)

        grade_items = grade_combo_items(self._grades, extra_raw=extra_grades)
        struct_items = structure_combo_items(self._structure_rows, extra_codes=extra_structs)

        prev_g = self._combo_grade.currentData()
        self._combo_grade.blockSignals(True)
        self._combo_grade.clear()
        self._combo_grade.addItem("(Tous les grades)", None)
        for label, code in grade_items:
            self._combo_grade.addItem(label, code)
        if prev_g is not None:
            for i in range(self._combo_grade.count()):
                if self._combo_grade.itemData(i) == prev_g:
                    self._combo_grade.setCurrentIndex(i)
                    break
        self._combo_grade.blockSignals(False)

        prev_s = self._combo_struct.currentData()
        self._combo_struct.blockSignals(True)
        self._combo_struct.clear()
        self._combo_struct.addItem("(Toutes les structures)", None)
        for label, code in struct_items:
            self._combo_struct.addItem(label, code)
        if prev_s is not None:
            for i in range(self._combo_struct.count()):
                if self._combo_struct.itemData(i) == prev_s:
                    self._combo_struct.setCurrentIndex(i)
                    break
        self._combo_struct.blockSignals(False)

    def _apply_filter(self) -> None:
        from sme_gend.ui.personnel_list_filters import (
            grade_matches_filter,
            structure_matches_filter,
        )

        search   = normalize_search_text(self._search.text().strip().lower())
        grade_f  = self._combo_grade.currentData()
        struct_f = self._combo_struct.currentData()
        grade_disp = self._combo_grade.currentText()
        proche   = self._chk_proche.isChecked()
        age_f    = self._chk_age.isChecked()
        age_op   = self._combo_age_op.currentText()
        age_val  = self._spin_age.value()

        filtered = []
        for p, r in self._all_rows:
            if grade_f and not grade_matches_filter(
                p.grade,
                grade_f,
                combo_display=grade_disp,
                grades=self._grades,
            ):
                continue
            if struct_f and not structure_matches_filter(p.structure, struct_f):
                continue
            if proche and not _is_within_next_two_years(r.limit_date, _limite_age_today()):
                continue
            if age_f:
                age = current_age_years(p.date_naissance, on=_limite_age_today())
                if not match_age_filter(age, age_op, age_val):
                    continue
            if search:
                hay = normalize_search_text(" ".join(filter(None, [
                    p.noms, p.grade, p.matricule_solde, p.matricule_militaire, p.structure,
                ])).lower())
                if search not in normalize_search_text(hay): continue
            filtered.append((p, r))

        filtered = sort_limite_age_rows(
            filtered,
            grade_code_map=self._grade_code_map,
            grades=self._grades,
        )

        if self._chk_checked_only.isChecked():
            checked = self._model._checked
            filtered = [
                (p, r) for p, r in filtered if _person_key(p) in checked
            ]

        self._filtered = filtered
        self._model.load(filtered)
        if self._chk_checked_only.isChecked():
            self._lbl_count.setText(
                f"{len(filtered)} coché(s) affiché(s) · {len(self._all_rows)} au total"
            )
        else:
            self._lbl_count.setText(
                f"{len(filtered)} / {len(self._all_rows)} personne(s)"
            )

    def _on_checked_only_toggled(self, on: bool) -> None:
        if on and self._model.checked_count() == 0:
            QMessageBox.information(
                self,
                "Cochés seulement",
                "Cochez au moins une personne dans la liste avant d'activer ce filtre.",
            )
            self._chk_checked_only.blockSignals(True)
            self._chk_checked_only.setChecked(False)
            self._chk_checked_only.blockSignals(False)
            return
        self._apply_filter()

    def _on_selection_changed(self) -> None:
        if not self._fresh:
            self._detail.clear()
            return
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
        self._on_check_changed()

    def _send_checked_to_soit_transmis(self) -> None:
        if not self._fresh:
            return
        if self._send_to_soit_transmis_fn is None:
            QMessageBox.warning(
                self,
                "Soit transmis",
                "La liaison avec l'onglet Soit transmis n'est pas encore prête.\n"
                "Fermez et rouvrez « Attestations et suivi de la retraite », ou ouvrez une fois l'onglet Soit transmis.",
            )
            return
        rows = self._model.checked_rows()
        if not rows:
            return
        invalid = [(p,r) for p,r in rows if r.limit_date is None or r.issues]
        if invalid:
            QMessageBox.warning(self,"Sélection à vérifier","Certaines attestations ne peuvent pas être émises :\n"+
                "\n".join(f"{p.noms or p.id} : {' '.join(r.issues) or 'limite indéterminée'}" for p,r in invalid[:10]))
            return
        persons = [p for p, _ in rows]
        results = [r for _, r in rows]
        self._send_to_soit_transmis_fn(persons, self._grade_code_map, results)

    def _print_filtered_list(self) -> None:
        if not require_export(self):
            return
        if not self._fresh:
            return
        headers = tuple(_HEADERS[1:])
        rows: list[tuple[str, ...]] = []
        for row_idx in range(self._model.rowCount()):
            cells: list[str] = []
            for col in range(1, len(_HEADERS)):
                val = self._model.data(
                    self._model.index(row_idx, col),
                    Qt.ItemDataRole.DisplayRole,
                )
                cells.append(str(val) if val is not None else "")
            rows.append(tuple(cells))
        _print_filtered_personnel_table(
            self,
            "Liste des personnes — limite d'âge",
            headers,
            tuple(rows),
        )

    def _on_check_changed(self) -> None:
        n = self._model.checked_count()
        visible = len(self._filtered)
        self._lbl_checked.setText(f"{n} coché(s)")
        has = n > 0
        ready = self._fresh and not self._loading and can_export()
        selected = self._model.checked_rows()
        eligible = lambda pair: pair[1].limit_date is not None and not pair[1].issues
        valid_selection = has and all(eligible(pair) for pair in selected)
        self._btn_print_checked.setEnabled(valid_selection and ready)
        self._btn_soit_transmis.setEnabled(valid_selection and ready)
        self._btn_zip.setEnabled(any(eligible(pair) for pair in (selected if has else self._filtered)) and ready)
        self._btn_print_filtered.setEnabled(visible > 0 and ready)
        if n == 0 and self._chk_checked_only.isChecked():
            self._chk_checked_only.blockSignals(True)
            self._chk_checked_only.setChecked(False)
            self._chk_checked_only.blockSignals(False)
            self._apply_filter()
        elif self._chk_checked_only.isChecked():
            self._apply_filter()
        if has:
            self._btn_toggle_all.setText(f"Décocher tout ({n})")
            self._btn_print_checked.setText(f"Imprimer {n} cochés (doc. groupé)")
            self._btn_zip.setText(f"Exporter ZIP ({n} cochés)")
            self._btn_zip.setToolTip(
                f"Génère un ZIP Word pour les {n} ligne(s) cochée(s)."
            )
        else:
            self._btn_toggle_all.setText(f"Cocher filtre ({visible})")
            self._btn_print_checked.setText("Imprimer cochés (doc. groupé)")
            if visible:
                self._btn_zip.setText(f"Exporter ZIP ({visible} filtrés)")
                self._btn_zip.setToolTip(
                    f"Génère un ZIP Word pour les {visible} personne(s) du filtre actuel."
                )
            else:
                self._btn_zip.setText("Exporter ZIP…")
                self._btn_zip.setToolTip(
                    "Exporte les lignes cochées en ZIP Word ; "
                    "sans coche, exporte toute la liste filtrée."
                )

    def _toggle_check_all(self) -> None:
        if self._model.checked_count() > 0:
            self._model.uncheck_all()
        else:
            self._model.check_all()

    def _export_zip(self) -> None:
        if not self._fresh:
            return
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        checked = self._model.checked_rows()
        rows, mode = resolve_zip_export_rows(checked, self._filtered)
        if mode == "checked":
            title = f"Exporter {len(rows)} attestation(s) cochée(s)"
            default = "attestations_selection.zip"
        else:
            title = f"Exporter {len(rows)} attestation(s) (filtre)"
            default = "attestations_limite_age.zip"
        _export_attestations_zip(
            self,
            self._svc,
            rows,
            self._settings,
            dialog_title=title,
            default_name=default,
        )

    def _print_checked(self) -> None:
        if not require_export(self):
            return
        if not self._fresh:
            return
        import os, tempfile
        rows = self._model.checked_rows()
        if not rows:
            return
        persons, gmap, grades, settings = deepcopy((
            [p for p, _ in rows], self._grade_code_map, self._grades, self._settings))
        tmp = preview_document("attestations.docx")
        def work():
            cfg = self._svc.load(settings)
            self._svc.generate_docx_batch(persons, cfg, tmp, None, gmap, grades, settings)
        if run_document_job(self, work):
            try:
                os.startfile(str(tmp))
            except OSError as exc:
                QMessageBox.critical(self, "Ouverture Word", str(exc))


# ---------------------------------------------------------------------------
# Onglet 2 — Configuration des limites par grade
# ---------------------------------------------------------------------------

class _LimiteCellDelegate(QStyledItemDelegate):
    """Éditeur de cellule lisible : le texte sélectionné (blanc) restait invisible sur fond blanc."""

    _EDITOR_QSS = (
        "background-color: #ffffff; color: #1a237e; "
        "padding: 0px 4px; margin: 0; border: none; border-radius: 0;"
    )

    def __init__(
        self,
        parent=None,
        *,
        numeric_columns: tuple[int, ...] = (),
    ) -> None:
        super().__init__(parent)
        self._numeric_columns = numeric_columns

    def createEditor(self, parent, option, index):  # noqa: ANN001
        del option
        editor = QLineEdit(parent)
        editor.setProperty("inlineTableEdit", True)
        editor.setAutoFillBackground(True)
        editor.setStyleSheet(self._EDITOR_QSS)
        if index.column() in self._numeric_columns:
            editor.setValidator(QIntValidator(0, 120, editor))
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return editor

    def setEditorData(self, editor, index) -> None:  # noqa: ANN001
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            editor.setStyleSheet(self._EDITOR_QSS)

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: ANN001
        del index
        editor.setGeometry(option.rect)


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
            "Utilisez <b>Synchroniser</b> pour ajouter les grades manquants. "
            "Article 98 nouveau — loi n° 17-2023 du 27 mai 2023 : les valeurs sont affichées et verrouillées ; "
            "seuls les autres grades sont modifiables. La lecture ne réenregistre pas les limites. "
            "Ces limites de carrière sont distinctes des durées de service ouvrant droit à pension."
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
        self._table.setItemDelegate(
            _LimiteCellDelegate(self._table, numeric_columns=(1, 2))
        )
        self._table.itemChanged.connect(self._on_config_cell_changed)

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

    def is_dirty(self) -> bool:
        return self._dirty

    def _touch(self) -> None:
        self._dirty = True

    def load_from_disk(self) -> None:
        self._load_table()

    def _on_config_cell_changed(self, item):
        self._touch()
        if item.column() != 0:
            return
        from sme_gend.services.statutory_limits import article98_limits, exact_canonical_grade
        code = exact_canonical_grade(item.text())
        imposed = article98_limits(code) if code else None
        self._table.blockSignals(True)
        try:
            for column in (1,2):
                value = self._table.item(item.row(),column)
                if value is None:
                    continue
                if imposed:
                    value.setText(str(imposed[column-1]))
                    value.setFlags(value.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    value.setToolTip("Barème imposé par le logiciel : valeur non modifiable dans cet écran.")
                else:
                    value.setFlags(value.flags() | Qt.ItemFlag.ItemIsEditable)
                    value.setToolTip("")
        finally:
            self._table.blockSignals(False)

    @config_editor_action
    def _load_table(self) -> None:
        cfg = self._svc.load(self._settings)
        self._fill_from_cfg(cfg)
        self._dirty = False

    def _fill_from_cfg(self, cfg: LimiteAgeConfig) -> None:
        from sme_gend.services.statutory_limits import article98_limits, exact_canonical_grade
        self._loaded_config = cfg
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for e in cfg.grades:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(e.grade))
            code = exact_canonical_grade(e.grade)
            imposed = article98_limits(code) if code else None
            for column,value in enumerate(imposed or (e.limite_age,e.limite_service),1):
                item = QTableWidgetItem(str(value))
                if imposed:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip("Barème imposé par le logiciel : valeur non modifiable dans cet écran.")
                self._table.setItem(r,column,item)
        self._table.blockSignals(False)

    def _gather(self) -> tuple[list[GradeLimiteEntry], list[int]]:
        """Retourne (entrées, lignes invalides) — une ligne est invalide si limite d'âge
        ou de service n'est pas un entier (au lieu de retomber silencieusement à 0)."""
        entries: list[GradeLimiteEntry] = []
        invalid_rows: list[int] = []
        for r in range(self._table.rowCount()):
            grade = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            if not grade: continue
            la_txt = (self._table.item(r, 1).text() if self._table.item(r, 1) else "0").strip() or "0"
            ls_txt = (self._table.item(r, 2).text() if self._table.item(r, 2) else "0").strip() or "0"
            try:
                la = int(la_txt)
                ls = int(ls_txt)
            except ValueError:
                invalid_rows.append(r)
                la = ls = 0
            entries.append(GradeLimiteEntry(grade=grade, limite_age=la, limite_service=ls))
        return entries, invalid_rows

    def _clear_invalid_highlight(self) -> None:
        for r in range(self._table.rowCount()):
            for c in (1, 2):
                it = self._table.item(r, c)
                if it is not None:
                    it.setBackground(QBrush())

    def _sync_from_db(self) -> None:
        if self._dirty:
            from sme_gend.ui.confirm_dialogs import confirm_yes_no
            if not confirm_yes_no(self, 'Synchroniser les grades',
                    'Des modifications ne sont pas enregistrées. Synchroniser et recharger le barème ?', default_no=True):
                return
        try:
            cfg, added = self._svc.sync_grades_from_referentiel(self._settings)
        except Exception as e:
            QMessageBox.critical(self, "Synchronisation", str(e))
            return
        cleanups = getattr(cfg, '_sync_cleanups', ())
        if not added and not cleanups:
            QMessageBox.information(self, "Synchronisation",
                "Tous les grades de la base sont déjà dans la configuration.")
            return
        self._fill_from_cfg(cfg)
        self._dirty = False
        details = []
        if added:
            details.append(f'{len(added)} grade(s) ajouté(s) depuis la base :\n' + '\n'.join(f'  • {g}' for g in added))
        if cleanups:
            details.append('Barème harmonisé :\n' + '\n'.join(f'  • {text}' for text in cleanups))
        if added:
            details.append('Ajustez les limites si nécessaire puis enregistrez.')
        QMessageBox.information(self, 'Synchronisation', '\n\n'.join(details))

    def _reload(self) -> None:
        if self._dirty:
            from sme_gend.ui.confirm_dialogs import confirm_yes_no
            if not confirm_yes_no(self, "Recharger",
                                   "Des modifications non enregistrées seront perdues. Recharger ?",
                                   default_no=True):
                return
        self._load_table()

    @config_editor_action
    def _save(self) -> None:
        cfg = displayed_config(self)
        self._clear_invalid_highlight()
        grades, invalid_rows = self._gather()
        cfg.grades = grades
        if invalid_rows:
            for r in invalid_rows:
                for c in (1, 2):
                    it = self._table.item(r, c)
                    if it is not None:
                        it.setBackground(QBrush(QColor("#ffcdd2")))
            noms = ", ".join(
                (self._table.item(r, 0).text() if self._table.item(r, 0) else "?")
                for r in invalid_rows
            )
            QMessageBox.warning(
                self, "Configuration",
                "Valeur non numérique en surbrillance rouge pour : "
                f"{noms}. Corrigez avant d'enregistrer (aucune valeur n'a été mise à 0 silencieusement).",
            )
            return
        if len({e.grade for e in cfg.grades}) != len(cfg.grades):
            QMessageBox.warning(self, "Configuration", "Chaque grade doit être unique.")
            return
        self._svc.save_grades(cfg,self._settings)
        self._loaded_config = cfg
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

    def is_dirty(self) -> bool:
        return self._dirty

    def _touch(self) -> None:
        self._dirty = True

    @config_editor_action
    def _load(self) -> None:
        cfg = self._svc.load()
        self._loaded_config = cfg
        o = cfg.options
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

    @config_editor_action
    def _save(self) -> None:
        cfg = displayed_config(self)
        cfg.options = self._gather_opts()
        self._svc.save_doc_options(cfg)
        self._loaded_config = cfg
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


def _table_ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


# ---------------------------------------------------------------------------
# Onglet 4 — Formatage des structures (attestations)
# ---------------------------------------------------------------------------

class _StructureFormatTab(QWidget):
    def __init__(self, settings: Settings, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._svc = svc
        self._dirty = False
        self._designations: dict[str, str] = {}

        hint = QLabel(
            "Une ligne par structure du référentiel (code). "
            "Le libellé attestation est proposé automatiquement ; ajustez-le si besoin. "
            "Les fiches personnel utilisent le code structure — même langage partout."
        )
        hint.setWordWrap(True)
        hint.setObjectName("settingsPathsHint")

        self._status = QLabel()
        self._status.setObjectName("settingsPathsHint")

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ["Code", "Désignation (référentiel)", "Libellé attestation"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setItemDelegate(_LimiteCellDelegate(self._table))
        self._table.itemChanged.connect(self._on_table_changed)

        self._preview_combo = QComboBox()
        self._preview_combo.setMinimumWidth(280)
        self._preview_combo.currentIndexChanged.connect(self._update_preview)
        self._preview_result = QLabel("—")
        self._preview_result.setWordWrap(True)
        self._preview_result.setObjectName("limiteAgeFormValue")

        preview_row = QHBoxLayout()
        preview_row.setSpacing(8)
        preview_row.addWidget(QLabel("Structure"))
        preview_row.addWidget(self._preview_combo, stretch=1)
        preview_row.addWidget(QLabel("En service"))
        preview_row.addWidget(self._preview_result, stretch=2)

        preview_grp = QGroupBox("Aperçu")
        preview_grp.setObjectName("limiteAgeDetailGroup")
        preview_lay = QVBoxLayout(preview_grp)
        preview_lay.setContentsMargins(10, 8, 10, 8)
        preview_lay.addLayout(preview_row)

        self._btn_sync = QPushButton("Synchroniser")
        self._btn_sync.setToolTip(
            "Recharge la liste depuis le référentiel Structures (conserve vos libellés personnalisés)"
        )
        self._btn_sync.clicked.connect(self._sync_from_referentiel)
        self._btn_reload = QPushButton("Recharger")
        self._btn_reload.clicked.connect(self._reload)
        self._btn_save = QPushButton("Enregistrer")
        self._btn_save.setObjectName("primaryButton")
        self._btn_save.clicked.connect(self._save)
        self._btn_reset = QPushButton("Réinitialiser la sélection")
        self._btn_reset.setToolTip("Recalcule le libellé attestation automatique pour les lignes sélectionnées")
        self._btn_reset.clicked.connect(self._reset_selected)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self._btn_sync)
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._btn_save)
        bar.addStretch()
        bar.addWidget(self._btn_reset)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(hint)
        lay.addWidget(self._status)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(preview_grp)

        self._merge_from_referentiel(mark_dirty_if_new=False)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    def load_from_disk(self) -> None:
        self._merge_from_referentiel(mark_dirty_if_new=False)

    def is_dirty(self) -> bool:
        return self._dirty

    def _on_table_changed(self, item: QTableWidgetItem | None = None) -> None:
        if item is not None and item.column() != 2:
            return
        self._dirty = True
        self._update_preview()

    def _fetch_designations(self) -> dict[str, str]:
        from sme_gend.repositories.structure_repository import StructureRepository

        try:
            rows = StructureRepository(self._settings).fetch_all()
        except Exception:
            return {}
        return {(r.code or "").strip(): (r.designation or "").strip() for r in rows if r.code}

    def _fill_table(self, overrides: list[StructureAttestationOverride]) -> None:
        self._designations = self._fetch_designations()
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for entry in overrides:
            code = (entry.source or "").strip()
            if not code:
                continue
            r = self._table.rowCount()
            self._table.insertRow(r)
            desig = self._designations.get(code, "—")
            self._table.setItem(r, 0, _table_ro_item(code))
            self._table.setItem(r, 1, _table_ro_item(desig))
            self._table.setItem(r, 2, QTableWidgetItem(entry.attestation))
        self._table.blockSignals(False)
        self._reload_preview_combo()

    def _reload_preview_combo(self) -> None:
        prev = self._preview_combo.currentData()
        self._preview_combo.blockSignals(True)
        self._preview_combo.clear()
        for r in range(self._table.rowCount()):
            code_item = self._table.item(r, 0)
            if code_item is None:
                continue
            code = code_item.text().strip()
            desig = (self._table.item(r, 1).text() if self._table.item(r, 1) else "").strip()
            label = f"{code} — {desig}" if desig and desig != "—" else code
            self._preview_combo.addItem(label, code)
        if self._preview_combo.count():
            idx = max(0, self._preview_combo.findData(prev))
            self._preview_combo.setCurrentIndex(idx)
        self._preview_combo.blockSignals(False)

    def _update_status(self, added: int = 0) -> None:
        n = self._table.rowCount()
        msg = f"{n} structure(s) — synchronisées avec Administration et outils → Structures"
        if added:
            msg += f" · {added} nouvelle(s) — enregistrez pour confirmer"
        self._status.setText(msg)

    @config_editor_action
    def _merge_from_referentiel(self, *, mark_dirty_if_new: bool) -> None:
        cfg = self._svc.load(self._settings)
        overrides, added = sync_structure_format_from_referentiel(self._settings, cfg)
        self._loaded_config = cfg
        self._fill_table(overrides)
        if mark_dirty_if_new and added:
            self._dirty = True
        elif not mark_dirty_if_new:
            self._dirty = False
        self._update_status(added if mark_dirty_if_new else 0)
        self._update_preview()

    def _gather(self) -> list[StructureAttestationOverride]:
        from sme_gend.services.limite_age_service import _grade_key

        existing_cfg = displayed_config(self)
        prep_by_src = {
            _grade_key(o.source): o.prep
            for o in existing_cfg.structure_format.overrides
            if o.prep
        }
        entries: list[StructureAttestationOverride] = []
        for r in range(self._table.rowCount()):
            code = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            att = (self._table.item(r, 2).text() if self._table.item(r, 2) else "").strip()
            if code and att:
                entries.append(
                    StructureAttestationOverride(
                        source=code,
                        attestation=att,
                        prep=prep_by_src.get(_grade_key(code)),
                    )
                )
        return entries

    def _partial_cfg(self) -> LimiteAgeConfig:
        cfg = displayed_config(self)
        cfg.structure_format.overrides = self._gather()
        return cfg

    def _update_preview(self) -> None:
        code = self._preview_combo.currentData()
        if not code:
            self._preview_result.setText("—")
            return
        line = preview_structure_attestation(str(code), self._partial_cfg(), self._settings)
        self._preview_result.setText(line or "—")

    @config_editor_action
    def _sync_from_referentiel(self) -> None:
        cfg = displayed_config(self)
        overrides, added = sync_structure_format_from_referentiel(self._settings, cfg)
        self._loaded_config = cfg
        self._fill_table(overrides)
        self._dirty = True
        self._update_status(added)
        self._update_preview()
        if added:
            QMessageBox.information(
                self,
                "Synchronisation",
                f"{added} structure(s) ajoutée(s) depuis le référentiel.\n"
                "Enregistrez pour conserver la liste à jour.",
            )
        else:
            QMessageBox.information(
                self,
                "Synchronisation",
                "La liste est déjà alignée sur le référentiel.",
            )

    def _reload(self) -> None:
        if self._dirty:
            from sme_gend.ui.confirm_dialogs import confirm_yes_no
            if not confirm_yes_no(
                self, "Recharger",
                "Des modifications non enregistrées seront perdues. Recharger ?",
                default_no=True,
            ):
                return
        self._merge_from_referentiel(mark_dirty_if_new=False)

    @config_editor_action
    def _save(self) -> None:
        cfg = displayed_config(self)
        entries = self._gather()
        if not entries:
            QMessageBox.warning(
                self, "Configuration",
                "Aucune structure dans le référentiel. "
                "Vérifiez Administration et outils → Structures, puis Synchroniser.",
            )
            return
        cfg.structure_format.overrides = entries
        self._svc.save_structure_format(cfg, self._settings)
        self._loaded_config = cfg
        self._dirty = False
        self._update_status()
        QMessageBox.information(self, "Enregistré", "Formatage des structures enregistré.")

    def _reset_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Réinitialiser", "Sélectionnez une ou plusieurs lignes.")
            return
        self._table.blockSignals(True)
        for r in rows:
            code = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            desig = self._designations.get(code, "")
            auto = default_attestation_label_for_structure_text(desig or code)
            item = self._table.item(r, 2)
            if item is None:
                item = QTableWidgetItem(auto)
                self._table.setItem(r, 2, item)
            else:
                item.setText(auto)
        self._table.blockSignals(False)
        self._dirty = True
        self._update_preview()


# ---------------------------------------------------------------------------
# Onglet Retraite
# ---------------------------------------------------------------------------

class _RetraiteModel(QAbstractTableModel):
    _HEADERS = (
        "Grade",
        "Noms",
        "Structure",
        "Motif",
        "Date de retraite",
        "Durée restante",
        "Âge",
        "Service",
    )

    def __init__(self) -> None:
        super().__init__()
        self._rows: list = []
        self._grade_codes: dict[str, str] = {}
        self._grade_rank: dict[str, int] = {}
        self._name_config = None

    def set_grade_codes(self, mapping: dict[str, str]) -> None:
        self._grade_codes = mapping

    def set_grade_rank(self, mapping: dict[str, int]) -> None:
        self._grade_rank = mapping

    def load(self, rows: list) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.SortOrder.DescendingOrder

        def rank_code(person) -> int:
            code = self._grade_codes.get(person.grade or "", person.grade or "")
            return self._grade_rank.get(code, 9999)

        if column == 0:
            self._rows.sort(key=lambda line: rank_code(line.person), reverse=reverse)
        elif column == 1:
            self._rows.sort(
                key=lambda line: (line.person.noms or "").casefold(),
                reverse=reverse,
            )
        elif column == 4:
            self._rows.sort(key=lambda line: line.date_retraite, reverse=reverse)
        else:
            self._rows.sort(
                key=lambda line: self._text(line.person, line, column).casefold(),
                reverse=reverse,
            )
        self.layoutChanged.emit()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._HEADERS)

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

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._rows)):
            return None
        line = self._rows[row]
        person = line.person
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(person, line, col)
        if role == Qt.ItemDataRole.ForegroundRole and col in (4, 5):
            return self._color(line.jours_restants)
        if role == Qt.ItemDataRole.TextAlignmentRole and col >= 3:
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        return None

    def _text(self, person, line, col: int) -> str:
        if col == 0:
            full = person.grade or ""
            return self._grade_codes.get(full, full)
        if col == 1:
            return format_person_name(person, self._name_config) if self._name_config else format_nom_prenoms(person.noms)
        if col == 2:
            return person.structure or ""
        if col == 3:
            return line.motif
        if col == 4:
            return line.date_retraite.strftime("%d/%m/%Y")
        if col == 5:
            from sme_gend.services.retraite_liste import format_duree_restante

            return format_duree_restante(line.jours_restants)
        if col == 6:
            age = current_age_years(person.date_naissance, on=_limite_age_today())
            return f"{age} ans" if age is not None else "—"
        if col == 7:
            years = current_age_years(person.date_service, on=_limite_age_today())
            return f"{years} ans" if years is not None else "—"
        return ""

    def _color(self, jours: int) -> QBrush | None:
        if jours < 0:
            return QBrush(_COLOR_DEPASSE)
        if jours < 365:
            return QBrush(_COLOR_PROCHE)
        return None


class _RetraiteTab(QWidget):
    """Liste des retraites selon la limite d'âge, de service ou la durée restante."""

    def __init__(self, settings: Settings, svc: LimiteAgeService, parent=None) -> None:
        super().__init__(parent)
        from sme_gend.services.retraite_liste import (
            ECHEANCE_ANNEE_COURANTE,
            ECHEANCE_ANNEE_SUIVANTE,
            ECHEANCE_ATTEINTE,
            ECHEANCE_DEFAUT,
            ECHEANCE_DANS,
            ECHEANCE_TOUTES,
            MOTIF_BOTH,
            MOTIF_TOUS,
        )

        self._settings = settings
        self._svc = svc
        self._stale = True
        self._loading = False
        self._all: list[tuple] = []
        self._grades: list = []
        self._grade_code_map: dict[str, str] = {}
        self._structure_rows: list = []
        self._echeance_custom: str | None = None
        self._chip_filter = ""

        self._stat_chips: dict[str, QLabel] = {}
        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(6)
        for key, label, bg, tip in (
            ("toutes", "Affichés", "#455a64", "Toutes les lignes filtrées"),
            ("atteinte", "Atteints", "#c62828", "Limite déjà passée"),
            ("ans", "< 1 an", "#ef6c00", "Retraite dans moins d'un an"),
            ("loin", "Au-delà", "#2e7d32", "Retraite dans plus d'un an"),
        ):
            chip = _stat_chip(label, bg, tooltip=tip)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)

            def _chip_click(event, chip_key=key):
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_stat_chip(chip_key)

            chip.mousePressEvent = _chip_click  # type: ignore[method-assign]
            self._stat_chips[key] = chip
            stats.addWidget(chip)
        stats.addStretch()
        self._stats_total = self._stat_chips["toutes"]
        self._stats_dus = self._stat_chips["atteinte"]
        self._stats_an = self._stat_chips["ans"]
        self._stats_loin = self._stat_chips["loin"]

        self._motif = NoWheelComboBox()
        self._motif.setFixedHeight(26)
        self._motif.setMinimumWidth(160)
        self._motif.addItem("Tous les motifs", MOTIF_TOUS)
        self._motif.addItem("Limite d'âge", "age")
        self._motif.addItem("Limite de service", "service")
        self._motif.addItem("La plus proche", MOTIF_BOTH)
        self._motif.setCurrentIndex(self._motif.findData(MOTIF_BOTH))
        self._motif.currentIndexChanged.connect(self._apply_filter)

        self._motif_valeur = NoWheelComboBox()
        self._motif_valeur.setFixedHeight(26)
        self._motif_valeur.setMinimumWidth(140)
        self._motif_valeur.addItem("(Tous)", None)
        self._motif_valeur.addItem("Âge", "Âge")
        self._motif_valeur.addItem("Service", "Service")
        self._motif_valeur.addItem("Âge et service", "Âge et service")
        self._motif_valeur.currentIndexChanged.connect(self._apply_filter)

        self._echeance = NoWheelComboBox()
        self._echeance.setEditable(True)
        self._echeance.setFixedHeight(26)
        self._echeance.setMinimumWidth(200)
        self._echeance.addItem("Toutes les échéances", ECHEANCE_TOUTES)
        self._echeance.addItem("Déjà atteintes", ECHEANCE_ATTEINTE)
        self._echeance.addItem("Année civile en cours", ECHEANCE_ANNEE_COURANTE)
        self._echeance.addItem("Année civile suivante", ECHEANCE_ANNEE_SUIVANTE)
        for code, label in (
            ("dans_3", "Dans les 3 mois"),
            ("dans_6", "Dans les 6 mois"),
            ("dans_12", "Dans les 12 mois"),
            ("dans_24", "Dans les 24 mois"),
            ("dans_60", "Dans les 60 mois"),
            ("dans_120", "Dans les 120 mois"),
        ):
            self._echeance.addItem(label, code)
        self._echeance.setCurrentIndex(self._echeance.findData(ECHEANCE_DEFAUT))
        self._echeance.currentIndexChanged.connect(self._on_echeance_combo_changed)
        echeance_line = self._echeance.lineEdit()
        if echeance_line is not None:
            echeance_line.textEdited.connect(self._on_echeance_edited)
            echeance_line.returnPressed.connect(self._on_echeance_manual)

        self._combo_grade = NoWheelComboBox()
        self._combo_grade.setFixedHeight(26)
        self._combo_grade.setMinimumWidth(120)
        self._combo_grade.addItem("(Tous les grades)", None)
        self._combo_grade.currentIndexChanged.connect(self._apply_filter)

        self._combo_struct = NoWheelComboBox()
        self._combo_struct.setFixedHeight(26)
        self._combo_struct.setMinimumWidth(140)
        self._combo_struct.addItem("(Toutes les structures)", None)
        self._combo_struct.currentIndexChanged.connect(self._apply_filter)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Noms, grade, matricule…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(26)
        self._search.textChanged.connect(self._apply_filter)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        lbl_motif = _compact_filter_label("Motif")
        lbl_motif.setFixedWidth(72)
        lbl_echeance = _compact_filter_label("Échéance")
        lbl_echeance.setFixedWidth(72)
        row1.addWidget(lbl_motif)
        row1.addWidget(self._motif, 2)
        row1.addWidget(QLabel("Motif affiché"))
        row1.addWidget(self._motif_valeur, 1)
        row1.addWidget(lbl_echeance)
        row1.addWidget(self._echeance, 2)
        row1.addStretch()

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(_compact_filter_label("Grade"))
        row2.addWidget(self._combo_grade, 2)
        row2.addWidget(_compact_filter_label("Structure"))
        row2.addWidget(self._combo_struct, 2)
        row2.addWidget(_compact_filter_label("Recherche"))
        row2.addWidget(self._search, 3)

        self._btn_reset = QPushButton("Réinitialiser")
        self._btn_reset.setFixedHeight(26)
        self._btn_reset.clicked.connect(self.reset_filters)

        filters = QGroupBox("Critères")
        filters.setObjectName("limiteAgeFiltersGroup")
        filters_lay = QVBoxLayout(filters)
        filters_lay.setContentsMargins(10, 8, 10, 8)
        filters_lay.setSpacing(6)
        filters_lay.addLayout(row1)
        filters_lay.addLayout(row2)
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_row.addWidget(self._btn_reset)
        filters_lay.addLayout(reset_row)

        self._model = _RetraiteModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        self._table.setSortingEnabled(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.sortIndicatorChanged.connect(lambda *_: self._model.sort(
            hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()
        ))
        self._table.setColumnWidth(0, 88)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(3, 120)
        self._table.setColumnWidth(4, 120)
        self._table.setColumnWidth(5, 150)
        self._table.setColumnWidth(6, 72)
        self._table.setColumnWidth(7, 80)

        self._empty = QLabel("Aucun personnel pour ces critères.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("settingsPathsHint")
        self._empty.setWordWrap(True)
        self._empty.hide()

        self._status = QLabel()
        self._status.setObjectName("dashSubtitle")
        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.setFixedHeight(26)
        self._btn_refresh.clicked.connect(self.reload)

        self._btn_print_filtered = QPushButton("Imprimer liste")
        self._btn_print_filtered.setFixedHeight(26)
        self._btn_print_filtered.clicked.connect(self._print_filtered_list)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self._btn_refresh)
        footer.addWidget(self._btn_print_filtered)
        footer.addStretch()
        footer.addWidget(self._status)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(stats)
        lay.addWidget(filters)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(self._empty, stretch=1)
        lay.addLayout(footer)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.mark_stale()

    def mark_stale(self) -> None:
        self._stale = True

    def ensure_loaded(self) -> None:
        if self._stale and not self._loading:
            self.reload()

    def reload(self) -> None:
        from dataclasses import replace as dc_replace

        from sme_gend.repositories.grade_repository import GradeRepository
        from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
        from sme_gend.repositories.structure_repository import StructureRepository
        from sme_gend.services.soit_transmis_service import sort_limite_age_rows, sort_persons_by_grade
        from sme_gend.ui.bg_task import run_with_progress

        if self._loading:
            return
        self._loading = True
        settings = self._settings
        svc = self._svc

        def do_load():
            cfg = svc.load()
            rows = ListeGeneraleRepository(settings).fetch_all()
            grades = GradeRepository(settings).fetch_all()
            structs = StructureRepository(settings).fetch_all()
            grade_map = {g.code: g.libelle for g in grades if g.code}
            resolved = []
            for person in rows:
                grade = grade_map.get((person.grade or "").strip()) or person.grade
                resolved.append(dc_replace(person, grade=grade, noms=format_person_name(person, cfg)))
            grade_code_map = {value: key for key, value in grade_map.items()}
            resolved = sort_persons_by_grade(
                resolved, grade_code_map=grade_code_map, grades=grades
            )
            paired = sort_limite_age_rows(
                [(person, svc.calculate_limit(person, cfg)) for person in resolved],
                grade_code_map=grade_code_map,
                grades=grades,
            )
            return paired, grade_code_map, grades, structs, cfg

        try:
            result = run_with_progress(
                self, "Retraite", "Chargement des échéances…", do_load, silent=True
            )
        finally:
            self._loading = False
        if result is None:
            return
        self._all, self._grade_code_map, self._grades, self._structure_rows, cfg = result
        self._model._name_config = cfg
        self._model.set_grade_codes(self._grade_code_map)
        self._model.set_grade_rank(self._grade_rank_map())
        self._stale = False
        self._refresh_combos()
        self._apply_filter()

    def _grade_rank_map(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for index, g in enumerate(self._grades):
            code = getattr(g, "code", None)
            if not code:
                continue
            pref = getattr(g, "pref_ord", None)
            out[str(code)] = int(pref if pref is not None else index + 1)
        return out

    def _echeance_code(self) -> str:
        from sme_gend.services.retraite_liste import ECHEANCE_TOUTES

        if self._echeance_custom:
            return self._echeance_custom
        data = self._echeance.currentData()
        return str(data) if data is not None else ECHEANCE_TOUTES

    def _on_echeance_combo_changed(self, _index: int) -> None:
        if self._echeance.currentIndex() >= 0:
            self._echeance_custom = None
        self._apply_filter()

    def _on_echeance_edited(self, _text: str = "") -> None:
        line = self._echeance.lineEdit()
        if line is not None:
            line.setProperty("periodError", False)

    def _on_echeance_manual(self) -> None:
        import re

        from sme_gend.services.retraite_liste import ECHEANCE_DANS

        line = self._echeance.lineEdit()
        if line is None:
            return
        text = (line.text() or "").strip().lower()
        if not text:
            return
        if m := re.fullmatch(r"(\d+)\s*mois", text):
            mois = int(m.group(1))
            self._echeance_custom = f"{ECHEANCE_DANS}_{mois}"
            line.setText(f"{mois} mois")
            line.setProperty("periodError", False)
            self._apply_filter()
            return
        if m := re.fullmatch(r"(\d+)\s*an[s]?", text):
            mois = int(m.group(1)) * 12
            self._echeance_custom = f"{ECHEANCE_DANS}_{mois}"
            line.setText(f"{mois} mois")
            line.setProperty("periodError", False)
            self._apply_filter()
            return
        if m := re.fullmatch(r"(\d{4})", text):
            self._echeance_custom = f"annee_{m.group(1)}"
            line.setText(m.group(1))
            line.setProperty("periodError", False)
            self._apply_filter()
            return
        line.setProperty("periodError", True)

    def _restore_echeance(self, code: str) -> None:
        from sme_gend.services.retraite_liste import ECHEANCE_DANS

        line = self._echeance.lineEdit()
        if code.startswith(f"{ECHEANCE_DANS}_"):
            try:
                mois = int(code.split("_", 1)[1])
            except (ValueError, IndexError):
                return
            self._echeance_custom = code
            if line is not None:
                line.setText(f"{mois} mois")
                line.setProperty("periodError", False)
            return
        if code.startswith("annee_"):
            self._echeance_custom = code
            if line is not None:
                line.setText(code.split("_", 1)[1])
                line.setProperty("periodError", False)

    def reset_filters(self) -> None:
        from sme_gend.services.retraite_liste import ECHEANCE_DEFAUT, MOTIF_BOTH

        self._motif_valeur.setCurrentIndex(0)
        self._echeance_custom = None
        self._echeance.setCurrentIndex(self._echeance.findData(ECHEANCE_DEFAUT))
        self._chip_filter = ""
        self._motif.setCurrentIndex(self._motif.findData(MOTIF_BOTH))
        self._search.clear()
        self._combo_grade.setCurrentIndex(0)
        self._combo_struct.setCurrentIndex(0)
        self._apply_filter()

    def _on_stat_chip(self, key: str) -> None:
        self._chip_filter = key if key != "toutes" else ""
        self._apply_filter()

    def _print_filtered_list(self) -> None:
        if not require_export(self):
            return
        headers = _RetraiteModel._HEADERS
        rows: list[tuple[str, ...]] = []
        for row_idx in range(self._model.rowCount()):
            cells = [
                str(
                    self._model.data(
                        self._model.index(row_idx, col),
                        Qt.ItemDataRole.DisplayRole,
                    )
                    or ""
                )
                for col in range(len(headers))
            ]
            rows.append(tuple(cells))
        _print_filtered_personnel_table(
            self,
            "Liste des échéances de retraite",
            headers,
            tuple(rows),
        )

    def _refresh_combos(self) -> None:
        from sme_gend.services.grade_label_format import grade_combo_items
        from sme_gend.services.structure_label_format import structure_combo_items

        extra_grades: list[str] = []
        extra_structs: list[str] = []
        for person, _limit in self._all:
            grade = (person.grade or "").strip()
            if grade and grade not in extra_grades:
                extra_grades.append(grade)
            structure = (person.structure or "").strip()
            if structure and structure not in extra_structs:
                extra_structs.append(structure)
        self._fill_combo(
            self._combo_grade,
            "(Tous les grades)",
            grade_combo_items(self._grades, extra_raw=extra_grades),
        )
        self._fill_combo(
            self._combo_struct,
            "(Toutes les structures)",
            structure_combo_items(self._structure_rows, extra_codes=extra_structs),
        )

    def _fill_combo(self, combo: QComboBox, first: str, items: list[tuple[str, str]]) -> None:
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(first, None)
        for label, code in items:
            combo.addItem(label, code)
        if previous is not None:
            for index in range(combo.count()):
                if combo.itemData(index) == previous:
                    combo.setCurrentIndex(index)
                    break
        combo.blockSignals(False)

    def _row_matches_search(self, line, person, search: str) -> bool:
        code = self._grade_code_map.get(person.grade or "", "")
        fields = [
            person.noms,
            person.grade,
            code,
            person.structure,
            person.matricule_solde,
            person.matricule_militaire,
            line.motif,
        ]
        low = normalize_search_text(search.lower())
        return any(low in (normalize_search_text(str(f or "").lower())) for f in fields)

    def _apply_filter(self) -> None:
        from sme_gend.services.retraite_liste import ECHEANCE_TOUTES, MOTIF_BOTH, filtrer_retraites
        from sme_gend.ui.personnel_list_filters import grade_matches_filter, structure_matches_filter

        if not self._all and self._stale:
            return
        grade_f = self._combo_grade.currentData()
        struct_f = self._combo_struct.currentData()
        grade_disp = self._combo_grade.currentText()
        search = normalize_search_text(self._search.text().strip().lower())
        motif_v = self._motif_valeur.currentData()
        narrowed = []
        for person, limit in self._all:
            if grade_f and not grade_matches_filter(
                person.grade,
                grade_f,
                combo_display=grade_disp,
                grades=self._grades,
            ):
                continue
            if struct_f and not structure_matches_filter(person.structure, struct_f):
                continue
            narrowed.append((person, limit))
        kw = _echeance_kwargs(self._echeance_code())
        shown = filtrer_retraites(
            narrowed,
            motif=str(self._motif.currentData() or MOTIF_BOTH),
            echeance=str(kw.get("echeance", ECHEANCE_TOUTES)),
            mois=kw.get("mois"),
            annee=kw.get("annee"),
            today=_limite_age_today(),
        )
        if motif_v:
            shown = [row for row in shown if row.motif == motif_v]
        if search:
            shown = [
                row
                for row in shown
                if self._row_matches_search(row, row.person, search)
            ]
        if self._chip_filter == "atteinte":
            shown = [row for row in shown if row.jours_restants <= 0]
        elif self._chip_filter == "ans":
            shown = [row for row in shown if 0 < row.jours_restants <= 365]
        elif self._chip_filter == "loin":
            shown = [row for row in shown if row.jours_restants > 365]
        self._model.load(shown)
        self._btn_print_filtered.setEnabled(bool(shown) and can_export())
        hdr = self._table.horizontalHeader()
        if hdr.sortIndicatorSection() >= 0:
            self._model.sort(hdr.sortIndicatorSection(), hdr.sortIndicatorOrder())
        empty = not shown
        self._table.setVisible(not empty)
        self._empty.setVisible(empty)
        if empty:
            self._empty.setText(
                "Aucune échéance chargée."
                if not self._all
                else "Aucun personnel pour ces critères."
            )
        dus = sum(1 for row in shown if row.jours_restants <= 0)
        proche = sum(1 for row in shown if 0 < row.jours_restants <= 365)
        loin = sum(1 for row in shown if row.jours_restants > 365)
        _stat_chip_set_count(self._stats_total, len(shown))
        _stat_chip_set_count(self._stats_dus, dus)
        _stat_chip_set_count(self._stats_an, proche)
        _stat_chip_set_count(self._stats_loin, loin)
        self._status.setText(f"{len(shown)} personne(s) · {len(self._all)} au total")


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

        title = QLabel("Attestations et suivi de la retraite")
        title.setObjectName("dashTitle")

        self._tabs = QTabWidget()
        self._tabs.setObjectName("settingsTabs")
        self._tabs.setDocumentMode(True)
        self._tab_attest  = _AttestationsTab(settings, self._svc)
        self._tab_retraite = _RetraiteTab(settings, self._svc)
        self._tab_attest._detail.name_format_saved.connect(self._tab_retraite.mark_stale)
        self._tab_config  = _ConfigLimitesTab(settings, self._svc)
        self._tab_options = _OptionsDocTab(self._svc)
        self._tab_struct  = _StructureFormatTab(settings, self._svc)

        self._tabs.setUsesScrollButtons(True)
        self._tabs.addTab(scroll_panel(self._tab_attest),  "Attestations")
        self._tabs.addTab(scroll_panel(self._tab_retraite), "Retraite")
        self._tabs.addTab(scroll_panel(self._tab_config),  "Configuration des limites")
        self._tabs.addTab(scroll_panel(self._tab_options), "Options du document")
        self._tabs.addTab(scroll_panel(self._tab_struct),  "Formatage des structures")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(8)
        lay.addWidget(title)
        lay.addWidget(self._tabs, stretch=1)

    def set_send_to_soit_transmis_fn(self, fn) -> None:
        self._tab_attest.set_send_to_soit_transmis_fn(fn)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._tab_attest.rebind_settings(settings)
        self._tab_retraite.rebind_settings(settings)
        self._tab_config.rebind_settings(settings)
        self._tab_struct.rebind_settings(settings)
        self._needs_reload = True

    @property
    def limite_age_service(self):
        """Service LimiteAge partagé (injecté dans SoitTransmisView par shell_window)."""
        return self._svc

    def is_dirty(self) -> bool:
        return (
            self._tab_config.is_dirty()
            or self._tab_options.is_dirty()
            or self._tab_struct.is_dirty()
        )

    def mark_dirty(self) -> None:
        self._needs_reload = True
        self._tab_attest.mark_stale()
        if self.isVisible():
            self._reload_if_needed()

    def on_page_activated(self) -> None:
        self._reload_if_needed()

    def _on_tab_changed(self, _index: int) -> None:
        if tab_content(self._tabs) is self._tab_retraite:
            self._tab_retraite.ensure_loaded()

    def _reload_if_needed(self) -> None:
        if not self._needs_reload:
            return
        if not self._tab_attest.load_personnel():
            return
        self._needs_reload = False
        self._tab_retraite.mark_stale()
        if tab_content(self._tabs) is self._tab_retraite:
            self._tab_retraite.ensure_loaded()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():
            return
        if not self._grades_synced:
            self._grades_synced = True
            try:
                self._svc.sync_grades_from_referentiel(self._settings)
                self._tab_config.load_from_disk()
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "Sync grades limite d'âge : %s", exc
                )
        self._reload_if_needed()
