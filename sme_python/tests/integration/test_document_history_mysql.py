"""Synthetic document histories on a verified disposable native MySQL only."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from sme_gend.services import doc_gen_log as log
from sme_gend.repositories.app_config_repository import AppConfigRepository

@pytest.fixture
def journal(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=isolated_mysql
    from sme_gend.repositories.mysql_common import _split_sql_statements
    sql=(Path(__file__).resolve().parents[2]/'data/schema/mysql_app_config.sql').read_text(encoding='utf-8')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):cur.execute(stmt)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    monkeypatch.setattr(log,'_log_path',lambda:tmp_path/'documents.jsonl')
    return settings,connect,params

def entry(**kw):return dict(ts_utc='2026-09-03T12:30:00+00:00',doc_type='attestation',format='pdf',count=1,persons=['ÉLOÏSE'],dest='synthetic.pdf',mois='2026-08',actor='SYN AUTHOR',meta={})|kw

def insert(journal,row):
    AppConfigRepository(journal[0]).append_audit(action='doc_gen',module='doc_gen',actor=row['actor'],ts_utc=datetime.fromisoformat(row['ts_utc']),payload={k:v for k,v in row.items() if k not in ('ts_utc','actor')})

def test_document_history_search_finds_the_displayed_local_action_date(journal):
    row=entry();insert(journal,row)
    shown=datetime.fromisoformat(row['ts_utc']).astimezone().strftime('%d/%m/%Y')
    assert len(log.read_doc_log(search_query=shown))==1

def test_document_history_search_finds_the_displayed_type_and_format(journal):
    insert(journal,entry())
    assert len(log.read_doc_log(search_query='Attestation (PDF)',type_labels={'attestation':'Attestation'}))==1

def test_document_history_search_does_not_match_invisible_json_field_names(journal):
    insert(journal,entry())
    assert log.read_doc_log(search_query='doc_type')==[]

def test_document_history_success_writes_author_count_and_notification_atomically(journal,monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN WRITER')
    log.log_doc_generation(doc_type='numerique',format='xlsx',count=42,mois='2026-09',dest_path='synthetic.xlsx')
    row=log.read_doc_log()[0]
    assert (row['actor'],row['count'],row['mois'])==('SYN WRITER',42,'2026-09')
    assert getattr(log,'read_document_revision')(settings=journal[0])

def test_document_history_missing_central_table_is_not_reported_as_success(journal):
    with journal[1](**journal[2]) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE audit_journal')
    with pytest.raises(RuntimeError,match='historique'):
        log.log_doc_generation(doc_type='numerique',format='xlsx',dest_path='synthetic.xlsx')

def test_document_history_csv_never_deduplicates_against_another_local_database(journal,tmp_path):
    row=entry();log._log_path().write_text(json.dumps(row)+'\n',encoding='utf-8')
    path=tmp_path/'history.csv';path.write_text(log.export_doc_log_csv([row]),encoding='utf-8')
    result=log.merge_doc_log_from_csv(path)
    assert result.added==1 and len(log.read_doc_log())==1
    again=log.merge_doc_log_from_csv(path)
    assert again.added==0 and again.already_present==1

def test_document_history_csv_failure_rolls_back_the_entire_batch(journal,tmp_path,monkeypatch):
    path=tmp_path/'history.csv';path.write_text(log.export_doc_log_csv([entry(),entry(ts_utc='2026-09-04T12:30:00+00:00')]),encoding='utf-8')
    original=log._append_imported_mysql;calls=[]
    def append(*a,**kw):
        calls.append(1)
        if len(calls)==2:raise OSError('synthetic second row failure')
        return original(*a,**kw)
    monkeypatch.setattr(log,'_append_imported_mysql',append)
    with pytest.raises(Exception):log.merge_doc_log_from_csv(path)
    assert log.read_doc_log()==[]



def test_document_history_notification_failure_rolls_back_the_event(journal,monkeypatch):
    monkeypatch.setattr(log,'_publish_document_revision',lambda settings:(_ for _ in ()).throw(OSError('synthetic marker failure')))
    with pytest.raises(RuntimeError,match='historique'):
        log.log_doc_generation(doc_type='numerique',format='xlsx',dest_path='synthetic.xlsx')
    assert log.read_doc_log()==[] and log.read_document_revision(settings=journal[0])==''


def test_document_history_csv_concurrent_import_is_idempotent(journal,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'parallel.csv';path.write_text(log.export_doc_log_csv([entry()]),encoding='utf-8')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:log.merge_doc_log_from_csv(path,settings=journal[0]),range(2)))
    assert sorted(result.added for result in results)==[0,1]
    assert len(log.read_doc_log())==1


def test_document_history_csv_keeps_all_imported_persons_and_stays_idempotent(journal,tmp_path):
    path=tmp_path/'people.csv';row=entry(persons=['SYN '+str(i) for i in range(55)],count=55)
    path.write_text(log.export_doc_log_csv([row]),encoding='utf-8')
    assert log.merge_doc_log_from_csv(path).added==1
    assert log.merge_doc_log_from_csv(path).already_present==1
    assert len(log.read_doc_log()[0]['persons'])==55


def test_document_history_csv_deduplicates_beyond_the_old_50000_tail(journal,tmp_path):
    row=entry(ts_utc='2020-01-01T00:00:00+00:00');insert(journal,row)
    with journal[1](**journal[2]) as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO audit_journal(ts_utc,action,module,payload) VALUES(%s,%s,%s,%s)",[('2026-10-01','doc_gen','doc_gen',json.dumps(dict(doc_type='numerique',count=1)))]*50001)
    path=tmp_path/'old.csv';path.write_text(log.export_doc_log_csv([row]),encoding='utf-8')
    result=log.merge_doc_log_from_csv(path)
    assert result.added==0 and result.already_present==1


def test_document_history_scope_does_not_treat_an_underscore_as_any_character(journal):
    AppConfigRepository(journal[0]).append_audit(action='docXgen_other',module='OTHER',payload=dict(doc_type='attestation',persons=['NOT A DOCUMENT']))
    assert log.read_doc_log()==[]

@pytest.mark.parametrize('suffix',['',' %H:%M',' %H:%M:%S'])
def test_document_history_formatted_timestamp_search_is_combined_with_type_and_month(journal,suffix):
    row=entry();insert(journal,row)
    shown=datetime.fromisoformat(row['ts_utc']).astimezone().strftime('%d/%m/%Y'+suffix)
    assert len(log.read_doc_log(search_query=shown,type_key='attestation',mois_filter='2026-08'))==1
    assert log.read_doc_log(search_query=shown,type_key='numerique')==[]
    assert log.read_doc_log(search_query=shown,mois_filter='2026-09')==[]



def test_document_history_csv_preserves_distinct_actions_within_the_same_second(journal,tmp_path):
    rows=[entry(ts_utc='2026-09-03T12:30:00.123000+00:00'),entry(ts_utc='2026-09-03T12:30:00.456000+00:00')]
    path=tmp_path/'milliseconds.csv';path.write_text(log.export_doc_log_csv(rows),encoding='utf-8')
    assert log.merge_doc_log_from_csv(path).added==2
    assert log.merge_doc_log_from_csv(path).already_present==2
    assert len(log.read_doc_log())==2



def test_document_history_uses_explicit_export_source_not_a_changed_global_profile(journal,monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:pytest.fail('global profile must not replace captured export settings'))
    log.log_doc_generation(doc_type='numerique',format='xlsx',dest_path='synthetic.xlsx',settings=journal[0])
    assert len(log.read_doc_log(settings=journal[0]))==1


def test_document_history_import_records_the_importing_operator_separately(journal,monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN IMPORTER')
    path=tmp_path/'operator.csv';path.write_text(log.export_doc_log_csv([entry(actor='ORIGINAL AUTHOR')]),encoding='utf-8')
    log.merge_doc_log_from_csv(path)
    assert log.read_doc_log()[0]['actor']=='ORIGINAL AUTHOR'
    with journal[1](**journal[2]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT actor,payload FROM audit_journal WHERE action='document_history_import'")
            rows=cur.fetchall();assert len(rows)==1 and rows[0][0]=='SYN IMPORTER'
            assert json.loads(rows[0][1])['added']==1


def test_document_history_committed_event_survives_a_local_mirror_delivery_failure(journal,monkeypatch,tmp_path):
    from sme_gend.services import audit_mirror_recovery as recovery
    monkeypatch.setenv('SME_AUDIT_JSON_MIRROR','1')
    monkeypatch.setattr(recovery,'_node_file',lambda:tmp_path/'delivery/node_id')
    original=recovery.write_all
    def write(stream,payload):
        if str(getattr(stream,'name','')).endswith('documents.jsonl'):
            raise OSError('synthetic mirror unavailable')
        return original(stream,payload)
    monkeypatch.setattr(recovery,'write_all',write)
    log.log_doc_generation(doc_type='numerique',format='xlsx',dest_path='synthetic.xlsx')
    assert len(log.read_doc_log())==1 and log.read_document_revision(settings=journal[0])
    monkeypatch.setattr(recovery,'write_all',original)
    assert recovery.recover_pending(journal[0])=={'delivered':1,'failed':0}
    assert recovery.recover_pending(journal[0])=={'delivered':0,'failed':0}
    assert len(log._log_path().read_text(encoding='utf-8').splitlines())==1
