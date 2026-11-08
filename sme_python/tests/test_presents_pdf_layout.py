"""Real Qt document/PDF output: readable columns and repeated print headers."""
import unicodedata
from dataclasses import replace
import pytest
from PySide6.QtGui import QFont,QFontDatabase,QTextDocument,QTextTable
from PySide6.QtWidgets import QApplication
from PySide6.QtPdf import QPdfDocument
from sme_gend.services.liste_present_service import ListePresentService
from test_presents_corrections import snapshot


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/tahoma.ttf')
    app.setFont(QFont('Tahoma',10))
    return app


def test_print_html_uses_real_small_fonts_and_repeated_column_headers(app,tmp_path):
    service=ListePresentService.__new__(ListePresentService)
    snap=snapshot(tmp_path)
    document=QTextDocument();document.setHtml(service.render_snapshot_html(snap,detailed=True,for_print=True))
    tables=[frame for frame in document.rootFrame().childFrames()
            if isinstance(frame,QTextTable) and frame.format().headerRowCount()==1]
    assert len(tables)==1 and tables[0].columns()==18 and tables[0].format().headerRowCount()==1
    block=document.begin()
    while block.isValid() and 'UNIQUE AGENT' not in block.text():block=block.next()
    assert block.isValid() and block.begin().fragment().charFormat().font().pointSizeF()==7


def test_pdf_is_landscape_complete_and_retains_the_month(app,tmp_path,monkeypatch):
    from sme_gend.services.document_pdf_output import save_document_pdf
    from sme_gend.services.liste_generale_print import sync_document_to_printer
    from test_liste_present_populate import _row
    service=ListePresentService.__new__(ListePresentService)
    snap=snapshot(tmp_path)
    people=[_row(id=str(index),nom=f'SYNTHETIC PERSON {index:03}',structure='SYN',sub='UNIT') for index in range(1,61)]
    snap=replace(snap,sections=[('SYNTHETIC STRUCTURE','','SYN',[('UNIT',people)])],anomalies=None)
    document=QTextDocument();document.setHtml(service.render_snapshot_html(snap,detailed=True,for_print=True))
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    destination=tmp_path/'presents.pdf'
    save_document_pdf(document,destination,configure=sync_document_to_printer,
        doc_type='liste_present',count=60,settings=snap.settings,mois=snap.month)
    pdf=QPdfDocument();assert pdf.load(str(destination))==QPdfDocument.Error.None_
    try:
        all_text=''
        for page in range(pdf.pageCount()):
            size=pdf.pagePointSize(page);assert size.width()>size.height()
            text=pdf.getAllText(page).text()
            compact=''.join(unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode().split())
            assert 'Nomsetprenoms' in compact
            all_text+=compact
        assert 'MoisSME:2026-09' in all_text
        assert all(all_text.count(f'SYNTHETICPERSON{index:03}')==1 for index in range(1,61))
        assert 'TOTALGENERAL' in all_text
    finally:pdf.close()
