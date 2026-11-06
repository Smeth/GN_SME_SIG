from datetime import date
import pytest
from unittest.mock import Mock
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services
from sme_gend.services.bulk_diploma import add_bulk_diploma


def setup(isolated_mysql,monkeypatch):
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,position_admin_libelle) "
                        "VALUES(2,2,1,'GND','Présent')")
    return settings,personnel,dossier


def values(category='MILITAIRE'):
    return dict(categorie=category,libelle='BREVET GROUPÉ',date_obtention=date(2026,9,20),
                lieu='Centre fictif',mention='Bien',etablissement='École fictive',obs='Note fictive')


def test_bulk_diploma_commits_each_agent_and_repeated_action_does_not_duplicate(isolated_mysql,monkeypatch):
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    rows=[personnel.get_by_id(str(i)) for i in (1,2,1)]
    progress=Mock()
    result=add_bulk_diploma(settings,rows,values(),progress)
    assert (result.added,result.existing,result.duplicate_agents,result.errors)==(2,0,1,[])
    assert progress.call_count==3
    for i in (1,2):
        row=personnel.get_by_id(str(i))
        assert row.diplome=='BREVET GROUPÉ'
        entries=dossier.list_diplomes(row)
        assert len(entries)==1
        assert entries[0].mention=='Bien' and entries[0].etablissement=='École fictive'
    repeat=add_bulk_diploma(settings,rows[:2],values())
    assert (repeat.added,repeat.existing,repeat.errors)==(0,2,[])
    assert all(len(dossier.list_diplomes(personnel.get_by_id(str(i))))==1 for i in (1,2))


def test_projection_failure_rolls_back_only_failed_agent(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_service import AgentDossierService
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    original=AgentDossierService._project_carriere
    def project(self,row,**kwargs):
        if row.id=='2':
            raise RuntimeError('SYNTHETIC projection failure')
        return original(self,row,**kwargs)
    monkeypatch.setattr(AgentDossierService,'_project_carriere',project)
    result=add_bulk_diploma(settings,[personnel.get_by_id('1'),personnel.get_by_id('2')],values())
    assert result.added==1 and len(result.errors)==1
    assert 'SYNTHETIC' in result.errors[0]
    assert len(dossier.list_diplomes(personnel.get_by_id('1')))==1
    assert dossier.list_diplomes(personnel.get_by_id('2'))==[]


def test_civil_diploma_is_saved_and_its_display_rule_explained(isolated_mysql,monkeypatch):
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    result=add_bulk_diploma(settings,[personnel.get_by_id('1')],values('CIVIL'))
    assert result.added==1 and not result.errors
    assert 'civil' in result.details[0]
    assert dossier.list_diplomes(personnel.get_by_id('1'))[0].categorie=='CIVIL'


def test_locked_month_writes_nothing(isolated_mysql,monkeypatch):
    from types import SimpleNamespace
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.evaluate_editing_state_for_settings',
        lambda settings:SimpleNamespace(locked=True,detail='SYNTHETIC closed month'))
    result=add_bulk_diploma(settings,[personnel.get_by_id('1'),personnel.get_by_id('2')],values())
    assert result.added==0 and len(result.errors)==2
    assert all(dossier.list_diplomes(personnel.get_by_id(str(i)))==[] for i in (1,2))


def test_permission_revoked_during_batch_stops_following_agents(isolated_mysql,monkeypatch):
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    allowed=[True]
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda pid:allowed[0])
    def completed(message):
        allowed[0]=False
    result=add_bulk_diploma(settings,[personnel.get_by_id('1'),personnel.get_by_id('2')],values(),completed)
    assert result.added==1 and len(result.errors)==1
    assert len(dossier.list_diplomes(personnel.get_by_id('1')))==1
    assert dossier.list_diplomes(personnel.get_by_id('2'))==[]


def test_audit_failure_rolls_back_diploma_and_list_projection(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_service import AgentDossierService
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    original=personnel.get_by_id('1')
    def fail(*args,**kwargs):
        raise RuntimeError('SYNTHETIC audit failure')
    monkeypatch.setattr(AgentDossierService,'_log_history',fail)
    result=add_bulk_diploma(settings,[original],values())
    assert result.added==0 and len(result.errors)==1
    assert dossier.list_diplomes(original)==[]
    assert personnel.get_by_id('1').diplome==original.diplome


def test_two_simultaneous_batches_do_not_create_duplicate_diplomas(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    barrier=Barrier(2)
    def work():
        barrier.wait(timeout=10)
        return add_bulk_diploma(settings,[row],values())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:work(),range(2)))
    assert sum(result.added for result in results)==1
    assert sum(result.existing for result in results)==1
    assert not any(result.errors for result in results)
    assert len(dossier.list_diplomes(row))==1


@pytest.mark.parametrize('kind',['older','future'])
def test_older_or_future_military_is_saved_without_wrong_display(isolated_mysql,monkeypatch,kind):
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    dossier.add_diplome(row,categorie='MILITAIRE',libelle='CURRENT',date_obtention=date(2026,9,21))
    payload=values()
    if kind=='future':
        payload['date_obtention']=date(2026,10,1)
    result=add_bulk_diploma(settings,[row],payload)
    assert result.added==1 and not result.errors
    assert len(dossier.list_diplomes(row))==2
    assert personnel.get_by_id('1').diplome=='CURRENT'
    assert ('postérieure' if kind=='future' else 'plus récent') in result.details[0]


def test_postcommit_mirror_failure_does_not_report_false_rollback(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_service import AgentDossierService
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    settings,personnel,dossier=setup(isolated_mysql,monkeypatch)
    original=AgentDossierService._log_history
    def log(self,*args,**kwargs):
        original(self,*args,**kwargs)
        def fail():
            raise OSError('SYNTHETIC local mirror failure')
        after_mysql_commit(fail)
    monkeypatch.setattr(AgentDossierService,'_log_history',log)
    result=add_bulk_diploma(settings,[personnel.get_by_id('1')],values())
    assert result.added==1 and not result.errors
    assert 'confirmé dans MySQL' in result.details[0]
    assert len(dossier.list_diplomes(personnel.get_by_id('1')))==1
