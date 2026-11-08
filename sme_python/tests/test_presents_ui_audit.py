"""Presents: worker context, real completion and export-dialog invalidation."""
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent

from sme_gend.config.settings import Settings
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.ui.views import liste_present_view as ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def lightweight_outline(monkeypatch):
    def capture(service, *, on_progress=None):
        return SimpleNamespace(html=service.capture_outline(on_progress=on_progress))
    monkeypatch.setattr(ui, 'capture_outline', capture)
    monkeypatch.setattr(ui, 'render_outline', lambda snapshot, search_filter=None: snapshot.html)


def pump(app, predicate):
    deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.002)
    assert predicate()


def test_worker_keeps_its_original_database_and_layout_during_a_rebind(app,monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema',lambda *a:None)
    first=Settings(database_path=tmp_path/'unused',mysql_database='SYNTHETIC_A',mysql_view_mois='2026-09')
    second=Settings(database_path=tmp_path/'unused',mysql_database='SYNTHETIC_B',mysql_view_mois='2026-10')
    service=ListePresentService(first,LayoutConfigService(first))
    original_repository=service._liste
    observations=[]
    def build(frozen,*a,**kw):
        observations.append((frozen._settings,frozen._layout_config._settings,frozen._liste))
        return 'SYNTHETIC HTML'
    monkeypatch.setattr(ui,'capture_outline',build)
    monkeypatch.setattr(ui,'render_outline',lambda snapshot,search_filter=None:snapshot)
    worker=ui._ListePresentPreviewWorker(service,None,1)
    service.rebind_settings(second);service._layout_config.rebind_settings(second)
    worker.run()
    assert observations==[(first,first,original_repository)]
    assert service._settings is second and service._layout_config._settings is second


def test_preview_waits_for_the_real_thread_end_and_does_not_reach_100_early(app,monkeypatch):
    emitted=Event();release=Event()
    original=ui._ListePresentPreviewWorker
    class Delayed(original):
        def run(self):
            super().run();emitted.set();release.wait(2)
    monkeypatch.setattr(ui,'_ListePresentPreviewWorker',Delayed)
    def build(*a,**kw):kw['on_progress'](100);return '<p>SYNTHETIC PRESENTS</p>'
    view=ui.ListePresentView(SimpleNamespace(capture_outline=build))
    try:
        view._do_refresh(skip_confirm=True)
        assert emitted.wait(2)
        app.processEvents()
        assert view._refresh_busy and not view._loaded
        assert view._refresh_progress.value()==99
    finally:release.set()
    pump(app,lambda:not view._refresh_busy)
    assert view._loaded and view._refresh_progress.value()==100
    assert 'SYNTHETIC PRESENTS' in view._last_html
    view.close();view.deleteLater();app.processEvents()


def test_old_generation_error_cannot_replace_the_new_context(app):
    release=Event();entered=Event()
    def build(*a,**kw):
        entered.set();release.wait(2);raise OSError('OLD DATABASE ERROR')
    view=ui.ListePresentView(SimpleNamespace(capture_outline=build))
    view.show();app.processEvents()
    try:
        view._do_refresh(skip_confirm=True);assert entered.wait(2)
        view.mark_dirty()
    finally:release.set()
    pump(app,lambda:not view._refresh_busy)
    assert not view._loaded and view._preview_snapshot is None
    assert 'OLD DATABASE ERROR' not in view._preview.toPlainText()
    assert 'F5' in view._preview.toPlainText()
    view.close();view.deleteLater();app.processEvents()


def test_permission_revoked_during_loading_discards_the_result(app,monkeypatch):
    release=Event();entered=Event();allowed=[True]
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda key:allowed[0])
    def build(*a,**kw):entered.set();release.wait(2);return '<p>SYNTHETIC CONFIDENTIAL LIST</p>'
    view=ui.ListePresentView(SimpleNamespace(capture_outline=build))
    try:
        view._do_refresh(skip_confirm=True);assert entered.wait(2);allowed[0]=False
    finally:release.set()
    pump(app,lambda:not view._refresh_busy)
    assert not view._loaded and not view._last_html
    assert 'CONFIDENTIAL' not in view._preview.toPlainText()
    assert 'Accès refusé' in view._status_lbl.text()
    view.close();view.deleteLater();app.processEvents()


