"""Voile plein écran affiché lorsque la session auth a expiré."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from sme_gend.ui.theme import theme_palette


class SessionLockOverlay(QWidget):
    """Bloque l'interaction avec la fenêtre principale jusqu'à reconnexion."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionLockOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

        title = QLabel("Session expirée")
        title.setObjectName("sessionLockTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = title

        detail = QLabel(
            "Reconnectez-vous pour continuer.\n"
            "Les données à l'écran restent masquées tant que la session n'est pas rétablie."
        )
        detail.setObjectName("sessionLockDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        self._detail = detail

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.addStretch()
        lay.addWidget(title)
        lay.addSpacing(12)
        lay.addWidget(detail)
        lay.addStretch()

        self.refresh_theme()

    def set_context(self, *, title: str, detail: str) -> None:
        self._title.setText(title)
        self._detail.setText(detail)

    def refresh_theme(self) -> None:
        pal = theme_palette()
        self.setStyleSheet(
            f"QWidget#sessionLockOverlay {{ background-color: rgba(10, 14, 39, 0.92); }}"
            f"QLabel#sessionLockTitle {{ color: {pal['text']}; font-size: 22px; font-weight: 700; }}"
            f"QLabel#sessionLockDetail {{ color: {pal['text_dim']}; font-size: 13px; }}"
        )

    def sync_geometry(self) -> None:
        host = self.parentWidget()
        if host is not None:
            self.setGeometry(host.rect())
