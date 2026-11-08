"""This page keeps the explicit-Present mode even with legacy configuration."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.ui.views import liste_present_view as ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('legacy_mode',['stage_interne','les_deux'])
def test_preview_worker_ignores_legacy_stage_configuration(app,legacy_mode):
    requested=[]
    def build(search,mode,**kwargs):
        requested.append((search,mode));return '<p>SYNTHETIC PRESENTS</p>'
    service=SimpleNamespace(build_preview_html=build,presences_mode=legacy_mode,
                            config=SimpleNamespace(presences_mode=legacy_mode))
    worker=ui._ListePresentPreviewWorker(service,'SYNTHETIC FILTER',1)
    worker.run()
    assert requested==[('SYNTHETIC FILTER','present')]


def test_pdf_does_not_accept_the_mode_from_an_old_stage_snapshot(app,monkeypatch,tmp_path):
    snapshot=SimpleNamespace(sections=[],search='',presence='stage_interne')
    modes=[]
    def validate(value,*,search_filter,presences_mode):
        modes.append(presences_mode)
        if value.presence!=presences_mode:raise ValueError('Ancien mode de présence : actualisez.')
    service=SimpleNamespace(validate_snapshot=validate,render_snapshot_html=Mock())
    view=SimpleNamespace(_loaded=True,_refresh_busy=False,_preview_snapshot=snapshot,
                         _last_html='<p>OLD STAGE PREVIEW</p>',_service=service,_set_status=Mock())
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:True)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(tmp_path/'present.pdf'),''))
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:True)
    errors=[];monkeypatch.setattr(ui.QMessageBox,'critical',lambda *a:errors.append(a[-1]))
    ui.ListePresentView._export_pdf(view)
    assert modes==['present'] and errors
    service.render_snapshot_html.assert_not_called()


def test_excel_keeps_its_fixed_mode_instead_of_an_old_snapshot_mode(app,monkeypatch,tmp_path):
    snapshot=SimpleNamespace(search='',presence='les_deux')
    requested=[]
    service=SimpleNamespace(export_excel=lambda destination,**kw:requested.append(kw))
    view=SimpleNamespace(_loaded=True,_refresh_busy=False,_preview_snapshot=snapshot,
                         _service=service,_set_status=Mock())
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:True)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(tmp_path/'present.xlsx'),''))
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:True)
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,text,fn:fn())
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
    ui.ListePresentView._export_excel(view)
    assert requested[0]['presences_mode']=='present'


def test_subdivision_sort_uses_the_single_strict_present_count(app):
    service=SimpleNamespace(present_counts_by_subdivision=Mock(return_value={'A':1,'B':2}),
                             count_subarme_occurrences=Mock(side_effect=AssertionError('No all-status counts')))
    panel=SimpleNamespace(_svc=service,_current_sec=lambda:SimpleNamespace(structure_code='STRUCT'),
        _sub_labels_from_list=lambda:['A','B'],_set_sub_list_labels=Mock(),_sync_sub_to_sec=Mock(),_trigger=Mock())
    ui._QuickEditPanel._sort_sub_by_effectif(panel)
    service.present_counts_by_subdivision.assert_called_once_with('STRUCT',['A','B'])
    service.count_subarme_occurrences.assert_not_called()
    panel._set_sub_list_labels.assert_called_once_with(['B','A'])
    panel._trigger.assert_called_once()


def test_failed_strict_counts_do_not_modify_the_layout_draft(app,monkeypatch):
    service=SimpleNamespace(present_counts_by_subdivision=Mock(side_effect=OSError('SYNTHETIC COUNT ERROR')))
    panel=SimpleNamespace(_svc=service,_current_sec=lambda:SimpleNamespace(structure_code='STRUCT'),
        _sub_labels_from_list=lambda:['A','B'],_set_sub_list_labels=Mock(),_sync_sub_to_sec=Mock(),_trigger=Mock())
    warnings=Mock();monkeypatch.setattr(ui.QMessageBox,'warning',warnings)
    ui._QuickEditPanel._sort_sub_by_effectif(panel)
    panel._set_sub_list_labels.assert_not_called()
    panel._sync_sub_to_sec.assert_not_called();panel._trigger.assert_not_called()
    warnings.assert_called_once()
