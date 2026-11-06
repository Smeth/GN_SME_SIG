"""En-tête de page unifié (titre + sous-titre)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PageHeader(QWidget):
    def __init__(
        self,
        title: str,
        subtitle: str | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")

        lbl_title = QLabel(title)
        lbl_title.setObjectName("dashTitle")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(6)
        lay.addWidget(lbl_title)

        if subtitle:
            lbl_sub = QLabel(subtitle)
            lbl_sub.setObjectName("dashSubtitle")
            lbl_sub.setWordWrap(True)
            lbl_sub.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(lbl_sub)
