"""Explicit links, filtered exports, freshness, vacancies and responsive workers."""
from contextlib import contextmanager,nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import threading,time
import pytest
from openpyxl import load_workbook
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QDialog,QListWidget,QPushButton
from sme_gend.config.settings import Settings
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.reference_collection_guard import CollectionSnapshot
from sme_gend.services import fonction_referentiel_harmonize as harmon
from sme_gend.ui import bg_task
from sme_gend.ui.views import fonction_referentiel_view as ui

REAL_PROGRESS=bg_task.run_with_progress


@pytest.fixture(autouse=True)
def app(monkeypatch):
    app=QApplication.instance() or QApplication([])
    for name in ('warning','critical','information'):monkeypatch.setattr(ui.QMessageBox,name,MagicMock())
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.evaluate_editing_state',lambda *a,**kw:NS(locked=False,detail=''))
    monkeypatch.setattr(ui,'evaluate_editing_state',lambda *a,**kw:NS(locked=False,detail=''))
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_progress',lambda parent,title,text,work,**kw:work())
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,text,work,**kw:work())
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_export_documents',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    yield app
    app.processEvents()


@pytest.fixture
def settings():return Settings(database_path=Path('synthetic-unused'),mysql_view_mois='2026-01')


@pytest.fixture
def service(settings):
    snapshot=CollectionSnapshot('links_all','','a'*64,((1,'FICTIF'),),
        ((1,'A','STRUCTURE FICTIVE A',1,None),(2,'B','STRUCTURE FICTIVE B',2,None)),((1,1,0),(2,1,0)))
    row=FonctionRefRow(1,'FICTIF',write_token=('fonction','1','b'*64))
    return NS(settings=settings,fonctions_all=lambda:[row],fonction_links_snapshot_all=lambda:snapshot,
        validate_links_snapshot=MagicMock(),fonction_links_snapshot=lambda fid:'LOCKED VERSION',
        fonction_update=MagicMock(),fonction_insert=MagicMock(return_value=2),fonction_set_structure_links=MagicMock())


@pytest.mark.parametrize('remaining',[[],['A']])
def test_manual_links_really_remove_unchecked_structures(service,monkeypatch,remaining):
    from sme_gend.ui import fonction_smart_dialog as smart
    monkeypatch.setattr(smart,'shared_reference_write',lambda settings:nullcontext())
    dialog=smart.FonctionSmartDialog(service,None,libelle='FICTIF',fonction_id=1,structure_code='A')
    assert all(dialog._list.item(i).checkState()==Qt.CheckState.Checked for i in range(2))
    for i in range(2):
        item=dialog._list.item(i);item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in remaining else Qt.CheckState.Unchecked)
    dialog._libelle.editingFinished.emit()
    dialog._structure_search.setText('ABSENT')
    dialog._structure_search.clear()
    assert dialog._selected_codes()==remaining
    assert not any('suggestion' in button.text().casefold() for button in dialog.findChildren(QPushButton))
    dialog._on_save()
    service.fonction_set_structure_links.assert_called_once_with(1,remaining,expected='LOCKED VERSION')
    assert dialog.result()==QDialog.DialogCode.Accepted
    dialog.close()


def test_new_function_does_not_preselect_the_context_structure(service,monkeypatch):
    from sme_gend.ui import fonction_smart_dialog as smart
    service.fonctions_all=lambda:[]
    dialog=smart.FonctionSmartDialog(service,None,libelle='',structure_code='A',subdivision='SECTION FICTIVE')
    assert dialog._libelle.text()=='' and dialog._selected_codes()==[]
    dialog._libelle.setText('NOUVEAU');dialog._libelle.editingFinished.emit()
    assert dialog._selected_codes()==[] and dialog._list.count()==2
    dialog.close()


