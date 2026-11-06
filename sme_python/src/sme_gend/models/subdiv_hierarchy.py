"""Arbre hiérarchique U→D→T (ref_subdivision_noeud)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SubdivHierarchyNode:
    """Nœud de l'arbre Secun / Secdeux / Sectrois."""

    node_id: int
    parent_id: int | None
    code: str
    libelle: str
    pri_out: float | None
    level: int  # 1=U, 2=D, 3=T (feuille)
    rsa_links: int = 0
    children: list[SubdivHierarchyNode] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return self.level >= 3

    @property
    def level_label(self) -> str:
        return {1: "Niveau U", 2: "Niveau D", 3: "Niveau T"}.get(self.level, "?")
