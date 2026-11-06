"""Postes de tête du mois SME, vacants ou pourvus.

Seules les structures et les subdivisions présentes dans ces effectifs
sont examinées. Le référentiel complet n'ajoute pas de siège vide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from sme_gend.services.fonction_occupancy import _struct_key
from sme_gend.config.structure_famille import structure_famille_label
from sme_gend.services.fonction_postes_tete import (
    poste_index_keys,
    postes_tete_libelles,
    structure_kind,
    subdivision_kind,
)
from sme_gend.services.fonction_structure_policy import (
    fold_label,
    structure_fonctions_non_liees,
)
from sme_gend.services.structure_label_format import structure_combo_label

_TITRES = {
    "COMREG": "Commandant de région",
    "CEMREG": "Chef d'état-major de région",
    "COMGROUP": "Commandant de groupement",
    "CEMGROUP": "Chef d'état-major de groupement",
    "COMEC": "Commandant de l'école",
    "CEMEC": "Chef d'état-major de l'école",
    "COMCI": "Commandant du centre d'instruction",
    "CEMCI": "Chef d'état-major du centre d'instruction",
    "DIRCAB": "Directeur de cabinet",
    "COMGEND": "Commandant de la gendarmerie",
    "C2 CEMGEND": "Chef d'état-major de la gendarmerie",
    "CHEF DE CENTRE": "Chef de centre",
    "INSPECTEUR IT": "Inspecteur IT",
    "CHEF DE DIVISION": "Chef de division",
    "CHEF DE SECTION": "Chef de section",
    "CHEFSEC": "Chef de secrétariat",
    "CHEF DE POLE": "Chef de pôle",
    "CHEF DE SERVICE": "Chef de service",
    "CC": "Commandant de compagnie",
    "CB": "Commandant de brigade",
    "COMPEL": "Commandant de peloton",
    "COMES": "Commandant d'escadron",
    "COM SRJ": "Commandant de la section de recherche judiciaire",
    "CHEF DEP": "Chef de département",
}


class _Structure(Protocol):
    code: str
    designation: str
    famille_unite: str | None
    ordre_tri: int | None


class _Personne(Protocol):
    structure: str | None
    fonction: str | None
    subdivision_arme: str | None
    noms: str | None


_TYPE_SUBDIVISION = {
    "DIVISION": "Division",
    "SECTION": "Section",
    "SECRETARIAT": "Secrétariat",
    "CABINET": "Cabinet",
    "POLE": "Pôle",
    "SERVICE": "Service",
    "ESCADRON": "Escadron",
    "PELOTON": "Peloton",
    "BRIGADE": "Brigade",
    "COMPAGNIE": "Compagnie",
    "SRJ": "Section de recherche judiciaire",
    "DEPARTEMENT": "Département",
}


@dataclass(frozen=True)
class TitulairePoste:
    id: str
    nom: str
    grade: str = ''
    militaire: str = ''
    solde: str = ''

    @property
    def label(self):
        identifiers=' · '.join(v for v in (f'fiche {self.id}' if self.id else '',self.militaire,self.solde) if v)
        return f'{self.grade} {self.nom}'.strip() + (f' [{identifiers}]' if identifiers else '')


@dataclass(frozen=True)
class PosteTete:
    structure_code: str
    structure_label: str
    subdivision: str
    type_code: str
    type_label: str
    poste: str
    intitule: str
    titulaires: tuple[str, ...]
    effectif: int
    titulaires_details: tuple[TitulairePoste,...] = ()

    @property
    def multiple_titulaires(self):
        return len(self.titulaires_details or self.titulaires)>1

    @property
    def etat(self):
        return 'Plusieurs titulaires — à vérifier' if self.multiple_titulaires else 'Vacant' if self.vacant else 'Pourvu'

    @property
    def vacant(self) -> bool:
        return not self.titulaires

    @property
    def titulaire_label(self) -> str:
        if self.titulaires_details:return ', '.join(t.label for t in self.titulaires_details)
        return ", ".join(self.titulaires) if self.titulaires else "Vacant"


def intitule_poste(poste: str) -> str:
    """Libellé lisible du poste de tête."""
    label = (poste or "").strip()
    folded = fold_label(label)
    titre = ""
    for code, title in _TITRES.items():
        if fold_label(code) == folded:
            titre = title
            break
    if titre and fold_label(titre) != folded:
        return f"{label} — {titre}"
    return label


def lister_postes_tete(
    structures: Iterable[_Structure],
    subdivisions: Iterable[tuple[str, str]],
    personnel: Iterable[_Personne],
) -> list[PosteTete]:
    """Sièges de tête du mois, avec titulaire et effectif.

    ``subdivisions`` est ignoré : une unité n'existe ici que si le mois SME
    y compte au moins une personne. Le contrôle spécial et les détachés
    sont exclus.
    """
    del subdivisions
    people = list(personnel)
    by_structure: dict[str, list[_Personne]] = {}
    observed: dict[str, list[str]] = {}
    for person in people:
        key = _struct_key(person.structure)
        if not key:
            continue
        by_structure.setdefault(key, []).append(person)
        sub = (person.subdivision_arme or "").strip()
        if sub:
            observed.setdefault(key, []).append(sub)

    found: list[PosteTete] = []
    ordered = sorted(
        structures,
        key=lambda row: (
            row.ordre_tri is None,
            row.ordre_tri or 0,
            (row.code or "").casefold(),
        ),
    )
    for structure in ordered:
        code = (structure.code or "").strip()
        if not code or structure_fonctions_non_liees(code):
            continue
        key = _struct_key(code)
        if key not in by_structure:
            continue
        people_here = by_structure.get(key, [])
        subs = _unique(observed.get(key, []))
        holders = _titulaires_par_cle(
            people_here,
            structure_code=code,
            designation=structure.designation,
            famille=structure.famille_unite,
            details=True,
        )
        for scope, sub_fold, subdivision, poste in _sieges(
            code,
            structure.designation,
            structure.famille_unite,
            subs,
        ):
            indexed = poste_index_keys(
                poste,
                structure_code=code,
                designation=structure.designation,
                famille=structure.famille_unite,
                subdivision=subdivision or None,
            )
            keys = indexed[1] if indexed else {fold_label(poste)}
            names: list[str] = []
            details=[]
            seen_names: set[str] = set()
            for alias in keys:
                for holder in holders.get((scope, sub_fold, alias), ()):
                    identity=holder.id or holder.label
                    if identity in seen_names:
                        continue
                    seen_names.add(identity)
                    names.append(holder.nom)
                    details.append(holder)
            type_code, type_label = _type_unite(
                scope,
                code,
                structure.designation,
                structure.famille_unite,
                subdivision,
            )
            found.append(
                PosteTete(
                    structure_code=code,
                    structure_label=structure_combo_label(code, structure.designation),
                    subdivision=subdivision,
                    type_code=type_code,
                    type_label=type_label,
                    poste=poste,
                    intitule=intitule_poste(poste),
                    titulaires=tuple(names),
                    effectif=_effectif(people_here, scope, sub_fold),
                    titulaires_details=tuple(details),
                )
            )
    return found


def lister_postes_vacants(
    structures: Iterable[_Structure],
    subdivisions: Iterable[tuple[str, str]],
    personnel: Iterable[_Personne],
) -> list[PosteTete]:
    """Sièges de tête sans titulaire."""
    return [
        row
        for row in lister_postes_tete(structures, subdivisions, personnel)
        if row.vacant
    ]


def _sieges(
    code: str,
    designation: str | None,
    famille: str | None,
    subdivisions: list[str],
) -> list[tuple[str, str, str, str]]:
    """(portée, subdivision repliée, libellé subdivision, poste)."""
    seats: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(scope: str, subdivision: str, poste: str) -> None:
        token = (scope, fold_label(subdivision), fold_label(poste))
        if not token[2] or token in seen:
            return
        seen.add(token)
        seats.append((scope, token[1], subdivision, poste))

    for poste in postes_tete_libelles(
        structure_code=code,
        designation=designation,
        famille=famille,
    ):
        add("structure", "", poste)
    for subdivision in subdivisions:
        for poste in postes_tete_libelles(
            structure_code=code,
            designation=designation,
            famille=famille,
            subdivision=subdivision,
        ):
            indexed = poste_index_keys(
                poste,
                structure_code=code,
                designation=designation,
                famille=famille,
                subdivision=subdivision,
            )
            if indexed is None or indexed[0] != "subdivision":
                continue
            add("subdivision", subdivision, poste)
    return seats


def _type_unite(
    scope: str,
    code: str,
    designation: str | None,
    famille: str | None,
    subdivision: str,
) -> tuple[str, str]:
    if scope == "subdivision":
        kind = subdivision_kind(subdivision) or ""
        return kind, _TYPE_SUBDIVISION.get(kind, kind.title() or "Subdivision")
    kind = structure_kind(code, designation, famille) or ""
    if not kind:
        return "COMMANDEMENT", "Commandement"
    return kind, structure_famille_label(kind)


def _effectif(people: Iterable[_Personne], scope: str, sub_fold: str) -> int:
    rows = list(people)
    if scope == "structure":
        return len(rows)
    return sum(1 for person in rows if fold_label(person.subdivision_arme) == sub_fold)


def _titulaires_par_cle(
    people: Iterable[_Personne],
    *,
    structure_code: str,
    designation: str | None,
    famille: str | None,
    details: bool = False,
) -> dict[tuple[str, str, str], tuple[str, ...]]:
    grouped: dict[tuple[str, str, str], list[str]] = {}
    seen: dict[tuple[str, str, str], set[str]] = {}
    for person in people:
        indexed = poste_index_keys(
            person.fonction,
            structure_code=structure_code,
            designation=designation,
            famille=famille,
            subdivision=person.subdivision_arme,
        )
        if indexed is None:
            continue
        scope, keys = indexed
        subdivision = "" if scope == "structure" else fold_label(person.subdivision_arme)
        nom = (getattr(person, "noms", None) or "").strip()
        if not nom:
            continue
        holder=TitulairePoste(str(getattr(person,'id','') or ''),nom,
            str(getattr(person,'grade','') or ''),str(getattr(person,'matricule_militaire','') or ''),str(getattr(person,'matricule_solde','') or ''))
        identity=(holder.id or holder.label) if details else nom
        for key in keys:
            token = (scope, subdivision, key)
            if identity in seen.setdefault(token, set()):
                continue
            seen[token].add(identity)
            grouped.setdefault(token, []).append(holder if details else nom)
    return {token: tuple(names) for token, names in grouped.items()}


def _unique(labels: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = (raw or "").strip()
        key = fold_label(label)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out