def ready_catalogue(service):
    view=ui.FonctionsView(service)
    view._rows=[FonctionRefRow(1,'LIÉE'),FonctionRefRow(2,'ISOLÉE')]
    view._links={1:['B']};view._usage={1:1,2:1}
    view._observed_by_structure={'a':{'liée':('LIÉE',1),'isolée':('ISOLÉE',1)}}
    view._view_data.finish_load();view._apply_filter()
    return view


def test_orphan_filter_keeps_global_meaning_and_empty_message(service):
    view=ready_catalogue(service)
    view._filter_scope.setCurrentIndex(1)
    assert [r.id for r in view._filtered]==[2]
    view._filter_structure.addItem('A','A');view._filter_structure.setCurrentIndex(1)
    assert [r.id for r in view._filtered]==[2]
    view._search.setText('ABSENT')
    assert 'Aucun résultat' in view._empty.text() and 'Toutes les fonctions' not in view._empty.text()
    view.close()


def test_failed_catalogue_reload_disables_actions_and_stays_stale(service,monkeypatch):
    view=ready_catalogue(service);view._table.selectRow(0)
    monkeypatch.setattr(ui,'run_with_progress',lambda *a,**kw:None)
    view.reload();view._search.setText('LIÉE')
    assert not view._view_data.is_ready()
    assert 'non actualisées' in view._status.text()
    assert not view._btn_edit.isEnabled() and not view._btn_order.isEnabled() and not view._btn_harmon_export.isEnabled()
    view.close()


def test_context_change_during_catalogue_load_cannot_publish_results(service,monkeypatch):
    view=ready_catalogue(service);before=view._rows
    def changed(*a,**kw):
        service.settings=replace(service.settings,mysql_database='synthetic_other')
        return [],{},{},[],[]
    monkeypatch.setattr(ui,'run_with_progress',changed)
    view.reload();assert view._rows is before and not view._view_data.is_ready()
    view.close()


def plan_service(service):
    snapshot=replace(service.fonction_links_snapshot_all(),links=())
    service.fonction_links_snapshot_all=lambda:snapshot
    service.fonction_observed_usage=lambda:[('A','NOUVELLE FICTIVE',3),('B','FICTIF',1)]
    service.fonction_ensure_catalog_and_link=MagicMock()
    return service


def test_backfill_plan_discloses_creations_and_refuses_changed_people(service,monkeypatch):
    from sme_gend.services.fonction_backfill import prepare_backfill,apply_backfill
    service=plan_service(service);plan=prepare_backfill(service)
    assert [(r.structure,r.create) for r in plan.items]==[('A',True),('B',False)]
    monkeypatch.setattr('sme_gend.services.fonction_backfill.shared_reference_write',lambda settings:nullcontext())
    service.fonction_observed_usage=lambda:[('A','MODIFIÉE',1)]
    with pytest.raises(ValueError,match='fiches du mois ont changé'):apply_backfill(service,plan)
    service.fonction_ensure_catalog_and_link.assert_not_called()


def test_backfill_cancel_does_not_write(service,monkeypatch):
    service=plan_service(service);view=ready_catalogue(service)
    preview=MagicMock(return_value=False);monkeypatch.setattr(ui,'confirm_reference_plan',preview)
    view._on_backfill()
    assert 'à créer' in preview.call_args.args[4]
    service.fonction_ensure_catalog_and_link.assert_not_called();view.close()


def test_whole_backfill_plan_rolls_back_when_second_item_fails(service,monkeypatch):
    from sme_gend.services.fonction_backfill import prepare_backfill,apply_backfill
    service=plan_service(service);plan=prepare_backfill(service);state=[]
    @contextmanager
    def transaction(settings):
        try:yield
        except Exception:state.clear();raise
    monkeypatch.setattr('sme_gend.services.fonction_backfill.shared_reference_write',transaction)
    def apply(code,label):
        state.append(code)
        if code=='B':raise OSError('synthetic second failure')
    service.fonction_ensure_catalog_and_link=apply
    with pytest.raises(OSError):apply_backfill(service,plan)
    assert state==[]