def test_denied_refresh_clears_a_previously_loaded_list(app,monkeypatch):
    view=ui.ListePresentView(SimpleNamespace())
    view.show();app.processEvents()
    view._loaded=True;view._last_html='<p>SYNTHETIC OLD PERSONNEL</p>'
    view._set_preview_html(view._last_html)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:False)
    view.refresh_from_database()
    assert not view._loaded and not view._last_html
    assert 'OLD PERSONNEL' not in view._preview.toPlainText()
    view.close();view.deleteLater();app.processEvents()


def test_repeated_actualiser_does_not_reuse_a_deleted_qthread(app):
    loads=[]
    def build(*a,**kw):loads.append(True);return f'<p>SYNTHETIC LOAD {len(loads)}</p>'
    view=ui.ListePresentView(SimpleNamespace(capture_outline=build))
    for number in (1,2):
        view.refresh_from_database()
        pump(app,lambda:not view._refresh_busy)
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert view._preview_worker is None
        assert view._loaded and f'LOAD {number}' in view._last_html
    assert len(loads)==2
    view.close();view.deleteLater();app.processEvents()


def test_context_change_cancels_a_search_queued_for_the_old_month(app):
    view=ui.ListePresentView(SimpleNamespace())
    view._loaded=True;view._auto_refresh_requested=True;view._refresh_pending=True
    view._search_timer.start(60000)
    view.mark_dirty()
    assert not view._search_timer.isActive()
    assert not view._refresh_pending and not view._auto_refresh_requested
    assert not view._loaded and view._preview_snapshot is None
    view.close();view.deleteLater();app.processEvents()


def test_refused_customization_does_not_load_a_hidden_editor(app,monkeypatch):
    view=ui.ListePresentView(SimpleNamespace(),SimpleNamespace())
    forbidden=Mock(side_effect=AssertionError('No editor read without layout permission'))
    view._ensure_panel=forbidden
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:False)
    view._btn_toggle.setChecked(True)
    assert not view._btn_toggle.isChecked()
    forbidden.assert_not_called()
    view.close();view.deleteLater();app.processEvents()


def export_view(tmp_path):
    settings=Settings(database_path=tmp_path/'unused',mysql_view_mois='2026-09')
    snapshot=SimpleNamespace(settings=settings,month='2026-09')
    personnel=SimpleNamespace(sections=[],settings=settings,month='2026-09',search='',presence='present')
    service=SimpleNamespace(_settings=settings,validate_snapshot=Mock(),export_excel=Mock(),
        capture_snapshot=Mock(return_value=personnel),render_snapshot_html=Mock(return_value='<p>SYNTHETIC PRINT</p>'))
    return SimpleNamespace(_loaded=True,_refresh_busy=False,_preview_snapshot=snapshot,
        _preview_generation=1,_render_generation=1,
        _last_html='<p>SYNTHETIC PRESENTS</p>',_service=service,_set_status=Mock())


@pytest.mark.parametrize('kind',['pdf','excel'])
@pytest.mark.parametrize('change',['denied','stale','overwrite_cancelled'])
def test_export_dialogs_cannot_bypass_revocation_context_change_or_overwrite(app,monkeypatch,tmp_path,kind,change):
    view=export_view(tmp_path)
    path=tmp_path/('existing.'+('pdf' if kind=='pdf' else 'xlsx'));path.write_bytes(b'ORIGINAL')
    permissions=[True]
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:permissions[0])
    def pick(*a,**kw):
        if change=='denied':permissions[0]=False
        if change=='stale':view._loaded=False;view._preview_snapshot=None
        return str(path),''
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',pick)
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:change!='overwrite_cancelled')
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
    forbidden=Mock(side_effect=AssertionError('No generation is allowed'))
    monkeypatch.setattr(ui,'run_with_progress',forbidden)
    monkeypatch.setattr('sme_gend.services.document_pdf_output.save_document_pdf',forbidden)
    getattr(ui.ListePresentView,'_export_'+kind)(view)
    assert path.read_bytes()==b'ORIGINAL'
    view._service.export_excel.assert_not_called()
    view._service.capture_snapshot.assert_not_called()
    view._service.validate_snapshot.assert_not_called()
    forbidden.assert_not_called()


