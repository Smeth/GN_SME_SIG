"""Populate the official workbook, retaining its layout and live formulae."""
from collections import Counter, defaultdict, deque
from copy import copy
from pathlib import Path
import re
from math import ceil

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties

from sme_gend.services.clean_sme_exports import list_cells
from sme_gend.services.excel_style_helpers import build_grade_abbrev_map, grade_to_abbrev
from sme_gend.services.export_workbook_tables import detail_cells, personnel_cells
from sme_gend.services.numerique_padmin import is_present_strict, row_predicate_for_rubrique
from sme_gend.services.position_codes import LABELS, position_code, normalized_label
from sme_gend.services.safe_paths import atomic_destination


def _put(ws, row, column, value):
    cell = ws.cell(row, column)
    if isinstance(cell, MergedCell):
        if value is not None:
            raise ValueError(f"Cellule de données fusionnée : {ws.title}!{cell.coordinate}")
        return
    cell.value = value
    if isinstance(value, str):
        cell.data_type = 's'  # Database text must never become an Excel formula.
    cell.comment = None
    cell.hyperlink = None


def _style_row(ws, target, source, columns):
    for col in range(1, columns + 1):
        ws.cell(target, col)._style = copy(ws.cell(source, col)._style)
    ws.row_dimensions[target].height = ws.row_dimensions[source].height


def _body(ws, values, *, start, columns):
    """Clear every former record, including stray data outside the printed table."""
    old_end = ws.max_row
    styles = [copy(ws.cell(start, col)._style) for col in range(1, columns + 1)]
    height = ws.row_dimensions[start].height
    # A footer merge must not cover an expanding data block (Feuil4).
    for merged in list(ws.merged_cells.ranges):
        if start <= merged.min_row < start + len(values):
            ws.unmerge_cells(str(merged))
    for cell in list(ws._cells.values()):
        if cell.row >= start and not isinstance(cell, MergedCell):
            _put(ws, cell.row, cell.column, None)
    end = max(start, start + len(values) - 1)
    for row, record in enumerate(values, start):
        for col, value in enumerate(record, 1):
            _put(ws, row, col, value)
            ws.cell(row, col)._style = copy(styles[col - 1])
        ws.row_dimensions[row].height = height
    for row in range(start, max(old_end, end) + 1):
        ws.row_dimensions[row].hidden = row > end
    ws.print_area = f'A1:{get_column_letter(columns)}{end}'
    # Keep original counter positions, but fix incorrect grade columns/start rows.
    grade_col = 'E' if ws.title == 'LISTE GENERALE' else 'B'
    for cell in list(ws._cells.values()):
        if cell.row == 2 and cell.data_type == 'f':
            label = ws.cell(1, cell.column).value
            if isinstance(label, str) and 'COUNTIF(' in cell.value.upper():
                escaped = label.replace('"', '""')
                cell.value = f'=COUNTIF({grade_col}{start}:{grade_col}{end},"{escaped}")'
            elif 'SUM(' in cell.value.upper():
                cell.value = f'=COUNT(A{start}:A{end})'
    return end


def _presents(ws, rows, gmap):
    """Reuse the model's structure/subdivision blocks, without retaining personnel."""
    slots = defaultdict(deque)
    headings = []
    old_data = []
    for row in range(2, ws.max_row + 1):
        first = ws.cell(row, 1)
        if isinstance(first.value, (int, float)):
            key = (normalized_label(ws.cell(row, 17).value), normalized_label(ws.cell(row, 14).value))
            slots[key].append(row)
            old_data.append(row)
        elif isinstance(first.value, str) and row != 3:
            headings.append((row, (first.font.sz or 0) >= 9))
    source = old_data[0] if old_data else 4
    heading_rows = {r for r, _ in headings}
    for cell in list(ws._cells.values()):
        if cell.row >= 4 and not isinstance(cell, MergedCell):
            if cell.column > 18 or cell.row not in heading_rows:
                _put(ws, cell.row, cell.column, None)
    placed = []
    overflow = defaultdict(list)
    for person in rows:
        key = (normalized_label(person.structure), normalized_label(person.subdivision_arme))
        if slots[key]:
            placed.append((slots[key].popleft(), person))
        else:
            overflow[(person.structure or 'Structure non renseignée', person.subdivision_arme or '')].append(person)
    for row in old_data:
        ws.row_dimensions[row].hidden = True
    filled = {row for row, _ in placed}
    for index, (row, major) in enumerate(headings):
        stop = next((r for r, big in headings[index + 1:] if not major or big), ws.max_row + 1)
        ws.row_dimensions[row].hidden = not any(row < r < stop for r in filled)
    end = ws.max_row
    for (structure, subdivision), people in overflow.items():
        end += 1
        _style_row(ws, end, headings[0][0] if headings else 2, 18)
        ws.merge_cells(start_row=end, start_column=1, end_row=end, end_column=18)
        _put(ws, end, 1, ' — '.join(filter(None, (structure, subdivision))))
        for person in people:
            end += 1
            _style_row(ws, end, source, 18)
            placed.append((end, person))
    group_numbers = Counter()
    for number, (row, person) in enumerate(sorted(placed, key=lambda pair: pair[0]), 1):
        key = (person.structure, person.subdivision_arme)
        group_numbers[key] += 1
        values = list_cells(person, number, gmap)
        record = [number, group_numbers[key], *values[1:]]
        for col, value in enumerate(record, 1):
            _put(ws, row, col, value)
        ws.row_dimensions[row].hidden = False
    # Original model has an obsolete stage list in S:Y: clear it above, not only A:R.
    ws.print_area = f'A1:R{max([3, *[r for r, _ in placed]])}'
    for cell in list(ws._cells.values()):
        if cell.row == 2 and cell.data_type == 'f':
            label = ws.cell(1, cell.column).value
            if isinstance(label, str) and 'COUNTIF(' in cell.value.upper():
                cell.value = f'=COUNTIF(F4:F{max(4, end)},"{label}")'
            elif 'SUM(' in cell.value.upper():
                cell.value = f'=COUNT(A4:A{max(4, end)})'


