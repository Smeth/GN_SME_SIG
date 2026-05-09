"""Règles métier — liste générale (couche au-dessus des repositories)."""

from __future__ import annotations

import logging

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, mois_actif_courant

_log = logging.getLogger(__name__)


def _audit_mois_context(action: str, personnel_id: str) -> str | None:
    """Retourne le mois actif pour l'audit ; log un avertissement s'il est absent."""
    ctx = (mois_actif_courant() or "").strip() or None
    if ctx is None:
        _log.warning(
            "Audit personnel '%s' (id=%s) sans contexte mois_actif — "
            "définir un mois actif dans Paramètres > Session SME pour la traçabilité.",
            action, personnel_id,
        )
    return ctx
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services import audit_log
from sme_gend.services.personnel_list_order import sort_liste_generale_default_order


class PersonnelService:
    """Service principal : liste du personnel (lecture et mise à jour)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def rebind_settings(self, settings: Settings) -> None:
        """Recrée les dépôts après changement de chemins (sans redémarrage)."""
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    def liste_complete(self) -> list[ListeGeneraleRow]:
        raw = self._repo.fetch_all()
        grades = self._grades.fetch_all()
        return sort_liste_generale_default_order(raw, grades)

    def liste_presents(self) -> list[ListeGeneraleRow]:
        """Personnes comptées présentes : Padmin « présent » ou stage interne (liste des présents — base)."""
        return self._repo.fetch_presents()

    def get_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        return self._repo.fetch_by_id(row_id)

    def update_personnel(
        self,
        before: ListeGeneraleRow,
        after: ListeGeneraleRow,
        *,
        edited_by: str | None = None,
        edit_motif: str | None = None,
    ) -> None:
        """Met à jour Access et enregistre la traçabilité (fichier JSON Lines)."""
        if before.id != after.id:
            raise ValueError("L'identifiant du personnel ne peut pas être modifié.")
        changes = audit_log.diff_personnel_rows(before, after)
        if not changes:
            return
        self._repo.update_row(after)
        ctx = _audit_mois_context("update", after.id)
        ctx_obj = load_sme_context()
        def_ed = (ctx_obj.editeur_defaut or "").strip()
        eb = (edited_by or "").strip() or (def_ed or None)
        audit_log.append_personnel_audit(
            action="update",
            personnel_id=after.id,
            changes=changes,
            snapshot_before=audit_log.row_to_audit_dict(before),
            snapshot_after=audit_log.row_to_audit_dict(after),
            sme_mois_context=ctx,
            edited_by=eb,
            edit_motif=(edit_motif or "").strip() or None,
        )

    def create_personnel(
        self,
        row: ListeGeneraleRow,
        *,
        edited_by: str | None = None,
    ) -> str:
        """Insère une nouvelle fiche ; retourne l'id attribué par la base."""
        new_id = self._repo.insert_row(row)
        ctx = _audit_mois_context("create", new_id)
        ctx_obj = load_sme_context()
        def_ed = (ctx_obj.editeur_defaut or "").strip()
        eb = (edited_by or "").strip() or (def_ed or None)
        new_row_with_id = ListeGeneraleRow(
            id=new_id,
            noms=row.noms,
            date_naissance=row.date_naissance,
            lieu_naissance=row.lieu_naissance,
            grade=row.grade,
            date_grade=row.date_grade,
            date_service=row.date_service,
            ndecision=row.ndecision,
            matricule_militaire=row.matricule_militaire,
            matricule_solde=row.matricule_solde,
            position_admin=row.position_admin,
            fonction=row.fonction,
            subdivision_arme=row.subdivision_arme,
            diplome=row.diplome,
            date_obtention=row.date_obtention,
            structure=row.structure,
            obs=row.obs,
            ordre_tri=row.ordre_tri,
            presseance=row.presseance,
        )
        audit_log.append_personnel_audit(
            action="create",
            personnel_id=new_id,
            changes={},
            snapshot_before={},
            snapshot_after=audit_log.row_to_audit_dict(new_row_with_id),
            sme_mois_context=ctx,
            edited_by=eb,
            edit_motif=None,
        )
        return new_id

    def delete_personnel(
        self,
        row: ListeGeneraleRow,
        *,
        edited_by: str | None = None,
        motif: str | None = None,
    ) -> None:
        """Supprime une fiche et enregistre l'événement dans l'audit."""
        self._repo.delete_row(row.id)
        ctx = _audit_mois_context("delete", row.id)
        ctx_obj = load_sme_context()
        def_ed = (ctx_obj.editeur_defaut or "").strip()
        eb = (edited_by or "").strip() or (def_ed or None)
        audit_log.append_personnel_audit(
            action="delete",
            personnel_id=row.id,
            changes={},
            snapshot_before=audit_log.row_to_audit_dict(row),
            snapshot_after={},
            sme_mois_context=ctx,
            edited_by=eb,
            edit_motif=(motif or "").strip() or None,
        )

    def count_by_structure_code(self) -> dict[str, int]:
        """Effectifs par code structure (liste générale brute)."""
        raw = self._repo.fetch_all()
        counts: dict[str, int] = {}
        for r in raw:
            c = (r.structure or "").strip()
            key = c if c else "(Sans structure)"
            counts[key] = counts.get(key, 0) + 1
        return counts
