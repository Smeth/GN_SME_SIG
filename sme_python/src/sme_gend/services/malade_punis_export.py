"""Export Excel des listes Malades & Punis affichées."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

from pathlib import Path

from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.services.grade_label_format import format_grade_display


def _autosize(ws, headers: tuple[str, ...], first_row: int, last_row: int) -> None:
    ws.auto_filter.ref = f"A{first_row - 1}:{get_column_letter(len(headers))}{last_row}"
    ws.freeze_panes = f"A{first_row}"
    for col in range(1, len(headers) + 1):
        width = len(headers[col - 1])
        for i in range(first_row, last_row + 1):
            v = ws.cell(row=i, column=col).value
            if v is not None:
                width = max(width, len(str(v)))
        ws.column_dimensions[get_column_letter(col)].width = min(60, width + 2)
    from sme_gend.services.excel_style_helpers import configure_table_print
    configure_table_print(ws, header_row=first_row - 1, last_row=last_row, columns=len(headers))


@tracked_export
def export_malades_workbook(
    dest: Path,
    *,
    rows: list[ListeGeneraleRow],
    details: dict[str, MaladeDetail],
    grades=None,
    settings=None,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Malades"
    write_literal_cell_value(ws, 1, 1, "Malades")
    writable_cell(ws, 1, 1).font = Font(bold=True)

    headers = (
        "Id liaison", "Nom", "Grade", "Structure", "Padmin actuel",
        "Date constat", "Pathologie", "Observations",
    )
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 2, col, h)
        writable_cell(ws, 2, col).font = Font(bold=True)

    for i, r in enumerate(export_rows(rows), start=3):
        key = r.satellite_join_key()
        d = details.get(key) or details.get(r.id or "")
        write_literal_cell_value(ws, i, 1, key)
        write_literal_cell_value(ws, i, 2, r.noms or "")
        write_literal_cell_value(ws, i, 3, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i, 4, r.structure or "")
        write_literal_cell_value(ws, i, 5, r.position_admin or "")
        write_literal_cell_value(ws, i, 6, (d.date_constat if d else "") or "")
        write_literal_cell_value(ws, i, 7, (d.pathologie if d else "") or "")
        write_literal_cell_value(ws, i, 8, (d.obs if d else "") or "")

    _autosize(ws, headers, 3, max(3, len(rows) + 2))
    export_phase("write", total=1, detail="Écriture du document final")
    wb.save(dest)
    export_phase("finalize", detail="Confirmation du fichier et historique")
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, rows, doc_type="malades", settings=settings)


@tracked_export
def export_punis_workbook(dest: Path, *, rows: list[ListeGeneraleRow], grades=None, settings=None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Punis"
    write_literal_cell_value(ws, 1, 1, "Punis & détention")
    writable_cell(ws, 1, 1).font = Font(bold=True)

    headers = ("Id liste", "Nom", "Grade", "Structure", "Padmin actuel", "Motif")
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 2, col, h)
        writable_cell(ws, 2, col).font = Font(bold=True)

    for i, r in enumerate(export_rows(rows), start=3):
        write_literal_cell_value(ws, i, 1, r.id or "")
        write_literal_cell_value(ws, i, 2, r.noms or "")
        write_literal_cell_value(ws, i, 3, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i, 4, r.structure or "")
        write_literal_cell_value(ws, i, 5, r.position_admin or "")
        write_literal_cell_value(ws, i, 6, r.obs or "")

    _autosize(ws, headers, 3, max(3, len(rows) + 2))
    export_phase("write", total=1, detail="Écriture du document final")
    wb.save(dest)
    export_phase("finalize", detail="Confirmation du fichier et historique")
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, rows, doc_type="punis", settings=settings)
