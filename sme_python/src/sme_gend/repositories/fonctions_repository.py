"""Fonctions distinctes : Access ou SQLite."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.fonctions_access_repository import AccessFonctionsRepository
from sme_gend.repositories.fonctions_sqlite_repository import SqliteFonctionsRepository

__all__ = [
    "FonctionsRepository",
    "AccessFonctionsRepository",
    "SqliteFonctionsRepository",
]


class FonctionsRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessFonctionsRepository | SqliteFonctionsRepository = (
                SqliteFonctionsRepository(settings)
            )
        else:
            self._impl = AccessFonctionsRepository(settings)

    def fetch_distinct(self) -> list[str]:
        return self._impl.fetch_distinct()
