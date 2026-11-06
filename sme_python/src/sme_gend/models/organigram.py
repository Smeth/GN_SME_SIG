"""Modèles pour l'organigramme structures / subdivisions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SubdivisionOrganigramNode:
    """Subdivision rattachée à une structure (ref_subdivision_arme ou donnée seule)."""

    rsa_id: int | None
    libelle: str
    ordre_tri: int | None
    agent_count: int = 0
    from_referentiel: bool = True
    hierarchy_hint: str | None = None


@dataclass(slots=True)
class StructureOrganigramNode:
    """Structure avec ses subdivisions et effectifs."""

    structure_id: int
    code: str
    designation: str
    ordre_tri: int | None
    agent_count: int = 0
    subdivisions: list[SubdivisionOrganigramNode] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReferentielUsage:
    """Comptages d'utilisation d'une entrée référentiel."""

    by_fk: int = 0
    by_text: int = 0

    @property
    def total(self) -> int:
        return self.by_fk + self.by_text

