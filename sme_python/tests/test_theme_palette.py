"""Thème applicatif : palette explicite et schéma Qt."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from sme_gend.ui.theme import (
    THEME_DARK,
    THEME_LIGHT,
    apply_app_theme,
    build_application_palette,
    effective_theme,
    is_dark,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_light_palette_uses_dark_text_on_light_background(qapp):
    apply_app_theme(qapp, THEME_LIGHT)
    pal = build_application_palette(THEME_LIGHT)
    assert pal.color(pal.ColorRole.WindowText).lightness() < 128
    assert pal.color(pal.ColorRole.Window).lightness() > 128
    assert effective_theme() == THEME_LIGHT
    assert not is_dark()


def test_dark_palette_uses_light_text_on_dark_background(qapp):
    apply_app_theme(qapp, THEME_DARK)
    pal = build_application_palette(THEME_DARK)
    assert pal.color(pal.ColorRole.WindowText).lightness() > 128
    assert pal.color(pal.ColorRole.Window).lightness() < 128
    assert effective_theme() == THEME_DARK
    assert is_dark()


def test_explicit_light_overrides_app_palette(qapp):
    apply_app_theme(qapp, THEME_LIGHT)
    app_pal = qapp.palette()
    assert app_pal.color(app_pal.ColorRole.Text) == QColor("#212121")


def test_paper_preview_forces_dark_text_on_white_base(qapp):
    from PySide6.QtWidgets import QTextBrowser

    apply_app_theme(qapp, THEME_DARK)
    browser = QTextBrowser()
    from sme_gend.ui.theme import apply_paper_preview_palette

    apply_paper_preview_palette(browser)
    pal = browser.palette()
    assert pal.color(pal.ColorRole.Text) == QColor("#212121")
    assert pal.color(pal.ColorRole.Base) == QColor("#ffffff")
    assert "#212121" in browser.document().defaultStyleSheet()
