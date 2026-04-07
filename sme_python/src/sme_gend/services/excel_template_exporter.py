"""Export Excel haute fidélité : copie de la maquette + styles issus des lignes modèle."""

from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.services.resultat_format import RESULTAT_HEADERS, row_to_resultat_cells


def _apply_cell_style(src, dst) -> None:
    if src.font:
        dst.font = copy(src.font)
    if src.border:
        dst.border = copy(src.border)
    if src.fill:
        dst.fill = copy(src.fill)
    if src.number_format:
        dst.number_format = src.number_format
    if src.protection:
        dst.protection = copy(src.protection)
    if src.alignment:
        dst.alignment = copy(src.alignment)


def _copy_row_values_and_style(
    tpl_ws: Worksheet,
    src_row: int,
    dst_ws: Worksheet,
    dst_row: int,
    ncol: int,
) -> None:
    for c in range(1, ncol + 1):
        s = tpl_ws.cell(src_row, c)
        d = dst_ws.cell(dst_row, c)
        d.value = s.value
        _apply_cell_style(s, d)


def _copy_merges_for_rows(tpl_ws: Worksheet, dst_ws: Worksheet, max_src_row: int) -> None:
    for m in tpl_ws.merged_cells.ranges:
        if m.min_row <= max_src_row:
            dst_ws.merge_cells(str(m))


def _copy_column_widths(tpl_ws: Worksheet, dst_ws: Worksheet, ncol: int) -> None:
    for c in range(1, ncol + 1):
        col_letter = get_column_letter(c)
        if col_letter in tpl_ws.column_dimensions:
            dst_ws.column_dimensions[col_letter].width = tpl_ws.column_dimensions[col_letter].width


def export_liste_fidele(
    template_path: Path,
    layout: ListeLayoutConfig,
    sections_data: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]],
    destination: Path,
) -> None:
    """
    Crée un classeur à partir de la maquette : lignes 1–3 identiques (titre, vide, en-têtes),
    puis corps généré avec styles copiés depuis les lignes modèle 4–6 de la feuille source.

    :param sections_data: liste de (title_main, title_sub, structure_code, blocks)
    """
    ncol = layout.ncols
    tpl_path = Path(template_path)
    if not tpl_path.is_file():
        raise FileNotFoundError(f"Maquette introuvable : {tpl_path}")

    tpl_wb = load_workbook(tpl_path, keep_vba=False, rich_text=False)
    if layout.template_sheet not in tpl_wb.sheetnames:
        tpl_wb.close()
        raise ValueError(f"Feuille « {layout.template_sheet} » absente de la maquette.")
    tpl_ws = tpl_wb[layout.template_sheet]

    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = layout.output_sheet

    # En-tête document (lignes 1–3)
    for r in range(1, layout.header_row + 1):
        _copy_row_values_and_style(tpl_ws, r, out_ws, r, ncol)
    _copy_merges_for_rows(tpl_ws, out_ws, layout.header_row)
    _copy_column_widths(tpl_ws, out_ws, ncol)

    r_sec = layout.style_row_section
    r_sub = layout.style_row_sub
    r_dat = layout.style_row_data

    cur = layout.header_row + 1
    n_global = 0

    for title_main, title_sub, _code, blocks in sections_data:
        out_ws.merge_cells(start_row=cur, start_column=1, end_row=cur, end_column=ncol)
        for c in range(1, ncol + 1):
            _apply_cell_style(tpl_ws.cell(r_sec, c), out_ws.cell(cur, c))
        out_ws.cell(cur, 1, value=title_main)
        cur += 1

        out_ws.merge_cells(start_row=cur, start_column=1, end_row=cur, end_column=ncol)
        for c in range(1, ncol + 1):
            _apply_cell_style(tpl_ws.cell(r_sub, c), out_ws.cell(cur, c))
        out_ws.cell(cur, 1, value=title_sub)
        cur += 1

        for sub_label, personnes in blocks:
            out_ws.merge_cells(start_row=cur, start_column=1, end_row=cur, end_column=ncol)
            for c in range(1, ncol + 1):
                _apply_cell_style(tpl_ws.cell(r_sub, c), out_ws.cell(cur, c))
            out_ws.cell(cur, 1, value=f"Subdivision d'arme : {sub_label}")
            cur += 1

            n_interne = 0
            for pr in personnes:
                n_global += 1
                n_interne += 1
                vals = row_to_resultat_cells(pr, n_global, n_interne)
                for col_idx, val in enumerate(vals, start=1):
                    cell = out_ws.cell(cur, col_idx, value=val)
                    _apply_cell_style(tpl_ws.cell(r_dat, col_idx), cell)
                    cell.value = val
                cur += 1

    tpl_wb.close()
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out_wb.save(dest)


def export_simple_fallback(
    destination: Path,
    sections_data: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]],
) -> None:
    """Sans maquette : classeur minimal (même logique que l’ancien export)."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    ncols = len(RESULTAT_HEADERS)
    wb = Workbook()
    ws = wb.active
    ws.title = "Liste_SME"
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill_hdr = PatternFill("solid", fgColor="D9E2F3")
    fill_sec1 = PatternFill("solid", fgColor="E8E8E8")
    fill_sec2 = PatternFill("solid", fgColor="F2F2F2")
    fill_sub = PatternFill("solid", fgColor="E6F0FF")

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    ws.cell(row=row, column=1, value="LISTE DES PRÉSENTS").font = Font(bold=True, size=14)
    row += 2
    for col, title in enumerate(RESULTAT_HEADERS, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = Font(bold=True, size=10)
        cell.fill = fill_hdr
        cell.border = border
    row += 1
    n_global = 0

    for title_main, title_sub, _c, blocks in sections_data:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        ws.cell(row=row, column=1, value=title_main).font = Font(bold=True, size=11)
        ws.cell(row=row, column=1).fill = fill_sec1
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        ws.cell(row=row, column=1, value=title_sub).font = Font(bold=True, size=10)
        ws.cell(row=row, column=1).fill = fill_sec2
        row += 1
        for sub_label, personnes in blocks:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
            ws.cell(row=row, column=1, value=f"Subdivision d'arme : {sub_label}").fill = fill_sub
            row += 1
            n_interne = 0
            for pr in personnes:
                n_global += 1
                n_interne += 1
                for col_idx, val in enumerate(row_to_resultat_cells(pr, n_global, n_interne), start=1):
                    ws.cell(row=row, column=col_idx, value=val).border = border
                row += 1

    for col in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    wb.save(destination)
