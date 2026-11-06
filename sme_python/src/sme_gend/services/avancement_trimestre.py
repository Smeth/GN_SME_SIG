"""Découpage annuel en 4 trimestres pour les campagnes d'avancement."""

from __future__ import annotations

from datetime import date

TRIMESTRE_LABELS: dict[int, str] = {
    1: "T1 — janvier à mars",
    2: "T2 — avril à juin",
    3: "T3 — juillet à septembre",
    4: "T4 — octobre à décembre",
}

TRIMESTRE_SHORT: dict[int, str] = {
    1: "T1",
    2: "T2",
    3: "T3",
    4: "T4",
}


def trimestre_from_date(on: date | None = None) -> int:
    d = on or date.today()
    return (d.month - 1) // 3 + 1


def normalize_trimestre(value: int | None) -> int:
    if value in TRIMESTRE_LABELS:
        return int(value)
    return trimestre_from_date()


def trimestre_debut(annee: int, trimestre: int) -> date:
    t = normalize_trimestre(trimestre)
    month = (t - 1) * 3 + 1
    return date(int(annee), month, 1)


def trimestre_fin(annee: int, trimestre: int) -> date:
    t = normalize_trimestre(trimestre)
    if t == 4:
        return date(int(annee), 12, 31)
    month = t * 3 + 1
    next_month = date(int(annee), month, 1)
    return next_month.fromordinal(next_month.toordinal() - 1)


def periode_label(annee: int, trimestre: int) -> str:
    t = normalize_trimestre(trimestre)
    debut = trimestre_debut(annee, t)
    fin = trimestre_fin(annee, t)
    return (
        f"{TRIMESTRE_SHORT[t]} {annee} "
        f"({debut.strftime('%d/%m/%Y')} – {fin.strftime('%d/%m/%Y')})"
    )
