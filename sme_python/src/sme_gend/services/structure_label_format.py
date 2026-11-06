"""Affichage structure — code référentiel + désignation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sme_gend.models.structure import StructureRow

ORPHAN_SUFFIX = " (hors référentiel)"


def structure_combo_label(code: str, designation: str | None = None) -> str:
    c = (code or "").strip()
    d = (designation or "").strip()
    if c and d:
        return f"{c} — {d}"
    return c or d


def _index_structures(
    structures: Sequence[StructureRow] | Mapping[str, StructureRow] | None,
) -> tuple[dict[str, StructureRow], dict[str, StructureRow]]:
    by_code: dict[str, StructureRow] = {}
    by_desig: dict[str, StructureRow] = {}
    if structures is None:
        return by_code, by_desig
    if isinstance(structures, Mapping):
        for key, row in structures.items():
            if isinstance(row, StructureRow):
                s = row
            else:
                s = StructureRow(code=(key or "").strip(), designation=str(row or ""), ordre_tri=0)
            code = (s.code or "").strip()
            des = (s.designation or "").strip()
            if code:
                by_code[code.casefold()] = s
            if des and code:
                by_desig[des.casefold()] = s
        return by_code, by_desig
    for s in structures:
        code = (s.code or "").strip()
        des = (s.designation or "").strip()
        if code:
            by_code[code.casefold()] = s
        if des and code:
            by_desig[des.casefold()] = s
    return by_code, by_desig


def resolve_structure_code(
    raw: str | None,
    structures: Sequence[StructureRow] | Mapping[str, StructureRow] | None = None,
) -> str:
    """Retourne le code référentiel ; accepte code ou désignation en entrée."""
    s = (raw or "").strip()
    if not s:
        return ""
    by_code, by_desig = _index_structures(structures)
    if s.casefold() in by_code:
        return (by_code[s.casefold()].code or s).strip()
    if s.casefold() in by_desig:
        return (by_desig[s.casefold()].code or s).strip()
    return s


def format_structure_code_display(
    raw: str | None,
    structures: Sequence[StructureRow] | Mapping[str, StructureRow] | None = None,
    *,
    orphan_suffix: str = ORPHAN_SUFFIX,
) -> str:
    """Retourne le sigle / code structure (colonnes tableau, export court)."""
    s = (raw or "").strip()
    if not s:
        return ""
    code = resolve_structure_code(s, structures)
    by_code, _ = _index_structures(structures)
    if code and code.casefold() in by_code:
        return (by_code[code.casefold()].code or code).strip()
    if code:
        return code
    if orphan_suffix:
        return f"{s}{orphan_suffix}"
    return s


def format_structure_display(
    raw: str | None,
    structures: Sequence[StructureRow] | Mapping[str, StructureRow] | None = None,
    *,
    orphan_suffix: str = ORPHAN_SUFFIX,
) -> str:
    """Libellé UI : « CODE — désignation » ou code + suffixe orphelin."""
    s = (raw or "").strip()
    if not s:
        return ""
    by_code, by_desig = _index_structures(structures)
    row = by_code.get(s.casefold()) or by_desig.get(s.casefold())
    if row is not None:
        return structure_combo_label(row.code, row.designation)
    code = resolve_structure_code(s, structures)
    if code and code.casefold() in by_code:
        row = by_code[code.casefold()]
        return structure_combo_label(row.code, row.designation)
    if orphan_suffix:
        return f"{s}{orphan_suffix}"
    return s


def structure_combo_items(
    structures: Sequence[StructureRow],
    extra_codes: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """(libellé affiché, code) triés par code."""
    by_code, _ = _index_structures(structures)
    seen: set[str] = set()
    codes: list[str] = []
    for s in structures:
        c = (s.code or "").strip()
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            codes.append(c)
    for raw in extra_codes or ():
        c = resolve_structure_code(raw, structures)
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            codes.append(c)
    codes.sort(key=lambda c: c.casefold())
    return [(format_structure_display(c, structures), c) for c in codes]
