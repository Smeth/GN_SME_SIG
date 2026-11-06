"""Lossless interpretation of legacy Access diploma date/place text."""
from dataclasses import dataclass
from datetime import date, datetime
import re

_TOKEN = re.compile(r'(?<![0-9])(?:[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}|[0-9]{1,2}[-/.][0-9]{1,2}[-/.](?:[0-9]{4}|[0-9]{2}))(?![0-9])')


@dataclass(frozen=True, slots=True)
class ImportedDiplomaDate:
    value: date | None
    raw: str
    place: str | None = None
    issue: str | None = None
    preserve_source: bool = False


def interpret_diploma_date(value) -> ImportedDiplomaDate:
    if value is None:
        return ImportedDiplomaDate(None, '')
    if isinstance(value, (date, datetime)):
        result = value.date() if isinstance(value, datetime) else value
        if result.year < 1000:
            return ImportedDiplomaDate(None, str(value), issue='année hors de la plage des dates prises en charge', preserve_source=True)
        return ImportedDiplomaDate(result, result.isoformat())
    raw = str(value).strip()
    if not raw:
        return ImportedDiplomaDate(None, '')
    try:
        result = datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    else:
        return interpret_diploma_date(result)
    content = raw
    if (content.startswith('(') and content.endswith(')')) or (content.startswith('[') and content.endswith(']')):
        content = content[1:-1].strip()
    matches = list(_TOKEN.finditer(content))
    if len(matches) != 1:
        reason = 'plusieurs dates dans le champ' if matches else 'date absente, incomplète ou non reconnue'
        return ImportedDiplomaDate(None, raw, issue=reason, preserve_source=True)
    token = matches[0]
    pieces = re.split(r'[-/.]', token.group())
    suffix = content[token.end():].strip(' ,;:').lstrip(')] ,;:')
    suffix = re.sub(r'^à\s+', '', suffix, flags=re.IGNORECASE)
    place = suffix if suffix and not re.search(r'[0-9]', suffix) and len(suffix) <= 255 else None
    if len(pieces[0]) == 4:
        year, month, day = map(int, pieces)
    else:
        day, month, year = map(int, pieces)
        if len(pieces[2]) != 4:
            return ImportedDiplomaDate(None, raw, place, 'année sur deux chiffres : siècle à confirmer', True)
        # Only swap an impossible French month for an unambiguous US date.
        if month > 12 and 1 <= day <= 12:
            day, month = month, day
    try:
        parsed = date(year, month, day)
        if year < 1000:
            raise ValueError()
    except ValueError:
        return ImportedDiplomaDate(None, raw, place, 'date calendaire invalide', True)
    return ImportedDiplomaDate(parsed, raw, place, preserve_source=raw != token.group())


def source_observation(obs, parsed):
    if not parsed.preserve_source:
        return obs
    text = f"Date et lieu d'obtention — valeur source Access : {parsed.raw}"
    if parsed.issue:
        text += f"\nDate à vérifier : {parsed.issue}. Valeur source conservée ; aucune année n'a été déduite."
    return (obs or '').rstrip() + '\n' + text
