"""Cross-module filters against synthetic rows only."""
from dataclasses import fields,replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail,MissionDetail,MaladeDetail

def row(**kw):
    return ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)}|dict(id='12',noms='ÉLOÏSE',grade='GND',structure='SYN')|kw))

@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

@pytest.mark.parametrize('field,value,query',[
    ('noms','ÉLOÏSE','eloise'),('lieu_naissance','BÉNIN','benin'),('ndecision','SYN-ACT','syn-act'),
    ('date_naissance',datetime(1990,2,3),'03/02/1990'),('date_grade',datetime(2020,2,3),'2020-02-03'),
])
def test_personnel_free_search_covers_visible_values(field,value,query):
    from sme_gend.ui.personnel_list_filters import free_text_matches
    assert free_text_matches(row(**{field:value}),query)

def test_satellite_generic_search_finds_visible_punishment_motif():
    from sme_gend.ui.list_table_filter import row_matches_search
    assert row_matches_search(row(obs='SANCTION SYNTHETIQUE'), 'SANCTION')

@pytest.mark.parametrize('query',['12','03/02/1990','SYN-ACT','eloise'])
def test_edition_free_search_covers_displayed_fields(app,query):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_EditionFilterProxy
    source=_EditionModel();source._rows=[row(date_naissance=datetime(1990,2,3),ndecision='SYN-ACT')]
    proxy=_EditionFilterProxy();proxy.setSourceModel(source);proxy.set_text(query)
    assert proxy.rowCount()==1

def test_edition_empty_solde_uses_the_business_placeholder(app):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_EditionFilterProxy
    source=_EditionModel();source._rows=[row(matricule_solde='/')]
    proxy=_EditionFilterProxy();proxy.setSourceModel(source);proxy.set_no_mat_solde(True)
    assert proxy.rowCount()==1

@pytest.mark.parametrize('kind',['stage','mission','malade'])
def test_satellite_detail_search_handles_accents(kind):
    if kind=='stage':
        from sme_gend.services.stages_list_filter import row_matches_stage_search as match
        detail=StageDetail('SÉCURITÉ','SYN',None)
    elif kind=='mission':
        from sme_gend.services.missions_list_filter import row_matches_mission_search as match
        detail=MissionDetail('SÉCURITÉ','SYN',None,None)
    else:
        from sme_gend.services.malade_punis_list_filter import row_matches_malade_search as match
        detail=MaladeDetail(None,'SÉCURITÉ',None)
    assert match(row(),'securite',detail)

def test_stage_nature_option_count_matches_its_case_insensitive_filter():
    from sme_gend.services.stages_list_filter import build_nature_filter_options,row_matches_nature_filter
    rows=[row(id='1',access_join_id='A'),row(id='2',access_join_id='B')]
    details=dict(A=StageDetail('SYNTHETIQUE',None,None),B=StageDetail('synthetique',None,None))
    options=build_nature_filter_options(rows,details)
    real=[(label,value) for label,value in options if value]
    assert len(real)==1 and '(2)' in real[0][0]
    assert sum(row_matches_nature_filter(d,real[0][1]) for d in details.values())==2

def test_gain_perte_search_matches_displayed_french_date(app):
    from sme_gend.ui.views.gain_perte_view import GainPerteView
    from sme_gend.models.gain_perte import GainPerteRow
    from PySide6.QtWidgets import QLineEdit,QTableWidget
    view=SimpleNamespace(_filter='ALL',_search=QLineEdit(),_table=QTableWidget(0,7),_count_lbl=MagicMock())
    view._search.setText('03/02/2026')
    item=GainPerteRow('12','GND','SYN','GAIN','022026','SYN',date_reference='2026-02-03')
    view._current_period_scope=lambda:('2026-02',2026);view._gaps_for_month=lambda m:[]
    view._scoped_base_rows=lambda m,y:[item]
    from sme_gend.services.personnel_list_order import GradeSortHelper
    view._ensure_grade_sort=lambda:GradeSortHelper([])
    for name in ('_update_stat_chips','_update_summary','_update_gap_banner','_update_crud_lock'):setattr(view,name,MagicMock())
    GainPerteView._apply_filter(view)
    assert view._displayed_rows==[item]


