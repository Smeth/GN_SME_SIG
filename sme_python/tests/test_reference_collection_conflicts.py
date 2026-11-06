"""Displayed relation sets and layout drafts must never silently rebase."""
from dataclasses import FrozenInstanceError,replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from contextlib import nullcontext
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QDialog,QWidget,QLineEdit,QDialogButtonBox
from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.reference_collection_guard import CollectionSnapshot
from sme_gend.repositories.reference_write_guard import ReferenceConflictError

def snapshot(kind):
    structures=((1,'OLD','Old',1,'test'),(2,'DEST','Dest',2,'test'))
    functions=((1,'FUNC'),(2,'FNEW'))
    if kind=='fonction':return CollectionSnapshot('links_fonction','1','a'*64,((1,'FUNC'),),structures,((1,1,0),))
    return CollectionSnapshot('links_structure','OLD','a'*64,(structures[0],),functions,((1,1,0),))

@pytest.fixture
def app():
    application=QApplication.instance() or QApplication([])
    yield application
    application.processEvents()

@pytest.mark.parametrize('scope',['links_structure','links_fonction','order_structures','order_subdivisions'])
@pytest.mark.parametrize('bad',['missing','wrong_scope'])
def test_mutating_collections_require_the_correct_loaded_snapshot_before_sql(monkeypatch,scope,bad):
    from sme_gend.repositories import mysql_fonction_structure_repository as links
    from sme_gend.repositories import mysql_organigram_repository as org
    module=links if scope.startswith('links') else org
    cls=links.MySQLFonctionStructureRepository if module is links else org.MySQLOrganigramRepository
    repo=cls.__new__(cls)
    def forbidden(*a,**kw):pytest.fail('Invalid collection must not open a transaction')
    monkeypatch.setattr(module,'shared_reference_write',forbidden)
    expected=None if bad=='missing' else snapshot('fonction' if scope!='links_fonction' else 'structure')
    with pytest.raises(ValueError,match="Version de l'ensemble indisponible"):
        if scope=='links_structure':repo.set_links_for_structure('OLD',[],expected=expected)
        elif scope=='links_fonction':repo.set_links_for_fonction(1,[],expected=expected)
        elif scope=='order_structures':repo.reorder_structures(['OLD'],expected=expected)
        else:repo.reorder_subdivisions(1,[10],expected=expected)

def test_layout_without_a_loaded_version_is_refused_before_sql(monkeypatch):
    from sme_gend.services import layout_write_guard as module
    monkeypatch.setattr(module,'shared_reference_write',lambda *a,**kw:pytest.fail('Unexpected SQL'))
    with pytest.raises(ValueError,match='Version de mise en page indisponible'):
        module.save_layout(Settings(database_path=Path('unused.mdb')),ListeLayoutConfig())

def test_snapshot_is_immutable_and_layout_metadata_is_not_serialized():
    value=snapshot('fonction')
    with pytest.raises(FrozenInstanceError):value.revision='b'*64
    assert 'a'*64 not in repr(value)
    cfg=ListeLayoutConfig(write_token='a'*64)
    assert 'write_token' not in cfg.to_dict() and '_pending_write' not in cfg.to_dict()
    assert cfg==ListeLayoutConfig.from_dict(cfg.to_dict()) and 'a'*64 not in repr(cfg)

def relation_dialog(kind,monkeypatch,failed=False,read_only=False):
    service=MagicMock();value=snapshot(kind)
    service.fonction_links_snapshot.return_value=value;service.structure_links_snapshot.return_value=value
    if failed:
        service.fonction_links_snapshot.side_effect=OSError('synthetic offline')
        service.structure_links_snapshot.side_effect=OSError('synthetic offline')
    if kind=='fonction':
        from sme_gend.ui import fonction_structures_dialog as module
        dialog=module.FonctionStructuresDialog(service,1,'FUNC')
        setter=service.fonction_set_structure_links
    else:
        from sme_gend.ui import structure_fonctions_dialog as module
        dialog=module.StructureFonctionsDialog(service,StructureRow('OLD','Old',1),read_only=read_only)
        setter=service.fonction_set_links_for_structure
    dialog.accept=MagicMock();monkeypatch.setattr(module,'show_reference_conflict',MagicMock())
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox,'warning',MagicMock());monkeypatch.setattr(QMessageBox,'critical',MagicMock())
    return dialog,service,setter,module,value

