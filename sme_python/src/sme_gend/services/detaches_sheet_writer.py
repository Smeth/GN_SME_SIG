"""Génération du contenu de la feuille Excel « DETACHE » (titres + lignes personnel)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.services.excel_style_helpers import write_literal_cell_value
from sme_gend.config.settings import Settings
from sme_gend.models.detaches_file_config import DetachesFileConfig, DetachesStructureEntry
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.grade_ref import GradeRefRow


def _norm_sigle_key(row: ListeGeneraleRow) -> str:
    s = (row.subdivision_arme or "").strip().upper()
    return s if s else "(NON RENSEIGNÉ)"


def _sort_persons(
    rows: list[ListeGeneraleRow],
    grades: Sequence[GradeRefRow],
    settings: Settings | None = None,
) -> list[ListeGeneraleRow]:
    from sme_gend.services.personnel_list_order import sort_liste_rows_like_ui

    return sort_liste_rows_like_ui(rows, list(grades), settings)


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
    *,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> tuple[int, int]:
    """
    Réécrit la feuille à partir de la ligne 4 (lignes 1–3 : titre feuille, vide, en-têtes).
    Numérotation globale (col. A) et par structure (col. B) pour les lignes personnel uniquement.
    Retourne (effectif total écrit, nombre de blocs affichés).
    """
    opt = cfg.options
    gmap = grade_abbrev_map or {}
    if not gmap and grades:
        from sme_gend.services.excel_style_helpers import build_grade_abbrev_map, grade_to_abbrev

        gmap = build_grade_abbrev_map(grades)
    else:
        from sme_gend.services.excel_style_helpers import grade_to_abbrev

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
        blocks.append((hold, _sort_persons(unknown_rows, grades, settings)))

    from sme_gend.services.excel_style_helpers import apply_row_styles, snapshot_row_styles

    tpl_header_row = 4 if ws.max_row >= 4 else 3
    tpl_data_row = 5 if ws.max_row >= 5 else tpl_header_row
    style_header = snapshot_row_styles(ws, tpl_header_row, 1, 7)
    style_data = snapshot_row_styles(ws, tpl_data_row, 1, 7)

    if ws.max_row > 3:
        ws.delete_rows(4, ws.max_row - 3)

    write_literal_cell_value(ws, 1, 1, opt.titre_ligne_feuille)
    ws["A3"] = "N°"
    ws["C3"] = "Lieu de détachement"
    ws["D3"] = "Grade"
    ws["E3"] = "Nom(s) et prénom(s)"
    ws["F3"] = "Réf note de service"
    ws["G3"] = "Date de détachement"
    apply_row_styles(ws, 3, style_header, col_start=1)

    row = 4
    global_n = 0
    n_blocs = 0

    for ent, personnes in blocks:
        n_blocs += 1
        for line in ent.titres_lignes:
            write_literal_cell_value(ws, row, 1, line)
            row += 1
        local_n = 0
        for r in _sort_persons(personnes, grades, settings):
            global_n += 1
            local_n += 1
            write_literal_cell_value(ws, row, 1, global_n)
            write_literal_cell_value(ws, row, 2, local_n)
            write_literal_cell_value(ws, row, 3, opt.lieu_defaut)
            write_literal_cell_value(ws, row, 4, grade_to_abbrev(r.grade, gmap) if gmap else r.grade)
            write_literal_cell_value(ws, row, 5, r.noms)
            write_literal_cell_value(ws, row, 6, r.ndecision)
            write_literal_cell_value(ws, row, 7, _fmt_date_grade(r))
            apply_row_styles(ws, row, style_data, col_start=1)
            row += 1

    return global_n, n_blocs