@pytest.mark.parametrize('kind',['pdf','excel'])
def test_output_extension_is_added_before_overwrite_confirmation(app,monkeypatch,tmp_path,kind):
    view=export_view(tmp_path);path=tmp_path/'without_extension'
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:True)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(path),''))
    destinations=[]
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda owner,path:destinations.append(path) or False)
    getattr(ui.ListePresentView,'_export_'+kind)(view)
    assert destinations==[str(path)+('.pdf' if kind=='pdf' else '.xlsx')]


@pytest.mark.parametrize('change',['denied','stale'])
def test_pdf_preparation_cannot_publish_after_permission_or_context_changes(app,monkeypatch,tmp_path,change):
    view=export_view(tmp_path);permissions=[True]
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a:permissions[0])
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(tmp_path/'presents.pdf'),''))
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:True)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,text,fn:fn())
    def render(snapshot,**kwargs):
        assert kwargs=={'detailed':True,'for_print':True}
        if change=='denied':permissions[0]=False
        else:view._preview_snapshot=None
        return '<p>SYNTHETIC PRINT</p>'
    view._service.render_snapshot_html=render
    output=Mock(side_effect=AssertionError('No PDF publication after invalidation'))
    monkeypatch.setattr('sme_gend.services.document_pdf_output.save_document_pdf',output)
    ui.ListePresentView._export_pdf(view)
    output.assert_not_called()


@pytest.mark.parametrize('width',[760,1280])
@pytest.mark.parametrize('custom',[False,True])
def test_present_toolbar_fits_available_client_width(app,width,custom,tmp_path):
    from PySide6.QtGui import QFont,QFontDatabase
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI',10))
    view=ui.ListePresentView(SimpleNamespace(),SimpleNamespace() if custom else None)
    if custom:view.set_open_layout_editor(Mock())
    view.resize(width,700);view.show();app.processEvents()
    assert view.width()<=width
    output=view._btn_export_menu if width<1100 else view._btn_export
    for button in (view._btn_refresh,output,view._btn_zoom_in):
        assert button.isVisible() and button.geometry().right()<view.width()
    assert view.grab().save(str(tmp_path/f'presents_{width}_{custom}.png'))
    view.close();view.deleteLater();app.processEvents()


def test_zoom_scales_the_explicit_html_table_font(app):
    view=ui.ListePresentView(SimpleNamespace())
    view._set_preview_html('<table style="font-size:10pt"><tr><td>SYNTHETIC ALPHA</td></tr></table>')
    before=view._preview.document().find('SYNTHETIC ALPHA').charFormat().fontPointSize()
    view._zoom_in()
    after=view._preview.document().find('SYNTHETIC ALPHA').charFormat().fontPointSize()
    assert after>before
    view._zoom_reset()
    assert view._preview.document().find('SYNTHETIC ALPHA').charFormat().fontPointSize()==before
    view.close();view.deleteLater();app.processEvents()


def test_zoom_does_not_modify_personnel_text_or_the_original_export_html():
    source='<style>td {font-size:10pt}</style><p style="font-size:12px">Observation : font-size:10pt</p>'
    rendered=ui._zoomed_preview_html(source,1.5)
    assert 'font-size:15pt' in rendered and 'font-size:18px' in rendered
    assert 'Observation : font-size:10pt' in rendered
    assert ui._zoomed_preview_html(source,1)==source
