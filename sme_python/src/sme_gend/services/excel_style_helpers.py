"""Styles Excel — copie depuis gabarit, bordures tableau, abréviations grade."""

from __future__ import annotations

import re
from copy import copy
from dataclasses import dataclass
from collections.abc import Sequence

from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.models.grade_ref import GradeRefRow

_SECTION_TITLE_RE = re.compile(r"^[IVXLC]+-\s", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CellStyleSnapshot:
    font: object
    border: object
    fill: object
    number_format: str
    alignment: object


def snapshot_cell(ws: Worksheet, row: int, col: int) -> CellStyleSnapshot:
    c = ws.cell(row=row, column=col)
    return CellStyleSnapshot(
        font=copy(c.font),
        border=copy(c.border),
        fill=copy(c.fill),
        number_format=c.number_format,
        alignment=copy(c.alignment),
    )


def apply_cell_style(ws: Worksheet, row: int, col: int, snap: CellStyleSnapshot) -> None:
    c = ws.cell(row=row, column=col)
    if isinstance(c, MergedCell):
        return
    c.font = copy(snap.font)
    c.border = copy(snap.border)
    c.fill = copy(snap.fill)
    c.number_format = snap.number_format
    c.alignment = copy(snap.alignment)


def snapshot_row_styles(ws: Worksheet, row: int, col_start: int, col_end: int) -> list[CellStyleSnapshot]:
    return [snapshot_cell(ws, row, c) for c in range(col_start, col_end + 1)]


def apply_row_styles(
    ws: Worksheet,
    row: int,
    styles: Sequence[CellStyleSnapshot],
    *,
    col_start: int = 1,
) -> None:
    for i, snap in enumerate(styles):
        apply_cell_style(ws, row, col_start + i, snap)


def clear_row_values(ws: Worksheet, row: int, col_start: int, col_end: int) -> None:
    for c in range(col_start, col_end + 1):
        clear_cell_value(ws, row, c)


def cell_write_anchor(ws: Worksheet, row: int, col: int) -> tuple[int, int]:
    """Coordonnée writable (coin haut-gauche si la cellule fait partie d'une fusion)."""
    cell = ws.cell(row=row, column=col)
    if not isinstance(cell, MergedCell):
        return row, col
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
            return merged.min_row, merged.min_col
    return row, col


def write_cell_value(ws: Worksheet, row: int, col: int, value) -> None:
    """Écrit une valeur sans erreur sur cellule fusionnée (MergedCell read-only)."""
    r, c = cell_write_anchor(ws, row, col)
    ws.cell(row=r, column=c).value = value


def write_literal_cell_value(ws: Worksheet, row: int, col: int, value) -> None:
    """User data stays text, including strings starting with '='; formulas use write_cell_value."""
    r, c = cell_write_anchor(ws, row, col)
    cell = ws.cell(row=r, column=c)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def fit_wrapped_row(ws, row: int, columns: int, *, min_height: float = 15) -> None:
    """Estimate wrapped height while preserving fonts, borders and alignment."""
    import textwrap
    from openpyxl.utils import get_column_letter
    height = max(min_height, ws.row_dimensions[row].height or 0)
    for ci in range(1, columns + 1):
        cell = ws.cell(row, ci)
        if isinstance(cell, MergedCell):
            continue
        alignment = copy(cell.alignment)
        alignment.wrap_text = True
        cell.alignment = alignment
        end = ci
        for merged in ws.merged_cells.ranges:
            if merged.min_row == merged.max_row == row and merged.min_col == ci:
                end = merged.max_col
                break
        width = max(1, sum(ws.column_dimensions[get_column_letter(col)].width or 12
                           for col in range(ci, end + 1)) - 1)
        text = "" if cell.value is None else str(cell.value)
        lines = sum(max(1, len(textwrap.wrap(part, width=max(1, int(width))))) for part in text.split("\n"))
        height = max(height, lines * (cell.font.sz or 11) * 1.5)
    ws.row_dimensions[row].height = height


def configure_table_print(ws, *, header_row: int, last_row: int, columns: int) -> None:
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.properties import PageSetupProperties
    ws.print_area = f"A1:{get_column_letter(columns)}{max(header_row, last_row)}"
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    for ri in range(header_row, last_row + 1):
        fit_wrapped_row(ws, ri, columns)



def writable_cell(ws: Worksheet, row: int, col: int):
    """Retourne la cellule writable (coin haut-gauche si fusion)."""
    r, c = cell_write_anchor(ws, row, col)
    return ws.cell(row=r, column=c)


def read_cell_value(ws: Worksheet, row: int, col: int):
    """Lit la valeur via la cellule ancre (évite None sur MergedCell)."""
    r, c = cell_write_anchor(ws, row, col)
    return ws.cell(row=r, column=c).value


def clear_cell_value(ws: Worksheet, row: int, col: int) -> None:
    write_cell_value(ws, row, col, None)


def unmerge_from_row(ws: Worksheet, min_row: int) -> None:
    to_remove = [str(m) for m in ws.merged_cells.ranges if m.min_row >= min_row]
    for m in to_remove:
        ws.unmerge_cells(m)


def apply_data_block_styles(
    ws: Worksheet,
    *,
    template_row: int,
    data_start: int,
    n_rows: int,
    col_start: int,
    col_end: int,
) -> None:
    """Applique le style d'une ligne modèle à toutes les lignes de données."""
    if n_rows <= 0:
        return
    styles = snapshot_row_styles(ws, template_row, col_start, col_end)
    for i in range(n_rows):
        apply_row_styles(ws, data_start + i, styles, col_start=col_start)


def build_grade_abbrev_map(grades: Sequence[GradeRefRow]) -> dict[str, str]:
    """Mappe libellé ou code (insensible casse) → code court TabGrade."""
    out: dict[str, str] = {}
    for g in grades:
        code = (g.code or "").strip()
        lib = (g.libelle or "").strip()
        if code:
            out[code.casefold()] = code
        if lib:
            out[lib.casefold()] = code or lib
    return out


def grade_to_abbrev(grade: str | None, abbrev_map: dict[str, str]) -> str:
    g = (grade or "").strip()
    if not g:
        return ""
    return abbrev_map.get(g.casefold(), g)


def is_section_title(text: str) -> bool:
    return bool(_SECTION_TITLE_RE.match((text or "").strip()))
