"""Required catalogue snapshots and UI drafts must survive concurrent changes."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import importlib
import pytest
from PySide6.QtWidgets import QApplication,QDialog,QWidget,QLineEdit
from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.repositories.reference_write_guard import ReferenceConflictError

def row(kind):
    if kind=='grade':return GradeRefRow('G','OLD',1,2,write_token=('grade','G','a'*64))
    if kind=='structure':return StructureRow('S','OLD',1,'test',write_token=('structure','S','a'*64))
    return FonctionRefRow(1,'OLD',write_token=('fonction','1','a'*64))

@pytest.fixture
def app():
    application=QApplication.instance() or QApplication([])
    yield application
    application.processEvents()

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
@pytest.mark.parametrize('operation',['update','delete'])
@pytest.mark.parametrize('invalid',['missing','wrong_namespace'])
def test_catalogue_snapshot_is_required_before_any_transaction(monkeypatch,kind,operation,invalid):
    from sme_gend.repositories import mysql_bundle_repositories as module
    cls={'grade':module.MySQLGradeRepository,'structure':module.MySQLStructureRepository,'fonction':module.MySQLFonctionsRepository}[kind]
    repo=cls.__new__(cls);original=row(kind)
    expected=None if invalid=='missing' else replace(original,write_token=('other',original.write_token[1],'a'*64))
    def forbidden(*a,**kw):pytest.fail('Invalid snapshot must not open SQL transaction')
    monkeypatch.setattr(module,'shared_reference_write',forbidden)
    with pytest.raises(ValueError,match='Version du référentiel indisponible'):
        if operation=='delete':repo.delete(getattr(original,'code',getattr(original,'id',None)),expected=expected)
        elif kind=='grade':repo.update('G','NEW',2,3,expected=expected)
        elif kind=='structure':repo.update('S','NEW',2,expected=expected)
        else:repo.update(1,'OLD','NEW',expected=expected)

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_historical_dtos_remain_readable_and_snapshot_is_not_business_equality(kind):
    original=row(kind)
    raw={'grade':('G','OLD',1,2),'structure':('S','OLD',1,'test'),'fonction':(1,'OLD')}[kind]
    historical=type(original).from_sequence(raw)
    assert historical.write_token is None and historical==original
    assert 'a'*64 not in repr(original)

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
@pytest.mark.parametrize('operation',['update','delete'])
def test_public_repository_forwards_expected_snapshot(kind,operation):
    from sme_gend.repositories.grade_repository import GradeRepository
    from sme_gend.repositories.structure_repository import StructureRepository
    from sme_gend.repositories.fonctions_repository import FonctionsRepository
    cls={'grade':GradeRepository,'structure':StructureRepository,'fonction':FonctionsRepository}[kind]
    repo=cls.__new__(cls);repo._impl=MagicMock();expected=row(kind)
    if operation=='delete':repo.delete(getattr(expected,'code',getattr(expected,'id',None)),expected=expected)
    elif kind=='grade':repo.update('G','NEW',2,3,expected=expected)
    elif kind=='structure':repo.update('S','NEW',2,expected=expected)
    else:repo.update(1,'OLD','NEW',expected=expected)
    assert getattr(repo._impl,operation).call_args.kwargs['expected'] is expected

def test_function_rename_rejects_a_label_that_does_not_match_its_snapshot(monkeypatch):
    from sme_gend.repositories import mysql_bundle_repositories as module
    repo=module.MySQLFonctionsRepository.__new__(module.MySQLFonctionsRepository)
    monkeypatch.setattr(module,'shared_reference_write',lambda *a,**kw:pytest.fail('Unexpected transaction'))
    with pytest.raises(ValueError,match='Ancien libellé incohérent'):repo.update(1,'WRONG','NEW',expected=row('fonction'))

def frame(kind,monkeypatch):
    name,cls,dialog_name,selection,edit,delete={
        'grade':('grade_referentiel_view','GradesView','GradeEditDialog','_selected_grade','_on_edit','_on_delete'),
        'structure':('structure_hub_view','StructureHubView','StructureEditDialog','_selected_structure','_edit_structure_identity','_on_delete_structure'),
        'fonction':('fonction_referentiel_view','FonctionsView','FonctionEditDialog','_selected_row','_on_edit','_delete_fonction_row'),
    }[kind]
    module=importlib.import_module('sme_gend.ui.views.'+name)
    view=getattr(module,cls).__new__(getattr(module,cls));QWidget.__init__(view)
    original=row(kind);view._service=MagicMock();view._service.settings=Settings(database_path=Path('unused.mdb'))
    setattr(view,selection,MagicMock(return_value=original));view._usage={};view.reload=MagicMock()
    view._require_unlocked=lambda:True;view._grade_usage=lambda code:0
    if kind=='structure':view._require_fresh=lambda:True
    if kind=='fonction':
        monkeypatch.setattr('sme_gend.ui.fonction_rename_confirmation.confirm_global_rename',lambda *a:(0,0))
        view._service.fonction_rename_confirmed.side_effect=lambda old,new,impact:view._service.fonction_update(old.id,old.libelle,new,expected=old)
    view._service.fonction_count_usage.return_value=0
    view._vacants=SimpleNamespace(mark_stale=MagicMock())
    monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
    if hasattr(module,'evaluate_editing_state'):monkeypatch.setattr(module,'evaluate_editing_state',lambda *a:SimpleNamespace(locked=False))
    conflict = MagicMock()
    view._test_conflict = conflict
    if kind == 'grade':
        from sme_gend.ui import reference_tasks
        monkeypatch.setattr(reference_tasks, 'show_reference_conflict', conflict)
        monkeypatch.setattr(module, 'session_has_permission', lambda *a: True)
        view._media = {}
    else:
        monkeypatch.setattr(module,'show_reference_conflict',conflict)
    monkeypatch.setattr(module.QMessageBox,'warning',MagicMock());monkeypatch.setattr(module.QMessageBox,'critical',MagicMock())
    dialog=MagicMock();dialog.exec.side_effect=[QDialog.DialogCode.Accepted,QDialog.DialogCode.Rejected]
    dialog.values.return_value=('G','MY DRAFT',1,2) if kind=='grade' else ('S','MY DRAFT',1,'test')
    dialog.libelle.return_value='MY DRAFT'
    factory=MagicMock(return_value=dialog);monkeypatch.setattr(module,dialog_name,factory)
    return view,module,dialog,factory,original,edit,delete

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_conflicted_editor_reuses_the_same_draft_and_original_version(app,monkeypatch,kind):
    view,module,dialog,factory,original,edit,delete=frame(kind,monkeypatch)
    dialog.exec.side_effect=[QDialog.DialogCode.Accepted,QDialog.DialogCode.Accepted,QDialog.DialogCode.Rejected]
    method=getattr(view._service, 'save_grade' if kind == 'grade' else kind+'_update');method.side_effect=ReferenceConflictError('CONFLICT')
    getattr(view,edit)()
    assert factory.call_count==1 and dialog.exec.call_count==3 and method.call_count==2
    assert all(call.kwargs['expected'] is original for call in method.call_args_list)
    assert view._test_conflict.call_count==2
    view.reload.assert_not_called();view.deleteLater()

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
@pytest.mark.parametrize('blocked',['cancel','locked'])
def test_cancel_or_denied_editing_state_still_prevents_writes(app,monkeypatch,kind,blocked):
    view,module,dialog,factory,original,edit,delete=frame(kind,monkeypatch)
    if blocked=='locked':
        if kind == 'grade':
            # Grades are global; a closed month is no longer a write policy.
            # An unauthorized role must still be blocked before opening a form.
            monkeypatch.setattr(module, 'session_has_permission', lambda *a: False)
        view._require_unlocked=lambda:False
        if hasattr(module,'evaluate_editing_state'):monkeypatch.setattr(module,'evaluate_editing_state',lambda *a:SimpleNamespace(locked=True,detail='LOCKED'))
    else:dialog.exec.side_effect=[QDialog.DialogCode.Rejected]
    getattr(view,edit)()
    getattr(view._service, 'save_grade' if kind == 'grade' else kind+'_update').assert_not_called()
    if blocked=='locked':factory.assert_not_called()
    view.deleteLater()

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_conflicted_deletion_sends_the_displayed_version_and_refreshes(app,monkeypatch,kind):
    view,module,dialog,factory,original,edit,delete=frame(kind,monkeypatch)
    if kind=='structure':
        from sme_gend.repositories import mysql_organigram_repository as org
        monkeypatch.setattr(org,'MySQLOrganigramRepository',lambda settings:SimpleNamespace(count_structure_usage=lambda code:SimpleNamespace(total=0)))
    method=getattr(view._service,kind+'_delete');method.side_effect=ReferenceConflictError('CONFLICT')
    getattr(view,delete)(original) if kind=='fonction' else getattr(view,delete)()
    assert method.call_args.kwargs['expected'] is original
    view._test_conflict.assert_called_once();view.reload.assert_called_once();view.deleteLater()

def test_inline_structure_editor_keeps_fields_and_does_not_rebase_on_conflict(app,monkeypatch):
    from sme_gend.ui import structure_subdivisions_dialog as module
    dialog=module.StructureSubdivisionsDialog.__new__(module.StructureSubdivisionsDialog);QDialog.__init__(dialog)
    dialog._structure=row('structure');dialog._service=MagicMock();dialog._allow_edit=True
    dialog._edit_designation=QLineEdit('MY DRAFT');dialog._edit_ordre=QLineEdit('42')
    dialog._service.structure_update.side_effect=ReferenceConflictError('CONFLICT')
    monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True);monkeypatch.setattr(module,'show_reference_conflict',MagicMock())
    dialog._save_structure_changes()
    assert dialog._edit_designation.text()=='MY DRAFT' and dialog._edit_ordre.text()=='42'
    assert dialog._service.structure_update.call_args.kwargs['expected'] is dialog._structure
    dialog._service.structures.assert_not_called();dialog.deleteLater()

@pytest.mark.parametrize('mode',['by_id','known_label','missing_original'])
def test_smart_function_dialog_does_not_refresh_away_the_initial_version(app,monkeypatch,mode):
    from sme_gend.ui import fonction_smart_dialog as module
    service=MagicMock();old=row('fonction');service.fonctions_all.return_value=[] if mode=='missing_original' else [old]
    dialog=module.FonctionSmartDialog(service,None,libelle='OLD',fonction_id=None if mode=='known_label' else 1)
    dialog._rename_impact=(0,0)
    service.fonction_rename_confirmed.side_effect=lambda old,new,impact:service.fonction_update(old.id,old.libelle,new,expected=old)
    service.fonctions_all.return_value=[replace(old,libelle='CONCURRENT',write_token=('fonction','1','b'*64))]
    if mode=='missing_original':
        with pytest.raises(ValueError,match='indisponible'):dialog._resolve_fonction_id('NEW')
        service.fonction_insert.assert_not_called()
    else:
        assert dialog._resolve_fonction_id('OLD' if mode=='known_label' else 'NEW')==1
        assert service.fonction_update.call_args.kwargs['expected'] is old
    assert service.fonctions_all.call_count==1;dialog.deleteLater()

def test_bulk_structure_editor_captures_versions_before_the_confirmation(app,monkeypatch):
    view,module,dialog,factory,original,edit,delete=frame('structure',monkeypatch)
    view._structures=[original];view._checked_structure_codes=lambda:['S']
    def accepted():view._structures=[replace(original,write_token=('structure','S','b'*64))];return QDialog.DialogCode.Accepted
    dialog.exec.side_effect=accepted;dialog.famille_code.return_value='test'
    monkeypatch.setattr(module,'StructureBulkFamilleDialog',factory);monkeypatch.setattr(module.QMessageBox,'information',MagicMock())
    view._service.structure_bulk_set_famille.return_value=1
    view._bulk_set_famille()
    assert view._service.structure_bulk_set_famille.call_args.kwargs['expected']==[original]
    assert view._service.structure_bulk_set_famille.call_args.kwargs['expected'][0] is original
    view.deleteLater()

def test_layout_editor_keeps_its_draft_and_configuration_on_conflict(app,monkeypatch):
    from sme_gend.ui.views import layout_editor_view as module
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    view=module.LayoutEditorView.__new__(module.LayoutEditorView);QWidget.__init__(view)
    original=row('structure');view._cfg=ListeLayoutConfig(sections=[SectionBlock('one',0,'S')])
    view._current_section=lambda:view._cfg.sections[0];view._svc=MagicMock();view._svc.structure_row.return_value=original
    view._svc.edit_structure_referentiel.side_effect=ReferenceConflictError('CONFLICT')
    from contextlib import nullcontext
    monkeypatch.setattr(module,'shared_reference_write',lambda *a,**kw:nullcontext())
    calls=[]
    def execute(dialog):
        fields=dialog.findChildren(QLineEdit)
        if not calls:fields[1].setText('MY DRAFT');calls.append(dialog);return QDialog.DialogCode.Accepted
        assert calls[0] is dialog and fields[1].text()=='MY DRAFT'
        calls.append(dialog);return QDialog.DialogCode.Rejected
    monkeypatch.setattr(module.QDialog,'exec',execute);monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
    monkeypatch.setattr(module,'show_reference_conflict',MagicMock())
    view._edit_structure_in_db()
    assert len(calls)==2 and view._cfg.sections[0].structure_code=='S'
    assert view._svc.edit_structure_referentiel.call_args.kwargs['expected'] is original
    view._svc.save.assert_not_called();view.deleteLater()



def test_type_editor_displays_the_same_version_it_retains_after_success(app,monkeypatch):
    from sme_gend.ui import structure_subdivisions_dialog as module
    from sme_gend.ui import structure_edit_dialog as edit_module
    dialog=module.StructureSubdivisionsDialog.__new__(module.StructureSubdivisionsDialog);QDialog.__init__(dialog)
    original=row('structure');fresh=replace(original,designation='CONCURRENT AFTER COMMIT',ordre_tri=99,write_token=('structure','S','b'*64))
    dialog._structure=original;dialog._service=MagicMock();dialog._allow_edit=True;dialog._on_structure_changed=None
    dialog._edit_designation=QLineEdit('OLD');dialog._edit_ordre=QLineEdit('1')
    dialog._service.structures.return_value=[fresh]
    popup=MagicMock();popup.exec.return_value=QDialog.DialogCode.Accepted
    popup.values.return_value=('S','OUR SAVED DRAFT',42,'test')
    monkeypatch.setattr(edit_module,'StructureEditDialog',lambda *a:popup)
    monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
    dialog._edit_type_unite()
    assert dialog._structure is fresh
    assert dialog._edit_designation.text()==fresh.designation
    assert dialog._edit_ordre.text()==str(fresh.ordre_tri)
    dialog.deleteLater()
