"""Règles métier — liste générale (couche au-dessus des repositories)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import replace

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, mois_actif_courant
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services import audit_log
from sme_gend.services.agent_dossier_sync import sync_dossier_after_personnel_save

_log = logging.getLogger(__name__)


@contextmanager
def _personnel_write(settings: Settings, *, reason: str = "personnel_write"):
    from sme_gend.services.shared_reference_write import shared_data_write, mark_shared_data_write
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    try:
        with shared_data_write(settings,reason=reason,mois=settings.mysql_view_mois):
            yield
            mark_shared_data_write(True)
    finally:
        # A rollback must also discard any intermediate reads cached by the
        # caller. Shared invalidation and notification follow the real commit.
        invalidate_liste_cache(settings)


def _audit_mois_context(action: str, personnel_id: str, settings: Settings | None = None) -> str | None:
    """Retourne le mois actif pour l'audit ; log un avertissement s'il est absent."""
    ctx = ((settings.mysql_view_mois if settings else None) or mois_actif_courant() or "").strip() or None
    if ctx is None:
        _log.warning(
            "Audit personnel '%s' (id=%s) sans contexte mois_actif — "
            "définir un mois actif dans Paramètres > Session SME pour la traçabilité.",
            action, personnel_id,
        )
    return ctx


def _audit_personnel_id(row: ListeGeneraleRow) -> str:
    """Clé stable pour l'audit (legacy Access si connu, sinon id carrière)."""
    leg = (row.satellite_join_key() or "").strip()
    return leg or str(row.id or "").strip()


