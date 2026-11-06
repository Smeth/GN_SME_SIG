"""Moteur de génération liste des présents — algorithme ``populate_liste`` (DATABASE)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services.liste_present_rg_template import (
    is_rg_compagnie,
    normalize,
    template_sub_matches_mdb,
)
from sme_gend.services.liste_present_rubric import (
    MERGED_SUBDIVISIONS_KEY,
    RG_COMMANDEMENT_MARKER,
    SANS_SUBDIVISION_KEY,
)

_SANS_STRUCTURE_KEY = "(Sans structure)"

# Structures sans rubriques subdivision : fusion + tri grade (§ 4.7)
NO_SUBDIVISION_STRUCTURES = frozenset({"CS-DPF"})

GRADE_ORDER: tuple[str, ...] = (
    "GDB", "COL", "LCL", "CDT", "CNE", "LTN", "SLT",
    "ADC", "ADJ", "SCH", "MDC", "SGT", "MDL", "SDT",
    "ADM", "CLM", "CIV",
)

FONCTION_ORDER_OVERRIDES: dict[tuple[str, str], list[str]] = {
    ("COMGEND", "Direction"): [
        "INSPECTEUR IT",
        "DIRECTEUR DOE",
        "DIRECTEUR DPF",
        "DIRECTEUR DSF",
        "DIRECTEUR DTRJD",
        "DIRECTEUR DIRTRANS",
        "DIRECTEUR DIRMAT",
        "DIRECTEUR DDT",
    ],
}


from sme_gend.services.matricule_format import normalize_matricule_solde


def person_key(row: ListeGeneraleRow) -> str:
    """Clé anti-doublon (§ 4.3)."""
    leg = (row.access_join_id or row.id or "").strip()
    if leg:
        return f"id:{leg}"
    ms = normalize_matricule_solde(row.matricule_solde)
    if ms:
        return f"mat:{ms}"
    return f"nom:{(row.noms or '').strip()}"


def _grade_rank(grade: str | None) -> int:
    """Rang hiérarchique (petit = grade le plus élevé). Codes courts ou libellés partiels."""
    g = (grade or "").strip().upper()
    if not g:
        return len(GRADE_ORDER)
    for i, code in enumerate(GRADE_ORDER):
        if g == code:
            return i
    best_i = len(GRADE_ORDER)
    best_len = 0
    for i, code in enumerate(GRADE_ORDER):
        if code in g or g in code:
            if len(code) > best_len:
                best_len = len(code)
                best_i = i
    return best_i


def grade_sort_key(row: ListeGeneraleRow) -> tuple[int, str]:
    return (_grade_rank(row.grade), (row.noms or "").strip().upper())


def default_sort_key(row: ListeGeneraleRow) -> tuple[int, int, str]:
    """Ordre MDB : OderTri, presseance, noms."""
    return (
        row.ordre_tri or 0,
        row.presseance or 0,
        (row.noms or "").strip().upper(),
    )


def fonction_sort_key(order: list[str]) -> Callable[[ListeGeneraleRow], tuple[int, str]]:
    rank = {f.upper(): i for i, f in enumerate(order)}
    lim = len(order)

    def _key(row: ListeGeneraleRow) -> tuple[int, str]:
        fn = (row.fonction or "").strip().upper()
        return (rank.get(fn, lim), (row.noms or "").strip().upper())

    return _key


def group_personnel(rows: list[ListeGeneraleRow]) -> dict[tuple[str, str], list[ListeGeneraleRow]]:
    groups: dict[tuple[str, str], list[ListeGeneraleRow]] = defaultdict(list)
    for row in rows:
        code = (row.structure or "").strip()
        sub = (row.subdivision_arme or "").strip()
        groups[(code, sub)].append(row)
    for key in groups:
        groups[key].sort(key=grade_sort_key)
    return dict(groups)


def find_people(
    groups: dict[tuple[str, str], list[ListeGeneraleRow]],
    code: str,
    template_sub: str,
) -> list[ListeGeneraleRow]:
    """Recherche effectif pour une rubrique template (§ 4.5)."""
    c = (code or "").strip()
    tpl = (template_sub or "").strip()

    if c.startswith("RG-"):
        result: list[ListeGeneraleRow] = []
        for (gc, mdb_sub), people in groups.items():
            if gc == c and template_sub_matches_mdb(c, tpl, mdb_sub):
                result.extend(people)
        result.sort(key=grade_sort_key)
        return result

    sub_norm = normalize(tpl)
    for (gc, s), people in groups.items():
        if gc == c and normalize(s) == sub_norm:
            return list(people)
    return []


def _resolve_subdivision_labels(
    sec: SectionBlock,
    resolve_labels: Callable[[SectionBlock], list[str] | None] | None,
) -> list[str]:
    """Ordre template (layout) prioritaire ; repli sur résolveur externe."""
    if sec.subdivision_order:
        return [x for x in sec.subdivision_order if (x or "").strip()]
    if resolve_labels is not None:
        resolved = resolve_labels(sec)
        if resolved:
            return resolved
    return []


def _sort_people(
    code: str,
    template_sub: str,
    people: list[ListeGeneraleRow],
) -> list[ListeGeneraleRow]:
    func_order = FONCTION_ORDER_OVERRIDES.get((code, template_sub))
    if func_order:
        return sorted(people, key=fonction_sort_key(func_order))
    return sorted(people, key=grade_sort_key)


def populate_liste_sections(
    layout: ListeLayoutConfig,
    rows: list[ListeGeneraleRow],
    st_idx: dict[str, StructureRow],
    *,
    include_orphans: bool = True,
    resolve_subdivision_labels: Callable[[SectionBlock], list[str] | None] | None = None,
) -> tuple[
    list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]],
    list[tuple[str, str, int]],
    set[str],
]:
    """
    Remplit les sections selon l'algorithme ``populate_liste``.

    Retourne (sections, missing[(code, sub, count)], assigned_keys).
    """
    groups = group_personnel(rows)
    assigned: set[str] = set()
    missing: list[tuple[str, str, int]] = []
    out: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]] = []
    covered_codes: set[str] = set()

    for sec in sorted(layout.sections, key=lambda s: s.order):
        code = sec.structure_code.strip()
        if code not in st_idx:
            continue
        covered_codes.add(code)
        subs = _resolve_subdivision_labels(sec, resolve_subdivision_labels)
        if not subs and code not in NO_SUBDIVISION_STRUCTURES:
            continue

        if code in st_idx:
            st = st_idx[code]
            title_main = (sec.title_main_override or st.designation or code).strip().upper()
            title_sub = (sec.title_sub_override or st.code or code).strip()
        else:
            title_main = (sec.title_main_override or code).strip().upper()
            title_sub = (sec.title_sub_override or code).strip()

        blocks: list[tuple[str, list[ListeGeneraleRow]]] = []

        if code in NO_SUBDIVISION_STRUCTURES:
            merged: list[ListeGeneraleRow] = []
            for sub in subs:
                for p in find_people(groups, code, sub):
                    pk = person_key(p)
                    if pk not in assigned:
                        merged.append(p)
            merged.sort(key=grade_sort_key)
            for p in merged:
                assigned.add(person_key(p))
            if merged:
                blocks.append((MERGED_SUBDIVISIONS_KEY, merged))
            if blocks:
                out.append((title_main, title_sub, code, blocks))
            continue

        if not sec.group_by_subdivision:
            people = [
                p for p in groups.get((code, ""), [])
                if person_key(p) not in assigned
            ]
            for sub in subs:
                for p in find_people(groups, code, sub):
                    pk = person_key(p)
                    if pk not in assigned:
                        people.append(p)
            people = _sort_people(code, "", people)
            for p in people:
                assigned.add(person_key(p))
            if people:
                blocks.append(("(Effectif)", people))
            if blocks:
                out.append((title_main, title_sub, code, blocks))
            continue

        for sub in subs:
            sub_label = sub if sub else SANS_SUBDIVISION_KEY
            block_labels: list[str] = [sub_label]
            if code.startswith("RG-") and is_rg_compagnie(sub):
                block_labels.append(RG_COMMANDEMENT_MARKER)

            people = [
                p for p in find_people(groups, code, sub)
                if person_key(p) not in assigned
            ]
            people = _sort_people(code, sub, people)

            for lbl in block_labels:
                if lbl == RG_COMMANDEMENT_MARKER:
                    blocks.append((RG_COMMANDEMENT_MARKER, []))
                elif lbl == sub_label:
                    for p in people:
                        assigned.add(person_key(p))
                    blocks.append((sub_label, people))

        if blocks:
            out.append((title_main, title_sub, code, blocks))

    if include_orphans:
        for (code, sub), people in sorted(groups.items()):
            remaining = [p for p in people if person_key(p) not in assigned]
            if not remaining:
                continue
            missing.append((code, sub, len(remaining)))
            if not code:
                desig = "⚠ STRUCTURE NON RENSEIGNÉE"
                code_out = _SANS_STRUCTURE_KEY
            elif code in st_idx:
                desig = (st_idx[code].designation or code).strip().upper()
                code_out = code
            elif code not in covered_codes:
                desig = f"⚠ HORS CONFIGURATION : {code}"
                code_out = code
            else:
                desig = (st_idx[code].designation or code).strip().upper()
                code_out = code
            sub_lbl = sub or SANS_SUBDIVISION_KEY
            orphan_blocks: list[tuple[str, list[ListeGeneraleRow]]] = [
                (sub_lbl, remaining),
            ]
            for p in remaining:
                assigned.add(person_key(p))
            out.append((desig, code_out, code_out, orphan_blocks))

    return out, missing, assigned
