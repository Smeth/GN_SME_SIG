"""Couples structure / subdivision d'arme : MySQL."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.structure_subdivision_access_repository import (
    AccessStructureSubdivisionRepository,
)

__all__ = ["StructureSubdivisionRepository", "AccessStructureSubdivisionRepository"]


class StructureSubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        from sme_gend.repositories.mysql_bundle_repositories import (
            MySQLStructureSubdivisionRepository,
        )

        self._impl = MySQLStructureSubdivisionRepository(settings)

    def fetch_distinct_pairs(self) -> list[tuple[str, str]]:
        return self._impl.fetch_distinct_pairs()

    def fetch_subdiv_labels_for_structure(self, structure_code: str) -> list[str]:
        return self._impl.fetch_subdiv_labels_for_structure(structure_code)

    def fetch_rsa_order_for_structure(self, structure_code: str) -> tuple[list[str], list[int]]:
        return self._impl.fetch_rsa_order_for_structure(structure_code)

    def fetch_distinct_subdiv_arme_labels(self) -> list[str]:
        return self._impl.fetch_distinct_subdiv_arme_labels()
