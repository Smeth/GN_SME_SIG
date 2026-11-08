"""Exports must preserve synthetic displayed values, filters and order."""
from contextlib import closing
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from openpyxl import load_workbook
from sme_gend.models.liste_generale import ListeGeneraleRow

def person(**kw):
    return ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)}|dict(id='12',access_join_id='A',noms='SYNTHETIQUE',grade='GND',structure='SYN')|kw))

@pytest.fixture(autouse=True)
def no_log(monkeypatch):monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)

@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

def test_default_personnel_excel_respects_hidden_columns(tmp_path):
    from sme_gend.services.personnel_excel_export import export_liste_filtered
    path=tmp_path/'hidden.xlsx';export_liste_filtered([person(obs='HIDDEN')],path,hidden_keys={'obs','matricule_solde'})
    with closing(load_workbook(path)) as wb:
        headers=[cell.value for cell in wb.active[2]]
        assert 'Obs' not in headers and 'Matricule solde' not in headers

@pytest.mark.parametrize('kind',['personnel','mission','stage','malade','punis'])
def test_user_text_is_not_interpreted_as_an_excel_formula(tmp_path,kind):
    path=tmp_path/(kind+'.xlsx');row=person(noms='=1+1')
    if kind=='personnel':
        from sme_gend.services.personnel_excel_export import export_liste_filtered
        export_liste_filtered([row],path);coordinate='B3'
    elif kind=='mission':
        from sme_gend.services.missions_desertion_export import export_missions_workbook
        export_missions_workbook(path,rows=[row],details={});coordinate='B3'
    elif kind=='stage':
        from sme_gend.services.stages_export import export_stages_workbook
        export_stages_workbook(path,title='Stages',rows=[row],details={});coordinate='B3'
    else:
        from sme_gend.services.malade_punis_export import export_malades_workbook,export_punis_workbook
        if kind=='malade':export_malades_workbook(path,rows=[row],details={})
        else:export_punis_workbook(path,rows=[row])
        coordinate='B3'
    with closing(load_workbook(path)) as wb:
        cell=wb.active[coordinate]
        assert cell.value=='=1+1' and cell.data_type=='s'

def test_malade_excel_uses_the_same_fallback_key_as_the_table(tmp_path):
    from sme_gend.models.numerique_satellite import MaladeDetail
    from sme_gend.services.malade_punis_export import export_malades_workbook
    path=tmp_path/'malade.xlsx';export_malades_workbook(path,rows=[person()],details={'12':MaladeDetail('2026-09-01','SYNTHETIC PATHOLOGY','OBS')})
    with closing(load_workbook(path)) as wb:assert wb.active['G3'].value=='SYNTHETIC PATHOLOGY'

def test_long_official_row_is_not_forced_into_a_single_line(tmp_path):
    from sme_gend.services.personnel_excel_export import export_liste_filtered
    path=tmp_path/'long.xlsx';export_liste_filtered([person(noms='SYNTHETIQUE '+('LONGUE VALEUR '*25))],path)
    with closing(load_workbook(path)) as wb:assert wb.active.row_dimensions[3].height>16

def test_mission_export_preserves_actual_sorted_table_order(app,tmp_path,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget,QTableWidgetItem
    from sme_gend.ui.views import missions_view as ui
    table=QTableWidget(2,2)
    rows=[person(id='1',access_join_id='A',noms='ZULU'),person(id='2',access_join_id='B',noms='ALPHA')]
    for i,row in enumerate(rows):
        key=QTableWidgetItem(row.access_join_id);key.setData(Qt.ItemDataRole.UserRole,row.access_join_id)
        table.setItem(i,0,key);table.setItem(i,1,QTableWidgetItem(row.noms))
    table.setSortingEnabled(True);table.sortItems(1,Qt.SortOrder.AscendingOrder)
    view=SimpleNamespace(_rows=rows,_details={},_tbl=table)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(tmp_path/'mission.xlsx'),''))
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:True)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
    seen=[];monkeypatch.setattr(ui,'export_missions_workbook',lambda path,**kw:seen.extend(kw['rows']))
    ui.MissionsView._on_export(view)
    assert [r.noms for r in seen]==['ALPHA','ZULU']

