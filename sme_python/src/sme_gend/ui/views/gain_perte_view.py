"""Vue Gains & Pertes — table mouvement_effectif avec sélecteur de mois et filtres GAIN/PERTE/DECES."""

from __future__ import annotations

from sme_gend.services.search_text import search_values

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.personnel_loss_motifs import PERSONNEL_LOSS_MOTIFS
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.gain_perte import (
    GainPerteRow,
    cdsme_to_mois_ym,
    observ_is_deces,
    periode_ym_for_mouvement,
)
from sme_gend.repositories.gain_perte_repository import GainPerteRepository, cdsme_from_mois_ym
from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError
from sme_gend.services.gain_perte_export import export_gain_perte_workbook
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.ui.agent_pick_dialog import AgentPickDialog
from sme_gend.ui.widgets.date_input import FrenchDateEdit
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
    grade_sort_helper_from_settings,
)
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.services.personnel_list_order import GradeSortHelper


def _cdsme_to_label(cdsme: str | None) -> str:
    """'012026' → '01/2026'."""
    if not cdsme:
        return "—"
    s = cdsme.strip()
    if len(s) == 6:
        return f"{s[:2]}/{s[2:]}"
    return s


_FILTER_ALL = "ALL"
_FILTER_GAIN = "GAIN"
_FILTER_PERTE = "PERTE"
_FILTER_DECES = "DECES"

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)

_GAIN_MOTIFS = ("AJOUT", "REINTEGRATION", "ERREUR", "AUTRE")
_PERTE_MOTIFS = PERSONNEL_LOSS_MOTIFS


def _parse_ym(ym: str) -> tuple[int, int] | None:
    try:
        y_s, m_s = (ym or "").strip().split("-", 1)
        y, m = int(y_s), int(m_s)
        if 1 <= m <= 12:
            return y, m
    except (ValueError, TypeError):
        pass
    return None


def _ym_to_fr(ym: str) -> str:
    """'2026-01' → 'Janvier 2026'."""
    p = _parse_ym(ym)
    if p is None:
        return ym
    y, m_int = p
    return f"{_MOIS_FR[m_int - 1]} {y}"


def _years_from_catalog(catalog: list[str]) -> list[int]:
    years: set[int] = set()
    for ym in catalog:
        p = _parse_ym(ym)
        if p is not None:
            years.add(p[0])
    return sorted(years, reverse=True)


def _row_in_period_scope(
    row: GainPerteRow,
    *,
    mois_ym: str | None,
    year: int | None,
) -> bool:
    eff = row.effective_mois_ym()
    if not eff:
        return year is None and mois_ym is None
    if mois_ym:
        return eff == mois_ym
    if year is not None:
        p = _parse_ym(eff)
        return p is not None and p[0] == year
    return True


def _row_matches_mois(row: GainPerteRow, mois_ym: str) -> bool:
    return row.effective_mois_ym() == mois_ym


def _scope_label(mois_ym: str | None, year: int | None) -> str:
    if mois_ym:
        return _ym_to_fr(mois_ym)
    if year is not None:
        return f"Année {year}"
    return "Toutes périodes"


def _stat_chip_html(label: str, count: int, *, bg: str, fg: str = "#ffffff") -> str:
    return (
        f"<span style='background:{bg};color:{fg};padding:3px 10px;"
        f"border-radius:10px;font-size:11px;font-weight:600;'>"
        f"{label} : {count}</span>"
    )


