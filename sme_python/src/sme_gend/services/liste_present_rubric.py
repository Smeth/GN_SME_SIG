"""Rubriques liste des présents — lien strict structure / subdivision (ref RSA)."""

from __future__ import annotations

from collections import defaultdict

from sme_gend.models.liste_generale import ListeGeneraleRow

SANS_SUBDIVISION_KEY = "(Sans subdivision)"
# Marqueurs internes (moteur populate_liste — ALGORITHME_LISTE_PRESENTS.md)
MERGED_SUBDIVISIONS_KEY = "__MERGED__"
RG_COMMANDEMENT_MARKER = "__RG_COMMANDEMENT__"


def _sort_personnel(rows: list[ListeGeneraleRow]) -> list[ListeGeneraleRow]:
    return sorted(rows, key=lambda x: (x.ordre_tri or 0, x.noms or ""))


def _match_subdivision_key(row_sub: str | None, rsa_label: str) -> bool:
    a = (row_sub or "").strip().casefold()
    b = (rsa_label or "").strip().casefold()
    return bool(a and b and a == b)


def build_strict_subdiv_blocks(
    rows: list[ListeGeneraleRow],
    subdivision_order: list[str] | None,
    *,
    include_empty_rubrics: bool = True,
) -> list[tuple[str, list[ListeGeneraleRow]]]:
    """
    Blocs subdivision alignés sur ``ref_subdivision_arme`` (ordre layout).

    - Une rubrique par libellé RSA (même vide si ``include_empty_rubrics``).
    - Effectif sans subdivision → bloc ``SANS_SUBDIVISION_KEY``.
    - Libellés carrière hors RSA → bloc conservé en fin (anomalie visible).
    """
    by_sub: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
    for r in rows:
        key = (r.subdivision_arme or "").strip() or SANS_SUBDIVISION_KEY
        by_sub[key].append(r)

    blocks: list[tuple[str, list[ListeGeneraleRow]]] = []
    consumed: set[str] = set()

    direct = by_sub.pop(SANS_SUBDIVISION_KEY, [])
    if direct:
        blocks.append((SANS_SUBDIVISION_KEY, _sort_personnel(direct)))
        consumed.add(SANS_SUBDIVISION_KEY)

    if subdivision_order:
        for label in subdivision_order:
            lbl = (label or "").strip()
            if not lbl:
                continue
            matched_key: str | None = None
            for k in list(by_sub.keys()):
                if _match_subdivision_key(k, lbl):
                    matched_key = k
                    break
            people = _sort_personnel(by_sub.pop(matched_key, [])) if matched_key else []
            if people or include_empty_rubrics:
                blocks.append((lbl, people))
            if matched_key:
                consumed.add(matched_key)

    remaining = sorted(by_sub.keys(), key=str.casefold)
    for k in remaining:
        people = _sort_personnel(by_sub[k])
        if people:
            blocks.append((k, people))

    return blocks


def should_emit_structure_code_rubric(
    blocks: list[tuple[str, list[ListeGeneraleRow]]],
    *,
    title_main: str,
    title_sub: str,
) -> bool:
    """
    Ligne sec2 « code structure » (ex. CS-DPF) : uniquement si effectif sans subdivision
    et pas de rubriques subdivision avec du monde.
    """
    sans = next((ps for lbl, ps in blocks if lbl == SANS_SUBDIVISION_KEY), [])
    if not sans:
        return False
    subdiv_busy = any(lbl != SANS_SUBDIVISION_KEY and ps for lbl, ps in blocks)
    if subdiv_busy:
        return False
    ts = (title_sub or "").strip()
    tm = (title_main or "").strip()
    return bool(ts) and ts.casefold() != tm.casefold()


def _emit_subdiv_block(
    out: list[tuple[str, str | list[ListeGeneraleRow]]],
    lbl: str,
    people: list[ListeGeneraleRow],
) -> None:
    if lbl == RG_COMMANDEMENT_MARKER:
        out.append(("sec2", "COMMANDEMENT"))
        return
    if lbl == MERGED_SUBDIVISIONS_KEY:
        if people:
            out.append(("data", people))
        return
    out.append(("sec2", lbl))
    if people:
        out.append(("data", people))


def iter_section_rubrics(
    title_main: str,
    title_sub: str,
    blocks: list[tuple[str, list[ListeGeneraleRow]]],
) -> list[tuple[str, str | list[ListeGeneraleRow]]]:
    """
    Séquence d'émission pour aperçu / export.

    Retourne une liste de ``("sec1"|"sec2"|"data", payload)``.
    """
    out: list[tuple[str, str | list[ListeGeneraleRow]]] = []
    out.append(("sec1", title_main))

    # CS-DPF et assimilés : une seule liste sans rubriques subdivision
    if (
        len(blocks) == 1
        and blocks[0][0] == MERGED_SUBDIVISIONS_KEY
        and blocks[0][1]
    ):
        out.append(("data", blocks[0][1]))
        return out

    emit_code = should_emit_structure_code_rubric(
        blocks, title_main=title_main, title_sub=title_sub
    )
    sans_people = next(
        (ps for lbl, ps in blocks if lbl == SANS_SUBDIVISION_KEY),
        [],
    )

    if emit_code:
        out.append(("sec2", title_sub))
        if sans_people:
            out.append(("data", sans_people))
        for lbl, people in blocks:
            if lbl == SANS_SUBDIVISION_KEY:
                continue
            _emit_subdiv_block(out, lbl, people)
        return out

    if sans_people:
        rubric = (title_sub or "").strip() or SANS_SUBDIVISION_KEY
        out.append(("sec2", rubric))
        out.append(("data", sans_people))

    for lbl, people in blocks:
        if lbl == SANS_SUBDIVISION_KEY:
            continue
        _emit_subdiv_block(out, lbl, people)

    return out
