"""Vue Gains & Pertes — table TabGainPerte avec sélecteur de mois et filtres GAIN/PERTE/DECES."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.gain_perte_repository import GainPerteRepository, cdsme_from_mois_ym
from sme_gend.ui.confirm_dialogs import confirm_yes_no


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


class GainPerteEditDialog(QDialog):
    """Saisie / modification d'une ligne TabGainPerte."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        existing: GainPerteRow | None,
        default_cdsme: str | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ligne gains & pertes" if existing else "Nouvelle ligne gains & pertes")
        self._existing = existing
        self._where_id: str | None = None
        self._where_cdsme: str | None = None

        form = QFormLayout()
        self._id_le = QLineEdit()
        self._grade_le = QLineEdit()
        self._nom_le = QLineEdit()
        self._obj_combo = QComboBox()
        self._obj_combo.addItems(["GAIN", "PERTE"])
        self._cdsme_le = QLineEdit()
        self._cdsme_le.setPlaceholderText("Ex. 052026 (MMYYYY)")
        self._obs_le = QLineEdit()

        form.addRow("ID (personnel / réf.) :", self._id_le)
        form.addRow("Grade :", self._grade_le)
        form.addRow("Nom :", self._nom_le)
        form.addRow("Objet :", self._obj_combo)
        form.addRow("Cdsme (MMYYYY) :", self._cdsme_le)
        form.addRow("Observation :", self._obs_le)

        if existing:
            self._where_id = (existing.id or "").strip()
            self._where_cdsme = (existing.cdsme or "").strip() or None
            self._id_le.setText(existing.id or "")
            self._grade_le.setText(existing.grade or "")
            self._nom_le.setText(existing.nom or "")
            o = (existing.objet or "").strip().upper()
            if o == "GAIN":
                self._obj_combo.setCurrentIndex(0)
            elif o == "PERTE":
                self._obj_combo.setCurrentIndex(1)
            self._cdsme_le.setText(existing.cdsme or "")
            self._obs_le.setText(existing.observ or "")
        elif default_cdsme:
            self._cdsme_le.setText(default_cdsme)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(bb)

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
        self.accept()

    def to_row(self) -> GainPerteRow:
        obj = self._obj_combo.currentText().strip().upper()
        return GainPerteRow(
            id=self._id_le.text().strip(),
            grade=self._grade_le.text().strip() or None,
            nom=self._nom_le.text().strip() or None,
            objet=obj,
            cdsme=self._cdsme_le.text().strip(),
            observ=self._obs_le.text().strip() or None,
        )

    def where_key(self) -> tuple[str, str | None] | None:
        if self._existing is None:
            return None
        return (self._where_id or "", self._where_cdsme)


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

        crud = QHBoxLayout()
        self._btn_add = QPushButton("Nouvelle ligne…")
        self._btn_edit = QPushButton("Modifier…")
        self._btn_del = QPushButton("Supprimer")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del.clicked.connect(self._on_delete)
        crud.addWidget(self._btn_add)
        crud.addWidget(self._btn_edit)
        crud.addWidget(self._btn_del)
        crud.addStretch()
        lay.addLayout(crud)

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
        self._table.cellDoubleClicked.connect(lambda *_: self._on_edit())
        lay.addWidget(self._table, stretch=1)

        self._displayed_rows: list[GainPerteRow] = []

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

        self._displayed_rows = rows

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

    def _default_cdsme(self) -> str | None:
        ym = (load_sme_context().mois_actif or "").strip()
        return cdsme_from_mois_ym(ym) if ym else None

    def _selected_row(self) -> GainPerteRow | None:
        ridx = self._table.currentRow()
        if ridx < 0 or ridx >= len(self._displayed_rows):
            return None
        return self._displayed_rows[ridx]

    def _on_add(self) -> None:
        dlg = GainPerteEditDialog(self, existing=None, default_cdsme=self._default_cdsme())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        row = dlg.to_row()
        try:
            self._repo.insert_row(row)
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Enregistrement impossible :\n{exc}")
            return
        self._reload_after_change()

    def _on_edit(self) -> None:
        cur = self._selected_row()
        if cur is None:
            QMessageBox.information(self, "Gains & Pertes", "Sélectionnez une ligne à modifier.")
            return
        dlg = GainPerteEditDialog(self, existing=cur, default_cdsme=None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.to_row()
        wk = dlg.where_key()
        try:
            if wk is not None:
                wid, wcd = wk
                self._repo.update_row(new_row, where_id=wid, where_cdsme=wcd)
            else:
                self._repo.insert_row(new_row)
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Enregistrement impossible :\n{exc}")
            return
        self._reload_after_change()

    def _on_delete(self) -> None:
        cur = self._selected_row()
        if cur is None:
            QMessageBox.information(self, "Gains & Pertes", "Sélectionnez une ligne à supprimer.")
            return
        if not confirm_yes_no(
            self,
            "Supprimer cette ligne TabGainPerte ?",
            f"ID {cur.id} — mois {_cdsme_to_label(cur.cdsme)}",
        ):
            return
        try:
            self._repo.delete_row(cur.id, cur.cdsme)
        except Exception as exc:
            QMessageBox.critical(self, "Gains & Pertes", f"Suppression impossible :\n{exc}")
            return
        self._reload_after_change()

    def _reload_after_change(self) -> None:
        self._dirty = True
        self._reload()
