"""Pin the month and coherent capture; reject stale exports before touching files."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services import liste_present_service as module
from test_liste_present_populate import _row
from test_presents_corrections import snapshot


def synthetic_service(monkeypatch,tmp_path):
    settings=Settings(database_path=tmp_path/'unused',mysql_view_mois=None)
    service=module.ListePresentService.__new__(module.ListePresentService)
    service._settings=settings
    month=['2026-11'];events=[]
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.resolve_export_mois',
        lambda selected:selected.mysql_view_mois or month[0])
    @contextmanager
    def read_snapshot(selected):
        events.append(('begin',selected.mysql_view_mois))
        yield
        events.append(('end',selected.mysql_view_mois))
    monkeypatch.setattr(module,'mysql_read_snapshot',read_snapshot)
    cfg=ListeLayoutConfig(sections=[SectionBlock(id='test',order=0,structure_code='SYN')])
    def load(*,read_only):
        assert read_only is True and events[-1]==('begin','2026-11')
        return cfg
    service._layout_config=SimpleNamespace(load=load,resolve_subdivision_order_labels=lambda sec:None)
    def fetch(mode,*,mois_ym):
        assert mode=='present_strict'
        assert mois_ym=='2026-11'
        month[0]='2026-12'  # A different month is activated during the read.
        return [_row(id='1',nom='SYNTHETIC',structure='SYN',sub='UNIT')]
    service._liste=SimpleNamespace(fetch_for_presence_mode=fetch)
    service._structures=SimpleNamespace(fetch_all=lambda:[StructureRow(code='SYN',designation='Structure',ordre_tri=1)])
    service._subdivisions=SimpleNamespace(fetch_flat_hierarchy=lambda:[])
    monkeypatch.setattr(module,'GradeRepository',lambda selected:SimpleNamespace(fetch_all=lambda:[]))
    return service,events


def test_month_is_pinned_and_database_read_ends_before_rendering(monkeypatch,tmp_path):
    service,events=synthetic_service(monkeypatch,tmp_path)
    original=module.ListePresentService.render_snapshot_html
    def render(snapshot,**kwargs):
        assert events[-1]==('end','2026-11')
        assert snapshot.month=='2026-11' and snapshot.settings.mysql_view_mois=='2026-11'
        return original(service,snapshot,**kwargs)
    service.render_snapshot_html=render
    html=service.build_preview_html()
    assert 'Mois SME : 2026-11' in html and '2026-12' not in html


@pytest.mark.parametrize('changed',['month','database','search','presence'])
def test_stale_snapshot_is_refused_without_overwriting_a_destination(tmp_path,changed):
    snap=snapshot(tmp_path)
    service=module.ListePresentService.__new__(module.ListePresentService)
    service._settings=snap.settings
    kwargs={}
    if changed=='month':service._settings=replace(snap.settings,mysql_view_mois='2026-10')
    if changed=='database':service._settings=replace(snap.settings,mysql_database='OTHER_SYNTHETIC')
    if changed=='search':kwargs['search_filter']='OTHER'
    if changed=='presence':kwargs['presences_mode']='stage_interne'
    destination=tmp_path/'kept.xlsx';destination.write_bytes(b'ORIGINAL FILE')
    with pytest.raises(ValueError,match='Actualisez'):
        service.export_excel(destination,snapshot=snap,**kwargs)
    assert destination.read_bytes()==b'ORIGINAL FILE'


def test_reference_read_failure_never_returns_an_incomplete_snapshot(monkeypatch,tmp_path):
    service,events=synthetic_service(monkeypatch,tmp_path)
    def fail():raise OSError('SYNTHETIC GRADE READ FAILURE')
    monkeypatch.setattr(module,'GradeRepository',lambda selected:SimpleNamespace(fetch_all=fail))
    captured=[]
    with pytest.raises(OSError,match='SYNTHETIC GRADE'):
        service.build_preview_html(on_snapshot=captured.append)
    assert not captured


def test_capture_preview_and_excel_keep_only_explicit_present_positions(monkeypatch,tmp_path):
    from openpyxl import load_workbook
    service,_=synthetic_service(monkeypatch,tmp_path)
    service._settings=replace(service._settings,mysql_view_mois='2026-11')
    labels=['Présent',' PRESENT ','présent au corps',None,'','À vérifier','StageInterne',
        'Mission','Malade','CongéAdmin','Détaché','Service spécial','Agent présent spécial']
    people=[]
    for index,label in enumerate(labels):
        person=_row(id=str(index),nom=f'SYNTHETIC {index:03}',structure='SYN',sub='UNIT')
        person.position_admin=label;people.append(person)
    service._liste.fetch_for_presence_mode=lambda mode,**kw:people
    snap=service.capture_snapshot()
    displayed=[row for _,_,_,blocks in snap.sections for _,rows in blocks for row in rows]
    assert [row.id for row in displayed]==['0','1','2']
    html=service.render_snapshot_html(snap,detailed=True)
    assert all(person.noms in html for person in people[:3])
    assert all(person.noms not in html for person in people[3:])
    traces=[]
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:traces.append(kw))
    destination=tmp_path/'explicit.xlsx';service.export_excel(destination,snapshot=snap)
    book=load_workbook(destination,data_only=True)
    try:
        values=[str(value) for row in book.worksheets[0].values for value in row if value is not None]
        assert all(person.noms in values for person in people[:3])
        assert all(person.noms not in values for person in people[3:])
    finally:book.close()
    assert traces[0]['count']==3


@pytest.mark.parametrize('label',[None,'','À vérifier','Mission','StageInterne','Service spécial','Présent exceptionnel'])
def test_old_or_injected_nonpresent_snapshot_cannot_be_rendered_or_exported(tmp_path,label):
    snap=snapshot(tmp_path)
    snap.sections[0][3][0][1][0].position_admin=label
    service=module.ListePresentService.__new__(module.ListePresentService)
    service._settings=snap.settings
    with pytest.raises(ValueError,match='Actualisez'):
        service.render_snapshot_html(snap,detailed=True)
    destination=tmp_path/'keep.xlsx';destination.write_bytes(b'UNCHANGED')
    with pytest.raises(ValueError,match='Actualisez'):
        service.export_excel(destination,snapshot=snap)
    assert destination.read_bytes()==b'UNCHANGED'


@pytest.mark.parametrize('mode',['stage_interne','les_deux'])
def test_module_refuses_other_presence_modes_before_reading_data(tmp_path,mode):
    service=module.ListePresentService.__new__(module.ListePresentService)
    with pytest.raises(ValueError,match='uniquement'):
        service.capture_snapshot(presences_mode=mode)
