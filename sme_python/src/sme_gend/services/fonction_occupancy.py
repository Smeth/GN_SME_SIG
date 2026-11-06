"""Une fonction ne peut être tenue que par une personne à la fois dans la même unité.

L'unité est la subdivision de la structure : division, section, brigade,
compagnie, PSIG, ESSIG, section de recherche judiciaire… Une région ou une
direction en contient plusieurs, donc la même fonction peut y être répétée.
Sans subdivision, le poste est celui du commandement de la structure.
Le contrôle spécial et les détachés restent hors règle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

from sme_gend.services.fonction_structure_policy import (
    fold_label,
    structure_fonctions_non_liees,
)


class _OccupancyRow(Protocol):
    id: str
    noms: str | None
    structure: str | None
    fonction: str | None
    subdivision_arme: str | None


@dataclass(frozen=True)
class FonctionOccupant:
    row_id: str
    noms: str
    structure: str
    fonction: str
    subdivision: str


@dataclass(frozen=True)
class FonctionChoiceState:
    libelle: str
    label: str
    enabled: bool
    tooltip: str


def _struct_key(code: str | None) -> str:
    return (code or "").strip().casefold()


def same_fonction_poste(
    structure_a: str | None,
    fonction_a: str | None,
    structure_b: str | None,
    fonction_b: str | None,
    subdivision_a: str | None = None,
    subdivision_b: str | None = None,
) -> bool:
    return (
        _struct_key(structure_a) == _struct_key(structure_b)
        and fold_label(fonction_a) == fold_label(fonction_b)
        and fold_label(subdivision_a) == fold_label(subdivision_b)
    )


def occupants_by_fonction(
    rows: Iterable[_OccupancyRow],
    *,
    structure: str | None,
    subdivision: str | None = None,
    exclude_row_id: str | None = None,
) -> dict[str, list[FonctionOccupant]]:
    """Fonctions déjà tenues dans cette unité, hors la fiche exclue."""
    if not _struct_key(structure) or structure_fonctions_non_liees(structure):
        return {}
    from sme_gend.services.fonction_postes_tete import poste_index_keys

    wanted = _struct_key(structure)
    wanted_sub = fold_label(subdivision)
    skip = (exclude_row_id or "").strip()
    grouped: dict[str, list[FonctionOccupant]] = {}
    seen: dict[str, set[str]] = {}
    for row in rows:
        if skip and str(row.id).strip() == skip:
            continue
        if _struct_key(row.structure) != wanted:
            continue
        poste = poste_index_keys(
            row.fonction,
            structure_code=structure,
            subdivision=row.subdivision_arme,
        )
        same_sub = fold_label(row.subdivision_arme) == wanted_sub
        if poste is None:
            if not same_sub:
                continue
            index_keys = {fold_label(row.fonction)}
        else:
            scope, index_keys = poste
            if scope != "structure" and not same_sub:
                continue
        label_key = fold_label(row.fonction)
        if not label_key and not index_keys:
            continue
        occupant = FonctionOccupant(
            row_id=str(row.id),
            noms=(row.noms or "").strip(),
            structure=(row.structure or "").strip(),
            fonction=(row.fonction or "").strip(),
            subdivision=(row.subdivision_arme or "").strip(),
        )
        for key in index_keys or {label_key}:
            if not key or row.id in seen.setdefault(key, set()):
                continue
            seen[key].add(str(row.id))
            grouped.setdefault(key, []).append(occupant)
    return grouped


def other_occupants(
    rows: Iterable[_OccupancyRow],
    *,
    structure: str | None,
    fonction: str | None,
    subdivision: str | None = None,
    exclude_row_id: str | None = None,
) -> list[FonctionOccupant]:
    if not fold_label(fonction):
        return []
    from sme_gend.services.fonction_postes_tete import poste_index_keys

    grouped = occupants_by_fonction(
        rows,
        structure=structure,
        subdivision=subdivision,
        exclude_row_id=exclude_row_id,
    )
    poste = poste_index_keys(
        fonction,
        structure_code=structure,
        subdivision=subdivision,
    )
    if poste is None:
        return grouped.get(fold_label(fonction), [])
    found: list[FonctionOccupant] = []
    seen: set[str] = set()
    for key in poste[1]:
        for occupant in grouped.get(key, []):
            if occupant.row_id in seen:
                continue
            seen.add(occupant.row_id)
            found.append(occupant)
    return found


def _who(occupants: list[FonctionOccupant]) -> str:
    labels = [(o.noms or "").strip() or f"fiche {o.row_id}" for o in occupants]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} et {labels[1]}"
    return f"{labels[0]}, {labels[1]} et {len(labels) - 2} autre(s)"


def occupancy_message(occupants: list[FonctionOccupant], fonction: str) -> str:
    fn = (fonction or "").strip()
    places = {(o.subdivision or "").strip() for o in occupants}
    places.discard("")
    if len(places) == 1:
        lieu = f" dans « {next(iter(places))} »"
    elif not places:
        lieu = " sur cette structure"
    else:
        lieu = ""
    return (
        f"« {fn} » est déjà occupée par {_who(occupants)}{lieu}. "
        "Deux personnes ne peuvent pas occuper la même fonction en même temps."
    )


def assert_fonction_disponible(
    rows: Iterable[_OccupancyRow],
    *,
    structure: str | None,
    fonction: str | None,
    subdivision: str | None = None,
    exclude_row_id: str | None = None,
) -> None:
    found = other_occupants(
        rows,
        structure=structure,
        fonction=fonction,
        subdivision=subdivision,
        exclude_row_id=exclude_row_id,
    )
    if found:
        raise ValueError(occupancy_message(found, fonction or ""))


def _holders_for_libelle(
    libelle: str,
    occupants: dict[str, list[FonctionOccupant]],
    *,
    structure_code: str | None,
    subdivision: str | None,
) -> list[FonctionOccupant]:
    from sme_gend.services.fonction_postes_tete import poste_index_keys

    poste = poste_index_keys(
        libelle,
        structure_code=structure_code,
        subdivision=subdivision,
    )
    keys = poste[1] if poste else {fold_label(libelle)}
    found: list[FonctionOccupant] = []
    seen: set[str] = set()
    for key in keys:
        for occupant in occupants.get(key, []):
            if occupant.row_id in seen:
                continue
            seen.add(occupant.row_id)
            found.append(occupant)
    return found


def fonction_choice_states(
    libelles: Iterable[str],
    occupants: dict[str, list[FonctionOccupant]],
    *,
    current: str | None,
    structure_code: str | None = None,
    subdivision: str | None = None,
) -> list[FonctionChoiceState]:
    """Libellés de combo : une fonction déjà tenue n'est pas sélectionnable."""
    own = fold_label(current)
    seen: set[str] = set()
    states: list[FonctionChoiceState] = []
    for raw in libelles:
        lib = (raw or "").strip()
        key = fold_label(lib)
        if not lib or key in seen:
            continue
        seen.add(key)
        holders = _holders_for_libelle(
            lib,
            occupants,
            structure_code=structure_code,
            subdivision=subdivision,
        )
        if holders and key != own:
            states.append(
                FonctionChoiceState(
                    libelle=lib,
                    label=f"{lib} — occupée par {_who(holders)}",
                    enabled=False,
                    tooltip=occupancy_message(holders, lib),
                )
            )
        elif holders:
            states.append(
                FonctionChoiceState(
                    libelle=lib,
                    label=f"{lib} — aussi occupée par {_who(holders)}",
                    enabled=True,
                    tooltip=occupancy_message(holders, lib),
                )
            )
        else:
            states.append(
                FonctionChoiceState(libelle=lib, label=lib, enabled=True, tooltip="")
            )
    return states


def affectation_devient_poste_actuel(
    *,
    new_date: date | None,
    existing: Iterable[tuple[date | None, int | None]],
) -> bool:
    """True si cette affectation devient le poste projeté sur la fiche du mois."""
    pairs = list(existing)
    if not pairs:
        return True
    new_ord = new_date.toordinal() if new_date else 0
    best = max((d.toordinal() if d else 0) for d, _i in pairs)
    return new_ord >= best