@pytest.mark.parametrize('kind',['fonction','structure'])
@pytest.mark.parametrize('result',['success','conflict','load_error'])
def test_link_dialog_preserves_checks_and_uses_its_original_snapshot(app,monkeypatch,kind,result):
    dialog,service,setter,module,value=relation_dialog(kind,monkeypatch,failed=result=='load_error')
    if result=='load_error':
        assert not dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).isEnabled()
        dialog._on_save();setter.assert_not_called();dialog.accept.assert_not_called()
    else:
        dialog._list.item(1).setCheckState(Qt.CheckState.Checked)
        selected=[dialog._list.item(i).checkState() for i in range(dialog._list.count())]
        # A new backend snapshot becoming available must not replace this draft's baseline.
        service.fonction_links_snapshot.return_value=replace(value,revision='b'*64)
        service.structure_links_snapshot.return_value=replace(value,revision='b'*64)
        if result=='conflict':setter.side_effect=ReferenceConflictError('CONFLICT')
        dialog._on_save()
        assert setter.call_args.kwargs['expected'] is value
        assert selected==[dialog._list.item(i).checkState() for i in range(dialog._list.count())]
        if result=='conflict':dialog.accept.assert_not_called();module.show_reference_conflict.assert_called_once()
        else:dialog.accept.assert_called_once()
    service.structures.assert_not_called();service.fonctions_all.assert_not_called();dialog.deleteLater()

def test_readonly_relation_dialog_never_writes(app,monkeypatch):
    dialog,service,setter,module,value=relation_dialog('structure',monkeypatch,read_only=True)
    dialog._on_save();setter.assert_not_called();dialog.deleteLater()

@pytest.mark.parametrize('kind',['fonction','structure'])
def test_filtered_checked_links_remain_in_the_saved_selection(app,monkeypatch,kind):
    dialog,service,setter,module,value=relation_dialog(kind,monkeypatch)
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked)
    checked=dialog._list.item(0).data(Qt.ItemDataRole.UserRole)
    dialog._search.setText('DOES NOT MATCH')
    dialog._on_save()
    assert checked in setter.call_args.args[1]
    dialog.deleteLater()

def test_smart_dialog_checks_links_before_renaming_or_merging(app,monkeypatch):
    from sme_gend.ui import fonction_smart_dialog as module
    dialog=module.FonctionSmartDialog.__new__(module.FonctionSmartDialog);QDialog.__init__(dialog)
    dialog._service=MagicMock();dialog._service.settings=Settings(database_path=Path('unused.mdb'))
    dialog._service.validate_links_snapshot.side_effect=ReferenceConflictError('CONFLICT')
    dialog._links_snapshot=snapshot('fonction');dialog._libelle=QLineEdit('MY DRAFT');dialog._selected_codes=lambda:['OLD']
    dialog._catalog_snapshot=[];dialog._fonction_id=None
    dialog._settings=dialog._service.settings
    dialog._resolve_fonction_id=MagicMock();dialog.accept=MagicMock()
    monkeypatch.setattr(module,'shared_reference_write',lambda *a,**kw:nullcontext())
    monkeypatch.setattr(module.QMessageBox,'warning',MagicMock())
    dialog._on_save()
    dialog._resolve_fonction_id.assert_not_called();dialog._service.fonction_merge_structure_links.assert_not_called()
    dialog.accept.assert_not_called();assert dialog._libelle.text()=='MY DRAFT';dialog.deleteLater()

def layout_frame(monkeypatch):
    from sme_gend.ui.views import layout_editor_view as module
    view=module.LayoutEditorView.__new__(module.LayoutEditorView);QWidget.__init__(view)
    view._cfg=ListeLayoutConfig(sections=[SectionBlock('a',0,'OLD')],write_token='a'*64)
    view._svc=MagicMock();view._autosave_timer=MagicMock();view._set_status=MagicMock()
    view._current_section_id=lambda:'a';view._populate_list=MagicMock();view._on_saved=MagicMock()
    view._sync_current_to_cfg=MagicMock();view._reorder_sections_from_ui=MagicMock()
    monkeypatch.setattr(module,'show_reference_conflict',MagicMock());monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
    monkeypatch.setattr(module.QMessageBox,'critical',MagicMock())
    return view,module

