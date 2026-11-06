"""Export Excel liste générale — modèle « SITUATION NOMINATIVE DES EFFECTIFS »."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from dataclasses import replace
import math
from sme_gend.services.excel_style_helpers import write_literal_cell_value

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.liste_generale_print import (
    LISTE_GENERALE_OFFICIAL_TITLE,
    OFFICIAL_CELL_PT,
    OFFICIAL_EXCEL_COLS,
    OFFICIAL_FONT,
    OFFICIAL_HDR_PT,
    OFFICIAL_META_PT,
    OFFICIAL_TITLE_PT,
    official_liste_cell_value,
    resolve_print_columns,
)

# Rétrocompatibilité (édition SME, imports externes).
EXPORT_COLS: list[tuple[str, str, int]] = list(OFFICIAL_EXCEL_COLS)

_THIN = Side(style="thin", color="000000")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_TITLE_FONT = Font(name=OFFICIAL_FONT, size=OFFICIAL_TITLE_PT, bold=True)
_META_FONT = Font(name=OFFICIAL_FONT, size=OFFICIAL_META_PT, italic=True)
_HDR_FONT = Font(name=OFFICIAL_FONT, size=OFFICIAL_HDR_PT, bold=True)
_CELL_FONT = Font(name=OFFICIAL_FONT, size=OFFICIAL_CELL_PT)
_HDR_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_CELL_ALIGN = Alignment(vertical="top", wrap_text=True)
_CENTER = Alignment(horizontal="center", vertical="top")


def _safe_sheet_name(name: str) -> str:
    for ch in r"\/?*[]:":
        name = name.replace(ch, "-")
    name = name.strip()
    return name[:31] if name else "Feuille"


def resolve_excel_columns(keys: Sequence[str] | None, hidden_keys: set[str] | None = None) -> list[tuple[str, str, int]]:
    """Colonnes Excel officielles, limitées aux clés demandées."""
    if not keys:
        hidden = set(hidden_keys or ())
        if {"date_naissance", "lieu_naissance"} <= hidden:
            hidden.add("naissance")
        return [col for col in OFFICIAL_EXCEL_COLS if col[0] not in hidden]
    chosen_keys = {col[0] for col in resolve_print_columns(keys)}
    chosen = [col for col in OFFICIAL_EXCEL_COLS if col[0] in chosen_keys]
    return chosen or list(OFFICIAL_EXCEL_COLS)


def _write_official_sheet_header(
    ws,
    *,
    mois_label: str | None,
    n_rows: int,
    columns: Sequence[tuple[str, str, int]] | None = None,
) -> tuple[int, int]:
    """Écrit titre + ligne de période (si connue) + ligne d'en-têtes.

    Retourne ``(ligne d'en-têtes, index de la 1re ligne de données)``.
    Le PDF n'affiche pas la période ; l'Excel officiel, si.
    """
    cols = list(columns or OFFICIAL_EXCEL_COLS)
    ncol = len(cols)
    last_col = get_column_letter(ncol)
    _ = n_rows
    ws.merge_cells(f"A1:{last_col}1")
    ws.row_dimensions[1].height = 18
    t = ws["A1"]
    t.value = LISTE_GENERALE_OFFICIAL_TITLE
    t.font = _TITLE_FONT
    t.alignment = Alignment(horizontal="center", vertical="center")

    hdr_row = 2
    mois = (mois_label or "").strip()
    if mois:
        ws.merge_cells(f"A{hdr_row}:{last_col}{hdr_row}")
        ws.row_dimensions[hdr_row].height = 12
        m = ws.cell(hdr_row, 1, f"Mois : {mois}")
        m.font = _META_FONT
        m.alignment = Alignment(horizontal="center", vertical="center")
        hdr_row += 1

    ws.row_dimensions[hdr_row].height = 36
    for ci, (_key, header, width) in enumerate(cols, 1):
        c = ws.cell(hdr_row, ci, header)
        c.font = _HDR_FONT
        c.alignment = _HDR_ALIGN
        c.border = _BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width

    return hdr_row, hdr_row + 1


def _apply_official_print_setup(
    ws,
    *,
    hdr_row: int,
    last_row: int,
    ncol: int | None = None,
) -> None:
    """A4 paysage — en-têtes de colonnes sur la 1re page uniquement (pas de lignes à répéter)."""
    ncol = ncol or len(OFFICIAL_EXCEL_COLS)
    last_col = get_column_letter(ncol)
    ws.print_title_rows = None
    ws.print_title_cols = None
    if last_row >= hdr_row:
        ws.print_area = f"A1:{last_col}{last_row}"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins = PageMargins(
        left=0.35,
        right=0.35,
        top=0.35,
        bottom=0.35,
        header=0.2,
        footer=0.2,
    )


def _write_official_data_rows(
    ws,
    rows: list[ListeGeneraleRow],
    *,
    flat: Sequence[SubdivisionFlatRow] | None,
    start_row: int,
    line_offset: int = 0,
    columns: Sequence[tuple[str, str, int]] | None = None,
    grades: Sequence[GradeRefRow] | None = None,
    hidden_keys: set[str] | None = None,
) -> None:
    cols = list(columns or OFFICIAL_EXCEL_COLS)
    for ri, row in enumerate(rows, start_row):
        line_no = line_offset + (ri - start_row) + 1
        hidden = hidden_keys or set()
        masked = {key: None for key in ("date_naissance", "lieu_naissance") if key in hidden}
        display_row = replace(row, **masked) if masked else row
        height = 16
        for ci, (key, _, _) in enumerate(cols, 1):
            val = official_liste_cell_value(
                display_row, key, flat=flat, line_no=line_no, grades=grades
            )
            write_literal_cell_value(ws, ri, ci, val)
            c = ws.cell(ri, ci)
            if key != "idx":
                lines = sum(max(1, math.ceil(len(part) / max(1, cols[ci - 1][2] - 1))) for part in str(val).split("\n"))
                height = max(height, lines * OFFICIAL_CELL_PT * 1.5)
            c.font = _CELL_FONT
            c.border = _BORDER
            c.alignment = _CENTER if key == "idx" else _CELL_ALIGN
        ws.row_dimensions[ri].height = height


def export_liste_filtered(
    rows: list[ListeGeneraleRow],
    path: Path,
    *,
    hidden_keys: set[str] | None = None,
    flat: Sequence[SubdivisionFlatRow] | None = None,
    grades: Sequence[GradeRefRow] | None = None,
    sheet_title: str = "Situation nominative",
    log_mois: str | None = None,
    log_doc_type: str = "liste_personnel",
    mois_label: str | None = None,
    column_keys: Sequence[str] | None = None,
    settings=None,
) -> int:
    """Export officiel. ``column_keys`` limite les colonnes ; vide = modèle complet."""
    cols = resolve_excel_columns(column_keys, hidden_keys)
    wb = Workbook()
    ws = wb.active
    ws.title = _safe_sheet_name(sheet_title)
    hdr_row, data_start = _write_official_sheet_header(
        ws, mois_label=mois_label or log_mois, n_rows=len(rows), columns=cols
    )
    _write_official_data_rows(
        ws, rows, flat=flat, start_row=data_start, columns=cols, grades=grades,
        hidden_keys=hidden_keys if not column_keys else None
    )
    last_row = data_start + max(len(rows), 1) - 1
    _apply_official_print_setup(ws, hdr_row=hdr_row, last_row=last_row, ncol=len(cols))
    wb.save(str(path))
    from sme_gend.services.doc_gen_log import log_doc_generation

    log_doc_generation(
        doc_type=log_doc_type, settings=settings,
        format="xlsx",
        count=len(rows),
        dest_path=str(path),
        mois=log_mois,
    )
    return len(rows)


def export_liste_by_padmin(
    rows: list[ListeGeneraleRow],
    path: Path,
    *,
    hidden_keys: set[str] | None = None,
    flat: Sequence[SubdivisionFlatRow] | None = None,
    grades: Sequence[GradeRefRow] | None = None,
    log_mois: str | None = None,
    mois_label: str | None = None,
    settings=None,
) -> dict[str, int]:
    """Une feuille par position administrative (même modèle officiel par feuille)."""
    cols = resolve_excel_columns(None, hidden_keys)

    groups: dict[str, list[ListeGeneraleRow]] = {}
    for r in rows:
        key = (r.position_admin or "").strip() or "(Non renseigné)"
        groups.setdefault(key, []).append(r)

    wb = Workbook()
    default = wb.active
    counts: dict[str, int] = {}
    first = True
    line_offset = 0

    for padmin in sorted(groups.keys(), key=str.lower):
        padmin_rows = groups[padmin]
        if first:
            ws = default
            ws.title = _safe_sheet_name(padmin)
            first = False
        else:
            ws = wb.create_sheet(_safe_sheet_name(padmin))
        hdr_row, data_start = _write_official_sheet_header(
            ws, mois_label=mois_label or log_mois, n_rows=len(padmin_rows), columns=cols
        )
        _write_official_data_rows(
            ws, padmin_rows, flat=flat, start_row=data_start,
            line_offset=line_offset, grades=grades, columns=cols, hidden_keys=hidden_keys,
        )
        last_row = data_start + max(len(padmin_rows), 1) - 1
        _apply_official_print_setup(ws, hdr_row=hdr_row, last_row=last_row, ncol=len(cols))
        line_offset += len(padmin_rows)
        counts[padmin] = len(padmin_rows)

    if not groups:
        default.title = "Vide"
        _write_official_sheet_header(default, mois_label=mois_label or log_mois, n_rows=0)

    ws_recap = wb.create_sheet("Récapitulatif")
    ws_recap.cell(1, 1, "Position administrative").font = Font(bold=True, name="Times New Roman", size=10)
    ws_recap.cell(1, 2, "Effectif").font = Font(bold=True, name="Times New Roman", size=10)
    ws_recap.column_dimensions["A"].width = 28
    ws_recap.column_dimensions["B"].width = 12
    total = 0
    for ri, (padmin, n) in enumerate(sorted(counts.items(), key=lambda x: -x[1]), 2):
        ws_recap.cell(ri, 1, padmin).font = Font(name="Times New Roman", size=10)
        ws_recap.cell(ri, 2, n).font = Font(name="Times New Roman", size=10)
        total += n
    last = len(counts) + 2
    ws_recap.cell(last, 1, "TOTAL").font = Font(bold=True, name="Times New Roman", size=10)
    ws_recap.cell(last, 2, total).font = Font(bold=True, name="Times New Roman", size=10)

    wb.save(str(path))
    from sme_gend.services.doc_gen_log import log_doc_generation

    log_doc_generation(
        doc_type="liste_padmin", settings=settings,
        format="xlsx",
        count=total,
        dest_path=str(path),
        mois=log_mois,
        meta={"feuilles": len(counts)},
    )
    return counts
