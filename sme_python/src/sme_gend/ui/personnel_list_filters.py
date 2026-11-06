"""Filtres texte / position admin partagés (liste générale)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.search_text import search_values

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtWidgets import QComboBox

    from sme_gend.models.grade_ref import GradeRefRow


def padmin_matches_filter(
    row_padmin: str | None,
    selected: str | None,
    *,
    padmins: "Sequence[object] | None" = None,
) -> bool:
    """Compare positions admin (insensible à la casse / accents légers)."""
    if selected is None:
        return True
    from sme_gend.services.padmin_label_format import resolve_padmin_label

    a = resolve_padmin_label(row_padmin, padmins) if padmins else (row_padmin or "").strip()
    b = resolve_padmin_label(selected, padmins) if padmins else (selected or "").strip()
    if not b:
        return True
    if not a:
        return False
    if a == b or a.casefold() == b.casefold():
        return True
    from sme_gend.padmin_present import _collapsed

    return _collapsed(a) == _collapsed(b)


def grade_matches_filter(
    row_grade: str | None,
    selected_code: str | None,
    *,
    combo_display: str | None = None,
    grades: "Sequence[GradeRefRow] | None" = None,
) -> bool:
    """Compare grade affiché (libellé ou code) au code choisi dans le combo."""
    if selected_code is None:
        return True
    g = (row_grade or "").strip()
    c = (selected_code or "").strip()
    if not c:
        return True
    if not g:
        return False
    if g == c or g.casefold() == c.casefold():
        return True
    if grades:
        from sme_gend.services.grade_label_format import format_grade_display

        row_code = format_grade_display(g, grades)
        sel_code = format_grade_display(c, grades)
        if row_code and sel_code and row_code.casefold() == sel_code.casefold():
            return True
    disp = (combo_display or "").strip()
    if disp and disp not in {"(Tous)", "Toutes"}:
        lib = disp.split(" — ", 1)[-1].strip() if " — " in disp else disp
        if g == lib or g.casefold() == lib.casefold():
            return True
        head = disp.split(" — ", 1)[0].strip() if " — " in disp else ""
        if head and (g == head or g.casefold() == head.casefold()):
            return True
    return False


def combo_filter_value(combo: "QComboBox") -> str | None:
    """Code filtre depuis userData, ou préfixe « CODE — libellé » du texte affiché."""
    data = combo.currentData()
    if data is not None:
        s = str(data).strip()
        if s:
            return s
    text = (combo.currentText() or "").strip()
    if not text or text in {"(Tous)", "Toutes", "(Tous les grades)"}:
        return None
    if text.casefold().startswith("(tous"):
        return None
    if " — " in text:
        head = text.split(" — ", 1)[0].strip()
        return head or None
    return text


def structure_matches_filter(row_structure: str | None, selected: str | None) -> bool:
    """Compare code structure (insensible à la casse, espaces)."""
    if selected is None:
        return True
    a = (row_structure or "").strip()
    b = (selected or "").strip()
    if not b:
        return True
    if not a:
        return False
    if " — " in b:
        b = b.split(" — ", 1)[0].strip() or b
    if a == b or a.casefold() == b.casefold():
        return True
    # Repli : code en préfixe « CODE — désignation » stocké en base
    if a.casefold().startswith(b.casefold() + " —") or a.casefold().startswith(b.casefold() + " -"):
        return True
    return False


def free_text_matches(row: ListeGeneraleRow, needle: str, *, grades=None) -> bool:
    fields = (
        "id", "noms", "sexe", "grade", "fonction", "structure", "position_admin",
        "subdivision_arme", "matricule_militaire", "matricule_solde", "diplome", "obs",
        "lieu_naissance", "date_naissance", "date_grade", "date_service", "ndecision",
        "date_obtention", "ordre_tri", "presseance",
    )
    values = [getattr(row, key, None) for key in fields]
    if grades:
        from sme_gend.services.grade_label_format import format_grade_display
        values.append(format_grade_display(row.grade, grades))
    return search_values(needle, *values)
