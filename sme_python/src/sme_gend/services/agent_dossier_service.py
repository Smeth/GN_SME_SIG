"""Diplômes et passations de grade — logique métier dossier agent.

Règle SoT (passations / diplômes) : ``agent_diplome`` et ``agent_passation_grade``
sont maîtres ; ``carriere_situation`` est une projection resynchronisée après
chaque mutation dossier (add / update / delete).
"""

from __future__ import annotations

from datetime import date

from sme_gend.services.dossier_write import atomic_dossier_write

from sme_gend.config.settings import Settings
from sme_gend.models.agent_dossier import AgentAffectationRow, AgentDiplomeRow, AgentPassationGradeRow
from sme_gend.models.distinction_ref import AgentDistinctionRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository


class AgentDossierService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLAgentDossierRepository(settings)

    def _log_history(self, action: str, row: ListeGeneraleRow, detail: str, **extra) -> None:
        from sme_gend.services.sensitive_audit_log import log_dossier_history_action
        from sme_gend.services.dossier_write import history_audit_context
        extra = {**(history_audit_context.get() or {}), **extra}

        log_dossier_history_action(
            action=action,
            row=row,
            detail=detail,
            extra=extra or None,
        )

    def _project_carriere(self, row: ListeGeneraleRow, *, component: str, dates=()) -> None:
        aid = self.agent_id_for_row(row)
        if dates:
            self._repo.apply_dated_grade(aid, row, min(dates), carriere_id=row.id, protected_dates=dates,component=component)
        else:
            self._repo.sync_carriere_from_dossier(aid, row.id, components=frozenset({component}))
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._settings)

    def agent_id_for_row(self, row: ListeGeneraleRow) -> int:
        return self._repo.resolve_agent_id(
            carriere_id=row.id,
            legacy_id=row.satellite_join_key(),
        )

    def list_diplomes(self, row: ListeGeneraleRow) -> list[AgentDiplomeRow]:
        aid = self.agent_id_for_row(row)
        return self._repo.list_diplomes(aid)

    def read_bundle(self, row: ListeGeneraleRow) -> dict:
        """Resolve the owner once for the four independent history reads."""
        aid = self.agent_id_for_row(row)
        return {name: getattr(self._repo, name)(aid) for name in
                ('list_diplomes', 'list_distinctions', 'list_passations', 'list_affectations')}

    @atomic_dossier_write
    def add_diplome(
        self,
        row: ListeGeneraleRow,
        *,
        categorie: str,
        libelle: str,
        date_obtention: date | None = None,
        lieu: str | None = None,
        mention: str | None = None,
        etablissement: str | None = None,
        obs: str | None = None,
    ) -> int:
        from sme_gend.services.sensitive_data_policy import assert_may_add_diplome

        assert_may_add_diplome()
        aid = self.agent_id_for_row(row)
        pid = self._repo.insert_diplome(
            aid,
            categorie=categorie,
            libelle=libelle,
            date_obtention=date_obtention,
            lieu=lieu,
            mention=mention,
            etablissement=etablissement,
            obs=obs,
        )
        self._project_carriere(row, component="diplome",dates=(date_obtention,) if date_obtention else ())
        self._log_history(
            "dossier_diplome_add",
            row,
            f"{categorie} — {libelle}",
            diplome_id=pid,
        )
        return pid

    @atomic_dossier_write
    def update_diplome(
        self,
        row: ListeGeneraleRow,
        diplome_id: int,
        **kwargs,
    ) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="modifier un diplôme")
        existing = next((d for d in self.list_diplomes(row) if d.id == diplome_id), None)
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="modifier un diplôme importé", obs=existing.obs
            )
        self._repo.update_diplome(diplome_id, **kwargs)
        self._project_carriere(row, component="diplome",dates=tuple(d for d in (existing.date_obtention,kwargs.get('date_obtention')) if d))
        lib = str(kwargs.get("libelle") or (existing.libelle if existing else "") or diplome_id)
        self._log_history("dossier_diplome_update", row, lib, diplome_id=diplome_id)

    @atomic_dossier_write
    def delete_diplome(self, row: ListeGeneraleRow, diplome_id: int) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="supprimer un diplôme")
        existing = next((d for d in self.list_diplomes(row) if d.id == diplome_id), None)
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="supprimer un diplôme importé", obs=existing.obs
            )
        self._repo.delete_diplome(diplome_id)
        self._project_carriere(row, component="diplome",dates=(existing.date_obtention,) if existing.date_obtention else ())
        lib = (existing.libelle if existing else str(diplome_id)) or str(diplome_id)
        self._log_history("dossier_diplome_delete", row, lib, diplome_id=diplome_id)

    def list_passations(self, row: ListeGeneraleRow) -> list[AgentPassationGradeRow]:
        aid = self.agent_id_for_row(row)
        return self._repo.list_passations(aid)

    @atomic_dossier_write
    def add_passation(
        self,
        row: ListeGeneraleRow,
        *,
        grade_libelle: str,
        date_passation: date | None = None,
        ndecision: str | None = None,
        obs: str | None = None,
        sync_fiche: bool = True,
    ) -> int:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action="ajouter une passation de grade")
        aid = self.agent_id_for_row(row)
        pid = self._repo.insert_passation(
            aid,
            grade_libelle=grade_libelle,
            date_passation=date_passation,
            ndecision=ndecision,
            obs=obs,
        )
        if sync_fiche:
            self._project_carriere(row, component="grade", dates=(date_passation,) if date_passation else ())
        self._log_history(
            "dossier_passation_add",
            row,
            grade_libelle,
            passation_id=pid,
        )
        return pid

    @atomic_dossier_write
    def update_passation(
        self,
        row: ListeGeneraleRow,
        passation_id: int,
        **kwargs,
    ) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="modifier une passation de grade")
        existing = next(
            (p for p in self.list_passations(row) if p.id == passation_id), None
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="modifier une passation importée", obs=existing.obs
            )
        sync = kwargs.pop("sync_fiche", True)
        self._repo.update_passation(passation_id, **kwargs)
        if sync:
            self._project_carriere(row, component="grade", dates=tuple(d for d in (existing.date_passation, kwargs.get('date_passation')) if d))
        gl = str(kwargs.get("grade_libelle") or (existing.grade_libelle if existing else "") or passation_id)
        self._log_history("dossier_passation_update", row, gl, passation_id=passation_id)

    @atomic_dossier_write
    def delete_passation(self, row: ListeGeneraleRow, passation_id: int) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="supprimer une passation de grade")
        existing = next(
            (p for p in self.list_passations(row) if p.id == passation_id), None
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="supprimer une passation importée", obs=existing.obs
            )
        self._repo.delete_passation(passation_id)
        self._project_carriere(row, component="grade", dates=(existing.date_passation,) if existing.date_passation else ())
        gl = (existing.grade_libelle if existing else str(passation_id)) or str(passation_id)
        self._log_history("dossier_passation_delete", row, gl, passation_id=passation_id)

    def list_affectations(self, row: ListeGeneraleRow) -> list[AgentAffectationRow]:
        aid = self.agent_id_for_row(row)
        return self._repo.list_affectations(aid)

    @atomic_dossier_write
    def delete_affectation(self, row: ListeGeneraleRow, affectation_id: int) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="supprimer une affectation")
        existing = next(
            (a for a in self.list_affectations(row) if a.id == affectation_id), None
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="supprimer une affectation importée", obs=existing.obs
            )
        self._repo.delete_affectation(affectation_id)
        self._project_carriere(row, component="affectation",dates=(existing.date_affectation,) if existing.date_affectation else ())
        sc = (existing.structure_code if existing else str(affectation_id)) or str(affectation_id)
        self._log_history("dossier_affectation_delete", row, sc, affectation_id=affectation_id)

    @atomic_dossier_write
    def add_affectation(
        self,
        row: ListeGeneraleRow,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = None,
    ) -> int:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action="ajouter une affectation")
        aid = self.agent_id_for_row(row)
        aff_id = self._repo.insert_affectation(
            aid,
            structure_code=structure_code,
            date_affectation=date_affectation,
            fonction=fonction,
            subdivision_arme=subdivision_arme,
            sme_periode_mois=sme_periode_mois,
            obs=obs,
        )
        self._project_carriere(row, component="affectation",dates=(date_affectation,))
        self._log_history(
            "dossier_affectation_add",
            row,
            structure_code,
            affectation_id=aff_id,
        )
        return aff_id

    @atomic_dossier_write
    def update_affectation(
        self,
        row: ListeGeneraleRow,
        affectation_id: int,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = None,
    ) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="modifier une affectation")
        existing = next(
            (a for a in self.list_affectations(row) if a.id == affectation_id), None
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="modifier une affectation importée", obs=existing.obs
            )
        self._repo.update_affectation(
            int(affectation_id),
            structure_code=structure_code,
            date_affectation=date_affectation,
            fonction=fonction,
            subdivision_arme=subdivision_arme,
            sme_periode_mois=sme_periode_mois,
            obs=obs,
        )
        self._project_carriere(row, component="affectation",dates=tuple(d for d in (existing.date_affectation,date_affectation) if d))
        sc = structure_code or (existing.structure_code if existing else str(affectation_id))
        self._log_history("dossier_affectation_update", row, sc, affectation_id=affectation_id)

    def list_distinctions(self, row: ListeGeneraleRow) -> list[AgentDistinctionRow]:
        aid = self.agent_id_for_row(row)
        return self._repo.list_distinctions(aid)

    @atomic_dossier_write
    def add_distinction(
        self,
        row: ListeGeneraleRow,
        *,
        kind: str,
        ref_medaille_id: int | None = None,
        ref_decoration_id: int | None = None,
        date_attribution: date | None = None,
        date_publication_journal: date | None = None,
        numero_arrete: str | None = None,
        date_arrete: date | None = None,
        citation: str | None = None,
        source: str | None = None,
    ) -> int:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action="ajouter une médaille ou décoration")
        aid = self.agent_id_for_row(row)
        did = self._repo.insert_distinction(
            aid,
            kind=kind,
            ref_medaille_id=ref_medaille_id,
            ref_decoration_id=ref_decoration_id,
            date_attribution=date_attribution,
            date_publication_journal=date_publication_journal,
            numero_arrete=numero_arrete,
            date_arrete=date_arrete,
            citation=citation,
            source=source or "saisie_dossier",
        )
        self._log_history(
            "dossier_distinction_add",
            row,
            f"{kind} #{did}",
            distinction_id=did,
        )
        return did

    @atomic_dossier_write
    def update_distinction(
        self,
        row: ListeGeneraleRow,
        distinction_id: int,
        **kwargs,
    ) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="modifier une médaille ou décoration")
        existing = next(
            (d for d in self.list_distinctions(row) if d.id == distinction_id),
            None,
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="modifier une distinction importée", obs=existing.source
            )
        self._repo.update_distinction(int(distinction_id), **kwargs)
        lib = existing.libelle if existing else str(distinction_id)
        self._log_history(
            "dossier_distinction_update",
            row,
            lib,
            distinction_id=distinction_id,
        )

    @atomic_dossier_write
    def delete_distinction(self, row: ListeGeneraleRow, distinction_id: int) -> None:
        from sme_gend.services.sensitive_data_policy import (
            assert_may_mutate_dossier_history,
            assert_may_mutate_imported_dossier_entry,
        )

        assert_may_mutate_dossier_history(action="supprimer une médaille ou décoration")
        existing = next(
            (d for d in self.list_distinctions(row) if d.id == distinction_id),
            None,
        )
        if existing is None:
            raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        if existing is not None:
            assert_may_mutate_imported_dossier_entry(
                action="supprimer une distinction importée", obs=existing.source
            )
        self._repo.delete_distinction(int(distinction_id))
        lib = existing.libelle if existing else str(distinction_id)
        self._log_history(
            "dossier_distinction_delete",
            row,
            lib,
            distinction_id=distinction_id,
        )
