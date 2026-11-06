"""Movement conflicts must not discard a draft or silently bypass snapshot checks."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication,QDialog,QWidget

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.gain_perte_write_guard import GainPerteConflictError


def loaded():
    return GainPerteRow('legacy-1','GND','SYNTHETIC','PERTE','092026','RETRAITE',
                       mois_ym='2026-09',date_reference='2026-09-30',movement_id=10,
                       write_token=(10,'a'*64))


@pytest.fixture
def application():
    app=QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.mark.parametrize('token',[None,(10,'short'),(11,'a'*64),(10,123),[10,'a'*64],('10','a'*64)])
@pytest.mark.parametrize('action',['update','delete'])
def test_missing_or_wrong_snapshot_is_refused_before_any_database_access(monkeypatch,token,action):
    from sme_gend.repositories import mysql_bundle_repositories as module
    repo=module.MySQLGainPerteRepository.__new__(module.MySQLGainPerteRepository)
    def forbidden(*args,**kwargs):pytest.fail('Invalid snapshot must not open a transaction')
    monkeypatch.setattr(module,'shared_data_write',forbidden)
    repo._connect=forbidden
    row=replace(loaded(),write_token=token)
    with pytest.raises(ValueError,match='Version du mouvement indisponible'):
        if action=='update':repo.update_row(row,where_id=row.id,where_cdsme=row.cdsme,movement_id=10)
        else:repo.delete_row(row.id,row.cdsme,movement_id=10,expected=row)




def test_month_pair_guard_rejects_divergent_sme_period_and_cdsme():
    from sme_gend.repositories.gain_perte_write_guard import require_consistent_movement_month
    row=GainPerteRow("legacy-1","GND","A","GAIN","082026","AJOUT",mois_ym="2026-09")
    with pytest.raises(ValueError,match="Incohérence de mois"):
        require_consistent_movement_month(row)


def test_month_pair_guard_returns_canonical_access_code():
    from sme_gend.repositories.gain_perte_write_guard import require_consistent_movement_month
    row=GainPerteRow("legacy-1","GND","A","GAIN"," 092026 ","AJOUT",mois_ym="2026-09")
    assert require_consistent_movement_month(row)==("2026-09","092026")

def test_closed_period_write_guard_refuses_inside_repository_layer():
    from sme_gend.repositories.gain_perte_write_guard import require_period_open
    cursor=MagicMock();cursor.fetchone.return_value=(91,"Fermé")
    with pytest.raises(ValueError,match="clôturée"):
        require_period_open(cursor,"2026-09")
    cursor.execute.assert_called_once()
    assert "FOR SHARE" in cursor.execute.call_args.args[0]


def test_missing_period_write_guard_refuses_writes():
    from sme_gend.repositories.gain_perte_write_guard import require_period_open
    cursor=MagicMock();cursor.fetchone.return_value=None
    with pytest.raises(ValueError,match="n’existe pas"):
        require_period_open(cursor,"2026-09")

def test_snapshot_is_not_part_of_business_equality_or_diagnostics():
    row=loaded()
    assert row==replace(row,write_token=(10,'b'*64))
    assert 'a'*64 not in repr(row)


def test_facade_forwards_the_loaded_snapshot_for_deletion():
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository
    repo=GainPerteRepository.__new__(GainPerteRepository);repo._impl=MagicMock()
    row=loaded()
    repo.delete_row(row.id,row.cdsme,movement_id=10,expected=row)
    repo._impl.delete_row.assert_called_once_with(row.id,row.cdsme,movement_id=10,expected=row)


def test_facade_forwards_batch_deletion():
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository
    repo=GainPerteRepository.__new__(GainPerteRepository);repo._impl=MagicMock();repo._settings=Settings(database_path=Path("unused.mdb"))
    rows=[loaded(),replace(loaded(),movement_id=11,write_token=(11,"b"*64))]
    repo.delete_rows(rows)
    repo._impl.delete_rows.assert_called_once_with(rows)



def test_batch_delete_locks_every_snapshot_before_deleting_any_row(monkeypatch):
    from contextlib import nullcontext
    from sme_gend.repositories import mysql_bundle_repositories as module
    repo=module.MySQLGainPerteRepository.__new__(module.MySQLGainPerteRepository)
    repo._settings=Settings(database_path=Path("unused.mdb"))
    cursor=MagicMock();cursor.rowcount=1
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value=cursor
    repo._connect=lambda:nullcontext(conn)
    checked=[];audited=[];revisions=[]
    monkeypatch.setattr(module,"shared_data_write",lambda *a,**kw:nullcontext())
    monkeypatch.setattr(module,"mark_shared_data_write",lambda changed:revisions.append(changed))
    monkeypatch.setattr(module,"check_movement_target",lambda cur,mid,token:checked.append(mid))
    monkeypatch.setattr(module,"require_movement_period_open",lambda cur,mid:"2026-09")
    monkeypatch.setattr("sme_gend.services.business_audit.record_business_action",lambda *a,**kw:audited.append(kw["entity_id"]))
    rows=[replace(loaded(),movement_id=11,write_token=(11,"b"*64)),loaded()]
    repo.delete_rows(rows)
    assert checked==[10,11]
    assert [call.args[1] for call in cursor.execute.call_args_list]==[(10,),(11,)]
    assert audited==[10,11] and revisions==[True]


def test_batch_delete_conflict_aborts_before_any_delete(monkeypatch):
    from contextlib import nullcontext
    from sme_gend.repositories import mysql_bundle_repositories as module
    repo=module.MySQLGainPerteRepository.__new__(module.MySQLGainPerteRepository)
    repo._settings=Settings(database_path=Path("unused.mdb"))
    cursor=MagicMock();conn=MagicMock();conn.cursor.return_value.__enter__.return_value=cursor
    repo._connect=lambda:nullcontext(conn)
    checks=[]
    def check(cur,mid,token):
        checks.append(mid)
        if mid==11:raise GainPerteConflictError("stale")
    monkeypatch.setattr(module,"shared_data_write",lambda *a,**kw:nullcontext())
    monkeypatch.setattr(module,"check_movement_target",check)
    monkeypatch.setattr(module,"require_movement_period_open",lambda cur,mid:"2026-09")
    rows=[loaded(),replace(loaded(),movement_id=11,write_token=(11,"b"*64))]
    with pytest.raises(GainPerteConflictError,match="stale"):
        repo.delete_rows(rows)
    assert checks==[10,11]
    assert not any("DELETE FROM mouvement_effectif" in str(call.args[0]) for call in cursor.execute.call_args_list)

def test_dialog_preserves_original_snapshot_when_identity_or_month_is_edited(application,monkeypatch):
    from sme_gend.ui.views import gain_perte_view as module
    monkeypatch.setattr(module,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09'))
    original=loaded()
    dialog=module.GainPerteEditDialog(Settings(database_path=Path('unused.mdb')),None,
        existing=original,default_cdsme=None,month_catalog=['2026-09','2026-10'])
    dialog._id_le.setText('legacy-2');dialog._nom_le.setText('MY DRAFT')
    dialog._mois_sme_combo.setCurrentIndex(dialog._mois_sme_combo.findData('2026-10'))
    edited=dialog.to_row()
    assert edited.id=='legacy-2' and edited.nom=='MY DRAFT' and edited.mois_ym=='2026-10'
    assert edited.write_token is original.write_token and edited.movement_id==10
    assert dialog.where_key()==('legacy-1','092026',10)
    assert original.nom=='SYNTHETIC' and original.mois_ym=='2026-09'
    dialog.deleteLater()


def test_editor_prefills_cdsme_from_authoritative_sme_period(application,monkeypatch):
    from sme_gend.ui.views import gain_perte_view as module
    monkeypatch.setattr(module,"load_sme_context",lambda:SimpleNamespace(mois_actif="2026-09"))
    legacy=replace(loaded(),cdsme="082026",mois_ym="2026-09")
    dialog=module.GainPerteEditDialog(Settings(database_path=Path("unused.mdb")),None,
        existing=legacy,default_cdsme=None,month_catalog=["2026-09"])
    assert dialog.to_row().effective_mois_ym()=="2026-09"
    assert dialog.to_row().cdsme=="092026"
    dialog.deleteLater()


@pytest.mark.parametrize('inferred',[False,True])
def test_new_or_inferred_movements_remain_insertions_without_a_snapshot(application,monkeypatch,inferred):
    from sme_gend.ui.views import gain_perte_view as module
    monkeypatch.setattr(module,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09'))
    row=replace(loaded(),movement_id=None,write_token=None,inferred=True) if inferred else None
    dialog=module.GainPerteEditDialog(Settings(database_path=Path('unused.mdb')),None,
        existing=row,default_cdsme='092026',month_catalog=['2026-09'])
    assert dialog.to_row().write_token is None and dialog.where_key() is None
    dialog.deleteLater()


@pytest.fixture
def frame(application,monkeypatch):
    from sme_gend.ui.views import gain_perte_view as module
    from sme_gend.services import auth_guard
    view=module.GainPerteView.__new__(module.GainPerteView);QWidget.__init__(view)
    view._settings=Settings(database_path=Path('unused.mdb'))
    view._repo=MagicMock();view._selected_row=MagicMock(return_value=loaded())
    view._selected_rows=MagicMock(return_value=[loaded()])
    view._editing_locked_for_mois=MagicMock(return_value=False)
    view._catalog_months=lambda:['2026-09','2026-10']
    view._editing_locked_for_current_mois=lambda:False
    view._reload_after_change=MagicMock();view._force_reload=MagicMock()
    view._lock_banner=SimpleNamespace(text=lambda:'MOIS FERME')
    monkeypatch.setattr(auth_guard,'require_permission',lambda *a,**kw:True)
    monkeypatch.setattr(module.QMessageBox,'warning',MagicMock())
    monkeypatch.setattr(module,'evaluate_editing_state',lambda *a,**kw:SimpleNamespace(locked=False,detail=''))
    monkeypatch.setattr(module.QMessageBox,'critical',MagicMock())
    monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
    dialog=MagicMock();dialog.exec.side_effect=[QDialog.DialogCode.Accepted]
    dialog.to_row.return_value=replace(loaded(),nom='MY DRAFT')
    dialog.where_key.return_value=('legacy-1','092026',10)
    factory=MagicMock(return_value=dialog);monkeypatch.setattr(module,'GainPerteEditDialog',factory)
    yield view,module,dialog,factory,auth_guard
    view.deleteLater()


def test_conflicted_edit_keeps_the_same_draft_and_never_rebases_onto_a_refresh(frame):
    view,module,dialog,factory,_=frame
    dialog.exec.side_effect=[QDialog.DialogCode.Accepted,QDialog.DialogCode.Accepted,QDialog.DialogCode.Rejected]
    view._repo.update_row.side_effect=GainPerteConflictError('Ce mouvement a été modifié ou supprimé.')
    # Even an incoming UI refresh must not change the baseline of an open draft.
    def warning(*args):view._selected_row.return_value=replace(loaded(),write_token=(10,'b'*64))
    module.QMessageBox.warning.side_effect=warning
    view._on_edit()
    assert factory.call_count==1 and dialog.exec.call_count==3
    assert view._repo.update_row.call_count==2 and module.QMessageBox.warning.call_count==2
    for call in view._repo.update_row.call_args_list:
        assert call.args[0].nom=='MY DRAFT' and call.args[0].write_token==(10,'a'*64)
    view._repo.insert_row.assert_not_called();view._reload_after_change.assert_not_called()
    view._force_reload.assert_called_once_with()


def test_successful_edit_saves_the_displayed_snapshot_once(frame):
    view,module,dialog,factory,_=frame
    view._on_edit()
    view._repo.update_row.assert_called_once_with(dialog.to_row.return_value,
        where_id='legacy-1',where_cdsme='092026',movement_id=10)
    view._reload_after_change.assert_called_once_with();view._force_reload.assert_not_called()


def test_editing_an_inferred_movement_still_creates_a_new_movement(frame):
    view,module,dialog,factory,_=frame
    view._selected_row.return_value=replace(loaded(),inferred=True,movement_id=None,write_token=None)
    dialog.where_key.return_value=None
    dialog.to_row.return_value=replace(view._selected_row.return_value,inferred=False)
    view._on_edit()
    view._repo.insert_row.assert_called_once_with(dialog.to_row.return_value)
    view._repo.update_row.assert_not_called()


@pytest.mark.parametrize('action',['edit','delete'])
@pytest.mark.parametrize('blocked',['permission','month'])
def test_existing_permissions_and_closed_month_checks_remain_before_writes(frame,monkeypatch,action,blocked):
    view,module,dialog,factory,guard=frame
    if blocked=='permission':monkeypatch.setattr(guard,'require_permission',lambda *a,**kw:False)
    elif action=='edit':monkeypatch.setattr(module,'evaluate_editing_state',lambda *a,**kw:SimpleNamespace(locked=True,detail='MOIS FERME'))
    else:view._editing_locked_for_mois=lambda ym:True
    getattr(view,'_on_'+action)()
    factory.assert_not_called();view._repo.update_row.assert_not_called();view._repo.delete_row.assert_not_called();view._repo.delete_rows.assert_not_called()


@pytest.mark.parametrize('action',['edit','delete'])
def test_cancelled_action_does_not_write_or_reload(frame,monkeypatch,action):
    view,module,dialog,factory,_=frame
    dialog.exec.side_effect=[QDialog.DialogCode.Rejected]
    monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:False)
    getattr(view,'_on_'+action)()
    view._repo.update_row.assert_not_called();view._repo.delete_row.assert_not_called();view._repo.delete_rows.assert_not_called()
    view._reload_after_change.assert_not_called();view._force_reload.assert_not_called()


def test_batch_deletion_passes_all_selected_snapshots_once(frame):
    view,module,dialog,factory,_=frame
    rows=[loaded(),replace(loaded(),movement_id=11,write_token=(11,"b"*64))]
    view._selected_rows=MagicMock(return_value=rows)
    view._on_delete()
    view._repo.delete_rows.assert_called_once_with(rows)
    view._reload_after_change.assert_called_once_with()


def test_add_refuses_closed_month_selected_in_dialog(frame,monkeypatch):
    view,module,dialog,factory,guard=frame
    from types import SimpleNamespace
    view._current_mois_ym=lambda:None
    view._default_cdsme=lambda:"092026"
    view._catalog_months=lambda:["2026-09"]
    view._all_rows=[]
    monkeypatch.setattr(module,"load_sme_context",lambda:SimpleNamespace(mois_actif="2026-09"))
    monkeypatch.setattr(module,"evaluate_editing_state",lambda *a,**kw:SimpleNamespace(locked=True,detail="Mois clôturé"))
    dialog.exec.return_value=QDialog.DialogCode.Accepted
    dialog.to_row.return_value=loaded()
    view._on_add()
    view._repo.insert_row.assert_not_called()
    module.QMessageBox.warning.assert_called_once()


def test_editing_closed_row_in_broad_scope_is_refused(frame,monkeypatch):
    view,module,dialog,factory,_=frame
    monkeypatch.setattr(module,'evaluate_editing_state',lambda *a,**kw:SimpleNamespace(locked=True,detail='Mois clôturé'))
    view._on_edit()
    factory.assert_not_called()
    view._repo.update_row.assert_not_called()
    module.QMessageBox.warning.assert_called_once()


def test_batch_deletion_refuses_a_selection_containing_a_closed_month(frame):
    view,module,dialog,factory,_=frame
    view._editing_locked_for_mois=MagicMock(return_value=True)
    view._on_delete()
    view._repo.delete_rows.assert_not_called()
    module.QMessageBox.warning.assert_called_once()
    view._reload_after_change.assert_not_called()


def test_conflicted_deletion_sends_its_snapshot_and_refreshes_without_success(frame):
    view,module,dialog,factory,_=frame
    row=view._selected_row.return_value
    view._selected_rows=MagicMock(return_value=[row])
    view._editing_locked_for_mois=MagicMock(return_value=False)
    view._repo.delete_rows.side_effect=GainPerteConflictError('Ce mouvement a été modifié ou supprimé.')
    view._on_delete()
    view._repo.delete_rows.assert_called_once_with([row])
    module.QMessageBox.warning.assert_called_once()
    view._force_reload.assert_called_once_with();view._reload_after_change.assert_not_called()
