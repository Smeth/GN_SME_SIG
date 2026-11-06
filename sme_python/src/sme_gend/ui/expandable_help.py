"""Optional explanations kept out of the main data area by default."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QToolButton, QVBoxLayout, QWidget


class ExpandableHelp(QWidget):
    def __init__(self, text, parent=None, *, label='Aide'):
        super().__init__(parent)
        self.button = QToolButton(parent)
        self.button.setText(label)
        self.button.setCheckable(True)
        self.button.setArrowType(Qt.ArrowType.RightArrow)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setToolTip('Afficher ou masquer les explications de cet écran.')
        self.text = QLabel(text, self)
        self.text.setWordWrap(True)
        self.text.setObjectName('settingsPathsHint')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.text)
        self.button.toggled.connect(self._toggle)
        self.hide()

    def _toggle(self, visible):
        self.setVisible(visible)
        self.button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
