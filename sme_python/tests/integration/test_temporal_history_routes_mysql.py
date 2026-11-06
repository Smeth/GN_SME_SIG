"""Regression coverage for dates across monthly snapshots."""
from datetime import date
import pytest
from test_restore_mysql import isolated_mysql
from test_grade_date_routing_mysql import calendar, period_row


@pytest.mark.parametrize('family,source,stamp', [
    ('diplome','2026-09',date(2026,10,1)), ('diplome','2026-11',date(2026,8,20)),
    ('affectation','2026-09',date(2026,10,1)), ('affectation','2026-11',date(2026,8,20)),
])
def test_dated_history_reprojects_existing_months(isolated_mysql, monkeypatch, family, source, stamp):
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch, source)
    row = period_row(personnel, '1' if source=='2026-09' else '4')
    if family == 'diplome':
        dossier.add_diplome(row,categorie='MILITAIRE',libelle='SYNTHETIC NEW',date_obtention=stamp)
        field='diplome'
        assert any(d.libelle=='SYNTHETIC NEW' for d in dossier.list_diplomes(row))
    else:
        dossier.add_affectation(row,structure_code='SYNTHETIC NEW',date_affectation=stamp)
        field='structure'
        assert any(a.structure_code=='SYNTHETIC NEW' for a in dossier.list_affectations(row))
    if source=='2026-09':
        assert getattr(period_row(personnel,'1'),field) is None
        assert getattr(period_row(personnel,'3'),field)=='SYNTHETIC NEW'
    else:
        assert getattr(period_row(personnel,'4'),field)=='SYNTHETIC NEW'
        assert all(getattr(period_row(personnel,rid),field)=='SYNTHETIC NEW' for rid in ('2','1','3'))


@pytest.mark.parametrize('family', ['mission','desertion'])
def test_event_belongs_to_effective_month(isolated_mysql, monkeypatch, family):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch)
    events=AgentDossierEventsService(settings)
    if family=='mission':
        event_id=events.add_mission('42',date_debut='2026-10-01',date_fin=None,nature='SYNTHETIC',lieu=None,reference_dossier=None,observation=None)
        table,column='agent_mission','date_debut'
    else:
        event_id=events.add_desertion('42',date_desertion='2026-10-01',observation='SYNTHETIC')
        table,column='agent_desertion','date_desertion'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT p.annee_mois,e.{column} FROM {table} e JOIN sme_periode p ON p.id=e.sme_periode_id WHERE e.id=%s',(event_id,))
            assert cur.fetchone()==('2026-10',date(2026,10,1))


def test_future_stage_is_excluded_from_earlier_month(isolated_mysql, monkeypatch):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch)
    events=AgentDossierEventsService(settings)
    events.add_stage('42',type_code='LOCAL',date_debut='2026-10-01',date_fin='2026-10-30',
                     nature='SYNTHETIC FUTURE',lieu=None,observation=None)
    september=MySQLNumeriqueRepository(settings).load_stages_loc()
    assert '42' not in september

@pytest.mark.parametrize('family', ['diplome','affectation'])
def test_history_move_and_delete_reprojects_months(isolated_mysql,monkeypatch,family):
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    row=period_row(personnel,'1')
    if family=='diplome':
        field='diplome'
        eid=dossier.add_diplome(row,categorie='MILITAIRE',libelle='SYN NEW',date_obtention=date(2026,10,1))
        entry=next(e for e in dossier.list_diplomes(row) if e.id==eid)
        dossier.update_diplome(row,eid,expected=entry,categorie='MILITAIRE',libelle='SYN NEW',date_obtention=date(2026,8,20))
    else:
        field='structure'
        eid=dossier.add_affectation(row,structure_code='SYN NEW',date_affectation=date(2026,10,1))
        entry=next(e for e in dossier.list_affectations(row) if e.id==eid)
        dossier.update_affectation(row,eid,expected=entry,structure_code='SYN NEW',date_affectation=date(2026,8,20))
    assert all(getattr(period_row(personnel,rid),field)=='SYN NEW' for rid in ('2','1','3','4'))
    row=period_row(personnel,'1')
    if family=='diplome':
        entry=next(e for e in dossier.list_diplomes(row) if e.id==eid)
        dossier.delete_diplome(row,eid,expected=entry)
    else:
        entry=next(e for e in dossier.list_affectations(row) if e.id==eid)
        dossier.delete_affectation(row,eid,expected=entry)
    assert all(getattr(period_row(personnel,rid),field) is None for rid in ('2','1','3','4'))
