"""Tests positions administratives — dédoublonnage filtres."""

from __future__ import annotations

from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.services.padmin_label_format import padmin_combo_items, resolve_padmin_label
from sme_gend.ui.personnel_list_filters import padmin_matches_filter


def test_resolve_padmin_label_case_and_accents() -> None:
    padmins = [PadminRefRow(libelle="Présent", compte_comme_present=True, ordre_tri=1)]
    assert resolve_padmin_label("present", padmins) == "Présent"
    assert resolve_padmin_label("PRÉSENT", padmins) == "Présent"


def test_padmin_combo_items_dedupes() -> None:
    padmins = [
        PadminRefRow(libelle="Présent", compte_comme_present=True, ordre_tri=1),
        PadminRefRow(libelle="Malade", compte_comme_present=False, ordre_tri=2),
    ]
    items = padmin_combo_items(padmins, extra_raw=["present", "MALADE", "Présent"])
    labels = [label for label, _ in items]
    assert labels == ["Malade", "Présent"]


def test_padmin_matches_filter_canonical() -> None:
    padmins = [PadminRefRow(libelle="Présent", compte_comme_present=True, ordre_tri=1)]
    assert padmin_matches_filter("present", "Présent", padmins=padmins)
    assert padmin_matches_filter("Présent", "Présent", padmins=padmins)
