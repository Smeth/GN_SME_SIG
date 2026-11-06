"""Active rules UI, removed campaign state and explicit load errors."""
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sme_gend.models.avancement import AgentAvancementInput,STATUT_ELIGIBLE,default_rules
from sme_gend.services.avancement_engine import evaluer
from sme_gend.services.avancement_service import AvancementService


def agent(**values):
    defaults=dict(agent_id=1,noms='SYNTHETIC',grade_raw='MDL',date_grade=date(2018,1,1),
        date_service=date(2016,1,1),date_naissance=date(1990,1,1),diplomes=('OPJ',),maniere_servir='favorable')
    defaults.update(values);return AgentAvancementInput(**defaults)


@pytest.mark.parametrize('status',['nomme','ecarte',None])
def test_retired_campaign_status_no_longer_decides_advancement(status):
    result=evaluer(agent(campagne_statut=status),on=date(2022,1,1))
    assert result.statut==STATUT_ELIGIBLE
    assert not any(condition.code=='tableau' and condition.ok is True for condition in result.conditions)


def test_rules_load_error_is_explicit_instead_of_silently_using_defaults():
    service=AvancementService.__new__(AvancementService);service._repo=MagicMock()
    service._repo.list_regles.side_effect=OSError('SYNTHETIC FAILURE')
    service._repo.load_regles_snapshot.side_effect=OSError('SYNTHETIC FAILURE')
    with pytest.raises(RuntimeError):service.regles()


def test_successful_rule_save_refreshes_the_ui_even_when_service_returns_none(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views import avancement_view as ui
    app=QApplication.instance() or QApplication([])
    view=SimpleNamespace(_view_data=MagicMock(),_fill_regles=MagicMock(),_reload=MagicMock())
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,label,fn,**kw:fn())
    ui.AvancementView._run_config_action(view,lambda:None)
    view._view_data.mark_stale.assert_called_once();view._fill_regles.assert_called_once();view._reload.assert_called_once()



@pytest.fixture
def rules_ui(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views import avancement_view as ui
    from sme_gend.config.settings import Settings
    from sme_gend.services.avancement_service import AvancementRules
    from sme_gend.repositories.avancement_rule_guard import AvancementRulesSnapshot
    app=QApplication.instance() or QApplication([])
    shown=replace(default_rules()[1],write_token='a'*64,_write_database=('SYNTHETIC',))
    snapshot=AvancementRulesSnapshot((shown,),'a'*64,('SYNTHETIC',))
    svc=MagicMock();svc.regles.return_value=AvancementRules({'MDC':shown},snapshot)
    monkeypatch.setattr(ui,'AvancementService',lambda settings:svc)
    monkeypatch.setattr(ui,'_can',lambda permission:True)
    monkeypatch.setattr(ui.QMessageBox,'critical',MagicMock())
    frame=ui.AvancementView(Settings(database_path=Path('unused.mdb')))
    frame._fill_regles();frame._regles_table.selectRow(0)
    yield frame,svc,ui,app,shown
    frame.deleteLater();app.processEvents()


def test_rules_conflict_preserves_selection_and_redisplays_edited_draft(rules_ui,monkeypatch):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    frame,svc,ui,app,shown=rules_ui;edited=replace(shown,annees_grade=7)
    drafts=[];messages=[]
    def dialog(parent,draft):drafts.append(draft);return edited if len(drafts)==1 else None
    monkeypatch.setattr(ui,'edit_regle',dialog)
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,label,fn,**kw:fn())
    monkeypatch.setattr(ui,'show_reference_conflict',lambda *args,**kwargs:messages.append(kwargs))
    svc.save_regle.side_effect=ReferenceConflictError('synthetic conflict')
    frame._fill_regles=MagicMock();frame._reload=MagicMock()
    frame._on_edit_regle()
    assert drafts==[shown,edited] and messages==[{'draft':True}]
    svc.save_regle.assert_called_once_with(edited,expected=shown)
    assert frame._regles_table.currentRow()==0
    frame._fill_regles.assert_not_called();frame._reload.assert_not_called()


def test_reset_captures_the_displayed_version_before_confirmation(rules_ui,monkeypatch):
    frame,svc,ui,app,shown=rules_ui;displayed=frame._rules_snapshot
    def confirm(*args,**kwargs):frame._rules_snapshot=object();return True
    monkeypatch.setattr(ui,'confirm_yes_no',confirm)
    frame._run_config_action=lambda fn,**kw:fn()
    frame._on_reset_regles()
    svc.reset_regles.assert_called_once_with(expected=displayed)


