"""Synthetic first-use layout, duplicate rubrics and Excel print settings."""
from dataclasses import replace
from unittest.mock import Mock

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.properties import PageSetupProperties
import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_populate import populate_liste_sections
from sme_gend.services.excel_template_exporter import export_liste_fidele
from test_liste_present_populate import _row, _st


@pytest.mark.parametrize('read_only,may_write',[(True,True),(False,False)])
def test_missing_layout_can_be_read_without_writing_or_edit_permission(tmp_path,monkeypatch,read_only,may_write):
    service=LayoutConfigService.__new__(LayoutConfigService)
    service._settings=Settings(database_path=tmp_path/'unused')
    cfg=ListeLayoutConfig(sections=[SectionBlock('synthetic',0,'U1')])
    service.build_default_from_database=lambda:cfg
    service.save=Mock(side_effect=PermissionError('Must not write during a consultation'))
    monkeypatch.setattr('sme_gend.services.layout_write_guard.load_layout_payload',lambda settings:None)
    monkeypatch.setattr('sme_gend.services.shared_config_write.may_write_config',lambda key:may_write)
    assert service.load(read_only=read_only) is cfg
    assert cfg.write_token==fingerprint(None)
    service.save.assert_not_called()


def test_authorized_first_layout_edit_still_initializes_with_the_absence_token(tmp_path,monkeypatch):
    service=LayoutConfigService.__new__(LayoutConfigService)
    service._settings=Settings(database_path=tmp_path/'unused')
    cfg=ListeLayoutConfig()
    service.build_default_from_database=lambda:cfg
    service.save=Mock()
    monkeypatch.setattr('sme_gend.services.layout_write_guard.load_layout_payload',lambda settings:None)
    monkeypatch.setattr('sme_gend.services.shared_config_write.may_write_config',lambda key:True)
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.has_import_transaction',lambda settings:False)
    assert service.load() is cfg
    assert cfg.write_token==fingerprint(None)
    service.save.assert_called_once_with(cfg)


@pytest.mark.parametrize('code,sub,labels,group',[
    ('CS-DPF','DIV A',['DIV A','div a'],True),
    ('RG-BZV','COMMANDEMENT',['COMMANDEMENT RG-BZV','COMMANDEMENT'],False),
    ('U1','DIV A',['DIV A','DIV A'],False),
    ('U1','DIV A',['DIV A'],True),
])
def test_equivalent_rubrics_and_repeated_source_rows_never_duplicate_an_agent(code,sub,labels,group):
    row=_row(id='42',structure=code,sub=sub)
    cfg=ListeLayoutConfig(sections=[SectionBlock('s',0,code,group_by_subdivision=group,subdivision_order=labels)])
    sections,missing,assigned=populate_liste_sections(cfg,[row,replace(row)],_st(code),include_orphans=True)
    people=[person for _,_,_,blocks in sections for _,persons in blocks for person in persons]
    assert [person.id for person in people]==['42']
    assert assigned=={'id:42'} and not missing


def test_repeated_orphan_source_rows_are_included_once_and_reported_once():
    row=_row(id='42',structure='UNKNOWN',sub='NOT CONFIGURED')
    sections,missing,assigned=populate_liste_sections(ListeLayoutConfig(),[row,replace(row)],{},include_orphans=True)
    assert sum(len(people) for _,_,_,blocks in sections for _,people in blocks)==1
    assert missing==[('UNKNOWN','NOT CONFIGURED',1)] and assigned=={'id:42'}


@pytest.mark.parametrize('code,group,labels',[
    ('U1',False,None),('U1',False,['DIV A']),('CS-DPF',True,['DIV A']),
])
def test_flat_structure_keeps_unconfigured_subdivisions_in_the_same_block(code,group,labels):
    rows=[_row(id='1',structure=code,sub='DIV A',grade='MDC'),
          _row(id='2',structure=code,sub='UNCONFIGURED',grade='COL'),
          _row(id='3',structure=code,sub='',grade='CNE')]
    cfg=ListeLayoutConfig(sections=[SectionBlock('s',0,code,group_by_subdivision=group,subdivision_order=labels)])
    sections,missing,assigned=populate_liste_sections(cfg,rows,_st(code),include_orphans=True)
    assert len(sections)==1 and len(sections[0][3])==1
    assert [person.id for person in sections[0][3][0][1]]==['2','3','1']
    assert not missing and len(assigned)==3


def test_faithful_excel_keeps_template_print_setup_but_uses_generated_area(tmp_path):
    template=tmp_path/'synthetic_template.xlsx'
    workbook=Workbook();sheet=workbook.active;sheet.title='Liste'
    sheet['A1']='SYNTHETIC PRESENTS';sheet.merge_cells('A1:R1')
    sheet['A3']='N°';sheet['B3']='Nom';sheet['A4']='Structure';sheet['A5']='Subdivision'
    sheet['A6']=1;sheet['B6']='STYLE ONLY'
    sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3
    sheet.page_setup.scale=75;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.page_setup.id='rIdTemplatePrinter'
    sheet.sheet_properties.pageSetUpPr=PageSetupProperties(fitToPage=True,autoPageBreaks=False)
    sheet.page_margins.left=.21;sheet.page_margins.right=.32
    sheet.print_options.horizontalCentered=True
    sheet.oddHeader.center.text='UNITÉ SYNTHÉTIQUE';sheet.oddFooter.right.text='Page &P / &N'
    sheet.print_title_rows='2:3';sheet.print_area='A1:R500';sheet.freeze_panes='A4'
    workbook.save(template);workbook.close()
    destination=tmp_path/'presents.xlsx'
    row=_row(id='42',structure='U1',sub='DIV A',nom='SYNTHETIC AGENT')
    export_liste_fidele(template,ListeLayoutConfig(),[('STRUCTURE','U1','U1',[('DIV A',[row])])],destination)
    output=load_workbook(destination)
    try:
        page=output.active
        assert page.page_setup.orientation=='landscape' and page.page_setup.paperSize==int(page.PAPERSIZE_A3)
        assert page.page_setup.id is None
        assert (page.page_setup.scale,page.page_setup.fitToWidth,page.page_setup.fitToHeight)==(75,1,0)
        assert page.sheet_properties.pageSetUpPr.fitToPage is True
        assert page.page_margins.left==.21 and page.page_margins.right==.32
        assert page.print_options.horizontalCentered is True
        assert page.oddHeader.center.text=='UNITÉ SYNTHÉTIQUE' and page.oddFooter.right.text=='Page &P / &N'
        assert page.print_title_rows=='$2:$3' and page.freeze_panes=='A4'
        assert '$A$1:$R$6' in str(page.print_area)
        assert page.max_row==6 and page['C6'].value=='SYNTHETIC AGENT'
    finally:
        output.close()


def test_layout_counts_use_the_monthly_reference_aware_presence_query(tmp_path):
    service=LayoutConfigService.__new__(LayoutConfigService)
    service._settings=Settings(database_path=tmp_path/'unused',mysql_view_mois='2026-09')
    rows=[_row(id='1',structure=' U1 '),_row(id='2',structure='U1'),_row(id='3',structure='')]
    rows[0].position_admin='Activité au corps'  # A custom label marked present in the reference.
    repository=Mock()
    repository.fetch_for_presence_mode.return_value=rows
    service._liste_generale=repository
    assert service.present_counts_by_structure()=={'U1':2}
    repository.fetch_for_presence_mode.assert_called_once_with('present',mois_ym='2026-09')
    repository.fetch_all.assert_not_called()