def _movements(ws, movements, rows, gmap):
    structures = {(r.noms or '').casefold(): r.structure for r in rows}
    gains = [m for m in movements if m.is_gain]
    losses = [m for m in movements if m.is_perte]
    # Preserve the two sections, moving the loss block only when gains need space.
    loss_heading = next(r for r in range(4, ws.max_row + 1)
                        if str(ws.cell(r, 1).value or '').strip().startswith('B-'))
    gain_header = next(r for r in range(4, loss_heading) if str(ws.cell(r, 2).value or '').strip().upper() == 'GRADE')
    loss_header = next(r for r in range(loss_heading + 1, ws.max_row + 1) if str(ws.cell(r, 2).value or '').strip().upper() == 'GRADE')
    first = gain_header + 1
    loss_styles = [copy(ws.cell(loss_header + 1, c)._style) for c in range(1, 6)]
    header_styles = [copy(ws.cell(loss_header, c)._style) for c in range(1, 6)]
    section_style = copy(ws.cell(loss_heading, 1)._style)
    section_text = ws.cell(loss_heading, 1).value
    header_values = [ws.cell(loss_header, c).value for c in range(1, 6)]
    gain_styles = [copy(ws.cell(first, c)._style) for c in range(1, 6)]
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row >= loss_heading:
            ws.unmerge_cells(str(merged))
    for cell in list(ws._cells.values()):
        if cell.row >= first and not isinstance(cell, MergedCell):
            _put(ws, cell.row, cell.column, None)
    new_heading = max(loss_heading, first + len(gains))
    new_header = new_heading + loss_header - loss_heading
    ws.merge_cells(start_row=new_heading, start_column=1, end_row=new_heading, end_column=5)
    _put(ws, new_heading, 1, section_text)
    ws.cell(new_heading, 1)._style = section_style
    for c, value in enumerate(header_values, 1):
        _put(ws, new_header, c, value)
        ws.cell(new_header, c)._style = header_styles[c - 1]
    for subset, start, styles in ((gains, first, gain_styles), (losses, new_header + 1, loss_styles)):
        for number, m in enumerate(subset, 1):
            values = [number, grade_to_abbrev(m.grade, gmap), m.nom,
                      m.structure_code or structures.get((m.nom or '').casefold()), m.observ_display]
            for c, value in enumerate(values, 1):
                _put(ws, start + number - 1, c, value)
                ws.cell(start + number - 1, c)._style = copy(styles[c - 1])
    ws.print_area = f'A1:E{max(new_header + len(losses), new_header + 1)}'


