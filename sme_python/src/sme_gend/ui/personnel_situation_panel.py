"""Bloc lecture seule « Situation en cours » (fiche personnel, dossier)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QWidget

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_situation_context import (
    build_situation_en_cours,
    section_title,
)


def situation_en_cours_groupbox(
    row: ListeGeneraleRow,
    settings: Settings | None,
    parent: QWidget | None = None,
) -> QGroupBox | None:
    """Groupe formulaire lecture seule, ou ``None`` si aucune situation particulière."""
    if settings is None:
        return None
    situation = build_situation_en_cours(row, settings)
    if situation is None:
        return None

    gb = QGroupBox(section_title(), parent)
    gb.setObjectName("PersonnelSituationEnCours")
    form = QFormLayout(gb)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    for label, value in situation.fields:
        lbl = QLabel(f"{label} :")
        lbl.setObjectName("PersonnelSituationLabel")
        val = QLabel(value or "—")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setObjectName("PersonnelSituationValue")
        form.addRow(lbl, val)

    return gb
