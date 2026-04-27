"""TabSme : Access ou SQLite selon ``Settings.use_sqlite_liste_generale``."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.sme_access_repository import AccessSmeRepository
from sme_gend.repositories.sme_sqlite_repository import SqliteSmeRepository

__all__ = ["SmeRepository", "AccessSmeRepository", "SqliteSmeRepository"]


class SmeRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessSmeRepository | SqliteSmeRepository = SqliteSmeRepository(
                settings
            )
        else:
            self._impl = AccessSmeRepository(settings)

    def table_exists(self) -> bool:
        return self._impl.table_exists()

    def fetch_all(self) -> list[SmeRow]:
        return self._impl.fetch_all()

    def insert(self, codsme: str, mois: str, statut: str) -> None:
        return self._impl.insert(codsme, mois, statut)

    def update(self, codsme: str, mois: str, statut: str) -> None:
        return self._impl.update(codsme, mois, statut)

    def delete(self, codsme: str) -> None:
        return self._impl.delete(codsme)
