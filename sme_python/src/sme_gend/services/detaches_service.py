"""Détachés — filtrage ListeGenerale (Padmin « Détaché » ou Structure = DETACHE), regroupement par subdivision / sigle."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

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
    """True si la colonne Structure correspond au code ``DETACHE`` (préfixe « DETACHE — … » inclus)."""
    from sme_gend.ui.personnel_list_filters import structure_matches_filter

    return structure_matches_filter(structure, STRUCTURE_CODE_DETACHE)


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


def detache_sans_ref_note(row: ListeGeneraleRow) -> bool:
    """True si la colonne « N° décision / réf. note » est vide."""
    return not (row.ndecision or "").strip()


def filter_detaches_rows(
    rows: Sequence[ListeGeneraleRow],
    *,
    sans_note_only: bool = False,
    avec_note_only: bool = False,
) -> list[ListeGeneraleRow]:
    """Filtre sur la réf. note de service (ndecision)."""
    if sans_note_only and not avec_note_only:
        return [r for r in rows if detache_sans_ref_note(r)]
    if avec_note_only and not sans_note_only:
        return [r for r in rows if not detache_sans_ref_note(r)]
    return list(rows)


_EMPTY_FIELD_LABEL = "(Non renseigné)"


def _field_filter_label(value: str | None) -> str:
    s = (value or "").strip()
    return s if s else _EMPTY_FIELD_LABEL


def _match_field_filter(row_value: str | None, selected: str | None) -> bool:
    key = (selected or "").strip()
    if not key or key == "__all__":
        return True
    return _field_filter_label(row_value).casefold() == key.casefold()


def filter_detaches_display(
    rows: Sequence[ListeGeneraleRow],
    *,
    search: str = "",
    sigle: str = "",
    structure: str = "",
    padmin: str = "",
) -> list[ListeGeneraleRow]:
    """Filtre recherche texte, sigle, structure et position administrative."""
    sk = (sigle or "").strip()
    if sk and sk != "__all__":
        rows = [r for r in rows if sigle_ou_subdivision(r).strip().upper() == sk.upper()]
    rows = [r for r in rows if _match_field_filter(r.structure, structure)]
    rows = [r for r in rows if _match_field_filter(r.position_admin, padmin)]
    needle = normalize_search_text((search or "").strip().casefold())
    if not needle:
        return list(rows)
    out: list[ListeGeneraleRow] = []
    for r in rows:
        hay = normalize_search_text(" ".join(
            p
            for p in (
                r.noms,
                r.grade,
                r.structure,
                r.subdivision_arme,
                r.position_admin,
                r.ndecision,
                r.fonction,
                sigle_ou_subdivision(r),
                detache_ecart_remarque(r),
            )
            if p
        ).casefold())
        if needle in normalize_search_text(hay):
            out.append(r)
    return out


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


def recap_par_structure(rows: Sequence[ListeGeneraleRow]) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for r in rows:
        c[_field_filter_label(r.structure)] += 1
    return sorted(c.items(), key=lambda x: (-x[1], x[0].lower()))


def recap_par_padmin(rows: Sequence[ListeGeneraleRow]) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for r in rows:
        c[_field_filter_label(r.position_admin)] += 1
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
        from sme_gend.services.liste_generale_cache import cached_liste_rows

        rows = [r for r in cached_liste_rows(self._settings) if in_detaches_scope(r)]
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
