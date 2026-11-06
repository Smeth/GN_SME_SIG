"""Structures : MySQL (données opérationnelles)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.structure import StructureRow
__all__ = ["StructureRepository"]


class StructureRepository:
    def __init__(self, settings: Settings) -> None:
        from sme_gend.repositories.mysql_bundle_repositories import MySQLStructureRepository

        self._impl = MySQLStructureRepository(settings)

    def fetch_all(self) -> list[StructureRow]:
        return self._impl.fetch_all()

    def fetch_by_code(self, code: str) -> StructureRow | None:
        return self._impl.fetch_by_code(code)

    def insert(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
    ) -> None:
        return self._impl.insert(code, designation, ordre_tri, famille_unite=famille_unite)

    def update(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
        expected=None,
    ) -> None:
        return self._impl.update(code, designation, ordre_tri, famille_unite=famille_unite, expected=expected)

    def delete(self, code: str, *, expected=None) -> None:
        return self._impl.delete(code, expected=expected)

    def rename_code(self, old_code: str, new_code: str, *, expected=None) -> None:
        return self._impl.rename_code(old_code, new_code, expected=expected)
