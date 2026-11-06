"""Écran de démarrage (splash) lisible — fond brand, texte contrasté."""

from __future__ import annotations

from sme_gend.config.branding import APP_NAME, APP_SUBTITLE, SME_FULL
from sme_gend.ui.resources import app_icon_path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen

_SPLASH_W = 480
_SPLASH_H = 240
_BG = QColor("#1a237e")
_TITLE = QColor("#ffffff")
_SUBTITLE = QColor("#c5cae9")
_STATUS = QColor("#e8eaf6")
_LOGO_SIZE = 64
_LOGO_LEFT = 24
_LOGO_TOP = 24
_TEXT_LEFT = 108
_TEXT_RIGHT_MARGIN = 20
_TITLE_TOP = 24
_TITLE_HEIGHT = 26
_SUBTITLE_TOP = 56
_SUBTITLE_HEIGHT = 40
_STATUS_TOP = 168
_STATUS_HEIGHT = 56
_STATUS_HMARGIN = 20


def _screen_device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    try:
        return float(screen.devicePixelRatio()) if screen else 1.0
    except Exception:
        return 1.0


def _paint_splash_pixmap(status: str = "") -> QPixmap:
    # Peint à taille logique fixe puis fixe devicePixelRatio sur le pixmap final :
    # sans ça, sur un écran Windows mis à l'échelle (125/150/200 %), le pixmap
    # brut est étiré par l'OS après coup et le texte se retrouve mal placé /
    # superposé (constaté en usage réel).
    dpr = _screen_device_pixel_ratio()
    pix = QPixmap(int(_SPLASH_W * dpr), int(_SPLASH_H * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(_BG)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    logo = app_icon_path()
    if logo is not None:
        p.drawPixmap(
            _LOGO_LEFT, _LOGO_TOP, QIcon(str(logo)).pixmap(_LOGO_SIZE, _LOGO_SIZE)
        )

    text_width = _SPLASH_W - _TEXT_LEFT - _TEXT_RIGHT_MARGIN

    p.setPen(_TITLE)
    p.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
    p.drawText(
        QRect(_TEXT_LEFT, _TITLE_TOP, text_width, _TITLE_HEIGHT),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        APP_NAME,
    )

    p.setPen(_SUBTITLE)
    p.setFont(QFont("Segoe UI", 9))
    p.drawText(
        QRect(_TEXT_LEFT, _SUBTITLE_TOP, text_width, _SUBTITLE_HEIGHT),
        int(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap
        ),
        f"{APP_SUBTITLE} — {SME_FULL}",
    )

    if status:
        p.setPen(_STATUS)
        p.setFont(QFont("Segoe UI", 10))
        status_rect = QRect(
            _STATUS_HMARGIN,
            _STATUS_TOP,
            _SPLASH_W - 2 * _STATUS_HMARGIN,
            _STATUS_HEIGHT,
        )
        p.drawText(
            status_rect,
            int(
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap
            ),
            status.strip(),
        )
    p.end()
    return pix


def create_startup_splash(initial_message: str = "Démarrage…") -> QSplashScreen:
    """Splash avec pixmap explicite (évite le carré blanc Qt par défaut)."""
    msg = (initial_message or "").strip()
    splash = QSplashScreen(_paint_splash_pixmap(msg))
    splash.setFont(QFont("Segoe UI", 10))
    return splash


def splash_show_status(splash: QSplashScreen, message: str) -> None:
    """Met à jour le message du bas (pixmap unique — pas de showMessage Qt superposé)."""
    splash.setPixmap(_paint_splash_pixmap(message))
    splash.show()
    splash.repaint()


def splash_hide_for_modal(splash: QSplashScreen | None) -> None:
    """Masque le splash pour qu'une boîte de dialogue modiale soit visible (reste au-dessus sinon)."""
    if splash is None:
        return
    splash.hide()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def splash_show_after_modal(splash: QSplashScreen | None, message: str = "") -> None:
    """Réaffiche le splash après une fenêtre modale."""
    if splash is None:
        return
    if message:
        splash_show_status(splash, message)
    else:
        splash.show()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.processEvents()
