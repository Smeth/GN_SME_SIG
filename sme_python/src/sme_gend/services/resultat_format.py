"""Alignement sur la maquette `resultat.xlsx` (feuille Liste) — 18 colonnes."""

from __future__ import annotations

from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow

# Titre document (ligne 1 / A1 de la maquette).
RESULTAT_DOCUMENT_TITLE = "LISTE DES PRESENTS"

# Libellés ligne d'en-tête (ligne 3) : la colonne 5 est vide dans la maquette (sous « Date et lieu de naissance »).
RESULTAT_HEADERS: list[str] = [
    "N°",
    "N° interne",
    "Noms et prénoms",
    "Date et lieu de naissance",
    "",
    "Grade",
    "Date de nomination au grade",
    "Date d'entrée en service",
    "N° decision d'engagement",
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

# En-tête HTML : (libellé, colspan) — même rubriques que la maquette, fusion D–E pour naissance.
RESULTAT_HEADER_HTML: list[tuple[str, int]] = [
    ("N°", 1),
    ("N° interne", 1),
    ("Noms et prénoms", 1),
    ("Date et lieu de naissance", 2),
    ("Grade", 1),
    ("Date de nomination au grade", 1),
    ("Date d'entrée en service", 1),
    ("N° decision d'engagement", 1),
    ("Matricule militaire", 1),
    ("Matricule solde", 1),
    ("Position administrative", 1),
    ("Fonction", 1),
    ("Subdivision d'arme", 1),
    ("Diplome", 1),
    ("Date et lieu d'obtention", 1),
    ("Structure", 1),
    ("Obs", 1),
]

assert sum(c for _, c in RESULTAT_HEADER_HTML) == len(RESULTAT_HEADERS) == 18


def _fmt_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y")


def row_to_resultat_cells(
    r: ListeGeneraleRow,
    numero_global: int,
    numero_interne: int,
    *,
    subdivision_arme_display: str | None = None,
) -> list[str]:
    """Une ligne personnel → 18 cellules (ordre maquette).

    Colonnes 4–5 : date et lieu de naissance (rubrique unique « Date et lieu de naissance » en en-tête).

    * ``numero_global`` : rang continu sur toute la liste exportée (colonne « N° »).
    * ``numero_interne`` : rang recommencé à chaque bloc subdivision (colonne « N° interne »).
    * ``subdivision_arme_display`` : si renseigné, remplace la colonne « Subdivision d'arme »
      (affichage court aligné référentiel) sans modifier la fiche en base.
    """
    sub_col = (
        subdivision_arme_display
        if subdivision_arme_display is not None
        else (r.subdivision_arme or "")
    )
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
        sub_col,
        r.diplome or "",
        r.date_obtention or "",
        r.structure or "",
        r.obs or "",
    ]
