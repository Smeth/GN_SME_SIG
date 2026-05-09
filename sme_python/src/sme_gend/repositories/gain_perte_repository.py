"""Factory : choisit Access ou SQLite selon les settings."""

from __future__ import annotations

from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow


def _ym_to_cdsme(ym: str) -> str | None:
    """'2026-01' → '012026' (format Cdsme Access)."""
    parts = ym.strip().split("-")
    if len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[1].zfill(2)}{parts[0]}"
    return None


class GainPerteRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._impl = self._make_impl(settings)

    @staticmethod
    def _make_impl(settings: Settings):  # type: ignore[return]
        if settings.use_sqlite_liste_generale and settings.sqlite_liste_path:
            from sme_gend.repositories.gain_perte_sqlite_repository import SqliteGainPerteRepository
            return SqliteGainPerteRepository(Path(settings.sqlite_liste_path).expanduser().resolve())
        from sme_gend.repositories.gain_perte_access_repository import AccessGainPerteRepository
        return AccessGainPerteRepository(settings)

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
