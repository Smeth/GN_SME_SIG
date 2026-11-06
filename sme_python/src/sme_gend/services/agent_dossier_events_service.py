"""Événements RH datés d'un agent (dossier personnel)."""

from __future__ import annotations

from datetime import date

from sme_gend.services.dossier_write import atomic_dossier_write

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import (
    AgentCongeRow,
    AgentDesertionRow,
    AgentMaladeRow,
    AgentMissionRow,
    AgentMouvementRow,
    AgentStageRow,
)
from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository


class AgentDossierEventsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLNumeriqueRepository(settings)

    def missions(self, legacy_id: str) -> list[AgentMissionRow]:
        return self._repo.list_missions_for_agent(legacy_id)

    @atomic_dossier_write
    def add_mission(self, legacy_id: str, **kwargs) -> int:
        self._assert_event_add("ajouter une mission")
        return self._repo.add_mission_for_agent(legacy_id, **kwargs)

    @atomic_dossier_write
    def update_mission(self, legacy_id: str, mission_id: int, **kwargs) -> None:
        self._assert_history_mutation("modifier une mission")
        self._repo.update_mission_for_agent(legacy_id, mission_id, **kwargs)

    @atomic_dossier_write
    def delete_mission(self, legacy_id: str, mission_id: int) -> None:
        self._assert_history_mutation("supprimer une mission")
        self._repo.delete_mission_for_agent(legacy_id, mission_id)

    def stages(self, legacy_id: str) -> list[AgentStageRow]:
        return self._repo.list_stages_for_agent(legacy_id)

    @atomic_dossier_write
    def add_stage(self, legacy_id: str, **kwargs) -> int:
        from sme_gend.services.sensitive_data_policy import assert_may_add_dated_event

        assert_may_add_dated_event(action="ajouter un stage")
        return self._repo.add_stage_for_agent(legacy_id, **kwargs)

    @atomic_dossier_write
    def update_stage(self, legacy_id: str, stage_id: int, **kwargs) -> None:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action="modifier un stage")
        self._repo.update_stage_for_agent(legacy_id, stage_id, **kwargs)

    @atomic_dossier_write
    def delete_stage(self, legacy_id: str, stage_id: int) -> None:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action="supprimer un stage")
        self._repo.delete_stage_for_agent(legacy_id, stage_id)

    def malades(self, legacy_id: str) -> list[AgentMaladeRow]:
        return self._repo.list_malades_for_agent(legacy_id)

    def desertions(self, legacy_id: str) -> list[AgentDesertionRow]:
        return self._repo.list_desertions_for_agent(legacy_id)

    @atomic_dossier_write
    def add_desertion(self, legacy_id: str, **kwargs) -> int:
        self._assert_event_add("ajouter une désertion")
        return self._repo.add_desertion_for_agent(legacy_id, **kwargs)

    @atomic_dossier_write
    def update_desertion(self, legacy_id: str, desertion_id: int, **kwargs) -> None:
        self._assert_history_mutation("modifier une désertion")
        self._repo.update_desertion_for_agent(legacy_id, desertion_id, **kwargs)

    @atomic_dossier_write
    def delete_desertion(self, legacy_id: str, desertion_id: int) -> None:
        self._assert_history_mutation("supprimer une désertion")
        self._repo.delete_desertion_for_agent(legacy_id, desertion_id)

    def mouvements(self, legacy_id: str) -> list[AgentMouvementRow]:
        return self._repo.list_mouvements_for_agent(legacy_id)

    def conges(self, legacy_id: str) -> list[AgentCongeRow]:
        return self._repo.list_conges_for_agent(legacy_id)

    @atomic_dossier_write
    def add_conge(self, legacy_id: str, **kwargs) -> None:
        self._assert_event_add("ajouter un congé")
        from sme_gend.repositories.mysql_agent_conge_repository import MySQLAgentCongeRepository

        MySQLAgentCongeRepository(self._settings).upsert_manual(legacy_id, insert_only=True, **kwargs)

    @atomic_dossier_write
    def update_conge(self, legacy_id: str, conge_id: int, **kwargs) -> None:
        self._assert_history_mutation("modifier un congé")
        from sme_gend.repositories.mysql_agent_conge_repository import MySQLAgentCongeRepository

        MySQLAgentCongeRepository(self._settings).update_manual(
            legacy_id, conge_id, **kwargs
        )

    @atomic_dossier_write
    def delete_conge(self, legacy_id: str, conge_id: int) -> None:
        self._assert_history_mutation("supprimer un congé")
        from sme_gend.repositories.mysql_agent_conge_repository import MySQLAgentCongeRepository

        MySQLAgentCongeRepository(self._settings).delete_for_agent(legacy_id, conge_id)

    @staticmethod
    def _assert_event_add(action: str) -> None:
        from sme_gend.services.sensitive_data_policy import assert_may_add_dated_event
        assert_may_add_dated_event(action=action)

    @staticmethod
    def _assert_history_mutation(action: str) -> None:
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history

        assert_may_mutate_dossier_history(action=action)

    @atomic_dossier_write
    def add_malade(
        self,
        legacy_id: str,
        *,
        date_debut: date,
        date_fin: date | None,
        pathologie: str | None,
        observation: str | None,
    ) -> None:
        self._assert_event_add("ajouter une maladie")
        self._repo.add_malade_row(
            legacy_id,
            date_debut=date_debut,
            date_fin=date_fin,
            pathologie=pathologie,
            observation=observation,
        )

    @atomic_dossier_write
    def update_malade(
        self,
        malade_id: int,
        *,
        date_debut: date,
        date_fin: date | None,
        pathologie: str | None,
        observation: str | None,
    ) -> None:
        self._assert_history_mutation("modifier une maladie")
        self._repo.update_malade_row(
            malade_id,
            date_debut=date_debut,
            date_fin=date_fin,
            pathologie=pathologie,
            observation=observation,
        )

    @atomic_dossier_write
    def delete_malade(self, malade_id: int) -> None:
        self._assert_history_mutation("supprimer une maladie")
        self._repo.delete_malade_row(malade_id)
