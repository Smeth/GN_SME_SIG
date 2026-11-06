"""Export Excel des gains & pertes affichés."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.services.grade_label_format import format_grade_display


def export_gain_perte_workbook(
    dest: Path,
    *,
    rows: list[GainPerteRow],
    mois_ym: str | None,
    title_suffix: str = "",
    grades=None,
    settings=None,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Gains_Pertes"

    mois_label = (mois_ym or "").strip() or "Tous les mois"
    title = f"Gains & Pertes — {mois_label}"
    if title_suffix:
        title = f"{title} ({title_suffix})"
    write_literal_cell_value(ws, 1, 1, title)
    writable_cell(ws, 1, 1).font = Font(bold=True)

    headers = ("ID", "Grade", "Nom", "Objet", "Mois SME", "Cdsme", "Date réf.", "Observation")
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 2, col, h)
        writable_cell(ws, 2, col).font = Font(bold=True)

    for i, r in enumerate(rows, start=3):
        write_literal_cell_value(ws, i, 1, str(r.id or ""))
        write_literal_cell_value(ws, i, 2, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i, 3, r.nom or "")
        write_literal_cell_value(ws, i, 4, r.objet_display)
        write_literal_cell_value(ws, i, 5, r.effective_mois_ym() or "")
        write_literal_cell_value(ws, i, 6, r.cdsme or "")
        write_literal_cell_value(ws, i, 7, r.date_display if r.date_reference else "")
        write_literal_cell_value(ws, i, 8, r.observ_display)

    last_row = max(3, len(rows) + 2)
    ws.auto_filter.ref = f"A2:H{last_row}"
    ws.freeze_panes = "A3"
    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        width = len(headers[col - 1])
        for i in range(3, last_row + 1):
            v = ws.cell(row=i, column=col).value
            if v is not None:
                width = max(width, len(str(v)))
        ws.column_dimensions[letter].width = min(60, width + 2)

    from sme_gend.services.excel_style_helpers import configure_table_print
    configure_table_print(ws, header_row=2, last_row=last_row, columns=len(headers))
    wb.save(dest)
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, rows, doc_type="gains_pertes", settings=settings, mois=mois_ym or "")