def test_rules_reload_failure_keeps_rows_but_disables_writes(rules_ui):
    frame,svc,ui,app,shown=rules_ui
    svc.regles.side_effect=OSError('SYNTHETIC PRIVATE DETAIL')
    frame._fill_regles()
    assert frame._regles_table.rowCount()==1 and frame._regles_table.currentRow()==0
    assert frame._rules_snapshot is None and not frame._btn_edit_regle.isEnabled() and not frame._btn_reset_regles.isEnabled()
    message=ui.QMessageBox.critical.call_args.args[-1]
    assert 'SYNTHETIC PRIVATE DETAIL' not in message


def test_shared_refresh_updates_the_visible_configuration_and_defers_the_hidden_table(rules_ui):
    frame,svc,ui,app,shown=rules_ui
    frame._tabs.setCurrentIndex(1);frame._view_data.mark_stale()
    frame._fill_regles=MagicMock();frame._reload=MagicMock()
    frame.on_page_activated()
    frame._fill_regles.assert_called_once();frame._reload.assert_not_called()
    frame._on_tab_changed(0);frame._reload.assert_called_once()


def test_rebinding_invalidates_the_old_rules_version(rules_ui):
    frame,svc,ui,app,shown=rules_ui
    frame.rebind_settings(frame._settings)
    assert frame._rules_snapshot is None and not frame._btn_edit_regle.isEnabled()


def test_rule_permissions_are_checked_before_edit_or_reset_confirmation(rules_ui,monkeypatch):
    frame,svc,ui,app,shown=rules_ui
    monkeypatch.setattr(ui,'_can',lambda permission:False)
    edit=MagicMock();confirm=MagicMock();monkeypatch.setattr(ui,'edit_regle',edit);monkeypatch.setattr(ui,'confirm_yes_no',confirm)
    frame._on_edit_regle();frame._on_reset_regles()
    edit.assert_not_called();confirm.assert_not_called();svc.save_regle.assert_not_called();svc.reset_regles.assert_not_called()


@pytest.mark.parametrize('accepted',[False,True])
def test_the_actual_rule_dialog_opens_and_preserves_values(rules_ui,monkeypatch,accepted):
    from PySide6.QtWidgets import QDialog
    from sme_gend.ui.views.avancement import dialogs
    frame,svc,ui,app,shown=rules_ui
    monkeypatch.setattr(dialogs.QDialog,'exec',lambda self:QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected)
    result=dialogs.edit_regle(frame,shown)
    assert result==shown if accepted else result is None


def test_vigilance_uses_only_dossier_data_and_rules_not_retired_tables():
    from sme_gend.services.avancement_engine import rules_by_cible
    svc=AvancementService.__new__(AvancementService);svc._repo=MagicMock();svc._settings=MagicMock()
    svc._repo.load_agents.return_value=[agent()];svc.regles=lambda:rules_by_cible()
    result=svc.vigilance(annee=2022,trimestre=1,on=date(2022,1,1))
    assert result[0].statut==STATUT_ELIGIBLE
    svc._repo.get_campagne.assert_not_called();svc._repo.list_lignes.assert_not_called();svc._repo.list_quotas.assert_not_called()
    assert svc.vigilance(annee=2030,trimestre=4,on=date(2022,1,1))==result


def test_retired_campaign_write_apis_and_dialogs_are_absent():
    from sme_gend.repositories.mysql_avancement_repository import MySQLAvancementRepository
    from sme_gend.ui.views.avancement import dialogs
    for name in ('ouvrir_campagne','cloturer_campagne','rouvrir_campagne','definir_quota','proposer','enregistrer_avis','inscrire','ecarter','nommer'):
        assert not hasattr(AvancementService,name)
    for name in ('create_campagne','set_campagne_statut','set_quota','upsert_ligne','update_ligne'):
        assert not hasattr(MySQLAvancementRepository,name)
    for name in ('ask_avis','ask_nomination','pick_agent_voie'):assert not hasattr(dialogs,name)


def test_rule_persistence_metadata_does_not_change_rule_equality_or_repr():
    shown=default_rules()[1];bound=replace(shown,write_token='test-token',_write_database=('test-database',))
    assert bound==shown and 'test-token' not in repr(bound) and 'test-database' not in repr(bound)
