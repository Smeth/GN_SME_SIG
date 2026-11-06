"""Regression checks for drafts, conflicts, export completeness and warnings."""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def config():
    from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
    return ListeLayoutConfig(sections=[SectionBlock(id='a', order=0, structure_code='A', title_main_override='Initial A'),
                                       SectionBlock(id='b', order=1, structure_code='B', title_main_override='Initial B')])


def test_title_and_checkbox_draft_survive_switch_before_autosave(app):
    from sme_gend.ui.views.liste_present_view import _QuickEditPanel
    cfg = config()
    panel = _QuickEditPanel(SimpleNamespace(load=lambda: cfg), lambda: None)
    panel._populate()
    panel._le_titre1.setText('New A')
    panel._chk_sub.setChecked(False)
    panel._sec_list.setCurrentRow(1)
    assert cfg.sections[0].title_main_override == 'New A'
    assert cfg.sections[0].group_by_subdivision is False
    panel._sec_list.setCurrentRow(0)
    assert panel._le_titre1.text() == 'New A'
    panel._timer.stop(); panel.close()


def test_save_conflict_preserves_draft_and_reports_failure(app):
    from sme_gend.ui.views.liste_present_view import _QuickEditPanel
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    cfg = config(); saved = []
    def fail(cfg): raise ReferenceConflictError('Reload required')
    panel = _QuickEditPanel(SimpleNamespace(load=lambda: cfg, save=fail), lambda: saved.append(True))
    panel._populate(); panel._le_titre1.setText('Draft')
    assert panel._do_save() is False
    assert cfg.sections[0].title_main_override == 'Draft' and not saved
    assert 'Reload required' in panel._status_lbl.text()
    panel._timer.stop(); panel.close()


def snapshot(tmp_path):
    from sme_gend.config.settings import Settings
    from sme_gend.services.liste_present_service import PresentsExportSnapshot, PresentsAnomalies
    from sme_gend.repositories.mysql_connection import mysql_database_key
    from test_liste_present_populate import _row
    settings = Settings(database_path=tmp_path/'unused.mdb', mysql_view_mois='2026-09')
    rows = [_row(id=str(n), nom=f'UNIQUE AGENT {n}') for n in range(3)]
    rows[0].position_admin = ''
    rows[1].position_admin = 'Présent'; rows[2].position_admin = 'Présent'
    return PresentsExportSnapshot(settings, mysql_database_key(settings), '', 'present', config(),
        [('TITLE', '', 'A', [('UNIT', rows)])], [], [], '2026-09', PresentsAnomalies(positions_vides=[rows[0]]))


def test_detailed_export_renders_all_snapshot_rows_without_database_read(tmp_path):
    from sme_gend.services.liste_present_service import ListePresentService
    service = ListePresentService.__new__(ListePresentService)
    service.PREVIEW_MAX_DETAIL_ROWS = 2
    snap = snapshot(tmp_path)
    summary = service.render_snapshot_html(snap)
    full = service.render_snapshot_html(snap, detailed=True)
    assert 'Aperçu synthétique' in summary
    assert 'Aperçu synthétique' not in full
    assert all(f'UNIQUE AGENT {n}' in full for n in range(3))
    assert 'position(s) administrative(s) vide(s)' in full and 'pointage quotidien' in full


def test_excel_keeps_warning_and_logs_persons_and_ids(tmp_path, monkeypatch):
    from sme_gend.services.liste_present_service import ListePresentService
    from openpyxl import load_workbook
    snap = snapshot(tmp_path)
    service = ListePresentService.__new__(ListePresentService); service._settings = snap.settings
    traces = []
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation', lambda **kw: traces.append(kw))
    dest = tmp_path/'presents.xlsx'; service.export_excel(dest, snapshot=snap)
    with closing(load_workbook(dest)) as workbook:
        checks = workbook['Contrôles de présence']
        assert checks['A4'].value == '0' and checks['B4'].value == 'UNIQUE AGENT 0'
    assert traces[0]['count'] == 3 and len(traces[0]['persons']) == 3
    assert traces[0]['meta']['personnel_ids'] == ['0', '1', '2']


def test_each_refresh_reads_current_subdivision_hierarchy():
    from sme_gend.services.liste_present_service import ListePresentService
    service = ListePresentService.__new__(ListePresentService)
    reads = []
    service._subdivisions = SimpleNamespace(fetch_flat_hierarchy=lambda: reads.append(True) or [len(reads)])
    assert service._subdivisions_flat() == [1]
    assert service._subdivisions_flat() == [2]


def test_database_rename_requires_both_layout_and_personnel_rights(monkeypatch):
    from sme_gend.services.layout_config_service import LayoutConfigService
    service = LayoutConfigService.__new__(LayoutConfigService)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda permission: False)
    with pytest.raises(PermissionError, match='droits'):
        service.merge_subarmes_in_database(config(), 'A', ['SOURCE'], 'TARGET')


def test_pdf_uses_detailed_html_when_preview_is_summary(tmp_path, app, monkeypatch):
    from sme_gend.ui.views import liste_present_view as ui
    from sme_gend.services.liste_present_service import ListePresentService
    snap = snapshot(tmp_path)
    service = ListePresentService.__new__(ListePresentService); service.PREVIEW_MAX_DETAIL_ROWS = 2
    view = SimpleNamespace(_loaded=True, _refresh_busy=False, _last_html='SYNTHETIC SUMMARY',
        _preview_snapshot=snap, _service=service, _set_status=lambda *a: None)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a: True)
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *a: (str(tmp_path/'present.pdf'), ''))
    monkeypatch.setattr(ui, 'run_with_progress', lambda parent, title, text, fn: fn())
    monkeypatch.setattr(ui.QMessageBox, 'information', lambda *a: None)
    monkeypatch.setattr(ui.QMessageBox, 'critical', lambda *a: pytest.fail(str(a)))
    published = []
    monkeypatch.setattr('sme_gend.services.document_pdf_output.save_document_pdf',
                        lambda doc, path, **kw: published.append((doc.toPlainText(), kw)))
    ui.ListePresentView._export_pdf(view)
    assert len(published) == 1 and 'SYNTHETIC SUMMARY' not in published[0][0]
    assert all(f'UNIQUE AGENT {n}' in published[0][0] for n in range(3))
    assert published[0][1]['meta']['personnel_ids'] == ['0', '1', '2']
