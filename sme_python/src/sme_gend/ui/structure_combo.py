"""Remplissage de QComboBox structure (code en userData)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox

from sme_gend.models.structure import StructureRow
from sme_gend.services.structure_label_format import (
    format_structure_display,
    structure_combo_items,
    structure_combo_label,
)


def fill_structure_combo(
    combo: QComboBox,
    structures: Sequence[StructureRow],
    current_code: str | None = None,
    *,
    include_empty: bool = True,
    empty_label: str = "",
    extra_codes: Sequence[str] | None = None,
    all_label: str | None = None,
) -> None:
    """Remplit une combo : libellé « code — désignation », valeur = code."""
    with QSignalBlocker(combo):
        combo.clear()
        start_idx = 0
        if all_label is not None:
            combo.addItem(all_label, None)
            start_idx = 1
        elif include_empty:
            combo.addItem(empty_label, None)
            start_idx = 1
        for label, code in structure_combo_items(structures, extra_codes):
            combo.addItem(label, code)
        cur = (current_code or "").strip()
        idx = start_idx
        if cur:
            found = False
            for i in range(combo.count()):
                data = combo.itemData(i)
                if data is not None and str(data).casefold() == cur.casefold():
                    idx = i
                    found = True
                    break
            if not found:
                combo.addItem(format_structure_display(cur, structures), cur)
                idx = combo.count() - 1
        combo.setCurrentIndex(idx)


def add_structure_combo_entry(
    combo: QComboBox,
    code: str,
    designation: str | None = None,
) -> None:
    label = structure_combo_label(code, designation)
    for i in range(combo.count()):
        if combo.itemData(i) == code:
            combo.setItemText(i, label)
            return
    combo.addItem(label, code)
