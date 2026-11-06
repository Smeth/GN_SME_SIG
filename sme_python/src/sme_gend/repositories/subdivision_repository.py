"""Hiérarchie subdivisions : MySQL."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
__all__ = ["SubdivisionRepository", "SubdivisionFlatRow"]


class SubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        from sme_gend.repositories.mysql_bundle_repositories import MySQLSubdivisionRepository

        self._impl = MySQLSubdivisionRepository(settings)

    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        return self._impl.fetch_flat_hierarchy()
