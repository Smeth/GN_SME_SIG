from importlib import import_module
from pathlib import Path
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from sme_gend.config.settings import Settings


@pytest.mark.parametrize('module_name,cls_name,table_name', [
    ('missions_view','MissionsView','_tbl'), ('conges_view','CongesView','_tbl'),
    ('desertion_view','DesertionView','_tbl'), ('stages_view','StagesView','_tbl_interne'),
    ('malade_punis_view','MaladePunisView','_tbl_malade')])
@pytest.mark.parametrize('width', [1100, 1536])
def test_module_table_occupies_main_area(monkeypatch, tmp_path, module_name, cls_name, table_name, width):
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    module = import_module('sme_gend.ui.views.' + module_name)
    monkeypatch.setattr(module, 'ListeGeneraleRepository', Mock())
    monkeypatch.setattr(module, 'NumeriqueSatelliteRepository', Mock())
    monkeypatch.setattr(getattr(module, cls_name), 'on_page_activated', lambda self: None)
    view = getattr(module, cls_name)(Settings(database_path=Path('unused')))
    view._view_mois = '2026-09'
    if module_name == 'stages_view':
        view._tabs.setCurrentIndex(2)
    view.resize(width, 820)
    view.show()
    for _ in range(3): app.processEvents()
    table = getattr(view, table_name)
    assert view.width() == width, view.minimumSizeHint()
    assert table.height() > 820 * .5
    assert table.width() > view.width() * .6
    assert view.grab().save(str(tmp_path / f'{module_name}_{width}.png'))
    view.close()


@pytest.mark.parametrize('width', [1100, 1536])
def test_structure_table_and_optional_help(monkeypatch, width):
    from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(StructureEffectifsView, 'on_page_activated', lambda self: None)
    view = StructureEffectifsView(Settings(database_path=Path('unused')), Mock(), Mock(), Mock())
    view.resize(width, 820)
    view.show()
    app.processEvents()
    initial_height = view._table.height()
    assert not view._help.isVisible()
    assert initial_height > 650
    assert view.width() == width
    view._help.button.click()
    app.processEvents()
    assert view._help.isVisible()
    assert view._table.height() < initial_height
    view._help.button.click()
    app.processEvents()
    assert view._table.height() == initial_height
    view.close()


@pytest.mark.parametrize('full_access', [True, False])
def test_document_history_keeps_access_warning(monkeypatch, full_access):
    from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(DocGenLogView, '_full_doc_log_access', lambda self: full_access)
    monkeypatch.setattr(DocGenLogView, '_attestation_history_access', lambda self: False)
    monkeypatch.setattr(DocGenLogView, '_rebuild_period_combos', lambda self: None)
    monkeypatch.setattr(DocGenLogView, 'on_page_activated', lambda self: None)
    view = DocGenLogView()
    view._revision_timer.stop()
    view.resize(1100, 820)
    view.show()
    app.processEvents()
    assert view.width() == 1100
    assert view._table.height() > 410
    assert view._subtitle.isVisible() == (not full_access)
    assert not view._help.isVisible()
    view.close()
