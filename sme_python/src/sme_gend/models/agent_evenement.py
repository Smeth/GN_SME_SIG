"""Événements datés rattachés à un agent (dossier personnel / numérique)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentMissionRow:
    date_debut: str | None
    date_fin: str | None
    nature: str | None
    lieu: str | None
    reference_dossier: str | None
    periode_sme: str | None
    id: int | None = None
    observation: str | None = None


@dataclass(slots=True)
class AgentStageRow:
    type_stage: str
    date_debut: str | None
    date_fin: str | None
    nature: str | None
    lieu: str | None
    observation: str | None
    periode_sme: str | None
    id: int | None = None
    reference_dossier: str | None = None
    session: str | None = None


@dataclass(slots=True)
class AgentMaladeRow:
    date_debut: str | None
    date_fin: str | None
    pathologie: str | None
    observation: str | None
    periode_sme: str | None
    malade_id: int | None = None
    """Identifiant ``agent_malade.id`` — requis pour modifier/supprimer la fiche."""

    saisie_manuelle: bool = False
    """Vrai si la fiche a été saisie dans l'application (résiste au réimport du mois)."""


@dataclass(slots=True)
class AgentDesertionRow:
    date_desertion: str | None
    periode_sme: str | None
    id: int | None = None
    observation: str | None = None
    reference_dossier: str | None = None


@dataclass(slots=True)
class AgentMouvementRow:
    periode_sme: str | None
    categorie: str | None
    motif: str | None
    grade: str | None
    nom: str | None
    date_reference: str | None


@dataclass(slots=True)
class AgentCongeRow:
    type_conge: str
    type_label: str
    date_debut: str | None
    date_fin: str | None
    quota_jours: str | None
    observation: str | None
    periode_sme: str | None
    source_code: str | None
    id: int | None = None


@dataclass(slots=True)
class CarriereSnapshotRow:
    """Instantané liste générale / carriere_situation pour une période SME."""

    annee_mois: str
    date_debut: str | None
    date_fin: str | None
    grade: str | None
    date_grade: str | None
    structure: str | None
    position_admin: str | None
    fonction: str | None
    subdivision_arme: str | None
    date_service: str | None
    ndecision: str | None
    diplome: str | None
    date_obtention: str | None = None
    est_actuelle: bool = False
