"""Chemins vers ressources embarquées (icônes, styles)."""

from __future__ import annotations

from pathlib import Path


def _sme_gend_root() -> Path:
    """Racine du package `sme_gend` (parent de `ui/`)."""
    return Path(__file__).resolve().parents[1]


def icon_path(name: str) -> Path:
    return _sme_gend_root() / "resources" / "icons" / name


def style_path(name: str) -> Path:
    return _sme_gend_root() / "resources" / "styles" / name


def app_icon_path() -> Path | None:
    """Icône applicative (grenade) : PNG embarqué, repli SVG."""
    for name in ("app.png", "app.svg"):
        p = icon_path(name)
        if p.is_file():
            return p
    data_png = _sme_gend_root().parents[1] / "data" / "app.png"
    if data_png.is_file():
        return data_png
    return None


def load_app_icon():
    """Icône fenêtre / barre des tâches."""
    from PySide6.QtGui import QIcon

    p = app_icon_path()
    if p is not None:
        return QIcon(str(p))
    return QIcon()
