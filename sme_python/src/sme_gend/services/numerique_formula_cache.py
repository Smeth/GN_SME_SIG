"""Evaluate the official model's small formula vocabulary and cache XLSX results.

No Python eval, macros or external references are executed. Unsupported formulae
stop the export rather than publishing plausible but incorrect cached totals.
"""
from functools import lru_cache
import fnmatch
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import range_boundaries


def cache_model_formulae(wb, path):
    active = set()

    def number(v):
        if v in (None, ''):
            return 0
        if isinstance(v, (int, float)):
            return v
        raise ValueError(f'Valeur non numérique dans une formule : {v!r}')

    @lru_cache(None)
    def value(sheet, coordinate):
        cell = wb[sheet][coordinate]
        if cell.data_type != 'f':
            return cell.value
        key = (sheet, coordinate)
        if key in active:
            raise ValueError(f'Formule circulaire : {sheet}!{coordinate}')
        active.add(key)
        try:
            return expression(cell.value, sheet)
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError(f'Calcul impossible : {sheet}!{coordinate} ({cell.value}) : {exc}') from exc
        finally:
            active.remove(key)

    @lru_cache(None)
    def reference(sheet, ref):
        if '!' in ref:
            sheet, ref = ref.rsplit('!', 1)
            sheet = sheet.strip("'").replace("''", "'")
        if '[' in sheet or sheet not in wb.sheetnames:
            raise ValueError('Référence Excel externe ou inconnue')
        ref = ref.replace('$', '')
        if ':' not in ref:
            return value(sheet, ref)
        left, top, right, bottom = range_boundaries(ref)
        return tuple(value(sheet, f'{_letter(col)}{row}')
                     for row in range(top, bottom + 1) for col in range(left, right + 1))

    def flat(args):
        return [v for arg in args for v in (arg if isinstance(arg, tuple) else (arg,))]

    @lru_cache(None)
    def criterion(values, expected):
        if isinstance(expected, str):
            match = re.match(r'^(>=|<=|<>|>|<|=)(.*)$', expected)
            op, target = match.groups() if match else ('=', expected)
            def matches(item):
                if op in ('=', '<>'):
                    result = fnmatch.fnmatchcase(str(item or '').casefold(), target.casefold())
                    return not result if op == '<>' else result
                try:
                    a, b = float(item), float(target)
                except (TypeError, ValueError):
                    return False
                return {'>': a > b, '<': a < b, '>=': a >= b, '<=': a <= b}[op]
            return tuple(matches(v) for v in values)
        return tuple(v == expected for v in values)

    def function(name, args):
        name = name.upper()
        if name in ('SUM', 'COUNT'):
            nums = [v for v in flat(args) if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return sum(nums) if name == 'SUM' else len(nums)
        if name in ('COUNTIF', 'COUNTIFS'):
            masks = [criterion(args[i] if isinstance(args[i], tuple) else (args[i],), args[i + 1])
                     for i in range(0, len(args), 2)]
            if len({len(mask) for mask in masks}) > 1:
                raise ValueError('Plages COUNTIFS de tailles différentes')
            return sum(all(items) for items in zip(*masks))
        if name == 'IF' and len(args) == 3:
            return args[1] if args[0] else args[2]
        raise ValueError(f'Fonction Excel non prise en charge : {name}')

    def expression(formula, sheet):
        tokens = [t for t in Tokenizer(formula).items if t.type != 'WHITE-SPACE']
        pos = 0

        def parse(minimum=0):
            nonlocal pos
            token = tokens[pos]
            pos += 1
            if token.type == 'OPERAND':
                if token.subtype == 'RANGE': result = reference(sheet, token.value)
                elif token.subtype == 'NUMBER': result = float(token.value)
                elif token.subtype == 'TEXT': result = token.value[1:-1].replace('""', '"')
                else: raise ValueError(f'Opérande Excel non pris en charge : {token.value}')
            elif token.type == 'FUNC' and token.subtype == 'OPEN':
                args = []
                while tokens[pos].subtype != 'CLOSE':
                    args.append(parse())
                    if tokens[pos].type == 'SEP': pos += 1
                    else: break
                if tokens[pos].subtype != 'CLOSE': raise ValueError('Formule Excel invalide')
                pos += 1
                result = function(token.value[:-1], args)
            elif token.type == 'PAREN' and token.subtype == 'OPEN':
                result = parse()
                if tokens[pos].subtype != 'CLOSE': raise ValueError('Parenthèse Excel manquante')
                pos += 1
            elif token.type == 'OPERATOR-PREFIX' and token.value in ('+', '-'):
                result = number(parse(30)) * (-1 if token.value == '-' else 1)
            else:
                raise ValueError(f'Formule Excel non prise en charge : {formula}')
            precedence = {'=': 5, '<>': 5, '+': 10, '-': 10, '*': 20, '/': 20}
            while pos < len(tokens) and tokens[pos].type == 'OPERATOR-INFIX':
                op = tokens[pos].value
                level = precedence.get(op, -1)
                if level < minimum: break
                pos += 1
                right = parse(level + 1)
                if op == '=': result = (result if result is not None else '') == right
                elif op == '<>': result = (result if result is not None else '') != right
                elif op == '+': result = number(result) + number(right)
                elif op == '-': result = number(result) - number(right)
                elif op == '*': result = number(result) * number(right)
                elif op == '/': result = number(result) / number(right)
            return result

        result = parse()
        if pos != len(tokens):
            raise ValueError(f'Formule Excel non prise en charge : {formula}')
        return result

    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    tag = lambda name: f'{{{namespace}}}{name}'
    with ZipFile(path) as archive:
        parts = [(entry, archive.read(entry.filename)) for entry in archive.infolist()]
    worksheets = {f'xl/worksheets/sheet{i}.xml': ws for i, ws in enumerate(wb, 1)}
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for entry, data in parts:
            if entry.filename in worksheets:
                ws = worksheets[entry.filename]
                root = ET.fromstring(data)
                for cell in root.iter(tag('c')):
                    if cell.find(tag('f')) is None:
                        continue
                    result = value(ws.title, cell.attrib['r'])
                    cached = cell.find(tag('v'))
                    if cached is None: cached = ET.SubElement(cell, tag('v'))
                    if isinstance(result, str):
                        cell.set('t', 'str')
                        cached.text = result
                    else:
                        cell.attrib.pop('t', None)
                        cached.text = str(int(result) if isinstance(result, float) and result.is_integer() else result or 0)
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            archive.writestr(entry, data)


def _letter(column):
    from openpyxl.utils import get_column_letter
    return get_column_letter(column)
