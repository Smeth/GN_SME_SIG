import pytest
from sme_gend.services import auth_client_instance as client
from sme_gend.services import sensitive_data_policy as policy


def test_copied_profile_has_a_different_machine_id(tmp_path,monkeypatch):
    monkeypatch.setattr(client,'user_application_data_dir',lambda:tmp_path)
    monkeypatch.setattr(client,'_machine_identity',lambda:'POSTE-A')
    first=client.client_instance_id()
    assert client.client_instance_id()==first
    monkeypatch.setattr(client,'_machine_identity',lambda:'POSTE-B')
    assert client.client_instance_id()!=first


@pytest.mark.parametrize('allowed',[True,False])
def test_diploma_add_uses_editing_rights_without_unlocking_history(monkeypatch,allowed):
    monkeypatch.setattr(policy,'session_may_edit_sensitive_data',lambda:False)
    monkeypatch.setattr(policy,'session_may_edit_career_fields',lambda:allowed)
    if allowed:policy.assert_may_add_diplome()
    else:
        with pytest.raises(policy.SensitiveDataPermissionError):policy.assert_may_add_diplome()
    with pytest.raises(policy.SensitiveDataPermissionError):
        policy.assert_may_mutate_dossier_history(action='modifier un diplome')


def test_dossier_operator_can_add_but_cannot_edit_existing_history(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:False)
    monkeypatch.setattr(policy,'session_may_edit_career_fields',lambda:True)
    names=['stage_add','stage_edit','stage_del','mission_add','mission_edit','mission_del',
           'desertion_add','desertion_edit','desertion_del','conge','conge_add','conge_edit','conge_del',
           'passation','dip_add','dip_edit','dip_del','pas_add','pas_edit','pas_del','aff_add','aff_edit','aff_del']
    view=SimpleNamespace(_allow_edit=True,_lbl_history_admin_hint=Mock(),**{'_btn_'+n:Mock() for n in names})
    PersonnelDossierDialog._sync_history_edit_controls(view)
    view._btn_dip_add.setEnabled.assert_called_with(True)
    view._btn_dip_edit.setEnabled.assert_called_with(False)
    view._btn_dip_del.setEnabled.assert_called_with(False)
    view._btn_aff_edit.setEnabled.assert_called_with(False)