def test_grade_filter_finds_accented_display_label(app):
    from sme_gend.ui.views.grade_referentiel_view import GradesView
    from sme_gend.models.grade_ref import GradeRefRow
    view=GradesView(SimpleNamespace(settings=object()))
    view._all_rows=[GradeRefRow('GEN','GÉNÉRAL',1,None)]
    view._search.setText('general')
    assert len(view._visible_rows)==1
    view.close();view.deleteLater();app.processEvents()

def test_distinction_search_includes_its_displayed_long_label():
    from sme_gend.ui.views.distinction_referentiel_view import _RefTab
    from sme_gend.models.distinction_ref import DistinctionRefRow
    view=SimpleNamespace(_search=MagicMock(),_rows=[DistinctionRefRow(1,'SYN',libelle_long='MÉDAILLE SYNTHETIQUE')],_model=MagicMock(),_HEADERS=[])
    view._search.text.return_value='medaille';_RefTab._apply_filter(view)
    assert len(view._filtered)==1

def test_detaches_search_finds_accented_note():
    from sme_gend.services.detaches_service import filter_detaches_display
    assert filter_detaches_display([row(ndecision='DÉCISION TEST')],search='decision')

def test_user_list_search_handles_accents(app):
    from PySide6.QtWidgets import QTableWidget,QTableWidgetItem
    from sme_gend.ui.views.users_auth_view import UsersAuthView
    table=QTableWidget(1,1);table.setItem(0,0,QTableWidgetItem('ÉLOÏSE'))
    UsersAuthView._filter_user_table(SimpleNamespace(_user_table=table),'eloise')
    assert not table.isRowHidden(0)

def test_retirement_search_handles_accented_display_values():
    from sme_gend.ui.views.limite_age_view import _RetraiteTab
    view=SimpleNamespace(_grade_code_map={})
    assert _RetraiteTab._row_matches_search(view,SimpleNamespace(motif='Âge'),row(),'eloise')


def test_search_cache_never_uses_old_values_after_edit():
    from sme_gend.ui.personnel_list_filters import free_text_matches
    current=row(noms='ANCIEN',date_grade=datetime(2020,1,1))
    assert free_text_matches(current,'ancien')
    current.noms='NOUVEAU';current.date_grade=datetime(2026,9,1)
    assert not free_text_matches(current,'ancien')
    assert free_text_matches(current,'nouveau') and free_text_matches(current,'01/09/2026')
    assert not free_text_matches(current,'01/01/2020')

@pytest.mark.parametrize('query,expected',[('eloise',True),('ÉLOÏSE',True),('eliose',False),('%',False),('_',False),('0',True)])
def test_shared_search_remains_literal_and_handles_zero(query,expected):
    from sme_gend.services.search_text import search_values
    assert search_values(query,'ÉLOÏSE',0)==expected

def test_present_list_preview_and_export_share_the_same_search():
    from sme_gend.services.liste_present_service import _row_matches
    assert _row_matches(row(noms='ÉLOÏSE'),'eloise')
    assert _row_matches(row(ndecision='SYN-ACT'),'syn-act')

def test_structure_effectifs_search_keeps_count_and_identity(app):
    from PySide6.QtWidgets import QTableWidget,QLineEdit
    from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView
    view=SimpleNamespace(_search=QLineEdit(),_rows_data=[('SYN','RÉGION SYNTHETIQUE',17)],_table=QTableWidget(0,3),_selected_code=lambda:None,_sync_print_state=lambda:None)
    view._search.setText('region');StructureEffectifsView._refill_table_filtered(view)
    assert view._table.rowCount()==1 and view._table.item(0,0).text()=='SYN'
    assert view._table.item(0,2).text()=='17'

def test_personnel_proxy_combines_search_and_exact_structure(app):
    from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
    from sme_gend.ui.personnel_table_model import PersonnelTableModel
    source=PersonnelTableModel();source.set_rows([row(id='1',structure='SYN'),row(id='2',structure='OTHER')])
    proxy=PersonnelFilterProxy();proxy.setSourceModel(source)
    proxy.set_filter_criteria(only_empty_structure=False,padmin=None,grade=None,structure='SYN',subarme=None,needle='eloise')
    assert proxy.rowCount()==1
    assert source.row_at(proxy.mapToSource(proxy.index(0,0)).row()).id=='1'