def harmon_row(code='A',sub='SECTION FICTIVE'):
    return harmon.FonctionHarmonizeRow(code,f'STRUCTURE {code}',sub,'SECTION','CHEF FICTIF',True,True,'FICTIF','FICTIF','OK')


def test_harmon_export_is_literal_atomic_and_logged_once(settings,monkeypatch,tmp_path):
    seen=[];monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:seen.append(kw))
    path=tmp_path/'export.xlsx'
    assert harmon.export_harmonize_workbook(path,settings,rows=[harmon_row(sub='=1+1')])==1
    cell=load_workbook(path).active['C4']
    assert cell.value=='=1+1' and cell.data_type=='s'
    assert len(seen)==1 and seen[0]['doc_type']=='harmonisation_fonctions' and seen[0]['count']==1
    previous=path.read_bytes()
    monkeypatch.setattr(harmon.Workbook,'save',MagicMock(side_effect=OSError('synthetic disk full')))
    with pytest.raises(OSError):harmon.export_harmonize_workbook(path,settings,rows=[harmon_row()])
    assert path.read_bytes()==previous and len(seen)==1


def test_denied_export_does_not_read_or_write(settings,monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_export_documents',lambda:False)
    collect=MagicMock();monkeypatch.setattr(harmon,'collect_harmonize_rows',collect)
    with pytest.raises(PermissionError):harmon.export_harmonize_workbook(tmp_path/'forbidden.xlsx',settings)
    collect.assert_not_called();assert not (tmp_path/'forbidden.xlsx').exists()


def test_export_preview_respects_selected_structure(service,monkeypatch,tmp_path):
    view=ready_catalogue(service);view._filter_structure.addItem('A','A');view._filter_structure.setCurrentIndex(1)
    monkeypatch.setattr(harmon,'collect_harmonize_rows',lambda settings:[harmon_row('A'),harmon_row('B')])
    preview=MagicMock(return_value=True);monkeypatch.setattr(ui,'confirm_reference_plan',preview)
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',lambda *a:(str(tmp_path/'filtered.xlsx'),''))
    export=MagicMock(return_value=1);monkeypatch.setattr(harmon,'export_harmonize_workbook',export)
    view._on_harmon_export()
    assert [r.structure_code for r in export.call_args.kwargs['rows']]==['A']
    assert len(preview.call_args.args[3])==1
    view.close()


def test_structure_export_uses_the_same_inferred_type_as_screen(monkeypatch,tmp_path):
    from sme_gend.services.structure_referentiel_export import export_structures_referentiel
    from sme_gend.config.structure_famille import resolved_structure_famille,structure_famille_label
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    structure=StructureRow('DPF','DIRECTION DU PERSONNEL',1,None);path=tmp_path/'structures.xlsx'
    export_structures_referentiel(path,[structure])
    assert load_workbook(path).active['C4'].value==structure_famille_label(resolved_structure_famille(structure.code,structure.designation,structure.famille_unite))


def multiple_poste():
    from sme_gend.services.postes_vacants import lister_postes_tete
    people=[NS(id=str(i),noms='HOMONYME FICTIF',grade='CNE',matricule_militaire=f'FICTIF-{i}',matricule_solde='',
        structure='RG-X',fonction='COMREG',subdivision_arme='') for i in (1,2)]
    rows=lister_postes_tete([StructureRow('RG-X','REGION FICTIVE',1,'REGION')],[],people)
    return next(r for r in rows if r.poste=='COMREG')


def test_homonyms_are_distinct_holders_and_conflict_filter_works(service):
    poste=multiple_poste()
    assert poste.multiple_titulaires and {p.id for p in poste.titulaires_details}=={'1','2'}
    view=ui._PostesVacantsTab(service);view._all=[poste,replace(poste,titulaires=(),titulaires_details=())];view._stale=False
    view._conflicts_only.setChecked(True)
    assert not view._vacants_only.isChecked() and view._model.rowCount()==1
    assert 'fiche 1' in view._model.data(view._model.index(0,4),Qt.ItemDataRole.ToolTipRole)
    assert 'Plusieurs titulaires' in view._model.data(view._model.index(0,6))
    view._table.selectRow(0)
    assert not view._btn_assign.isEnabled() and view._btn_titulaires.isEnabled()
    view.close()


def test_failed_vacancy_load_blocks_all_actions(service,monkeypatch):
    view=ui._PostesVacantsTab(service);view._all=[multiple_poste()];view._stale=False
    view._vacants_only.setChecked(False);view._apply_filter();view._table.selectRow(0)
    monkeypatch.setattr(ui,'run_with_progress',lambda *a,**kw:None)
    view.reload();assert view._stale and 'non actualisées' in view._status.text()
    assert not view._btn_assign.isEnabled() and not view._btn_del.isEnabled() and not view._btn_titulaires.isEnabled()
    view.close()


def test_roster_opens_selected_identifier_using_existing_readonly_dossier(service,monkeypatch):
    view=ui._PostesVacantsTab(service);view._all=[multiple_poste()];view._stale=False
    view._vacants_only.setChecked(False);view._apply_filter();view._table.selectRow(0)
    row=object();personnel=NS(get_by_id=MagicMock(return_value=row))
    monkeypatch.setattr('sme_gend.services.personnel_service.PersonnelService',lambda settings:personnel)
    opened=MagicMock();monkeypatch.setattr('sme_gend.ui.personnel_dossier_open.open_personnel_dossier_dialog',opened)
    def execute(dialog):
        roster=dialog.findChild(QListWidget);roster.setCurrentRow(1)
        button=next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('Ouvrir le dossier'))
        button.click();return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',execute)
    view._on_titulaires();personnel.get_by_id.assert_called_once_with('2')
    assert opened.call_args.args[1] is row and opened.call_args.kwargs['allow_edit'] is False
    view.close()


