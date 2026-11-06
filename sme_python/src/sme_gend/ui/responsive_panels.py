"""Wrapping controls and scrollable panes for document consultation screens."""
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtWidgets import QLayout, QSplitter, QScrollArea, QFrame


class WrapLayout(QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left()+margins.right(), margins.top()+margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            width = min(hint.width(), area.width())
            if height and x + width > area.right()+1:
                x, y, height = area.x(), y+height+self.spacing(), 0
            if apply:
                item.setGeometry(QRect(x, y, width, hint.height()))
            x += width+self.spacing()
            height = max(height, hint.height())
        return y-area.y()+height+margins.top()+margins.bottom()


def scroll_panel(widget):
    area = QScrollArea()
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setMinimumSize(0, 0)
    return area


def tab_content(tabs):
    page = tabs.currentWidget()
    return page.widget() if isinstance(page, QScrollArea) else page


class ResponsiveSplitter(QSplitter):
    """Keep both panes reachable; stack them when two columns become cramped."""
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setChildrenCollapsible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Orientation.Vertical if self.width() < 1150 else Qt.Orientation.Horizontal
        if self.count() > 1:
            pane = self.widget(1)
            content = pane.widget()
            pane.setMinimumWidth(content.minimumSizeHint().width()+24 if orientation == Qt.Orientation.Horizontal else 0)
        if orientation != self.orientation():
            self.setOrientation(orientation)
            total = self.height() if orientation == Qt.Orientation.Vertical else self.width()
            self.setSizes([int(total*.65), int(total*.35)])
