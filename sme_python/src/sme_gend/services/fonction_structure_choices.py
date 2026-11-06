"""Choix de fonctions proposés à la saisie agent (référentiel + postes qualifiés)."""

from __future__ import annotations

from sme_gend.services.fonction_libelle_qualifier import (
    fonction_matches_qualified_poste,
    qualified_poste_libelle,
)
from sme_gend.services.fonction_postes_tete import (
    completer_postes_tete,
    postes_tete_libelles,
)


def build_fonction_choice_libelles(
    linked_libelles: list[str],
    *,
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> list[str]:
    """Libellés proposés dans la combo (liens structure + postes de tête attendus)."""
    return completer_postes_tete(
        list(linked_libelles),
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    )


def fonction_in_choice_list(fonction: str | None, choices: list[str]) -> bool:
    fn = (fonction or "").strip()
    if not fn:
        return False
    key = fn.casefold()
    return any(key == (c or "").strip().casefold() for c in choices)


def fonction_acceptable_for_assignment(
    fonction: str | None,
    *,
    linked_libelles: list[str],
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> bool:
    """True si la fonction peut être enregistrée pour cette structure / subdivision."""
    fn = (fonction or "").strip()
    if not fn:
        return False
    choices = build_fonction_choice_libelles(
        linked_libelles,
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    )
    if fonction_in_choice_list(fn, choices):
        return True
    if subdivision and fonction_matches_qualified_poste(fn, subdivision):
        return True
    for label in postes_tete_libelles(
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    ):
        if fn.casefold() == label.casefold():
            return True
    return False


def canonical_fonction_libelle(
    fonction: str | None,
    *,
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> str:
    """Normalise vers le libellé qualifié attendu quand c'est un poste de tête."""
    fn = (fonction or "").strip()
    if not fn:
        return fn
    if subdivision and fonction_matches_qualified_poste(fn, subdivision):
        expected = qualified_poste_libelle(subdivision)
        if expected:
            return expected
    for label in postes_tete_libelles(
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    ):
        if fn.casefold() == label.casefold():
            return label
    choices = build_fonction_choice_libelles(
        [],
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    )
    for label in choices:
        if fn.casefold() == label.casefold():
            return label
    return fn


def merge_fonction_suggestions(
    *,
    current: str | None,
    used_same_subdivision: list[str],
    used_same_structure: list[str],
    linked_and_expected: list[str],
) -> list[str]:
    """Ordre de la combo Édition SME : valeur en cours, fonctions déjà portées, puis postes attendus."""
    out: list[str] = []
    seen: set[str] = set()

    def push(value: str | None) -> None:
        text = (value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(text)

    push(current)
    for value in used_same_subdivision:
        push(value)
    for value in used_same_structure:
        push(value)
    for value in linked_and_expected:
        push(value)
    return out
