"""Outils layout — subdivisions d'arme (tri, fusion, dédoublonnage)."""

from __future__ import annotations

SANS_SUBDIVISION = "(Sans subdivision)"


def sort_subdiv_labels_alpha(labels: list[str]) -> list[str]:
    """Tri alphabétique ; « (Sans subdivision) » en dernier."""
    sans = SANS_SUBDIVISION
    rest = sorted((x for x in labels if x != sans), key=str.casefold)
    return rest + ([sans] if sans in labels else [])


def sort_subdiv_labels_by_count(
    labels: list[str],
    counts: dict[str, int],
) -> list[str]:
    """Tri par effectif décroissant ; « (Sans subdivision) » en dernier."""
    sans = SANS_SUBDIVISION

    def _count_for(lbl: str) -> int:
        c = counts.get(lbl, 0)
        for k, v in counts.items():
            if k.casefold() == lbl.casefold():
                c = max(c, v)
        return c

    def _key(lbl: str) -> tuple[int, int, str]:
        if lbl == sans:
            return (1, 0, lbl)
        return (0, -_count_for(lbl), lbl.casefold())

    return sorted(labels, key=_key)


def dedupe_subdiv_labels(labels: list[str], *, case_insensitive: bool = True) -> list[str]:
    """Retire les doublons en conservant la première occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for lbl in labels:
        s = (lbl or "").strip()
        if not s:
            continue
        key = s.casefold() if case_insensitive else s
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def merge_subdiv_labels(
    labels: list[str],
    sources: list[str],
    target: str,
) -> list[str]:
    """
    Fusionne plusieurs rubriques layout en une seule (``target``).

    Les libellés ``sources`` sont retirés ; ``target`` n'apparaît qu'une fois
    (à la position de la première source rencontrée).
    """
    src_norm = {(s or "").strip().casefold() for s in sources if (s or "").strip()}
    tgt = (target or "").strip()
    if not tgt:
        return list(labels)
    tgt_norm = tgt.casefold()
    src_norm.discard(tgt_norm)

    out: list[str] = []
    inserted = False
    for lbl in labels:
        s = (lbl or "").strip()
        if not s:
            continue
        if s.casefold() in src_norm:
            if not inserted:
                out.append(tgt)
                inserted = True
            continue
        if s.casefold() == tgt_norm:
            if not inserted:
                out.append(tgt)
                inserted = True
            continue
        out.append(s)
    if not inserted:
        out.append(tgt)
    return out
