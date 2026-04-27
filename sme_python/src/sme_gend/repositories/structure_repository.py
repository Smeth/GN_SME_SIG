"""Structures : Access ou SQLite selon ``Settings.use_sqlite_liste_generale``."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.structure_access_repository import AccessStructureRepository
from sme_gend.repositories.structure_sqlite_repository import SqliteStructureRepository

__all__ = ["StructureRepository", "AccessStructureRepository", "SqliteStructureRepository"]


class StructureRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessStructureRepository | SqliteStructureRepository = (
                SqliteStructureRepository(settings)
            )
        else:
            self._impl = AccessStructureRepository(settings)

    def fetch_all(self) -> list[StructureRow]:
        return self._impl.fetch_all()

    def fetch_by_code(self, code: str) -> StructureRow | None:
        return self._impl.fetch_by_code(code)

    def insert(self, code: str, designation: str, ordre_tri: int | None) -> None:
        return self._impl.insert(code, designation, ordre_tri)

    def update(self, code: str, designation: str, ordre_tri: int | None) -> None:
        return self._impl.update(code, designation, ordre_tri)

    def delete(self, code: str) -> None:
        return self._impl.delete(code)

    def rename_code(self, old_code: str, new_code: str) -> None:
        return self._impl.rename_code(old_code, new_code)
