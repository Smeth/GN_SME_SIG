"""Couples structure / subdivision d'arme : Access ou SQLite."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.structure_subdivision_access_repository import (
    AccessStructureSubdivisionRepository,
)
from sme_gend.repositories.structure_subdivision_sqlite_repository import (
    SqliteStructureSubdivisionRepository,
)

__all__ = [
    "StructureSubdivisionRepository",
    "AccessStructureSubdivisionRepository",
    "SqliteStructureSubdivisionRepository",
]


class StructureSubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessStructureSubdivisionRepository | SqliteStructureSubdivisionRepository
            self._impl = SqliteStructureSubdivisionRepository(settings)
        else:
            self._impl = AccessStructureSubdivisionRepository(settings)

    def fetch_distinct_pairs(self) -> list[tuple[str, str]]:
        return self._impl.fetch_distinct_pairs()
