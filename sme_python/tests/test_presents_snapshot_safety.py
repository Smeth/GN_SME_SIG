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
