"""Selected audit corrections, with fictitious state and no real database."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from sme_gend.config.settings import Settings
from sme_gend.models.structure import StructureRow
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode
from sme_gend.models.organigram import StructureOrganigramNode, SubdivisionOrganigramNode
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_organigram_service import StructureOrganigramService
from sme_gend.ui.views import structure_hub_view as hub, subdivisions_hierarchy_view as hi
from sme_gend.ui.views import fonction_referentiel_view as fn


@pytest.fixture(autouse=True)
def ui(monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema',lambda *a:None)
    for name in ('critical','warning','information'):
        monkeypatch.setattr(QMessageBox,name,MagicMock())
    for module in (hub,hi,fn):
        monkeypatch.setattr(module,'evaluate_editing_state',lambda *a,**kw:NS(locked=False,detail=''))
    yield app
    app.processEvents()


@pytest.fixture
def settings():return Settings(database_path=Path('synthetic-unused'),mysql_view_mois='2026-01')


@pytest.fixture
def service(settings):
    return NS(settings=settings,structures=lambda:[StructureRow('A','FICTIF A',1),StructureRow('B','FICTIF B',2)],
              fonction_counts_by_structure=lambda:{'a':2,'b':10},fonction_linked_choices_for_structure=lambda code:[],
              fonction_linked_labels_by_structure=lambda:{'a':['FICTIF 1','FICTIF 2'],'b':[str(i) for i in range(10)]})


@pytest.fixture
def view(service,monkeypatch):
    view=hub.StructureHubView(service)
    monkeypatch.setattr(view._org,'fetch_tree',lambda:[])
    view.reload(force=True)
    yield view
    view.close()


def test_failed_reload_preserves_whole_snapshot_and_blocks_actions(view,service,monkeypatch):
    original=view._structures
    service.structures=lambda:[StructureRow('NEW','NEW',1)]
    monkeypatch.setattr(view._org,'fetch_tree',MagicMock(side_effect=OSError('synthetic failure')))
    view.reload(force=True)
    assert view._structures is original
    assert {view._table.item(i,1).text() for i in range(2)}=={'A','B'}
    assert not view._view_data.is_ready() and not view._btn_add.isEnabled() and not view._btn_export.isEnabled()
    assert 'impossible' in view._status.text().casefold()


def test_context_rebind_clears_old_selection_and_uses_new_period(view,service):
    view._table.item(0,0).setCheckState(Qt.CheckState.Checked)
    service.settings=replace(service.settings,mysql_view_mois='2026-02',mysql_database='synthetic-other')
    view.reload(force=True)
    assert view._org.settings==service.settings and not view._checked_structure_codes()
    assert view._view_data.is_ready()


def test_check_selection_survives_filters_and_refresh_but_prunes_removed(view,service):
    view._table.item(0,0).setCheckState(Qt.CheckState.Checked)
    code=view._checked_structure_codes()[0]
    view._search.setText('NOT MATCHED');assert view._checked_structure_codes()==[code]
    view._search.clear();assert view._checked_structure_codes()==[code]
    view.reload(force=True);assert view._checked_structure_codes()==[code]
    service.structures=lambda:[r for r in view._structures if r.code!=code]
    view.reload(force=True);assert not view._checked_structure_codes()


def test_numeric_sort_and_function_read_error_are_explicit(view,service):
    view._table.sortItems(5,Qt.SortOrder.AscendingOrder)
    assert [view._table.item(i,5).text() for i in range(2)]==['2','10']
    service.fonction_linked_labels_by_structure=MagicMock(side_effect=OSError('failure'))
    view.reload(force=True)
    view._update_detail()
    assert 'impossible' in view._fonct_list.item(0).text()
    assert 'Aucune' not in view._fonct_list.item(0).text()


def test_strict_structure_counts_exclude_observed_fallback(settings):
    service=ReferentielService.__new__(ReferentielService)
    service._fonctions=NS(links_summary=lambda:{1:['A','A'],2:['A','B']},fetch_choice_counts_by_structure=lambda:pytest.fail('Observed fallback'))
    assert service.fonction_counts_by_structure()=={'a':2,'b':1}


@pytest.mark.parametrize('bad',['abc','1.5','2147483648','-2147483649'])
def test_invalid_structure_order_keeps_dialog_open(bad):
    from sme_gend.ui.structure_edit_dialog import StructureEditDialog
    dialog=StructureEditDialog(None);dialog._code.setText('FICTIF');dialog._designation.setText('FICTIF');dialog._ordre.setText(bad)
    dialog.accept();assert dialog.result()!=QDialog.DialogCode.Accepted
    QMessageBox.warning.assert_called_once();dialog.close()


def test_parent_filter_keeps_descendants_and_restores_branch_state(service):
    view=hi.SubdivisionsHierarchyView(service)
    leaf=SubdivHierarchyNode(4,3,'T','ENFANT',None,3)
    middle=SubdivHierarchyNode(3,2,'D','MILIEU',None,2,children=[leaf])
    view._roots=[SubdivHierarchyNode(2,1,'U','PARENT',None,1,children=[middle])]
    view._rebuild_tree();view._tree.topLevelItem(0).setExpanded(False)
    view._search.setText('PARENT')
    assert view._tree.topLevelItem(0).child(0).childCount()==1
    view._search.clear();assert not view._tree.topLevelItem(0).isExpanded()
    view.close()


def test_generic_function_has_manual_structure_choices_and_no_automatic_link(service):
    from sme_gend.services.fonction_smart_link import build_structure_suggestions
    service.fonctions_all=lambda:[];service.subdivisions_arme_for_structure=lambda code:[]
    _,choices=build_structure_suggestions(service,'ASSISTANT FICTIF',structures=service.structures())
    assert {c.code for c in choices}=={'A','B'} and not any(c.checked for c in choices)


def test_new_function_preserves_the_selected_structure_filter(service,monkeypatch):
    view=fn.FonctionsView(service);view._filter_structure.addItem('A','A');view._filter_structure.setCurrentIndex(1)
    view._view_data.finish_load()
    capture=MagicMock(return_value=None)
    monkeypatch.setattr('sme_gend.ui.fonction_smart_dialog.open_fonction_smart_dialog',capture)
    view._on_add();assert capture.call_args.kwargs['structure_code']=='A'
    view.close()


def test_harmonization_does_not_report_identical_labels(settings,monkeypatch):
    from sme_gend.services import fonction_referentiel_harmonize as h
    label='DIRECTEUR DPF'
    svc=NS(fonctions_all=lambda:[FonctionRefRow(1,label)],fonction_links_summary=lambda:{1:['DPF']},subdivision_arme_choices_for_structure=lambda code:[])
    monkeypatch.setattr(h,'ReferentielService',lambda settings:svc)
    monkeypatch.setattr(h,'StructureRepository',lambda settings:NS(fetch_all=lambda:[StructureRow('DPF','DIRECTION FICTIVE',1,'DIRECTION')]))
    monkeypatch.setattr(h,'_observed_by_scope',lambda settings:{('dpf',''):{label}})
    monkeypatch.setattr(h,'_titulaires_map',lambda settings:{('dpf','',label):['FICTIF']})
    assert h.collect_harmonize_rows(settings)[0].statut=='OK'


def sql_repo(cls,settings,responses):
    repo=cls.__new__(cls);repo._settings=settings
    cur=MagicMock();cur.__enter__.return_value=cur;cur.fetchall.side_effect=responses
    conn=MagicMock();conn.__enter__.return_value=conn;conn.cursor.return_value=cur
    repo._connect=lambda **kwargs:conn
    return repo,cur


def test_function_counts_and_observations_use_selected_month(settings,monkeypatch):
    from sme_gend.repositories import mysql_fonction_structure_repository as links, mysql_bundle_repositories as catalog
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.carriere_scope_for_settings',lambda settings:('c.sme_periode_id = %s',(7,)))
    monkeypatch.setattr(links,'carriere_scope_for_settings',lambda settings:('c.sme_periode_id = %s',(7,)))
    repo,cur=sql_repo(links.MySQLFonctionStructureRepository,settings,[[('A','FICTIF',1)]])
    assert repo.fetch_observed_usage()==[('A','FICTIF',1)]
    assert cur.execute.call_args.args[1]==(7,) and 'sme_periode_id' in cur.execute.call_args.args[0]
    repo,cur=sql_repo(catalog.MySQLFonctionsRepository,settings,[[(1,'FICTIF')],[(1,2)],[]])
    assert repo.fetch_usage_counts()=={1:2}
    assert all(call.args[1]==(7,) for call in cur.execute.call_args_list[1:])


def test_backfill_uses_consulted_month(settings,monkeypatch):
    from sme_gend.repositories import mysql_fonction_structure_repository as links
    monkeypatch.setattr(links,'carriere_write_scope_for_settings',lambda settings:('c.sme_periode_id = %s',(7,)))
    monkeypatch.setattr(links,'shared_reference_write',lambda settings:nullcontext())
    repo,cur=sql_repo(links.MySQLFonctionStructureRepository,settings,[[]])
    assert repo.backfill_from_carriere()==0
    assert 'c.sme_periode_id' in cur.execute.call_args.args[0] and cur.execute.call_args.args[1]==(7,)


def test_rename_confirmation_discloses_all_periods_and_both_impact_counts(monkeypatch):
    from sme_gend.ui import fonction_rename_confirmation as confirmation
    ask=MagicMock(return_value=True);monkeypatch.setattr(confirmation,'confirm_yes_no',ask)
    assert confirmation.confirm_global_rename(None,NS(fonction_rename_impact=lambda row:(25,8)),FonctionRefRow(1,'OLD'),'NEW')==(25,8)
    text=ask.call_args.args[2]
    assert '25' in text and '8' in text and 'toutes périodes' in text and 'historiques' in text


def test_confirmed_rename_rechecks_impact_before_writing(settings,monkeypatch):
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_reference_write',lambda settings:nullcontext())
    svc=ReferentielService.__new__(ReferentielService);svc._settings=settings
    svc._fonctions=NS(rename_impact=lambda *a:(3,2));svc.fonction_update=MagicMock()
    with pytest.raises(ValueError,match='périmètre'):svc.fonction_rename_confirmed(FonctionRefRow(1,'OLD'),'NEW',(2,2))
    svc.fonction_update.assert_not_called()
    svc.fonction_rename_confirmed(FonctionRefRow(1,'OLD'),'NEW',(3,2));svc.fonction_update.assert_called_once()


def test_catalogue_function_used_only_by_affectation_cannot_be_deleted(settings,monkeypatch):
    from sme_gend.repositories import mysql_bundle_repositories as catalog
    monkeypatch.setattr(catalog,'shared_reference_write',lambda settings:nullcontext())
    monkeypatch.setattr(catalog,'check_reference_target',lambda *a:None)
    repo,cur=sql_repo(catalog.MySQLFonctionsRepository,settings,[])
    cur.fetchone.side_effect=[('FICTIF',),(0,),(3,)]
    row=FonctionRefRow(1,'FICTIF',write_token=('fonction','1','a'*64))
    with pytest.raises(ValueError,match='Impossible de supprimer'):repo.delete(1,expected=row)
    assert not any('DELETE FROM' in call.args[0] for call in cur.execute.call_args_list)


def test_removing_expected_post_never_deletes_shared_catalogue():
    owner=NS(_dismiss_expected_poste=MagicMock(),_find_fonction=MagicMock(side_effect=AssertionError('No global lookup')))
    poste=NS(poste='CHEF FICTIF',structure_code='A',subdivision='UNITÉ')
    fn.FonctionsView.delete_or_dismiss_poste(owner,poste)
    owner._dismiss_expected_poste.assert_called_once_with(poste,'CHEF FICTIF')


@pytest.mark.parametrize('failure',['link','personnel'])
def test_assignment_rolls_back_catalogue_if_later_step_fails(settings,monkeypatch,failure):
    from sme_gend.ui.fonction_assign_agent_dialog import assign_poste_atomically
    state=[]
    @contextmanager
    def transaction(settings):
        old=list(state)
        try:yield
        except Exception:state[:]=old;raise
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_reference_write',transaction)
    def ensure(*a):
        state.append('catalogue')
        if failure=='link':raise OSError('synthetic link failure')
    svc=NS(settings=settings,fonction_ensure_catalog_and_link=ensure)
    personnel=NS(update_personnel=MagicMock(side_effect=OSError('synthetic personnel failure')))
    with pytest.raises(OSError):assign_poste_atomically(svc,personnel,object(),NS(structure='A',fonction='FICTIF'))
    assert state==[]


def test_catalogue_and_link_creation_share_a_single_rollback_boundary(settings,monkeypatch):
    state=[]
    @contextmanager
    def transaction(settings):
        old=list(state)
        try:yield
        except Exception:state[:]=old;raise
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_reference_write',transaction)
    svc=ReferentielService.__new__(ReferentielService);svc._settings=settings
    svc._fonctions=NS(fetch_all=lambda:[],insert=lambda lib:state.append(lib) or 1,
        ensure_structure_link=MagicMock(side_effect=OSError('synthetic link failure')))
    with pytest.raises(OSError):svc.fonction_ensure_catalog_and_link('A','FICTIF')
    assert state==[]


@pytest.fixture
def subdivision_scope(settings,monkeypatch):
    nodes=((1,None,'ROOT','Racine',None),(2,1,'U','U',None),(3,2,'D','D',None),(4,3,'T','FEUILLE FICTIVE',None))
    rows=((10,1,4,'SUBDIVISION FICTIVE',1,1),)
    snapshot=NS(parts=(nodes,rows))
    cursor=MagicMock();cursor.fetchone.side_effect=[('A',),(0,),(0,)]
    cursor.rowcount=1
    @contextmanager
    def maintenance(*a,**kw):
        assert a[1] is snapshot and a[2]=='hierarchy'
        yield cursor,snapshot
    monkeypatch.setattr('sme_gend.repositories.hierarchy_merge_guard.maintenance_write',maintenance)
    monkeypatch.setattr('sme_gend.services.shared_reference_write.mark_reference_write',lambda *a:None)
    monkeypatch.setattr('sme_gend.services.business_audit.record_business_action',MagicMock())
    svc=StructureOrganigramService.__new__(StructureOrganigramService);svc._settings=settings
    svc.add_subdivision=MagicMock(return_value=11);svc.rename_subdivision=MagicMock();svc.delete_subdivision=MagicMock()
    return svc,cursor,snapshot


@pytest.mark.parametrize('action',['create','rename','delete'])
def test_subdivision_actions_validate_structure_and_use_versioned_intent(subdivision_scope,action):
    svc,cursor,snapshot=subdivision_scope
    svc.change_subdivision(action,1,'A',label='NOUVEAU FICTIF',rsa_id=None if action=='create' else 10,leaf_id=4,expected=snapshot)
    method={'create':svc.add_subdivision,'rename':svc.rename_subdivision,'delete':svc.delete_subdivision}[action]
    method.assert_called_once()


def test_used_historical_subdivision_cannot_be_deleted(subdivision_scope):
    svc,cursor,snapshot=subdivision_scope;cursor.fetchone.side_effect=[('A',),(3,),(1,)]
    with pytest.raises(ValueError,match='toutes périodes'):
        svc.change_subdivision('delete',1,'A',rsa_id=10,expected=snapshot)
    svc.delete_subdivision.assert_not_called()


def test_subdivision_create_requires_a_real_leaf(subdivision_scope):
    svc,cursor,snapshot=subdivision_scope
    with pytest.raises(ValueError,match='feuille T'):
        svc.change_subdivision('create',1,'A',label='FICTIF',leaf_id=2,expected=snapshot)
    svc.add_subdivision.assert_not_called()


def test_subdivision_dialog_distinguishes_official_and_observed(service,monkeypatch):
    from sme_gend.ui.structure_subdivisions_dialog import StructureSubdivisionsDialog
    node=StructureOrganigramNode(1,'A','FICTIF',1,subdivisions=[
        SubdivisionOrganigramNode(10,'OFFICIELLE',1,2),SubdivisionOrganigramNode(None,'OBSERVÉE',None,1,False)])
    org=NS(settings=service.settings,subdivision_editor_snapshot=lambda:object(),fetch_tree=lambda:[node])
    monkeypatch.setattr('sme_gend.ui.structure_subdivisions_dialog.StructureOrganigramService',lambda settings:org)
    dialog=StructureSubdivisionsDialog(None,service,service.structures()[0])
    assert dialog._list.count()==2
    assert 'référentiel' in dialog._list.item(0).text() and 'Donnée seule' in dialog._list.item(1).text()
    dialog.close()


def test_generic_function_can_be_saved_through_the_actual_dialog(settings,monkeypatch):
    from sme_gend.ui import fonction_smart_dialog as smart
    svc=NS(settings=settings,fonctions_all=lambda:[],fonction_links_snapshot_all=lambda:object(),
        subdivisions_arme_for_structure=lambda code:[],validate_links_snapshot=MagicMock(),
        fonction_insert=MagicMock(return_value=99),fonction_set_structure_links=MagicMock(),fonction_links_snapshot=lambda fid:'FRESH LOCKED SNAPSHOT')
    monkeypatch.setattr('sme_gend.repositories.structure_repository.StructureRepository',lambda settings:NS(fetch_all=lambda:[StructureRow('A','FICTIF',1)]))
    monkeypatch.setattr(smart,'shared_reference_write',lambda settings:nullcontext())
    dialog=smart.FonctionSmartDialog(svc,None,libelle='ASSISTANT FICTIF')
    assert dialog._list.count()==1 and dialog._list.item(0).checkState()==Qt.CheckState.Unchecked
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked);dialog._on_save()
    assert dialog.result()==QDialog.DialogCode.Accepted
    svc.fonction_set_structure_links.assert_called_once_with(99,['A'],expected='FRESH LOCKED SNAPSHOT')
    dialog.close()


def test_subdivision_editor_refuses_missing_permission_before_sql(settings,monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    svc=StructureOrganigramService.__new__(StructureOrganigramService);svc._settings=settings
    with pytest.raises(PermissionError):svc.change_subdivision('create',1,'A',label='FICTIF',leaf_id=4,expected=None)


def test_nomenclature_rename_requires_explicit_impact_confirmation(service,monkeypatch):
    view=hi.SubdivisionsHierarchyView(service)
    node=SubdivHierarchyNode(4,3,'T','ANCIEN FICTIF',None,3,rsa_links=2)
    view._roots=[node];view._snapshot=object()
    monkeypatch.setattr(view,'_check_editable',lambda:True)
    monkeypatch.setattr(view,'_selected_node',lambda:(4,3))
    monkeypatch.setattr(hi,'_NodeDialog',lambda *a,**kw:NS(exec=lambda:QDialog.DialogCode.Accepted,values=lambda:('T','NOUVEAU FICTIF',None)))
    asked=MagicMock(return_value=False);monkeypatch.setattr(hi,'confirm_yes_no',asked)
    view._svc.update_node=MagicMock()
    view._on_edit();view._svc.update_node.assert_not_called()
    assert 'historiques restent inchangés' in asked.call_args.args[2]
    assert '2 affiliation' in asked.call_args.args[2]
    view.close()


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('width',[640,1000])
def test_structure_and_nomenclature_fit_compact_windows(ui,service,monkeypatch,tmp_path,theme,width):
    from PySide6.QtGui import QFontDatabase
    from sme_gend.ui.theme import apply_app_theme
    from sme_gend.ui.views.referentiel_views import StructuresView
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    apply_app_theme(ui,theme)
    root=StructuresView(service)
    monkeypatch.setattr(root._hub_panel._org,'fetch_tree',lambda:[StructureOrganigramNode(1,'A','FICTIF A',1,2)])
    root.resize(width,700);root.show();ui.processEvents()
    tree=hi.SubdivisionsHierarchyView(service)
    tree._roots=[SubdivHierarchyNode(2,1,'U','NOMENCLATURE FICTIVE',None,1)]
    tree._rebuild_tree();tree.resize(width,700);tree.show();ui.processEvents()
    try:
        assert root.width()==width, root.minimumSizeHint()
        assert tree.width()==width, tree.minimumSizeHint()
        assert root._hub_panel._table.columnWidth(2)>=200
        root.grab().save(str(tmp_path/f'structures-{theme}-{width}.png'))
        tree.grab().save(str(tmp_path/f'nomenclature-{theme}-{width}.png'))
    finally:
        root.close();tree.close();root.deleteLater();tree.deleteLater();ui.processEvents();apply_app_theme(ui,'light')
