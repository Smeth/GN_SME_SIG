"""Affichage et dédoublonnage des positions administratives."""

from __future__ import annotations

from collections.abc import Sequence

from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.padmin_present import _collapsed


def resolve_padmin_label(
    raw: str | None,
    padmins: Sequence[PadminRefRow] | None = None,
) -> str:
    """Ramène un libellé saisi vers le libellé canonique du référentiel."""
    s = (raw or "").strip()
    if not s:
        return ""
    cf = s.casefold()
    col = _collapsed(s)
    for p in padmins or ():
        lib = (p.libelle or "").strip()
        if not lib:
            continue
        if lib.casefold() == cf or _collapsed(lib) == col:
            return lib
    return s


def padmin_combo_items(
    padmins: Sequence[PadminRefRow],
    extra_raw: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """(libellé affiché, valeur filtre) — un libellé par position admin."""
    seen: set[str] = set()
    labels: list[str] = []

    def _add(label: str) -> None:
        canonical = resolve_padmin_label(label, padmins)
        if not canonical:
            return
        key = canonical.casefold()
        if key in seen:
            return
        seen.add(key)
        labels.append(canonical)

    for p in padmins:
        _add(p.libelle)
    for raw in extra_raw or ():
        _add(raw)

    labels.sort(key=lambda x: x.casefold())
    return [(label, label) for label in labels]
