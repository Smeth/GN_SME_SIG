from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget

from sme_gend.config.settings import Settings
from sme_gend.ui.personnel_advanced_filter import AdvancedFilterRule
from sme_gend.ui.views.edition_sme_view import _EditionModel, _EditionFilterProxy, _COL_IDX, EditionSmeView
from test_edition_sme_editor import _row


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    application.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.interface_zoom import set_interface_zoom
    from sme_gend.ui.theme import apply_app_theme
    set_interface_zoom(application, 100)
    apply_app_theme(application, 'light')
    yield application
    set_interface_zoom(application, 100)


def test_edition_advanced_rules_combine_with_quick_filters_and_edits(app):
    model = _EditionModel()
    rows = [replace(_row('1'), fonction='CHEF', date_grade=datetime(2020, 1, 1)),
            replace(_row('2'), fonction='CHEF', date_grade=datetime(2026, 1, 1)),
            replace(_row('3'), fonction='ADJOINT', date_grade=datetime(2020, 1, 1))]
    model.load(rows, None, [], [], [], [])
    model._persist_update = Mock()
    proxy = _EditionFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_advanced_rules([AdvancedFilterRule('fonction', 'contient', 'chef'),
                              AdvancedFilterRule('anciennete_grade', 'au moins', '2 ans', '2026-11-06')])
    assert proxy.rowCount() == 1
    proxy.set_text('Agent 2')
    assert proxy.rowCount() == 0
    proxy.set_text('')
    model.setData(model.index(0, _COL_IDX['fonction']), 'ADJOINT', Qt.ItemDataRole.EditRole)
    assert proxy.rowCount() == 0
    proxy.set_advanced_rules([])
    assert proxy.rowCount() == 3


@pytest.mark.parametrize('width,height', [(1100, 720), (1536, 820)])
def test_edition_compact_layout_and_advanced_reset(app, tmp_path, width, height):
    view = EditionSmeView(Settings(database_path=Path('unused'), mysql_view_mois='2026-09'))
    view._dirty = False
    view._apply_editing_lock = lambda: None
    view._start_career_alerts_scan = lambda: None
    view._model.load([_row()], None, [], [], [], [])
    view._advanced_filters.load_state({'rules': [{'column': 'fonction', 'operator': 'est vide', 'value': ''}]})
    view._apply_advanced_filters()
    assert view._proxy.rowCount() == 1
    view.resize(width, height)
    view.show()
    app.processEvents()
    assert view.width() == width
    assert not view._advanced_filters._body.isVisible()
    assert not view._lbl_month.isVisible()
    assert view._table.height() > view.height() * .6
    assert view.grab().save(str(tmp_path / f'edition_{width}.png'))
    view._advanced_filters._on_toggle_body()
    app.processEvents()
    assert view._advanced_filters._body.isVisible()
    view._advanced_filters._on_toggle_body()
    assert len(view._proxy._advanced_rules) == 1
    view._reset_filters()
    assert view._advanced_filters.rules() == [] and view._proxy._advanced_rules == []
    view.close()


def test_display_fullscreen_restores_maximized_state_and_zoom_theme(app):
    from sme_gend.ui.display_controls import DisplayControls
    from sme_gend.ui.theme import apply_app_theme
    from sme_gend.ui.interface_zoom import zoom_percent
    window = QMainWindow()
    host = QWidget()
    button = QPushButton('Zoom de l’interface')
    QVBoxLayout(host).addWidget(button)
    window.setCentralWidget(host)
    controls = DisplayControls(window, window.menuBar().addMenu('Affichage'))
    window.showMaximized(); app.processEvents()
    from PySide6.QtTest import QTest
    window.activateWindow(); app.processEvents()
    QTest.keyClick(window, Qt.Key.Key_F11); app.processEvents()
    assert window.isFullScreen() and controls.fullscreen.isChecked()
    controls.fullscreen.trigger(); app.processEvents()
    assert not window.isFullScreen() and window.isMaximized()
    before = button.font().pixelSize() if button.font().pixelSize() > 0 else button.font().pointSizeF()
    from PySide6.QtGui import QTextDocument
    print_font = QTextDocument().defaultFont()
    controls.set_zoom(125); app.processEvents()
    after = button.font().pixelSize() if button.font().pixelSize() > 0 else button.font().pointSizeF()
    assert after > before and zoom_percent(app) == 125
    assert QTextDocument().defaultFont() == print_font
    css = app.styleSheet()
    controls.set_zoom(125)
    assert app.styleSheet() == css
    apply_app_theme(app, 'dark')
    assert zoom_percent(app) == 125
    assert '125' in controls.zoom_menu.title()
    controls.zoom_reset.trigger()
    assert zoom_percent(app) == 100
    controls.set_zoom(500)
    assert zoom_percent(app) == 150 and not controls.zoom_in.isEnabled()
    controls.set_zoom(1)
    assert zoom_percent(app) == 75 and not controls.zoom_out.isEnabled()
    window.close()


def test_advanced_filters_are_saved_and_restored_with_presets(app, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    view = EditionSmeView(Settings(database_path=Path('unused'), mysql_view_mois='2026-09'))
    view._model.load([_row()], None, [], [], [], [])
    state = {'rules': [{'column': 'noms', 'operator': 'contient', 'value': 'Agent'}]}
    view._advanced_filters.load_state(state)
    view._apply_advanced_filters()
    view._load_presets = lambda: []
    view._store_presets = Mock()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('TEST', True))
    view._save_current_preset()
    preset = view._store_presets.call_args.args[0][0]
    assert preset['advanced_filters']['rules'][0]['value'] == 'Agent'
    view._reset_filters()
    view._apply_preset(preset)
    assert len(view._proxy._advanced_rules) == 1 and view._proxy.rowCount() == 1
    view.close()


def test_zoom_stylesheet_does_not_change_icons_or_colours():
    from sme_gend.ui.interface_zoom import scaled_stylesheet
    css = 'QWidget { font-size: 10pt; padding: 4px; color: #123456; image: url(icon-16px.svg); }'
    result = scaled_stylesheet(css, 150)
    assert '15pt' in result and '6px' in result
    assert 'icon-16px.svg' in result and '#123456' in result


def test_compact_header_keeps_read_only_warning(app, monkeypatch):
    from types import SimpleNamespace
    from sme_gend.config import liste_generale_scope
    from sme_gend.services import sme_editing_policy
    state = SimpleNamespace(locked=True, tabsme_statut='Fermé')
    monkeypatch.setattr(liste_generale_scope, 'build_export_mois_context', lambda settings: SimpleNamespace(banner_html=lambda: 'Mois 2026-09'))
    monkeypatch.setattr(sme_editing_policy, 'evaluate_editing_state_for_settings', lambda settings: state)
    view = EditionSmeView(Settings(database_path=Path('unused')))
    view._update_month_banner()
    assert not view._lbl_month.isHidden() and 'Lecture seule' in view._lbl_month.text()
    state.locked = False
    view._update_month_banner()
    assert view._lbl_month.isHidden()
    view.close()
