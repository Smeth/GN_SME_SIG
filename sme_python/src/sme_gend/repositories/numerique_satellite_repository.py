"""Lecture tables satellites situation numérique : Access ou SQLite."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.numerique_satellite import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    StageDetail,
)
from sme_gend.repositories.numerique_satellite_access_repository import (
    AccessNumeriqueSatelliteRepository,
)
from sme_gend.repositories.numerique_satellite_sqlite_repository import (
    SqliteNumeriqueSatelliteRepository,
)

__all__ = [
    "NumeriqueSatelliteRepository",
    "MissionDetail",
    "MaladeDetail",
    "DeserteurDetail",
    "StageDetail",
    "AccessNumeriqueSatelliteRepository",
    "SqliteNumeriqueSatelliteRepository",
]


class NumeriqueSatelliteRepository:
    """Données satellites jointes sur Id Access (``ListeGenerale.Id`` / ``id_acces``)."""

    def __init__(self, settings: Settings) -> None:
        if settings.use_sqlite_liste_generale:
            self._impl: AccessNumeriqueSatelliteRepository | SqliteNumeriqueSatelliteRepository
            self._impl = SqliteNumeriqueSatelliteRepository(settings)
        else:
            self._impl = AccessNumeriqueSatelliteRepository(settings)

    def load_missions(self) -> dict[str, MissionDetail]:
        return self._impl.load_missions()

    def load_malades(self) -> dict[str, MaladeDetail]:
        return self._impl.load_malades()

    def load_deserteurs(self) -> dict[str, DeserteurDetail]:
        return self._impl.load_deserteurs()

    def load_stages_loc(self) -> dict[str, StageDetail]:
        return self._impl.load_stages_loc()

    def load_stages_ex(self) -> dict[str, StageDetail]:
        return self._impl.load_stages_ex()

    def upsert_malade(self, person_id: str, detail: MaladeDetail) -> None:
        self._impl.upsert_malade(person_id, detail)

    def delete_malade(self, person_id: str) -> None:
        self._impl.delete_malade(person_id)
