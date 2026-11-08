"""Direct export keeps every official sheet and replaces its monthly records."""
from hashlib import sha256
from types import SimpleNamespace

from openpyxl import load_workbook
import pytest

from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.numerique_satellite import DeserteurDetail, MaladeDetail, MissionDetail, StageDetail
from sme_gend.services.numerique_model_export import build_from_model
from test_numerique_model_fidelity import MODEL, details, person


def test_all_official_rubrics_and_hidden_sheets_keep_model_and_monthly_data(tmp_path):
    fingerprint=sha256(MODEL.read_bytes()).hexdigest()
    template=tmp_path/'synthetic_hidden_model.xlsx'
    template.write_bytes(MODEL.read_bytes())
    original=load_workbook(template)
    original['MISSION'].sheet_state='veryHidden'
    original['Feuil4'].sheet_state='hidden'
    original.save(template)
    states={sheet.title:sheet.sheet_state for sheet in original}
    names=original.sheetnames
    setup={sheet.title:sheet.page_setup for sheet in original}
    original.close()
    specs=[('P','Présent'),('I','Stage interne'),('M','Mission'),('L','Malade'),
           ('D','Déserteur'),('C','CongéAdmin'),('E','CongéExp'),('R','Prison MA'),
           ('U','Punis'),('N','Inst Mise Route'),('X','StageEx'),('S','StageLocal'),('T','Détaché')]
    rows=[person(key,position,obs='OBS SYNTHETIQUE '+key,
                 subdivision_arme='EMPLOYEUR TEST' if key=='T' else 'Direction')
          for key,position in specs]
    data=details()
    data['missions']['M']=MissionDetail('NATURE M','LIEU M','NOTE M','03/11/2026')
    data['malades']['L']=MaladeDetail('04/11/2026','PATHOLOGIE L','OBS L')
    data['deserteurs']['D']=DeserteurDetail('05/11/2026')
    for key,dictionary in [('I','stages_internal'),('X','stages_ex'),('S','stages_loc')]:
        data[dictionary][key]=StageDetail('FORMATION '+key,'ÉCOLE '+key,'01–30/11/2026')
    data['conges']['C']=SimpleNamespace(type_conge='ADMIN',type_label='Congé administratif',
        date_debut='01/11/2026',date_fin='30/11/2026',observation='OBS CONGE')
    data['positions'][('T','détaché')]=('06/11/2026',None,'LIEU T','NOTE T','OBS T')
    movements=[GainPerteRow('G','CNE','GAIN SYNTHETIQUE','GAIN','112026','AJOUT'),
               GainPerteRow('Q','CNE','PERTE SYNTHETIQUE','PERTE','112026','MUTATION')]
    destination=tmp_path/'monthly.xlsx'
    result=build_from_model(template,destination,mois='2026-11',rows=rows,grades=[],details=data,
        detaches=[rows[-1]],previous_counts=[0]*10,movements=movements,detaches_config=DetachesFileConfig())
    workbook=load_workbook(destination)
    cached=load_workbook(destination,data_only=True)
    try:
        assert list(result)==names==workbook.sheetnames
        assert {sheet.title:sheet.sheet_state for sheet in workbook}==states
        assert all(sheet.page_setup==setup[sheet.title] for sheet in workbook)
        assert cached['NUMERIQUE']['T8'].value==13
        for row,total in [(10,1),(11,1),(12,1),(13,1),(14,1),(15,1),(16,1),(17,1),(18,1),(19,2)]:
            assert cached['NUMERIQUE'].cell(row,20).value==total
        assert cached['NUMERIQUE']['G48'].value==11  # The model's ten categories include detached staff.
        expected={'MISSION':('M',4),'MALADE':('L',4),'DESERTEUR':('D',4),
                  'CONGé ADMIN':('C',4),'NON-REJOINT':('N',4),'STAGE-EXT':('X',4),
                  'STAGE-LOC':('S',4),'Feuil4':('I',5),'DETACHE':('T',5)}
        for sheet,(key,row) in expected.items():
            assert workbook[sheet].cell(row,3).value=='PERSONNE TEST '+key
            assert workbook[sheet].cell(row+1,3).value is None
        assert {workbook['DETENTION-PRISON'].cell(row,3).value for row in (4,5)}=={'PERSONNE TEST R','PERSONNE TEST U'}
        assert workbook['MISSION']['E4'].value=='NATURE M' and workbook['MISSION']['G4'].value=='NOTE M'
        assert workbook['MALADE']['F4'].value=='PATHOLOGIE L'
        assert workbook['Feuil4']['E5'].value=='FORMATION I'
        assert workbook['STAGE-LOC']['E4'].value=='FORMATION S'
        assert workbook['STAGE-EXT']['E4'].value=='FORMATION X'
        assert workbook['DETACHE']['F5'].value=='NOTE T'
        present_names={cell.value for row in workbook['PRESENT'] for cell in row if cell.column==3 and str(cell.value).startswith('PERSONNE TEST ')}
        assert present_names=={'PERSONNE TEST P'}
        assert any(cell.value=='GAIN SYNTHETIQUE' for row in workbook['GAIN PERTE'] for cell in row)
        assert any(cell.value=='PERTE SYNTHETIQUE' for row in workbook['GAIN PERTE'] for cell in row)
        assert cached['DETACHE PAR STRUCTURE']['C110'].value==1
        assert 'novembre' in str(workbook['PAGE INTER']['A10'].value).casefold()
        formulas=[cell for row in workbook['INDISPO PAR STRUCTURE bis'] for cell in row if cell.data_type=='f']
        assert formulas and any(isinstance(cached['INDISPO PAR STRUCTURE bis'][cell.coordinate].value,(int,float))
                                and cached['INDISPO PAR STRUCTURE bis'][cell.coordinate].value>0 for cell in formulas)
        assert all(cell.value is None for row in workbook['PRESENT'].iter_rows(min_row=4,min_col=19) for cell in row)
    finally:
        workbook.close();cached.close()
    assert sha256(MODEL.read_bytes()).hexdigest()==fingerprint


def test_formula_cache_failure_keeps_existing_destination_without_registry(tmp_path,monkeypatch):
    source_hash=sha256(MODEL.read_bytes()).hexdigest()
    destination=tmp_path/'existing.xlsx'
    destination.write_bytes(b'SYNTHETIC PREVIOUS WORKBOOK')
    failure=RuntimeError('SYNTHETIC FORMULA FAILURE')
    def fail_after_write(workbook,path):
        assert path!=destination and path.is_file() and path.stat().st_size>0
        raise failure
    monkeypatch.setattr('sme_gend.services.numerique_formula_cache.cache_model_formulae',fail_after_write)
    with pytest.raises(RuntimeError) as caught:
        build_from_model(MODEL,destination,mois='2026-11',rows=[],grades=[],details=details(),
            detaches=[],previous_counts=[None]*10,movements=[],detaches_config=DetachesFileConfig())
    assert caught.value is failure and destination.read_bytes()==b'SYNTHETIC PREVIOUS WORKBOOK'
    assert sha256(MODEL.read_bytes()).hexdigest()==source_hash
