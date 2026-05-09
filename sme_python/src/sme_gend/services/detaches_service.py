"""Détachés — filtrage ListeGenerale (position = Détaché), regroupement par subdivision / sigle."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.numerique_padmin import PADMIN_DETACHE


def is_detache_row(row: ListeGeneraleRow) -> bool:
    return (row.position_admin or "").strip() == PADMIN_DETACHE


def sigle_ou_subdivision(row: ListeGeneraleRow) -> str:
    """Sigle / initiales structure : colonne Access « subdivision arme »."""
    s = (row.subdivision_arme or "").strip()
    return s if s else "(Non renseigné)"


def designation_detachement(
    row: ListeGeneraleRow,
    lookup: dict[str, str],
) -> str:
    """Résout le sigle SubArme dans la hiérarchie TabSec* et retourne la désignation complète.

    lookup = dict code → désignation construit via _build_sec_lookup().
    Si non trouvé, retourne le sigle brut.
    """
    sigle = (row.subdivision_arme or "").strip()
    if not sigle:
        return "(Non renseigné)"
    return lookup.get(sigle, sigle)


def recap_par_sigle(rows: Sequence[ListeGeneraleRow]) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for r in rows:
        c[sigle_ou_subdivision(r)] += 1
    return sorted(c.items(), key=lambda x: (-x[1], x[0].lower()))


class DetachesService:
    """Accès aux lignes « Détaché » et synthèses."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)

    def fetch_detaches(self) -> list[ListeGeneraleRow]:
        return [r for r in self._repo.fetch_all() if is_detache_row(r)]

    def distinct_sigles_with_counts(self) -> list[tuple[str, int]]:
        """
        Sigles distincts (subdivision arme, normalisé majuscules) parmi les Détaché,
        avec effectif par sigle. Les vides sont regroupés sous « (NON RENSEIGNÉ) ».
        """
        c: Counter[str] = Counter()
        for r in self.fetch_detaches():
            s = (r.subdivision_arme or "").strip().upper()
            if not s:
                s = "(NON RENSEIGNÉ)"
            c[s] += 1
        return sorted(c.items(), key=lambda x: (-x[1], x[0]))
