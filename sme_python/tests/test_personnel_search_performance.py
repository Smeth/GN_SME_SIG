from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import replace
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QLineEdit
from test_filter_coherence import row


@pytest.fixture(scope='module')
def app():return QApplication.instance() or QApplication([])


def test_search_cache_covers_a_large_month_and_reflects_edits(monkeypatch):
    from sme_gend.services import search_text
    from sme_gend.ui.personnel_list_filters import free_text_matches
    search_text._search_blob.cache_clear()
    build=Mock(wraps=search_text._build_search_blob)
    monkeypatch.setattr(search_text,'_build_search_blob',build)
    rows=[row(id=str(i),noms=f'Agent {i}')for i in range(12000)]
    assert sum(free_text_matches(r,'agent')for r in rows)==12000
    assert build.call_count==12000
    assert sum(free_text_matches(r,'does not exist')for r in rows)==0
    assert build.call_count==12000
    rows[0].noms='Nouvelle identite'
    assert free_text_matches(rows[0],'nouvelle identite')
    assert not free_text_matches(rows[0],'Agent 0')
    assert build.call_count==12001
    search_text._search_blob.cache_clear()


def test_grade_alias_cache_reflects_catalogue_edits():
    from sme_gend.services.grade_label_format import format_grade_display
    from sme_gend.models.grade_ref import GradeRefRow
    grades=[GradeRefRow('CNE','Capitaine',1,None)]
    assert format_grade_display('CAPITAINE',grades)=='CNE'
    grades[0].libelle='Capitaine nouveau'
    assert format_grade_display('Capitaine nouveau',grades)=='CNE'
    assert format_grade_display('CAPITAINE',grades)=='CAPITAINE'


def test_related_filters_are_published_once():
    from sme_gend.ui.filter_change import row_filter_change,batch_row_filters
    changes=[]
    proxy=SimpleNamespace(invalidateFilter=lambda:changes.append(True))
    @row_filter_change
    def setter(self,key,value):setattr(self,key,value)
    with batch_row_filters(proxy):
        setter(proxy,'text','A')
        with batch_row_filters(proxy):setter(proxy,'quality',True)
        setter(proxy,'other',False)
        assert changes==[]
    assert changes==[True] and proxy.text=='A' and proxy.quality


def test_export_flushes_pending_search_and_counts_are_coalesced(app):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_EditionFilterProxy,EditionSmeView
    model=_EditionModel();model.load([row(id='1',noms='ALPHA'),row(id='2',noms='BETA')],None,[],[],[],[])
    proxy=_EditionFilterProxy();proxy.setSourceModel(model)
    timer=QTimer();timer.setSingleShot(True);timer.start(180)
    text=QLineEdit();text.setText('BETA')
    view=SimpleNamespace(_model=model,_proxy=proxy,_search_debounce=timer,_search=text)
    view._flush_search_filter=lambda:EditionSmeView._flush_search_filter(view)
    assert [r.id for r in EditionSmeView._current_filtered_rows(view)]==['2']
    assert not timer.isActive()
    counts=Mock();count_timer=QTimer();count_timer.setSingleShot(True);count_timer.timeout.connect(counts)
    view._count_debounce=count_timer
    for _ in range(100):EditionSmeView._schedule_count_update(view)
    app.processEvents()
    assert counts.call_count==1


def test_find_replace_does_not_scan_on_every_keystroke(app):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_EditionFilterProxy,_FindReplaceDialog
    model=_EditionModel();model.load([row(id='1',noms='ALPHA'),row(id='2',noms='BETA')],None,[],[],[],[])
    proxy=_EditionFilterProxy();proxy.setSourceModel(model)
    view=SimpleNamespace(_model=model,_proxy=proxy,focus_find_hit=Mock())
    dialog=_FindReplaceDialog(view)
    scan=Mock(wraps=dialog._rebuild_hits);dialog._rebuild_hits=scan
    for text in ('A','AL','ALP','ALPHA'):dialog._txt_find.setText(text)
    assert scan.call_count==0
    dialog._find_next()
    assert scan.call_count==1 and dialog._hits
    assert all(model._rows[i].noms=='ALPHA' for i,field in dialog._hits)
    dialog.close()