def _detach_recap(ws, detaches, config):
    counts = Counter(normalized_label(r.subdivision_arme) for r in detaches)
    candidates = defaultdict(set)
    for entry in config.structures:
        for title in entry.titres_lignes[-1:]:
            candidates[normalized_label(title)].add(normalized_label(entry.sigle))
    labels = {title: next(iter(codes)) for title, codes in candidates.items() if len(codes) == 1}
    used = set()
    total_row = next(c.row for c in ws['C'] if c.data_type == 'f')
    for row in range(4, total_row):
        if isinstance(ws.cell(row, 3), MergedCell):
            continue
        label = ws.cell(row, 2).value
        if label is None and not isinstance(ws.cell(row, 1).value, str):
            continue
        text = normalized_label(label or ws.cell(row, 1).value)
        key = labels.get(text, text)
        # Parent section titles never consume counts intended for leaf structures.
        if key in counts and key not in used:
            _put(ws, row, 3, counts[key])
            used.add(key)
        elif not isinstance(ws.cell(row, 3), MergedCell):
            _put(ws, row, 3, 0 if label else None)
    extra_row = ws.max_row + 1
    extra_start = extra_row
    for key, count in sorted(counts.items()):
        if key not in used:
            _style_row(ws, extra_row, 5, 3)
            label = next((r.subdivision_arme for r in detaches if normalized_label(r.subdivision_arme) == key), None)
            for col, value in enumerate([extra_row - extra_start + 1, label or 'Non renseignée', count], 1):
                _put(ws, extra_row, col, value)
            extra_row += 1
    ws.cell(total_row, 3).value = f'=SUM(C4:C{total_row-1})' + (f'+SUM(C{extra_start}:C{extra_row-1})' if extra_row > extra_start else '')
    ws.print_area = f'A1:C{max(total_row, extra_row - 1)}'


