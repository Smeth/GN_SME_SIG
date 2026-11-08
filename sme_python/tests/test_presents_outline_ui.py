"""The page shows editable rubrics; personnel is read only for an export."""
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QListWidgetItem

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
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


@pytest.fixture
def outline_page(app, monkeypatch, tmp_path):
    forbidden = Mock(side_effect=AssertionError('Opening rubrics must not read or render personnel'))
    service = SimpleNamespace(build_preview_html=forbidden, capture_snapshot=forbidden,
        export_excel=forbidden, render_snapshot_html=forbidden)
    captures, searches = [], []
    cfg = ListeLayoutConfig(sections=[SectionBlock('s', 0, 'SYN')])
    snapshot = SimpleNamespace(layout=cfg, month='2026-11', structures=[], flat=[])
    def capture(selected, *, on_progress=None):
        assert selected is service
        captures.append(True)
        if on_progress:
            on_progress(100)
        return snapshot
    def render(captured, search_filter=None):
        assert captured is snapshot
        searches.append(search_filter)
        return '<h2>SYNTHETIC STRUCTURE</h2><p>SYNTHETIC SUBDIVISION</p>'
    monkeypatch.setattr(ui, 'capture_outline', capture)
    monkeypatch.setattr(ui, 'render_outline', render)
    confirmation = Mock(side_effect=AssertionError('A lightweight outline needs no load confirmation'))
    monkeypatch.setattr(ui, 'confirm_yes_no', confirmation)
    view = ui.ListePresentView(service)
    yield SimpleNamespace(view=view, service=service, captures=captures, searches=searches,
                          forbidden=forbidden, snapshot=snapshot)
    if view._refresh_busy:
        pump(app, lambda: not view._refresh_busy)
    view.close()
    view.deleteLater()
    app.processEvents()


def test_open_and_f5_render_only_rubrics_without_personnel_or_confirmation(app, outline_page):
    case = outline_page
    case.view.on_page_activated()
    pump(app, lambda: case.view._loaded and not case.view._refresh_busy)
    assert case.view._preview_snapshot is case.snapshot
    assert 'SYNTHETIC STRUCTURE' in case.view._preview.toPlainText()
    case.view.refresh_from_database()
    pump(app, lambda: case.view._loaded and not case.view._refresh_busy)
    assert len(case.captures) == 2
    case.forbidden.assert_not_called()


def test_rubric_search_never_calls_a_personnel_query_or_document_render(app, outline_page):
    case = outline_page
    case.view.on_page_activated()
    pump(app, lambda: case.view._loaded and not case.view._refresh_busy)
    case.view._search.setText('SYNTHETIC SUBDIVISION')
    pump(app, lambda: bool(case.searches) and case.searches[-1] == 'SYNTHETIC SUBDIVISION'
         and not case.view._refresh_busy)
    assert len(case.captures) == 1
    case.forbidden.assert_not_called()


def test_editor_reference_cache_does_not_load_personnel_counts(app):
    service = SimpleNamespace(all_structure_designations=Mock(return_value={'SYN': 'SYNTHETIC STRUCTURE'}),
        database_structure_codes=Mock(return_value={'SYN'}),
        present_counts_by_structure=Mock(side_effect=AssertionError('No automatic personnel counts')))
    panel = SimpleNamespace(_svc=service)
    ui._QuickEditPanel._reload_caches(panel)
    assert panel._desig_cache == {'SYN': 'SYNTHETIC STRUCTURE'}
    assert panel._db_codes == {'SYN'}
    service.present_counts_by_structure.assert_not_called()


@pytest.mark.parametrize('kind', ['section', 'subdivision'])
def test_clicking_a_rubric_selects_the_existing_editor_without_personnel(app, monkeypatch, kind):
    sec_list, sub_list, title = QListWidget(), QListWidget(), QLineEdit()
    section = SectionBlock('SYNTHETIC:SECTION', 0, 'SYN')
    item = QListWidgetItem('SYNTHETIC STRUCTURE')
    item.setData(Qt.ItemDataRole.UserRole, section.id)
    sec_list.addItem(item)
    sub_list.addItems(['FIRST SUBDIVISION', 'SECOND SUBDIVISION'])
    panel = SimpleNamespace(_sec_list=sec_list, _sub_list=sub_list, _le_titre1=title)
    view = SimpleNamespace(_btn_toggle=Mock(), _panel=panel,
        _preview_snapshot=SimpleNamespace(layout=ListeLayoutConfig(sections=[section]),
            subdivisions={section.id: ['FIRST SUBDIVISION', 'SECOND SUBDIVISION']}))
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: True)
    link = kind + ':SYNTHETIC%3ASECTION' + (':1' if kind == 'subdivision' else '')
    try:
        ui.ListePresentView._on_rubric_clicked(view, QUrl(link))
        view._btn_toggle.setChecked.assert_called_once_with(True)
        assert sec_list.currentRow() == 0
        if kind == 'subdivision':
            assert sub_list.currentRow() == 1
    finally:
        for widget in (sec_list, sub_list, title):
            widget.deleteLater()
        app.processEvents()


