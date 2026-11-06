"""Liste générale : MySQL (données opérationnelles)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_access_repository import (
    AccessListeGeneraleRepository,
    SELECT_LISTE_GENERALE,
)
from sme_gend.repositories.mysql_agent_repository import (
    MySQLAgentRepository,
)

__all__ = [
    "ListeGeneraleRepository",
    "AccessListeGeneraleRepository",
    "MySQLAgentRepository",
    "SELECT_LISTE_GENERALE",
]


class ListeGeneraleRepository:
    """Délègue à MySQL."""

    def __init__(self, settings: Settings) -> None:
        self._impl = MySQLAgentRepository(settings)

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        return self._impl.fetch_by_id(row_id)

    def fetch_all(self) -> list[ListeGeneraleRow]:
        return self._impl.fetch_all()

    def fetch_all_at_mois(self, mois_ym: str) -> list[ListeGeneraleRow]:
        return self._impl.fetch_all_at_mois(mois_ym)

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        return self._impl.fetch_presents()

    def fetch_for_presence_mode(self, mode: str = "present", *, mois_ym: str | None = None) -> list[ListeGeneraleRow]:
        return self._impl.fetch_for_presence_mode(mode, **({'mois_ym': mois_ym} if mois_ym is not None else {}))

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

    def delete_row_with_perte(
        self,
        row: ListeGeneraleRow,
        *,
        mois_ym: str,
        motif: str,
    ) -> None:
        return self._impl.delete_row_with_perte(
            row,
            mois_ym=mois_ym,
            motif=motif,
        )

    def restore_deleted_row(self, row: ListeGeneraleRow, *, deleted_career_id: str) -> str:
        return self._impl.restore_deleted_row(row, deleted_career_id=deleted_career_id)

    def update_row(self, row: ListeGeneraleRow, *, expected: ListeGeneraleRow | None = None) -> None:
        if expected is not None:
            return self._impl.update_row(row, expected=expected)
        return self._impl.update_row(row)

    def update_structure_column(self, old_code: str, new_code: str) -> int:
        return self._impl.update_structure_column(old_code, new_code)

    def backfill_structure_id_for_code(self, structure_code: str) -> int:
        return self._impl.backfill_structure_id_for_code(structure_code)

    def rename_subarme_for_structure(
        self, structure_code: str, old_label: str, new_label: str, *, mois_ym: str | None = None
    ) -> int:
        return self._impl.rename_subarme_for_structure(
            structure_code, old_label, new_label, **({'mois_ym': mois_ym} if mois_ym is not None else {})
        )
