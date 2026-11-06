"""Session-local interface zoom, always calculated from the unscaled theme."""
import re
from PySide6.QtGui import QFont

MIN_ZOOM = 75
MAX_ZOOM = 150


def zoom_percent(app):
    return int(app.property('sme_interface_zoom') or 100)


def scaled_stylesheet(text, percent):
    if percent == 100:
        return text
    properties = r'(?:font-size|(?:min-|max-)?(?:width|height)|padding(?:-[a-z]+)?|margin(?:-[a-z]+)?|border-radius)'
    def declaration(match):
        value = re.sub(r'(-?\d+(?:\.\d+)?)(px|pt)\b',
                       lambda number: f'{round(float(number[1]) * percent / 100, 2):g}{number[2]}', match[2])
        return match[1] + value
    return re.sub(r'(' + properties + r'\s*:\s*)([^;{}]+)', declaration, text)


def set_theme_stylesheet(app, stylesheet):
    app.setProperty('sme_unscaled_stylesheet', stylesheet)
    if app.property('sme_unscaled_font') is None:
        app.setProperty('sme_unscaled_font', QFont(app.font()))
    set_interface_zoom(app, zoom_percent(app))


def set_interface_zoom(app, percent):
    percent = max(MIN_ZOOM, min(MAX_ZOOM, int(percent)))
    if app.property('sme_unscaled_stylesheet') is None:
        app.setProperty('sme_unscaled_stylesheet', app.styleSheet())
    if app.property('sme_unscaled_font') is None:
        app.setProperty('sme_unscaled_font', QFont(app.font()))
    app.setProperty('sme_interface_zoom', percent)
    font = QFont(app.property('sme_unscaled_font'))
    # Themes style every QWidget. Keep the application default unchanged so
    # QTextDocument-based printouts are not enlarged by an interface preference.
    if not app.property('sme_unscaled_stylesheet'):
        if font.pixelSize() > 0:
            font.setPixelSize(max(1, round(font.pixelSize() * percent / 100)))
        else:
            font.setPointSizeF(max(1, font.pointSizeF() * percent / 100))
    if app.font() != font:
        app.setFont(font)
    stylesheet = scaled_stylesheet(str(app.property('sme_unscaled_stylesheet') or ''), percent)
    # Reapplying an identical global QSS repolishes every cached page.
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)
    return percent
