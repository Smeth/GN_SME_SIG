"""Liste générale : Access ou SQLite selon ``Settings.use_sqlite_liste_generale``."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_access_repository import (
    AccessListeGeneraleRepository,
    SELECT_LISTE_GENERALE,
)
from sme_gend.repositories.liste_generale_sqlite_repository import (
    SqliteListeGeneraleRepository,
)

__all__ = [
    "ListeGeneraleRepository",
    "AccessListeGeneraleRepository",
    "SqliteListeGeneraleRepository",
    "SELECT_LISTE_GENERALE",
]


class ListeGeneraleRepository:
    """Délègue à Access ou SQLite."""

    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessListeGeneraleRepository | SqliteListeGeneraleRepository
            self._impl = SqliteListeGeneraleRepository(settings)
        else:
            self._impl = AccessListeGeneraleRepository(settings)

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        return self._impl.fetch_by_id(row_id)

    def fetch_all(self) -> list[ListeGeneraleRow]:
        return self._impl.fetch_all()

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        return self._impl.fetch_presents()

    def fetch_distinct_padmin(self) -> list[str]:
        return self._impl.fetch_distinct_padmin()

    def fetch_distinct_subarme(self) -> list[str]:
        return self._impl.fetch_distinct_subarme()

    def fetch_distinct_subarme_for_structure(self, structure_code: str) -> list[str]:
        return self._impl.fetch_distinct_subarme_for_structure(structure_code)

    def insert_row(self, row: ListeGeneraleRow) -> str:
        return self._impl.insert_row(row)

    def delete_row(self, row_id: str) -> None:
        return self._impl.delete_row(row_id)

    def update_row(self, row: ListeGeneraleRow) -> None:
        return self._impl.update_row(row)

    def update_structure_column(self, old_code: str, new_code: str) -> int:
        return self._impl.update_structure_column(old_code, new_code)

    def rename_subarme_for_structure(
        self, structure_code: str, old_label: str, new_label: str
    ) -> int:
        return self._impl.rename_subarme_for_structure(
            structure_code, old_label, new_label
        )
