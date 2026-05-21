"""Malades (TabMalade) et positions Punis / Prison MA — saisie liée à la liste générale."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.numerique_padmin import (
    PADMIN_MALADE,
    PADMIN_PRISON_MA,
    PADMIN_PUNIS,
)


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


class MaladePunisView(QWidget):
    """Onglets : fiche maladie (TabMalade) ; positions Punis / Prison MA (ListeGenerale)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._dirty = True
        self._malade_rows: list[ListeGeneraleRow] = []
        self._punis_rows: list[ListeGeneraleRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("<h2>Malades &amp; Punis</h2>", textFormat=Qt.TextFormat.RichText)
        root.addWidget(title)

        hint = QLabel(
            "<b>Malades</b> : personnel avec position « Malade » — enregistrement dans <b>TabMalade</b> / "
            "<code>tab_malade</code> (clé = identifiant liste).<br>"
            "<b>Punis</b> : personnel en « Punis » ou « Prison MA » — modification de la "
            "<b>position administrative</b> (ListeGenerale).",
            textFormat=Qt.TextFormat.RichText,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._build_malade_tab(), "Malades (TabMalade)")
        tabs.addTab(self._build_punis_tab(), "Punis & détention")
        root.addWidget(tabs, stretch=1)

    def _build_malade_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        bar = QHBoxLayout()
        self._btn_m_refresh = QPushButton("Actualiser")
        self._btn_m_refresh.clicked.connect(self._reload_malades)
        bar.addWidget(self._btn_m_refresh)
        bar.addStretch()
        lay.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._tbl_malade = QTableWidget()
        self._tbl_malade.setColumnCount(5)
        self._tbl_malade.setHorizontalHeaderLabels(
            ["Id liaison", "Nom", "Grade", "Structure", "Padmin"]
        )
        hh = self._tbl_malade.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_malade.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_malade.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._tbl_malade.itemSelectionChanged.connect(self._on_malade_select)
        split.addWidget(self._tbl_malade)

        form_box = QGroupBox("Fiche maladie (TabMalade)")
        fl = QFormLayout(form_box)
        self._m_date = QLineEdit()
        self._m_date.setPlaceholderText("Date constat (texte libre)")
        self._m_path = QLineEdit()
        self._m_path.setPlaceholderText("Pathologie")
        self._m_obs = QLineEdit()
        self._m_obs.setPlaceholderText("Observations")
        fl.addRow("Date constat :", self._m_date)
        fl.addRow("Pathologie :", self._m_path)
        fl.addRow("Obs. :", self._m_obs)
        self._btn_m_save = QPushButton("Enregistrer la fiche")
        self._btn_m_save.clicked.connect(self._save_malade_fiche)
        self._btn_m_del = QPushButton("Supprimer la fiche TabMalade")
        self._btn_m_del.clicked.connect(self._delete_malade_fiche)
        vb = QVBoxLayout()
        vb.addWidget(form_box)
        vb.addWidget(self._btn_m_save)
        vb.addWidget(self._btn_m_del)
        vb.addStretch()
        wrap = QWidget()
        wrap.setLayout(vb)
        wrap.setMinimumWidth(320)
        split.addWidget(wrap)
        lay.addWidget(split)
        return w

    def _build_punis_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        bar = QHBoxLayout()
        self._btn_p_refresh = QPushButton("Actualiser")
        self._btn_p_refresh.clicked.connect(self._reload_punis)
        bar.addWidget(self._btn_p_refresh)
        bar.addStretch()
        lay.addLayout(bar)

        self._tbl_punis = QTableWidget()
        self._tbl_punis.setColumnCount(5)
        self._tbl_punis.setHorizontalHeaderLabels(
            ["Id liste", "Nom", "Grade", "Structure", "Padmin actuel"]
        )
        h2 = self._tbl_punis.horizontalHeader()
        h2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_punis.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_punis.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        lay.addWidget(self._tbl_punis, stretch=1)

        box = QGroupBox("Nouvelle position administrative")
        hb = QHBoxLayout(box)
        self._combo_new_padmin = QComboBox()
        for lab in _PADMIN_CHOICES:
            self._combo_new_padmin.addItem(lab)
        hb.addWidget(QLabel("Position :"))
        hb.addWidget(self._combo_new_padmin, stretch=1)
        self._btn_p_apply = QPushButton("Appliquer aux lignes sélectionnées")
        self._btn_p_apply.clicked.connect(self._apply_padmin_selection)
        hb.addWidget(self._btn_p_apply)
        lay.addWidget(box)
        return w

    def mark_dirty(self) -> None:
        self._dirty = True

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._sat = NumeriqueSatelliteRepository(settings)
        self._dirty = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._dirty:
            self._reload_malades()
            self._reload_punis()
            self._dirty = False

    def _reload_malades(self) -> None:
        try:
            all_rows = self._liste.fetch_all()
        except Exception as exc:
            QMessageBox.warning(self, "Malades", f"Lecture liste impossible :\n{exc}")
            return
        self._malade_rows = [r for r in all_rows if _is_malade_row(r)]
        self._tbl_malade.setRowCount(len(self._malade_rows))
        for i, r in enumerate(self._malade_rows):
            key = r.satellite_join_key()
            self._tbl_malade.setItem(i, 0, QTableWidgetItem(key))
            self._tbl_malade.setItem(i, 1, QTableWidgetItem(r.noms or ""))
            self._tbl_malade.setItem(i, 2, QTableWidgetItem(r.grade or ""))
            self._tbl_malade.setItem(i, 3, QTableWidgetItem(r.structure or ""))
            self._tbl_malade.setItem(i, 4, QTableWidgetItem(r.position_admin or ""))
        self._clear_malade_form()

    def _reload_punis(self) -> None:
        try:
            all_rows = self._liste.fetch_all()
        except Exception as exc:
            QMessageBox.warning(self, "Punis", f"Lecture liste impossible :\n{exc}")
            return
        self._punis_rows = [r for r in all_rows if _is_punis_row(r)]
        self._tbl_punis.setRowCount(len(self._punis_rows))
        for i, r in enumerate(self._punis_rows):
            self._tbl_punis.setItem(i, 0, QTableWidgetItem(r.id or ""))
            self._tbl_punis.setItem(i, 1, QTableWidgetItem(r.noms or ""))
            self._tbl_punis.setItem(i, 2, QTableWidgetItem(r.grade or ""))
            self._tbl_punis.setItem(i, 3, QTableWidgetItem(r.structure or ""))
            self._tbl_punis.setItem(i, 4, QTableWidgetItem(r.position_admin or ""))

    def _clear_malade_form(self) -> None:
        self._m_date.clear()
        self._m_path.clear()
        self._m_obs.clear()

    def _on_malade_select(self) -> None:
        rows = self._tbl_malade.selectionModel().selectedRows()
        if not rows:
            self._clear_malade_form()
            return
        idx = rows[0].row()
        if idx < 0 or idx >= len(self._malade_rows):
            return
        r = self._malade_rows[idx]
        key = r.satellite_join_key()
        try:
            m = self._sat.load_malades().get(key)
        except Exception:
            m = None
        self._m_date.setText(m.date_constat or "" if m else "")
        self._m_path.setText(m.pathologie or "" if m else "")
        self._m_obs.setText(m.obs or "" if m else "")

    def _current_malade_row(self) -> ListeGeneraleRow | None:
        rows = self._tbl_malade.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._malade_rows):
            return self._malade_rows[idx]
        return None

    def _save_malade_fiche(self) -> None:
        r = self._current_malade_row()
        if r is None:
            QMessageBox.information(self, "Malades", "Sélectionnez une ligne dans le tableau.")
            return
        key = r.satellite_join_key()
        if not key:
            QMessageBox.warning(self, "Malades", "Identifiant de liaison vide — impossible d'écrire TabMalade.")
            return
        detail = MaladeDetail(
            date_constat=self._m_date.text().strip() or None,
            pathologie=self._m_path.text().strip() or None,
            obs=self._m_obs.text().strip() or None,
        )
        try:
            self._sat.upsert_malade(key, detail)
        except Exception as exc:
            QMessageBox.critical(self, "Malades", f"Enregistrement impossible :\n{exc}")
            return
        QMessageBox.information(self, "Malades", "Fiche enregistrée.")
        self._on_malade_select()

    def _delete_malade_fiche(self) -> None:
        r = self._current_malade_row()
        if r is None:
            QMessageBox.information(self, "Malades", "Sélectionnez une ligne dans le tableau.")
            return
        key = r.satellite_join_key()
        if not key:
            return
        if QMessageBox.question(
            self,
            "Supprimer",
            "Supprimer la ligne TabMalade pour ce personnel ?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._sat.delete_malade(key)
        except Exception as exc:
            QMessageBox.critical(self, "Malades", f"Suppression impossible :\n{exc}")
            return
        self._clear_malade_form()
        self._on_malade_select()

    def _apply_padmin_selection(self) -> None:
        sel = self._tbl_punis.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(self, "Punis", "Sélectionnez au moins une ligne.")
            return
        new_p = self._combo_new_padmin.currentText().strip()
        if not new_p:
            return
        indices = sorted({ix.row() for ix in sel})
        try:
            all_by_id = {r.id: r for r in self._liste.fetch_all() if r.id}
        except Exception as exc:
            QMessageBox.critical(self, "Punis", str(exc))
            return
        n_ok = 0
        errs: list[str] = []
        for ix in indices:
            if ix < 0 or ix >= len(self._punis_rows):
                continue
            pr = self._punis_rows[ix]
            cur = all_by_id.get(pr.id)
            if cur is None:
                errs.append(pr.id or "?")
                continue
            try:
                updated = replace(cur, position_admin=new_p)
                self._liste.update_row(updated)
                n_ok += 1
            except Exception as exc:
                errs.append(f"{pr.noms or pr.id}: {exc}")
        self._reload_punis()
        msg = f"{n_ok} ligne(s) mise(s) à jour."
        if errs:
            msg += "\n\nÉchecs :\n" + "\n".join(errs[:8])
            if len(errs) > 8:
                msg += f"\n… (+{len(errs) - 8})"
        QMessageBox.information(self, "Punis", msg)
