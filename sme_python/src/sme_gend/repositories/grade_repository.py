"""Grades : Access ou SQLite selon ``Settings.use_sqlite_liste_generale``."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.repositories.grade_access_repository import AccessGradeRepository
from sme_gend.repositories.grade_sqlite_repository import SqliteGradeRepository

__all__ = ["GradeRepository", "AccessGradeRepository", "SqliteGradeRepository"]


class GradeRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessGradeRepository | SqliteGradeRepository = SqliteGradeRepository(
                settings
            )
        else:
            self._impl = AccessGradeRepository(settings)

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
    ) -> None:
        return self._impl.update(code, libelle, pref_ord, effectif_g)

    def delete(self, code: str) -> None:
        return self._impl.delete(code)
