"""Combos / spinboxes sans changement accidentel à la molette (sauf focus clavier)."""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox


class NoWheelComboBox(QComboBox):
    """Aucun changement de valeur à la molette.

    En Édition SME chaque frappe est **enregistrée en base immédiatement** :
    une molette accidentelle (au survol du champ ou à l'ouverture de la
    liste) modifierait la donnée sans annulation possible par l'utilisateur.
    La molette est donc ignorée sur le combo comme sur sa liste déroulante ;
    on navigue au clic ou au clavier (flèches / Tab).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        view = self.view()
        if view is not None and view.viewport() is not None:
            view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            view = self.view()
            if view is not None and obj is view.viewport():
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDateEdit(QDateEdit):
    """Ignore la molette sauf si le champ a le focus (évite de changer la date en défilant)."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
