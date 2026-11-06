"""Affichage menu controls; no shared preferences or database writes."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication
from sme_gend.ui.interface_zoom import set_interface_zoom, zoom_percent, MIN_ZOOM, MAX_ZOOM


class DisplayControls(QObject):
    def __init__(self, window, menu):
        super().__init__(window)
        self.window = window
        self._previous_state = Qt.WindowState.WindowNoState
        menu.addSeparator()
        self.fullscreen = QAction('Plein écran', window)
        self.fullscreen.setCheckable(True)
        self.fullscreen.setShortcut('F11')
        self.fullscreen.triggered.connect(self.toggle_fullscreen)
        menu.addAction(self.fullscreen)
        self.zoom_menu = menu.addMenu('Zoom')
        self.zoom_in = self.zoom_menu.addAction('Agrandir')
        self.zoom_out = self.zoom_menu.addAction('Réduire')
        self.zoom_reset = self.zoom_menu.addAction('Revenir à 100 %')
        self.zoom_in.triggered.connect(lambda: self.set_zoom(zoom_percent(QApplication.instance()) + 10))
        self.zoom_out.triggered.connect(lambda: self.set_zoom(zoom_percent(QApplication.instance()) - 10))
        self.zoom_reset.triggered.connect(lambda: self.set_zoom(100))
        window.installEventFilter(self)
        self.sync()

    def set_zoom(self, percent):
        set_interface_zoom(QApplication.instance(), percent)
        self.sync()

    def toggle_fullscreen(self):
        if self.window.isFullScreen():
            if self._previous_state & Qt.WindowState.WindowMaximized:
                self.window.showMaximized()
            else:
                self.window.showNormal()
        else:
            self._previous_state = self.window.windowState()
            self.window.showFullScreen()
        self.sync()

    def sync(self):
        percent = zoom_percent(QApplication.instance())
        self.zoom_menu.setTitle(f'Zoom ({percent} %)')
        self.zoom_in.setEnabled(percent < MAX_ZOOM)
        self.zoom_out.setEnabled(percent > MIN_ZOOM)
        self.zoom_reset.setEnabled(percent != 100)
        self.fullscreen.setChecked(self.window.isFullScreen())
        self.fullscreen.setText('Quitter le plein écran' if self.window.isFullScreen() else 'Plein écran')

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.WindowStateChange:
            self.sync()
        return super().eventFilter(watched, event)