def build_from_model(template, destination, *, mois, rows, grades, details, detaches,
                     previous_counts, movements, detaches_config):
    wb = load_workbook(template)
    try:
        required = {'LISTE GENERALE', 'NUMERIQUE', 'PAGE INTER', 'PRESENT', 'Feuil4', 'GAIN PERTE'}
        missing = required - set(wb.sheetnames)
        if missing:
            raise ValueError('Modèle SME incomplet : ' + ', '.join(sorted(missing)))
        gmap = build_grade_abbrev_map(grades)
        from sme_gend.services.numerique_excel_export import _sort_liste_rows, month_title_from_mois_actif, _prev_ym_canon
        rows = _sort_liste_rows(rows, grades=grades)
        known_grades = {str(wb['NUMERIQUE'].cell(7, c).value).casefold() for c in range(2, 20)}
        unknown = {grade_to_abbrev(r.grade, gmap) or '(vide)' for r in rows
                   if (grade_to_abbrev(r.grade, gmap) or '').casefold() not in known_grades}
        if unknown:
            raise ValueError('Grades absents du modèle SME : ' + ', '.join(sorted(unknown)))
        values = []
        for i, person in enumerate(rows, 1):
            record = list_cells(person, i, gmap)
            code = position_code(person.position_admin)
            if code:
                record[10] = LABELS[code][0]
            values.append(record)
        list_end = _body(wb['LISTE GENERALE'], values, start=4, columns=17)
        specs = [('MISSION', 'mission', 4, 8), ('MALADE', 'malade', 4, 7),
                 ('DESERTEUR', 'deserteur', 4, 5), ('CONGé ADMIN', 'conge_admin', 4, 5),
                 ('DETENTION-PRISON', 'prison_ma', 4, 7), ('NON-REJOINT', 'inst_mr', 4, 5),
                 ('STAGE-EXT', 'stage_ex', 4, 7), ('STAGE-LOC', 'stage_local', 4, 7),
                 ('Feuil4', 'stage_interne', 5, 7), ('DETACHE', 'detache', 5, 7)]
        for name, key, start, columns in specs:
            if key == 'detache':
                subset = detaches
            elif key == 'stage_interne':
                subset = [r for r in rows if position_code(r.position_admin) == key]
            else:
                subset = [r for r in rows if row_predicate_for_rubrique(key)(r)]
            output = []
            for i, person in enumerate(subset, 1):
                nature, lieu, ref, debut, fin, obs = detail_cells(person, details)
                if key.startswith('stage_'):
                    source_key = {'stage_local': 'stages_loc', 'stage_ex': 'stages_ex', 'stage_interne': 'stages_internal'}[key]
                    stage = details[source_key].get(person.satellite_join_key())
                    nature, lieu, debut = ((stage.nature_stage, stage.lieu_stage, stage.date_debut_fin)
                                           if stage else (None, None, None))
                record = personnel_cells(person, i, gmap)
                if key == 'mission': record += [nature, lieu, ref, debut]
                elif key == 'malade': record += [debut, nature, obs]
                elif key == 'deserteur': record += [debut]
                elif key.startswith('stage_'): record += [nature, lieu, debut]
                elif key == 'detache': record = record[:3] + [person.subdivision_arme, lieu, ref or person.ndecision, debut]
                elif key == 'conge_admin': record += [lieu]
                elif key == 'inst_mr': record += [obs]
                else: record += [lieu, nature, obs]
                output.append(record)
            _body(wb[name], output, start=start, columns=columns)
        _presents(wb['PRESENT'], [r for r in rows if is_present_strict(r)], gmap)
        _movements(wb['GAIN PERTE'], movements, rows, gmap)
        _detach_recap(wb['DETACHE PAR STRUCTURE'], detaches, detaches_config)
        title = month_title_from_mois_actif(mois)
        # All month labels, including the lower second page of NUMERIQUE.
        for ws in wb:
            had_month = False
            for cell in list(ws._cells.values()):
                if isinstance(cell.value, str) and re.match(r"^Mois\s+(?:de|d['’]|SME)", cell.value, re.I):
                    _put(ws, cell.row, cell.column, title)
                    had_month = True
            if not had_month and ws.title not in ('NUMERIQUE', 'PAGE INTER'):
                if ws.title == 'PRESENT' or isinstance(ws.cell(2, 1), MergedCell):
                    _put(ws, 1, 1, f'{ws.cell(1, 1).value}\n{title}')
                    alignment = copy(ws.cell(1, 1).alignment)
                    alignment.wrap_text = True
                    ws.cell(1, 1).alignment = alignment
                    ws.row_dimensions[1].height = max(30, 2.6 * (ws.cell(1, 1).font.sz or 11), ws.row_dimensions[1].height or 15)
                else:
                    _put(ws, 2, 1, title)
                    width = {'LISTE GENERALE': 17, 'MISSION': 8, 'DETACHE PAR STRUCTURE': 3,
                             'DETENTION-PRISON': 7, 'DESERTEUR': 5, 'CONGé ADMIN': 5,
                             'MALADE': 7, 'NON-REJOINT': 5, 'STAGE-LOC': 7,
                             'STAGE-EXT': 7, 'DETACHE': 7}.get(ws.title)
                    if width and not any(m.min_row <= 2 <= m.max_row for m in ws.merged_cells.ranges):
                        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)
                        alignment = copy(ws.cell(1, 1).alignment)
                        alignment.horizontal = 'center'
                        ws.cell(2, 1).alignment = alignment
        for merged in wb['PAGE INTER'].merged_cells.ranges:
            cell = wb['PAGE INTER'].cell(merged.min_row, merged.min_col)
            if cell.value:
                _put(wb['PAGE INTER'], merged.min_row, merged.min_col, f'{cell.value}\n{title}')
                alignment = copy(cell.alignment)
                alignment.wrap_text = True
                cell.alignment = alignment
                # Account for wrapping of the large original headings as well.
                sheet = wb['PAGE INTER']
                width_points = sum((sheet.column_dimensions.get(get_column_letter(c)).width
                                    if sheet.column_dimensions.get(get_column_letter(c)) else 8.43) * 5.25
                                   for c in range(merged.min_col, merged.max_col + 1))
                font_size = cell.font.sz or 11
                lines = sum(max(1, ceil(len(line) * font_size * .65 / width_points))
                            for line in str(cell.value).split('\n'))
                required_height = lines * font_size * 1.4
                last = merged.max_row
                preceding_height = sum(sheet.row_dimensions[r].height or 15 for r in range(merged.min_row, last))
                sheet.row_dimensions[last].height = max(sheet.row_dimensions[last].height or 15,
                                                        required_height - preceding_height)
        ws = wb['NUMERIQUE']
        ws['CD4'] = _prev_ym_canon(mois)
        ws['CE4'] = mois
        for row in range(5, 14):
            before = previous_counts[row - 5] if previous_counts and row - 5 < len(previous_counts) else None
            _put(ws, row, 82, before)
            ws.cell(row, 84).value = f'=IF(CD{row}="","",CE{row}-CD{row})'
        ws['CD14'] = '=IF(COUNT(CD5:CD13)=9,SUM(CD5:CD13),"")'
        ws['CF14'] = '=IF(CD14="","",CE14-CD14)'
        for col in range(2, 20):
            letter = get_column_letter(col)
            ws.cell(48, col).value = f'=SUM({letter}38:{letter}47)'
        # Extend every cross-sheet range: personnel beyond old model bounds count too.
        for sheet in wb:
            for cell in list(sheet._cells.values()):
                if cell.data_type == 'f' and "'LISTE GENERALE'!" in cell.value:
                    cell.value = re.sub(r"('LISTE GENERALE'!\$?[A-Z]+\$?)\d+:(\$?[A-Z]+\$?)\d+",
                                        lambda m: f'{m[1]}4:{m[2]}{list_end}', cell.value)
        wb.calculation = CalcProperties(calcId=0, fullCalcOnLoad=True, forceFullCalc=True, calcMode='auto')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(destination, temporary_suffix='.xlsx') as staged:
            wb.save(staged)
            from sme_gend.services.numerique_formula_cache import cache_model_formulae
            cache_model_formulae(wb, staged)
        return tuple(wb.sheetnames)
    finally:
        wb.close()
