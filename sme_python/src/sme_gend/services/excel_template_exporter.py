"""Export Excel haute fidélité : copie de la maquette + styles issus des lignes modèle."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

from collections.abc import Callable
from copy import copy
from pathlib import Path

from sme_gend.services.excel_style_helpers import write_cell_value, write_literal_cell_value, writable_cell, fit_wrapped_row, configure_table_print
from openpyxl import Workbook, load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.services.liste_present_rubric import iter_section_rubrics
from sme_gend.services.resultat_format import (
    RESULTAT_DOCUMENT_TITLE,
    RESULTAT_HEADERS,
    row_to_resultat_cells,
)


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


def _row_height(tpl_ws: Worksheet, row: int, default: float) -> float:
    h = tpl_ws.row_dimensions[row].height
    return float(h) if h is not None else default


def _copy_row_values_and_style(
    tpl_ws: Worksheet,
    src_row: int,
    dst_ws: Worksheet,
    dst_row: int,
    ncol: int,
) -> None:
    for c in range(1, ncol + 1):
        s = tpl_ws.cell(src_row, c)
        write_cell_value(dst_ws, dst_row, c, s.value)
        d = writable_cell(dst_ws, dst_row, c)
        _apply_cell_style(s, d)


def _copy_merges_for_rows(tpl_ws: Worksheet, dst_ws: Worksheet, max_src_row: int) -> None:
    for m in tpl_ws.merged_cells.ranges:
        if m.min_row <= max_src_row:
            dst_ws.merge_cells(str(m))


def _copy_column_widths(tpl_ws: Worksheet, dst_ws: Worksheet, ncol: int) -> None:
    # Excel may store one dimension for several columns (e.g. A:B). Copying
    # only the keyed column loses the width of the remaining columns.
    for col_letter, source in tpl_ws.column_dimensions.items():
        first = source.min or column_index_from_string(col_letter)
        last = source.max or first
        if first > ncol:
            continue
        dimension = copy(source)
        dimension.parent = dst_ws
        dimension.min, dimension.max = first, min(last, ncol)
        # Style IDs belong to the source workbook. Register the actual style
        # components in the destination instead of retaining those IDs.
        dimension._style = None
        _apply_cell_style(source, dimension)
        dst_ws.column_dimensions[col_letter] = dimension


def _write_merged_rubric_row(
    *,
    tpl_ws: Worksheet,
    out_ws: Worksheet,
    cur: int,
    ncol: int,
    style_row: int,
    label: str,
    height: float,
) -> None:
    out_ws.merge_cells(start_row=cur, start_column=1, end_row=cur, end_column=ncol)
    for c in range(1, ncol + 1):
        _apply_cell_style(tpl_ws.cell(style_row, c), out_ws.cell(cur, c))
    write_literal_cell_value(out_ws, cur, 1, label)
    out_ws.row_dimensions[cur].height = height


@tracked_export
def export_liste_fidele(
    template_path: Path,
    layout: ListeLayoutConfig,
    sections_data: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]],
    destination: Path,
    *,
    subdivision_display_for_row: Callable[[ListeGeneraleRow], str] | None = None,
    grades=None,
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

    for r in range(1, layout.header_row + 1):
        _copy_row_values_and_style(tpl_ws, r, out_ws, r, ncol)
        out_ws.row_dimensions[r].height = tpl_ws.row_dimensions[r].height
    _copy_merges_for_rows(tpl_ws, out_ws, layout.header_row)
    _copy_column_widths(tpl_ws, out_ws, ncol)
    out_ws.sheet_format = copy(tpl_ws.sheet_format)
    view = copy(tpl_ws.sheet_view)
    # Retain the model's display mode and zoom, but open the newly generated
    # document at its beginning rather than the model's old personnel row.
    view.topLeftCell = None
    view.selection = out_ws.sheet_view.selection
    view.pane = None
    view.tabSelected = None
    view.workbookViewId = 0
    out_ws.views.sheetView = [view]
    out_ws.page_setup = copy(tpl_ws.page_setup)
    out_ws.page_setup._parent = out_ws
    # Printer-driver binary relationships belong to the source package. The
    # portable page settings are copied, but its relationship must not dangle.
    out_ws.page_setup.id = None
    out_ws.page_margins = copy(tpl_ws.page_margins)
    out_ws.print_options = copy(tpl_ws.print_options)
    out_ws.sheet_properties.pageSetUpPr = copy(tpl_ws.sheet_properties.pageSetUpPr)
    out_ws.HeaderFooter = copy(tpl_ws.HeaderFooter)
    out_ws.print_title_rows = tpl_ws.print_title_rows or f'1:{layout.header_row}'
    out_ws.print_title_cols = tpl_ws.print_title_cols
    out_ws.freeze_panes = tpl_ws.freeze_panes

    r_sec = layout.style_row_section
    r_sub = layout.style_row_sub
    r_dat = layout.style_row_data
    h_sec = _row_height(tpl_ws, r_sec, 22.05)
    h_sub = _row_height(tpl_ws, r_sub, 13.95)
    h_dat = _row_height(tpl_ws, r_dat, 18.0)

    cur = layout.header_row + 1
    n_global = 0
    export_phase('render', total=sum(len(people) for _, _, _, blocks in sections_data for _, people in blocks))

    for title_main, title_sub, _code, blocks in sections_data:
        for kind, payload in iter_section_rubrics(title_main, title_sub, blocks):
            if kind == "sec1":
                _write_merged_rubric_row(
                    tpl_ws=tpl_ws,
                    out_ws=out_ws,
                    cur=cur,
                    ncol=ncol,
                    style_row=r_sec,
                    label=str(payload),
                    height=h_sec,
                )
                cur += 1
            elif kind == "sec2":
                _write_merged_rubric_row(
                    tpl_ws=tpl_ws,
                    out_ws=out_ws,
                    cur=cur,
                    ncol=ncol,
                    style_row=r_sub,
                    label=str(payload),
                    height=h_sub,
                )
                cur += 1
            elif kind == "data":
                n_interne = 0
                for pr in export_rows(payload):
                    n_global += 1
                    n_interne += 1
                    sub_disp = (
                        subdivision_display_for_row(pr)
                        if subdivision_display_for_row is not None
                        else None
                    )
                    vals = row_to_resultat_cells(
                        pr,
                        n_global,
                        n_interne,
                        subdivision_arme_display=sub_disp,
                        grades=grades,
                    )
                    for col_idx, val in enumerate(vals, start=1):
                        write_literal_cell_value(out_ws, cur, col_idx, val)
                        cell = writable_cell(out_ws, cur, col_idx)
                        _apply_cell_style(tpl_ws.cell(r_dat, col_idx), cell)
                    fit_wrapped_row(out_ws, cur, ncol, min_height=h_dat)
                    cur += 1

    out_ws.print_area = f'A1:{get_column_letter(ncol)}{max(layout.header_row, cur - 1)}'
    tpl_wb.close()
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    export_phase("write", total=1, detail="Écriture du document final")
    out_wb.save(dest)
    export_phase("finalize", detail="Confirmation du fichier et historique")


@tracked_export
def export_simple_fallback(
    destination: Path,
    sections_data: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]],
    *,
    subdivision_display_for_row: Callable[[ListeGeneraleRow], str] | None = None,
    grades=None,
) -> None:
    """Sans maquette : classeur minimal (même logique de rubriques que l'export fidèle)."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    ncols = len(RESULTAT_HEADERS)
    wb = Workbook()
    ws = wb.active
    ws.title = "Liste_SME"
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill_hdr = PatternFill("solid", fgColor="D9E2F3")
    fill_sec1 = PatternFill("solid", fgColor="E8E8E8")
    fill_sec2 = PatternFill("solid", fgColor="DCE6F1")

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    write_cell_value(ws, row, 1, RESULTAT_DOCUMENT_TITLE)
    writable_cell(ws, row, 1).font = Font(bold=True, size=14)
    row += 2
    for col, title in enumerate(RESULTAT_HEADERS, start=1):
        write_cell_value(ws, row, col, title)
        cell = writable_cell(ws, row, col)
        cell.font = Font(bold=True, size=10)
        cell.fill = fill_hdr
        cell.border = border
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    row += 1
    n_global = 0
    export_phase('render', total=sum(len(people) for _, _, _, blocks in sections_data for _, people in blocks))

    for title_main, title_sub, _c, blocks in sections_data:
        for kind, payload in iter_section_rubrics(title_main, title_sub, blocks):
            if kind == "sec1":
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
                write_literal_cell_value(ws, row, 1, str(payload))
                sec1_cell = writable_cell(ws, row, 1)
                sec1_cell.font = Font(bold=True, size=11)
                sec1_cell.fill = fill_sec1
                ws.row_dimensions[row].height = 22
                row += 1
            elif kind == "sec2":
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
                write_literal_cell_value(ws, row, 1, str(payload))
                cell = writable_cell(ws, row, 1)
                cell.fill = fill_sec2
                cell.font = Font(bold=True, size=10)
                ws.row_dimensions[row].height = 14
                row += 1
            elif kind == "data":
                n_interne = 0
                for pr in export_rows(payload):
                    n_global += 1
                    n_interne += 1
                    sub_disp = (
                        subdivision_display_for_row(pr)
                        if subdivision_display_for_row is not None
                        else None
                    )
                    cells = row_to_resultat_cells(
                        pr,
                        n_global,
                        n_interne,
                        subdivision_arme_display=sub_disp,
                        grades=grades,
                    )
                    for col_idx, val in enumerate(cells, start=1):
                        write_literal_cell_value(ws, row, col_idx, val)
                        writable_cell(ws, row, col_idx).border = border
                        writable_cell(ws, row, col_idx).alignment = Alignment(vertical="top", wrap_text=True)
                    ws.row_dimensions[row].height = 18
                    row += 1

    for col in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    configure_table_print(ws, header_row=3, last_row=row - 1, columns=ncols)
    export_phase("write", total=1, detail="Écriture du document final")
    wb.save(destination)
    export_phase("finalize", detail="Confirmation du fichier et historique")
