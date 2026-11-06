"""Règles de filtrage avancé (style Excel) pour la liste générale."""

from __future__ import annotations

from dataclasses import dataclass
import calendar
import re
from datetime import date, datetime
from typing import Any, Literal

from sme_gend.models.liste_generale import ListeGeneraleRow

ColumnKind = Literal["text", "date", "number", "duration"]

FILTERABLE_COLUMNS: tuple[tuple[str, str, ColumnKind], ...] = (
    ("id", "N°", "text"),
    ("noms", "Nom et prénom", "text"),
    ("sexe", "Sexe", "text"),
    ("grade", "Grade", "text"),
    ("fonction", "Fonction", "text"),
    ("subdivision_arme", "Subdivision", "text"),
    ("position_admin", "Position adm.", "text"),
    ("structure", "Structure", "text"),
    ("matricule_militaire", "Matricule militaire", "text"),
    ("matricule_solde", "Matricule solde", "text"),
    ("lieu_naissance", "Lieu naissance", "text"),
    ("date_naissance", "Date naissance", "date"),
    ("date_grade", "Date de grade", "date"),
    ("date_service", "Date de service", "text"),
    ("anciennete_grade", "Ancienneté dans le grade", "duration"),
    ("duree_service", "Durée de service", "duration"),
    ("ndecision", "N° décision", "text"),
    ("diplome", "Diplôme", "text"),
    ("date_obtention", "Date diplôme", "text"),
    ("obs", "Observations", "text"),
    ("ordre_tri", "Ordre tri", "number"),
    ("presseance", "Préséance", "number"),
)

OPS_BY_KIND: dict[ColumnKind, tuple[str, ...]] = {
    "duration": ("au moins", "moins de", "égal à", "entre"),
    "text": (
        "contient",
        "égal à",
        "différent de",
        "commence par",
        "est vide",
        "n'est pas vide",
    ),
    "date": (
        "égal à",
        "avant",
        "après",
        "est vide",
        "n'est pas vide",
    ),
    "number": (
        "=",
        ">",
        "<",
        ">=",
        "<=",
        "est vide",
        "n'est pas vide",
    ),
}

_COLUMN_BY_KEY = {c[0]: c for c in FILTERABLE_COLUMNS}


@dataclass(frozen=True, slots=True)
class AdvancedFilterRule:
    column_key: str
    operator: str
    value: str
    reference_date: str = ""

    def label(self) -> str:
        col = _COLUMN_BY_KEY.get(self.column_key)
        title = col[1] if col else self.column_key
        if self.operator in ("est vide", "n'est pas vide"):
            return f"{title} {self.operator}"
        suffix = ""
        if column_kind(self.column_key) == "duration":
            suffix = f" (au {self.reference_date or 'jour courant'})"
        return f"{title} {self.operator} « {self.value} »{suffix}"


def column_kind(column_key: str) -> ColumnKind:
    col = _COLUMN_BY_KEY.get(column_key)
    return col[2] if col else "text"


def default_operator_for(column_key: str) -> str:
    return OPS_BY_KIND[column_kind(column_key)][0]


def is_filterable_column(column_key: str) -> bool:
    return column_key in FILTERABLE_COLUMN_KEYS


def _norm_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return str(v).strip()


def _parse_user_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _row_date(row: ListeGeneraleRow, key: str) -> date | None:
    v = getattr(row, key, None)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return _parse_user_date(_norm_text(v))


def _row_number(row: ListeGeneraleRow, key: str) -> float | None:
    v = getattr(row, key, None)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _duration_months(raw: str) -> int | None:
    """Un nombre seul désigne des années ; les mois peuvent être précisés."""
    value = raw.strip().casefold()
    if re.fullmatch(r"\d+", value):
        return int(value) * 12
    match = re.fullmatch(r"(?:(\d+)\s*ans?)?\s*(?:(\d+)\s*mois)?", value)
    if not match or not any(match.groups()):
        return None
    return int(match[1] or 0) * 12 + int(match[2] or 0)


