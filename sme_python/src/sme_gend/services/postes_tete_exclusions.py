"""Postes de tête retirés de l'affichage alors qu'ils ne sont pas au catalogue.

Ces sièges sont recalculés à chaque lecture (subdivision → CHEF DE …).
Sans cette liste, Supprimer ne peut pas les faire disparaître.
"""

from __future__ import annotations

from typing import Iterable

from sme_gend.config.settings import Settings
from sme_gend.services.action_permissions import requires_any_permission
from sme_gend.services.shared_config_write import load_shared_dict,save_shared_model
from sme_gend.services.shared_reference_write import shared_reference_write
from functools import wraps


def _atomic_exclusion_intent(method):
    @wraps(method)
    def write(settings,*args,**kwargs):
        with shared_reference_write(settings):return method(settings,*args,**kwargs)
    return write
from sme_gend.services.fonction_structure_policy import fold_label

KEY_POSTES_TETE_EXCLUS = "postes_tete_exclus"


def poste_exclusion_key(
    structure_code: str | None,
    subdivision: str | None,
    poste: str | None,
) -> tuple[str, str, str]:
    return (
        fold_label(structure_code),
        fold_label(subdivision),
        fold_label(poste),
    )


def load_exclusion_keys(settings: Settings) -> set[tuple[str, str, str]]:
    raw = load_shared_dict(settings, KEY_POSTES_TETE_EXCLUS)
    items = raw.get("items") if isinstance(raw, dict) else None
    keys: set[tuple[str, str, str]] = set()
    if not isinstance(items, list):
        return keys
    for item in items:
        if not isinstance(item, dict):
            continue
        key = poste_exclusion_key(
            str(item.get("structure") or ""),
            str(item.get("subdivision") or ""),
            str(item.get("poste") or ""),
        )
        if key[0] and key[2]:
            keys.add(key)
    return keys


@requires_any_permission('referentiels.fonctions')
@_atomic_exclusion_intent
def exclude_expected_poste(
    settings: Settings,
    structure_code: str,
    subdivision: str | None,
    poste: str,
) -> None:
    key = poste_exclusion_key(structure_code, subdivision, poste)
    if not key[0] or not key[2]:
        return
    raw = load_shared_dict(settings, KEY_POSTES_TETE_EXCLUS)
    items = list(raw.get("items") or []) if isinstance(raw, dict) else []
    kept: list[dict[str, str]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        existing = poste_exclusion_key(
            str(item.get("structure") or ""),
            str(item.get("subdivision") or ""),
            str(item.get("poste") or ""),
        )
        if existing in seen or not existing[0] or not existing[2]:
            continue
        seen.add(existing)
        kept.append(
            {
                "structure": str(item.get("structure") or "").strip(),
                "subdivision": str(item.get("subdivision") or "").strip(),
                "poste": str(item.get("poste") or "").strip(),
            }
        )
    if key not in seen:
        kept.append(
            {
                "structure": (structure_code or "").strip(),
                "subdivision": (subdivision or "").strip(),
                "poste": (poste or "").strip(),
            }
        )
    raw["items"]=kept
    save_shared_model(settings, KEY_POSTES_TETE_EXCLUS,raw)


@_atomic_exclusion_intent
def clear_expected_poste(
    settings: Settings,
    structure_code: str,
    poste: str,
) -> None:
    """Réaffiche le siège si la fonction est recréée pour cette structure."""
    target_structure = fold_label(structure_code)
    target_poste = fold_label(poste)
    if not target_structure or not target_poste:
        return
    raw = load_shared_dict(settings, KEY_POSTES_TETE_EXCLUS)
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        return
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (
            fold_label(str(item.get("structure") or "")) == target_structure
            and fold_label(str(item.get("poste") or "")) == target_poste
        ):
            continue
        kept.append(item)
    if len(kept) != len(items):
        raw["items"]=kept
        save_shared_model(settings, KEY_POSTES_TETE_EXCLUS,raw)


def filter_excluded_postes(settings: Settings, rows: Iterable) -> list:
    keys = load_exclusion_keys(settings)
    if not keys:
        return list(rows)
    kept = []
    for row in rows:
        key = poste_exclusion_key(
            getattr(row, "structure_code", None),
            getattr(row, "subdivision", None),
            getattr(row, "poste", None),
        )
        if key in keys:
            continue
        kept.append(row)
    return kept
