"""A failed export must preserve the previous complete destination."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from openpyxl import Workbook
from sme_gend.config.settings import Settings


@pytest.mark.parametrize('kind',['sme','dpf'])
def test_workbook_save_failure_preserves_previous_destination(tmp_path,monkeypatch,kind):
    from sme_gend.services import clean_sme_exports as export
    target=tmp_path/'previous.xlsx';target.write_bytes(b'PREVIOUS COMPLETE EXPORT')
    monkeypatch.setattr(export,'_movements',lambda *a,**kw:[])
    def fail(book,path):Path(path).write_bytes(b'PARTIAL ZIP');raise OSError('synthetic disk full')
    monkeypatch.setattr(Workbook,'save',fail)
    settings=Settings(database_path=Path('unused.mdb'),mysql_view_mois='2026-09')
    with pytest.raises(OSError,match='disk full'):
        if kind=='sme':export.build_numerique(settings,target,rows=[],grades=[],details={},mois='2026-09',detaches=[],grade_order=[],previous_counts=[])
        else:export.build_dpf(settings,target,SimpleNamespace(mois='2026-09',liste_gnle=[]),[],{})
    assert target.read_bytes()==b'PREVIOUS COMPLETE EXPORT'
    assert not list(tmp_path.glob('.sme-*'))


def test_pdf_print_failure_preserves_previous_destination(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QTextDocument
    from sme_gend.services import parcours_pdf_export as pdf
    app=QApplication.instance() or QApplication([])
    target=tmp_path/'previous.pdf';target.write_bytes(b'PREVIOUS COMPLETE PDF')
    monkeypatch.setattr(pdf,'build_dossier_html',lambda *a:'<p>SYNTHETIC</p>')
    def fail(document,printer):Path(printer.outputFileName()).write_bytes(b'PARTIAL PDF');raise OSError('synthetic printer failure')
    monkeypatch.setattr(QTextDocument,'print_',fail)
    with pytest.raises(OSError,match='printer failure'):
        pdf.export_parcours_pdf(None,Settings(database_path=Path('unused.mdb')),target)
    assert target.read_bytes()==b'PREVIOUS COMPLETE PDF'
    assert not list(tmp_path.glob('.sme-*'))



@pytest.mark.parametrize('kind',['limite_age','soit_transmis','batch'])
def test_docx_save_failure_preserves_previous_destination(tmp_path,monkeypatch,kind):
    from dataclasses import fields
    from datetime import datetime,date
    from docx.document import Document
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.models.limite_age_config import LimiteAgeConfig,LimiteAgeDocOptions,GradeLimiteEntry
    from sme_gend.models.soit_transmis_config import SoitTransmisConfig
    from sme_gend.services.limite_age_service import LimiteAgeService
    from sme_gend.services.soit_transmis_service import SoitTransmisService
    root=Path(__file__).resolve().parents[1]
    monkeypatch.setattr('sme_gend.config.settings.resolve_modele_file',lambda name:root/'modeles'/name)
    monkeypatch.setattr('sme_gend.services.limite_age_service._resolve_service_line',lambda *args:('','',''))
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    person=ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|
        dict(id='42',noms='SYNTHETIC PERSON',grade='LTN',date_naissance=datetime(1990,2,3),date_service='01/01/2010')))
    cfg=LimiteAgeConfig(grades=[GradeLimiteEntry('LTN',55,37)],options=LimiteAgeDocOptions(signataire_nom='SYNTHETIC SIGNER'))
    target=tmp_path/'previous.docx';target.write_bytes(b'PREVIOUS COMPLETE DOCX')
    original=Document.save
    def fail(document,path):
        if Path(path).parent==target.parent:
            Path(path).write_bytes(b'PARTIAL DOCX');raise OSError('synthetic docx failure')
        return original(document,path)
    monkeypatch.setattr(Document,'save',fail)
    with pytest.raises(OSError,match='docx failure'):
        if kind=='limite_age':LimiteAgeService().generate_docx(person,cfg,target,sign_date=date(2026,9,30),log_generation=False)
        elif kind=='batch':LimiteAgeService().generate_docx_batch([person],cfg,target,sign_date=date(2026,9,30))
        else:SoitTransmisService().generate_docx([person],SoitTransmisConfig(),target,sign_date=date(2026,9,30))
    assert target.read_bytes()==b'PREVIOUS COMPLETE DOCX'
    assert not list(tmp_path.glob('.sme-*'))
