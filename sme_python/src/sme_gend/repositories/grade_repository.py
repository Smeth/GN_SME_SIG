"""Grades : MySQL."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.repositories.mysql_bundle_repositories import MySQLGradeRepository

__all__ = ["GradeRepository", "MySQLGradeRepository"]


class GradeRepository:
    def __init__(self, settings: Settings) -> None:
        self._impl = MySQLGradeRepository(settings)

    def fetch_all(self) -> list[GradeRefRow]:
        return self._impl.fetch_all()

    def insert(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        return self._impl.insert(code, libelle, pref_ord, effectif_g)

    def update(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
        *, expected=None,
    ) -> None:
        return self._impl.update(code, libelle, pref_ord, effectif_g, expected=expected)

    def delete(self, code: str, *, expected=None) -> None:
        return self._impl.delete(code, expected=expected)
