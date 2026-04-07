"""Alignement sur la maquette `resultat.xlsx` (feuille Liste) — 18 colonnes."""

from __future__ import annotations

from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow

# Libellés comme sur la ligne d'en-tête de la maquette (ligne 3 Excel).
RESULTAT_HEADERS: list[str] = [
    "N°",
    "N° interne",
    "Noms et prénoms",
    "Date de naissance",
    "Lieu de naissance",
    "Grade",
    "Date de nomination au grade",
    "Date d'entrée en service",
    "N° décision d'engagement",
    "Matricule militaire",
    "Matricule solde",
    "Position administrative",
    "Fonction",
    "Subdivision d'arme",
    "Diplome",
    "Date et lieu d'obtention",
    "Structure",
    "Obs",
]


def _fmt_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y")


def row_to_resultat_cells(
    r: ListeGeneraleRow,
    numero_global: int,
    numero_interne: int,
) -> list[str]:
    """Une ligne personnel → 18 cellules (ordre maquette).

    * ``numero_global`` : rang continu sur toute la liste exportée (colonne « N° »).
    * ``numero_interne`` : rang recommencé à chaque bloc subdivision (colonne « N° interne »).
    """
    return [
        str(numero_global),
        str(numero_interne),
        r.noms or "",
        _fmt_date(r.date_naissance),
        r.lieu_naissance or "",
        r.grade or "",
        _fmt_date(r.date_grade),
        r.date_service or "",
        r.ndecision or "",
        r.matricule_militaire or "",
        r.matricule_solde or "",
        r.position_admin or "",
        r.fonction or "",
        r.subdivision_arme or "",
        r.diplome or "",
        r.date_obtention or "",
        r.structure or "",
        r.obs or "",
    ]
