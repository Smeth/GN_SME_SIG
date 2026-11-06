from datetime import date,datetime
from dataclasses import replace
import json
import pytest
from test_restore_mysql import isolated_mysql
from test_grade_date_routing_mysql import calendar


def closed_calendar(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_service import AgentDossierService
    settings,connect,params,personnel,_=calendar(isolated_mysql,monkeypatch,source='2026-12')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ferm\u00e9'")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(6,'2026-12','2026-12-31','Ouvert')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,date_grade,position_admin_libelle) VALUES(6,1,6,'MDL','2026-06-01','Pr\u00e9sent'),(7,2,1,'OTHER','2026-06-01','Pr\u00e9sent')")
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL',date_grade='2026-06-01',position_admin_libelle='Pr\u00e9sent' WHERE agent_id=1")
            cur.execute('DELETE FROM agent_passation_grade WHERE agent_id=1')
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'MDL','2026-06-01')")
    return settings,connect,params,personnel,AgentDossierService(settings)


def snapshots(connect,params,columns):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT p.annee_mois,{columns} FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=1 ORDER BY p.annee_mois')
            return dict(cur.fetchall())


@pytest.mark.parametrize('component',['grade','diplome','affectation'])
@pytest.mark.parametrize('later',[False,True])
def test_december_correction_updates_all_closed_months_and_keeps_later_events(isolated_mysql,monkeypatch,component,later):
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('6')
    if component=='grade':
        if later:
            dossier.add_passation(row,grade_libelle='MAJ',date_passation=date(2026,10,10))
        row=personnel.get_by_id('6')
        personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,7,20),ndecision='OG RETRO'))
        field='c.grade_libelle'; expected='ADM'; successor='MAJ'
    elif component=='diplome':
        if later:dossier.add_diplome(row,categorie='MILITAIRE',libelle='OCTOBER',date_obtention=date(2026,10,10))
        dossier.add_diplome(personnel.get_by_id('6'),categorie='MILITAIRE',libelle='JULY',date_obtention=date(2026,7,20))
        field='c.diplome_libelle'; expected='JULY'; successor='OCTOBER'
    else:
        if later:dossier.add_affectation(row,structure_code='OCTOBER',date_affectation=date(2026,10,10))
        dossier.add_affectation(personnel.get_by_id('6'),structure_code='JULY',date_affectation=date(2026,7,20))
        field='c.structure_code'; expected='JULY'; successor='OCTOBER'
    actual=snapshots(connect,params,field)
    assert actual=={f'2026-{m:02}':successor if later and m>=10 else expected for m in range(7,13)}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT statut FROM sme_periode WHERE annee_mois<'2026-12'")
            assert all(r[0]=='Ferm\u00e9' for r in cur.fetchall())
            cur.execute('SELECT grade_libelle FROM carriere_situation WHERE agent_id=2')
            assert cur.fetchone()[0]=='OTHER'
            cur.execute("SELECT payload,personnel_id FROM audit_journal WHERE action='dated_history_projection' ORDER BY id DESC LIMIT 1")
            payload,key=cur.fetchone()
            payload=json.loads(payload) if isinstance(payload,str) else payload
            assert key=='42' and payload['component']==component
            assert any(m['month']=='2026-07' and m['closed'] and m['before']!=m['after'] for m in payload['months'])
    from sme_gend.services.personnel_service import PersonnelService
    july=PersonnelService(replace(settings,mysql_view_mois='2026-07')).get_by_id('5')
    assert getattr(july,{'grade':'grade','diplome':'diplome','affectation':'structure'}[component])==expected


def test_closed_absence_dates_project_and_display_in_every_affected_month(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    events=AgentDossierEventsService(settings)
    events.add_conge('42',type_code='ADMIN',date_debut=date(2026,7,1),date_fin=date(2026,8,15),quota_jours=None,observation=None,sme_mois='2026-12')
    actual=snapshots(connect,params,'c.position_admin_libelle')
    assert actual['2026-07']=='Cong\u00e9Admin' and actual['2026-08']=='Pr\u00e9sent'
    assert all(actual[f'2026-{m:02}']=='Pr\u00e9sent' for m in range(9,13))
    for month in ('2026-07','2026-08'):
        assert '42' in MySQLNumeriqueRepository(replace(settings,mysql_view_mois=month)).load_latest_conges_for_period()
    old=events.conges('42')[0]
    events.update_conge('42',old.id,expected=old,type_code='ADMIN',date_debut=date(2026,7,1),date_fin=date(2026,8,31),quota_jours=None,observation=None,sme_mois='2026-12')
    assert snapshots(connect,params,'c.position_admin_libelle')['2026-08']=='Cong\u00e9Admin'


def test_audit_failure_rolls_back_closed_month_correction_and_history(isolated_mysql,monkeypatch):
    from sme_gend.services import sensitive_audit_log
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('6')
    before=snapshots(connect,params,'c.grade_libelle')
    append=sensitive_audit_log._append_entry
    def fail_projection_audit(*args,**kwargs):
        if kwargs.get('action')=='dated_history_projection':raise RuntimeError('synthetic projection audit failure')
        return append(*args,**kwargs)
    monkeypatch.setattr(sensitive_audit_log,'_append_entry',fail_projection_audit)
    with pytest.raises(RuntimeError,match='synthetic projection audit'):
        personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,7,20)))
    assert snapshots(connect,params,'c.grade_libelle')==before
    assert not any(p.grade_libelle=='ADM' for p in dossier.list_passations(row))


def test_closed_position_details_follow_dates_across_months(isolated_mysql,monkeypatch):
    from sme_gend.services.position_admin_events import add_position_event
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    add_position_event(settings,'42',position_admin='D\u00e9tach\u00e9',date_debut=date(2026,7,1),date_fin=date(2026,8,15),
                       lieu='SYN PLACE',reference_decision='OG CORRECTION',observation='SYN',mois_sme='2026-12')
    actual=snapshots(connect,params,'c.position_admin_libelle')
    assert actual['2026-07']=='D\u00e9tach\u00e9' and actual['2026-08']=='Pr\u00e9sent'
    details=MySQLNumeriqueRepository(replace(settings,mysql_view_mois='2026-08')).load_position_events_for_period()
    assert details[('42','d\u00e9tach\u00e9')][2:]==('SYN PLACE','OG CORRECTION','SYN')
