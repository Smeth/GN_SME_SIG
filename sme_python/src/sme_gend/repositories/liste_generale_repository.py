"""Accès lecture à `ListeGenerale`."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.access_connection import AccessConnection

# Ordre des colonnes = ordre du SELECT (évite les ambiguïtés de noms Access).
SELECT_LISTE_GENERALE = """
SELECT
    Id,
    noms,
    DateN,
    LieuN,
    Grade,
    DateGrade,
    DateService,
    Ndecision,
    Matmilitaire,
    Matsolde,
    Padmin,
    Fonction,
    SubArme,
    Diplome,
    DateObtention,
    Structure,
    Obs,
    OderTri,
    presseance
FROM ListeGenerale
"""


class ListeGeneraleRepository:
    """Repository : lecture seule pour la liste générale."""

    def __init__(self, settings: Settings) -> None:
        self._conn_factory = AccessConnection(settings)

    def fetch_all(self) -> list[ListeGeneraleRow]:
        """Toutes les lignes (tri par ordre métier si défini)."""
        with self._conn_factory.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"{SELECT_LISTE_GENERALE} ORDER BY OderTri, Id")
            return [ListeGeneraleRow.from_sequence(r) for r in cursor.fetchall()]

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        """Personnes dont la position administrative est « présent » (tolérance accents)."""
        rows = self.fetch_all()
        return [r for r in rows if _is_present(r.position_admin)]


def _is_present(padmin: str | None) -> bool:
    if not padmin:
        return False
    return "present" in padmin.strip().lower().replace("é", "e").replace("è", "e")
