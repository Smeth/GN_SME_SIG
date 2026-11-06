"""Filtre par nature de stage (module Stages)."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from collections import Counter
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail

NATURE_FILTER_ALL = ""
NATURE_FILTER_EMPTY = "__EMPTY__"
TOP_NATURES_LIMIT = 15


def normalize_nature(text: str | None) -> str:
    return " ".join((text or "").strip().split())


def nature_from_detail(detail: StageDetail | None) -> str:
    if detail is None:
        return ""
    return normalize_nature(detail.nature_stage)


def stage_row_lookup_keys(row: ListeGeneraleRow) -> list[str]:
    """Clés possibles pour joindre une ligne liste à parcours_stage (Id Access, carrière…)."""
    keys: list[str] = []
    for candidate in (
        row.satellite_join_key(),
        (row.access_join_id or "").strip(),
        (row.id or "").strip(),
    ):
        if candidate and candidate not in keys:
            keys.append(candidate)
    return keys


def stage_detail_text_blob(detail: StageDetail | None) -> str:
    if detail is None:
        return ""
    return " ".join(
        (
            nature_from_detail(detail),
            (detail.lieu_stage or "").strip(),
            (detail.date_debut_fin or "").strip(),
        )
    )


def row_matches_stage_search(
    row: ListeGeneraleRow,
    needle: str,
    detail: StageDetail | None,
) -> bool:
    """Recherche texte sur la liste + nature / lieu / dates de la fiche stage."""
    from sme_gend.ui.list_table_filter import row_matches_search

    qcf = normalize_search_text(needle)
    if not qcf:
        return True
    if qcf in normalize_search_text(stage_detail_text_blob(detail)):
        return True
    return row_matches_search(row, needle)


def stage_detail_for_row(
    row: ListeGeneraleRow,
    details: dict[str, StageDetail],
) -> StageDetail | None:
    """Retourne la fiche stage la plus informative pour cette ligne."""
    fallback: StageDetail | None = None
    for key in stage_row_lookup_keys(row):
        detail = details.get(key)
        if detail is None:
            continue
        if (
            nature_from_detail(detail)
            or (detail.lieu_stage or "").strip()
            or (detail.date_debut_fin or "").strip()
        ):
            return detail
        fallback = detail
    return fallback


def row_matches_nature_filter(
    detail: StageDetail | None,
    filter_value: str | None,
) -> bool:
    fv = filter_value if filter_value is not None else NATURE_FILTER_ALL
    if fv == NATURE_FILTER_ALL or fv == "":
        return True
    if fv == NATURE_FILTER_EMPTY:
        return not nature_from_detail(detail)
    return normalize_search_text(nature_from_detail(detail)) == normalize_search_text(fv)


def build_nature_filter_options(
    rows: list[ListeGeneraleRow],
    details: dict[str, StageDetail],
    *,
    top_limit: int = TOP_NATURES_LIMIT,
) -> list[tuple[str, str]]:
    """Paires (libellé combo, valeur filtre) — les plus fréquentes en premier."""
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    empty_count = 0
    for row in rows:
        nat = nature_from_detail(stage_detail_for_row(row, details))
        if nat:
            key = normalize_search_text(nat)
            labels.setdefault(key, nat)
            counts[key] += 1
        else:
            empty_count += 1

    options: list[tuple[str, str]] = [("Toutes les natures", NATURE_FILTER_ALL)]
    frequent = counts.most_common(top_limit)
    seen: set[str] = set()
    for nat, n in frequent:
        label = labels[nat]
        options.append((f"{label} ({n})", label))
        seen.add(nat)

    for nat in sorted(counts.keys(), key=lambda s: s.casefold()):
        if nat in seen:
            continue
        label = labels[nat]
        options.append((f"{label} ({counts[nat]})", label))

    if empty_count:
        options.append((f"(Sans nature — {empty_count})", NATURE_FILTER_EMPTY))
    return options


def distinct_stage_natures_from_details(
    *detail_maps: dict[str, StageDetail],
) -> list[str]:
    """Natures distinctes (tri alphabétique) à partir de dictionnaires parcours_stage."""
    seen: set[str] = set()
    out: list[str] = []
    for dmap in detail_maps:
        for detail in dmap.values():
            nat = nature_from_detail(detail)
            if not nat:
                continue
            key = nat.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(nat)
    return sorted(out, key=lambda s: s.casefold())


def load_known_stage_natures(settings) -> list[str]:
    """Natures déjà présentes en base (tous types de stage)."""
    from sme_gend.repositories.numerique_satellite_repository import (
        NumeriqueSatelliteRepository,
    )

    sat = NumeriqueSatelliteRepository(settings)
    return distinct_stage_natures_from_details(
        sat.load_stages_loc(),
        sat.load_stages_ex(),
        sat.load_stages_internal(),
    )
