"""Au démarrage : confirmer ou choisir le mois SME actif (aligné sur TabSme si disponible)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm, extract_yyyy_mm

_SME_MONTHS_FR = (
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
)


def _sme_month_combo() -> QComboBox:
    cb = QComboBox()
    for i, name in enumerate(_SME_MONTHS_FR, start=1):
        cb.addItem(name, i)
    return cb


def _sme_year_spin() -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(2000, 2100)
    from datetime import date

    sp.setValue(date.today().year)
    return sp


def _widgets_to_ym(cb: QComboBox, sp: QSpinBox) -> str:
    m = cb.currentData()
    y = sp.value()
    if m is None:
        return ""
    return f"{y:04d}-{int(m):02d}"


def _ym_to_widgets(ym_hint: str, cb: QComboBox, sp: QSpinBox) -> None:
    ym = extract_yyyy_mm(ym_hint)
    if ym:
        y_s, mo_s = ym.split("-")
        sp.setValue(int(y_s))
        cb.setCurrentIndex(int(mo_s) - 1)
    else:
        from datetime import date

        d = date.today()
        sp.setValue(d.year)
        cb.setCurrentIndex(d.month - 1)


def run_startup_sme_session_dialog(parent, settings: Settings) -> None:
    """
    Si ``prompt_mois_au_demarrage`` est vrai : propose de fixer le mois actif.
    Ne bloque pas l’application si l’utilisateur ferme sans enregistrer (conserver le contexte).
    """
    ctx0 = load_sme_context()
    if not ctx0.prompt_mois_au_demarrage:
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("Période SME — ouverture de session")
    dlg.setMinimumWidth(560)
    lay = QVBoxLayout(dlg)

    lay.addWidget(
        QLabel(
            "Choisissez le <b>mois de travail</b> pour cette session (champ "
            "<code>sme_mois_context</code> dans l’audit). Vous pouvez vous appuyer sur "
            "<b>TabSme</b> si la table est disponible."
        )
    )

    repo = SmeRepository(settings)
    rows: list = []
    fetch_error: str | None = None
    if repo.table_exists():
        try:
            rows = repo.fetch_all()
        except Exception as exc:
            rows = []
            fetch_error = str(exc)
    else:
        fetch_error = "Table TabSme absente ou base inaccessible."

    if fetch_error:
        from PySide6.QtWidgets import QLabel as _QL
        warn_lbl = _QL(f"⚠ TabSme indisponible : {fetch_error}")
        warn_lbl.setStyleSheet("color: #c0392b; font-style: italic;")
        warn_lbl.setWordWrap(True)
        lay.addWidget(warn_lbl)

    if rows:
        g = QGroupBox("Périodes enregistrées (TabSme)")
        gv = QVBoxLayout(g)
        tbl = QTableWidget()
        tbl.setColumnCount(3)
        tbl.setHorizontalHeaderLabels(["Code", "Mois / période", "Statut"])
        tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            tbl.setItem(i, 0, QTableWidgetItem(r.codsme))
            tbl.setItem(i, 1, QTableWidgetItem(r.mois))
            tbl.setItem(i, 2, QTableWidgetItem(r.statut))
        tbl.resizeColumnsToContents()
        tbl.setMaximumHeight(220)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        gv.addWidget(tbl)
        lay.addWidget(g)

    g2 = QGroupBox("Mois actif pour cette session")
    f2 = QFormLayout(g2)
    cb_m = _sme_month_combo()
    sp_y = _sme_year_spin()
    _ym_to_widgets(ctx0.mois_actif, cb_m, sp_y)
    grid = QGridLayout()
    grid.addWidget(QLabel("Mois :"), 0, 0)
    grid.addWidget(cb_m, 0, 1)
    grid.addWidget(QLabel("Année :"), 0, 2)
    grid.addWidget(sp_y, 0, 3)
    f2.addRow(grid)

    combo_pick = QComboBox()
    combo_pick.blockSignals(True)
    combo_pick.addItem("(Choisir une période TabSme…)", None)
    seen_ym: set[str] = set()
    for r in rows:
        ym = canonical_sme_mois_yyyy_mm(r.mois)
        if ym and ym not in seen_ym:
            seen_ym.add(ym)
            combo_pick.addItem(f"{ym} — {r.statut} ({r.codsme})", ym)
    combo_pick.blockSignals(False)

    def _apply_pick(_idx: int) -> None:
        data = combo_pick.currentData()
        if data:
            _ym_to_widgets(str(data), cb_m, sp_y)

    combo_pick.currentIndexChanged.connect(_apply_pick)
    f2.addRow("Remplir depuis TabSme :", combo_pick)

    chk = QCheckBox("Afficher ce dialogue au prochain démarrage de l’application")
    chk.setChecked(True)
    f2.addRow(chk)

    lay.addWidget(g2)

    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        ctx = load_sme_context()
        ctx.prompt_mois_au_demarrage = chk.isChecked()
        save_sme_context(ctx)
        return

    ym = _widgets_to_ym(cb_m, sp_y)
    if not ym or extract_yyyy_mm(ym) != ym:
        QMessageBox.warning(
            dlg,
            "Mois SME",
            "Mois ou année invalides — le contexte n’a pas été modifié.",
        )
        ctx = load_sme_context()
        ctx.prompt_mois_au_demarrage = chk.isChecked()
        save_sme_context(ctx)
        return

    ctx = load_sme_context()
    ctx.mois_actif = ym
    ctx.prompt_mois_au_demarrage = chk.isChecked()
    save_sme_context(ctx)
