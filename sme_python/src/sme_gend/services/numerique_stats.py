"""Agrégats situation numérique et INDISPO — source : ListeGenerale (Access)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.numerique_padmin import (
    PADMIN_CONGE_ADMIN,
    PADMIN_DESERTEUR,
    PADMIN_INST_MR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_PRISON_MA,
    PADMIN_PUNIS,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)

# Ordre des rubriques : première matrice NUMERIQUE (lignes 8–19), clés prédicats.
RUBRIQUE_KEYS_ROW8_19: tuple[str, ...] = (
    "inscrits",
    "presents",
    "detache",
    "deserteur",
    "conge_admin",
    "conge_exp",
    "mission",
    "malade",
    "inst_mr",
    "stage_local",
    "stage_ex",
    "prison_ma",
)

# Colonnes B–I feuille INDISPO (sans TOTAL) : (clé interne, valeur Padmin ou tuple)
INDISPO_CATEGORY_SPECS: tuple[tuple[str, str | tuple[str, ...]], ...] = (
    ("mission", PADMIN_MISSION),
    ("malade", PADMIN_MALADE),
    ("conge_admin", PADMIN_CONGE_ADMIN),
    ("stage_local", PADMIN_STAGE_LOCAL),
    ("stage_ex", PADMIN_STAGE_EX),
    ("deserteur", PADMIN_DESERTEUR),
    ("inst_mr", PADMIN_INST_MR),
    ("detention", (PADMIN_PUNIS, PADMIN_PRISON_MA)),
)


def _match_padmin(row: ListeGeneraleRow, spec: str | tuple[str, ...]) -> bool:
    p = (row.position_admin or "").strip().casefold()
    if isinstance(spec, str):
        return p == spec.casefold()
    return p in {s.casefold() for s in spec}


def count_by_grades(
    rows: Sequence[ListeGeneraleRow],
    grades: Sequence[str],
    predicate: Callable[[ListeGeneraleRow], bool],
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> dict[str, int]:
    """Compte les lignes vérifiant `predicate` par code grade (colonne gabarit NUMERIQUE)."""
    from sme_gend.services.excel_style_helpers import grade_to_abbrev

    gmap = grade_abbrev_map or {}
    want = {g.strip().casefold() for g in grades if (g or "").strip()}
    key_by_cf = {g.strip().casefold(): g for g in grades if (g or "").strip()}
    out: dict[str, int] = {g: 0 for g in grades}
    for row in rows:
        if not predicate(row):
            continue
        raw = (row.grade or "").strip()
        code = grade_to_abbrev(raw, gmap) if gmap else raw
        cf = code.casefold()
        if cf in want:
            key = key_by_cf.get(cf)
            if key is not None:
                out[key] = out.get(key, 0) + 1
    return out


def sum_counts(counts: dict[str, int], grades: Sequence[str]) -> int:
    return sum(counts.get(g, 0) for g in grades)


# Tranches de grades (colonnes B→S) alignées sur le gabarit NUMERIQUE : blocs U, AY (agrégats par catégorie).
NUMERIQUE_CATEGORY_SLICES: tuple[tuple[str, slice], ...] = (
    ("Général", slice(0, 1)),
    ("Officier — supérieur", slice(1, 5)),
    ("Officier — subalterne", slice(5, 9)),
    ("Sous-officier — supérieur", slice(9, 12)),
    ("Sous-officier — subalterne", slice(12, 16)),
    ("MDR (SDT)", slice(16, 17)),
    ("Civil", slice(17, 18)),
)


def sum_by_grade_category(
    counts: dict[str, int],
    grades: Sequence[str],
) -> list[int]:
    """
    Sommes par les 7 catégories du gabarit + total (8 valeurs), dans l'ordre des colonnes V→AB.
    Les indices hors de la liste grades (gabarit avec moins de 18 colonnes) contribuent 0.
    """
    glist = list(grades)
    n = len(glist)
    cats: list[int] = []
    for _name, sl in NUMERIQUE_CATEGORY_SLICES:
        s = sum(int(counts.get(glist[i], 0)) for i in range(sl.start, min(sl.stop, n)))
        cats.append(s)
    cats.append(sum(cats))
    return cats


def category_indisponibles(
    inscrits: dict[str, int],
    presents: dict[str, int],
    grades: Sequence[str],
) -> list[int]:
    """Pour chaque catégorie : inscrits − présents (+ total), comme U11 du gabarit."""
    ci = sum_by_grade_category(inscrits, grades)
    cp = sum_by_grade_category(presents, grades)
    out: list[int] = []
    for a, b in zip(ci[:-1], cp[:-1], strict=True):
        out.append(a - b)
    out.append(sum(out))
    return out


# Récap « variation des indisponibles » (CB) : clé rubrique → ligne Excel dont la colonne T alimente CE.
RECAP_INDISPO_KEYS: tuple[tuple[str, str], ...] = (
    ("detache", "Détaché"),
    ("deserteur", "Déserteur"),
    ("conge_admin", "Congé administratif"),
    ("mission", "Mission"),
    ("malade", "Malade"),
    ("inst_mr", "En instance de mise route"),
    ("stage_local", "Stage local"),
    ("stage_ex", "Stage extérieur"),
    ("prison_ma", "Punis"),
)


def matrix_row_total(matrix: dict[str, dict[str, int]], key: str, grades: Sequence[str]) -> int:
    return sum(int(matrix[key].get(g, 0)) for g in grades)


def build_numerique_matrix(
    rows: Sequence[ListeGeneraleRow],
    grades: Sequence[str],
    rubrique_keys: Sequence[str] = RUBRIQUE_KEYS_ROW8_19,
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Pour chaque rubrique, comptages par grade."""
    from sme_gend.services.numerique_padmin import row_predicate_for_rubrique

    matrix: dict[str, dict[str, int]] = {}
    for key in rubrique_keys:
        pred = row_predicate_for_rubrique(key)
        matrix[key] = count_by_grades(
            rows, grades, pred, grade_abbrev_map=grade_abbrev_map
        )
    return matrix


