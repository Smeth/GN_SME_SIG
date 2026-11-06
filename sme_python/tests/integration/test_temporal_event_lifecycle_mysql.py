from datetime import date
import pytest
from test_restore_mysql import isolated_mysql
from test_grade_date_routing_mysql import calendar, period_row


@pytest.mark.parametrize('family,label', [
    ('mission','Mission'), ('malade','Malade'), ('desertion','D\u00e9serteur'),
    ('stage','StageLocal'), ('conge','Cong\u00e9Admin'),
])
def test_delete_dated_event_restores_monthly_position(isolated_mysql, monkeypatch, family, label):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, personnel, dossier=calendar(isolated_mysql,monkeypatch)
    events=AgentDossierEventsService(settings)
    start=date(2026,9,1)
    if family=='mission':
        events.add_mission('42',date_debut=start,date_fin=None,nature='SYN',lieu=None,reference_dossier=None,observation=None)
        entry=events.missions('42')[0]
        delete=lambda:events.delete_mission('42',entry.id,expected=entry)
    elif family=='malade':
        events.add_malade('42',date_debut=start,date_fin=None,pathologie='SYN',observation=None)
        entry=events.malades('42')[0]
        delete=lambda:events.delete_malade(entry.malade_id,expected=entry,owner_legacy_id='42')
    elif family=='desertion':
        events.add_desertion('42',date_desertion=start,observation='SYN')
        entry=events.desertions('42')[0]
        delete=lambda:events.delete_desertion('42',entry.id,expected=entry)
    elif family=='stage':
        events.add_stage('42',type_code='LOCAL',date_debut=start,date_fin=None,nature='SYN',lieu=None,observation=None)
        entry=events.stages('42')[0]
        delete=lambda:events.delete_stage('42',entry.id,expected=entry)
    else:
        events.add_conge('42',type_code='ADMIN',date_debut=start,date_fin=None,quota_jours=None,observation='SYN',sme_mois='2026-09')
        entry=events.conges('42')[0]
        delete=lambda:events.delete_conge('42',entry.id,expected=entry)
    assert period_row(personnel,'1').position_admin==label
    assert period_row(personnel,'3').position_admin==label
    delete()
    assert period_row(personnel,'1').position_admin=='Pr\u00e9sent'
    assert period_row(personnel,'3').position_admin=='Pr\u00e9sent'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_position_event WHERE agent_id=1")
            assert cur.fetchone()[0]==0


def test_closed_event_target_accepts_dated_correction(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch,closed='2026-10')
    events=AgentDossierEventsService(settings)
    before=period_row(personnel,'1').position_admin
    events.add_malade('42',date_debut=date(2026,10,1),date_fin=None,pathologie='SYN',observation=None)
    assert len(events.malades('42'))==1
    assert period_row(personnel,'1').position_admin==before
    assert period_row(personnel,'3').position_admin=='Malade'


def test_position_detail_keeps_decision_and_location(isolated_mysql,monkeypatch):
    from sme_gend.services.position_admin_events import add_position_event
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    add_position_event(settings,'42',position_admin='Punis',date_debut=date(2026,9,1),date_fin=None,
                       lieu='SYN PLACE',reference_decision='SYN DECISION',observation='SYN NOTES',mois_sme='2026-09')
    value=MySQLNumeriqueRepository(settings).load_position_events_for_period()[('42','punis')]
    assert value[2:]==('SYN PLACE','SYN DECISION','SYN NOTES')