def test_assignment_read_failure_is_visible_and_marks_posts_stale(service,monkeypatch):
    poste=multiple_poste();poste=replace(poste,titulaires=(),titulaires_details=())
    view=ui._PostesVacantsTab(service);view._all=[poste];view._stale=False
    view._apply_filter();view._table.selectRow(0)
    monkeypatch.setattr('sme_gend.ui.fonction_assign_agent_dialog.open_assign_poste_dialog',MagicMock(side_effect=OSError('synthetic read failure')))
    view._on_assign_agent()
    assert view._stale and not view._btn_assign.isEnabled()
    ui.QMessageBox.critical.assert_called_once()
    view.close()


def test_reference_task_runs_off_ui_thread_and_preserves_exception(app,monkeypatch):
    from sme_gend.ui.reference_task import run_reference_task
    monkeypatch.setattr(bg_task,'run_with_progress',REAL_PROGRESS)
    main=threading.get_ident();seen=[];ticks=[]
    timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    def work():seen.append(threading.get_ident());time.sleep(.08);return 7
    try:
        assert run_reference_task(None,'TEST',work)==7
        assert seen[0]!=main and ticks
        with pytest.raises(ValueError,match='FICTIF'):
            run_reference_task(None,'TEST',lambda:(_ for _ in ()).throw(ValueError('FICTIF')))
    finally:timer.stop()


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('width',[640,1000])
def test_function_tabs_fit_small_windows(app,service,tmp_path,theme,width):
    from sme_gend.ui.theme import apply_app_theme
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf');apply_app_theme(app,theme)
    view=ready_catalogue(service);view._vacants._stale=False
    view.resize(width,700);view.show();app.processEvents()
    try:
        for index in (0,1):
            view._tabs.setCurrentIndex(index);app.processEvents()
            assert view.width()==width,view.minimumSizeHint()
            view.grab().save(str(tmp_path/f'fonctions-{theme}-{width}-{index}.png'))
    finally:view.close();view.deleteLater();app.processEvents();apply_app_theme(app,'light')