class PersonnelService:
    """Service principal : liste du personnel (lecture et mise à jour)."""

    def _assert_fonction_si_changee(
        self, before: ListeGeneraleRow, after: ListeGeneraleRow
    ) -> None:
        from sme_gend.services.fonction_occupancy import (
            assert_fonction_disponible,
            same_fonction_poste,
        )

        if same_fonction_poste(
            before.structure,
            before.fonction,
            after.structure,
            after.fonction,
            before.subdivision_arme,
            after.subdivision_arme,
        ):
            return
        assert_fonction_disponible(
            self.liste_complete(),
            structure=after.structure,
            fonction=after.fonction,
            subdivision=after.subdivision_arme,
            exclude_row_id=after.id,
        )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def rebind_settings(self, settings: Settings) -> None:
        """Recrée les dépôts après changement de chemins (sans redémarrage)."""
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._settings)
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    def liste_complete(self) -> list[ListeGeneraleRow]:
        from sme_gend.services.liste_generale_cache import cached_liste_rows

        return cached_liste_rows(self._settings)

    def liste_presents(self) -> list[ListeGeneraleRow]:
        """Présents : ref_position_admin.compte_comme_present (repli texte « présent »)."""
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
        """Met à jour MySQL et enregistre la traçabilité (audit_journal).

        SoT : écriture carrière + agent, puis dossier maître (diplôme/grade/affectation)
        et projection ``sync_carriere_from_dossier`` pour aligner l'affichage liste.
        """
        from sme_gend.services.sensitive_data_policy import assert_may_edit_personnel
        assert_may_edit_personnel()
        if before.id != after.id:
            raise ValueError("L'identifiant du personnel ne peut pas être modifié.")
        from sme_gend.services.agent_dossier_sync import validate_monthly_history_edit
        validate_monthly_history_edit(before, after)
        from sme_gend.services.sensitive_data_policy import (
            ADMIN_ONLY_PERSONNEL_FIELDS,
            assert_may_edit_personnel_sensitive_fields,
        )

        assert_may_edit_personnel_sensitive_fields(before, after)
        self._assert_fonction_si_changee(before, after)
        changes = audit_log.diff_personnel_rows(before, after)
        if not changes:
            return
        sensitive = {k: v for k, v in changes.items() if k in ADMIN_ONLY_PERSONNEL_FIELDS}
        with _personnel_write(self._settings):
            self._repo.update_row(after, expected=before)
            sync_dossier_after_personnel_save(self._settings, before, after)
            ctx = _audit_mois_context("update", after.id, self._settings)
            from sme_gend.services.auth_context import resolve_audit_actor

            from sme_gend.services.auth_context import resolve_write_actor
            eb = resolve_write_actor(edited_by)
            audit_log.append_personnel_audit(
                action="update",
                personnel_id=_audit_personnel_id(after),
                changes=changes,
                snapshot_before=audit_log.row_to_audit_dict(before),
                snapshot_after=audit_log.row_to_audit_dict(after),
                sme_mois_context=ctx,
                edited_by=eb,
                edit_motif=(edit_motif or "").strip() or None,
            )
            if sensitive:
                from sme_gend.services.sensitive_audit_log import log_personnel_sensitive_update

                log_personnel_sensitive_update(before, after, sensitive)
            if "date_naissance" in changes:
                from sme_gend.services.sensitive_audit_log import log_personnel_age_update

                log_personnel_age_update(before, after, changes["date_naissance"])

    def create_personnel(
        self,
        row: ListeGeneraleRow,
        *,
        edited_by: str | None = None,
    ) -> str:
        """Insère une nouvelle fiche ; retourne l'id attribué par la base."""
        from sme_gend.services.sensitive_data_policy import assert_may_edit_personnel
        assert_may_edit_personnel(action="cr?er une fiche personnel")
        empty_before = ListeGeneraleRow(
            id="0",
            noms=None,
            date_naissance=None,
            lieu_naissance=None,
            grade=None,
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin=None,
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure=None,
            obs=None,
            ordre_tri=None,
            presseance=None,
        )
        from sme_gend.services.sensitive_data_policy import (
            assert_may_edit_personnel_sensitive_fields,
        )

        assert_may_edit_personnel_sensitive_fields(empty_before, row)
        self._assert_fonction_si_changee(empty_before, row)
        with _personnel_write(self._settings):
            new_id = self._repo.insert_row(row)
            ctx = _audit_mois_context("create", new_id, self._settings)
            from sme_gend.services.auth_context import resolve_audit_actor

            from sme_gend.services.auth_context import resolve_write_actor
            eb = resolve_write_actor(edited_by)
            new_row_with_id = self._repo.fetch_by_id(new_id)
            if new_row_with_id is None:
                raise ValueError("Nouvelle fiche introuvable après création : opération annulée.")
            sync_dossier_after_personnel_save(self._settings, empty_before, new_row_with_id)
            audit_log.append_personnel_audit(
                action="create",
                personnel_id=_audit_personnel_id(new_row_with_id),
                changes=audit_log.diff_personnel_rows(empty_before, new_row_with_id),
                snapshot_before={},
                snapshot_after=audit_log.row_to_audit_dict(new_row_with_id),
                sme_mois_context=ctx,
                edited_by=eb,
                edit_motif=None,
            )
            if new_row_with_id.date_naissance:
                from sme_gend.services.sensitive_audit_log import log_personnel_age_update

                log_personnel_age_update(
                    empty_before,
                    new_row_with_id,
                    {"old": None, "new": audit_log.row_to_audit_dict(new_row_with_id).get("date_naissance")},
                )
        return new_id

    def delete_personnel(
        self,
        row: ListeGeneraleRow,
        *,
        edited_by: str | None = None,
        motif: str | None = None,
    ) -> None:
        """Sortie du mois SME : perte G&P, segment carrière clôturé, audit (restauration possible)."""
        loss_reason = (motif or "").strip()
        if not loss_reason:
            raise ValueError(
                "Le motif de sortie (décès, retraite, etc.) est obligatoire pour supprimer une fiche."
            )
        from sme_gend.config.liste_generale_scope import resolve_mutation_view_mois

        ym = resolve_mutation_view_mois(self._settings)
        with _personnel_write(self._settings):
            delete_with_perte = getattr(self._repo, "delete_row_with_perte", None)
            if callable(delete_with_perte):
                delete_with_perte(row, mois_ym=ym, motif=loss_reason)
            else:
                self._repo.delete_row(row.id)
            ctx = _audit_mois_context("delete", row.id, self._settings)
            from sme_gend.services.auth_context import resolve_audit_actor

            from sme_gend.services.auth_context import resolve_write_actor
            eb = resolve_write_actor(edited_by)
            audit_log.append_personnel_audit(
                action="delete",
                personnel_id=_audit_personnel_id(row),
                changes={},
                snapshot_before=audit_log.row_to_audit_dict(row),
                snapshot_after={},
                sme_mois_context=ctx,
                edited_by=eb,
                edit_motif=(motif or "").strip() or None,
            )

    def restore_deleted_personnel(
        self,
        entry_row: ListeGeneraleRow,
        *,
        deleted_career_id: str,
        edited_by: str | None = None,
    ) -> str:
        """Restaure une fiche à partir du snapshot d'audit (nouvel id carrière)."""
        from sme_gend.services.personnel_deleted_service import (
            DeletedPersonnelStatus,
            assess_deleted_restore,
        )

        cid = (deleted_career_id or entry_row.id or "").strip()
        st, detail = assess_deleted_restore(self._repo, entry_row, cid)
        if st == DeletedPersonnelStatus.ALREADY_PRESENT:
            raise ValueError(detail or "Cette personne est déjà en liste.")
        if st == DeletedPersonnelStatus.NO_SNAPSHOT:
            raise ValueError(detail or "Snapshot d'audit incomplet.")
        if st == DeletedPersonnelStatus.MATRICULE_CONFLICT:
            raise ValueError(
                detail
                or "Conflit de matricule — modifiez les matricules lors de la restauration."
            )
        if st != DeletedPersonnelStatus.RESTORABLE:
            raise ValueError(detail or "Restauration impossible pour cette fiche.")
        from sme_gend.services.fonction_occupancy import assert_fonction_disponible

        assert_fonction_disponible(
            self.liste_complete(),
            structure=entry_row.structure,
            fonction=entry_row.fonction,
            subdivision=entry_row.subdivision_arme,
            exclude_row_id=entry_row.id,
        )
        with _personnel_write(self._settings):
            new_id = self._repo.restore_deleted_row(entry_row, deleted_career_id=cid)
            restored = self._repo.fetch_by_id(new_id)
            if restored is None:
                raise RuntimeError("Restauration effectuée mais fiche introuvable après insertion.")
            empty = ListeGeneraleRow(
                id="0",
                noms=None,
                date_naissance=None,
                lieu_naissance=None,
                grade=None,
                date_grade=None,
                date_service=None,
                ndecision=None,
                matricule_militaire=None,
                matricule_solde=None,
                position_admin=None,
                fonction=None,
                subdivision_arme=None,
                diplome=None,
                date_obtention=None,
                structure=None,
                obs=None,
                ordre_tri=None,
                presseance=None,
            )
            sync_dossier_after_personnel_save(self._settings, empty, restored)
            ctx = _audit_mois_context("restore", new_id, self._settings)
            from sme_gend.services.auth_context import resolve_audit_actor

            from sme_gend.services.auth_context import resolve_write_actor
            eb = resolve_write_actor(edited_by)
            audit_log.append_personnel_audit(
                action="restore",
                personnel_id=_audit_personnel_id(restored),
                changes={"restored_from_career_id": {"old": cid, "new": new_id}},
                snapshot_before={},
                snapshot_after=audit_log.row_to_audit_dict(restored),
                sme_mois_context=ctx,
                edited_by=eb,
                edit_motif=f"Restauration depuis suppression (carrière id={cid})",
            )
        return new_id

    def count_by_structure_code(self) -> dict[str, int]:
        """Effectifs par code structure (liste générale brute)."""
        raw = self._repo.fetch_all()
        counts: dict[str, int] = {}
        for r in raw:
            c = (r.structure or "").strip()
            key = c if c else "(Sans structure)"
            counts[key] = counts.get(key, 0) + 1
        return counts
