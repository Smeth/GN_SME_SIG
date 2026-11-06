"""Page QStackedWidget chargée à la première visite (économie RAM au démarrage)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class LazyStackPage(QWidget):
    """Construit le widget réel après affichage (navigation réactive)."""

    built = Signal(object)

    def __init__(
        self,
        factory: Callable[[], QWidget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._child: QWidget | None = None
        self._building = False
        self._build_error: Exception | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._placeholder = QLabel("Chargement de la vue…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("lazyPagePlaceholder")
        lay.addWidget(self._placeholder)

    def built_widget(self) -> QWidget | None:
        return self._child

    def build_error(self) -> Exception | None:
        return self._build_error

    def ensure_built(self) -> QWidget | None:
        if self._child is not None:
            return self._child
        if not self._building:
            self._building = True
            self._build_error = None
            self._placeholder.setText("Chargement de la vue…")
            self._placeholder.show()
            QTimer.singleShot(0, self._build_deferred)
        return self._child

    def _build_deferred(self) -> None:
        app = QApplication.instance()
        try:
            if self._child is None:
                if app is not None:
                    app.processEvents()
                try:
                    self._child = self._factory()
                except Exception as exc:
                    self._build_error = exc
                    self._placeholder.setText(
                        f"Impossible d'ouvrir cette vue.\n\n{type(exc).__name__}: {exc}"
                    )
                    self._placeholder.setWordWrap(True)
                    return
                lay = self.layout()
                if lay is not None:
                    lay.removeWidget(self._placeholder)
                self._placeholder.hide()
                lay.addWidget(self._child)  # type: ignore[union-attr]
                self.built.emit(self._child)
                if app is not None:
                    app.processEvents()
        finally:
            self._building = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._child is None:
            self.ensure_built()
