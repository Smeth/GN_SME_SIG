"""Stages — gestion parcours_stage (local / extérieur) et position « Stage interne »."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.numerique_padmin import PADMIN_STAGE_EX, PADMIN_STAGE_LOCAL
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.services.stages_export import export_stage_interne_workbook, export_stages_workbook
from sme_gend.services.stages_list_filter import (
    NATURE_FILTER_ALL,
    build_nature_filter_options,
    distinct_stage_natures_from_details,
    nature_from_detail,
    normalize_nature,
    row_matches_nature_filter,
    row_matches_stage_search,
    stage_detail_for_row,
)
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.stage_start_dialog import StageStartDialog
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
    grade_sort_helper_from_settings,
)
from sme_gend.ui.list_table_filter import row_matches_search
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.widgets.date_input import FrenchDateEdit

StageKind = Literal["local", "ex", "interne"]

_PADMIN_STAGE_INTERNE = "Stage interne"

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


def _parse_ym(ym: str) -> tuple[int, int] | None:
    try:
        y_s, m_s = (ym or "").strip().split("-", 1)
        y, m = int(y_s), int(m_s)
        if 1 <= m <= 12:
            return y, m
    except (ValueError, TypeError):
        pass
    return None


def _norm_padmin(p: str | None) -> str:
    return " ".join((p or "").casefold().split())


def _is_stage_local(r: ListeGeneraleRow) -> bool:
    return _norm_padmin(r.position_admin).replace(" ", "") == PADMIN_STAGE_LOCAL.casefold().replace(" ", "")


def _is_stage_ex(r: ListeGeneraleRow) -> bool:
    return _norm_padmin(r.position_admin).replace(" ", "") == PADMIN_STAGE_EX.casefold().replace(" ", "")


def _is_stage_interne(r: ListeGeneraleRow) -> bool:
    return _norm_padmin(r.position_admin).replace(" ", "") == "stageinterne"


def _is_any_stage_row(r: ListeGeneraleRow) -> bool:
    return _is_stage_local(r) or _is_stage_ex(r) or _is_stage_interne(r)


def padmin_for_stage_kind(kind: StageKind) -> str:
    if kind == "local":
        return PADMIN_STAGE_LOCAL
    if kind == "ex":
        return PADMIN_STAGE_EX
    return _PADMIN_STAGE_INTERNE


def stage_kind_label(kind: StageKind) -> str:
    if kind == "local":
        return "Stage local"
    if kind == "ex":
        return "Stage extérieur"
    return "Stage interne"


def _editable_fiche_nature_combo() -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    le = combo.lineEdit()
    if le:
        le.setPlaceholderText("Nature du stage")
    return combo


def _combo_nature_text(combo: QComboBox) -> str:
    return normalize_nature(combo.currentText())


def _set_combo_nature_text(combo: QComboBox, text: str | None) -> None:
    nat = normalize_nature(text)
    if not nat:
        combo.setCurrentIndex(-1)
        combo.setEditText("")
        return
    idx = combo.findText(nat, Qt.MatchFlag.MatchFixedString)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setEditText(nat)


def _clear_combo_nature(combo: QComboBox) -> None:
    combo.setCurrentIndex(-1)
    combo.setEditText("")


def _add_stage_fiche_header(fl: QFormLayout) -> tuple[QLabel, QLabel, QLabel]:
    lbl_agent = QLabel("Aucune ligne sélectionnée")
    lbl_agent.setObjectName("gainPerteCount")
    lbl_agent.setWordWrap(True)
    lbl_grade = QLabel("—")
    lbl_structure = QLabel("—")
    lbl_structure.setWordWrap(True)
    fl.addRow("Agent :", lbl_agent)
    fl.addRow("Grade :", lbl_grade)
    fl.addRow("Structure :", lbl_structure)
    return lbl_agent, lbl_grade, lbl_structure


def _apply_stage_checked_button_label(kind: StageKind) -> str:
    if kind == "local":
        return "Appliquer stage local (cochés)…"
    if kind == "ex":
        return "Appliquer stage extérieur (cochés)…"
    return "Appliquer stage interne (cochés)…"


def _build_stage_toolbar(
    search: QLineEdit,
    nature_filter: QComboBox,
    lbl_checked: QLabel,
    action_buttons: list[QWidget],
) -> QFrame:
    """Ligne filtres (recherche large) + ligne boutons en dessous."""
    action_bar = QFrame()
    action_bar.setObjectName("gainPerteToolbar")
    outer = QVBoxLayout(action_bar)
    outer.setContentsMargins(8, 6, 8, 6)
    outer.setSpacing(6)

    filter_row = QHBoxLayout()
    filter_row.setSpacing(8)
    lbl_search = QLabel("Rechercher")
    lbl_search.setObjectName("gainPerteToolbarLabel")
    filter_row.addWidget(lbl_search)
    search.setMinimumWidth(220)
    filter_row.addWidget(search, stretch=1)
    lbl_nat = QLabel("Nature")
    lbl_nat.setObjectName("gainPerteToolbarLabel")
    filter_row.addWidget(lbl_nat)
    nature_filter.setMinimumWidth(220)
    filter_row.addWidget(nature_filter)
    filter_row.addWidget(lbl_checked)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    for btn in action_buttons:
        btn_row.addWidget(btn)
    btn_row.addStretch(1)

    outer.addLayout(filter_row)
    outer.addLayout(btn_row)
    return action_bar


def _stat_chip_html(label: str, count: int, *, bg: str) -> str:
    return (
        f"<span style='background:{bg};color:#ffffff;padding:3px 10px;"
        f"border-radius:10px;font-size:11px;font-weight:600;'>"
        f"{label} : {count}</span>"
    )


def _parse_stage_dates(detail: StageDetail | None) -> tuple[str, str]:
    if detail is None or not (detail.date_debut_fin or "").strip():
        return "", ""
    ddf = detail.date_debut_fin.strip()
    if " — " in ddf:
        deb, fin = ddf.split(" — ", 1)
        return deb.strip(), fin.strip()
    return ddf, ""


def _stage_ddf(deb: str, fin: str) -> str | None:
    deb, fin = deb.strip(), fin.strip()
    if deb and fin:
        return f"{deb} — {fin}"
    return deb or fin or None


def _stage_table_headers(kind: StageKind) -> list[str]:
    id_col = "Id liste" if kind == "interne" else "Id liaison"
    headers = [
        "✓",
        id_col,
        "Nom",
        "Grade",
        "Structure",
        "Nature stage",
        "Lieu",
        "Dates",
        "Padmin",
    ]
    return headers + ['Réf/note'] + (['Session'] if kind == 'interne' else [])


def _stage_type_code(kind: StageKind) -> str:
    if kind == "local":
        return "LOCAL"
    if kind == "ex":
        return "EXTERIEUR"
    return "INTERNE"


class StagesView(QWidget):
    """Suivi des stages — aligné situation numérique (StageLocal, StageEx, Stage interne)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._view_mois: str | None = None
        self._mois_catalog: list[str] = []
        self._scoped_settings: Settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._view_data = ViewDataState(stale=True)
        self._grade_sort: GradeSortHelper | None = None
        self._grade_rows: list | None = None
        self._can_edit = True

        self._rows_local_all: list[ListeGeneraleRow] = []
        self._rows_ex_all: list[ListeGeneraleRow] = []
        self._rows_interne_all: list[ListeGeneraleRow] = []
        self._rows_local: list[ListeGeneraleRow] = []
        self._rows_ex: list[ListeGeneraleRow] = []
        self._rows_interne: list[ListeGeneraleRow] = []
        self._details_local: dict[str, StageDetail] = {}
        self._details_ex: dict[str, StageDetail] = {}
        self._details_interne: dict[str, StageDetail] = {}
        self._checked: dict[StageKind, set[str]] = {
            "local": set(),
            "ex": set(),
            "interne": set(),
        }
        self._load_generation = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)

        title = QLabel("<h2>Stages</h2>", textFormat=Qt.TextFormat.RichText)
        title.setContentsMargins(0, 0, 0, 0)

        self._summary = QLabel()
        self._summary.setObjectName("gainPerteSummary")
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self._summary)
        self._summary.hide()

        self._lock_banner = QLabel()
        self._lock_banner.setObjectName("gainPerteLockBanner")
        self._lock_banner.setWordWrap(True)
        self._lock_banner.hide()
        root.addWidget(self._lock_banner)

        top_bar = QFrame()
        top_bar.setObjectName("gainPerteTopBar")
        top_lay = QVBoxLayout(top_bar)
        top_lay.setContentsMargins(8, 6, 8, 6)
        top_lay.setSpacing(5)

        period_row = QHBoxLayout()
        period_row.setSpacing(10)
        period_row.addWidget(QLabel("Période"))
        self._combo_annee = QComboBox()
        self._combo_annee.setObjectName("gainPertePeriodCombo")
        self._combo_annee.setMinimumWidth(92)
        self._combo_annee.currentIndexChanged.connect(self._on_annee_changed)
        period_row.addWidget(self._combo_annee)

        period_row.addWidget(QLabel("Mois"))
        self._combo_mois = QComboBox()
        self._combo_mois.setObjectName("gainPertePeriodCombo")
        self._combo_mois.setMinimumWidth(128)
        self._combo_mois.currentIndexChanged.connect(self._on_mois_changed)
        self._combo_mois.activated.connect(self._on_mois_activated)
        period_row.addWidget(self._combo_mois)

        self._lbl_mois_actif_ref = QLabel()
        self._lbl_mois_actif_ref.setObjectName("gainPerteCount")
        period_row.addWidget(self._lbl_mois_actif_ref)

        period_row.addSpacing(12)
        self._chip_local = QLabel()
        self._chip_ex = QLabel()
        self._chip_interne = QLabel()
        for chip in (self._chip_local, self._chip_ex, self._chip_interne):
            chip.setObjectName("gainPerteStatChip")
            chip.setTextFormat(Qt.TextFormat.RichText)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            period_row.addWidget(chip)
        period_row.addStretch()

        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("gainPerteCount")
        period_row.addWidget(self._count_lbl)
        top_lay.addLayout(period_row)

        root.addWidget(top_bar)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._build_stage_tab("local"), "Stages locaux")
        tabs.addTab(self._build_stage_tab("ex"), "Stages extérieurs")
        tabs.addTab(self._build_interne_tab(), "Stage interne")
        self._tabs = tabs
        self._tabs.currentChanged.connect(lambda _i: self._update_summary())
        root.addWidget(tabs, stretch=1)

    def _active_stage_kind(self) -> StageKind:
        idx = self._tabs.currentIndex() if getattr(self, "_tabs", None) else 0
        return ("local", "ex", "interne")[max(0, min(idx, 2))]

    def _update_visible_count_label(self) -> None:
        kind = self._active_stage_kind()
        n = len(self._rows_for(kind))
        short = {"local": "locaux", "ex": "extérieurs", "interne": "interne"}[kind]
        self._count_lbl.setText(f"{n} ligne(s) visibles — stages {short}")

    def _build_stage_tab(self, kind: StageKind) -> QWidget:
        assert kind in ("local", "ex")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(6)
        lay.setContentsMargins(0, 4, 0, 0)

        search = QLineEdit()
        search.setObjectName("gainPerteSearch")
        search.setPlaceholderText("Nom, grade, structure, nature, lieu, dates…")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(lambda: self._apply_filters(kind))

        nature_filter = QComboBox()
        nature_filter.setObjectName("gainPertePeriodCombo")
        nature_filter.setMinimumWidth(200)
        nature_filter.addItem("Toutes les natures", NATURE_FILTER_ALL)
        nature_filter.currentIndexChanged.connect(lambda: self._apply_filters(kind))

        lbl_checked = QLabel("0 coché(s)")
        lbl_checked.setObjectName("gainPerteCount")

        btn_add = QPushButton("Ajouter depuis l'effectif…")
        btn_add.setObjectName("gainPerteBtnPrimary")
        btn_add.setToolTip(
            "Choisir un agent dans l'effectif du mois affiché (même s'il n'est pas encore "
            "dans le tableau) et le passer en stage avec sa fiche."
        )
        btn_add.clicked.connect(lambda: self._on_add_from_effectif(kind))

        btn_create = QPushButton(_apply_stage_checked_button_label(kind))
        btn_create.setToolTip(
            "Met en stage local / extérieur les lignes cochées (ou la ligne sélectionnée) : "
            "position administrative + nature, lieu et dates (fiche parcours_stage)."
        )
        btn_create.clicked.connect(lambda: self._on_start_stage(kind))

        btn_select_all = QPushButton("Tout cocher")
        btn_select_all.setToolTip("Cocher toutes les lignes actuellement affichées.")
        btn_select_all.clicked.connect(lambda: self._toggle_select_all(kind))

        btn_save = QPushButton("Enregistrer la fiche")
        btn_save.setObjectName("gainPerteBtnPrimary")
        btn_save.setToolTip(
            "Enregistre nature, lieu et dates pour l'agent sélectionné dans le tableau."
        )
        btn_save.clicked.connect(lambda: self._save_fiche(kind))

        btn_end = QPushButton("Terminer le(s) stage(s)")
        btn_end.setToolTip(
            "Clôture le stage : repasse en « Présent », enregistre la date de fin sur la fiche "
            "stage, et propose éventuellement le diplôme obtenu (dossier agent)."
        )
        btn_end.clicked.connect(lambda: self._end_checked_stages(kind))

        btn_refresh = QPushButton("Rafraîchir")
        btn_refresh.clicked.connect(self._reload_lists)

        btn_export = QPushButton("Exporter…")
        btn_export.clicked.connect(lambda: self._on_export(kind))

        lay.addWidget(
            _build_stage_toolbar(
                search,
                nature_filter,
                lbl_checked,
                [
                    btn_select_all,
                    btn_add,
                    btn_create,
                    btn_end,
                    btn_refresh,
                    btn_export,
                ],
            )
        )

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        tbl = QTableWidget()
        tbl.setObjectName("gainPerteTable")
        tbl.setColumnCount(len(_stage_table_headers(kind)))
        tbl.setHorizontalHeaderLabels(_stage_table_headers(kind))
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tbl.setMinimumHeight(280)
        tbl.itemSelectionChanged.connect(lambda: self._on_select(kind))
        sm = tbl.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(lambda *_: self._on_table_selection_changed(kind))
        tbl.itemChanged.connect(lambda item: self._on_table_item_changed(kind, item))
        split.addWidget(tbl)

        form_box = QGroupBox("Fiche stage")
        form_box.setObjectName("limiteAgeDetailGroup")
        form_box.setMaximumWidth(280)
        fl = QFormLayout(form_box)
        lbl_agent, lbl_grade, lbl_structure = _add_stage_fiche_header(fl)
        f_nature = _editable_fiche_nature_combo()
        f_lieu = QLineEdit()
        f_lieu.setPlaceholderText("Lieu")
        f_reference = QLineEdit()
        f_reference.setMaxLength(256)
        f_reference.setPlaceholderText("Facultatif")
        f_deb = FrenchDateEdit()
        f_fin = FrenchDateEdit()
        fl.addRow("Nature * :", f_nature)
        fl.addRow("Lieu :", f_lieu)
        fl.addRow("Réf/note :", f_reference)
        fl.addRow("Date début :", f_deb)
        fl.addRow("Date fin :", f_fin)
        fl.addRow("", btn_save)

        split.addWidget(form_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 260])
        lay.addWidget(split, stretch=1)

        if kind == "local":
            self._lbl_local_agent, self._lbl_local_grade, self._lbl_local_structure = (
                lbl_agent,
                lbl_grade,
                lbl_structure,
            )
            self._tbl_local = tbl
            self._search_local = search
            self._nature_local = nature_filter
            self._lbl_local_checked = lbl_checked
            self._btn_local_create = btn_create
            self._btn_local_select_all = btn_select_all
            self._btn_local_save = btn_save
            self._btn_local_end = btn_end
            self._f_local_nature = f_nature
            self._f_local_lieu = f_lieu
            self._f_local_reference = f_reference
            self._f_local_deb = f_deb
            self._f_local_fin = f_fin
        else:
            self._lbl_ex_agent, self._lbl_ex_grade, self._lbl_ex_structure = (
                lbl_agent,
                lbl_grade,
                lbl_structure,
            )
            self._tbl_ex = tbl
            self._search_ex = search
            self._nature_ex = nature_filter
            self._lbl_ex_checked = lbl_checked
            self._btn_ex_create = btn_create
            self._btn_ex_select_all = btn_select_all
            self._btn_ex_save = btn_save
            self._btn_ex_end = btn_end
            self._f_ex_nature = f_nature
            self._f_ex_lieu = f_lieu
            self._f_ex_reference = f_reference
            self._f_ex_deb = f_deb
            self._f_ex_fin = f_fin
        return w

    def _build_interne_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(6)
        lay.setContentsMargins(0, 4, 0, 0)

        search = QLineEdit()
        search.setObjectName("gainPerteSearch")
        search.setPlaceholderText("Nom, grade, structure, nature, lieu, dates…")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(lambda: self._apply_filters("interne"))

        nature_filter = QComboBox()
        nature_filter.setObjectName("gainPertePeriodCombo")
        nature_filter.setMinimumWidth(200)
        nature_filter.addItem("Toutes les natures", NATURE_FILTER_ALL)
        nature_filter.currentIndexChanged.connect(lambda: self._apply_filters("interne"))

        lbl_checked = QLabel("0 coché(s)")
        lbl_checked.setObjectName("gainPerteCount")

        btn_add = QPushButton("Ajouter depuis l'effectif…")
        btn_add.setObjectName("gainPerteBtnPrimary")
        btn_add.setToolTip(
            "Choisir un agent dans l'effectif du mois affiché et le passer en stage interne."
        )
        btn_add.clicked.connect(lambda: self._on_add_from_effectif("interne"))

        btn_create = QPushButton(_apply_stage_checked_button_label("interne"))
        btn_create.setToolTip(
            "Passe en « Stage interne » les lignes cochées (ou la sélection) : "
            "position administrative ; complétez nature/lieu/dates via la fiche à droite."
        )
        btn_create.clicked.connect(lambda: self._on_start_stage("interne"))

        btn_select_all = QPushButton("Tout cocher")
        btn_select_all.setToolTip("Cocher toutes les lignes actuellement affichées.")
        btn_select_all.clicked.connect(lambda: self._toggle_select_all("interne"))

        btn_save = QPushButton("Enregistrer la fiche")
        btn_save.setObjectName("gainPerteBtnPrimary")
        btn_save.setToolTip(
            "Enregistre nature, lieu et dates pour l'agent sélectionné dans le tableau."
        )
        btn_save.clicked.connect(lambda: self._save_fiche("interne"))

        btn_end = QPushButton("Terminer le(s) stage(s)")
        btn_end.setToolTip(
            "Clôture le stage : repasse en « Présent », enregistre la date de fin sur la fiche "
            "stage, et propose éventuellement le diplôme obtenu (dossier agent)."
        )
        btn_end.clicked.connect(lambda: self._end_checked_stages("interne"))
        btn_refresh = QPushButton("Rafraîchir")
        btn_refresh.clicked.connect(self._reload_lists)
        btn_export = QPushButton("Exporter…")
        btn_export.clicked.connect(lambda: self._on_export("interne"))

        lay.addWidget(
            _build_stage_toolbar(
                search,
                nature_filter,
                lbl_checked,
                [
                    btn_select_all,
                    btn_add,
                    btn_create,
                    btn_end,
                    btn_refresh,
                    btn_export,
                ],
            )
        )

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        tbl = QTableWidget()
        tbl.setObjectName("gainPerteTable")
        tbl.setColumnCount(len(_stage_table_headers('interne')))
        tbl.setHorizontalHeaderLabels(_stage_table_headers("interne"))
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        tbl.setSortingEnabled(True)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tbl.setMinimumHeight(280)
        tbl.itemSelectionChanged.connect(lambda: self._on_select("interne"))
        sm_interne = tbl.selectionModel()
        if sm_interne is not None:
            sm_interne.selectionChanged.connect(
                lambda *_: self._on_table_selection_changed("interne")
            )
        tbl.itemChanged.connect(lambda item: self._on_table_item_changed("interne", item))
        split.addWidget(tbl)

        form_box = QGroupBox("Fiche stage")
        form_box.setObjectName("limiteAgeDetailGroup")
        form_box.setMaximumWidth(280)
        fl = QFormLayout(form_box)
        lbl_agent, lbl_grade, lbl_structure = _add_stage_fiche_header(fl)
        f_nature = _editable_fiche_nature_combo()
        f_lieu = QLineEdit()
        f_lieu.setPlaceholderText("Lieu")
        f_reference = QLineEdit()
        f_reference.setMaxLength(256)
        f_reference.setPlaceholderText("Facultatif")
        f_deb = FrenchDateEdit()
        f_fin = FrenchDateEdit()
        fl.addRow("Nature * :", f_nature)
        fl.addRow("Lieu :", f_lieu)
        fl.addRow("Réf/note :", f_reference)
        fl.addRow("Date début :", f_deb)
        fl.addRow("Date fin :", f_fin)
        fl.addRow("", btn_save)
        split.addWidget(form_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 260])
        lay.addWidget(split, stretch=1)

        self._lbl_interne_agent, self._lbl_interne_grade, self._lbl_interne_structure = (
            lbl_agent,
            lbl_grade,
            lbl_structure,
        )
        self._tbl_interne = tbl
        self._search_interne = search
        self._nature_interne = nature_filter
        self._lbl_interne_checked = lbl_checked
        self._btn_interne_create = btn_create
        self._btn_interne_select_all = btn_select_all
        self._btn_interne_save = btn_save
        self._btn_interne_end = btn_end
        self._f_interne_nature = f_nature
        self._f_interne_lieu = f_lieu
        self._f_interne_reference = f_reference
        self._f_interne_session = QLineEdit()
        self._f_interne_session.setMaxLength(256)
        self._f_interne_session.setPlaceholderText('Facultatif')
        fl.insertRow(6, 'Session :', self._f_interne_session)
        self._f_interne_deb = f_deb
        self._f_interne_fin = f_fin
        return w

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        self._refresh_action_state()
        if self._view_data.needs_load():
            self._reload_lists_async()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._view_mois = None
        self._rebind_repositories()
        self._grade_sort = None
        self._grade_rows = None
        self._view_data.mark_stale()
        if self.isVisible():
            self._reload_lists_async()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _rebind_repositories(self) -> None:
        from dataclasses import replace

        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        ym = canonical_sme_mois_yyyy_mm(self._view_mois or "") or None
        if ym:
            self._scoped_settings = replace(self._settings, mysql_view_mois=ym)
        else:
            self._scoped_settings = self._settings
        self._liste = ListeGeneraleRepository(self._scoped_settings)
        self._sat = NumeriqueSatelliteRepository(self._scoped_settings)

    def _on_annee_changed(self, _index: int) -> None:
        prev_month = self._combo_mois.currentData()
        self._combo_mois.blockSignals(True)
        self._combo_mois.clear()
        self._fill_mois_combo(prev_month)
        self._combo_mois.blockSignals(False)
        self._on_period_combo_changed()

    def _on_mois_changed(self, _index: int) -> None:
        if self._combo_mois.signalsBlocked():
            return
        self._on_period_combo_changed()

    def _on_mois_activated(self, _index: int) -> None:
        self._on_period_combo_changed()

    def _on_period_combo_changed(self) -> None:
        self._sync_view_mois_from_combos()
        self._rebind_repositories()
        self._load_generation += 1
        self._view_data.fail_load()
        self._view_data.mark_stale()
        self._reload_lists_async(force=True)

    def _sync_view_mois_from_combos(self) -> None:
        year = self._combo_annee.currentData()
        month = self._combo_mois.currentData()
        self._view_mois = f"{int(year):04d}-{int(month):02d}" if year and month else None

    def _fill_mois_combo(self, target_month: int | None) -> None:
        year = self._combo_annee.currentData()
        for m in range(1, 13):
            ym = f"{int(year):04d}-{m:02d}" if year else ""
            label = _MOIS_FR[m - 1]
            if ym and ym in self._mois_catalog:
                label = f"{label} •"
            self._combo_mois.addItem(label, m)
        for i in range(self._combo_mois.count()):
            if self._combo_mois.itemData(i) == target_month:
                self._combo_mois.setCurrentIndex(i)
                return
        if self._combo_mois.count():
            self._combo_mois.setCurrentIndex(0)

    def _select_period_ym(self, ym: str) -> None:
        tp = _parse_ym(ym)
        if not tp:
            return
        ty, tm = tp
        for i in range(self._combo_annee.count()):
            if self._combo_annee.itemData(i) == ty:
                self._combo_annee.setCurrentIndex(i)
                break
        self._combo_mois.blockSignals(True)
        self._combo_mois.clear()
        self._fill_mois_combo(tm)
        self._combo_mois.blockSignals(False)
        self._sync_view_mois_from_combos()

    def _rebuild_period_combos(self, *, preferred_ym: str | None = None) -> None:
        mois_actif = (load_sme_context().mois_actif or "").strip()
        years = sorted({p[0] for ym in self._mois_catalog if (p := _parse_ym(ym))}, reverse=True)
        target = (preferred_ym or self._view_mois or mois_actif or "").strip() or None
        tp = _parse_ym(target) if target else None
        if tp and tp[0] not in years:
            years = sorted(set(years) | {tp[0]}, reverse=True)
        if not years and tp:
            years = [tp[0]]

        self._combo_annee.blockSignals(True)
        self._combo_mois.blockSignals(True)
        self._combo_annee.clear()
        for y in years:
            self._combo_annee.addItem(str(y), y)
        if tp:
            for i in range(self._combo_annee.count()):
                if self._combo_annee.itemData(i) == tp[0]:
                    self._combo_annee.setCurrentIndex(i)
                    break
        self._combo_mois.clear()
        self._fill_mois_combo(tp[1] if tp else None)
        self._combo_annee.blockSignals(False)
        self._combo_mois.blockSignals(False)
        self._sync_view_mois_from_combos()

        if mois_actif and (p := _parse_ym(mois_actif)):
            self._lbl_mois_actif_ref.setText(f"(mois actif : {_MOIS_FR[p[1] - 1]} {p[0]})")
        else:
            self._lbl_mois_actif_ref.setText("")

    def _reload_lists(self) -> None:
        self._view_data.mark_stale()
        self._reload_lists_async(force=True)

    def _refresh_mois_combo_markers(self) -> None:
        """Met à jour les pastilles « • » sans réinitialiser le mois choisi."""
        if self._combo_annee.count() == 0:
            return
        current_month = self._combo_mois.currentData()
        self._combo_mois.blockSignals(True)
        self._combo_mois.clear()
        self._fill_mois_combo(current_month)
        self._combo_mois.blockSignals(False)
        self._sync_view_mois_from_combos()

    def _reload_lists_async(self, *, force: bool = False) -> None:
        if force:
            self._view_data.mark_stale()
        if not self._view_data.begin_load():
            return
        gen = self._load_generation
        request_mois = (self._view_mois or "").strip() or None
        liste = self._liste
        sat = self._sat
        base_settings = self._settings

        def work():
            if request_mois:
                rows = liste.fetch_all_at_mois(request_mois)
            else:
                rows = liste.fetch_all()
            details_local = sat.load_stages_loc()
            details_ex = sat.load_stages_ex()
            details_interne = sat.load_stages_internal()
            try:
                from sme_gend.config.liste_generale_scope import list_available_export_mois

                catalog = list_available_export_mois(base_settings)
            except Exception:
                catalog = []
            return rows, details_local, details_ex, details_interne, catalog, gen, request_mois

        def done(result: tuple) -> None:
            rows, details_local, details_ex, details_interne, catalog, result_gen, result_mois = result
            if result_gen != self._load_generation:
                return
            if (result_mois or None) != (self._view_mois or None):
                return
            try:
                mois_actif = (load_sme_context().mois_actif or "").strip()
                merged = sorted(
                    {*(ym.strip() for ym in catalog if ym), *([mois_actif] if mois_actif else [])},
                    reverse=True,
                )
                self._mois_catalog = merged
                if self._combo_annee.count() == 0:
                    self._rebuild_period_combos(preferred_ym=self._view_mois)
                else:
                    self._refresh_mois_combo_markers()
                self._details_local = details_local
                self._details_ex = details_ex
                self._details_interne = details_interne
                self._rows_local_all = [r for r in rows if _is_stage_local(r)]
                self._rows_ex_all = [r for r in rows if _is_stage_ex(r)]
                self._rows_interne_all = [r for r in rows if _is_stage_interne(r)]
                self._refresh_nature_combo("local")
                self._refresh_nature_combo("ex")
                self._refresh_nature_combo("interne")
                self._apply_filters("local")
                self._apply_filters("ex")
                self._apply_filters("interne")
                self._view_data.finish_load()
                self._refresh_action_state()
                self._update_summary()
            except Exception:
                self._view_data.fail_load()
                raise

        def on_err(exc: BaseException) -> None:
            self._view_data.fail_load()
            QMessageBox.warning(self, "Stages", str(exc))

        run_in_background(self, work, done, on_error=on_err)

    def _stage_end_reference_date(self) -> str:
        from datetime import date

        from sme_gend.config.liste_generale_scope import reference_date_for_mois

        ym = (self._view_mois or "").strip()
        if ym:
            try:
                return reference_date_for_mois(ym).isoformat()
            except ValueError:
                pass
        return date.today().isoformat()

    def _period_label(self) -> str:
        if self._view_mois:
            p = _parse_ym(self._view_mois)
            if p:
                return f"{_MOIS_FR[p[1] - 1]} {p[0]}"
            return self._view_mois
        mois_actif = (load_sme_context().mois_actif or "").strip()
        if mois_actif and (p := _parse_ym(mois_actif)):
            return f"{_MOIS_FR[p[1] - 1]} {p[0]} (mois actif)"
        return "Mois actif"

    def _update_summary(self) -> None:
        period = self._period_label()
        kind = self._active_stage_kind()
        n_active = len(self._rows_for(kind))
        if self._view_mois:
            scope = (
                f"Effectif et créations/modifications sur le mois consulté ({period})."
            )
        else:
            scope = "Effectif du mois actif SME — les actions modifient la situation officielle."
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        actif = canonical_sme_mois_yyyy_mm(load_sme_context().mois_actif or "") or ""
        view_ym = (self._view_mois or "").strip()
        mois_warn = ""
        if view_ym and actif and view_ym != actif:
            mois_warn = (
                f"<br><span style='color:#e65100;'>"
                f"⚠ Mois affiché ({view_ym}) différent du mois actif de session ({actif}) : "
                f"la liste générale par défaut peut ne pas refléter ces modifications."
                f"</span>"
            )
        self._summary.setText(
            f"<b>{period}</b> — "
            f"{len(self._rows_local_all)} local(aux), "
            f"{len(self._rows_ex_all)} extérieur(s), "
            f"{len(self._rows_interne_all)} interne(s) — "
            f"affichés ({stage_kind_label(kind)}) : {n_active} · {scope}"
            f"{mois_warn}"
        )
        self._update_visible_count_label()
        self._summary.setToolTip(self._summary.text())
        self._summary.setVisible(bool(mois_warn))
        if mois_warn:
            self._summary.setText(f'<b>Mois consulté : {view_ym}</b> — mois actif de session : {actif}.')
        self._chip_local.setText(_stat_chip_html("Local", len(self._rows_local_all), bg="#00695c"))
        self._chip_ex.setText(_stat_chip_html("Extérieur", len(self._rows_ex_all), bg="#4527a0"))
        self._chip_interne.setText(_stat_chip_html("Interne", len(self._rows_interne_all), bg="#ef6c00"))

    def _refresh_action_state(self) -> None:
        from sme_gend.services.auth_guard import session_can_edit_effectifs
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        can_edit = session_can_edit_effectifs() and not state.locked
        self._can_edit = can_edit
        if state.locked:
            self._lock_banner.setText(f"🔒 {state.detail}")
            self._lock_banner.show()
        else:
            self._lock_banner.hide()
        self._btn_local_create.setEnabled(can_edit)
        self._btn_local_select_all.setEnabled(bool(self._rows_local))
        self._btn_local_save.setEnabled(can_edit)
        self._btn_local_end.setEnabled(can_edit and bool(self._checked["local"]))
        self._btn_ex_create.setEnabled(can_edit)
        self._btn_ex_select_all.setEnabled(bool(self._rows_ex))
        self._btn_ex_save.setEnabled(can_edit)
        self._btn_ex_end.setEnabled(can_edit and bool(self._checked["ex"]))
        self._btn_interne_create.setEnabled(can_edit)
        self._btn_interne_select_all.setEnabled(bool(self._rows_interne))
        self._btn_interne_save.setEnabled(can_edit)
        self._btn_interne_end.setEnabled(can_edit and bool(self._checked["interne"]))

    def _ensure_grade_sort(self) -> GradeSortHelper:
        if self._grade_sort is None:
            self._grade_sort = grade_sort_helper_from_settings(self._settings)
        return self._grade_sort

    def _ensure_grade_rows(self) -> list:
        if self._grade_rows is None:
            from sme_gend.repositories.grade_repository import GradeRepository

            try:
                self._grade_rows = GradeRepository(self._settings).fetch_all()
            except Exception:
                self._grade_rows = []
        return self._grade_rows

    def _grade_sigle(self, raw: str | None) -> str:
        return format_grade_display(raw, self._ensure_grade_rows()) or (raw or "").strip()

    def _rows_for(self, kind: StageKind) -> list[ListeGeneraleRow]:
        if kind == "local":
            return self._rows_local
        if kind == "ex":
            return self._rows_ex
        return self._rows_interne

    def _tbl_for(self, kind: StageKind) -> QTableWidget:
        if kind == "local":
            return self._tbl_local
        if kind == "ex":
            return self._tbl_ex
        return self._tbl_interne

    def _search_for(self, kind: StageKind) -> QLineEdit:
        if kind == "local":
            return self._search_local
        if kind == "ex":
            return self._search_ex
        return self._search_interne

    def _nature_combo_for(self, kind: StageKind) -> QComboBox:
        if kind == "local":
            return self._nature_local
        if kind == "ex":
            return self._nature_ex
        return self._nature_interne

    def _details_for(self, kind: StageKind) -> dict[str, StageDetail]:
        if kind == "local":
            return self._details_local
        if kind == "ex":
            return self._details_ex
        return self._details_interne

    def _rows_all_for(self, kind: StageKind) -> list[ListeGeneraleRow]:
        if kind == "local":
            return self._rows_local_all
        if kind == "ex":
            return self._rows_ex_all
        return self._rows_interne_all

    def _refresh_nature_combo(self, kind: StageKind) -> None:
        combo = self._nature_combo_for(kind)
        preserve = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        options = build_nature_filter_options(
            self._rows_all_for(kind),
            self._details_for(kind),
        )
        for label, value in options:
            combo.addItem(label, value)
        idx = 0
        if preserve is not None:
            for i in range(combo.count()):
                if combo.itemData(i) == preserve:
                    idx = i
                    break
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self._sync_fiche_nature_suggestions(kind)

    def _sync_fiche_nature_suggestions(self, kind: StageKind) -> None:
        nature_combo, *_rest = self._form_widgets(kind)
        current = _combo_nature_text(nature_combo)
        natures = distinct_stage_natures_from_details(self._details_for(kind))
        nature_combo.blockSignals(True)
        nature_combo.clear()
        for nat in natures:
            nature_combo.addItem(nat)
        _set_combo_nature_text(nature_combo, current or None)
        nature_combo.blockSignals(False)

    def _apply_filters(self, kind: StageKind) -> None:
        q = self._search_for(kind).text()
        nature_filter = self._nature_combo_for(kind).currentData()
        details = self._details_for(kind)
        filtered: list[ListeGeneraleRow] = []
        for r in self._rows_all_for(kind):
            detail = stage_detail_for_row(r, details)
            if not row_matches_stage_search(r, q, detail):
                continue
            if not row_matches_nature_filter(detail, nature_filter):
                continue
            filtered.append(r)
        if kind == "local":
            self._rows_local = filtered
        elif kind == "ex":
            self._rows_ex = filtered
        else:
            self._rows_interne = filtered
        self._fill_table(kind)
        if kind == self._active_stage_kind():
            self._update_summary()
        else:
            self._chip_local.setText(
                _stat_chip_html("Local", len(self._rows_local_all), bg="#00695c")
            )
            self._chip_ex.setText(
                _stat_chip_html("Extérieur", len(self._rows_ex_all), bg="#4527a0")
            )
            self._chip_interne.setText(
                _stat_chip_html("Interne", len(self._rows_interne_all), bg="#ef6c00")
            )

    def _row_key(self, row: ListeGeneraleRow, kind: StageKind) -> str:
        return row.satellite_join_key()

    def _fill_table(self, kind: StageKind) -> None:
        rows = self._rows_for(kind)
        tbl = self._tbl_for(kind)
        grade_helper = self._ensure_grade_sort()
        checked = self._checked[kind]
        tbl.setSortingEnabled(False)
        tbl.blockSignals(True)
        details = self._details_for(kind)
        tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            key = self._row_key(r, kind)
            detail = stage_detail_for_row(r, details)
            chk = QTableWidgetItem("")
            chk.setData(Qt.ItemDataRole.UserRole, key)
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(
                Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked
            )
            tbl.setItem(i, 0, chk)
            id_item = QTableWidgetItem(key)
            id_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 1, id_item)
            name_item = QTableWidgetItem(r.noms or "")
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 2, name_item)
            grade_item = GradeSortTableWidgetItem(r.grade, grade_helper)
            grade_item.setText(self._grade_sigle(r.grade))
            grade_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 3, grade_item)
            structure_item = QTableWidgetItem(r.structure or "")
            structure_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 4, structure_item)
            nature_item = QTableWidgetItem(nature_from_detail(detail))
            nature_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 5, nature_item)
            lieu_item = QTableWidgetItem((detail.lieu_stage if detail else "") or "")
            lieu_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 6, lieu_item)
            dates_item = QTableWidgetItem((detail.date_debut_fin if detail else "") or "")
            dates_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 7, dates_item)
            padmin_item = QTableWidgetItem(r.position_admin or "")
            padmin_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 8, padmin_item)
            reference_item = QTableWidgetItem((detail.reference_dossier if detail else '') or '')
            reference_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 9, reference_item)
            if kind == 'interne':
                session_item = QTableWidgetItem((detail.session if detail else '') or '')
                session_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                tbl.setItem(i, 10, session_item)
        tbl.blockSignals(False)
        tbl.setSortingEnabled(True)
        self._update_checked_label(kind)
        self._sync_select_all_button(kind)
        self._clear_form(kind)

    def _form_widgets(
        self, kind: StageKind
    ) -> tuple[QComboBox, QLineEdit, FrenchDateEdit, FrenchDateEdit]:
        if kind == "local":
            return self._f_local_nature, self._f_local_lieu, self._f_local_deb, self._f_local_fin
        if kind == "ex":
            return self._f_ex_nature, self._f_ex_lieu, self._f_ex_deb, self._f_ex_fin
        return self._f_interne_nature, self._f_interne_lieu, self._f_interne_deb, self._f_interne_fin

    def _agent_header_for(self, kind: StageKind) -> tuple[QLabel, QLabel, QLabel] | None:
        if kind == "local":
            names = ("_lbl_local_agent", "_lbl_local_grade", "_lbl_local_structure")
        elif kind == "ex":
            names = ("_lbl_ex_agent", "_lbl_ex_grade", "_lbl_ex_structure")
        else:
            names = ("_lbl_interne_agent", "_lbl_interne_grade", "_lbl_interne_structure")
        if not all(hasattr(self, n) for n in names):
            return None
        return tuple(getattr(self, n) for n in names)

    def _clear_form(self, kind: StageKind) -> None:
        getattr(self, f'_f_{kind}_reference').clear()
        if kind == 'interne':
            self._f_interne_session.clear()
        header = self._agent_header_for(kind)
        if header:
            agent, grade, structure = header
            agent.setText("Aucune ligne sélectionnée")
            grade.setText("—")
            structure.setText("—")
        nature, lieu, deb, fin = self._form_widgets(kind)
        _clear_combo_nature(nature)
        lieu.clear()
        deb.set_empty()
        fin.set_empty()

    def _selected_row_key(self, kind: StageKind) -> str | None:
        keys = self._selected_row_keys(kind)
        return keys[0] if len(keys) == 1 else None

    def _selected_row_keys(self, kind: StageKind) -> list[str]:
        tbl = self._tbl_for(kind)
        sm = tbl.selectionModel()
        if sm is None:
            return []
        keys: list[str] = []
        seen: set[str] = set()
        for idx in sm.selectedRows():
            item = tbl.item(idx.row(), 0)
            val = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not val:
                continue
            key_s = str(val)
            if key_s in seen:
                continue
            seen.add(key_s)
            keys.append(key_s)
        return keys

    def _active_action_count(self, kind: StageKind) -> int:
        n_chk = len(self._checked[kind])
        if n_chk:
            return n_chk
        return len(self._selected_row_keys(kind))

    def _on_table_selection_changed(self, kind: StageKind) -> None:
        self._update_checked_label(kind)

    def _checked_keys(self, kind: StageKind) -> list[str]:
        return sorted(self._checked[kind])

    def _on_table_item_changed(self, kind: StageKind, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        key_s = str(key)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked[kind].add(key_s)
        else:
            self._checked[kind].discard(key_s)
        self._update_checked_label(kind)
        self._sync_select_all_button(kind)

    def _select_all_button(self, kind: StageKind) -> QPushButton:
        if kind == "local":
            return self._btn_local_select_all
        if kind == "ex":
            return self._btn_ex_select_all
        return self._btn_interne_select_all

    def _sync_select_all_button(self, kind: StageKind) -> None:
        visible_keys = {self._row_key(row, kind) for row in self._rows_for(kind)}
        all_checked = bool(visible_keys) and visible_keys.issubset(self._checked[kind])
        button = self._select_all_button(kind)
        button.setText("Tout décocher" if all_checked else "Tout cocher")
        button.setEnabled(bool(visible_keys))

    def _toggle_select_all(self, kind: StageKind) -> None:
        visible_keys = {self._row_key(row, kind) for row in self._rows_for(kind)}
        if not visible_keys:
            return
        checked = self._checked[kind]
        if visible_keys.issubset(checked):
            checked.difference_update(visible_keys)
        else:
            checked.update(visible_keys)
        self._fill_table(kind)

    def _checked_label_for(self, kind: StageKind) -> QLabel:
        if kind == "local":
            return self._lbl_local_checked
        if kind == "ex":
            return self._lbl_ex_checked
        return self._lbl_interne_checked

    def _update_checked_label(self, kind: StageKind) -> None:
        n_chk = len(self._checked[kind])
        n_sel = len(self._selected_row_keys(kind))
        n_vis = len(self._rows_for(kind))
        parts = [f"{n_chk} coché(s)"]
        if n_sel:
            parts.append(f"{n_sel} sélectionné(s)")
        parts.append(f"{n_vis} affiché(s)")
        self._checked_label_for(kind).setText(" · ".join(parts))
        n_act = self._active_action_count(kind)
        if kind == "local":
            self._btn_local_end.setEnabled(self._can_edit and n_act > 0)
            self._btn_local_create.setEnabled(self._can_edit and n_act > 0)
        elif kind == "ex":
            self._btn_ex_end.setEnabled(self._can_edit and n_act > 0)
            self._btn_ex_create.setEnabled(self._can_edit and n_act > 0)
        else:
            self._btn_interne_end.setEnabled(self._can_edit and n_act > 0)
            self._btn_interne_create.setEnabled(self._can_edit and n_act > 0)

    def _on_select(self, kind: StageKind) -> None:
        key = self._selected_row_key(kind)
        if not key:
            self._clear_form(kind)
            return
        row = self._current_row(kind)
        header = self._agent_header_for(kind)
        if header and row:
            agent, grade, structure = header
            agent.setText(row.noms or self._row_key(row, kind) or "—")
            grade.setText(self._grade_sigle(row.grade) or "—")
            structure.setText(row.structure or "—")
        detail = stage_detail_for_row(row, self._details_for(kind)) if row else None
        deb, fin = _parse_stage_dates(detail)
        nature, lieu, f_deb, f_fin = self._form_widgets(kind)
        _set_combo_nature_text(nature, detail.nature_stage if detail else "")
        lieu.setText(detail.lieu_stage if detail else "")
        getattr(self, f'_f_{kind}_reference').setText(detail.reference_dossier or '' if detail else '')
        if kind == 'interne':
            self._f_interne_session.setText(detail.session or '' if detail else '')
        f_deb.set_from_text(deb)
        f_fin.set_from_text(fin)

    def _current_row(self, kind: StageKind) -> ListeGeneraleRow | None:
        key = self._selected_row_key(kind)
        if not key:
            return None
        for r in self._rows_for(kind):
            if self._row_key(r, kind) == key:
                return r
        return None

    def _rows_for_keys(self, kind: StageKind, keys: list[str]) -> list[ListeGeneraleRow]:
        want = set(keys)
        return [r for r in self._rows_for(kind) if self._row_key(r, kind) in want]

    def _all_rows_for_kind(self, kind: StageKind) -> list[ListeGeneraleRow]:
        if kind == "local":
            return self._rows_local_all
        if kind == "ex":
            return self._rows_ex_all
        return self._rows_interne_all

    def _keys_for_start_action(self, kind: StageKind) -> list[str]:
        keys = self._checked_keys(kind)
        if keys:
            return keys
        return self._selected_row_keys(kind)

    def _resolve_start_stage_rows(
        self, kind: StageKind, keys: list[str]
    ) -> list[ListeGeneraleRow]:
        want = set(keys)
        by_key: dict[str, ListeGeneraleRow] = {}
        for r in self._all_rows_for_kind(kind):
            k = self._row_key(r, kind)
            if k in want:
                by_key[k] = r
        for r in self._personnel_rows_for_view():
            k = self._row_key(r, kind)
            if k in want and k not in by_key:
                by_key[k] = r
        return [by_key[k] for k in keys if k in by_key]

    def _agent_label_for_start_dialog(self, rows: list[ListeGeneraleRow]) -> str:
        if len(rows) == 1:
            r = rows[0]
            g = self._grade_sigle(r.grade) or r.grade or ""
            return f"{r.noms or '—'} — {g} ({r.structure or ''})".strip()
        lines = [f"  • {r.noms or r.id or '—'}" for r in rows[:10]]
        if len(rows) > 10:
            lines.append(f"  … et {len(rows) - 10} autre(s)")
        return f"<b>{len(rows)} personne(s)</b><br/>" + "<br/>".join(lines)

    def _confirm_start_stages(
        self,
        kind: StageKind,
        rows: list[ListeGeneraleRow],
        *,
        target_label: str,
    ) -> bool:
        period = self._period_label()
        preview = "\n".join(f"  • {r.noms or r.id}" for r in rows[:12])
        if len(rows) > 12:
            preview += f"\n  … et {len(rows) - 12} autre(s)"
        text = (
            f"Période : {period}\n\n"
            f"Mettre {len(rows)} personne(s) en <b>{target_label}</b> "
            f"avec la fiche saisie (si renseignée) ?\n\n"
            f"{preview}\n\n"
            "La position administrative et la fiche stage (nature, lieu, dates) "
            "seront mises à jour pour chaque ligne."
        )
        return confirm_yes_no(
            self,
            "Confirmer la mise en stage",
            text,
            default_no=True,
            rich_text=True,
        )

    def _confirm_end_stages(self, kind: StageKind, keys: list[str]) -> bool:
        labels = [r.noms or self._row_key(r, kind) for r in self._rows_for_keys(kind, keys)]
        period = self._period_label()
        kind_label = {
            "local": "stage(s) local(aux)",
            "ex": "stage(s) extérieur(s)",
            "interne": "stage(s) interne(s)",
        }[kind]
        preview_lines = "\n".join(f"  • {lab}" for lab in labels[:12])
        if len(labels) > 12:
            preview_lines += f"\n  … et {len(labels) - 12} autre(s)"
        scope = (
            f"la situation officielle du mois consulté ({period})"
            if self._view_mois
            else f"la situation officielle du mois actif ({period})"
        )
        text = (
            f"Période affichée : {period}\n\n"
            f"Vous allez terminer {len(keys)} {kind_label} et repasser "
            f"{'ces personnes' if len(keys) > 1 else 'cette personne'} "
            f"en position administrative « Présent » et clôturer la fiche stage du mois "
            f"(la date réelle de fin sera demandée à l’étape suivante).\n\n"
            f"{preview_lines}\n\n"
            f"Cette action modifie {scope} : effectifs, liste des présents "
            "et situation numérique du mois concerné."
        )
        return confirm_yes_no(
            self,
            "Confirmer la fin de stage",
            text,
            default_no=True,
        )

    def _check_can_edit(self, *, action: str) -> bool:
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        if not require_permission(
            self, "effectifs.edition",
            message=f"Votre compte n'a pas la permission {action}.",
        ):
            return False
        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        if state.locked:
            QMessageBox.warning(self, "Stages", state.detail)
            return False
        return True

    def _personnel_rows_for_view(self) -> list[ListeGeneraleRow]:
        request_mois = (self._view_mois or "").strip() or None
        if request_mois:
            return self._liste.fetch_all_at_mois(request_mois)
        return self._liste.fetch_all()

    def _personnel_row_by_id(self, row_id: str | None) -> ListeGeneraleRow | None:
        rid = (row_id or "").strip()
        if not rid:
            return None
        for r in self._personnel_rows_for_view():
            if (r.id or "").strip() == rid:
                return r
        return None

    def _prepare_stage_rows(self, rows):
        view_rows = self._liste.fetch_all()
        by_id = {r.id: r for r in view_rows if r.id}
        by_sat = {r.satellite_join_key(): r for r in view_rows if r.satellite_join_key()}
        return {row.id: by_id.get(row.id) or by_sat.get(row.satellite_join_key()) or row for row in rows}

    def _liste_row_for_personnel_update(self, row: ListeGeneraleRow) -> ListeGeneraleRow:
        """Ligne carrière alignée sur la liste générale (est_actuelle / périmètre mois)."""
        try:
            view_rows = self._liste.fetch_all()
        except Exception:
            return row
        by_id = {r.id: r for r in view_rows if r.id}
        by_sat = {
            r.satellite_join_key(): r for r in view_rows if r.satellite_join_key()
        }
        sk = row.satellite_join_key()
        return by_id.get(row.id) or (by_sat.get(sk) if sk else None) or row

    def _invalidate_liste_caches(self) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._scoped_settings)
        invalidate_liste_cache(self._settings)
        win = self.window()
        notify = getattr(win, "notify_effectifs_liste_changed", None)
        if callable(notify):
            notify()

    def _on_add_from_effectif(self, kind: StageKind) -> None:
        if not self._check_can_edit(action="de mettre un agent en stage"):
            return
        from sme_gend.ui.agent_pick_dialog import AgentPickDialog

        dlg = AgentPickDialog(
            self._scoped_settings,
            self,
            active_effectif_only=True,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dlg.selected_row()
        if picked is None:
            return
        row = self._personnel_row_by_id(picked.id) or picked
        self._execute_start_stage(kind, [row])

    def _on_start_stage(self, kind: StageKind) -> None:
        if not self._check_can_edit(action="de mettre un agent en stage"):
            return
        keys = self._keys_for_start_action(kind)
        if not keys:
            QMessageBox.information(
                self,
                "Stages",
                "Cochez une ou plusieurs lignes dans le tableau,\n"
                "sélectionnez une ligne, ou utilisez « Ajouter depuis l'effectif… ».",
            )
            return
        rows = self._resolve_start_stage_rows(kind, keys)
        if not rows:
            QMessageBox.warning(
                self,
                "Stages",
                "Impossible de retrouver les agents cochés dans l'effectif du mois affiché.",
            )
            return
        self._execute_start_stage(kind, rows)

    def _execute_start_stage(self, kind: StageKind, rows: list[ListeGeneraleRow]) -> None:
        if not rows:
            return
        target_padmin = padmin_for_stage_kind(kind)
        target_label = stage_kind_label(kind)
        tgt_p = _norm_padmin(target_padmin)

        cross_type = [
            r
            for r in rows
            if _is_any_stage_row(r) and _norm_padmin(r.position_admin) != tgt_p
        ]
        if cross_type:
            names = ", ".join((r.noms or r.id or "?") for r in cross_type[:5])
            if len(cross_type) > 5:
                names += f" (+{len(cross_type) - 5})"
            if not confirm_yes_no(
                self,
                "Changer de type de stage",
                f"{len(cross_type)} personne(s) sont déjà en stage (autre type) :\n{names}\n\n"
                f"Les repasser en « {target_padmin} » ?",
                default_no=True,
            ):
                return

        try:
            personnel_snapshots = self._prepare_stage_rows(rows)
            stage_snapshots = {row.id: self._sat.stage_history_snapshot(row.satellite_join_key()) for row in rows}
            detail_snapshots = {row.id: stage_detail_for_row(row, self._details_for(kind)) for row in rows}
        except Exception as exc:
            QMessageBox.critical(self, "Stages", f"Lecture préalable impossible :\n{exc}")
            return
        with_fiche = kind != "interne"
        start_dlg = StageStartDialog(
            self,
            agent_label=self._agent_label_for_start_dialog(rows),
            stage_label=target_label,
            with_fiche=with_fiche,
        )
        if start_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._confirm_start_stages(kind, rows, target_label=target_label):
            return

        detail = start_dlg.stage_detail()
        deb, fin = start_dlg.stage_dates()
        type_code = _stage_type_code(kind)
        n_ok = 0
        errs: list[str] = []
        from sme_gend.services.personnel_service import PersonnelService, _personnel_write

        personnel = PersonnelService(self._scoped_settings)
        for row in rows:
            cur = personnel_snapshots[row.id]
            position_changed = _norm_padmin(cur.position_admin) != tgt_p
            after_position = replace(cur, position_admin=target_padmin)
            try:
                with _personnel_write(self._scoped_settings, reason="start_stage"):
                    if position_changed:
                        personnel.update_personnel(
                            cur,
                            after_position,
                            edit_motif=f"Mise en stage {target_label}",
                        )
                    sat_key = cur.satellite_join_key()
                    if not sat_key:
                        raise ValueError("Identifiant de liaison satellite vide.")
                    self._sat.clear_other_stages_for_period(sat_key, keep_type=type_code, expected=stage_snapshots[row.id])
                    if with_fiche or detail is not None:
                        self._sat.upsert_stage(
                            sat_key,
                            detail or StageDetail(None, None, None), expected=detail_snapshots[row.id],
                            type_code=type_code,
                            date_debut=deb,
                            date_fin=fin,
                        )
                n_ok += 1
            except Exception as exc:
                message = f"{cur.noms or cur.id}: {exc}"
                errs.append(message)

        self._invalidate_liste_caches()
        self._checked[kind].clear()
        self._view_data.mark_stale()
        self._reload_lists_async(force=True)
        msg = f"{n_ok} personne(s) — {target_label} appliqué pour {self._period_label()}."
        if errs:
            msg += "\n\nÉchecs :\n" + "\n".join(errs[:8])
            if len(errs) > 8:
                msg += f"\n… (+{len(errs) - 8})"
        QMessageBox.information(self, "Stages", msg)

    def _save_fiche(self, kind: StageKind) -> None:
        if not self._check_can_edit(action="de modifier les fiches stage"):
            return
        row = self._current_row(kind)
        if row is None:
            QMessageBox.information(self, "Stages", "Sélectionnez une ligne dans le tableau.")
            return
        key = row.satellite_join_key()
        if not key:
            QMessageBox.warning(self, "Stages", "Identifiant de liaison vide.")
            return
        nature, lieu, f_deb, f_fin = self._form_widgets(kind)
        deb = f_deb.text_for_validation().strip()
        fin = f_fin.text_for_validation().strip()
        if not _combo_nature_text(nature):
            QMessageBox.warning(self, "Stages", "La nature du stage est obligatoire.")
            return
        if kind != 'interne' and not deb:
            QMessageBox.warning(self, "Stages", "La date de début est obligatoire.")
            return
        detail = StageDetail(
            nature_stage=_combo_nature_text(nature) or None,
            lieu_stage=lieu.text().strip() or None,
            reference_dossier=getattr(self, f'_f_{kind}_reference').text().strip() or None,
            session=(self._f_interne_session.text().strip() or None) if kind == 'interne' else None,
            date_debut_fin=_stage_ddf(deb, fin),
        )
        type_code = _stage_type_code(kind)
        try:
            self._sat.upsert_stage(
                key,
                detail, expected=stage_detail_for_row(row, self._details_for(kind)),
                type_code=type_code,
                date_debut=deb or None,
                date_fin=fin or None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Stages", f"Enregistrement impossible :\n{exc}")
            return
        self._invalidate_liste_caches()
        if kind == "local":
            try:
                self._details_local = self._sat.load_stages_loc()
            except Exception as exc:
                QMessageBox.warning(self, "Relecture des données",
                    f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
                return
        elif kind == "ex":
            try:
                self._details_ex = self._sat.load_stages_ex()
            except Exception as exc:
                QMessageBox.warning(self, "Relecture des données",
                    f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
                return
        else:
            try:
                self._details_interne = self._sat.load_stages_internal()
            except Exception as exc:
                QMessageBox.warning(self, "Relecture des données",
                    f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
                return
        self._refresh_nature_combo(kind)
        self._apply_filters(kind)
        QMessageBox.information(self, "Stages", "Fiche enregistrée.")
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _end_checked_stages(self, kind: StageKind) -> None:
        if not self._check_can_edit(action="de modifier la position administrative"):
            return
        keys = self._keys_for_start_action(kind)
        if not keys:
            QMessageBox.information(
                self,
                "Stages",
                "Cochez une ou plusieurs lignes, ou sélectionnez des lignes "
                "(clic, Maj+clic, glisser) pour terminer.",
            )
            return
        if not self._confirm_end_stages(kind, keys):
            return
        rows = self._resolve_start_stage_rows(kind, keys)
        if not rows:
            QMessageBox.warning(
                self,
                "Stages",
                "Impossible de retrouver les agents dans l'effectif du mois affiché.",
            )
            return
        fin_ref = self._stage_end_reference_date()
        from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog

        try:
            personnel_snapshots = self._prepare_stage_rows(rows)
            detail_snapshots = {row.id: stage_detail_for_row(row, self._details_for(kind)) for row in rows}
        except Exception as exc:
            QMessageBox.critical(self, "Stages", f"Lecture préalable impossible :\n{exc}")
            return
        diploma_choice = StageEndDiplomaDialog.run(
            self,
            agent_count=len(rows),
            default_date_iso=fin_ref,
        )
        if diploma_choice is False:
            return
        diploma_payload: dict | None = diploma_choice if isinstance(diploma_choice, dict) else None
        stage_end_reference = None
        if diploma_payload is not None:
            diploma_payload = dict(diploma_payload)
            stage_end_reference = diploma_payload.pop('_stage_reference', None)
            fin_ref = diploma_payload.pop('_stage_end_date', fin_ref)
            diploma_payload = diploma_payload or None
        from sme_gend.services.stage_completion import validate_stage_end
        try:
            fin_ref = validate_stage_end(fin_ref, self._scoped_settings.mysql_view_mois,
                [_parse_stage_dates(detail)[0] for detail in detail_snapshots.values() if detail])
        except ValueError as exc:
            QMessageBox.warning(self, "Fin de stage", f"Aucune modification enregistrée :\n{exc}")
            return
        if diploma_payload is not None:
            from sme_gend.services.sensitive_data_policy import assert_may_add_diplome
            try:
                assert_may_add_diplome()
            except PermissionError as exc:
                QMessageBox.warning(self, "Stages", f"Aucune modification enregistrée :\n{exc}")
                return

        n_ok = 0
        n_dip = 0
        n_dip_existing = 0
        n_already = 0
        diploma_results: list[str] = []
        committed_warnings: list[str] = []
        errs: list[str] = []
        from sme_gend.services.agent_dossier_service import AgentDossierService
        from sme_gend.services.personnel_service import PersonnelService, _personnel_write
        from sme_gend.repositories.mysql_connection import CommittedMirrorError

        personnel = PersonnelService(self._scoped_settings)
        dossier_svc = AgentDossierService(self._scoped_settings) if diploma_payload else None
        type_code = _stage_type_code(kind)
        details_map = self._details_for(kind)
        from sme_gend.services.temporal_changes import temporal_capability_enabled
        modern_temporal = temporal_capability_enabled(self._scoped_settings)
        for row in rows:
            cur = personnel_snapshots[row.id]
            sat_key = cur.satellite_join_key()
            already_present = _norm_padmin(cur.position_admin) == _norm_padmin("Présent")
            stage_closed = False
            existing_diploma = None
            try:
                with _personnel_write(self._scoped_settings, reason="end_stage"):
                    stage_source_id = None
                    if modern_temporal:
                        from sme_gend.repositories.write_guard import lock_agent
                        from sme_gend.repositories.mysql_connection import MySQLConnection
                        from sme_gend.services.activity_completion import _read_source
                        aid = lock_agent(self._scoped_settings, carriere_id=cur.id)
                        if aid is None:
                            raise ValueError('Agent introuvable. Rechargez les données.')
                        if personnel.get_by_id(cur.id) != cur:
                            raise ValueError('La situation de cet agent a changé. Rechargez les données avant de terminer son stage.')
                        if not already_present and _norm_padmin(cur.position_admin) != _norm_padmin(padmin_for_stage_kind(kind)):
                            raise ValueError('Cet agent possède maintenant une autre position. Sa nouvelle activité a été conservée.')
                        with MySQLConnection(self._scoped_settings).connect() as connection:
                            with connection.cursor() as cursor:
                                stage_source_id = int(_read_source(self._scoped_settings, cursor, aid, 'stage',
                                                                  detail_snapshots[row.id])['id'])
                    if not already_present and not modern_temporal:
                        personnel.update_personnel(
                            cur,
                            replace(cur, position_admin="Présent"),
                            edit_motif="Fin de stage depuis le module Stages",
                        )
                    if sat_key:
                        detail = detail_snapshots[row.id]
                        deb_s, _fin_old = _parse_stage_dates(detail)
                        if detail and (
                            (detail.nature_stage or "").strip()
                            or (detail.lieu_stage or "").strip()
                            or deb_s or detail.session or detail.reference_dossier
                        ):
                            closed = StageDetail(
                                nature_stage=detail.nature_stage,
                                lieu_stage=detail.lieu_stage,
                                reference_dossier=stage_end_reference or detail.reference_dossier,
                                session=detail.session,
                                date_debut_fin=_stage_ddf(deb_s, fin_ref),
                            )
                            self._sat.upsert_stage(
                                sat_key,
                                closed, expected=detail_snapshots[row.id],
                                type_code=type_code,
                                date_debut=deb_s or None,
                                date_fin=fin_ref,
                            )
                            stage_closed = True
                    if dossier_svc is not None and diploma_payload is not None:
                        from sme_gend.repositories.write_guard import lock_agent
                        from sme_gend.services.stage_completion import find_identical_diploma
                        aid = dossier_svc.agent_id_for_row(cur)
                        if lock_agent(self._scoped_settings, agent_id=aid) is None:
                            raise ValueError('Agent introuvable. Actualisez les données.')
                        existing_diploma = find_identical_diploma(dossier_svc.list_diplomes(cur), diploma_payload)
                        diploma_id = existing_diploma.id if existing_diploma else dossier_svc.add_diplome(cur, **diploma_payload)
                    if modern_temporal and stage_closed:
                        from sme_gend.services.stage_completion import normalize_validated_stage_return
                        normalize_validated_stage_return(self._scoped_settings, aid, type_code, fin_ref,
                                                         source_id=stage_source_id)
                    elif not already_present:
                        from sme_gend.services.temporal_events import record_position
                        from sme_gend.services.access_date_parse import parse_access_date
                        record_position(self._scoped_settings,AgentDossierService(self._scoped_settings).agent_id_for_row(cur), 'Présent',parse_access_date(fin_ref),
                            reference=f'sgi:auto:stage_end:{cur.id}:{type_code}:{fin_ref}')
                if already_present:
                    n_already += 1
                if stage_closed or not already_present:
                    n_ok += 1
                if diploma_payload is not None:
                    if existing_diploma:
                        n_dip_existing += 1
                    else:
                        n_dip += 1
                    try:
                        from sme_gend.services.stage_completion import diploma_display_result
                        from sme_gend.config.liste_generale_scope import reference_date_for_mois
                        from sme_gend.services.access_date_parse import parse_access_date
                        month = self._scoped_settings.mysql_view_mois
                        cutoff = reference_date_for_mois(month) if month else parse_access_date(fin_ref)
                        result = diploma_display_result(diploma_payload, dossier_svc.list_diplomes(cur), diploma_id, cutoff)
                    except Exception:
                        result = 'diplôme enregistré ; vérification de l’affichage indisponible, actualisez la SME'
                    diploma_results.append(f'{cur.noms or cur.id} : {result}.')
            except CommittedMirrorError:
                if stage_closed or not already_present:
                    n_ok += 1
                if already_present:
                    n_already += 1
                if diploma_payload is not None:
                    if existing_diploma:
                        n_dip_existing += 1
                    else:
                        n_dip += 1
                committed_warnings.append(f'{cur.noms or cur.id} : enregistrement confirmé dans MySQL ; copie locale du journal à vérifier. Ne recommencez pas l’opération.')
            except Exception as exc:
                errs.append(f"{cur.noms or cur.id}: {exc} — aucune modification enregistrée pour cet agent.")
        self._invalidate_liste_caches()
        self._checked[kind].clear()
        self._view_data.mark_stale()
        self._reload_lists_async(force=True)
        msg = (
            f"{n_ok} stage(s) terminé(s) au {fin_ref} — position « Présent » appliquée ou conservée "
            f"(fiches du mois clôturées si présentes)."
        )
        if n_dip:
            msg += f"\n{n_dip} diplôme(s) ajouté(s) au dossier."
        if n_dip_existing:
            msg += f"\n{n_dip_existing} diplôme(s) déjà présent(s), conservé(s) sans doublon."
        if diploma_results:
            msg += "\n" + "\n".join(diploma_results[:8])
            if len(diploma_results) > 8:
                msg += "\nLes autres diplômes ont été enregistrés ; consultez les dossiers pour le détail."
        if n_already:
            msg += f"\n{n_already} agent(s) déjà en position « Présent »."
        if committed_warnings:
            msg += "\n\n" + "\n".join(committed_warnings[:8])
        if errs:
            msg += "\n\nÉchecs :\n" + "\n".join(errs[:8])
        QMessageBox.information(self, "Stages", msg)

    def _on_export(self, kind: StageKind) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.ui.export_snapshot import visible_export_rows
        from copy import deepcopy
        try:
            rows = visible_export_rows(self._tbl_for(kind), self._rows_for(kind), key=lambda row: self._row_key(row, kind))
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        details = deepcopy(self._details_for(kind))
        grades = deepcopy(self._ensure_grade_rows())
        if not rows:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        if kind == "interne":
            default = "Stage_interne.xlsx"
            title = "Stage interne"
        elif kind == "local":
            default = "Stages_locaux.xlsx"
            title = "Stages locaux"
        else:
            default = "Stages_exterieurs.xlsx"
            title = "Stages extérieurs"
        settings = getattr(self, "_scoped_settings", getattr(self, "_settings", None))
        dest, _ = QFileDialog.getSaveFileName(
            self, f"Exporter {title.lower()}", str(Path.home() / default), "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        try:
            if kind == "interne":
                export_stage_interne_workbook(Path(dest), rows=rows, details=details, grades=grades, settings=settings)
            else:
                export_stages_workbook(Path(dest), title=title, rows=rows, details=details, grades=grades, settings=settings)
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Export impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
