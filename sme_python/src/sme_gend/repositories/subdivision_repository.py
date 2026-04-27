"""Hiérarchie subdivisions : Access ou SQLite."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.repositories.subdivision_access_repository import AccessSubdivisionRepository
from sme_gend.repositories.subdivision_sqlite_repository import SqliteSubdivisionRepository

__all__ = [
    "SubdivisionRepository",
    "SubdivisionFlatRow",
    "AccessSubdivisionRepository",
    "SqliteSubdivisionRepository",
]


class SubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessSubdivisionRepository | SqliteSubdivisionRepository = (
                SqliteSubdivisionRepository(settings)
            )
        else:
            self._impl = AccessSubdivisionRepository(settings)

    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        return self._impl.fetch_flat_hierarchy()
