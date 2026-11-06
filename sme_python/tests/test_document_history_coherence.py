"""Document history must describe real successful outputs and central actions."""
from contextlib import closing
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
import pytest
from openpyxl import Workbook
from sme_gend.services import doc_gen_log as log
from sme_gend.models.liste_generale import ListeGeneraleRow

@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

def person():return ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)}|dict(id='1',noms='SYNTHETIC',grade='CNE',structure='SYN')))

def test_document_log_failure_is_explicit_even_without_a_local_mirror(monkeypatch):
    monkeypatch.setattr(log,'_append_doc_gen_mysql',lambda **kw:False)
    monkeypatch.setenv('SME_AUDIT_JSON_MIRROR','0')
    with pytest.raises(RuntimeError,match='généré.*historique'):
        log.log_doc_generation(doc_type='liste_personnel',format='xlsx',dest_path='synthetic.xlsx')

def test_csv_history_import_requires_permission_before_opening_a_file(monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:False)
    with pytest.raises(PermissionError):log.merge_doc_log_from_csv(tmp_path/'must_not_be_opened.csv')

def test_document_history_shows_count_when_persons_is_empty(app,monkeypatch):
    from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    view=DocGenLogView()
    try:
        view._entries=[dict(doc_type='numerique',persons=[],count=42)]
        view._render_entries()
        assert '42' in str(view._model.index(0,3).data())
    finally:view._filter_timer.stop();view.close();view.deleteLater();app.processEvents()

@pytest.mark.parametrize('kind',['mission','desertion','stage','stage_interne','malade','punis','gain','distinction'])
@pytest.mark.parametrize('failure',[False,True])
def test_list_workbooks_log_once_only_after_successful_save(tmp_path,monkeypatch,kind,failure):
    from sme_gend.services.missions_desertion_export import export_missions_workbook,export_desertions_workbook
    from sme_gend.services.stages_export import export_stages_workbook,export_stage_interne_workbook
    from sme_gend.services.malade_punis_export import export_malades_workbook,export_punis_workbook
    from sme_gend.services.gain_perte_export import export_gain_perte_workbook
    from sme_gend.services.distinction_excel_export import export_distinctions_workbook
    from sme_gend.config.settings import Settings
    settings=Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    dest=tmp_path/(kind+'.xlsx');seen=[]
    def capture(**kw):
        assert dest.is_file()
        seen.append(kw)
    monkeypatch.setattr(log,'log_doc_generation',capture)
    if failure:
        def fail(*a,**kw):raise OSError('synthetic disk full')
        monkeypatch.setattr(Workbook,'save',fail)
    rows=[person()]
    if kind=='mission':run=lambda:export_missions_workbook(dest,rows=rows,details={})
    elif kind=='desertion':run=lambda:export_desertions_workbook(dest,rows=rows,details={})
    elif kind=='stage':run=lambda:export_stages_workbook(dest,title='Test stage',rows=rows,details={})
    elif kind=='stage_interne':run=lambda:export_stage_interne_workbook(dest,rows=rows,details={})
    elif kind=='malade':run=lambda:export_malades_workbook(dest,rows=rows,details={})
    elif kind=='punis':run=lambda:export_punis_workbook(dest,rows=rows)
    elif kind=='gain':
        from sme_gend.models.gain_perte import GainPerteRow
        run=lambda:export_gain_perte_workbook(dest,rows=[GainPerteRow('1','CNE','SYNTHETIC','GAIN','092026','',mois_ym='2026-09')],mois_ym='2026-09')
    else:run=lambda:export_distinctions_workbook(dest,lines=[])
    if failure:
        with pytest.raises(OSError,match='disk full'):run()
        assert not seen
    else:
        run();assert len(seen)==1
        assert seen[0]['count']==(0 if kind=='distinction' else 1)
        assert seen[0]['format']=='xlsx' and seen[0]['dest_path']==str(dest)


@pytest.mark.parametrize('failure',[False,True])
def test_pdf_history_only_records_a_successful_atomic_publication(app,tmp_path,monkeypatch,failure):
    from PySide6.QtGui import QTextDocument
    from sme_gend.services.document_pdf_output import save_document_pdf
    dest=tmp_path/'previous.pdf';dest.write_bytes(b'PREVIOUS PDF');seen=[]
    monkeypatch.setattr(log,'log_doc_generation',lambda **kw:seen.append(kw))
    def render(document,printer):
        Path(printer.outputFileName()).write_bytes(b'%PDF-SYNTHETIC')
        if failure:raise OSError('synthetic printer failure')
    monkeypatch.setattr(QTextDocument,'print_',render)
    doc=QTextDocument()
    run=lambda:save_document_pdf(doc,dest,configure=lambda *a:None,doc_type='liste_present',count=7,mois='2026-09')
    if failure:
        with pytest.raises(OSError,match='printer failure'):run()
        assert not seen and dest.read_bytes()==b'PREVIOUS PDF'
    else:
        run();assert len(seen)==1 and seen[0]['count']==7 and seen[0]['dest_path']==str(dest)


