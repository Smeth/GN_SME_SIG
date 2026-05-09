"""Vue Gains & Pertes — table TabGainPerte avec sélecteur de mois et filtres GAIN/PERTE/DECES."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.gain_perte_repository import GainPerteRepository


def _cdsme_to_ym(cdsme: str | None) -> str | None:
    """'012026' → 'YYYY-MM' ('2026-01') pour comparaison."""
    if not cdsme:
        return None
    s = cdsme.strip()
    if len(s) == 6:
        return f"{s[2:]}-{s[:2]}"
    return None


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


def _ym_to_fr(ym: str) -> str:
    """'2026-01' → 'Janvier 2026'."""
    try:
        y, m = ym.split("-")
        m_int = int(m)
        if 1 <= m_int <= 12:
            return f"{_MOIS_FR[m_int - 1]} {y}"
    except (ValueError, IndexError):
        pass
    return ym


class GainPerteView(QWidget):
    """Vue dédiée TabGainPerte : tableau filtrable gains / pertes / décès par mois."""

    _COMBO_ALL = "(Tous les mois)"

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._repo = GainPerteRepository(settings)
        self._all_rows: list[GainPerteRow] = []
        self._filter = _FILTER_ALL
        self._dirty = True

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 16, 20, 16)

        title = QLabel("<h2>Gains &amp; Pertes</h2>", textFormat=Qt.TextFormat.RichText)
        lay.addWidget(title)

        desc = QLabel(
            "Données issues de <b>TabGainPerte</b> — mutations entrantes (Gains), "
            "sorties (Pertes) et décès enregistrés dans la base.",
            textFormat=Qt.TextFormat.RichText,
        )
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # ── Sélecteur de mois + barre de filtres ─────────────────────────────
        top_bar = QFrame()
        top_bar.setStyleSheet(
            "QFrame { background: #f5f5f5; border-radius: 8px; }"
        )
        tb = QHBoxLayout(top_bar)
        tb.setContentsMargins(10, 8, 10, 8)
        tb.setSpacing(12)

        tb.addWidget(QLabel("Mois :"))
        self._combo_mois = QComboBox()
        self._combo_mois.setMinimumWidth(160)
        self._combo_mois.currentIndexChanged.connect(self._on_mois_changed)
        tb.addWidget(self._combo_mois)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background: #bdbdbd; min-width: 1px; max-width: 1px;")
        tb.addWidget(sep)

        tb.addWidget(QLabel("Filtre :"))

        self._btn_all = QPushButton("Tous")
        self._btn_gain = QPushButton("Gains ↑")
        self._btn_perte = QPushButton("Pertes ↓")
        self._btn_deces = QPushButton("Décès ✝")

        for btn in (self._btn_all, self._btn_gain, self._btn_perte, self._btn_deces):
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setMinimumWidth(80)
        self._btn_all.setChecked(True)

        self._btn_all.clicked.connect(lambda: self._set_filter(_FILTER_ALL))
        self._btn_gain.clicked.connect(lambda: self._set_filter(_FILTER_GAIN))
        self._btn_perte.clicked.connect(lambda: self._set_filter(_FILTER_PERTE))
        self._btn_deces.clicked.connect(lambda: self._set_filter(_FILTER_DECES))

        tb.addWidget(self._btn_all)
        tb.addWidget(self._btn_gain)
        tb.addWidget(self._btn_perte)
        tb.addWidget(self._btn_deces)
        tb.addStretch()

        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color: #616161; font-size: 12px;")
        tb.addWidget(self._count_lbl)

        lay.addWidget(top_bar)

        # ── Tableau ───────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setObjectName("gainPerteTable")
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Grade", "Nom", "Objet", "Mois", "Observation"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        lay.addWidget(self._table, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self._dirty = True

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = GainPerteRepository(settings)
        self._dirty = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._dirty:
            self._reload()

    # ── Private ───────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._dirty = False
        try:
            self._all_rows = self._repo.fetch_all()
        except Exception:
            self._all_rows = []

        self._rebuild_combo()
        self._apply_filter()

    def _rebuild_combo(self) -> None:
        """Reconstruit le sélecteur de mois depuis les lignes chargées, sélectionne le mois actif."""
        from sme_gend.config.sme_context import load_sme_context

        mois_actif = (load_sme_context().mois_actif or "").strip()

        # Mois distincts dans les données, triés décroissant
        seen: dict[str, str] = {}  # YYYY-MM → label affiché
        for r in self._all_rows:
            ym = _cdsme_to_ym(r.cdsme)
            if ym and ym not in seen:
                seen[ym] = _ym_to_fr(ym)
        sorted_months = sorted(seen.keys(), reverse=True)

        self._combo_mois.blockSignals(True)
        self._combo_mois.clear()
        self._combo_mois.addItem(self._COMBO_ALL, userData=None)
        for ym in sorted_months:
            self._combo_mois.addItem(seen[ym], userData=ym)

        # Sélectionner le mois actif par défaut
        selected = False
        if mois_actif:
            for i in range(self._combo_mois.count()):
                if self._combo_mois.itemData(i) == mois_actif:
                    self._combo_mois.setCurrentIndex(i)
                    selected = True
                    break
        if not selected:
            self._combo_mois.setCurrentIndex(0)

        self._combo_mois.blockSignals(False)

    def _current_mois_ym(self) -> str | None:
        """Retourne le YYYY-MM sélectionné, ou None si 'Tous les mois'."""
        return self._combo_mois.currentData()

    def _on_mois_changed(self, _index: int) -> None:
        self._apply_filter()

    def _set_filter(self, f: str) -> None:
        self._filter = f
        self._apply_filter()

    def _apply_filter(self) -> None:
        mois_ym = self._current_mois_ym()

        # Filtrage par mois
        if mois_ym is not None:
            base = [r for r in self._all_rows if _cdsme_to_ym(r.cdsme) == mois_ym]
        else:
            base = list(self._all_rows)

        # Filtrage par type
        if self._filter == _FILTER_GAIN:
            rows = [r for r in base if r.is_gain]
        elif self._filter == _FILTER_PERTE:
            rows = [r for r in base if r.is_perte]
        elif self._filter == _FILTER_DECES:
            rows = [r for r in base if r.is_deces]
        else:
            rows = base

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(r.id or ""))
            self._table.setItem(i, 1, QTableWidgetItem(r.grade or ""))
            self._table.setItem(i, 2, QTableWidgetItem(r.nom or ""))
            self._table.setItem(i, 3, QTableWidgetItem(r.objet_display))
            self._table.setItem(i, 4, QTableWidgetItem(_cdsme_to_label(r.cdsme)))
            obs_item = QTableWidgetItem(r.observ_display)
            if r.is_deces:
                obs_item.setForeground(Qt.GlobalColor.red)
            self._table.setItem(i, 5, obs_item)
        self._table.setSortingEnabled(True)

        total_base = len(base)
        self._count_lbl.setText(
            f"{len(rows)} / {total_base} ligne(s)"
            + (f" — {mois_ym}" if mois_ym else "")
        )