def test_rubric_search_does_not_invalidate_the_complete_personnel_export():
    from sme_gend.ui.views.liste_present_view import ListePresentView
    snapshot=SimpleNamespace()
    view=SimpleNamespace(_loaded=True,_last_html='SYNTHETIC RUBRICS',_search_timer=MagicMock(),
                         _refresh_busy=False,_preview_snapshot=snapshot)
    ListePresentView._on_search_changed(view)
    assert view._loaded and view._preview_snapshot is snapshot
    assert view._last_html=='SYNTHETIC RUBRICS'
    view._search_timer.start.assert_called_once_with()

def test_present_error_is_not_an_exportable_document():
    from sme_gend.ui.views.liste_present_view import ListePresentView
    view=SimpleNamespace(_loaded=True,_last_html='OLD',_refresh_token=1,_set_preview_html=MagicMock(),_set_status=MagicMock())
    ListePresentView._on_refresh_error(view,'SYNTHETIC FAILURE',1)
    assert not view._loaded and not view._last_html

def test_structure_export_uses_visible_rows_not_the_full_catalog(app,tmp_path,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget,QTableWidgetItem
    from sme_gend.ui.views import structure_hub_view as ui
    from sme_gend.models.structure import StructureRow
    from sme_gend.config.liste_generale_scope import ExportMoisContext
    # Only the code attribute is required by this export path.
    all_rows=[SimpleNamespace(code='A'),SimpleNamespace(code='B')]
    table=QTableWidget(1,7);key=QTableWidgetItem('B');key.setData(Qt.ItemDataRole.UserRole,'B');table.setItem(0,1,key)
    view=SimpleNamespace(_require_fresh=lambda:True,_structures=all_rows,_org_by_code={},_fonction_count=lambda code:0,_table=table,_service=SimpleNamespace(settings=object()))
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.build_export_mois_context',lambda s:SimpleNamespace(label_short=lambda:'2026-09'))
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_overwrite_file',lambda *a:True)
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',lambda *a,**kw:(str(tmp_path/'structures.xlsx'),''))
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
    seen=[];monkeypatch.setattr('sme_gend.services.structure_referentiel_export.export_structures_referentiel',lambda path,rows,**kw:seen.extend(rows))
    monkeypatch.setattr(ui, 'run_reference_task', lambda parent, title, work: work())
    ui.StructureHubView._export_referentiel_excel(view)
    assert [r.code for r in seen]==['B']


def test_visible_export_snapshot_preserves_sort_hidden_rows_and_values(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget,QTableWidgetItem
    from sme_gend.ui.export_snapshot import visible_export_rows
    rows=[person(id='1',noms='ONE'),person(id='2',noms='TWO')]
    table=QTableWidget(2,1)
    for i,key in enumerate(['2','1']):
        item=QTableWidgetItem(key);item.setData(Qt.ItemDataRole.UserRole,key);table.setItem(i,0,item)
    table.setRowHidden(1,True)
    captured=visible_export_rows(table,rows,key=lambda row:row.id)
    rows[1].noms='REMOTE EDIT'
    assert [r.noms for r in captured]==['TWO']

def test_unknown_table_identity_refuses_instead_of_exporting_another_person(app):
    from PySide6.QtWidgets import QTableWidget,QTableWidgetItem
    from sme_gend.ui.export_snapshot import visible_export_rows
    table=QTableWidget(1,1);table.setItem(0,0,QTableWidgetItem('UNKNOWN'))
    with pytest.raises(ValueError,match='Actualisez'):visible_export_rows(table,[person()],key=lambda row:row.id)

def test_present_success_after_filter_invalidation_is_ignored():
    from sme_gend.ui.views.liste_present_view import ListePresentView
    view=SimpleNamespace(_refresh_token=1,_render_generation=0,_preview_generation=1,_loaded=False,_last_html='',_set_preview_html=MagicMock(),_set_status=MagicMock())
    ListePresentView._on_refresh_done(view,'OLD RESULT',1)
    assert not view._loaded and not view._last_html

def test_rubric_rapid_typing_keeps_the_local_debounced_render_requested():
    from sme_gend.ui.views.liste_present_view import ListePresentView
    view=SimpleNamespace(_loaded=True,_last_html='SYNTHETIC RUBRICS',_search_timer=MagicMock(),
                         _refresh_busy=False,_preview_snapshot=SimpleNamespace())
    ListePresentView._on_search_changed(view);ListePresentView._on_search_changed(view)
    assert view._loaded and view._search_timer.start.call_count==2

def test_present_export_uses_preview_snapshot_without_any_source_reread(tmp_path,monkeypatch,app):
    from sme_gend.services import liste_present_service as mod
    from sme_gend.models.liste_layout import ListeLayoutConfig
    from sme_gend.config.settings import Settings
    service=mod.ListePresentService.__new__(mod.ListePresentService)
    service._settings=Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09')
    layout=ListeLayoutConfig();service._layout_config=SimpleNamespace(load=lambda **kw:layout)
    from contextlib import nullcontext
    monkeypatch.setattr(mod,'mysql_read_snapshot',lambda settings:nullcontext())
    row=person(noms='ORIGINAL PREVIEW',position_admin='Présent')
    service._fetch_all_data=lambda **kw:SimpleNamespace(flat=[],st_idx={},presents=[row],rows_all=[row])
    service.build_sections_data=lambda *a,**kw:[('SYN TITLE','','SYN',[('SYN UNIT',[row])])]
    service.collect_anomalies=lambda *a,**kw:mod.PresentsAnomalies()
    monkeypatch.setattr(mod,'GradeRepository',lambda settings:SimpleNamespace(fetch_all=lambda:[]))
    captured=[];html=service.build_preview_html(on_snapshot=captured.append)
    assert 'ORIGINAL PREVIEW' in html and len(captured)==1
    row.noms='REMOTE EDIT'
    service._fetch_all_data=lambda **kw:pytest.fail('source must not be read again')
    service._layout_config.load=lambda:pytest.fail('layout must not be read again')
    dest=tmp_path/'present.xlsx';service.export_excel(dest,snapshot=captured[0])
    with closing(load_workbook(dest)) as wb:
        texts=[str(cell.value or '') for ws in wb for cells in ws for cell in cells]
        assert 'ORIGINAL PREVIEW' in texts and 'REMOTE EDIT' not in texts
    with pytest.raises(ValueError,match='aperçu'):service.export_excel(tmp_path/'wrong.xlsx',search_filter='OTHER',snapshot=captured[0])
    assert not (tmp_path/'wrong.xlsx').exists()

@pytest.mark.parametrize('full',[False,True])
def test_detaches_explicit_snapshot_never_refetches_all_agents(tmp_path,monkeypatch,full):
    from sme_gend.config.settings import Settings
    from sme_gend.models.detaches_file_config import DetachesFileConfig
    from sme_gend.services import detaches_excel_export as mod,detaches_sheet_writer
    def forbidden(*a,**kw):pytest.fail('unexpected population read')
    monkeypatch.setattr(mod,'DetachesService',forbidden)
    monkeypatch.setattr(mod,'GradeRepository',lambda settings:SimpleNamespace(fetch_all=lambda:[]))
    monkeypatch.setattr(detaches_sheet_writer,'_sort_persons',lambda rows,*a:list(rows))
    settings=Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09')
    cfg=DetachesFileConfig();rows=[person(noms='VISIBLE ONLY',position_admin='Détaché')]
    path=tmp_path/'detaches.xlsx'
    if full:mod.export_detaches_workbook_full(settings,path,cfg,use_template=False,rows=rows)
    else:mod.export_detaches_workbook(settings,path,use_template=False,rows=rows,configuration=cfg)
    with closing(load_workbook(path)) as wb:
        assert wb['SME_Detaches']['B2'].value=='VISIBLE ONLY'
        if full:assert sum(cell.value=='VISIBLE ONLY' for cells in wb['DETACHE'] for cell in cells)==1

def test_detaches_preview_source_month_is_independent_of_comparison_months(tmp_path,monkeypatch):
    from sme_gend.config.settings import Settings
    from sme_gend.models.detaches_file_config import DetachesFileConfig
    from sme_gend.services import detaches_preview_service as mod
    settings=Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09')
    service=mod.DetachesPreviewService.__new__(mod.DetachesPreviewService);service._settings=settings
    rows=[person(position_admin='Détaché')];service._svc=SimpleNamespace(fetch_detaches=lambda:rows)
    service._html_compare=lambda *a,**kw:''
    monkeypatch.setattr(mod,'DetachesConfigService',lambda s:SimpleNamespace(load=lambda:DetachesFileConfig()))
    monkeypatch.setattr(mod,'_build_sec_lookup',lambda s:{})
    monkeypatch.setattr(mod,'default_compare_months',lambda s:('2020-01','2020-02'))
    monkeypatch.setattr(mod,'list_available_export_mois',lambda s:[])
    bundle=service.build_bundle(search='SYNTHETIQUE')
    rows[0].noms='CHANGED'
    assert bundle.source_settings.mysql_view_mois=='2026-09'
    assert bundle.rows[0].noms=='SYNTHETIQUE'

def test_personnel_grouped_export_respects_hidden_columns_and_total(tmp_path):
    from sme_gend.services.personnel_excel_export import export_liste_by_padmin
    path=tmp_path/'grouped.xlsx';counts=export_liste_by_padmin([person(position_admin='Présent')],path,hidden_keys={'obs','matricule_solde'})
    assert counts=={'Présent':1}
    with closing(load_workbook(path)) as wb:
        assert 'Obs' not in [cell.value for cell in wb['Présent'][2]]
        assert wb['Récapitulatif']['B3'].value==1

def test_literal_export_keeps_zero_and_identifier_leading_zeros(tmp_path):
    from sme_gend.services.personnel_excel_export import export_liste_filtered
    path=tmp_path/'identifiers.xlsx';export_liste_filtered([person(matricule_militaire='0007',matricule_solde='0')],path)
    with closing(load_workbook(path)) as wb:
        assert wb.active['H3'].value=='0007' and wb.active['H3'].data_type=='s'
        assert wb.active['I3'].value=='0'


def test_present_fallback_preserves_literal_text_and_template_formulas(tmp_path):
    from sme_gend.services.excel_template_exporter import export_simple_fallback,_copy_row_values_and_style
    from openpyxl import Workbook
    path=tmp_path/'present_literal.xlsx'
    export_simple_fallback(path,[('TITLE','','SYN',[('UNIT',[person(noms='=1+1')])])])
    with closing(load_workbook(path)) as wb:
        found=[cell for cells in wb.active for cell in cells if cell.value=='=1+1']
        assert len(found)==1 and found[0].data_type=='s'
    template=Workbook();template.active['A1']='=SUM(1,2)';out=Workbook()
    _copy_row_values_and_style(template.active,1,out.active,1,1)
    assert out.active['A1'].data_type=='f' and out.active['A1'].value=='=SUM(1,2)'

def test_personnel_hidden_birth_component_does_not_reappear_in_combined_cell(tmp_path):
    from datetime import datetime
    from sme_gend.services.personnel_excel_export import export_liste_filtered
    path=tmp_path/'birth.xlsx';export_liste_filtered([person(date_naissance=datetime(1990,1,1),lieu_naissance='HIDDEN LOCATION')],path,hidden_keys={'lieu_naissance'})
    with closing(load_workbook(path)) as wb:
        values=[str(cell.value or '') for cells in wb.active for cell in cells]
        assert any('01/01/1990' in value for value in values)
        assert not any('HIDDEN LOCATION' in value for value in values)

def test_explicit_personnel_column_choice_keeps_its_authorized_scope(tmp_path):
    from sme_gend.services.personnel_excel_export import export_liste_filtered
    path=tmp_path/'explicit.xlsx';export_liste_filtered([person(obs='CHOSEN')],path,hidden_keys={'obs'},column_keys=['noms','obs'])
    with closing(load_workbook(path)) as wb:
        assert [cell.value for cell in wb.active[2]]==['Noms et prénoms','Obs']
        assert wb.active['B3'].value=='CHOSEN'

@pytest.mark.parametrize('method',['_update_official_full','_update_official_sme_only'])
def test_official_workbook_update_requires_export_permission_before_path_access(monkeypatch,method):
    from sme_gend.ui.views.detaches_view import DetachesView
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:False)
    view=SimpleNamespace(_require_official_path=lambda:pytest.fail('path must not be opened'))
    getattr(DetachesView,method)(view)

def test_empty_structure_filter_never_falls_back_to_the_entire_catalog(app,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QTableWidget
    from sme_gend.ui.views.structure_hub_view import StructureHubView
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    monkeypatch.setattr('PySide6.QtWidgets.QMessageBox.information',lambda *a:None)
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',lambda *a,**kw:pytest.fail('empty export must not prompt for a file'))
    view=SimpleNamespace(_require_fresh=lambda:True,_structures=[SimpleNamespace(code='A')],_table=QTableWidget(0,7))
    StructureHubView._export_referentiel_excel(view)


@pytest.mark.parametrize('template',[False,True])
def test_present_excel_wraps_long_rows_without_losing_template_styles(tmp_path,template):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment,Font
    from sme_gend.services.excel_template_exporter import export_simple_fallback,export_liste_fidele
    from sme_gend.models.liste_layout import ListeLayoutConfig
    sections=[('TITLE','','SYN',[('UNIT',[person(noms='LONG NAME ' * 12,obs='Line one\nLine two')])])]
    path=tmp_path/'wrapped.xlsx'
    if template:
        book=Workbook();sheet=book.active;sheet.title='Liste'
        sheet.column_dimensions['C'].width=18
        sheet['C6'].font=Font(name='Arial',size=10,bold=True)
        sheet['C6'].alignment=Alignment(horizontal='center',vertical='top')
        sheet.row_dimensions[3].height=42
        book.save(tmp_path/'template.xlsx');book.close()
        export_liste_fidele(tmp_path/'template.xlsx',ListeLayoutConfig(),sections,path)
    else:export_simple_fallback(path,sections)
    with closing(load_workbook(path)) as wb:
        ws=wb.active;cell=next(c for row in ws for c in row if c.value=='LONG NAME ' * 12)
        assert cell.alignment.wrap_text
        assert ws.row_dimensions[cell.row].height>18
        if template:
            assert cell.font.name=='Arial' and cell.font.bold
            assert cell.alignment.horizontal=='center'
            assert ws.column_dimensions['C'].width==18
            assert ws.row_dimensions[3].height==42
        else:
            assert ws.page_setup.orientation=='landscape'
            assert ws.page_setup.fitToWidth==1 and ws.page_setup.fitToHeight==0
            assert ws.print_area and ws.print_title_rows=='$3:$3'


@pytest.mark.parametrize('module_name,class_name,method',[('edition_sme_view','EditionSmeView','_export_excel_filtered'),('edition_sme_view','EditionSmeView','_export_excel_by_padmin'),('personnel_list_view','PersonnelListView','_export_excel_filtered'),('personnel_list_view','PersonnelListView','_export_excel_by_padmin')])
def test_personnel_export_month_is_captured_before_the_file_dialog(app,tmp_path,monkeypatch,module_name,class_name,method):
    import importlib
    from unittest.mock import MagicMock
    ui=importlib.import_module('sme_gend.ui.views.'+module_name)
    settings=SimpleNamespace(mysql_view_mois='2026-09')
    reference=SimpleNamespace(grades=lambda:[],subdivisions_flat=lambda:[])
    view=SimpleNamespace(_settings=settings,_referentiel=reference,_current_filtered_rows=lambda:[person()],_effective_hidden_keys=lambda:set(),_extra_columns_shown=lambda:set(),_ensure_personnel_services=lambda:(None,reference))
    def context(settings):
        month=settings.mysql_view_mois
        return SimpleNamespace(export_mois=month,label_short=lambda:month)
    def dialog(*a,**kw):
        settings.mysql_view_mois='2026-10'
        return str(tmp_path/'month.xlsx'),''
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.build_export_mois_context',context)
    monkeypatch.setattr(ui,'build_export_mois_context',context,raising=False)
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',dialog)
    monkeypatch.setattr('PySide6.QtWidgets.QMessageBox.information',lambda *a:None)
    run=lambda parent,title,text,work:work()
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_progress',run)
    monkeypatch.setattr(ui,'run_with_progress',run,raising=False)
    seen=[]
    def exporter(*a,**kw):
        seen.append(kw)
        return {'Présent':1} if method.endswith('padmin') else 1
    target='export_liste_by_padmin' if method.endswith('padmin') else 'export_liste_filtered'
    monkeypatch.setattr('sme_gend.services.personnel_excel_export.'+target,exporter)
    getattr(getattr(ui,class_name),method)(view)
    assert seen[0]['log_mois']=='2026-09'
