"""Formatage lignes SME DPF (16 colonnes — gabarit Excel DPF)."""

from __future__ import annotations

from datetime import date, datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.matricule_format import format_matricule_solde_display

LISTE_GNLE_HEADERS: tuple[str, ...] = (
    "N°",
    "Nom(s) et prénom(s)",
    "Date et lieu de naissance",
    "Grade",
    "Date de nomination au grade",
    "Date Entrée en service",
    "N° Décision",
    "Matricule militaire",
    "Matricule solde",
    "Position Administrative",
    "Fonction occupée",
    "Subdivision d'arme",
    "Dernier diplôme militaire",
    "Date et lieu d'obtention",
    "Structure",
    "Observations",
)


def _fmt_date(value: datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _date_lieu_naissance(row: ListeGeneraleRow) -> str:
    dn = _fmt_date(row.date_naissance)
    ln = (row.lieu_naissance or "").strip()
    if dn and ln:
        return f"{dn} {ln}"
    return dn or ln


def row_to_dpf_liste_cells(
    row: ListeGeneraleRow, numero: int, grades=None
) -> list[object]:
    return [
        numero,
        row.noms or "",
        _date_lieu_naissance(row),
        format_grade_display(row.grade, grades),
        _fmt_date(row.date_grade),
        row.date_service or "",
        row.ndecision or "",
        row.matricule_militaire or "",
        format_matricule_solde_display(row.matricule_solde, empty=""),
        row.position_admin or "",
        row.fonction or "",
        row.subdivision_arme or "",
        row.diplome or "",
        row.date_obtention or "",
        row.structure or "",
        row.obs or "",
    ]
