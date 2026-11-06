"""Personnel concerné par une retraite selon la limite d'âge, de service ou la durée."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

MOTIF_AGE = "age"
MOTIF_SERVICE = "service"
MOTIF_BOTH = "both"
MOTIF_TOUS = "tous"

ECHEANCE_TOUTES = "toutes"
ECHEANCE_ATTEINTE = "atteinte"
ECHEANCE_DANS = "dans"
ECHEANCE_ANNEE = "annee"
ECHEANCE_ANNEE_COURANTE = "annee_courante"
ECHEANCE_ANNEE_SUIVANTE = "annee_suivante"
ECHEANCE_DEFAUT = "dans_12"


class _Personne(Protocol):
    date_naissance: object
    date_service: object


class _Limite(Protocol):
    age_limit_date: date | None
    service_limit_date: date | None


@dataclass(frozen=True)
class RetraiteLigne:
    person: _Personne
    motif: str
    date_retraite: date
    jours_restants: int


def echeance_spec(code: str) -> tuple[str, int | None, int | None]:
    """Décode un code d'échéance (preset combo ou saisie libre)."""
    raw = (code or "").strip()
    if not raw:
        return (ECHEANCE_TOUTES, None, None)
    if raw.startswith(f"{ECHEANCE_DANS}_"):
        try:
            mois = int(raw.split("_", 1)[1])
            return (ECHEANCE_DANS, mois, None)
        except (ValueError, IndexError):
            return (ECHEANCE_TOUTES, None, None)
    if raw.startswith("annee_"):
        try:
            annee = int(raw.split("_", 1)[1])
            return (ECHEANCE_ANNEE, None, annee)
        except (ValueError, IndexError):
            return (ECHEANCE_TOUTES, None, None)
    if raw in {
        ECHEANCE_TOUTES,
        ECHEANCE_ATTEINTE,
        ECHEANCE_DANS,
        ECHEANCE_ANNEE_COURANTE,
        ECHEANCE_ANNEE_SUIVANTE,
    }:
        return (raw, None, None)
    return (ECHEANCE_TOUTES, None, None)


def filtrer_retraites(
    rows: Iterable[tuple[_Personne, _Limite]],
    *,
    motif: str,
    echeance: str,
    annees: int = 2,
    mois: int | None = None,
    annee: int | None = None,
    today: date | None = None,
) -> list[RetraiteLigne]:
    """Lignes dont la date de retraite correspond au motif et à l'échéance."""
    ref = today or date.today()
    chosen = motif if motif in {MOTIF_AGE, MOTIF_SERVICE, MOTIF_BOTH, MOTIF_TOUS} else MOTIF_BOTH
    window = echeance if echeance in {
        ECHEANCE_TOUTES,
        ECHEANCE_ATTEINTE,
        ECHEANCE_DANS,
        ECHEANCE_ANNEE,
        ECHEANCE_ANNEE_COURANTE,
        ECHEANCE_ANNEE_SUIVANTE,
    } else ECHEANCE_TOUTES
    found: list[RetraiteLigne] = []
    for person, limit in rows:
        pick_motif = MOTIF_BOTH if chosen == MOTIF_TOUS else chosen
        picked = _pick(limit, pick_motif)
        if picked is None:
            continue
        label, when = picked
        days = (when - ref).days
        if window == ECHEANCE_ATTEINTE and days > 0:
            continue
        if window == ECHEANCE_ANNEE_COURANTE and when.year != ref.year:
            continue
        if window == ECHEANCE_ANNEE_SUIVANTE and when.year != ref.year + 1:
            continue
        if window == ECHEANCE_ANNEE and annee is not None and when.year != annee:
            continue
        if window == ECHEANCE_DANS:
            if mois is not None:
                horizon = _add_months(ref, mois)
            else:
                horizon = _add_years(ref, max(0, int(annees)))
            if days < 0 or when > horizon:
                continue
        found.append(
            RetraiteLigne(
                person=person,
                motif=label,
                date_retraite=when,
                jours_restants=days,
            )
        )
    found.sort(key=lambda row: (row.date_retraite, _nom(row.person)))
    return found


def format_duree_restante(jours: int) -> str:
    """Durée lisible jusqu'à la retraite, ou le retard si la limite est passée."""
    if jours < 0:
        return f"Atteinte ({-jours} j)"
    if jours == 0:
        return "Aujourd'hui"
    years, rem = divmod(jours, 365)
    months = rem // 30
    if years and months:
        return f"{years} an(s) {months} mois"
    if years:
        return f"{years} an(s)"
    if months:
        return f"{months} mois"
    return f"{jours} j"


def _pick(limit: _Limite, motif: str) -> tuple[str, date] | None:
    age = limit.age_limit_date
    service = limit.service_limit_date
    if motif == MOTIF_AGE:
        return ("Âge", age) if age else None
    if motif == MOTIF_SERVICE:
        return ("Service", service) if service else None
    if age and service:
        if age == service:
            return ("Âge et service", age)
        if age < service:
            return ("Âge", age)
        return ("Service", service)
    if age:
        return ("Âge", age)
    if service:
        return ("Service", service)
    return None


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _add_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + max(0, int(months))
    year, month = divmod(total, 12)
    month += 1
    day = min(value.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def _nom(person: _Personne) -> str:
    return str(getattr(person, "noms", "") or "").casefold()
