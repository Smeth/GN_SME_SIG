"""Attribuer un poste de tête à un agent du mois SME."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
)

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.fonction_structure_choices import canonical_fonction_libelle
from sme_gend.services.postes_vacants import PosteTete
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.list_table_filter import row_matches_search
from sme_gend.ui.reference_task import run_reference_task


def assign_poste_atomically(service, personnel, before, after):
    from sme_gend.services.shared_reference_write import shared_reference_write
    with shared_reference_write(service.settings):
        service.fonction_ensure_catalog_and_link(after.structure,after.fonction)
        personnel.update_personnel(before,after,edit_motif="Attribution poste de tête")


def open_assign_poste_dialog(
    parent,
    *,
    settings: Settings,
    service: ReferentielService,
    poste: PosteTete,
) -> bool:
    """Propose les agents de la structure et enregistre la fonction sur la ligne choisie."""
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    from sme_gend.repositories.structure_repository import StructureRepository
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.sme_editing_policy import evaluate_editing_state

    st_edit = evaluate_editing_state(settings)
    if st_edit.locked:
        QMessageBox.warning(parent, "Édition verrouillée", st_edit.detail)
        return False

    sc = (poste.structure_code or "").strip()
    sub_poste = (poste.subdivision or "").strip()
    libelle = (poste.poste or poste.intitule or "").strip()
    if not sc or not libelle:
        return False

    people=run_reference_task(parent,"Personnel de la structure",lambda:ListeGeneraleRepository(settings).fetch_all())
    rows = [
        r
        for r in people
        if (r.structure or "").strip().casefold() == sc.casefold()
    ]
    if not rows:
        QMessageBox.information(
            parent,
            "Attribuer le poste",
            "Aucun agent dans cette structure pour le mois en cours.",
        )
        return False

    struct_row = next(
        (s for s in run_reference_task(parent,"Structure",lambda:StructureRepository(settings).fetch_all()) if (s.code or "").strip() == sc),
        None,
    )
    des = struct_row.designation if struct_row else None
    fam = struct_row.famille_unite if struct_row else None

    def sort_key(r: ListeGeneraleRow) -> tuple:
        rs = (r.subdivision_arme or "").strip()
        same_sub = 0 if sub_poste and rs.casefold() == sub_poste.casefold() else 1
        return (same_sub, (r.noms or "").casefold())

    rows = sorted(rows, key=sort_key)

    dlg = QDialog(parent)
    dlg.setWindowTitle("Attribuer le poste")
    dlg.setMinimumWidth(520)
    hint = QLabel(
        f"<b>{poste.structure_label}</b><br>"
        f"Unité : {sub_poste or 'Commandement'}<br>"
        f"Poste : {libelle}"
    )
    hint.setWordWrap(True)

    search = QLineEdit()
    search.setPlaceholderText("Rechercher nom, grade, matricule, fonction, subdivision…")
    search.setClearButtonEnabled(True)

    list_w = QListWidget()
    list_w.setMinimumHeight(300)

    ok_btn = None

    def _refill_list(needle: str = "") -> None:
        list_w.clear()
        for row in rows:
            if not row_matches_search(row, needle):
                continue
            sub = (row.subdivision_arme or "").strip() or "—"
            fn = (row.fonction or "").strip() or "—"
            grade = (row.grade or "").strip()
            prefix = f"{grade} — " if grade else ""
            label = f"{prefix}{row.noms or '?'} — {sub} — {fn}"
            list_w.addItem(label)
            item = list_w.item(list_w.count() - 1)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, row.id)
        if list_w.count():
            list_w.setCurrentRow(0)
        if ok_btn is not None:
            ok_btn.setEnabled(list_w.count() > 0)

    search.textChanged.connect(_refill_list)
    _refill_list()

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
    if ok_btn is not None:
        ok_btn.setEnabled(list_w.count() > 0)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    list_w.itemDoubleClicked.connect(lambda _item: dlg.accept())

    lay = QVBoxLayout(dlg)
    lay.addWidget(hint)
    lay.addWidget(QLabel("Agent :"))
    lay.addWidget(search)
    lay.addWidget(list_w)
    lay.addWidget(buttons)
    search.setFocus()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    item = list_w.currentItem()
    if item is None:
        return False
    row_id = item.data(Qt.ItemDataRole.UserRole)
    if not row_id:
        return False

    personnel = PersonnelService(settings)
    before = run_reference_task(parent,"Dossier",lambda:personnel.get_by_id(str(row_id)))
    if before is None:
        QMessageBox.warning(parent, "Attribuer le poste", "Ligne introuvable. Actualisez la liste.")
        return False

    sub_agent = (before.subdivision_arme or "").strip()
    if sub_poste and sub_agent.casefold() != sub_poste.casefold():
        if QMessageBox.question(
            parent,
            "Subdivision",
            f"L'agent est en « {sub_agent or '—'} ».\n"
            f"Le poste est dans « {sub_poste} ».\n\n"
            "Mettre à jour la subdivision d'arme de l'agent ?",
        ) != QMessageBox.StandardButton.Yes:
            return False
        sub_agent = sub_poste

    canon = canonical_fonction_libelle(
        libelle,
        structure_code=sc,
        designation=des,
        famille=fam,
        subdivision=sub_agent or sub_poste or None,
    )
    after = replace(
        before,
        structure=sc,
        subdivision_arme=sub_agent or sub_poste or before.subdivision_arme,
        fonction=canon,
    )
    try:
        if service.settings != settings:
            raise ValueError("Le contexte a changé. Fermez puis rouvrez cette fenêtre.")
        run_reference_task(parent,"Attribution du poste",lambda:assign_poste_atomically(service,personnel,before,after))
    except Exception as exc:
        QMessageBox.critical(parent, "Enregistrement", str(exc))
        return False
    return True
