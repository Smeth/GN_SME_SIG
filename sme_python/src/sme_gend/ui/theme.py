"""Thème applicatif : clair, sombre, ou suivi du mode système."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QTextBrowser, QWidget

PREVIEW_PAPER_BG = "#ffffff"
PREVIEW_PAPER_TEXT = "#212121"
PREVIEW_PAPER_SURROUND_DARK = "#d0d4dc"

from sme_gend.ui.resources import style_path

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM = "system"


def detect_system_theme() -> str:
    """Retourne ``light`` ou ``dark`` selon le schéma couleur de l'OS / Qt."""
    app = QApplication.instance()
    if app is None:
        return THEME_LIGHT
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None and callable(scheme):
        try:
            if scheme() == Qt.ColorScheme.Dark:
                return THEME_DARK
            if scheme() == Qt.ColorScheme.Light:
                return THEME_LIGHT
        except Exception:
            pass
    # Windows 10/11 : palette fenêtre
    try:
        pal = app.palette()
        window = pal.color(pal.ColorRole.Window)
        if window.lightness() < 128:
            return THEME_DARK
    except Exception:
        pass
    return THEME_LIGHT


def resolve_effective_theme(preference: str) -> str:
    """``system`` → détection OS ; sinon ``light`` / ``dark``."""
    pref = (preference or THEME_LIGHT).strip().lower()
    if pref == THEME_SYSTEM:
        return detect_system_theme()
    if pref == THEME_DARK:
        return THEME_DARK
    return THEME_LIGHT


def effective_theme(preference: str | None = None) -> str:
    """Thème réellement appliqué (``light`` ou ``dark``)."""
    if preference in (THEME_LIGHT, THEME_DARK):
        return preference
    app = QApplication.instance()
    if preference is None and app is not None:
        eff = app.property("sme_theme_effective")
        if eff in (THEME_LIGHT, THEME_DARK):
            return str(eff)
    pref = preference
    if pref is None and app is not None:
        pref = app.property("sme_theme_preference")
    if pref is None:
        from sme_gend.config.sme_context import load_sme_context

        pref = load_sme_context().theme
    return resolve_effective_theme(str(pref or THEME_LIGHT))


def is_dark(preference_or_effective: str | None = None) -> bool:
    return effective_theme(preference_or_effective) == THEME_DARK


def theme_palette(preference: str | None = None) -> dict[str, str]:
    """Couleurs pour styles inline (graphiques, HTML, cartes)."""
    dark = effective_theme(preference) == THEME_DARK
    if dark:
        return {
            "bg": "#1a1b2e",
            "bg_card": "#252636",
            "bg_elevated": "#2a2b40",
            "border": "#3d3e5c",
            "text": "#e8eaf6",
            "text_muted": "#9fa8da",
            "text_dim": "#7986cb",
            "accent": "#ffd54f",
            "chart_bg": "#252636",
            "chart_label": "#e8eaf6",
            "success": "#a5d6a7",
            "warning": "#ffcc80",
            "error": "#ef9a9a",
            "info": "#90caf9",
            "bar_bg": "#3d3e5c",
            "link": "#26c6da",
        }
    return {
        "bg": "#f0f2f5",
        "bg_card": "#ffffff",
        "bg_elevated": "#fafafa",
        "border": "#e0e0e0",
        "text": "#212121",
        "text_muted": "#424242",
        "text_dim": "#757575",
        "accent": "#1a237e",
        "chart_bg": "#ffffff",
        "chart_label": "#212121",
        "success": "#2e7d32",
        "warning": "#f57f17",
        "error": "#c62828",
        "info": "#1565c0",
        "bar_bg": "#f5f5f5",
        "link": "#26c6da",
    }


def build_application_palette(effective: str) -> QPalette:
    """Palette Qt explicite, indépendante du mode sombre/clair de l'OS."""
    pal = QPalette()
    if effective == THEME_DARK:
        window = QColor("#1a1b2e")
        base = QColor("#252636")
        alt = QColor("#2a2b40")
        text = QColor("#e8eaf6")
        button = QColor("#2a2b40")
        border = QColor("#3d3e5c")
        accent = QColor("#3949ab")
        placeholder = QColor("#7986cb")
        tooltip_bg = QColor("#252636")
    else:
        window = QColor("#f0f2f5")
        base = QColor("#ffffff")
        alt = QColor("#fafafa")
        text = QColor("#212121")
        button = QColor("#ffffff")
        border = QColor("#e0e0e0")
        accent = QColor("#3949ab")
        placeholder = QColor("#757575")
        tooltip_bg = QColor("#ffffff")

    roles = QPalette.ColorRole
    pal.setColor(roles.Window, window)
    pal.setColor(roles.WindowText, text)
    pal.setColor(roles.Base, base)
    pal.setColor(roles.AlternateBase, alt)
    pal.setColor(roles.Text, text)
    pal.setColor(roles.Button, button)
    pal.setColor(roles.ButtonText, text)
    pal.setColor(roles.BrightText, QColor("#ffffff"))
    pal.setColor(roles.Highlight, accent)
    pal.setColor(roles.HighlightedText, QColor("#ffffff"))
    pal.setColor(roles.PlaceholderText, placeholder)
    pal.setColor(roles.ToolTipBase, tooltip_bg)
    pal.setColor(roles.ToolTipText, text)
    pal.setColor(roles.Mid, border)
    pal.setColor(roles.Dark, border.darker(110))
    pal.setColor(roles.Light, border.lighter(110))
    return pal


