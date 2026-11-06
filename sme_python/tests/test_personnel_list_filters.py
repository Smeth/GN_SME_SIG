"""Filtres combo liste générale."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from sme_gend.ui.personnel_list_filters import combo_filter_value


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_combo_filter_value_tous_grades_returns_none(qapp) -> None:
    combo = QComboBox()
    combo.addItem("(Tous les grades)", None)
    combo.setCurrentIndex(0)
    assert combo_filter_value(combo) is None