def test_zip_history_records_final_archive_not_temporary_docx(app,tmp_path,monkeypatch):
    import zipfile
    from contextlib import contextmanager
    @contextmanager
    def temporary_documents():
        # Inherit the workspace ACL instead of Windows tempfile's mode 0700.
        folder = tmp_path / "synthetic-documents"
        folder.mkdir()
        try:
            yield str(folder)
        finally:
            for file in folder.iterdir():
                file.unlink()
            folder.rmdir()
    monkeypatch.setattr('tempfile.TemporaryDirectory', temporary_documents)
    from sme_gend.config.settings import Settings
    from sme_gend.ui.views import limite_age_view as ui
    dest=tmp_path/'synthetic.zip';seen=[]
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',lambda *a,**kw:(str(dest),''))
    monkeypatch.setattr('PySide6.QtWidgets.QMessageBox.information',lambda *a:None)
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_progress',lambda parent,title,text,work:work())
    monkeypatch.setattr(log,'log_doc_generation',lambda **kw:seen.append(kw))
    class Service:
        def load(self):return object()
        def generate_docx(self,row,cfg,path,*args,**kw):
            assert kw['log_generation'] is False
            Path(path).write_bytes(b'SYNTHETIC DOCX')
    ui._export_attestations_zip(None,Service(),[(person(),None),(person(),None)],Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'),dialog_title='TEST',default_name='synthetic.zip')
    assert len(seen)==1 and seen[0]['doc_type']=='zip' and seen[0]['dest_path']==str(dest) and seen[0]['count']==2
    with zipfile.ZipFile(dest) as archive:assert len(set(archive.namelist()))==2


def test_history_revision_poll_refreshes_filters_and_rejects_old_database(app,monkeypatch,tmp_path):
    from dataclasses import replace
    from sme_gend.config.settings import Settings
    from sme_gend.ui.views import doc_gen_log_view as ui
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    jobs=[]
    monkeypatch.setattr(ui,'run_in_background',lambda parent,work,done,**kw:jobs.append((done,kw['on_error'])))
    view=ui.DocGenLogView();settings=Settings(database_path=tmp_path/'unused.mdb',mysql_database='SYN_A')
    view._settings=settings;view.isVisible=lambda:True
    refreshes=[];view.refresh=lambda:refreshes.append(view._filter_search.text())
    try:
        view._filter_search.setText('KEEP FILTER');view._filter_timer.stop()
        view._poll_document_revision();assert len(jobs)==1
        jobs[-1][0]('ONE');assert refreshes==['KEEP FILTER']
        view._poll_document_revision();view._settings=replace(settings,mysql_database='SYN_B')
        jobs[-1][0]('OLD DATABASE');assert refreshes==['KEEP FILTER']
        view._poll_document_revision();jobs[-1][1](RuntimeError('PRIVATE DRIVER DATA'))
        assert 'PRIVATE' not in view._lbl_count.text() and not view._filtered_entries()
    finally:view._filter_timer.stop();view._revision_timer.stop();view.close();view.deleteLater();app.processEvents()



def test_saved_excel_history_uses_actual_format_even_without_filename_extension(tmp_path,monkeypatch):
    from sme_gend.config.settings import Settings
    seen=[];monkeypatch.setattr(log,'log_doc_generation',lambda **kw:seen.append(kw))
    log.log_saved_list(tmp_path/'file_without_extension',[],doc_type='missions',settings=Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'))
    assert seen[0]['format']=='xlsx' and seen[0]['count']==0


def test_qt_pdf_output_really_creates_a_pdf_before_logging(app,tmp_path,monkeypatch):
    from PySide6.QtGui import QTextDocument
    from sme_gend.services.document_pdf_output import save_document_pdf
    from sme_gend.services.liste_generale_print import sync_document_to_printer
    seen=[];path=tmp_path/'native.pdf'
    def capture(**kw):
        assert path.read_bytes().startswith(b'%PDF-')
        seen.append(kw)
    monkeypatch.setattr(log,'log_doc_generation',capture)
    doc=QTextDocument();doc.setHtml('<p>SYNTHETIC DOCUMENT HISTORY TEST</p>')
    save_document_pdf(doc,path,configure=sync_document_to_printer,doc_type='liste_present',count=1)
    assert len(seen)==1