def _month_choices_for_dialog(
    catalog: list[str],
    *,
    default_mois_ym: str | None,
    existing_mois: str | None,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ym in catalog:
        ym = (ym or "").strip()
        if ym and ym not in seen:
            seen.add(ym)
            out.append(ym)
    for ym in (existing_mois, default_mois_ym, (load_sme_context().mois_actif or "").strip()):
        ym = (ym or "").strip()
        if ym and ym not in seen:
            seen.add(ym)
            out.append(ym)
    return sorted(out, reverse=True)


class GainPerteEditDialog(QDialog):
    """Saisie / modification d'une ligne mouvement_effectif."""

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None,
        *,
        existing: GainPerteRow | None,
        default_cdsme: str | None,
        default_mois_ym: str | None = None,
        month_catalog: list[str] | None = None,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Ligne gains & pertes" if existing else "Nouvelle ligne gains & pertes")
        self._existing = existing
        self._where_id: str | None = None
        self._where_cdsme: str | None = None
        self._where_movement_id: int | None = None

        self._form = QFormLayout()
        id_row = QHBoxLayout()
        self._id_le = QLineEdit()
        self._btn_pick = QPushButton("Rechercher…")
        self._btn_pick.clicked.connect(self._pick_agent)
        id_row.addWidget(self._id_le, stretch=1)
        id_row.addWidget(self._btn_pick)

        self._grade_le = QLineEdit()
        self._nom_le = QLineEdit()
        self._obj_combo = QComboBox()
        self._obj_combo.addItems(["GAIN", "PERTE"])
        self._obj_combo.currentIndexChanged.connect(self._sync_motif_choices)
        self._motif_combo = QComboBox()
        self._motif_combo.setEditable(True)
        self._motif_combo.currentTextChanged.connect(lambda _t: self._sync_date_ref_visibility())
        self._mois_sme_combo = QComboBox()
        self._mois_sme_combo.setToolTip(
            "Mois SME auquel le mouvement est rattaché (affichage, numérique, exports). "
            "Peut différer du code Cdsme historique Access."
        )
        self._cdsme_le = QLineEdit()
        self._cdsme_le.setPlaceholderText("Ex. 052026 (MMYYYY)")
        self._cdsme_le.setToolTip(
            "Code mois Access (MMYYYY). Par défaut aligné sur le mois SME ; "
            "modifiable si l'archive utilisait un autre code."
        )
        self._btn_align_cdsme = QPushButton("↔ Aligner Cdsme sur mois SME")
        self._btn_align_cdsme.setToolTip("Recopie le mois SME sélectionné au format MMYYYY dans Cdsme.")
        self._btn_align_cdsme.clicked.connect(self._align_cdsme_from_mois)
        cdsme_row = QHBoxLayout()
        cdsme_row.addWidget(self._cdsme_le, stretch=1)
        cdsme_row.addWidget(self._btn_align_cdsme)
        self._obs_le = QLineEdit()
        self._obs_le.setPlaceholderText("Texte libre (précision, référence, etc.) — toujours modifiable")
        self._cdsme_manual = False
        self._cdsme_le.textEdited.connect(self._on_cdsme_edited)
        self._mois_sme_combo.currentIndexChanged.connect(self._on_mois_sme_changed)

        self._date_ref = FrenchDateEdit()
        self._date_ref.setToolTip("Date du décès (obligatoire pour une perte « décès »).")

        self._form.addRow("ID (personnel / réf.) :", id_row)
        self._form.addRow("Grade :", self._grade_le)
        self._form.addRow("Nom :", self._nom_le)
        self._form.addRow("Objet :", self._obj_combo)
        self._form.addRow("Motif :", self._motif_combo)
        self._form.addRow("Date de décès * :", self._date_ref)
        self._form.addRow("Mois SME (rattachement) :", self._mois_sme_combo)
        self._form.addRow("Cdsme (MMYYYY) :", cdsme_row)
        self._form.addRow("Observation :", self._obs_le)

        init_mois = (
            periode_ym_for_mouvement(existing)
            if existing
            else (default_mois_ym or cdsme_to_mois_ym(default_cdsme or ""))
        )
        choices = _month_choices_for_dialog(
            month_catalog or [],
            default_mois_ym=default_mois_ym,
            existing_mois=init_mois,
        )
        self._populate_mois_sme_combo(choices, init_mois)

        if existing:
            self._where_id = (existing.id or "").strip()
            self._where_cdsme = (existing.cdsme or "").strip() or None
            self._where_movement_id = existing.movement_id
            self._id_le.setText(existing.id or "")
            self._grade_le.setText(existing.grade or "")
            self._nom_le.setText(existing.nom or "")
            o = (existing.objet or "").strip().upper()
            if o == "GAIN":
                self._obj_combo.setCurrentIndex(0)
            elif o == "PERTE":
                self._obj_combo.setCurrentIndex(1)
            self._sync_motif_choices()
            self._apply_observ_to_fields(existing.observ)
            self._sync_date_ref_visibility()
            self._cdsme_le.setText(cdsme_from_mois_ym(init_mois) or existing.cdsme or "")
            eff = periode_ym_for_mouvement(existing)
            cds_ym = cdsme_to_mois_ym(existing.cdsme)
            if eff and cds_ym and eff != cds_ym:
                self._cdsme_manual = True
            if existing.date_reference:
                self._date_ref.set_from_text(str(existing.date_reference)[:10])
        elif default_cdsme:
            self._cdsme_le.setText(default_cdsme)
            self._sync_motif_choices()
        else:
            self._align_cdsme_from_mois()

        self._sync_date_ref_visibility()

        if read_only:
            for w in (
                self._id_le,
                self._grade_le,
                self._nom_le,
                self._obj_combo,
                self._motif_combo,
                self._date_ref,
                self._mois_sme_combo,
                self._cdsme_le,
                self._obs_le,
                self._btn_pick,
                self._btn_align_cdsme,
            ):
                w.setEnabled(False)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        if read_only:
            bb.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(self._form)
        root.addWidget(bb)

    def _populate_mois_sme_combo(self, choices: list[str], select_ym: str | None) -> None:
        self._mois_sme_combo.blockSignals(True)
        self._mois_sme_combo.clear()
        for ym in choices:
            self._mois_sme_combo.addItem(_ym_to_fr(ym), ym)
        if select_ym:
            idx = self._mois_sme_combo.findData(select_ym)
            if idx >= 0:
                self._mois_sme_combo.setCurrentIndex(idx)
            else:
                self._mois_sme_combo.insertItem(0, _ym_to_fr(select_ym), select_ym)
                self._mois_sme_combo.setCurrentIndex(0)
        self._mois_sme_combo.blockSignals(False)

    def _selected_mois_ym(self) -> str | None:
        data = self._mois_sme_combo.currentData()
        if data:
            return str(data).strip()
        return None

    def _on_cdsme_edited(self, _text: str) -> None:
        self._cdsme_manual = True

    def _on_mois_sme_changed(self, _index: int) -> None:
        if not self._cdsme_manual:
            self._align_cdsme_from_mois()

    def _align_cdsme_from_mois(self) -> None:
        ym = self._selected_mois_ym()
        cds = cdsme_from_mois_ym(ym) if ym else None
        if cds:
            self._cdsme_le.blockSignals(True)
            self._cdsme_le.setText(cds)
            self._cdsme_le.blockSignals(False)
            self._cdsme_manual = False

    def _saisie_is_deces(self) -> bool:
        if self._obj_combo.currentText().strip().upper() != "PERTE":
            return False
        motif = self._motif_combo.currentText().strip()
        if observ_is_deces(motif):
            return True
        return observ_is_deces(self._resolved_observ())

    def _sync_date_ref_visibility(self) -> None:
        show = self._saisie_is_deces()
        lbl = self._form.labelForField(self._date_ref)
        if lbl is not None:
            lbl.setVisible(show)
        self._date_ref.setVisible(show)

    def _sync_motif_choices(self) -> None:
        is_gain = self._obj_combo.currentText().strip().upper() == "GAIN"
        motifs = _GAIN_MOTIFS if is_gain else _PERTE_MOTIFS
        current = self._motif_combo.currentText().strip()
        self._motif_combo.blockSignals(True)
        self._motif_combo.clear()
        self._motif_combo.addItems(motifs)
        if current:
            idx = self._motif_combo.findText(current, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._motif_combo.setCurrentIndex(idx)
            else:
                self._motif_combo.setEditText(current)
        self._motif_combo.blockSignals(False)
        self._sync_date_ref_visibility()

    def _pick_agent(self) -> None:
        dlg = AgentPickDialog(self._settings, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        leg = dlg.selected_legacy_id()
        if leg:
            self._id_le.setText(leg)
        grade = dlg.selected_grade()
        if grade:
            self._grade_le.setText(grade)
        nom = dlg.selected_nom()
        if nom:
            self._nom_le.setText(nom)

    def _apply_observ_to_fields(self, observ: str | None) -> None:
        """Répartit le texte stocké entre Motif (liste) et Observation (libre)."""
        obs = (observ or "").strip()
        if not obs:
            self._obs_le.clear()
            return
        motifs = [
            self._motif_combo.itemText(i) for i in range(self._motif_combo.count())
        ]
        obs_u = obs.upper()
        for m in motifs:
            mu = (m or "").upper()
            if not mu or mu == "AUTRE":
                continue
            if obs_u == mu:
                idx = self._motif_combo.findText(m, Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    self._motif_combo.setCurrentIndex(idx)
                self._obs_le.clear()
                return
            for sep in (" — ", " – ", " - ", ": ", " : "):
                prefix = m + sep
                if obs.startswith(prefix) or obs_u.startswith(mu + sep.upper()):
                    idx = self._motif_combo.findText(m, Qt.MatchFlag.MatchFixedString)
                    if idx >= 0:
                        self._motif_combo.setCurrentIndex(idx)
                    self._obs_le.setText(obs[len(prefix) :].strip())
                    return
            if obs_u.startswith(mu) and len(obs) > len(m) and obs[len(m)] in " —–-:" :
                idx = self._motif_combo.findText(m, Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    self._motif_combo.setCurrentIndex(idx)
                self._obs_le.setText(obs[len(m) :].lstrip(" —–-:").strip())
                return
        idx_autre = self._motif_combo.findText("AUTRE", Qt.MatchFlag.MatchFixedString)
        if idx_autre >= 0:
            self._motif_combo.setCurrentIndex(idx_autre)
        else:
            self._motif_combo.setEditText("AUTRE")
        self._obs_le.setText(obs)

    def _resolved_observ(self) -> str | None:
        """Observation enregistrée : motif structuré + précision libre si besoin."""
        motif = self._motif_combo.currentText().strip()
        detail = self._obs_le.text().strip()
        if motif.upper() == "AUTRE":
            return detail or None
        if detail:
            detail_u = detail.upper()
            motif_u = motif.upper()
            if detail_u == motif_u or detail_u.startswith(motif_u):
                return detail
            return f"{motif} — {detail}"
        return motif or None

    def _try_accept(self) -> None:
        if not self._id_le.text().strip():
            QMessageBox.warning(self, "Saisie", "L'identifiant est obligatoire.")
            return
        cds = self._cdsme_le.text().strip()
        if len(cds) != 6 or not cds.isdigit():
            QMessageBox.warning(
                self,
                "Saisie",
                "Le champ Cdsme doit contenir 6 chiffres au format MMYYYY (ex. 052026).",
            )
            return
        if not self._selected_mois_ym():
            QMessageBox.warning(
                self,
                "Saisie",
                "Choisissez le mois SME de rattachement du mouvement.",
            )
            return
        if not self._resolved_observ():
            QMessageBox.warning(
                self,
                "Saisie",
                "Indiquez un motif, ou une observation (obligatoire si motif = AUTRE).",
            )
            return
        if self._saisie_is_deces() and not self._date_ref.to_datetime():
            QMessageBox.warning(
                self,
                "Saisie",
                "La date de décès est obligatoire pour une perte « décès ».",
            )
            return
        self.accept()

    def to_row(self) -> GainPerteRow:
        obj = self._obj_combo.currentText().strip().upper()
        base = self._existing
        mois_ym = self._selected_mois_ym()
        date_ref: str | None = None
        if self._saisie_is_deces():
            dt = self._date_ref.to_datetime()
            if dt:
                date_ref = dt.date().isoformat()
        elif base:
            date_ref = base.date_reference
        return GainPerteRow(
            id=self._id_le.text().strip(),
            grade=self._grade_le.text().strip() or None,
            nom=self._nom_le.text().strip() or None,
            objet=obj,
            cdsme=self._cdsme_le.text().strip(),
            observ=self._resolved_observ(),
            mois_ym=mois_ym,
            date_reference=date_ref,
            movement_id=base.movement_id if base else None,
            write_token=base.write_token if base else None,
        )

    def where_key(self) -> tuple[str, str | None, int | None] | None:
        if self._existing is None or self._existing.inferred:
            return None
        return (self._where_id or "", self._where_cdsme, self._where_movement_id)


class GainPerteView(QWidget):
    """Vue dédiée mouvement_effectif : tableau filtrable gains / pertes / décès par mois."""

    _ALL_YEARS = "(Toutes)"
    _ALL_MONTHS = "(Tous)"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._repo = GainPerteRepository(settings)
        self._all_rows: list[GainPerteRow] = []
        self._month_catalog: list[str] = []
        self._unsignaled_by_mois: dict[str, list[GainPerteRow]] = {}
        self._unsignaled_loading: set[str] = set()
        self._unsignaled_gen = 0
        self._gap_rows: list[GainPerteRow] = []
        self._filter = _FILTER_ALL
        self._pending_mois: str | None = None
        self._pending_filter: str | None = None
        self._view_data = ViewDataState(stale=True)
        self._reload_busy = False
        self._grade_sort: GradeSortHelper | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(20, 12, 20, 16)

        title = QLabel("<h2>Gains &amp; Pertes</h2>", textFormat=Qt.TextFormat.RichText)
        lay.addWidget(title)

        self._summary = QLabel()
        self._summary.setObjectName("gainPerteSummary")
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self._summary.hide()
        lay.addWidget(self._summary)

        self._lock_banner = QLabel()
        self._lock_banner.setObjectName("gainPerteLockBanner")
        self._lock_banner.setWordWrap(True)
        self._lock_banner.hide()
        lay.addWidget(self._lock_banner)

        self._gap_banner = QLabel()
        self._gap_banner.setObjectName("gainPerteGapBanner")
        self._gap_banner.setWordWrap(True)
        self._gap_banner.setTextFormat(Qt.TextFormat.RichText)
        self._gap_banner.hide()
        lay.addWidget(self._gap_banner)

        toolbar = QFrame()
        toolbar.setObjectName("gainPerteToolbar")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(10, 8, 10, 8)
        tb_lay.setSpacing(8)

        self._btn_add = QPushButton("Ajouter un mouvement…")
        self._btn_add.setObjectName("gainPerteBtnPrimary")
        self._btn_record_gaps = QPushButton("Enregistrer les sorties non signalées")
        self._btn_record_gaps.setToolTip(
            "Inscrit en base les agents sortis de la liste générale sans perte, "
            "avec le motif « Non signalé »."
        )
        self._btn_record_gaps.hide()
        self._btn_edit = QPushButton("Modifier la sélection…")
        self._btn_del = QPushButton("Supprimer la sélection")
        self._btn_refresh = QPushButton("Rafraîchir")
        self._btn_dated = QPushButton("Mouvements datés…")
        self._btn_dated.setToolTip("Déclarer les dates réelles des gains et pertes dans un registre distinct des observations Access.")
        self._btn_dated.clicked.connect(self._open_dated_movements)
        self._btn_export = QPushButton("Exporter vers Excel…")

        self._btn_add.setToolTip(
            "Gain ou perte pour le mois affiché. Mois SME de rattachement modifiable à la saisie."
        )
        self._btn_edit.setToolTip("Modifier la ligne sélectionnée dans le tableau.")
        self._btn_del.setToolTip("Supprimer définitivement la ligne sélectionnée.")
        self._btn_refresh.setToolTip("Recharger les mouvements depuis la base.")
        self._btn_export.setToolTip("Exporter les lignes visibles (filtres appliqués) vers un fichier Excel.")

        self._btn_add.clicked.connect(self._on_add)
        self._btn_record_gaps.clicked.connect(self._on_record_gaps)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_refresh.clicked.connect(self._force_reload)
        self._btn_export.clicked.connect(self._on_export)

        tb_lay.addWidget(self._btn_add)
        tb_lay.addWidget(self._btn_record_gaps)
        tb_lay.addWidget(self._btn_edit)
        tb_lay.addWidget(self._btn_del)
        tb_lay.addWidget(self._btn_dated)
        tb_lay.addStretch()
        tb_lay.addWidget(self._btn_refresh)
        tb_lay.addWidget(self._btn_export)
        lay.addWidget(toolbar)

        top_bar = QFrame()
        top_bar.setObjectName("gainPerteTopBar")
        tb = QVBoxLayout(top_bar)
        tb.setContentsMargins(12, 10, 12, 10)
        tb.setSpacing(10)

        period_row = QHBoxLayout()
        period_row.setSpacing(10)
        period_row.addWidget(QLabel("Période"))
        self._combo_year = QComboBox()
        self._combo_year.setObjectName("gainPertePeriodCombo")
        self._combo_year.setMinimumWidth(92)
        self._combo_year.currentIndexChanged.connect(self._on_year_changed)
        period_row.addWidget(self._combo_year)

        period_row.addWidget(QLabel("Mois"))
        self._combo_month = QComboBox()
        self._combo_month.setObjectName("gainPertePeriodCombo")
        self._combo_month.setMinimumWidth(128)
        self._combo_month.currentIndexChanged.connect(self._on_mois_changed)
        period_row.addWidget(self._combo_month)

        period_row.addSpacing(12)
        self._chip_gain = QLabel()
        self._chip_perte = QLabel()
        self._chip_deces = QLabel()
        for chip in (self._chip_gain, self._chip_perte, self._chip_deces):
            chip.setObjectName("gainPerteStatChip")
            chip.setTextFormat(Qt.TextFormat.RichText)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        period_row.addWidget(self._chip_gain)
        period_row.addWidget(self._chip_perte)
        period_row.addWidget(self._chip_deces)
        period_row.addStretch()
        tb.addLayout(period_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Afficher"))

        self._btn_all = QPushButton("Tous")
        self._btn_gain = QPushButton("Gains")
        self._btn_perte = QPushButton("Pertes")
        self._btn_deces = QPushButton("Décès")

        self._filter_group = QButtonGroup(self)
        for btn, tip in (
            (self._btn_all, "Tous les mouvements de la période."),
            (self._btn_gain, "Entrées d'effectif uniquement."),
            (self._btn_perte, "Sorties d'effectif (hors décès)."),
            (self._btn_deces, "Décès uniquement."),
        ):
            btn.setCheckable(True)
            btn.setObjectName("gainPerteFilterBtn")
            btn.setMinimumWidth(76)
            btn.setToolTip(tip)
            self._filter_group.addButton(btn)
        self._btn_all.setChecked(True)

        self._btn_all.clicked.connect(lambda: self._set_filter(_FILTER_ALL))
        self._btn_gain.clicked.connect(lambda: self._set_filter(_FILTER_GAIN))
        self._btn_perte.clicked.connect(lambda: self._set_filter(_FILTER_PERTE))
        self._btn_deces.clicked.connect(lambda: self._set_filter(_FILTER_DECES))

        filter_row.addWidget(self._btn_all)
        filter_row.addWidget(self._btn_gain)
        filter_row.addWidget(self._btn_perte)
        filter_row.addWidget(self._btn_deces)

        self._search = QLineEdit()
        self._search.setObjectName("gainPerteSearch")
        self._search.setPlaceholderText("Rechercher nom, grade, matricule, motif…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search, stretch=1)

        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("gainPerteCount")
        filter_row.addWidget(self._count_lbl)

        tb.addLayout(filter_row)
        lay.addWidget(top_bar)

        self._table = QTableWidget()
        self._table.setObjectName("gainPerteTable")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Grade", "Nom", "Objet", "Mois SME", "Date réf.", "Observation"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.setToolTip("Double-clic : ouvrir le dossier historique de l'agent.")
        self._table.cellDoubleClicked.connect(lambda *_: self._on_open_dossier())
        lay.addWidget(self._table, stretch=1)

        self._displayed_rows: list[GainPerteRow] = []
        self._table.itemSelectionChanged.connect(self._update_delete_button)

    # ── Public API ────────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def apply_navigation(self, *, mois_ym: str | None = None, filter_type: str | None = None) -> None:
        """Pré-sélection depuis le tableau de bord (mois + filtre GAIN/PERTE/DECES)."""
        self._pending_mois = (mois_ym or "").strip() or None
        ft = (filter_type or "").strip().upper()
        if ft in (_FILTER_GAIN, _FILTER_PERTE, _FILTER_DECES, _FILTER_ALL):
            self._pending_filter = ft
        if self.isVisible() and not self._view_data.needs_load():
            self._apply_pending_navigation()
        elif self._pending_mois or self._pending_filter:
            self._view_data.mark_stale()
            self._reload_async()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self._reload_async()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = GainPerteRepository(settings)
        self._grade_sort = None
        self._view_data.mark_stale()
        if self.isVisible():
            self._reload_async()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    # ── Private ───────────────────────────────────────────────────────────────

    def _force_reload(self) -> None:
        self._unsignaled_gen += 1
        self._unsignaled_by_mois.clear()
        self._unsignaled_loading.clear()
        self._gap_rows = []
        self._view_data.mark_stale()
        self._reload_async()

    def _reload_async(self) -> None:
        if self._reload_busy:
            return
        if not self._view_data.begin_load():
            return
        self._reload_busy = True
        repo = self._repo

        def work():
            rows = repo.fetch_all()
            months = repo.fetch_month_catalog()
            return rows, months

        def done(result: tuple) -> None:
            self._reload_busy = False
            rows, months = result
            self._all_rows = rows
            self._month_catalog = months
            self._rebuild_period_combos()
            self._apply_pending_navigation()
            self._apply_filter()
            self._view_data.finish_load()

        def failed(exc):
            self._reload_busy = False
            self._view_data.fail_load()
            from sme_gend.services.mysql_health import format_mysql_error_for_user
            QMessageBox.warning(self, "Gains et pertes", format_mysql_error_for_user(exc))

        run_in_background(self, work, done, on_error=failed)

    def _apply_pending_navigation(self) -> None:
        changed = False
        if self._pending_filter:
            self._set_filter(self._pending_filter, refresh=False)
            self._pending_filter = None
            changed = True
        if self._pending_mois:
            ym = self._pending_mois
            self._pending_mois = None
            if self._select_period_ym(ym):
                changed = True
        if changed:
            self._apply_filter()

    def _catalog_months(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        mois_actif = (load_sme_context().mois_actif or "").strip()
        for ym in list(self._month_catalog):
            ym = (ym or "").strip()
            if ym and ym not in seen:
                seen.add(ym)
                out.append(ym)
        for r in self._all_rows:
            ym = r.effective_mois_ym()
            if ym and ym not in seen:
                seen.add(ym)
                out.append(ym)
        if mois_actif and mois_actif not in seen:
            out.append(mois_actif)
        return sorted(out, reverse=True)

    def _default_target_mois(self, mois_actif: str, catalog: list[str]) -> str | None:
        """Mois actif s'il a des mouvements ; sinon le mois le plus récent qui en a.

        Sans ça, la vue s'ouvrait toujours sur le mois actif — vide juste après une
        clôture (le nouveau mois n'a pas encore de mouvements) — donnant l'impression
        que les mouvements du mois qu'on vient de clôturer avaient disparu, alors
        qu'ils sont toujours là (il fallait re-sélectionner le mois manuellement).
        """
        if mois_actif and any(r.effective_mois_ym() == mois_actif for r in self._all_rows):
            return mois_actif
        for ym in catalog:  # catalog déjà trié du plus récent au plus ancien
            if any(r.effective_mois_ym() == ym for r in self._all_rows):
                return ym
        return mois_actif or (catalog[0] if catalog else None)

    def _rebuild_period_combos(self) -> None:
        mois_actif = (load_sme_context().mois_actif or "").strip()
        catalog = self._catalog_months()
        years = _years_from_catalog(catalog)
        if mois_actif:
            p = _parse_ym(mois_actif)
            if p and p[0] not in years:
                years = sorted(set(years) | {p[0]}, reverse=True)

        prev_ym = self._current_mois_ym()
        target = self._pending_mois or prev_ym or self._default_target_mois(mois_actif, catalog)

        self._combo_year.blockSignals(True)
        self._combo_year.clear()
        self._combo_year.addItem(self._ALL_YEARS, userData=None)
        for y in years:
            self._combo_year.addItem(str(y), userData=y)

        if target:
            tp = _parse_ym(target)
            if tp:
                ty, _ = tp
                for i in range(self._combo_year.count()):
                    if self._combo_year.itemData(i) == ty:
                        self._combo_year.setCurrentIndex(i)
                        break
        elif mois_actif:
            tp = _parse_ym(mois_actif)
            if tp:
                for i in range(self._combo_year.count()):
                    if self._combo_year.itemData(i) == tp[0]:
                        self._combo_year.setCurrentIndex(i)
                        break

        self._combo_year.blockSignals(False)
        self._rebuild_month_combo()

        if target:
            self._select_period_ym(target)

    def _rebuild_month_combo(self) -> None:
        year = self._combo_year.currentData()
        prev_month = self._combo_month.currentData()

        self._combo_month.blockSignals(True)
        self._combo_month.clear()
        if year is None:
            self._combo_month.addItem(self._ALL_MONTHS, userData=None)
            self._combo_month.setEnabled(False)
        else:
            self._combo_month.setEnabled(True)
            self._combo_month.addItem(self._ALL_MONTHS, userData=None)
            catalog = set(self._catalog_months())
            for m in range(1, 13):
                ym = f"{int(year):04d}-{m:02d}"
                label = _MOIS_FR[m - 1]
                if ym in catalog:
                    label = f"{label} •"
                self._combo_month.addItem(label, userData=m)
            if prev_month is not None:
                for i in range(self._combo_month.count()):
                    if self._combo_month.itemData(i) == prev_month:
                        self._combo_month.setCurrentIndex(i)
                        break
        self._combo_month.blockSignals(False)

    def _select_period_ym(self, ym: str) -> bool:
        p = _parse_ym(ym)
        if p is None:
            return False
        y, m = p
        for i in range(self._combo_year.count()):
            if self._combo_year.itemData(i) == y:
                self._combo_year.blockSignals(True)
                self._combo_year.setCurrentIndex(i)
                self._combo_year.blockSignals(False)
                break
        self._rebuild_month_combo()
        for i in range(self._combo_month.count()):
            if self._combo_month.itemData(i) == m:
                self._combo_month.setCurrentIndex(i)
                return True
        return False

    def _current_period_scope(self) -> tuple[str | None, int | None]:
        """Retourne (YYYY-MM exact, année seule) — l'un ou l'autre, ou (None, None)."""
        year = self._combo_year.currentData()
        month = self._combo_month.currentData()
        if year is None:
            return None, None
        if month is None:
            return None, int(year)
        return f"{int(year):04d}-{int(month):02d}", None

    def _current_mois_ym(self) -> str | None:
        ym, _year = self._current_period_scope()
        return ym

    def _on_year_changed(self, _index: int) -> None:
        self._rebuild_month_combo()
        self._apply_filter()

    def _on_mois_changed(self, _index: int) -> None:
        self._apply_filter()

    def _set_filter(self, f: str, *, refresh: bool = True) -> None:
        self._filter = f
        mapping = {
            _FILTER_ALL: self._btn_all,
            _FILTER_GAIN: self._btn_gain,
            _FILTER_PERTE: self._btn_perte,
            _FILTER_DECES: self._btn_deces,
        }
        btn = mapping.get(f)
        if btn is not None:
            btn.setChecked(True)
        if refresh:
            self._apply_filter()

    def _ensure_grade_sort(self) -> GradeSortHelper:
        if self._grade_sort is None:
            self._grade_sort = grade_sort_helper_from_settings(self._settings)
        return self._grade_sort

    def _scoped_base_rows(self, mois_ym: str | None, year: int | None) -> list[GainPerteRow]:
        return [
            r
            for r in self._all_rows
            if _row_in_period_scope(r, mois_ym=mois_ym, year=year)
        ]

    def _month_base_rows(self, mois_ym: str | None) -> list[GainPerteRow]:
        if mois_ym is not None:
            return [r for r in self._all_rows if _row_matches_mois(r, mois_ym)]
        return list(self._all_rows)

    def _count_by_type(self, rows: list[GainPerteRow]) -> tuple[int, int, int]:
        n_gains = sum(1 for r in rows if r.is_gain)
        n_pertes = sum(1 for r in rows if r.is_perte)
        n_deces = sum(1 for r in rows if r.is_deces)
        return n_gains, n_pertes, n_deces

    def _update_stat_chips(self, base: list[GainPerteRow]) -> None:
        n_g, n_p, n_d = self._count_by_type(base)
        self._chip_gain.setText(_stat_chip_html("Gains", n_g, bg="#2e7d32"))
        self._chip_perte.setText(_stat_chip_html("Pertes", n_p, bg="#c62828"))
        self._chip_deces.setText(_stat_chip_html("Décès", n_d, bg="#4a148c"))

    def _update_summary(
        self,
        mois_ym: str | None,
        year: int | None,
        base: list[GainPerteRow],
    ) -> None:
        if base:
            self._summary.hide()
            return
        label = _scope_label(mois_ym, year)
        self._summary.setText(
            f"<span style='color:#64748b'>Aucun mouvement — {label}.</span>"
        )
        self._summary.show()

    def _update_crud_lock(self, mois_ym: str | None, year: int | None = None) -> None:
        from sme_gend.services.auth_guard import session_can_edit_effectifs

        can_edit_perm = session_can_edit_effectifs()
        if mois_ym is None:
            # Une vue annuelle contient des mois ouverts et clôturés : les
            # handlers vérifieront la période réellement visée avant d'écrire.
            self._lock_banner.hide()
            for btn in (self._btn_add, self._btn_edit, self._btn_del, self._btn_record_gaps):
                btn.setEnabled(can_edit_perm)
            return
        view_mois = mois_ym or (load_sme_context().mois_actif or "").strip()
        if not view_mois:
            self._lock_banner.hide()
            for btn in (self._btn_add, self._btn_edit, self._btn_del, self._btn_record_gaps):
                btn.setEnabled(can_edit_perm)
            return
        state = evaluate_editing_state(self._settings, view_mois=view_mois)
        locked = state.locked
        if locked:
            self._lock_banner.setText(f"🔒 {state.detail}")
            self._lock_banner.show()
        else:
            self._lock_banner.hide()
        for btn in (self._btn_add, self._btn_edit, self._btn_del, self._btn_record_gaps):
            btn.setEnabled(not locked and can_edit_perm)

    def _apply_filter(self) -> None:
        mois_ym, year = self._current_period_scope()
        gaps = self._gaps_for_month(mois_ym)
        self._gap_rows = gaps
        base = self._scoped_base_rows(mois_ym, year) + gaps

        if self._filter == _FILTER_GAIN:
            rows = [r for r in base if r.is_gain]
        elif self._filter == _FILTER_PERTE:
            rows = [r for r in base if r.is_perte]
        elif self._filter == _FILTER_DECES:
            rows = [r for r in base if r.is_deces]
        else:
            rows = base

        query = self._search.text() if hasattr(self, "_search") else ""
        rows = [r for r in rows if search_values(query, r.id, r.grade, r.nom,
            r.objet_display, r.observ_display, r.cdsme, r.effective_mois_ym(),
            r.date_reference, r.date_display,
            _ym_to_fr(r.effective_mois_ym()) if r.effective_mois_ym() else "")]

        self._displayed_rows = rows
        grade_helper = self._ensure_grade_sort()

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            id_item = QTableWidgetItem(r.id or "")
            id_item.setData(Qt.ItemDataRole.UserRole, i)
            self._table.setItem(i, 0, id_item)
            self._table.setItem(i, 1, GradeSortTableWidgetItem(r.grade, grade_helper))
            self._table.setItem(i, 2, QTableWidgetItem(r.nom or ""))
            self._table.setItem(i, 3, QTableWidgetItem(r.objet_display))
            ym = r.effective_mois_ym()
            mois_txt = _ym_to_fr(ym) if ym else _cdsme_to_label(r.cdsme)
            mois_item = QTableWidgetItem(mois_txt)
            cds_ym = cdsme_to_mois_ym(r.cdsme)
            if ym and cds_ym and ym != cds_ym:
                mois_item.setToolTip(
                    f"Mois SME : {ym}\nCdsme : {r.cdsme} ({cds_ym})"
                )
            self._table.setItem(i, 4, mois_item)
            self._table.setItem(i, 5, QTableWidgetItem(r.date_display))
            obs_item = QTableWidgetItem(r.observ_display)
            if r.is_deces:
                obs_item.setForeground(Qt.GlobalColor.red)
            self._table.setItem(i, 6, obs_item)
            if r.inferred:
                hint = (
                    "Présent le mois précédent, absent de la liste générale de ce mois, "
                    "sans perte enregistrée. Motif proposé : Non signalé."
                )
                bg = QColor("#fff4d6")
                for col in range(7):
                    item = self._table.item(i, col)
                    if item is not None:
                        item.setBackground(bg)
                        item.setToolTip(hint)
        self._table.setSortingEnabled(True)

        total_base = len(base)
        scope_txt = _scope_label(mois_ym, year)
        self._count_lbl.setText(
            f"{len(rows)} / {total_base} ligne(s) — {scope_txt}"
        )
        self._update_stat_chips(base)
        self._update_summary(mois_ym, year, base)
        self._update_gap_banner(mois_ym)
        self._update_crud_lock(mois_ym, year)

    def _gaps_for_month(self, mois_ym: str | None) -> list[GainPerteRow]:
        """Pertes « Non signalé » du mois, hors agents qui ont déjà une perte saisie."""
        ym = (mois_ym or "").strip()
        if not ym:
            return []
        self._schedule_unsignaled(ym)
        covered = {
            (r.id or "").strip()
            for r in self._all_rows
            if r.is_perte and not r.inferred and r.effective_mois_ym() == ym
        }
        return [
            r
            for r in self._unsignaled_by_mois.get(ym, [])
            if (r.id or "").strip() not in covered
        ]

    def _schedule_unsignaled(self, mois_ym: str) -> None:
        if mois_ym in self._unsignaled_by_mois or mois_ym in self._unsignaled_loading:
            return
        self._unsignaled_loading.add(mois_ym)
        gen = self._unsignaled_gen
        settings = self._settings
        mouvements = list(self._all_rows)

        def work() -> list[GainPerteRow]:
            from sme_gend.services.gain_perte_coherence import load_unsignaled_pertes

            return load_unsignaled_pertes(settings, mois_ym, mouvements)

        def done(rows: list[GainPerteRow]) -> None:
            self._unsignaled_loading.discard(mois_ym)
            if gen != self._unsignaled_gen:
                return
            self._unsignaled_by_mois[mois_ym] = list(rows or [])
            self._apply_filter()

        def fail(_exc: BaseException) -> None:
            self._unsignaled_loading.discard(mois_ym)
            if gen != self._unsignaled_gen:
                return
            self._unsignaled_by_mois[mois_ym] = []

        run_in_background(self, work, done, on_error=fail)

    def _update_gap_banner(self, mois_ym: str | None) -> None:
        n = len(self._gap_rows)
        if not mois_ym or n <= 0:
            self._gap_banner.hide()
            self._btn_record_gaps.hide()
            return
        label = "sortie" if n == 1 else "sorties"
        self._gap_banner.setText(
            f"<span style='color:#8a5a00;'><b>{n} {label}</b> de la liste générale "
            "sans perte enregistrée. Elles figurent ici avec le motif "
            "<b>Non signalé</b>. Modifiez la ligne pour indiquer le motif réel "
            "(décès, retraite, erreur…), ou enregistrez-les telles quelles.</span>"
        )
        self._gap_banner.show()
        self._btn_record_gaps.show()

    def _on_record_gaps(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self, "effectifs.edition",
            message="Votre compte n'a pas la permission d'enregistrer un mouvement.",
        ):
            return
        if self._editing_locked_for_current_mois():
            QMessageBox.warning(self, "Gains & Pertes", self._lock_banner.text())
            return
        rows = list(self._gap_rows)
        if not rows:
            return
        n = len(rows)
        label = "sortie" if n == 1 else "sorties"
        if not confirm_yes_no(
            self,
            "Enregistrer les sorties non signalées",
            f"Inscrire {n} {label} en Gains & Pertes avec le motif « Non signalé » ?\n\n"
            "Vous pourrez ensuite remplacer ce motif par le motif réel "
            "(décès, retraite, erreur…).",
            default_no=True,
        ):
            return
        try:
            self._repo.insert_rows(rows)
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Enregistrement impossible :\n{exc}")
            self._reload_after_change()
            return
        self._reload_after_change()

    def _default_cdsme(self) -> str | None:
        ym = self._current_mois_ym() or (load_sme_context().mois_actif or "").strip()
        return cdsme_from_mois_ym(ym) if ym else None

    def _selected_row(self) -> GainPerteRow | None:
        """Ligne sélectionnée via l'index stocké sur la cellule.

        Le tri de colonne réordonne l'affichage sans réordonner ``_displayed_rows``.
        L'index d'origine est donc porté par la cellule, pas lu via ``currentRow()``.
        """
        ridx = self._table.currentRow()
        if ridx < 0:
            return None
        id_item = self._table.item(ridx, 0)
        if id_item is None:
            return None
        idx = id_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._displayed_rows):
            return self._displayed_rows[idx]
        return None

    def _selected_rows(self) -> list[GainPerteRow]:
        selected = self._table.selectionModel().selectedRows()
        indexes = sorted({index.row() for index in selected})
        if not indexes and self._table.currentRow() >= 0:
            indexes = [self._table.currentRow()]
        rows: list[GainPerteRow] = []
        seen: set[tuple] = set()
        for visual_row in indexes:
            item = self._table.item(visual_row, 0)
            if item is None:
                continue
            original_index = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(original_index, int) or not 0 <= original_index < len(self._displayed_rows):
                continue
            row = self._displayed_rows[original_index]
            key = ("movement", row.movement_id) if row.movement_id is not None else (
                "legacy", row.id, row.effective_mois_ym(), row.objet_norm
            )
            if key not in seen:
                seen.add(key)
                rows.append(row)
        return rows

    def _update_delete_button(self) -> None:
        count = len(self._selected_rows())
        self._btn_del.setText(f"Supprimer la sélection ({count})" if count else "Supprimer la sélection")
        self._btn_del.setToolTip(
            f"Supprimer définitivement les {count} mouvement(s) sélectionné(s)."
            if count else "Sélectionnez une ou plusieurs lignes à supprimer."
        )

    def _editing_locked_for_mois(self, mois_ym: str) -> bool:
        return evaluate_editing_state(self._settings, view_mois=mois_ym).locked

    def _editing_locked_for_current_mois(self) -> bool:
        mois_ym = self._current_mois_ym()
        if not mois_ym:
            _, year = self._current_period_scope()
            actif = (load_sme_context().mois_actif or "").strip()
            if year is not None and actif:
                p = _parse_ym(actif)
                if p and p[0] == year:
                    mois_ym = actif
            if not mois_ym:
                return False
        return evaluate_editing_state(self._settings, view_mois=mois_ym).locked

    def _open_dated_movements(self) -> None:
        from sme_gend.ui.dated_effectif_movement_dialog import open_dated_effectif_movements
        open_dated_effectif_movements(self, self._settings, month=self._current_mois_ym())

    def _on_add(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self, "effectifs.edition",
            message="Votre compte n'a pas la permission d'ajouter un mouvement.",
        ):
            return
        view_mois = self._current_mois_ym() or (load_sme_context().mois_actif or "").strip()
        dlg = GainPerteEditDialog(
            self._settings,
            self,
            existing=None,
            default_cdsme=self._default_cdsme(),
            default_mois_ym=view_mois or None,
            month_catalog=self._catalog_months(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        row = dlg.to_row()
        target_month = row.effective_mois_ym()
        if not target_month:
            QMessageBox.warning(
                self, "Gains & Pertes",
                "Le mois de ce mouvement est absent ou invalide. Choisissez un mois SME valide.",
            )
            return
        state = evaluate_editing_state(self._settings, view_mois=target_month)
        if state.locked:
            QMessageBox.warning(self, "Gains & Pertes", state.detail)
            return
        if self._find_duplicate(row) is not None:
            if not confirm_yes_no(
                self,
                "Mouvement similaire existant",
                f"Un mouvement « {row.objet_display} » existe déjà pour {row.nom or row.id} "
                f"sur {_cdsme_to_label(row.cdsme)}.\n\nEnregistrer quand même ?",
                default_no=True,
            ):
                return
        try:
            self._repo.insert_row(row)
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Enregistrement impossible :\n{exc}")
            return
        self._reload_after_change()

    def _find_duplicate(self, row: GainPerteRow) -> GainPerteRow | None:
        """Mouvement existant avec le même agent, mois et objet (GAIN/PERTE)."""
        rid = (row.id or "").strip()
        ym = row.effective_mois_ym()
        if not rid or not ym:
            return None
        for r in self._all_rows:
            if (
                (r.id or "").strip() == rid
                and r.effective_mois_ym() == ym
                and r.objet_norm == row.objet_norm
            ):
                return r
        return None

    def _on_open_dossier(self) -> None:
        cur = self._selected_row()
        if cur is None:
            return
        leg = (cur.id or "").strip()
        if not leg:
            QMessageBox.information(
                self,
                "Dossier",
                "Identifiant personnel absent — impossible d'ouvrir le dossier.",
            )
            return
        from sme_gend.ui.historical_dossier_open import open_historical_dossier_for_legacy

        open_historical_dossier_for_legacy(self, self._settings, leg)

    def _on_edit(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self, "effectifs.edition",
            message="Votre compte n'a pas la permission de modifier un mouvement.",
        ):
            return
        cur = self._selected_row()
        if cur is None:
            QMessageBox.information(self, "Gains & Pertes", "Sélectionnez une ligne à modifier.")
            return
        target_month = cur.effective_mois_ym()
        if not target_month:
            QMessageBox.warning(
                self, "Gains & Pertes",
                "Le mois de ce mouvement est absent ou invalide. Actualisez les données avant de le modifier.",
            )
            return
        state = evaluate_editing_state(self._settings, view_mois=target_month)
        if state.locked:
            QMessageBox.warning(self, "Gains & Pertes", state.detail)
            return
        dlg = GainPerteEditDialog(
            self._settings,
            self,
            existing=cur,
            default_cdsme=None,
            default_mois_ym=cur.effective_mois_ym(),
            month_catalog=self._catalog_months(),
        )
        conflicted = False
        while dlg.exec() == QDialog.DialogCode.Accepted:
            new_row = dlg.to_row()
            wk = dlg.where_key()
            try:
                if wk is not None:
                    wid, wcd, mid = wk
                    self._repo.update_row(
                        new_row,
                        where_id=wid,
                        where_cdsme=wcd,
                        movement_id=mid,
                    )
                else:
                    self._repo.insert_row(new_row)
            except GainPerteConflictError as exc:
                conflicted = True
                QMessageBox.warning(
                    self, "Conflit de modification", str(exc) + "\n\n"
                    "Votre saisie va être réaffichée pour pouvoir la consulter ou la copier. "
                    "Annulez cette saisie pour charger la version actuelle et reprendre la modification.",
                )
                # Keep the same dialog and original snapshot. Never silently rebase
                # the edited fields onto another user's changes.
                continue
            except Exception as exc:
                QMessageBox.critical(self, "Gains & Pertes", f"Enregistrement impossible :\n{exc}")
                return
            self._reload_after_change()
            return
        if conflicted:
            self._force_reload()

    def _on_delete(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self, "effectifs.edition",
            message="Votre compte n'a pas la permission de supprimer un mouvement.",
        ):
            return
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Gains & Pertes", "Sélectionnez une ou plusieurs lignes à supprimer.")
            return
        inferred = [row for row in rows if row.inferred]
        if inferred:
            QMessageBox.information(
                self, "Gains & Pertes",
                "La sélection contient une ou plusieurs lignes calculées qui ne sont pas enregistrées. "
                "Elles ne peuvent pas être supprimées ici. Enregistrez-les avec un motif ou retirez-les de la sélection.",
            )
            return
        unknown_months = [row for row in rows if not row.effective_mois_ym()]
        if unknown_months:
            QMessageBox.warning(
                self, "Gains & Pertes",
                "La sélection contient un mouvement sans mois SME valide. "
                "Actualisez les données et corrigez son rattachement avant suppression.",
            )
            return
        locked_months = sorted({
            row.effective_mois_ym() for row in rows
            if self._editing_locked_for_mois(row.effective_mois_ym())
        })
        if locked_months:
            QMessageBox.warning(
                self, "Gains & Pertes",
                "La sélection contient un mois clôturé ou verrouillé : "
                + ", ".join(locked_months) + ". Aucune ligne n’a été supprimée.",
            )
            return
        month_labels = sorted({_cdsme_to_label(row.cdsme) for row in rows})
        details = f"{len(rows)} mouvement(s) sélectionné(s) — mois : {', '.join(month_labels)}"
        if not confirm_yes_no(
            self, "Supprimer les mouvements sélectionnés ?", details, default_no=True,
        ):
            return
        try:
            self._repo.delete_rows(rows)
        except GainPerteConflictError as exc:
            QMessageBox.warning(self, "Conflit de modification", str(exc))
            self._force_reload()
            return
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Suppression impossible :\n{exc}")
            return
        self._reload_after_change()

    def _on_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        mois_ym = self._current_mois_ym()
        from sme_gend.ui.export_snapshot import visible_export_rows
        try:
            rows = visible_export_rows(self._table, self._displayed_rows)
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        if not rows:
            QMessageBox.information(
                self,
                "Export",
                "Aucune ligne à exporter avec les filtres actuels.",
            )
            return
        default_name = "Gains_Pertes"
        if mois_ym:
            default_name += f"_{mois_ym.replace('-', '')}"
        settings = getattr(self, "_scoped_settings", getattr(self, "_settings", None))
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter gains & pertes",
            str(Path.home() / f"{default_name}.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        filter_label = self._filter if self._filter != _FILTER_ALL else ""
        try:
            export_gain_perte_workbook(
                Path(dest),
                rows=rows,
                mois_ym=mois_ym,
                title_suffix=filter_label,
                settings=settings,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Export impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{dest}")

    def _reload_after_change(self) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_dashboard_snapshot

        invalidate_dashboard_snapshot()
        home = getattr(self.window(), "_home_view", None)
        if home is not None and hasattr(home, "mark_dirty"):
            home.mark_dirty()
        self._view_data.mark_stale()
        self._force_reload()
