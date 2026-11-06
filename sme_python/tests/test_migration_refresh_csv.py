from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import csv
import io
import pytest


def test_shell_f5_does_not_start_eager_reload_before_explicit_refresh(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.config.settings import Settings
    from sme_gend.ui.shell_window import ShellWindow
    app = QApplication.instance() or QApplication([])
    page = SimpleNamespace(mark_dirty=Mock(side_effect=AssertionError("double load")),
                           refresh_from_database=Mock())
    shell = SimpleNamespace(_settings=Settings(database_path=Path('unused')),
        _has_unsaved_changes=lambda: False, _stack=Mock(), _page_widget=lambda *a, **kw: page,
        _context_banner=Mock(), statusBar=Mock())
    monkeypatch.setattr(QApplication, 'activeModalWidget', lambda: None)
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.peek_editing_state', lambda s: None)
    ShellWindow._reload_current_view(shell)
    page.refresh_from_database.assert_called_once()
    page.mark_dirty.assert_not_called()


def test_dashboard_failed_render_remains_stale_then_can_retry(monkeypatch):
    from sme_gend.ui.views import home_view as module
    panel = Mock()
    panel.update.side_effect = ValueError('synthetic render failure')
    view = SimpleNamespace(_reload_generation=0, _stats_dirty=True, _shown_epoch=-1,
        _stats_panel=panel, _dashboard=Mock(), _update_scope_subtitle=Mock(), _reload_deferred=Mock())
    monkeypatch.setattr(module, 'run_with_progress', lambda *a, **k: object())
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.dashboard_snapshot_epoch', lambda: 42)
    module.HomeView._reload_stats(view)
    assert view._stats_dirty and view._shown_epoch == -1
    view._reload_deferred.assert_not_called()
    panel.update.side_effect = None
    module.HomeView._reload_stats(view)
    assert not view._stats_dirty and view._shown_epoch == 42
    view._reload_deferred.assert_called_once()


@pytest.mark.parametrize('value', ['=1+2', '+cmd', '-2+3', '@SUM(A1)', '  =1', '\t=1', "'original"])
def test_document_csv_formula_text_is_literal_and_reimports_losslessly(value):
    from sme_gend.services.doc_gen_log import export_doc_log_csv, parse_doc_log_csv, doc_entry_fingerprint
    original = dict(ts_utc='2026-09-02T10:00:00', doc_type='test', format='PDF',
        mois='2026-09', count=1, persons=[value], dest=value, actor=value, meta={'test': value})
    rendered = export_doc_log_csv([original])
    exported = next(csv.DictReader(io.StringIO(rendered), delimiter=';'))
    assert exported['actor'].startswith("'")
    parsed, invalid = parse_doc_log_csv(rendered)
    assert not invalid
    assert doc_entry_fingerprint(parsed[0]) == doc_entry_fingerprint(original)


def test_legacy_document_csv_apostrophe_is_not_removed():
    from sme_gend.services.doc_gen_log import parse_doc_log_csv
    rows, invalid = parse_doc_log_csv("ts_utc;doc_type;actor\n2026-09-01T10:00:00;test;'original\n")
    assert not invalid and rows[0]['actor'] == "'original"


def test_reapplying_same_zoom_does_not_repolish_cached_pages():
    from PySide6.QtGui import QFont
    from sme_gend.ui.interface_zoom import set_interface_zoom
    font = QFont('Arial', 10)
    style = 'QWidget {font-size:10px;}'
    properties = dict(sme_unscaled_font=font, sme_unscaled_stylesheet=style, sme_interface_zoom=100)
    app = SimpleNamespace(property=properties.get, setProperty=lambda k, v: properties.update({k:v}),
        font=lambda:font, styleSheet=lambda:style, setFont=Mock(), setStyleSheet=Mock())
    assert set_interface_zoom(app, 100) == 100
    app.setFont.assert_not_called()
    app.setStyleSheet.assert_not_called()