def test_a_rubric_link_cannot_open_an_editor_without_the_layout_permission(app, monkeypatch):
    view = SimpleNamespace(_btn_toggle=Mock(side_effect=AssertionError('No editor without permission')))
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: False)
    ui.ListePresentView._on_rubric_clicked(view, QUrl('section:synthetic'))
    view._btn_toggle.setChecked.assert_not_called()


@pytest.mark.parametrize('kind', ['excel', 'pdf'])
def test_unsaved_rubric_draft_cannot_be_exported(app, monkeypatch, tmp_path, kind):
    view = SimpleNamespace(_panel=SimpleNamespace(has_unsaved_changes=lambda: True))
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: True)
    destination = Mock(side_effect=AssertionError('Save the rubric draft before choosing an export file'))
    messages = Mock()
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', destination)
    monkeypatch.setattr(ui.QMessageBox, 'information', messages)
    getattr(ui.ListePresentView, '_export_' + kind)(view)
    destination.assert_not_called()
    assert 'Enregistrez les modifications des rubriques' in messages.call_args.args[-1]


@pytest.mark.parametrize('kind', ['excel', 'pdf'])
def test_personnel_read_starts_only_after_destination_and_overwrite_are_accepted(app, monkeypatch, tmp_path, kind):
    settings = Settings(database_path=tmp_path/'unused', mysql_view_mois='2026-11')
    events = []
    person = SimpleNamespace(id='42', noms='SYNTHETIC AGENT')
    personnel = SimpleNamespace(settings=settings, month='2026-11', search='', presence='present',
                                sections=[('SYNTHETIC STRUCTURE', 'SYN', 'SYN', [('SYNTHETIC SUBDIVISION', [person])])])
    def capture(*args, **kwargs):
        events.append('capture')
        assert events[:2] == ['destination', 'overwrite']
        assert kwargs['presences_mode'] == 'present'
        return personnel
    def export(destination, **kwargs):
        events.append('excel')
        assert events[:2] == ['destination', 'overwrite']
        assert kwargs['presences_mode'] == 'present'
        assert kwargs.get('search_filter') is None
        assert kwargs.get('snapshot') is None
    service = SimpleNamespace(_settings=settings, capture_snapshot=Mock(side_effect=capture),
        export_excel=Mock(side_effect=export), validate_snapshot=Mock(),
        render_snapshot_html=Mock(return_value='<p>SYNTHETIC AGENT FOR EXPORT ONLY</p>'))
    view = ui.ListePresentView(service)
    view._loaded = True
    view._preview_snapshot = SimpleNamespace(settings=settings, month='2026-11')
    view._render_generation = view._preview_generation
    view._search.setText('SYNTHETIC STRUCTURE')
    view._search_timer.stop()
    view._loaded = True
    view._preview_snapshot = SimpleNamespace(settings=settings, month='2026-11')
    view._render_generation = view._preview_generation
    view._set_preview_html('<p>SYNTHETIC STRUCTURE ONLY</p>')
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: True)
    def destination(*args, **kwargs):
        assert not service.capture_snapshot.called and not service.export_excel.called
        events.append('destination')
        return str(tmp_path/('export.' + ('xlsx' if kind == 'excel' else 'pdf'))), ''
    def overwrite(*args):
        assert not service.capture_snapshot.called and not service.export_excel.called
        events.append('overwrite')
        return True
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', destination)
    monkeypatch.setattr(ui, 'confirm_overwrite_file', overwrite)
    monkeypatch.setattr(ui, 'run_with_progress', lambda parent, title, text, work: work())
    monkeypatch.setattr(ui.QMessageBox, 'information', lambda *a: None)
    monkeypatch.setattr(ui.QMessageBox, 'critical', lambda *a: pytest.fail(str(a[-1])))
    save_pdf = Mock()
    monkeypatch.setattr('sme_gend.services.document_pdf_output.save_document_pdf', save_pdf)
    try:
        getattr(view, '_export_' + kind)()
        assert 'SYNTHETIC AGENT' not in view._preview.toPlainText()
        if kind == 'excel':
            assert events == ['destination', 'overwrite', 'excel']
            service.capture_snapshot.assert_not_called()
        else:
            assert events == ['destination', 'overwrite', 'capture']
            service.capture_snapshot.assert_called_once()
            service.render_snapshot_html.assert_called_once_with(personnel, detailed=True, for_print=True)
            assert save_pdf.call_args.kwargs['count'] == 1
            assert save_pdf.call_args.kwargs['meta']['personnel_ids'] == ['42']
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()