def extract_structure_codes_from_indispo_formula(formula: str | None) -> list[str]:
    """
    Extrait les codes structure depuis une formule COUNTIFS de la colonne B
    (feuille INDISPO PAR STRUCTURE bis), ex. ...$P$...,"EM-GEND").
    """
    if not formula or not isinstance(formula, str) or not formula.startswith("="):
        return []
    return re.findall(r'P\$[0-9:$]+,\s*"([^"]+)"', formula)


def count_indispo_cell(
    rows: Sequence[ListeGeneraleRow],
    structure_codes: Sequence[str],
    padmin_spec: str | tuple[str, ...],
) -> int:
    sc = {(c or "").strip() for c in structure_codes if (c or "").strip()}
    n = 0
    for row in rows:
        st = (row.structure or "").strip()
        if st not in sc:
            continue
        if not _match_padmin(row, padmin_spec):
            continue
        n += 1
    return n


def build_indispo_matrix(
    rows: Sequence[ListeGeneraleRow],
    *,
    row_structure_codes: Sequence[list[str]],
    category_specs: Sequence[tuple[str, str | tuple[str, ...]]] = INDISPO_CATEGORY_SPECS,
) -> list[list[int]]:
    """
    Une ligne par structure (même ordre que row_structure_codes),
    colonnes : une par catégorie + total en fin (calculé ailleurs).
    Retourne matrice [ligne][col] pour len(category_specs) colonnes.
    """
    matrix: list[list[int]] = []
    for codes in row_structure_codes:
        line: list[int] = []
        for _key, spec in category_specs:
            line.append(count_indispo_cell(rows, codes, spec))
        matrix.append(line)
    return matrix
