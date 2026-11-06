"""Champ de recherche disponible dans la page ou l'onglet actuellement visible."""
from PySide6.QtWidgets import QLineEdit, QWidget


def page_search_field(page: QWidget | None) -> QLineEdit | None:
    if page is None:
        return None
    for name in ("search", "_search"):
        field = getattr(page, name, None)
        if isinstance(field, QLineEdit) and field.isEnabled() and field.isVisibleTo(page):
            return field
    for field in page.findChildren(QLineEdit):
        hint = field.placeholderText().casefold()
        if (field.isEnabled() and field.isVisibleTo(page)
                and (field.property("pageSearch") or any(word in hint for word in ("recherch", "filtr", "nom,")))):
            return field
    return None
