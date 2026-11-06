"""Malades (agent_malade) et positions Punis / Prison MA — saisie liée à la liste générale."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QTabWidget,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.malade_punis_export import export_malades_workbook, export_punis_workbook
from sme_gend.services.malade_punis_list_filter import row_matches_malade_search
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
    grade_sort_helper_from_settings,
)
from sme_gend.ui.widgets.date_input import FrenchDateEdit
from sme_gend.ui.list_table_filter import row_matches_search
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.services.numerique_padmin import (
    PADMIN_MALADE,
    PADMIN_PRISON_MA,
    PADMIN_PUNIS,
)


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
    return (p or "").strip().casefold()


def _is_malade_row(r: ListeGeneraleRow) -> bool:
    return _norm_padmin(r.position_admin) == PADMIN_MALADE.casefold()


def _is_punis_row(r: ListeGeneraleRow) -> bool:
    p = _norm_padmin(r.position_admin)
    return p == PADMIN_PUNIS.casefold() or p == PADMIN_PRISON_MA.casefold()


# Libellés exacts stockés en base (alignés numerique_padmin + positions courantes).
_PADMIN_CHOICES: tuple[str, ...] = (
    "Présent",
    "Stage interne",
    "Malade",
    "Punis",
    "Prison MA",
    "Détaché",
    "Mission",
    "CongéAdmin",
    "CongéExp",
    "StageLocal",
    "StageEx",
    "Déserteur",
    "Inst Mise Route",
)


def _stat_chip_html(label: str, count: int, *, bg: str, fg: str = "#ffffff") -> str:
    return (
        f"<span style='background:{bg};color:{fg};padding:3px 10px;"
        f"border-radius:10px;font-size:11px;font-weight:600;'>"
        f"{label} : {count}</span>"
    )


def _make_search_field(
    parent: QWidget,
    *,
    placeholder: str,
    on_change,
) -> QLineEdit:
    le = QLineEdit(parent)
    le.setObjectName("gainPerteSearch")
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.textChanged.connect(on_change)
    return le


class MaladePunisView(QWidget):
    """Onglets : fiche maladie (agent_malade) ; positions Punis / Prison MA (carrière)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        # Mois consulté : None = mois actif de session. Sinon YYYY-MM (archive).
        self._view_mois: str | None = None
        self._mois_catalog: list[str] = []
        self._scoped_settings: Settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._view_data = ViewDataState(stale=True)
        self._malade_rows_all: list[ListeGeneraleRow] = []
        self._malade_rows_history_extra: list[ListeGeneraleRow] = []
        self._malade_details: dict[str, MaladeDetail] = {}
        self._malade_checked: set[str] = set()
        self._punis_checked: set[str] = set()
        self._punis_rows_all: list[ListeGeneraleRow] = []
        self._malade_rows: list[ListeGeneraleRow] = []
        self._punis_rows: list[ListeGeneraleRow] = []
        self._grade_sort: GradeSortHelper | None = None
        self._can_edit = True

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)

        title = QLabel(
            "<h2 style='margin:0;padding:0;'>Malades &amp; Punis</h2>",
            textFormat=Qt.TextFormat.RichText,
        )

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
        period_row.addWidget(self._combo_mois)
        self._lbl_mois_actif_ref = QLabel()
        self._lbl_mois_actif_ref.setObjectName("gainPerteCount")
        period_row.addWidget(self._lbl_mois_actif_ref)
        period_row.addSpacing(12)
        self._chip_malades = QLabel()
        self._chip_punis = QLabel()
        self._chip_history = QLabel()
        for chip in (self._chip_malades, self._chip_punis, self._chip_history):
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

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_malade_tab(), "Malades")
        self._tabs.addTab(self._build_punis_tab(), "Punis & détention")
        self._tabs.currentChanged.connect(lambda _i: self._update_summary())
        root.addWidget(self._tabs, stretch=1)

    def _build_malade_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        lay.setContentsMargins(0, 4, 0, 0)

        action_row = QFrame()
        action_row.setObjectName("gainPerteToolbar")
        from sme_gend.ui.responsive_panels import WrapLayout
        tb = WrapLayout(action_row)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(6)

        self._btn_m_del = QPushButton("Supprimer la fiche…")
        self._btn_m_del.setToolTip("Supprime la fiche maladie de la ligne sélectionnée.")
        self._btn_m_del.clicked.connect(self._delete_malade_fiche)

        self._btn_m_bulk_del = QPushButton("Supprimer les fiches cochées")
        self._btn_m_bulk_del.setEnabled(False)
        self._btn_m_bulk_del.setToolTip("Supprime toutes les fiches maladie cochées dans le tableau.")
        self._btn_m_bulk_del.clicked.connect(self._delete_checked_malade_fiches)

        self._btn_m_refresh = QPushButton("Rafraîchir")
        self._btn_m_refresh.setToolTip("Recharger les listes depuis la base.")
        self._btn_m_refresh.clicked.connect(self._reload_malades)

        self._btn_m_export = QPushButton("Exporter vers Excel…")
        self._btn_m_export.setToolTip("Exporter les malades visibles (filtre appliqué) vers Excel.")
        self._btn_m_export.clicked.connect(self._on_export_malades)

        self._search_malade = _make_search_field(
            w,
            placeholder="Nom, grade, structure, pathologie, obs.…",
            on_change=lambda: self._apply_malade_search(),
        )
        self._search_malade.setMinimumWidth(160)
        self._chk_malade_history = QCheckBox("Historique")
        self._chk_malade_history.setToolTip(
            "Affiche aussi les fiches maladie enregistrées alors que le padmin "
            "n'est plus « Malade »."
        )
        self._chk_malade_history.toggled.connect(lambda _v: self._apply_malade_search())
        self._lbl_malade_checked = QLabel("0 coché(s)")
        self._lbl_malade_checked.setObjectName("gainPerteCount")
        self._btn_m_select_all = QPushButton("Tout cocher")
        self._btn_m_select_all.setToolTip("Cocher toutes les lignes malades affichées.")
        self._btn_m_select_all.clicked.connect(self._toggle_malade_select_all)

        tb.addWidget(QLabel("Rechercher"))
        tb.addWidget(self._search_malade)
        tb.addWidget(self._chk_malade_history)
        tb.addWidget(self._lbl_malade_checked)
        tb.addWidget(self._btn_m_select_all)
        tb.addWidget(self._btn_m_del)
        tb.addWidget(self._btn_m_bulk_del)
        tb.addWidget(self._btn_m_refresh)
        tb.addWidget(self._btn_m_export)
        lay.addWidget(action_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        self._tbl_malade = QTableWidget()
        self._tbl_malade.setObjectName("gainPerteTable")
        self._tbl_malade.setColumnCount(8)
        self._tbl_malade.setHorizontalHeaderLabels(
            [
                "✓",
                "Nom",
                "Grade",
                "Structure",
                "Date constat",
                "Pathologie",
                "Obs.",
                "Padmin",
            ]
        )
        hh = self._tbl_malade.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 6, 7):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl_malade.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_malade.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._tbl_malade.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_malade.setAlternatingRowColors(True)
        self._tbl_malade.verticalHeader().setVisible(False)
        self._tbl_malade.setSortingEnabled(True)
        self._tbl_malade.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._tbl_malade.setMinimumHeight(280)
        self._tbl_malade.itemSelectionChanged.connect(self._on_malade_select)
        self._tbl_malade.itemChanged.connect(self._on_malade_item_changed)
        self._tbl_malade.itemDoubleClicked.connect(self._edit_malade_from_table)
        split.addWidget(self._tbl_malade)

        self._form_malade_box = QGroupBox("Fiche maladie")
        self._form_malade_box.setObjectName("limiteAgeDetailGroup")
        self._form_malade_box.setMaximumWidth(280)
        fl = QFormLayout(self._form_malade_box)
        self._lbl_malade_selection = QLabel("Aucune ligne sélectionnée")
        self._lbl_malade_selection.setObjectName("gainPerteCount")
        self._lbl_malade_selection.setWordWrap(True)
        fl.addRow("Agent :", self._lbl_malade_selection)
        self._lbl_malade_grade = QLabel("—")
        self._lbl_malade_structure = QLabel("—")
        self._lbl_malade_structure.setWordWrap(True)
        fl.addRow("Grade :", self._lbl_malade_grade)
        fl.addRow("Structure :", self._lbl_malade_structure)
        self._m_date = FrenchDateEdit()
        self._m_date.setToolTip("Date du constat (jj/mm/aaaa)")
        self._m_path = QLineEdit()
        self._m_path.setPlaceholderText("Pathologie")
        self._m_obs = QLineEdit()
        self._m_obs.setPlaceholderText("Observations")
        fl.addRow("Date constat :", self._m_date)
        fl.addRow("Pathologie :", self._m_path)
        fl.addRow("Observations :", self._m_obs)
        self._btn_m_save = QPushButton("Enregistrer la fiche")
        self._btn_m_save.setObjectName("gainPerteBtnPrimary")
        self._btn_m_save.setToolTip("Enregistre la fiche maladie de la ligne sélectionnée.")
        self._btn_m_save.clicked.connect(self._save_malade_fiche)
        fl.addRow("", self._btn_m_save)
        split.addWidget(self._form_malade_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 280])
        lay.addWidget(split, stretch=1)
        return w

    def _build_punis_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        lay.setContentsMargins(0, 4, 0, 0)

        action_row = QFrame()
        action_row.setObjectName("gainPerteToolbar")
        from sme_gend.ui.responsive_panels import WrapLayout
        tb = WrapLayout(action_row)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(6)

        self._search_punis = _make_search_field(
            w,
            placeholder="Nom, grade, structure, motif…",
            on_change=lambda: self._apply_punis_search(),
        )
        self._search_punis.setMinimumWidth(160)
        self._lbl_punis_checked = QLabel("0 coché(s)")
        self._lbl_punis_checked.setObjectName("gainPerteCount")
        self._btn_p_select_all = QPushButton("Tout cocher")
        self._btn_p_select_all.setToolTip("Cocher toutes les lignes punis affichées.")
        self._btn_p_select_all.clicked.connect(self._toggle_punis_select_all)
        self._combo_new_padmin = QComboBox()
        self._combo_new_padmin.setObjectName("gainPertePeriodCombo")
        self._combo_new_padmin.setMinimumWidth(140)
        for lab in _PADMIN_CHOICES:
            self._combo_new_padmin.addItem(lab)

        self._btn_p_apply = QPushButton("Appliquer la position (cochés)")
        self._btn_p_apply.setObjectName("gainPerteBtnPrimary")
        self._btn_p_apply.setToolTip(
            "Applique la position choisie aux lignes cochées (ou à la sélection si aucune case)."
        )
        self._btn_p_apply.clicked.connect(self._apply_padmin_selection)

        self._btn_p_reset = QPushButton("Remettre à Présent (cochés)")
        self._btn_p_reset.setToolTip(
            "Repasser en « Présent » les lignes cochées (ou la sélection si aucune case)."
        )
        self._btn_p_reset.clicked.connect(self._reset_punis_selection)

        self._btn_p_refresh = QPushButton("Rafraîchir")
        self._btn_p_refresh.clicked.connect(self._reload_punis)

        self._btn_p_export = QPushButton("Exporter…")
        self._btn_p_export.clicked.connect(self._on_export_punis)

        tb.addWidget(QLabel("Rechercher"))
        tb.addWidget(self._search_punis)
        tb.addWidget(self._lbl_punis_checked)
        tb.addWidget(self._btn_p_select_all)
        tb.addWidget(self._combo_new_padmin)
        tb.addWidget(self._btn_p_apply)
        tb.addWidget(self._btn_p_reset)
        tb.addWidget(self._btn_p_refresh)
        tb.addWidget(self._btn_p_export)
        lay.addWidget(action_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        self._tbl_punis = QTableWidget()
        self._tbl_punis.setObjectName("gainPerteTable")
        self._tbl_punis.setColumnCount(7)
        self._tbl_punis.setHorizontalHeaderLabels(
            [
                "✓",
                "Nom",
                "Grade",
                "Structure",
                "Padmin actuel",
                "Motif",
                "Id liste",
            ]
        )
        h2 = self._tbl_punis.horizontalHeader()
        h2.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h2.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h2.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h2.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h2.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._tbl_punis.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_punis.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._tbl_punis.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_punis.setAlternatingRowColors(True)
        self._tbl_punis.verticalHeader().setVisible(False)
        self._tbl_punis.setSortingEnabled(True)
        self._tbl_punis.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._tbl_punis.setMinimumHeight(280)
        self._tbl_punis.itemSelectionChanged.connect(self._on_punis_select)
        self._tbl_punis.itemChanged.connect(self._on_punis_item_changed)
        split.addWidget(self._tbl_punis)

        self._form_punis_box = QGroupBox("Fiche punition & détention")
        self._form_punis_box.setObjectName("limiteAgeDetailGroup")
        self._form_punis_box.setMaximumWidth(280)
        pfl = QFormLayout(self._form_punis_box)
        self._lbl_punis_selection = QLabel("Aucune ligne sélectionnée")
        self._lbl_punis_selection.setObjectName("gainPerteCount")
        self._lbl_punis_selection.setWordWrap(True)
        pfl.addRow("Agent :", self._lbl_punis_selection)
        self._lbl_punis_grade = QLabel("—")
        self._lbl_punis_structure = QLabel("—")
        self._lbl_punis_structure.setWordWrap(True)
        self._lbl_punis_padmin = QLabel("—")
        pfl.addRow("Grade :", self._lbl_punis_grade)
        pfl.addRow("Structure :", self._lbl_punis_structure)
        pfl.addRow("Padmin :", self._lbl_punis_padmin)
        self._p_motif = QTextEdit()
        self._p_motif.setPlaceholderText("Motif de la punition ou de la mesure disciplinaire…")
        self._p_motif.setMaximumHeight(88)
        self._p_motif.setToolTip(
            "Enregistré dans le champ « Observations » de la fiche agent (liste générale). "
            "Utilisé aussi comme motif d'audit lors d'un changement de position."
        )
        pfl.addRow("Motif :", self._p_motif)
        self._btn_p_save = QPushButton("Enregistrer le motif")
        self._btn_p_save.setObjectName("gainPerteBtnPrimary")
        self._btn_p_save.setToolTip(
            "Enregistre le motif pour la ligne sélectionnée (une seule à la fois)."
        )
        self._btn_p_save.clicked.connect(self._save_punis_motif)
        pfl.addRow("", self._btn_p_save)
        split.addWidget(self._form_punis_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([820, 280])
        lay.addWidget(split, stretch=1)
        return w

    def _update_punis_checked_label(self) -> None:
        from sme_gend.ui.check_all import sync_check_all
        sync_check_all(self._btn_p_select_all, (r.id for r in self._punis_rows if r.id), self._punis_checked)
        n = len(self._punis_checked)
        self._lbl_punis_checked.setText(
            f"{n} coché(s) · {len(self._punis_rows)} affiché(s)"
        )
        has_targets = bool(self._punis_target_ids())
        if hasattr(self, "_btn_p_apply"):
            self._btn_p_apply.setEnabled(self._can_edit and has_targets)
        if hasattr(self, "_btn_p_reset"):
            self._btn_p_reset.setEnabled(self._can_edit and has_targets)

    def _punis_id_from_row(self, row_index: int) -> str | None:
        item = self._tbl_punis.item(row_index, 6)
        if item is None:
            return None
        rid = item.data(Qt.ItemDataRole.UserRole)
        return rid if rid else None

    def _punis_target_ids(self) -> list[str]:
        if self._punis_checked:
            return sorted(self._punis_checked)
        ids: list[str] = []
        for ix in self._tbl_punis.selectionModel().selectedRows():
            rid = self._punis_id_from_row(ix.row())
            if rid:
                ids.append(rid)
        return ids

    def _toggle_malade_select_all(self) -> None:
        if not self._malade_rows:
            return
        keys = {
            r.satellite_join_key()
            for r in self._malade_rows
            if r.satellite_join_key()
        }
        if keys <= self._malade_checked:
            self._malade_checked -= keys
        else:
            self._malade_checked |= keys
        self._fill_malade_table()

    def _toggle_punis_select_all(self) -> None:
        if not self._punis_rows:
            return
        ids = {r.id for r in self._punis_rows if r.id}
        if ids <= self._punis_checked:
            self._punis_checked -= ids
        else:
            self._punis_checked |= ids
        self._fill_punis_table()

    def _on_punis_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        rid = item.data(Qt.ItemDataRole.UserRole)
        if not rid:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._punis_checked.add(rid)
        else:
            self._punis_checked.discard(rid)
        self._update_punis_checked_label()

    def _clear_punis_form(self) -> None:
        self._lbl_punis_selection.setText("Aucune ligne sélectionnée")
        self._lbl_punis_grade.setText("—")
        self._lbl_punis_structure.setText("—")
        self._lbl_punis_padmin.setText("—")
        self._p_motif.clear()

    def _primary_punis_row_id(self) -> str | None:
        sel = self._tbl_punis.selectionModel().selectedRows()
        if not sel:
            return None
        return self._punis_id_from_row(sel[0].row())

    def _current_punis_row(self) -> ListeGeneraleRow | None:
        pid = self._primary_punis_row_id()
        if not pid:
            return None
        for r in self._punis_rows:
            if r.id == pid:
                return r
        return None

    def _on_punis_select(self) -> None:
        sel = self._tbl_punis.selectionModel().selectedRows()
        if not sel:
            self._clear_punis_form()
            return
        if len(sel) > 1:
            self._lbl_punis_selection.setText(f"{len(sel)} lignes sélectionnées")
            self._lbl_punis_grade.setText("—")
            self._lbl_punis_structure.setText("—")
            self._lbl_punis_padmin.setText("—")
            self._p_motif.clear()
            self._btn_p_save.setEnabled(False)
            return
        row = self._current_punis_row()
        if row is None:
            self._clear_punis_form()
            return
        self._lbl_punis_selection.setText(row.noms or row.id or "—")
        self._lbl_punis_grade.setText(self._ensure_grade_sort().display_label(row.grade) or "—")
        self._lbl_punis_structure.setText(row.structure or "—")
        self._lbl_punis_padmin.setText(row.position_admin or "—")
        self._p_motif.setPlainText(row.obs or "")
        self._btn_p_save.setEnabled(self._can_edit)

    def _period_label(self) -> str:
        if self._view_mois:
            p = _parse_ym(self._view_mois)
            if p:
                return f"{_MOIS_FR[p[1] - 1]} {p[0]}"
            return self._view_mois
        mois_actif = (load_sme_context().mois_actif or "").strip()
        if mois_actif:
            p = _parse_ym(mois_actif)
            if p:
                return f"{_MOIS_FR[p[1] - 1]} {p[0]} (mois actif)"
        return "Mois actif"

    def _malade_tab_active(self) -> bool:
        return getattr(self, "_tabs", None) is None or self._tabs.currentIndex() == 0

    def _update_stat_chips(self) -> None:
        n_hist = len(self._malade_rows_history_extra)
        self._chip_malades.setText(
            _stat_chip_html("Malades", len(self._malade_rows_all), bg="#1565c0")
        )
        self._chip_punis.setText(
            _stat_chip_html("Punis", len(self._punis_rows_all), bg="#c62828")
        )
        if n_hist:
            self._chip_history.setText(
                _stat_chip_html("Historique", n_hist, bg="#6a1b9a")
            )
            self._chip_history.show()
        else:
            self._chip_history.hide()

    def _update_visible_count_label(self) -> None:
        if self._malade_tab_active():
            n = len(self._malade_rows)
            self._count_lbl.setText(f"{n} ligne(s) visibles — Malades")
        else:
            n = len(self._punis_rows)
            self._count_lbl.setText(f"{n} ligne(s) visibles — Punis")

    def _update_summary(self) -> None:
        self._summary.hide()
        period = self._period_label()
        if self._view_mois:
            scope = f"Effectif et fiches sur le mois consulté ({period})."
        else:
            scope = "Effectif du mois actif SME — les actions modifient la situation officielle."
        tab = "Malades" if self._malade_tab_active() else "Punis & détention"
        n_vis = len(self._malade_rows) if self._malade_tab_active() else len(self._punis_rows)
        self._summary.setText(
            f"<b>{period}</b> — "
            f"{len(self._malade_rows_all)} malade(s), "
            f"{len(self._punis_rows_all)} puni(s) — "
            f"affichés ({tab}) : {n_vis} · {scope}"
        )
        self._update_stat_chips()
        self._update_visible_count_label()
        self._update_malade_checked_label()
        if hasattr(self, "_tbl_punis"):
            self._update_punis_checked_label()

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
        self._view_data.mark_stale()
        if self.isVisible():
            self._reload_lists_async()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _rebind_repositories(self) -> None:
        from sme_gend.config.liste_generale_scope import settings_for_chosen_export_mois

        self._scoped_settings = settings_for_chosen_export_mois(self._settings, self._view_mois)
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
        self._on_period_combo_changed()

    def _on_period_combo_changed(self) -> None:
        self._sync_view_mois_from_combos()
        self._rebind_repositories()
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _sync_view_mois_from_combos(self) -> None:
        year = self._combo_annee.currentData()
        month = self._combo_mois.currentData()
        self._view_mois = f"{int(year):04d}-{int(month):02d}" if year and month else None

    def _fill_mois_combo(self, target_month: int | None) -> None:
        """Remplit ``_combo_mois`` pour l'année actuellement sélectionnée dans
        ``_combo_annee`` — l'appelant gère blockSignals et le vidage préalable."""
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

    def _rebuild_period_combos(self) -> None:
        """Deux listes déroulantes Année / Mois en cascade — même principe que
        Gains & Pertes — plutôt qu'une seule liste plate de tous les mois connus,
        qui deviendrait vite illisible au fil des mois accumulés."""
        mois_actif = (load_sme_context().mois_actif or "").strip()
        years = sorted({p[0] for ym in self._mois_catalog if (p := _parse_ym(ym))}, reverse=True)
        target = self._view_mois or mois_actif or None
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

    def _reload_lists_async(self) -> None:
        if not self._view_data.needs_load():
            return
        liste = self._liste
        sat = self._sat
        base_settings = self._settings

        def work():
            rows = liste.fetch_all()
            malade_details = sat.load_malades()
            try:
                from sme_gend.config.liste_generale_scope import list_available_export_mois

                catalog = list_available_export_mois(base_settings)
            except Exception:
                catalog = []
            return rows, malade_details, catalog

        def done(result: tuple) -> None:
            rows, malade_details, catalog = result
            self._mois_catalog = catalog
            self._rebuild_period_combos()
            self._malade_details = malade_details
            self._malade_rows_all = [r for r in rows if _is_malade_row(r)]
            self._punis_rows_all = [r for r in rows if _is_punis_row(r)]
            # Personnes ayant une fiche maladie enregistrée (agent_malade) mais dont
            # le padmin actuel n'est plus « Malade » — invisibles sans ça dès que la
            # personne guérit/change de position (cf. case « Inclure l'historique »).
            current_keys = {r.satellite_join_key() for r in self._malade_rows_all}
            self._malade_rows_history_extra = [
                r for r in rows
                if r.satellite_join_key() in malade_details
                and r.satellite_join_key() not in current_keys
            ]
            self._apply_malade_search()
            self._apply_punis_search()
            self._view_data.finish_load()
            self._refresh_action_state()
            self._update_summary()

        def on_err(exc: BaseException) -> None:
            self._view_data.fail_load()
            QMessageBox.warning(self, "Malades & Punis", str(exc))

        run_in_background(self, work, done, on_error=on_err)

    def _refresh_action_state(self) -> None:
        """Grise les actions d'écriture si le mois consulté est verrouillé ou sans
        permission — jusqu'ici aucun bouton de ce module ne reflétait ces deux états,
        et le verrou ne tenait pas compte d'un mois archivé consulté explicitement."""
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
        self._btn_m_save.setEnabled(can_edit)
        self._btn_m_del.setEnabled(can_edit)
        self._btn_m_bulk_del.setEnabled(can_edit and bool(self._malade_checked))
        if hasattr(self, "_lbl_punis_checked"):
            self._update_punis_checked_label()
        if hasattr(self, "_btn_p_save"):
            single = (
                len(self._tbl_punis.selectionModel().selectedRows()) == 1
                if hasattr(self, "_tbl_punis")
                else False
            )
            self._btn_p_save.setEnabled(can_edit and single)

    def _reload_malades(self) -> None:
        if not self._confirm_discard_malade_form():
            return
        if not confirm_yes_no(
            self,
            "Rafraîchir",
            "Recharger la liste des malades depuis la base ?",
            default_no=True,
        ):
            return
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _malade_detail_for_row(self, row: ListeGeneraleRow) -> MaladeDetail | None:
        key = row.satellite_join_key()
        if not key:
            return None
        d = self._malade_details.get(key)
        if d is not None:
            return d
        if row.id:
            return self._malade_details.get(row.id)
        return None

    def _apply_malade_search(self) -> None:
        q = self._search_malade.text()
        base = self._malade_rows_all
        if getattr(self, "_chk_malade_history", None) is not None and self._chk_malade_history.isChecked():
            base = base + self._malade_rows_history_extra
        self._malade_rows = [
            r
            for r in base
            if row_matches_malade_search(r, q, self._malade_detail_for_row(r))
        ]
        self._fill_malade_table()
        self._update_summary()

    def _apply_punis_search(self) -> None:
        q = self._search_punis.text()
        self._punis_rows = [r for r in self._punis_rows_all if row_matches_search(r, q)]
        self._fill_punis_table()
        self._update_summary()

    def _ensure_grade_sort(self) -> GradeSortHelper:
        if self._grade_sort is None:
            self._grade_sort = grade_sort_helper_from_settings(self._settings)
        return self._grade_sort

    def _fill_malade_table(self) -> None:
        grade_helper = self._ensure_grade_sort()
        self._tbl_malade.setSortingEnabled(False)
        self._tbl_malade.blockSignals(True)
        self._tbl_malade.setRowCount(len(self._malade_rows))
        for i, r in enumerate(self._malade_rows):
            key = r.satellite_join_key()
            key_item = QTableWidgetItem(key)
            key_item.setData(Qt.ItemDataRole.UserRole, key)
            key_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            key_item.setCheckState(
                Qt.CheckState.Checked if key in self._malade_checked else Qt.CheckState.Unchecked
            )
            self._tbl_malade.setItem(i, 0, key_item)
            detail = self._malade_detail_for_row(r)
            for column, text in (
                (1, r.noms or ""),
                (2, r.grade or ""),
                (3, r.structure or ""),
                (4, (detail.date_constat if detail else "") or ""),
                (5, (detail.pathologie if detail else "") or ""),
                (6, (detail.obs if detail else "") or ""),
                (7, r.position_admin or ""),
            ):
                cell = (
                    GradeSortTableWidgetItem(text, grade_helper)
                    if column == 2
                    else QTableWidgetItem(text)
                )
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._tbl_malade.setItem(i, column, cell)
        self._tbl_malade.blockSignals(False)
        self._tbl_malade.setSortingEnabled(True)
        self._clear_malade_form()
        self._update_malade_checked_label()

    def _on_malade_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._malade_checked.add(key)
        else:
            self._malade_checked.discard(key)
        self._update_malade_checked_label()

    def _update_malade_checked_label(self) -> None:
        from sme_gend.ui.check_all import sync_check_all
        sync_check_all(self._btn_m_select_all, (r.satellite_join_key() for r in self._malade_rows if r.satellite_join_key()), self._malade_checked)
        n = len(self._malade_checked)
        self._lbl_malade_checked.setText(
            f"{n} coché(s) · {len(self._malade_rows)} affiché(s)"
        )
        self._btn_m_bulk_del.setEnabled(self._can_edit and n > 0)

    def _reload_punis(self) -> None:
        if not self._confirm_discard_malade_form():
            return
        if not confirm_yes_no(
            self,
            "Rafraîchir",
            "Recharger la liste des punis depuis la base ?",
            default_no=True,
        ):
            return
        self._view_data.mark_stale()
        self._reload_lists_async()

    def _fill_punis_table(self) -> None:
        grade_helper = self._ensure_grade_sort()
        self._tbl_punis.setSortingEnabled(False)
        self._tbl_punis.blockSignals(True)
        self._tbl_punis.setRowCount(len(self._punis_rows))
        for i, r in enumerate(self._punis_rows):
            rid = r.id or ""
            chk = QTableWidgetItem("")
            chk.setData(Qt.ItemDataRole.UserRole, rid)
            chk.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            chk.setCheckState(
                Qt.CheckState.Checked if rid in self._punis_checked else Qt.CheckState.Unchecked
            )
            self._tbl_punis.setItem(i, 0, chk)
            for column, text in (
                (1, r.noms or ""),
                (2, r.grade or ""),
                (3, r.structure or ""),
                (4, r.position_admin or ""),
                (5, r.obs or ""),
            ):
                cell = (
                    GradeSortTableWidgetItem(text, grade_helper)
                    if column == 2
                    else QTableWidgetItem(text)
                )
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._tbl_punis.setItem(i, column, cell)
            id_item = QTableWidgetItem(rid)
            id_item.setData(Qt.ItemDataRole.UserRole, rid)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._tbl_punis.setItem(i, 6, id_item)
        self._tbl_punis.blockSignals(False)
        self._tbl_punis.setSortingEnabled(True)
        self._on_punis_select()
        self._update_punis_checked_label()

    def _punis_edit_motif(self) -> str:
        return self._p_motif.toPlainText().strip()

    def _save_punis_motif(self) -> None:
        if not self._check_can_edit(action="de modifier le motif de punition"):
            return
        row = self._current_punis_row()
        if row is None:
            QMessageBox.information(
                self, "Punis", "Sélectionnez une seule ligne pour enregistrer le motif."
            )
            return
        motif = self._punis_edit_motif() or None
        updated = replace(row, obs=motif)
        if updated.obs == row.obs:
            QMessageBox.information(self, "Punis", "Aucune modification à enregistrer.")
            return
        label = row.noms or row.id or "?"
        if not confirm_yes_no(
            self,
            "Enregistrer le motif",
            f"Enregistrer le motif pour :\n{label} ?",
            default_no=True,
        ):
            return
        from sme_gend.services.personnel_service import PersonnelService

        personnel = PersonnelService(self._scoped_settings)
        audit_motif = motif or "Mise à jour motif punition / détention"
        try:
            personnel.update_personnel(row, updated, edit_motif=audit_motif)
        except Exception as exc:
            QMessageBox.critical(self, "Punis", str(exc))
            return
        for i, cached in enumerate(self._punis_rows):
            if cached.id == row.id:
                self._punis_rows[i] = updated
                break
        for i, cached in enumerate(self._punis_rows_all):
            if cached.id == row.id:
                self._punis_rows_all[i] = updated
                break
        self._fill_punis_table()
        rows = self._tbl_punis.selectionModel().selectedRows()
        if rows:
            self._tbl_punis.selectRow(rows[0].row())
        QMessageBox.information(self, "Punis", "Motif enregistré.")

    def _clear_malade_form(self) -> None:
        self._m_date.set_empty()
        self._m_path.clear()
        self._m_obs.clear()
        self._lbl_malade_selection.setText("Aucune ligne sélectionnée")
        if hasattr(self, "_lbl_malade_grade"):
            self._lbl_malade_grade.setText("—")
        if hasattr(self, "_lbl_malade_structure"):
            self._lbl_malade_structure.setText("—")

    def _malade_form_dirty(self) -> bool:
        if not self._m_path.text().strip() and not self._m_obs.text().strip():
            if self._m_date.is_empty():
                return False
        key = self._selected_malade_key()
        if not key:
            return bool(
                self._m_path.text().strip()
                or self._m_obs.text().strip()
                or not self._m_date.is_empty()
            )
        m = self._malade_details.get(key)
        cur_date = self._m_date.text_for_validation().strip() or None
        cur_path = self._m_path.text().strip() or None
        cur_obs = self._m_obs.text().strip() or None
        if m is None:
            return bool(cur_date or cur_path or cur_obs)
        return (
            cur_date != (m.date_constat or None)
            or cur_path != (m.pathologie or None)
            or cur_obs != (m.obs or None)
        )

    def _confirm_discard_malade_form(self) -> bool:
        if not self._malade_form_dirty():
            return True
        return confirm_yes_no(
            self,
            "Modifications non enregistrées",
            "La fiche maladie affichée a été modifiée sans enregistrement.\n\n"
            "Continuer et abandonner ces changements ?",
            default_no=True,
        )

    def _selected_malade_key(self) -> str | None:
        """Clé de la ligne sélectionnée, lue sur la cellule (pas par index de position).

        ``_tbl_malade`` a le tri par colonne activé : l'index de ligne visuel ne
        correspond plus à celui de ``_malade_rows`` dès qu'un tri est appliqué.
        """
        rows = self._tbl_malade.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._tbl_malade.item(rows[0].row(), 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_malade_select(self) -> None:
        key = self._selected_malade_key()
        if not key:
            self._clear_malade_form()
            return
        row = self._current_malade_row()
        if row and (row.noms or row.id):
            self._lbl_malade_selection.setText(row.noms or row.id or key)
        else:
            self._lbl_malade_selection.setText(key)
        if row is not None:
            self._lbl_malade_grade.setText(self._ensure_grade_sort().display_label(row.grade) or "—")
            self._lbl_malade_structure.setText(row.structure or "—")
        else:
            self._lbl_malade_grade.setText("—")
            self._lbl_malade_structure.setText("—")
        m = self._malade_details.get(str(key))
        self._m_date.set_from_text(m.date_constat or "" if m else "")
        self._m_path.setText(m.pathologie or "" if m else "")
        self._m_obs.setText(m.obs or "" if m else "")

    def _edit_malade_from_table(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        row_index = item.row()
        key_item = self._tbl_malade.item(row_index, 0)
        key = key_item.data(Qt.ItemDataRole.UserRole) if key_item else None
        if not key:
            return
        row = next(
            (candidate for candidate in self._malade_rows
             if candidate.satellite_join_key() == key),
            None,
        )
        if row is None or not self._check_can_edit(action="de modifier les fiches maladie"):
            return
        from sme_gend.ui.padmin_satellite_dialog import PadminSatelliteDialog

        expected = self._malade_details.get(str(key))
        result = PadminSatelliteDialog.run(
            self,
            kind="malade",
            agent_label=row.noms or row.id or str(key),
            malade=expected,
        )
        if result is None or result.malade is None:
            return
        try:
            self._sat.upsert_malade(str(key), result.malade, expected=expected)
        except Exception as exc:
            QMessageBox.critical(self, "Malades", f"Enregistrement impossible :\n{exc}")
            return
        try:
            self._malade_details = self._sat.load_malades()
        except Exception as exc:
            QMessageBox.warning(self, "Relecture des données",
                f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
            return
        self._on_malade_select()

    def _current_malade_row(self) -> ListeGeneraleRow | None:
        key = self._selected_malade_key()
        if not key:
            return None
        for r in self._malade_rows:
            if r.satellite_join_key() == key:
                return r
        return None

    def _check_can_edit(self, *, action: str) -> bool:
        """Permission + verrou du mois **consulté** (pas forcément le mois actif) —
        aucune des actions de ce module ne les vérifiait auparavant (ni fiche
        maladie, ni changement de position en masse), contrairement au reste de
        l'application."""
        from sme_gend.services.auth_guard import require_permission
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        if not require_permission(
            self, "effectifs.edition",
            message=f"Votre compte n'a pas la permission {action}.",
        ):
            return False
        state = evaluate_editing_state(self._settings, view_mois=self._view_mois)
        if state.locked:
            QMessageBox.warning(self, "Malades & Punis", state.detail)
            return False
        return True

    def _save_malade_fiche(self) -> None:
        if not self._check_can_edit(action="de modifier les fiches maladie"):
            return
        r = self._current_malade_row()
        if r is None:
            QMessageBox.information(self, "Malades", "Sélectionnez une ligne dans le tableau.")
            return
        key = r.satellite_join_key()
        if not key:
            QMessageBox.warning(
                self, "Malades", "Identifiant de liaison vide — impossible d'enregistrer la fiche."
            )
            return
        detail = MaladeDetail(
            date_constat=self._m_date.text_for_validation().strip() or None,
            pathologie=self._m_path.text().strip() or None,
            obs=self._m_obs.text().strip() or None,
        )
        label = r.noms or r.id or key
        snapshots = dict(self._malade_details)
        if not confirm_yes_no(
            self,
            "Enregistrer la fiche maladie",
            f"Enregistrer la fiche maladie pour :\n{label} ?",
            default_no=True,
        ):
            return
        try:
            self._sat.upsert_malade(key, detail, expected=snapshots.get(str(key)))
        except Exception as exc:
            QMessageBox.critical(self, "Malades", f"Enregistrement impossible :\n{exc}")
            return
        try:
            self._malade_details = self._sat.load_malades()
        except Exception as exc:
            QMessageBox.warning(self, "Relecture des données",
                f"Enregistrement effectué. Relecture impossible :\n{exc}\nRechargez les données avant une nouvelle modification.")
            return
        QMessageBox.information(self, "Malades", "Fiche enregistrée.")
        self._fill_malade_table()
        rows = self._tbl_malade.selectionModel().selectedRows()
        if rows:
            self._tbl_malade.selectRow(rows[0].row())
        self._on_malade_select()

    def _delete_malade_fiche(self) -> None:
        if not self._check_can_edit(action="de supprimer les fiches maladie"):
            return
        r = self._current_malade_row()
        if r is None:
            QMessageBox.information(self, "Malades", "Sélectionnez une ligne dans le tableau.")
            return
        key = r.satellite_join_key()
        if not key:
            return
        label = r.noms or r.id or key
        snapshots = dict(self._malade_details)
        if not confirm_yes_no(
            self,
            "Supprimer la fiche maladie",
            f"Supprimer définitivement la fiche maladie pour :\n{label} ?\n\n"
            "La position « Malade » en liste générale n'est pas modifiée.",
            default_no=True,
        ):
            return
        try:
            self._sat.delete_malade(key, expected=snapshots.get(str(key)))
        except Exception as exc:
            QMessageBox.critical(self, "Malades", f"Suppression impossible :\n{exc}")
            return
        self._clear_malade_form()
        self._on_malade_select()

    def _delete_checked_malade_fiches(self) -> None:
        if not self._check_can_edit(action="de supprimer des fiches maladie"):
            return
        keys = sorted(self._malade_checked)
        if not keys:
            return
        snapshots = dict(self._malade_details)
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer {len(keys)} fiche(s) maladie cochée(s) ?",
            default_no=True,
        ):
            return
        n_ok = 0
        errs: list[str] = []
        for key in keys:
            try:
                self._sat.delete_malade(key, expected=snapshots.get(str(key)))
                n_ok += 1
            except Exception as exc:
                errs.append(f"{key}: {exc}")
        self._malade_checked.clear()
        self._clear_malade_form()
        self._reload_malades()
        msg = f"{n_ok} fiche(s) supprimée(s)."
        if errs:
            msg += "\n\nÉchecs :\n" + "\n".join(errs[:8])
            if len(errs) > 8:
                msg += f"\n… (+{len(errs) - 8})"
        QMessageBox.information(self, "Malades", msg)

    def _reset_punis_selection(self) -> None:
        if not self._check_can_edit(action="de modifier la position administrative"):
            return
        selected_ids = self._punis_target_ids()
        if not selected_ids:
            QMessageBox.information(
                self,
                "Punis",
                "Cochez au moins une ligne (ou sélectionnez une ligne) à remettre en « Présent ».",
            )
            return
        n_sel = len(selected_ids)
        if not confirm_yes_no(
            self,
            "Retirer la punition",
            f"Remettre la position administrative à « Présent » pour {n_sel} personne(s) ?\n\n"
            "Cette action modifie la carrière officielle et les effectifs du mois.",
            default_no=True,
        ):
            return
        idx = self._combo_new_padmin.findText("Présent")
        if idx >= 0:
            self._combo_new_padmin.setCurrentIndex(idx)
        self._apply_padmin_selection(skip_confirm=True)

    def _apply_padmin_selection(self, *, skip_confirm: bool = False) -> None:
        if not self._check_can_edit(action="de modifier la position administrative"):
            return
        selected_ids = self._punis_target_ids()
        if not selected_ids:
            QMessageBox.information(
                self,
                "Punis",
                "Cochez au moins une ligne (ou sélectionnez une ligne) à modifier.",
            )
            return
        new_p = self._combo_new_padmin.currentText().strip()
        if not new_p:
            return
        n_sel = len(selected_ids)
        if not skip_confirm and not confirm_yes_no(
            self,
            "Confirmer le changement de position",
            f"Appliquer la position « {new_p} » à {n_sel} personne(s) cochée(s) ?\n\n"
            "Cette action modifie la position administrative officielle de chacune "
            "et affecte les effectifs présents / le numérique du mois.",
            default_no=True,
        ):
            return
        try:
            all_by_id = {r.id: r for r in self._liste.fetch_all() if r.id}
        except Exception as exc:
            QMessageBox.critical(self, "Punis", str(exc))
            return
        n_ok = 0
        errs: list[str] = []
        updated_rows: list[ListeGeneraleRow] = []
        from sme_gend.services.personnel_service import PersonnelService

        from sme_gend.services.padmin_satellite_apply import prompt_satellite_bulk_shared, prepare_position_change
        from sme_gend.services.personnel_service import _personnel_write
        candidates = [all_by_id[pid] for pid in selected_ids if pid in all_by_id]
        shared_write = prompt_satellite_bulk_shared(
            self, self._scoped_settings, candidates, new_padmin=new_p, prepare_only=True,
        )
        if shared_write is False:
            return
        personnel = PersonnelService(self._scoped_settings)
        motif_audit = self._punis_edit_motif()
        default_audit = "Changement de position administrative depuis Malades & Punis"
        edit_motif = motif_audit or default_audit
        for pid in selected_ids:
            cur = all_by_id.get(pid)
            if cur is None:
                errs.append(pid or "?")
                continue
            try:
                obs = cur.obs
                if len(selected_ids) == 1 and motif_audit:
                    obs = motif_audit
                updated = replace(cur, position_admin=new_p, obs=obs)
                write_position = (lambda: shared_write(updated)) if callable(shared_write) else prepare_position_change(
                    self, self._scoped_settings, updated, old_padmin=cur.position_admin, new_padmin=new_p,
                )
                if write_position is False or write_position is None:
                    errs.append(f"{cur.noms or cur.id}: fiche complémentaire annulée.")
                    continue
                with _personnel_write(self._scoped_settings):
                    personnel.update_personnel(cur, updated, edit_motif=edit_motif)
                    if callable(write_position):
                        write_position()
                updated_rows.append(updated)
                n_ok += 1
            except Exception as exc:
                errs.append(f"{cur.noms or cur.id}: {exc}")
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        # Invalidation et fiche satellite sur les settings du mois consulté (et pas
        # forcément le mois actif) : sinon un changement de padmin appliqué à une
        # archive resterait masqué par le cache de l'ancien mois, ou la fiche
        # satellite serait écrite sur la mauvaise période.
        invalidate_liste_cache(self._scoped_settings)
        self._punis_checked.clear()
        self._reload_punis()
        msg = f"{n_ok} ligne(s) mise(s) à jour."
        if errs:
            msg += "\n\nÉchecs :\n" + "\n".join(errs[:8])
            if len(errs) > 8:
                msg += f"\n… (+{len(errs) - 8})"
        QMessageBox.information(self, "Punis", msg)

    def _on_export_malades(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.ui.export_snapshot import visible_export_rows
        from copy import deepcopy
        try:
            rows = visible_export_rows(self._tbl_malade, self._malade_rows, key=lambda row: row.satellite_join_key())
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        details = deepcopy(self._malade_details)
        if not rows:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        if not confirm_yes_no(
            self,
            "Export Excel",
            f"Exporter {len(rows)} malade(s) visibles (filtres appliqués) "
            f"vers un fichier Excel ?",
            default_no=True,
        ):
            return
        settings = getattr(self, "_scoped_settings", getattr(self, "_settings", None))
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter malades", str(Path.home() / "Malades.xlsx"), "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        try:
            export_malades_workbook(
                Path(dest), rows=rows, details=details, settings=settings,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Export impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")

    def _on_export_punis(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        from sme_gend.ui.export_snapshot import visible_export_rows
        from copy import deepcopy
        try:
            rows = visible_export_rows(self._tbl_punis, self._punis_rows, key=lambda row: row.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        if not rows:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter.")
            return
        if not confirm_yes_no(
            self,
            "Export Excel",
            f"Exporter {len(rows)} puni(s) visibles (filtres appliqués) "
            f"vers un fichier Excel ?",
            default_no=True,
        ):
            return
        settings = getattr(self, "_scoped_settings", getattr(self, "_settings", None))
        dest, _ = QFileDialog.getSaveFileName(
            self, "Exporter punis", str(Path.home() / "Punis.xlsx"), "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        try:
            export_punis_workbook(Path(dest), rows=rows, settings=settings)
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Export impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")
