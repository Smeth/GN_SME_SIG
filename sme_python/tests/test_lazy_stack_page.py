"""Comportement des pages différées en cas d'échec de construction."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from sme_gend.ui.lazy_stack_page import LazyStackPage


app = QApplication.instance() or QApplication([])


def test_lazy_page_exposes_factory_error() -> None:
    def fail_to_build() -> QWidget:
        raise RuntimeError("factory failed")

    page = LazyStackPage(fail_to_build)
    page.ensure_built()
    for _ in range(10):
        app.processEvents()
        if page.build_error() is not None:
            break

    assert isinstance(page.build_error(), RuntimeError)
    assert page.built_widget() is None
