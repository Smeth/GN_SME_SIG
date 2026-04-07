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


def load_app_icon():
    """Icône fenêtre : `data/app.png` si présent, sinon ressource `app.svg`."""
    from PySide6.QtGui import QIcon

    data_png = _sme_gend_root().parents[1] / "data" / "app.png"
    if data_png.is_file():
        return QIcon(str(data_png))
    svg = icon_path("app.svg")
    if svg.is_file():
        return QIcon(str(svg))
    return QIcon()
