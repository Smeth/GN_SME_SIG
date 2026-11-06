"""Regression coverage for corrected absence and position dates."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from test_restore_mysql import isolated_mysql
from test_grade_date_routing_mysql import calendar, period_row

DATES = [
    (date(2026,10,1),date(2026,10,15)),
    (date(2026,8,10),date(2026,8,20)),
    (date(2026,9,20),date(2026,10,10)),
]


@pytest.mark.parametrize('start,end', DATES)
def test_corrected_leave_stays_attached_to_entered_month(isolated_mysql,monkeypatch,start,end):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    events=AgentDossierEventsService(settings)
    events.add_conge('42',type_code='ADMIN',date_debut=start,date_fin=end,quota_jours=None,
                     observation='SYNTHETIC',sme_mois='2026-09')
    assert len(events.conges('42'))==1
    assert ('42' in MySQLNumeriqueRepository(settings).load_latest_conges_for_period()) == (start<=date(2026,9,30) and end>=date(2026,9,1))
    for month in ('2026-08','2026-10'):
        from sme_gend.services.mysql_import_period import first_day_of_month,last_day_of_month
        assert ('42' in MySQLNumeriqueRepository(replace(settings,mysql_view_mois=month)).load_latest_conges_for_period()) == (start<=last_day_of_month(month) and end>=first_day_of_month(month))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois,c.date_debut,c.date_fin FROM agent_conge c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=1')
            assert cur.fetchone()==(start.strftime('%Y-%m'),start,end)
    print('Corrected dated path verified.')


@pytest.mark.parametrize('start,end', DATES)
def test_corrected_manual_illness_is_in_history_but_absent_from_monthly_reader(isolated_mysql,monkeypatch,start,end):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    events=AgentDossierEventsService(settings)
    events.add_malade('42',date_debut=start,date_fin=end,pathologie='SYNTHETIC',observation=None)
    assert len(events.malades('42'))==1
    for month in ('2026-08','2026-09','2026-10'):
        repo=MySQLNumeriqueRepository(replace(settings,mysql_view_mois=month))
        from sme_gend.services.mysql_import_period import first_day_of_month,last_day_of_month
        eligible=start<=last_day_of_month(month) and end>=first_day_of_month(month)
        assert ('42' in repo.load_malades()) == eligible
        assert ('42' in repo.load_malades(legacy_id='42')) == eligible
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois,m.date_debut,m.date_fin FROM agent_malade m JOIN sme_periode p ON p.id=m.sme_periode_id WHERE m.agent_id=1')
            assert cur.fetchone()==(start.strftime('%Y-%m'),start,end)
    print('Corrected dated path verified.')


@pytest.mark.parametrize('start,end', DATES)
def test_corrected_position_change_is_immediate_in_view_month_despite_event_dates(isolated_mysql,monkeypatch,start,end):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_COL_IDX
    from sme_gend.services.position_admin_events import add_position_event,apply_position_event_schema
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    apply_position_event_schema(settings)
    row=period_row(personnel,'1')
    app=QApplication.instance() or QApplication([])
    model=_EditionModel()
    model.load([row],None,[],[],[],[],personnel=personnel,settings=settings)
    def prepare(*args):
        return lambda: add_position_event(settings,'42',position_admin='Détaché',date_debut=start,
            date_fin=end,lieu=None,reference_decision=None,observation='SYNTHETIC',mois_sme='2026-09')
    model.position_change_prompt=prepare
    assert model.setData(model.index(0,_COL_IDX['position_admin']),'Détaché')
    assert period_row(personnel,'1').position_admin == ('Détaché' if start<=date(2026,9,30)<=end else 'Présent')
    assert period_row(personnel,'3').position_admin == 'Présent'
    app.processEvents()


def test_corrected_grouped_future_mission_changes_current_month(isolated_mysql,monkeypatch):
    from sme_gend.services import padmin_satellite_apply as module
    from sme_gend.ui.padmin_satellite_dialog import SatelliteFormResult
    from sme_gend.models.numerique_satellite import MissionDetail
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,position_admin_libelle) VALUES(9,2,1,'Présent'),(10,2,3,'Présent')")
    rows=[period_row(personnel,'1'),personnel.get_by_id('9')]
    form=SatelliteFormResult(kind='mission',mission=MissionDetail('SYNTHETIC',None,None,'01/10/2026'))
    monkeypatch.setattr(module.BulkMissionStageDialog,'run',lambda *a,**kw:('mission',form))
    monkeypatch.setattr(module,'_bulk_stage_nature_suggestions',lambda _:[])
    count,errors=module.bulk_apply_mission_or_stage(None,settings,rows)
    assert count==2 and not errors
    assert period_row(personnel,'1').position_admin=='Pr\u00e9sent'
    assert personnel.get_by_id('9').position_admin=='Pr\u00e9sent'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois,m.date_debut FROM agent_mission m JOIN sme_periode p ON p.id=m.sme_periode_id ORDER BY m.agent_id')
            assert cur.fetchall()==(('2026-10',date(2026,10,1)),('2026-10',date(2026,10,1)))
    print('Corrected dated path verified.')


def test_corrected_dossier_leave_dialog_uses_active_month_instead_of_viewed_month(monkeypatch):
    from sme_gend.ui import personnel_dossier_dialog as module, conge_edit_dialog
    monkeypatch.setattr('sme_gend.config.sme_context.load_sme_context',lambda:SimpleNamespace(mois_actif='2026-11'))
    captured={}
    def dialog_factory(*args,**kwargs):
        captured.update(kwargs)
        dialog=Mock()
        dialog.exec.return_value=QDialog.DialogCode.Accepted
        dialog.values.return_value={'sme_mois':kwargs['sme_periode_mois']}
        return dialog
    monkeypatch.setattr(conge_edit_dialog,'CongeEditDialog',dialog_factory)
    events=Mock()
    view=SimpleNamespace(_service=SimpleNamespace(settings=SimpleNamespace(mysql_view_mois='2026-09')),
        _row=SimpleNamespace(satellite_join_key=lambda:'42'),_events_svc=events,_refresh_dossier_tab=Mock())
    module.PersonnelDossierDialog._on_add_conge(view)
    assert captured['sme_periode_mois']=='2026-09'
    events.add_conge.assert_called_once_with('42',sme_mois='2026-09')
    print('Corrected dated path verified.')