def _anniversary(start: date, months: int) -> date | None:
    year, month0 = divmod(start.year * 12 + start.month - 1 + months, 12)
    if not 1 <= year <= 9999:
        return None
    month = month0 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _duration_matches(row: ListeGeneraleRow, rule: AdvancedFilterRule) -> bool:
    start = _row_date(row, "date_grade" if rule.column_key == "anciennete_grade" else "date_service")
    reference = _parse_user_date(rule.reference_date) if rule.reference_date else date.today()
    if start is None or reference is None or start > reference:
        return False
    parts = rule.value.split(";")
    months = _duration_months(parts[0])
    if months is None:
        return False
    threshold = _anniversary(start, months)
    if threshold is None:
        return False
    if rule.operator == "entre":
        upper = _duration_months(parts[1]) if len(parts) == 2 else None
        end = _anniversary(start, upper) if upper is not None else None
        return upper is not None and upper >= months and end is not None and threshold <= reference <= end
    if len(parts) != 1:
        return False
    if rule.operator == "au moins":
        return reference >= threshold
    if rule.operator == "moins de":
        return reference < threshold
    if rule.operator == "égal à":
        # Années révolues si saisie en années, mois révolus si les mois sont précisés.
        step = 1 if "mois" in rule.value.casefold() else 12
        end = _anniversary(start, months + step)
        return end is not None and threshold <= reference < end
    return False


def _is_empty(row: ListeGeneraleRow, key: str) -> bool:
    v = getattr(row, key, None)
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    return False


def rule_matches(row: ListeGeneraleRow, rule: AdvancedFilterRule) -> bool:
    key = rule.column_key
    op = rule.operator
    val = (rule.value or "").strip()
    kind = column_kind(key)
    if kind == "duration":
        return _duration_matches(row, rule)

    if op == "est vide":
        return _is_empty(row, key)
    if op == "n'est pas vide":
        return not _is_empty(row, key)

    cell = _norm_text(getattr(row, key, None))
    if kind == "date":
        d_row = _row_date(row, key)
        d_val = _parse_user_date(val)
        if op == "égal à":
            return d_row is not None and d_val is not None and d_row == d_val
        if op == "avant":
            return d_row is not None and d_val is not None and d_row < d_val
        if op == "après":
            return d_row is not None and d_val is not None and d_row > d_val
        return True

    if kind == "number":
        n_row = _row_number(row, key)
        try:
            n_val = float(val.replace(",", ".")) if val else None
        except ValueError:
            return False
        if n_row is None:
            return False
        if op == "=":
            return n_val is not None and n_row == n_val
        if op == ">":
            return n_val is not None and n_row > n_val
        if op == "<":
            return n_val is not None and n_row < n_val
        if op == ">=":
            return n_val is not None and n_row >= n_val
        if op == "<=":
            return n_val is not None and n_row <= n_val
        return True

    cell_cf = cell.casefold()
    val_cf = val.casefold()
    if op == "contient":
        return val_cf in cell_cf if val_cf else True
    if op == "égal à":
        return cell_cf == val_cf
    if op == "différent de":
        return cell_cf != val_cf
    if op == "commence par":
        return cell_cf.startswith(val_cf) if val_cf else True
    return True


MatchMode = Literal["and", "or"]

FILTERABLE_COLUMN_KEYS = frozenset(c[0] for c in FILTERABLE_COLUMNS)


def rules_match(
    row: ListeGeneraleRow,
    rules: list[AdvancedFilterRule],
    *,
    mode: MatchMode = "and",
) -> bool:
    if not rules:
        return True
    if mode == "or":
        return any(rule_matches(row, r) for r in rules)
    return all(rule_matches(row, r) for r in rules)


def advanced_rules_match(
    row: ListeGeneraleRow,
    rules: list[AdvancedFilterRule],
    *,
    mode: MatchMode = "and",
) -> bool:
    return rules_match(row, rules, mode=mode)


def rule_to_dict(rule: AdvancedFilterRule) -> dict[str, str]:
    return {
        "column": rule.column_key,
        "operator": rule.operator,
        "value": rule.value,
        "reference_date": rule.reference_date,
    }


def rule_from_dict(data: dict) -> AdvancedFilterRule | None:
    if not isinstance(data, dict):
        return None
    key = str(data.get("column", "")).strip()
    if key not in FILTERABLE_COLUMN_KEYS:
        return None
    op = str(data.get("operator", "")).strip()
    if not op:
        op = default_operator_for(key)
    return AdvancedFilterRule(column_key=key, operator=op, value=str(data.get("value", "")),
                              reference_date=str(data.get("reference_date", "") or ""))


def rules_from_list(raw: object) -> list[AdvancedFilterRule]:
    if not isinstance(raw, list):
        return []
    out: list[AdvancedFilterRule] = []
    for item in raw:
        r = rule_from_dict(item) if isinstance(item, dict) else None
        if r is not None:
            out.append(r)
    return out
