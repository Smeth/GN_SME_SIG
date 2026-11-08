"""Measured service progress, transaction boundaries and batched reporting."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sme_gend.services.operation_progress import operation_progress, progress_reporting


def test_commit_completion_waits_for_the_owning_transaction(monkeypatch):
    from sme_gend.services import operation_commit_progress as module
    callbacks=[];events=[]
    monkeypatch.setattr(module,'has_import_transaction',lambda settings:True)
    monkeypatch.setattr(module,'after_mysql_commit',callbacks.append)
    with progress_reporting(events.append):
        with operation_progress((('write','Écriture'),('commit','Validation MySQL'))) as progress:
            progress.finish('write')
            progress.start('commit')
            module.finish_after_commit(object(),progress,'commit',detail='Validation confirmée')
        assert events[-1].percent<100
        assert events[-1].steps[-1].state=='running'
        assert len(callbacks)==1
        callbacks[0]()
    assert events[-1].percent==100


def test_discarded_commit_callback_never_reports_success(monkeypatch):
    from sme_gend.services import operation_commit_progress as module
    callbacks=[];events=[]
    monkeypatch.setattr(module,'has_import_transaction',lambda settings:True)
    monkeypatch.setattr(module,'after_mysql_commit',callbacks.append)
    with progress_reporting(events.append),operation_progress((('commit','Validation'),)) as progress:
        progress.start('commit')
        module.finish_after_commit(object(),progress,'commit',detail='Terminé')
    callbacks.clear()  # Owning transaction rolled back; hooks must not execute.
    assert all(event.percent<100 for event in events)


def test_precedence_keeps_career_rows_before_reading_grade_catalog():
    from sme_gend.services.carriere_reconciliation import sync_ordre_tri_from_pref_ord
    from sme_gend.services.mysql_ref_etl import RefEtlCache,_cf
    class Cursor:
        def __init__(self):self.rows=[];self.updated=[]
        def execute(self,sql,params=()):
            if 'FROM ref_grade' in sql:self.rows=[(5,15)]
            elif sql.lstrip().startswith('SELECT'):self.rows=[(10,'MDL'),(11,'Unknown')]
            else:self.updated.append(params);self.rows=[]
        def fetchall(self):
            result,self.rows=self.rows,[]
            return result
    cache=RefEtlCache();cache.grade_by_code[_cf('MDL')]=5
    cursor=Cursor();events=[]
    with progress_reporting(events.append):
        assert sync_ordre_tri_from_pref_ord(cursor,cache)==1
    assert cursor.updated==[(15,15,10)]
    assert events[-1].current==events[-1].total==2


@pytest.mark.parametrize('fail',[False,True])
def test_dossier_progress_counts_agents_and_batches_notifications(monkeypatch,fail):
    from sme_gend.services import agent_dossier_import_enrichment as module
    history={str(i):('',[object()]) for i in range(123)}
    monkeypatch.setattr(module,'MySQLAgentRepository',lambda settings:SimpleNamespace(load_carriere_history_bulk=lambda:history))
    monkeypatch.setattr(module,'MySQLAgentDossierRepository',lambda settings:object())
    calls=[];events=[]
    def agent(repo,leg,snaps,totals):
        assert all(event.percent<100 for event in events)
        if fail and len(calls)==51:raise RuntimeError('synthetic dossier failure')
        calls.append(leg);totals.diplomes+=1
    monkeypatch.setattr(module,'_backfill_dossier_agent',agent)
    with progress_reporting(events.append):
        if fail:
            with pytest.raises(RuntimeError,match='synthetic dossier failure'):
                module.backfill_dossier_from_all_snapshots(object())
            assert all(event.percent<100 for event in events)
            assert max(event.current or 0 for event in events)==50
        else:
            result=module.backfill_dossier_from_all_snapshots(object())
            assert result['dossier_diplomes']==123
            assert events[-1].percent==100
            assert {(event.current,event.total) for event in events}>={(50,123),(100,123),(123,123)}
    assert len(events)<20


def test_fonction_backfill_finishes_after_transaction_exit(monkeypatch):
    from sme_gend.services import fonction_backfill as module
    events=[];linked=[];committed=[]
    plan=module.BackfillPlan(('synthetic',),object(),(),tuple(module.BackfillItem('U',str(i),True) for i in range(103)))
    service=SimpleNamespace(settings=object(),validate_links_snapshot=lambda links:None,
        fonction_observed_usage=lambda:(),fonction_ensure_catalog_and_link=lambda code,label:linked.append((code,label)))
    monkeypatch.setattr(module,'_context',lambda service:plan.context)
    @contextmanager
    def transaction(settings):
        yield
        assert events[-1].active_key=='commit'
        assert all(event.percent<100 for event in events)
        committed.append(True)
    monkeypatch.setattr(module,'shared_reference_write',transaction)
    with progress_reporting(events.append):assert module.apply_backfill(service,plan)==103
    assert len(linked)==103 and committed==[True]
    assert events[-1].percent==100
    assert {(event.current,event.total) for event in events}>={(100,103),(103,103)}


def test_maintenance_fingerprint_reports_tables_and_batches_without_extra_reads():
    from sme_gend.services.automatic_maintenance import _capture
    class Cursor:
        def __init__(self):self.rows=[];self.statements=[]
        def execute(self,sql,params=()):
            self.statements.append(sql)
            if 'information_schema.TABLES' in sql:
                self.rows=[(name,'InnoDB','BASE TABLE') for name in ('agent','sme_periode','carriere_situation','app_config')]
            elif 'information_schema.COLUMNS' in sql:
                self.rows=[('id','int','NO',None,'')]
            elif sql.startswith('SELECT JSON_ARRAY'):
                self.rows=[('[1]',)]*(1001 if 'FROM `agent` ' in sql else 1)
            else:raise AssertionError(sql)
        def fetchall(self):
            result,self.rows=self.rows,[]
            return result
        def fetchmany(self,size):
            result,self.rows=self.rows[:size],self.rows[size:]
            return result
    cursor=Cursor();events=[]
    with progress_reporting(events.append):result=_capture(cursor,locking=True)
    assert dict((name,count) for name,_,count,_ in result)=={'agent':1001,'sme_periode':1,'carriere_situation':1,'app_config':1}
    assert len(cursor.statements)==9  # One metadata read, then columns + data for four tables.
    assert len(events)<25
    assert events[-1].percent==100 and events[-1].current==events[-1].total==4
    assert any('500 ligne(s) lue(s)' in event.detail for event in events)
