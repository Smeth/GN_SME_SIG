"""Workspace snapshots, local-only preferences, editor drafts and audit identity."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock
import json
import pytest
from PySide6.QtWidgets import QApplication,QFrame,QLineEdit,QPushButton,QMessageBox
from sme_gend.config import sme_context as context
from sme_gend.config.settings import Settings
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services import sme_workspace_service as module
from sme_gend.services.shared_config_write import bind_config_version


def preferences(settings=None,raw=None):
    settings=settings or Settings(database_path=Path('unused.mdb'))
    raw=raw if raw is not None else dict(module._default_workspace(),future={'preserved':True})
    cfg=module.WorkspacePreferences(raw)
    return bind_config_version(cfg,'sme_workspace',raw,settings)


@pytest.fixture
def app(monkeypatch):
    app=QApplication.instance() or QApplication([])
    for name in ('warning','critical','information'):monkeypatch.setattr(QMessageBox,name,MagicMock())
    yield app
    app.processEvents()


@pytest.mark.parametrize('central',['CURRENT','',None])
def test_central_signatory_is_authoritative_even_when_explicitly_cleared(monkeypatch,tmp_path,central):
    legacy=tmp_path/'legacy.json';legacy.write_text(json.dumps({'editeur_defaut':'OBSOLETE'}))
    monkeypatch.setattr(context,'_legacy_context_file',lambda:legacy)
    monkeypatch.setattr(context,'sme_context_file',lambda:tmp_path/'local.json')
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    monkeypatch.setattr('sme_gend.services.app_config_store.load_config_dict',lambda *a:None if central is None else {'editeur_defaut':central})
    assert context.peek_editeur_defaut()==('OBSOLETE' if central is None else central)


def test_workspace_roundtrip_preserves_unknown_fields_without_exposing_legacy_months():
    raw=dict(module._default_workspace(),mois_actif='2020-01',future={'nested':[1,2]})
    cfg=preferences(raw=raw)
    assert 'mois_actif' not in cfg and 'future' not in cfg
    cfg['editeur_defaut']='NEW';encoded=cfg.to_dict()
    assert encoded['mois_actif']=='2020-01' and encoded['future']==raw['future']
    assert not any(name in encoded for name in ('write_token','_write_key','_write_database','_pending_write'))
    ctx=context.SmeContext(_workspace_snapshot=cfg,_loaded_mois_actif='2026-09')
    assert '_workspace_snapshot' not in ctx.to_dict() and cfg.write_token not in repr(ctx)
    assert deepcopy(cfg).write_token==cfg.write_token


@pytest.mark.parametrize('field,value',[('editeur_defaut',3),('jour_cloture',True),('jour_cloture',0),('jour_cloture',32),('prompt_mois_au_demarrage','false'),('presences_mode','bad'),('mois_actif','2026-12')])
def test_invalid_workspace_preference_is_refused_before_sql(monkeypatch,field,value):
    service=module.SmeWorkspaceService.__new__(module.SmeWorkspaceService)
    service._settings=Settings(database_path=Path('unused.mdb'))
    cfg=preferences();cfg[field]=value
    monkeypatch.setattr(module,'save_shared_model',lambda *a,**kw:pytest.fail('Invalid preference reached SQL'))
    with pytest.raises(ValueError):service.save_workspace_payload(cfg)


def test_plain_workspace_dictionary_cannot_replace_a_loaded_snapshot(monkeypatch):
    service=module.SmeWorkspaceService.__new__(module.SmeWorkspaceService)
    service._settings=Settings(database_path=Path('unused.mdb'))
    monkeypatch.setattr(module,'save_shared_model',lambda *a,**kw:pytest.fail('Unversioned preference reached SQL'))
    with pytest.raises(ValueError,match='Rechargez'):service.save_workspace_payload(module._default_workspace())


@pytest.mark.parametrize('local',['theme','columns'])
def test_explicit_local_save_does_not_even_load_settings_or_workspace(monkeypatch,local):
    writes=[]
    monkeypatch.setattr(context,'_save_local_ui_prefs',lambda ctx:writes.append(ctx.to_dict()))
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:pytest.fail('Local edit accessed shared settings'))
    ctx=context.SmeContext(theme='dark' if local=='theme' else 'light',hidden_columns=['x'])
    context.save_sme_context(ctx,promote_operational_month=False,save_shared_preferences=False)
    assert len(writes)==1


def test_local_context_does_not_reinterpret_legacy_operational_preferences(monkeypatch):
    monkeypatch.setattr(context,'_load_local_ui_prefs',lambda:{'mois_actif':'2020-01','editeur_defaut':'OBSOLETE','theme':'dark','hidden_columns':['x']})
    cfg=context.load_local_sme_context()
    assert cfg.theme=='dark' and cfg.hidden_columns==['x'] and cfg.mois_actif=='' and cfg.editeur_defaut=='' and cfg._workspace_snapshot is None


def test_unloaded_shared_context_cannot_save_defaults(monkeypatch):
    monkeypatch.setattr(context,'_save_local_ui_prefs',lambda ctx:None)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:pytest.fail('Unloaded shared edit accessed settings'))
    with pytest.raises(ValueError,match="n'a pas pu être chargé"):context.save_sme_context(context.SmeContext())


def test_workflow_has_no_manual_signatory_field_or_save_action(app,monkeypatch):
    from PySide6.QtWidgets import QLabel
    from sme_gend.ui.views import sme_workflow_panel as ui
    monkeypatch.setattr(ui,'session_can_perform_action',lambda *args:True)
    view=ui.SmeWorkflowPanel(Settings(database_path=Path('unused.mdb')))
    assert not hasattr(view,'_editeur_audit')
    assert not any('Signataire' in label.text() for label in view.findChildren(QLabel))
    assert not any(button.text() in ('Enregistrer','Recharger') for button in view.findChildren(QPushButton))
    view.close()


def test_refresh_of_workflow_permissions_does_not_load_shared_signatory(app,monkeypatch):
    from sme_gend.ui.views import sme_workflow_panel as ui
    view=ui.SmeWorkflowPanel(Settings(database_path=Path('unused.mdb')))
    read=MagicMock(side_effect=AssertionError('Permission refresh must not load shared configuration'))
    monkeypatch.setattr(ui,'load_sme_context',read)
    permissions=MagicMock()
    monkeypatch.setattr(view,'_apply_workflow_permissions',permissions)
    view.refresh_workflow()
    read.assert_not_called()
    permissions.assert_called_once()
    view.close()



@pytest.mark.parametrize('intent',['preference','month'])
def test_context_from_another_database_is_refused_before_a_business_write(monkeypatch,intent):
    settings=Settings(database_path=Path('unused.mdb'))
    cfg=context.SmeContext(mois_actif='2026-09',_loaded_mois_actif='2026-09',_workspace_snapshot=preferences(settings))
    if intent=='preference':cfg.editeur_defaut='NEW'
    else:cfg.mois_actif='2026-10'
    from dataclasses import replace
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:replace(settings,mysql_database='other_database'))
    monkeypatch.setattr(context,'_save_local_ui_prefs',lambda ctx:None)
    monkeypatch.setattr(module,'SmeWorkspaceService',lambda *a:pytest.fail('Old database snapshot reached a business write'))
    with pytest.raises(ValueError,match='base de travail a changé'):context.save_sme_context(cfg)


@pytest.mark.parametrize('closing',['cancel','window'])
def test_startup_cancel_keeps_original_snapshot_and_never_promotes_the_month(app,monkeypatch,closing):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog,QDialogButtonBox,QCheckBox
    from sme_gend.ui import sme_session_dialog as ui
    settings=Settings(database_path=Path('unused.mdb'))
    initial=context.SmeContext(mois_actif='2026-09',_loaded_mois_actif='2026-09',_workspace_snapshot=preferences())
    read=MagicMock(return_value=initial);save=MagicMock()
    monkeypatch.setattr(ui,'load_sme_context',read);monkeypatch.setattr(ui,'save_sme_context',save)
    monkeypatch.setattr(ui,'SmeRepository',lambda *a:SimpleNamespace(table_exists=lambda:False))
    def execute(dialog):
        dialog.findChild(QCheckBox).setChecked(False)
        if closing=='cancel':dialog.findChild(QDialogButtonBox).rejected.emit()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',execute)
    ui.run_startup_sme_session_dialog(None,settings)
    save.assert_called_once();read.assert_called_once()
    submitted=save.call_args.args[0]
    assert submitted.prompt_mois_au_demarrage is False and initial.prompt_mois_au_demarrage is True
    assert submitted._workspace_snapshot.write_token==initial._workspace_snapshot.write_token
    assert save.call_args.kwargs=={'promote_operational_month':False}


@pytest.mark.parametrize('failure',['conflict','offline'])
def test_startup_failed_save_keeps_dialog_open_then_explicit_reload_can_recover(app,monkeypatch,failure):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog,QDialogButtonBox,QCheckBox,QSpinBox
    from sme_gend.ui import sme_session_dialog as ui
    settings=Settings(database_path=Path('unused.mdb'))
    initial=context.SmeContext(mois_actif='2026-09',_loaded_mois_actif='2026-09',_workspace_snapshot=preferences())
    fresh=context.SmeContext(mois_actif='2026-10',_loaded_mois_actif='2026-10',_workspace_snapshot=preferences())
    fresh._workspace_snapshot.write_token='b'*64
    read=MagicMock(side_effect=[initial,fresh]);error=ReferenceConflictError('synthetic conflict') if failure=='conflict' else OSError('synthetic offline')
    save=MagicMock(side_effect=[error,None])
    monkeypatch.setattr(ui,'load_sme_context',read);monkeypatch.setattr(ui,'save_sme_context',save)
    monkeypatch.setattr(ui,'confirm_yes_no',lambda *a,**kw:True)
    monkeypatch.setattr(ui,'SmeRepository',lambda *a:SimpleNamespace(table_exists=lambda:False))
    def execute(dialog):
        checkbox=dialog.findChild(QCheckBox);checkbox.setChecked(False)
        year=dialog.findChild(QSpinBox);year.setValue(2030)
        buttons=dialog.findChild(QDialogButtonBox)
        buttons.accepted.emit()
        assert dialog.result()!=QDialog.DialogCode.Accepted
        assert checkbox.isChecked() is False and year.value()==2030
        next(button for button in buttons.buttons() if button.text()=='Recharger').click()
        assert checkbox.isChecked() is True and year.value()==2026
        buttons.accepted.emit()
        assert dialog.result()==QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',execute)
    ui.run_startup_sme_session_dialog(None,settings)
    assert save.call_count==2 and read.call_count==2
    assert save.call_args.args[0]._workspace_snapshot.write_token=='b'*64



def test_post_wipe_reset_uses_a_loaded_snapshot_and_keeps_unrecognized_config(monkeypatch):
    from sme_gend.services import wipe_operational_data as wipe
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    cfg=preferences(raw=dict(module._default_workspace(),editeur_defaut='OLD',future={'preserved':True}))
    service=MagicMock();service.load_workspace_payload.return_value=cfg
    monkeypatch.setattr(AppConfigRepository,'ensure_tables',lambda self:None)
    monkeypatch.setattr(wipe,'save_config_dict',MagicMock())
    monkeypatch.setattr(module,'SmeWorkspaceService',lambda settings:service)
    wipe._restore_post_wipe_config(Settings(database_path=Path('unused.mdb')),None)
    submitted=service.save_workspace_payload.call_args.args[0]
    assert submitted is cfg and submitted['editeur_defaut']==''
    assert submitted.to_dict()['future']=={'preserved':True} and submitted.write_token is not None



def test_shared_preference_edit_does_not_rewrite_unchanged_local_display_settings(monkeypatch):
    settings=Settings(database_path=Path('unused.mdb'));service=MagicMock()
    service.load_workspace_payload.return_value=preferences(settings)
    service.get_active_mois.return_value='2026-09';service.list_closed_months.return_value=[]
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready',lambda *a:None)
    monkeypatch.setattr(context,'_migrate_legacy_context_to_db',lambda *a:None)
    monkeypatch.setattr(context,'_load_local_ui_prefs',lambda:{'theme':'light','hidden_columns':['x']})
    monkeypatch.setattr(module,'SmeWorkspaceService',lambda *a:service)
    local_save=MagicMock();monkeypatch.setattr(context,'_save_local_ui_prefs',local_save)
    ctx=context.load_sme_context();ctx.editeur_defaut='NEW'
    context.save_sme_context(ctx,promote_operational_month=False)
    service.save_workspace_payload.assert_called_once();local_save.assert_not_called()
