"""TabSme : MySQL."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.mysql_bundle_repositories import MySQLSmeRepository

__all__ = ["SmeRepository", "MySQLSmeRepository"]


class SmeRepository:
    def __init__(self, settings: Settings) -> None:
        self._impl = MySQLSmeRepository(settings)

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
