"""Liste déroulante des fonctions d'une structure, postes déjà tenus désactivés."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from sme_gend.services.fonction_occupancy import FonctionChoiceState


def fill_fonction_combo(
    combo: QComboBox,
    states: list[FonctionChoiceState],
    *,
    current: str | None,
) -> None:
    combo.clear()
    combo.addItem("", None)
    wanted = (current or "").strip()
    selected = 0
    for state in states:
        combo.addItem(state.label, state.libelle)
        item = combo.model().item(combo.count() - 1)
        if item is not None:
            item.setEnabled(state.enabled)
            if state.tooltip:
                item.setToolTip(state.tooltip)
        if wanted and state.libelle.casefold() == wanted.casefold():
            selected = combo.count() - 1
    combo.setCurrentIndex(selected)
    if combo.isEditable() and wanted and selected == 0:
        combo.setCurrentText(wanted)
