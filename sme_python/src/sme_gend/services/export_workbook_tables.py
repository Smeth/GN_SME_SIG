"""Tables Excel reconstruites : aucune donnée ni fusion héritée des anciens mois."""
from collections import Counter
from datetime import date, datetime
from math import ceil
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sme_gend.services.excel_style_helpers import grade_to_abbrev

NAVY = "18345B"
LINE = Side(style="thin", color="CFD7E3")


def table_sheet(wb, name, title, headers, rows, *, mois, header_row=3, widths=None):
    from sme_gend.services.export_progress import export_rows
    if name in wb.sheetnames:
        index = wb.sheetnames.index(name)
        wb.remove(wb[name])
        ws = wb.create_sheet(name, index)
    else:
        ws = wb.create_sheet(name)
    last_col = get_column_letter(len(headers))
    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = title
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28
    month_row = 3 if header_row > 3 else 2
    ws.merge_cells(start_row=month_row, start_column=1, end_row=month_row, end_column=len(headers))
    ws.cell(month_row, 1, f"Mois SME : {mois}")
    ws.cell(month_row, 1).font = Font(name="Calibri", size=11, bold=True, color=NAVY)
    for col, header in enumerate(headers, 1):
        cell = ws.cell(header_row, col, header)
        cell.font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = (widths[col - 1] if widths and col <= len(widths) else 19)
    ws.row_dimensions[header_row].height = 42
    body_font = Font(name="Calibri", size=10)
    body_border = Border(left=LINE, right=LINE, top=LINE, bottom=LINE)
    body_alignment = Alignment(vertical="top", wrap_text=True)
    stripe_fill = PatternFill("solid", fgColor="F0F4F8")
    date_columns = [any(part in str(header).casefold() for part in ("date", "début", "fin")) for header in headers]
    rows = list(rows)
    for row_index, values in enumerate(export_rows(rows, label=name), header_row + 1):
        required_lines = 1
        for col, value in enumerate(values, 1):
            if col > len(headers):
                raise ValueError(f"Trop de colonnes pour {name} : {len(values)} / {len(headers)}")
            is_date = date_columns[col - 1]
            if is_date and isinstance(value, str) and len(value.strip()) in (8, 10):
                from sme_gend.services.access_date_parse import parse_access_date
                parsed = parse_access_date(value)
                if parsed is not None:
                    value = parsed
            cell = ws.cell(row_index, col, value)
            if isinstance(value, str):
                cell.data_type = "s"
            cell.font = body_font
            cell.border = body_border
            cell.alignment = body_alignment
            if row_index % 2 == 0:
                cell.fill = stripe_fill
            if isinstance(value, (date, datetime)):
                cell.number_format = "dd/mm/yyyy"
            text = value.strftime("%d/%m/%Y") if isinstance(value, (date, datetime)) else str(value or "")
            available = max(4, int(ws.column_dimensions[get_column_letter(col)].width) - 2)
            required_lines = max(required_lines, sum(max(1, ceil(len(line) / available)) for line in text.split("\n")))
        ws.row_dimensions[row_index].height = min(409, max(36, required_lines * 15 + 8))
    end = max(header_row + len(rows), header_row)
    ws.auto_filter.ref = f"A{header_row}:{last_col}{end}"
    ws.freeze_panes = f"A{header_row + 1}"
    ws.print_title_rows = f"1:{header_row}"
    ws.print_area = f"A1:{last_col}{end}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3 if len(headers) > 12 else ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.oddFooter.center.text = "Page &P / &N"
    ws.oddFooter.right.text = mois
    return ws


def export_details(repo):
    names = {"missions": "load_missions", "malades": "load_malades",
             "deserteurs": "load_deserteurs", "stages_loc": "load_stages_loc",
             "stages_ex": "load_stages_ex", "stages_internal": "load_stages_internal",
             "conges": "load_latest_conges_for_period", "conges_by_type": "load_latest_conges_by_type_for_period",
             "positions": "load_position_events_for_period"}
    return {key: getattr(repo, method, lambda: {})() for key, method in names.items()}


def personnel_cells(row, number, gmap):
    return [number, grade_to_abbrev(row.grade, gmap), row.noms or "", row.structure or ""]


def detail_cells(row, details):
    """Intitulé, lieu, référence, début, fin, observations — depuis le vrai registre."""
    p = (row.position_admin or "").strip().casefold().replace(" ", "")
    key = row.satellite_join_key()
    if p == "mission":
        d = details["missions"].get(key)
        if d:
            return [d.nature, d.lieu_mission, d.refnds, d.date_depart, None, row.obs]
    if p == "malade":
        d = details["malades"].get(key)
        if d:
            return [d.pathologie, None, None, d.date_constat, None, d.obs]
    stage_key = {"stagelocal": "stages_loc", "stageex": "stages_ex", "stageinterne": "stages_internal"}.get(p)
    if stage_key:
        d = details[stage_key].get(key)
        if d:
            return [d.nature_stage, d.lieu_stage, d.reference_dossier, d.date_debut_fin, None, row.obs]
    if p.startswith(("congé", "conge")):
        from sme_gend.services.agent_conge_service import padmin_to_conge_type
        expected = padmin_to_conge_type(row.position_admin)
        d = details.get("conges_by_type", {}).get((key, expected))
        if d is None:
            d = details["conges"].get(key)
            if d is not None and getattr(d, "type_conge", expected) != expected:
                d = None
        if d:
            return [d.type_label, None, None, d.date_debut, d.date_fin, d.observation]
    if p == "déserteur":
        d = details["deserteurs"].get(key)
        if d:
            return [row.position_admin, None, d.reference_dossier, d.dat_desertion, None, row.obs]
    d = details["positions"].get((key, p))
    if d:
        return [row.position_admin, d[2], d[3], d[0], d[1], d[4]]
    return [row.position_admin, None, None, None, None, row.obs]


def grade_summary(wb, name, rows, *, mois, gmap, grade_order, dpf=False):
    from sme_gend.services.numerique_padmin import is_present_strict
    codes = list(dict.fromkeys([*grade_order, *(grade_to_abbrev(r.grade, gmap) or "Non renseigné" for r in rows)]))
    def counts(subset):
        counter = Counter(grade_to_abbrev(r.grade, gmap) or "Non renseigné" for r in subset)
        return [counter[g] for g in codes] + [len(subset)]
    groups = {}
    for row in rows:
        from sme_gend.services.sme_dpf_scope import is_dpf_control_row
        if dpf and is_dpf_control_row(row):
            label = "Contrôle spécial DPF"
        elif is_present_strict(row):
            label = "Présents"
        else:
            label = row.position_admin or "Position non renseignée"
        groups.setdefault(label, []).append(row)
    output = [["Inscrits", *counts(rows)]]
    for label, subset in sorted(groups.items(), key=lambda item: (item[0] != "Présents", item[0])):
        output.append([label, *counts(subset)])
    output.append(["Total ventilé", *counts(rows)])
    ws = table_sheet(wb, name, "SITUATION NUMÉRIQUE DES EFFECTIFS", ["Position administrative", *codes, "Total"],
                     output, mois=mois, header_row=7, widths=[30] + [9] * (len(codes) + 1))
    return ws
