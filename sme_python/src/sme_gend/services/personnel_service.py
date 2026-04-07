"""Règles métier — liste générale (couche au-dessus des repositories)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository


class PersonnelService:
    """Service principal : accès lecture à la liste du personnel."""

    def __init__(self, settings: Settings) -> None:
        self._repo = ListeGeneraleRepository(settings)

    def liste_complete(self) -> list[ListeGeneraleRow]:
        return self._repo.fetch_all()

    def liste_presents(self) -> list[ListeGeneraleRow]:
        """Personnes en position « présent » (liste des présents — base)."""
        return self._repo.fetch_presents()
