"""Synthetic Qt render of rubrics with the inline layout editor open."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services.liste_present_outline import PresentsOutlineSnapshot
from sme_gend.ui.views import liste_present_view as ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


@pytest.mark.parametrize('width,height', [(1280, 800), (900, 720)])
def test_outline_and_editor_remain_accessible_at_client_sizes(app, monkeypatch, tmp_path, width, height):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    settings = Settings(database_path=tmp_path/'unused', mysql_view_mois='2026-11')
    structures = [
        StructureRow('DPF', 'DIRECTION DU PERSONNEL ET DE LA FORMATION', 1),
        StructureRow('RG-SYN', 'RÉGION DE GENDARMERIE SYNTHÉTIQUE', 2),
        StructureRow('UNITE', 'UNITÉ SYNTHÉTIQUE', 3),
    ]
    labels = {
        'section-dpf': ['DIRECTION', 'SECRÉTARIAT', 'DIVISION GESTION DU PERSONNEL',
                        'DIVISION FORMATION', 'BUREAU ARCHIVES', 'BUREAU ADMINISTRATION'],
        'section-region': ['COMMANDEMENT', 'ÉTAT-MAJOR', 'COMPAGNIE SYNTHÉTIQUE'],
        'section-unite': ['COMMANDEMENT', 'SERVICE ADMINISTRATIF'],
    }
    config = ListeLayoutConfig(sections=[
        SectionBlock('section-dpf', 0, 'DPF', subdivision_order=labels['section-dpf']),
        SectionBlock('section-region', 1, 'RG-SYN', subdivision_order=labels['section-region']),
        SectionBlock('section-unite', 2, 'UNITE', subdivision_order=labels['section-unite']),
    ])
    forbidden = Mock(side_effect=AssertionError('The visual outline must not load agents'))
    layout_service = SimpleNamespace(load=lambda **kw: deepcopy(config),
        all_structure_designations=lambda: {row.code: row.designation for row in structures},
        database_structure_codes=lambda: {row.code for row in structures},
        present_counts_by_structure=forbidden, present_counts_by_subdivision=forbidden,
        resolve_subdivision_order_labels=lambda section: section.subdivision_order,
        reconcile_with_database=forbidden, save=forbidden)
    service = SimpleNamespace(_settings=settings, _layout_config=layout_service,
        build_preview_html=forbidden, capture_snapshot=forbidden)
    snapshot = PresentsOutlineSnapshot(settings, ('synthetic',), '2026-11', config, structures, [], labels)
    def capture(selected, *, on_progress=None):
        if on_progress:
            on_progress(100)
        return snapshot
    monkeypatch.setattr(ui, 'capture_outline', capture)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: True)
    view = ui.ListePresentView(service, layout_service)
    view.set_open_layout_editor(Mock())
    view.resize(width, height)
    view.show()
    try:
        view.on_page_activated()
        pump(app, lambda: view._loaded and not view._refresh_busy)
        view._btn_toggle.setChecked(True)
        app.processEvents()
        panel = view._panel
        assert panel is not None and panel.isVisible()
        assert view.width() <= width
        assert 280 <= panel.width() <= 360
        assert view._preview.width() >= 220
        for widget in (view._btn_refresh, view._btn_export_menu if width < 1100 else view._btn_export,
                       view._btn_zoom_in, panel._sec_list, panel._sub_list, panel._btn_save, view._preview):
            assert widget.isVisible()
            origin = widget.mapTo(view, QPoint(0, 0))
            assert origin.x() >= 0 and origin.y() >= 0
            assert origin.x() + widget.width() <= view.width()
            assert origin.y() + widget.height() <= view.height()
        assert panel._sec_list.count() == 3 and panel._sub_list.count() == 6
        assert panel._sub_list.height() >= 100
        assert 'DIRECTION DU PERSONNEL ET DE LA FORMATION' in view._preview.toPlainText()
        assert 'DIVISION FORMATION' in view._preview.toPlainText()
        forbidden.assert_not_called()
        screenshot = tmp_path/f'presents_outline_editor_{width}x{height}.png'
        assert view.grab().save(str(screenshot))
        print('Presents outline render:', screenshot)
    finally:
        if view._panel is not None:
            view._panel._timer.stop()
        view.close()
        view.deleteLater()
        app.processEvents()
