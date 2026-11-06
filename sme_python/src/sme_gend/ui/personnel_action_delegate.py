"""Délégué Qt — colonne Actions : boutons Voir / Modifier / Supprimer peints par ligne."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from sme_gend.models.liste_generale import ListeGeneraleRow

_log = logging.getLogger("sme_gend")

ACT_VIEW = "view"
ACT_EDIT = "edit"
ACT_DELETE = "delete"

_BTN_W, _BTN_H, _GAP = 30, 22, 6

_COLORS: dict[str, tuple[str, str]] = {
    ACT_VIEW:   ("#f57c00", "#fff3e0"),
    ACT_EDIT:   ("#2e7d32", "#e8f5e9"),
    ACT_DELETE: ("#c62828", "#ffebee"),
}
_LABELS = {ACT_VIEW: "◉", ACT_EDIT: "✎", ACT_DELETE: "✕"}


def _row_from_model_index(model, index: QModelIndex) -> ListeGeneraleRow | None:
    """Résout la ligne personnel depuis le modèle (proxy ou source)."""
    row = index.data(Qt.ItemDataRole.UserRole)
    if row is not None:
        return row
    if hasattr(model, "mapToSource"):
        src = model.mapToSource(index)
        sm = model.sourceModel()
        if src.isValid() and sm is not None:
            return sm.data(src, Qt.ItemDataRole.UserRole)
    return None


class PersonnelActionDelegate(QStyledItemDelegate):
    """Peint 3 boutons (voir / modifier / supprimer) dans la colonne Actions."""

    ACTIONS_COL_WIDTH: int = 3 * _BTN_W + 2 * _GAP + 20

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._callback: Callable[[ListeGeneraleRow, str], None] | None = None

    def set_callback(self, fn: Callable[[ListeGeneraleRow, str], None]) -> None:
        self._callback = fn

    def _btn_rects(self, cell: QRect) -> list[tuple[str, QRect]]:
        total = 3 * _BTN_W + 2 * _GAP
        x0 = cell.left() + max(0, (cell.width() - total) // 2)
        y = cell.top() + max(0, (cell.height() - _BTN_H) // 2)
        out: list[tuple[str, QRect]] = []
        x = x0
        for act in (ACT_VIEW, ACT_EDIT, ACT_DELETE):
            out.append((act, QRect(x, y, _BTN_W, _BTN_H)))
            x += _BTN_W + _GAP
        return out

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self.ACTIONS_COL_WIDTH, 36)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        row_bg = index.siblingAtColumn(0).data(Qt.ItemDataRole.BackgroundRole)
        if row_bg is not None:
            painter.fillRect(option.rect, row_bg)
        elif option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif index.row() % 2 == 0:
            painter.fillRect(option.rect, option.palette.base())
        else:
            painter.fillRect(option.rect, option.palette.alternateBase())

        f = QFont(painter.font())
        f.setPointSize(max(8, f.pointSize() - 1))
        painter.setFont(f)

        for act, rect in self._btn_rects(option.rect):
            fg, bg = _COLORS[act]
            painter.setBrush(QColor(bg))
            painter.setPen(QColor(fg))
            painter.drawRoundedRect(rect, 4, 4)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _LABELS[act])

        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonDblClick:
            return False
        if event.type() not in (
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonPress,
        ):
            return False
        if not isinstance(event, QMouseEvent):
            return False
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if event.type() == QEvent.Type.MouseButtonPress:
            for _act, rect in self._btn_rects(option.rect):
                if rect.contains(pos):
                    return True
            return False
        row = _row_from_model_index(model, index)
        if row is None:
            _log.warning("Colonne Actions : ligne introuvable (index proxy/source)")
            return False
        for act, rect in self._btn_rects(option.rect):
            if rect.contains(pos):
                if self._callback:
                    try:
                        from PySide6.QtCore import QTimer

                        # Différer : évite exec() modal pendant le release souris.
                        QTimer.singleShot(0, lambda r=row, a=act: self._callback(r, a))
                    except Exception as exc:
                        _log.exception("Erreur action colonne Actions (ligne personnel)")
                        parent = self.parent()
                        if parent is not None:
                            from PySide6.QtWidgets import QMessageBox

                            QMessageBox.critical(
                                parent,
                                "Action personnel",
                                f"Impossible d'exécuter l'action :\n{exc}",
                            )
                return True
        return False
