"""Export Excel missions et désertions (listes affichées)."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import DeserteurDetail, MissionDetail
from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.malade_punis_export import _autosize


@tracked_export
def export_missions_workbook(
    dest: Path,
    *,
    rows: list[ListeGeneraleRow],
    details: dict[str, MissionDetail],
    grades=None,
    settings=None,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Missions"
    write_literal_cell_value(ws, 1, 1, "Missions")
    writable_cell(ws, 1, 1).font = Font(bold=True)
    headers = (
        "Id liaison",
        "Nom",
        "Grade",
        "Structure",
        "Padmin",
        "Nature",
        "Lieu",
        "Réf. dossier",
        "Date départ",
    )
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 2, col, h)
        writable_cell(ws, 2, col).font = Font(bold=True)
    for i, r in enumerate(export_rows(rows), start=3):
        key = r.satellite_join_key()
        d = details.get(key) or (details.get(r.id or "") if r.id else None)
        write_literal_cell_value(ws, i, 1, key)
        write_literal_cell_value(ws, i, 2, r.noms or "")
        write_literal_cell_value(ws, i, 3, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i, 4, r.structure or "")
        write_literal_cell_value(ws, i, 5, r.position_admin or "")
        write_literal_cell_value(ws, i, 6, (d.nature if d else "") or "")
        write_literal_cell_value(ws, i, 7, (d.lieu_mission if d else "") or "")
        write_literal_cell_value(ws, i, 8, (d.refnds if d else "") or "")
        write_literal_cell_value(ws, i, 9, (d.date_depart if d else "") or "")
    _autosize(ws, headers, 3, max(3, len(rows) + 2))
    export_phase("write", total=1, detail="Écriture du document final")
    wb.save(dest)
    export_phase("finalize", detail="Confirmation du fichier et historique")
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, rows, doc_type="missions", settings=settings)


@tracked_export
def export_desertions_workbook(
    dest: Path,
    *,
    rows: list[ListeGeneraleRow],
    details: dict[str, DeserteurDetail],
    grades=None,
    settings=None,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Désertions"
    write_literal_cell_value(ws, 1, 1, "Désertions")
    writable_cell(ws, 1, 1).font = Font(bold=True)
    headers = (
        "Id liaison",
        "Nom",
        "Grade",
        "Structure",
        "Padmin",
        "Date désertion",
        'Réf/note',
    )
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 2, col, h)
        writable_cell(ws, 2, col).font = Font(bold=True)
    for i, r in enumerate(export_rows(rows), start=3):
        key = r.satellite_join_key()
        d = details.get(key) or (details.get(r.id or "") if r.id else None)
        write_literal_cell_value(ws, i, 1, key)
        write_literal_cell_value(ws, i, 2, r.noms or "")
        write_literal_cell_value(ws, i, 3, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i, 4, r.structure or "")
        write_literal_cell_value(ws, i, 5, r.position_admin or "")
        write_literal_cell_value(ws, i, 6, (d.dat_desertion if d else "") or "")
        write_literal_cell_value(ws, i, 7, (d.reference_dossier if d else '') or '')
    _autosize(ws, headers, 3, max(3, len(rows) + 2))
    export_phase("write", total=1, detail="Écriture du document final")
    wb.save(dest)
    export_phase("finalize", detail="Confirmation du fichier et historique")
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, rows, doc_type="desertions", settings=settings)