def test_layout_conflict_stops_autosave_and_keeps_draft_and_visible_status(app,monkeypatch):
    view,module=layout_frame(monkeypatch);cfg=view._cfg
    view._svc.save.side_effect=ReferenceConflictError('CONFLICT')
    view._auto_save();view._auto_save();view._manual_save();view._mark_pending()
    assert view._save_conflict and view._cfg is cfg and view._svc.save.call_count==1
    assert 'Conflit' in view._set_status.call_args.args[0]
    view._populate_list.assert_not_called();view._on_saved.assert_not_called();view.deleteLater()

@pytest.mark.parametrize('failed',[False,True])
def test_explicit_layout_reload_only_replaces_a_draft_after_success(app,monkeypatch,failed):
    view,module=layout_frame(monkeypatch);original=view._cfg;view._save_conflict=True
    fresh=ListeLayoutConfig(write_token='b'*64)
    if failed:view._svc.load.side_effect=RuntimeError('synthetic read failure')
    else:view._svc.load.return_value=fresh
    view._reload()
    if failed:assert view._cfg is original and view._save_conflict
    else:assert view._cfg is fresh and not view._save_conflict
    view.deleteLater()

def test_background_layout_reconcile_does_not_mutate_the_draft_on_conflict(app,monkeypatch):
    view,module=layout_frame(monkeypatch);original=view._cfg
    def reconcile(copy):copy.sections[0].structure_code='CHANGED';return copy,SimpleNamespace(changed=True)
    view._svc.reconcile_with_database.side_effect=reconcile;view._svc.save.side_effect=ReferenceConflictError('CONFLICT')
    view._silent_sync_from_db()
    assert view._cfg is original and original.sections[0].structure_code=='OLD' and view._save_conflict
    view.deleteLater()

def test_reset_layout_preserves_the_previous_baseline(app,monkeypatch):
    view,module=layout_frame(monkeypatch);original=view._cfg;view._do_save=MagicMock()
    view._svc.build_default_from_database.return_value=ListeLayoutConfig()
    view._reset_db()
    assert view._cfg.write_token==original.write_token
    view._do_save.assert_called_once();view.deleteLater()

def test_layout_filter_and_merge_keep_snapshot_metadata():
    from sme_gend.services.liste_present_layout_coherence import filter_layout_to_database
    from sme_gend.services.layout_config_service import LayoutConfigService
    cfg=ListeLayoutConfig(sections=[SectionBlock('a',0,'OLD')],write_token='a'*64)
    rows=[StructureRow('OLD','Old',1)]
    filtered,_=filter_layout_to_database(cfg,rows)
    service=LayoutConfigService.__new__(LayoutConfigService);service._structures=SimpleNamespace(fetch_all=lambda:rows)
    merged=service.merge_missing_structures(filtered)
    assert filtered.write_token==cfg.write_token and merged.write_token==cfg.write_token



@pytest.mark.parametrize('failed',['catalogue','links'])
def test_smart_load_failure_is_visible_and_cannot_create_an_entry(app,monkeypatch,failed):
    from sme_gend.ui import fonction_smart_dialog as module
    service=MagicMock();service.fonctions_all.return_value=[]
    if failed=='catalogue':service.fonctions_all.side_effect=OSError('synthetic catalogue offline')
    else:service.fonction_links_snapshot_all.side_effect=OSError('synthetic links offline')
    monkeypatch.setattr(module.QMessageBox,'warning',MagicMock())
    dialog=module.FonctionSmartDialog(service,None,libelle='MY DRAFT')
    assert dialog._links_load_error and not dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).isEnabled()
    dialog._on_save()
    assert dialog._libelle.text()=='MY DRAFT'
    service.fonction_insert.assert_not_called();service.fonction_update.assert_not_called()
    service.fonction_merge_structure_links.assert_not_called();dialog.deleteLater()