def _apply_qt_color_scheme(app: QApplication, effective: str) -> None:
    """Force le schéma couleur Qt (découple l'app du mode OS quand thème explicite)."""
    hints = app.styleHints()
    setter = getattr(hints, "setColorScheme", None)
    if setter is None:
        return
    scheme = Qt.ColorScheme.Dark if effective == THEME_DARK else Qt.ColorScheme.Light
    try:
        setter(scheme)
    except Exception:
        pass


def apply_app_theme(app: QApplication, preference: str = "light") -> str:
    """
    Applique le QSS selon la préférence (y compris ``system``).
    Retourne le thème effectif appliqué (``light`` ou ``dark``).
    """
    effective = resolve_effective_theme(preference)
    app.setProperty("sme_theme_preference", preference)
    app.setProperty("sme_theme_effective", effective)
    _apply_qt_color_scheme(app, effective)
    app.setPalette(build_application_palette(effective))
    qss_name = "app_dark.qss" if effective == THEME_DARK else "app.qss"
    p = style_path(qss_name)
    if not p.is_file():
        p = style_path("app.qss")
    app.setStyle("Fusion")
    if p.is_file():
        with open(p, encoding="utf-8") as f:
            from sme_gend.ui.resources import icon_path

            from sme_gend.ui.interface_zoom import set_theme_stylesheet
            set_theme_stylesheet(app, f.read().replace(
                "__CHECKBOX_TICK__", icon_path("checkbox-tick.svg").as_posix(),
            ))
    else:
        from sme_gend.ui.interface_zoom import set_theme_stylesheet
        set_theme_stylesheet(app, '')
    return effective


def status_semantic_color(kind: str) -> str:
    """Couleur lisible pour messages de statut (succès, erreur, attente…)."""
    pal = theme_palette()
    return {
        "muted": pal["text_dim"],
        "success": pal["success"],
        "warning": pal["warning"],
        "error": pal["error"],
        "info": pal["info"],
    }.get(kind, pal["text"])


def alert_banner_stylesheet(*, loading: bool = False, warning: bool = False) -> str:
    """Bandeau alertes (liste générale, édition SME) — clair / sombre."""
    pal = theme_palette()
    if loading:
        bg = "#1e2a3a" if is_dark() else "#e3f2fd"
        fg = pal["info"]
        border = "#2a4a6a" if is_dark() else "#90caf9"
    elif warning:
        bg = "#3d3520" if is_dark() else "#fff3e0"
        fg = pal["warning"]
        border = "#5c4a2a" if is_dark() else "#ffcc80"
    else:
        bg = pal["bg_elevated"]
        fg = pal["text_muted"]
        border = pal["border"]
    return (
        f"background: {bg}; color: {fg}; padding: 5px 10px; "
        f"border-radius: 6px; border: 1px solid {border};"
    )


def repolish_widget(widget: QWidget) -> None:
    """Réapplique le QSS après changement de propriété dynamique."""
    st = widget.style()
    if st is not None:
        st.unpolish(widget)
        st.polish(widget)
    widget.update()


def apply_paper_preview_palette(browser: QTextBrowser) -> None:
    """
    Aperçu « document papier » : texte sombre explicite, indépendant du thème app.
    Évite le texte clair hérité de la palette sombre sur fond blanc (QTextBrowser).
    """
    surround = PREVIEW_PAPER_SURROUND_DARK if is_dark() else PREVIEW_PAPER_BG
    pal = QPalette()
    roles = QPalette.ColorRole
    pal.setColor(roles.Base, QColor(PREVIEW_PAPER_BG))
    pal.setColor(roles.Text, QColor(PREVIEW_PAPER_TEXT))
    pal.setColor(roles.Window, QColor(surround))
    pal.setColor(roles.WindowText, QColor(PREVIEW_PAPER_TEXT))
    browser.setPalette(pal)
    browser.document().setDefaultStyleSheet(
        "body, p, td, th, div, span, li, b, strong { "
        f"color: {PREVIEW_PAPER_TEXT}; }}"
    )


def propagate_theme_refresh(root: QWidget) -> None:
    """Rafraîchit les styles inline des vues qui exposent ``refresh_theme_styles``."""
    seen: set[int] = set()

    def _walk(widget: QWidget) -> None:
        wid = id(widget)
        if wid in seen:
            return
        seen.add(wid)
        fn = getattr(widget, "refresh_theme_styles", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
        for child in widget.findChildren(QWidget):
            _walk(child)

    _walk(root)


def install_system_theme_watcher(app: QApplication, callback: Callable[[], None]) -> None:
    """Rappelle ``callback`` quand le schéma système change (si supporté par Qt)."""
    hints = app.styleHints()

    def _forward(_scheme=None) -> None:
        callback()

    signal = getattr(hints, "colorSchemeChanged", None)
    if signal is not None:
        try:
            signal.connect(_forward)
        except Exception:
            pass
