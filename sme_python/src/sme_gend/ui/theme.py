"""Application du thème QSS."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from sme_gend.ui.resources import style_path


def apply_app_theme(app: QApplication) -> None:
    p = style_path("app.qss")
    if not p.is_file():
        return
    app.setStyle("Fusion")
    with open(p, encoding="utf-8") as f:
        app.setStyleSheet(f.read())
