"""Affichage grade — code court (TabGrade) plutôt que libellé long."""

from __future__ import annotations

from collections.abc import Sequence
import unicodedata
from functools import lru_cache

from sme_gend.models.grade_ref import GradeRefRow


@lru_cache(maxsize=4096)
def normalize_grade_key(s: str) -> str:
    """Clé de comparaison insensible à la casse, aux accents et à la ponctuation."""
    normalized = unicodedata.normalize("NFKD", (s or "").casefold())
    chars = (
        " " if unicodedata.category(char).startswith("P") else char
        for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join("".join(chars).split())


@lru_cache(maxsize=32)
def _grade_display_maps(rows: tuple):
    by_code: dict[str, str] = {}
    by_lib: dict[str, str] = {}
    by_lib_norm: dict[str, str] = {}
    for raw_code, raw_lib in rows:
        code = raw_code.strip()
        lib = raw_lib.strip()
        if code:
            by_code[code.casefold()] = code
        if lib and code:
            lib_cf = lib.casefold()
            lib_nk = normalize_grade_key(lib)
            if lib_cf not in by_lib or len(code) < len(by_lib[lib_cf]):
                by_lib[lib_cf] = code
            if lib_nk not in by_lib_norm or len(code) < len(by_lib_norm[lib_nk]):
                by_lib_norm[lib_nk] = code
    return by_code, by_lib, by_lib_norm


def format_grade_display(
    raw: str | None,
    grades: Sequence[GradeRefRow] | None = None,
) -> str:
    """Resolve against a value-keyed catalogue; edits cannot reuse stale aliases."""
    s = (raw or "").strip()
    if not s:
        return ""
    cf = s.casefold()
    nk = normalize_grade_key(s)
    by_code, by_lib, by_lib_norm = _grade_display_maps(
        tuple((g.code or '', g.libelle or '') for g in grades or ())
    )
    if cf in by_lib:
        return by_lib[cf]
    if nk in by_lib_norm:
        return by_lib_norm[nk]
    if cf in by_code:
        return by_code[cf]
    return s


def grade_combo_label(code: str, grades: Sequence[GradeRefRow] | None = None) -> str:
    """Libellé combo filtre : « CODE — libellé » si le référentiel le permet."""
    c = (code or "").strip()
    if not c:
        return ""
    for g in grades or ():
        gc = (g.code or "").strip()
        if gc.casefold() != c.casefold():
            continue
        lib = (g.libelle or "").strip()
        if lib and lib.casefold() != gc.casefold():
            return f"{gc} — {lib}"
        return gc
    return c


def grade_combo_items(
    grades: Sequence[GradeRefRow],
    extra_raw: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """(libellé affiché, code) triés — un seul code par grade référentiel."""
    by_lib: dict[str, str] = {}
    orphan_codes: list[str] = []

    for g in grades:
        code = (g.code or "").strip()
        if not code:
            continue
        lib = (g.libelle or "").strip()
        if lib and lib.casefold() != code.casefold():
            key = normalize_grade_key(lib)
            cur = by_lib.get(key)
            if cur is None or len(code) < len(cur):
                by_lib[key] = code
        else:
            orphan_codes.append(code)

    seen: set[str] = set()
    codes: list[str] = []
    for c in list(by_lib.values()) + orphan_codes:
        resolved = format_grade_display(c, grades)
        rk = resolved.casefold()
        if not resolved or rk in seen:
            continue
        seen.add(rk)
        codes.append(resolved)

    for raw in extra_raw or ():
        c = format_grade_display(raw, grades)
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            codes.append(c)

    codes.sort(key=lambda x: x.casefold())
    return [(grade_combo_label(c, grades), c) for c in codes]
