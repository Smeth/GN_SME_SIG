"""Au démarrage : confirmer ou choisir le mois SME actif (aligné sur sme_periode si disponible)."""

from __future__ import annotations

from copy import deepcopy
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict
from sme_gend.ui.confirm_dialogs import confirm_yes_no


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
            "<b>sme_periode</b> si la table est disponible."
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
        fetch_error = "Table sme_periode absente ou base inaccessible."

    if fetch_error:
        from PySide6.QtWidgets import QLabel as _QL
        warn_lbl = _QL(f"⚠ sme_periode indisponible : {fetch_error}")
        warn_lbl.setStyleSheet("color: #c0392b; font-style: italic;")
        warn_lbl.setWordWrap(True)
        lay.addWidget(warn_lbl)

    if rows:
        g = QGroupBox("Périodes enregistrées (sme_periode)")
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
    combo_pick.addItem("(Choisir une période sme_periode…)", None)
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
    f2.addRow("Remplir depuis sme_periode :", combo_pick)

    chk = QCheckBox("Afficher ce dialogue au prochain démarrage de l’application")
    chk.setChecked(True)
    f2.addRow(chk)

    lay.addWidget(g2)

    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    def persist(*, include_month: bool) -> bool:
        ctx = deepcopy(ctx0)
        ctx.prompt_mois_au_demarrage = chk.isChecked()
        if include_month:
            ym = _widgets_to_ym(cb_m, sp_y)
            if not ym or extract_yyyy_mm(ym) != ym:
                QMessageBox.warning(dlg,"Mois SME","Mois ou année invalides — aucune modification enregistrée.")
                return False
            ctx.mois_actif = ym
        try:
            if include_month:
                save_sme_context(ctx,force_month_change=True)
            else:
                save_sme_context(ctx,promote_operational_month=False)
        except ReferenceConflictError as exc:
            show_reference_conflict(dlg,exc,draft=True,reload_available=True)
            return False
        except Exception as exc:
            QMessageBox.critical(dlg,"Période SME",str(exc))
            return False
        return True

    def accept() -> None:
        if persist(include_month=True):
            dlg.accept()

    cancel_saved = False

    def cancel() -> None:
        nonlocal cancel_saved
        # Keep the historical checkbox save on Cancel when possible. A failed
        # save is explicit; the user can still leave without changing the month.
        persist(include_month=False)
        cancel_saved = True
        dlg.reject()

    def reload() -> None:
        nonlocal ctx0
        if not confirm_yes_no(dlg,"Recharger","Le mois et la préférence non enregistrés seront remplacés. Recharger ?",default_no=True):
            return
        fresh = load_sme_context()
        if fresh._workspace_snapshot is None:
            QMessageBox.critical(dlg,"Période SME","Workspace indisponible. Votre saisie est conservée.")
            return
        ctx0 = fresh
        chk.setChecked(ctx0.prompt_mois_au_demarrage)
        _ym_to_widgets(ctx0.mois_actif, cb_m, sp_y)

    reload_btn = bb.addButton("Recharger", QDialogButtonBox.ButtonRole.ActionRole)
    reload_btn.clicked.connect(reload)
    bb.accepted.connect(accept)
    bb.rejected.connect(cancel)
    lay.addWidget(bb)
    if dlg.exec() != QDialog.DialogCode.Accepted and not cancel_saved:
        persist(include_month=False)



def run_startup_open_month_recovery(parent, settings) -> bool:
    """Ask an administrator to reconcile multiple open months before app use."""
    from sme_gend.services.active_month_guard import list_open_months_for_recovery,recover_multiple_open_months
    from sme_gend.services.auth_guard import session_has_admin_role
    try:
        months=list_open_months_for_recovery(settings)
    except Exception as exc:
        QMessageBox.critical(parent,"Périodes SME",f"Impossible de vérifier les mois ouverts. Aucune période n'a été modifiée.\n\n{exc}")
        return False
    if len(months)<2:
        return True
    if not session_has_admin_role():
        QMessageBox.critical(parent,"Réparation des périodes","Plusieurs mois sont ouverts dans la base partagée. Connectez-vous avec un compte administrateur pour choisir le mois de travail. Aucune période n'a été modifiée.")
        return False

    dlg=QDialog(parent)
    dlg.setWindowTitle("Réparer les périodes SME")
    dlg.setMinimumWidth(560)
    layout=QVBoxLayout(dlg)
    intro=QLabel("Plusieurs mois sont actuellement marqués « Ouvert ». Choisissez celui qui doit rester le mois de travail partagé.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    table=QTableWidget(len(months),2,dlg)
    table.setHorizontalHeaderLabels(["Mois ouvert","Code(s) SME"])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    for row,(month,codes) in enumerate(months):
        table.setItem(row,0,QTableWidgetItem(month))
        table.setItem(row,1,QTableWidgetItem(", ".join(codes)))
    table.resizeColumnsToContents()
    layout.addWidget(table)
    choice=QComboBox(dlg)
    choice.addItem("— Choisir le mois à garder ouvert —",None)
    for month,_codes in months:
        choice.addItem(month,month)
    layout.addWidget(QLabel("Mois de travail à conserver :"))
    layout.addWidget(choice)
    warning=QLabel("Les autres mois « Ouvert » seront fermés. L’application mettra aussi à jour les indicateurs de carrière, la synchronisation et le journal. Les mois « Réouvert » pour correction exceptionnelle ne seront pas modifiés.")
    warning.setWordWrap(True)
    layout.addWidget(warning)
    buttons=QDialogButtonBox(dlg)
    apply_button=buttons.addButton("Garder ce mois et fermer les autres",QDialogButtonBox.ButtonRole.AcceptRole)
    cancel_button=buttons.addButton("Quitter sans réparer",QDialogButtonBox.ButtonRole.RejectRole)
    apply_button.setEnabled(False)
    choice.currentIndexChanged.connect(lambda index:apply_button.setEnabled(index>0))
    cancel_button.clicked.connect(dlg.reject)
    def apply_recovery():
        target=choice.currentData()
        if not target:
            return
        answer=QMessageBox.question(dlg,"Confirmer la réparation",
            f"Garder {target} comme seul mois « Ouvert » et fermer les autres mois listés ?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:
            return
        try:
            result=recover_multiple_open_months(settings,str(target),expected_open_months=[month for month,_codes in months])
        except Exception as exc:
            QMessageBox.critical(dlg,"Réparation annulée",f"Aucune réparation n’a été validée.\n\n{exc}")
            return
        dlg.setProperty("recovery_succeeded",True)
        dlg.accept()
        closed=", ".join(result["closed_months"]) or "aucun"
        QMessageBox.information(parent,"Périodes SME",f"Mois de travail : {result['active_month']}\nMois fermés : {closed}\n\nLes autres postes seront actualisés à leur prochaine synchronisation.")
    apply_button.clicked.connect(apply_recovery)
    layout.addWidget(buttons)
    dlg.exec()
    return bool(dlg.property("recovery_succeeded"))
