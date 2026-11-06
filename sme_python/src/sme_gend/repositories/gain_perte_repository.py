"""Gains et pertes : MySQL."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow


def _ym_to_cdsme(ym: str) -> str | None:
    """'2026-01' → '012026' (format Cdsme Access)."""
    parts = ym.strip().split("-")
    if len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[1].zfill(2)}{parts[0]}"
    return None


def cdsme_from_mois_ym(ym: str) -> str | None:
    """Public pour l’UI : mois ``YYYY-MM`` → code ``Cdsme`` TabGainPerte."""
    return _ym_to_cdsme(ym)


class GainPerteRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._impl = self._make_impl(settings)

    @staticmethod
    def _make_impl(settings: Settings):  # type: ignore[return]
        from sme_gend.repositories.mysql_bundle_repositories import MySQLGainPerteRepository

        return MySQLGainPerteRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._impl = self._make_impl(settings)

    def fetch_all(self) -> list[GainPerteRow]:
        return self._impl.fetch_all()

    def fetch_for_month(self, mois_ym: str) -> list[GainPerteRow]:
        """Charge les lignes pour un mois YYYY-MM (ex. '2026-01')."""
        cdsme = _ym_to_cdsme(mois_ym)
        if not cdsme:
            return []
        return self._impl.fetch_for_cdsme(cdsme)

    def fetch_distinct_months(self) -> list[str]:
        return self._impl.fetch_distinct_months()

    def fetch_month_catalog(self) -> list[str]:
        return self._impl.fetch_month_catalog()

    def insert_row(self, row: GainPerteRow) -> None:
        self._impl.insert_row(row)

    def insert_rows(self, rows: list[GainPerteRow]) -> None:
        """One confirmed batch, with no partial movements or early revision."""
        if not rows:
            return
        from sme_gend.models.gain_perte import periode_ym_for_mouvement
        from sme_gend.services.shared_reference_write import shared_data_write
        months = {periode_ym_for_mouvement(row) for row in rows}
        month = next(iter(months)) if len(months) == 1 else None
        with shared_data_write(self._settings, reason="gain_perte", mois=month):
            for row in rows:
                self._impl.insert_row(row)

    def update_row(
        self,
        row: GainPerteRow,
        *,
        where_id: str,
        where_cdsme: str | None,
        movement_id: int | None = None,
    ) -> None:
        self._impl.update_row(
            row,
            where_id=where_id,
            where_cdsme=where_cdsme,
            movement_id=movement_id,
        )

    def delete_row(
        self,
        row_id: str,
        cdsme: str | None,
        *,
        movement_id: int | None = None,
        expected: GainPerteRow | None = None,
    ) -> None:
        self._impl.delete_row(row_id, cdsme, movement_id=movement_id, expected=expected)

    def delete_rows(self, rows: list[GainPerteRow]) -> None:
        """Supprime une sélection dans une transaction et une notification."""
        if not rows:
            return
        self._impl.delete_rows(rows)
