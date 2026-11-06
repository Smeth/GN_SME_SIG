from datetime import datetime,timedelta,timezone
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.config.auth_config import AuthConfig
from sme_gend.services import auth_session_registry as registry


def register(settings,cfg,user,client,now):
    token,error=registry.try_register_session(settings,cfg,username=user,session_rev=0,
        client_instance_id=client,machine_label=client,expires_at=now+timedelta(hours=1))
    assert token and not error
    return token


@pytest.mark.parametrize('same_user',[False,True])
def test_new_login_never_deletes_a_live_token(journal,same_user):
    settings,_=journal
    cfg=AuthConfig(enabled=True,concurrent_sessions_mode='off')
    now=datetime.now(timezone.utc)
    first=register(settings,cfg,'USER-A','COPIED-ID',now)
    second=register(settings,cfg,'USER-A' if same_user else 'USER-B','COPIED-ID',now)
    assert first!=second
    assert registry.heartbeat_session(settings,session_token=first,username='USER-A',session_rev=0)


@pytest.mark.parametrize('offset',[-48,48])
def test_other_account_with_wrong_clock_does_not_expire_first_account(journal,monkeypatch,offset):
    settings,_=journal
    cfg=AuthConfig(enabled=True,concurrent_sessions_mode='off')
    now=datetime.now(timezone.utc)
    first=register(settings,cfg,'USER-A','POSTE-A',now)
    with monkeypatch.context() as patch:
        remote_now=now+timedelta(hours=offset)
        patch.setattr(registry,'_utc_now',lambda:remote_now)
        second=register(settings,cfg,'USER-B','POSTE-B',remote_now)
        assert registry.heartbeat_session(settings,session_token=second,username='USER-B',session_rev=0,
                                          expires_at=remote_now+timedelta(hours=1))
    assert registry.heartbeat_session(settings,session_token=first,username='USER-A',session_rev=0)
    assert registry.heartbeat_session(settings,session_token=second,username='USER-B',session_rev=0)
    assert {r.username for r in registry.list_active_sessions(settings)}=={'USER-A','USER-B'}


def test_single_computer_limit_refuses_second_without_revoking_first(journal):
    settings,_=journal
    cfg=AuthConfig(enabled=True,concurrent_sessions_mode='single')
    now=datetime.now(timezone.utc)
    first=register(settings,cfg,'USER-A','POSTE-A',now)
    token,error=registry.try_register_session(settings,cfg,username='USER-A',session_rev=0,
        client_instance_id='POSTE-B',machine_label='POSTE-B',expires_at=now+timedelta(hours=1))
    assert not token and error
    assert registry.heartbeat_session(settings,session_token=first,username='USER-A',session_rev=0)


def test_operator_can_end_stage_and_really_save_diploma(isolated_mysql,monkeypatch):
    from sme_gend.services import sensitive_data_policy as policy
    from test_sme_stage_persistence_mysql import test_stage_end_and_diploma_commit_together
    monkeypatch.setattr(policy,'session_may_edit_sensitive_data',lambda:False)
    monkeypatch.setattr(policy,'session_may_edit_career_fields',lambda:True)
    test_stage_end_and_diploma_commit_together(isolated_mysql,monkeypatch,'local',False,False)


def test_diploma_permission_failure_does_not_end_stage(isolated_mysql,monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from datetime import date
    from PySide6.QtWidgets import QMessageBox
    from sme_gend.services import sensitive_data_policy as policy
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from test_sme_stage_persistence_mysql import setup_services
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET position_admin_libelle='StageLocal' WHERE id=1")
    row=personnel.get_by_id('1')
    monkeypatch.setattr(policy,'session_may_edit_career_fields',lambda:False)
    monkeypatch.setattr(StageEndDiplomaDialog,'run',lambda *a,**kw:dict(categorie='MILITAIRE',libelle='SYN',date_obtention=date(2026,9,30)))
    view=SimpleNamespace(_scoped_settings=settings,_check_can_edit=lambda **kw:True,
        _keys_for_start_action=lambda _:['42'],_confirm_end_stages=lambda *a:True,
        _resolve_start_stage_rows=lambda *a:[row],_stage_end_reference_date=lambda:'2026-09-30',
        _prepare_stage_rows=lambda rows:{row.id:row},_details_for=lambda _: {})
    module.StagesView._end_checked_stages(view,'local')
    assert personnel.get_by_id('1').position_admin=='StageLocal'
    assert dossier.list_diplomes(row)==[]
    assert 'Aucune modification' in QMessageBox.warning.call_args.args[2]
