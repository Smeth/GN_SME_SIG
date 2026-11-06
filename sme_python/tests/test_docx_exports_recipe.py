"""Real DOCX generators with synthetic people and unmodified templates.

These check OOXML/content only; Word/LibreOffice layout is a separate recipe.
"""
from dataclasses import fields,replace
from datetime import datetime,date
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
from docx import Document
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.limite_age_config import LimiteAgeConfig,LimiteAgeDocOptions,GradeLimiteEntry
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteAgeService
from sme_gend.services.soit_transmis_service import SoitTransmisService


@pytest.mark.parametrize('kind',['limite_age','soit_transmis','batch'])
def test_actual_word_exports_reopen_with_expected_people_and_valid_relationships(tmp_path,monkeypatch,kind):
    root=Path(__file__).resolve().parents[1]
    monkeypatch.setattr('sme_gend.config.settings.resolve_modele_file',lambda name:root/'modeles'/name)
    monkeypatch.setattr('sme_gend.services.limite_age_service._resolve_service_line',lambda *args:('','',''))
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    person=ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|
        dict(id='42',noms='SYNTHETIC PERSON ONE',grade='LTN',date_naissance=datetime(1990,2,3),date_service='05/06/2010',matricule_militaire='SYNTHETIC-42')))
    second=replace(person,id='43',noms='SYNTHETIC PERSON TWO',matricule_militaire='SYNTHETIC-43')
    cfg=LimiteAgeConfig(grades=[GradeLimiteEntry('LTN',55,37)],options=LimiteAgeDocOptions(signataire_nom='SYNTHETIC SIGNER'))
    target=tmp_path/(kind+'.docx')
    if kind=='limite_age':LimiteAgeService().generate_docx(person,cfg,target,sign_date=date(2026,9,30),log_generation=False)
    elif kind=='batch':LimiteAgeService().generate_docx_batch([person,second],cfg,target,sign_date=date(2026,9,30))
    else:SoitTransmisService().generate_docx([person,second],SoitTransmisConfig(),target,sign_date=date(2026,9,30))
    document=Document(target)
    texts=[para.text for para in document.paragraphs]
    texts += [cell.text for table in document.tables for row in table.rows for cell in row.cells]
    text=' '.join(texts)
    assert ('SYNTHETIC PERSON ONE' if kind=='soit_transmis' else 'SYNTHETIC Person One') in text
    if kind!='limite_age':assert ('SYNTHETIC PERSON TWO' if kind=='soit_transmis' else 'SYNTHETIC Person Two') in text
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        tree=ElementTree.fromstring(archive.read('word/_rels/document.xml.rels'))
        ids=[rel.attrib['Id'] for rel in tree]
        assert len(ids)==len(set(ids))
    assert not list(tmp_path.glob('.sme-*'))
