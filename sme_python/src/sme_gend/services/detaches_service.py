"""Détachés — filtrage ListeGenerale (Padmin « Détaché » ou Structure = DETACHE), regroupement par subdivision / sigle."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sme_gend.config.settings import Settings
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.numerique_padmin import row_predicate_for_rubrique

# Code structure sur ListeGenerale (aligné tableau de bord — insensible à la casse).
STRUCTURE_CODE_DETACHE = "DETACHE"


def _structure_col_is_detachement(structure: str | None) -> bool:
    """True si la colonne Structure vaut le code liste générale ``DETACHE`` (seule valeur utilisée ; casse ignorée)."""
    s = (structure or "").strip()
    if not s:
        return False
    return s.casefold() == STRUCTURE_CODE_DETACHE.casefold()


def is_padmin_detache_row(row: ListeGeneraleRow) -> bool:
    """Position administrative = « Détaché » (insensible à la casse, aligné rubrique numérique)."""
    return row_predicate_for_rubrique("detache")(row)


def is_structure_detache_row(row: ListeGeneraleRow) -> bool:
    """Colonne Structure = code ``DETACHE`` (insensible à la casse)."""
    return _structure_col_is_detachement(row.structure)


def in_detaches_scope(row: ListeGeneraleRow) -> bool:
    """Périmètre liste détachés : Padmin détaché et/ou Structure = DETACHE."""
    return is_padmin_detache_row(row) or is_structure_detache_row(row)


def detache_ecart_remarque(row: ListeGeneraleRow) -> str:
    """Texte court pour export / contrôle : vide si cohérent ; sinon incohérence DETACHE vs Padmin."""
    if not in_detaches_scope(row):
        return ""
    if is_padmin_detache_row(row):
        return ""
    pa = (row.position_admin or "").strip() or "(vide)"
    return f"À vérifier : Structure DETACHE, Padmin « {pa} »"


def is_detache_row(row: ListeGeneraleRow) -> bool:
    """Alias : ligne dans le périmètre « liste détachés » (voir :func:`in_detaches_scope`)."""
    return in_detaches_scope(row)


def _sort_key_detaches(row: ListeGeneraleRow) -> tuple:
    """Écarts Padmin en fin de liste pour faciliter le contrôle."""
    anomalie = 1 if is_structure_detache_row(row) and not is_padmin_detache_row(row) else 0
    return (anomalie, sigle_ou_subdivision(row).lower(), (row.noms or "").lower())


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


def designation_bloc_excel(cfg: DetachesFileConfig, sigle_subdivision: str) -> str:
    """
    Texte des titres de bloc dans la feuille DETACHE pour un sigle : provient de ``detaches_config.json``
    (champ ``titres_lignes``). À rapprocher de la colonne ListeGenerale « subdivision arme ».
    """
    key = (sigle_subdivision or "").strip().upper()
    if not key or key == "(NON RENSEIGNÉ)":
        return ""
    for ent in cfg.structures:
        if ent.sigle == key:
            if ent.titres_lignes:
                return " — ".join(ent.titres_lignes)
            return ent.sigle
    return ""


class DetachesService:
    """Accès aux lignes « Détaché » et synthèses."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)

    def fetch_detaches(self) -> list[ListeGeneraleRow]:
        rows = [r for r in self._repo.fetch_all() if in_detaches_scope(r)]
        return sorted(rows, key=_sort_key_detaches)

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
