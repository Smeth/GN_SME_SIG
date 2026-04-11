"""Génération du contenu de la feuille Excel « DETACHE » (titres + lignes personnel)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.models.detaches_file_config import DetachesFileConfig, DetachesStructureEntry
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.grade_ref import GradeRefRow


def _norm_sigle_key(row: ListeGeneraleRow) -> str:
    s = (row.subdivision_arme or "").strip().upper()
    return s if s else "(NON RENSEIGNÉ)"


def _grade_order_map(grades: Sequence[GradeRefRow]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, g in enumerate(grades):
        code = (g.code or "").strip()
        if code:
            pref = g.pref_ord
            out[code] = int(pref) if pref is not None else i
    return out


def _sort_persons(rows: list[ListeGeneraleRow], grade_order: dict[str, int]) -> list[ListeGeneraleRow]:
    def key(r: ListeGeneraleRow) -> tuple:
        g = (r.grade or "").strip()
        go = grade_order.get(g, 10_000)
        return (go, (r.noms or "").lower())

    return sorted(rows, key=key)


def _fmt_date_grade(row: ListeGeneraleRow) -> str | None:
    dg = row.date_grade
    if dg:
        return dg.strftime("%d/%m/%Y")
    ds = (row.date_service or "").strip()
    return ds or None


def _structure_sort_key(
    entry: DetachesStructureEntry,
    sigle_presidence: str,
) -> tuple:
    pres = (sigle_presidence or "").strip().upper()
    if pres and entry.sigle == pres:
        return (0, entry.ordre, entry.sigle)
    return (1, entry.ordre, entry.sigle)


def fill_detache_sheet(
    ws: Worksheet,
    cfg: DetachesFileConfig,
    detaches: Sequence[ListeGeneraleRow],
    grades: Sequence[GradeRefRow],
) -> tuple[int, int]:
    """
    Réécrit la feuille à partir de la ligne 4 (lignes 1–3 : titre feuille, vide, en-têtes).
    Numérotation globale (col. A) et par structure (col. B) pour les lignes personnel uniquement.
    Retourne (effectif total écrit, nombre de blocs affichés).
    """
    opt = cfg.options
    grade_order = _grade_order_map(grades)

    by_sigle: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
    for r in detaches:
        by_sigle[_norm_sigle_key(r)].append(r)

    configured = {s.sigle for s in cfg.structures}
    unknown_rows: list[ListeGeneraleRow] = []
    for sig, lst in by_sigle.items():
        if sig not in configured:
            unknown_rows.extend(lst)

    blocks: list[tuple[DetachesStructureEntry, list[ListeGeneraleRow]]] = []

    ordered = sorted(cfg.structures, key=lambda e: _structure_sort_key(e, opt.sigle_presidence))
    for ent in ordered:
        rows_b = list(by_sigle.get(ent.sigle, []))
        titres = list(ent.titres_lignes)
        if not titres and rows_b:
            titres = [f"— {ent.sigle} —"]
        if not titres and not rows_b:
            continue
        entry_out = DetachesStructureEntry(sigle=ent.sigle, ordre=ent.ordre, titres_lignes=titres)
        blocks.append((entry_out, rows_b))

    if unknown_rows:
        hold = DetachesStructureEntry(
            sigle="__NON_CONFIG__",
            ordre=99999,
            titres_lignes=[opt.libelle_bloc_non_reference],
        )
        blocks.append((hold, _sort_persons(unknown_rows, grade_order)))

    if ws.max_row > 3:
        ws.delete_rows(4, ws.max_row - 3)

    ws["A1"] = opt.titre_ligne_feuille
    ws["A3"] = "N°"
    ws["C3"] = "Lieu de détachement"
    ws["D3"] = "Grade"
    ws["E3"] = "Nom(s) et prénom(s)"
    ws["F3"] = "Réf note de service"
    ws["G3"] = "Date de détachement"

    row = 4
    global_n = 0
    n_blocs = 0

    for ent, personnes in blocks:
        n_blocs += 1
        for line in ent.titres_lignes:
            ws.cell(row=row, column=1, value=line)
            row += 1
        local_n = 0
        for r in _sort_persons(personnes, grade_order):
            global_n += 1
            local_n += 1
            ws.cell(row=row, column=1, value=global_n)
            ws.cell(row=row, column=2, value=local_n)
            ws.cell(row=row, column=3, value=opt.lieu_defaut)
            ws.cell(row=row, column=4, value=r.grade)
            ws.cell(row=row, column=5, value=r.noms)
            ws.cell(row=row, column=6, value=r.ndecision)
            ws.cell(row=row, column=7, value=_fmt_date_grade(r))
            row += 1

    return global_n, n_blocs
