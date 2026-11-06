"""Audit searches on a disposable native MySQL schema only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services import sensitive_audit_log as audit

@pytest.fixture
def journal(isolated_mysql,monkeypatch):
    settings,connect,params=isolated_mysql
    sql=(Path(__file__).resolve().parents[2]/'data/schema/mysql_app_config.sql').read_text(encoding='utf-8')
    from sme_gend.repositories.mysql_common import _split_sql_statements
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for statement in _split_sql_statements(sql):cur.execute(statement)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:True)
    def insert(*,actor='ADMIN',pid='12',nom='ÉLOI',detail='DIPLOME',ts='2026-10-01 12:00:00',payload=None,module='sensitive_data',action='dossier_diplome_add',month='2026-09'):
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO audit_journal(ts_utc,action,actor,module,personnel_id,sme_mois_context,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (ts,action,actor,module,pid,month,json.dumps(payload if payload is not None else dict(personnel_nom=nom,detail=detail),ensure_ascii=False)))
    return settings,insert

def test_audit_lot32_free_text_finds_author(journal):
    _,insert=journal;insert(actor='UNIQUE AUTHOR')
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(text='UNIQUE AUTHOR'))
    assert len(rows)==1

def test_audit_lot32_name_filter_only_searches_agent_name(journal):
    _,insert=journal;insert(nom='ALPHA',detail='Contact BETA')
    assert audit.search_sensitive_audit(audit.SensitiveAuditQuery(personnel_nom='BETA'))==[]

def test_audit_lot32_number_is_exact(journal):
    _,insert=journal;insert(pid='12');insert(pid='112')
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(personnel_id='12'))
    assert [r['personnel_id'] for r in rows]==['12']

def test_audit_lot32_accents_do_not_discard_sql_matches(journal):
    _,insert=journal;insert()
    assert len(audit.search_sensitive_audit(audit.SensitiveAuditQuery(personnel_nom='eloi')))==1

def test_audit_lot32_payload_cannot_replace_authoritative_metadata(journal):
    _,insert=journal;insert(payload=dict(actor='FORGED',action='FORGED',module='OTHER',personnel_id='999',ts_utc='2000-01-01',sme_mois_context='2000-01',detail='SYNTHETIC'))
    row=audit.search_sensitive_audit(audit.SensitiveAuditQuery())[0]
    assert (row['actor'],row['action'],row['module'],row['personnel_id'],row['sme_mois_context'])==('ADMIN','dossier_diplome_add','sensitive_data','12','2026-09')
    assert row['ts_utc'].startswith('2026-10-01')

def test_audit_lot32_result_contains_complete_history_payload(journal):
    _,insert=journal;payload=dict(personnel_nom='ALPHA',detail='AFFECTATION',date_debut='2025-01-01',ndecision='ACT-TEST')
    insert(payload=payload)
    row=audit.search_sensitive_audit(audit.SensitiveAuditQuery())[0]
    assert row.get('payload')==payload

def test_audit_lot32_timestamp_has_explicit_utc(journal):
    _,insert=journal;insert()
    row=audit.search_sensitive_audit(audit.SensitiveAuditQuery())[0]
    assert datetime.fromisoformat(row['ts_utc']).utcoffset().total_seconds()==0

def test_audit_lot32_results_are_chronological_before_limiting(journal):
    _,insert=journal;insert(pid='NEW',ts='2026-10-02 12:00:00');insert(pid='OLD',ts='2026-10-01 12:00:00')
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(limit=1))
    assert [r['personnel_id'] for r in rows]==['NEW']

def test_audit_lot32_literal_percent_does_not_expand_filter(journal):
    _,insert=journal;insert(actor='ADMIN');insert(actor='ADMIN%')
    rows=AppConfigRepository(journal[0]).search_audit_journal(module='sensitive_data',actor='ADMIN%')
    assert [r['actor'] for r in rows]==['ADMIN%']

def test_audit_lot32_no_local_rows_when_central_filter_is_empty(journal,monkeypatch):
    _,insert=journal;insert(nom='NEW DATABASE')
    monkeypatch.setattr(audit,'_parse_local_entries',lambda n:[dict(personnel_nom='OLD DATABASE',module='sensitive_data')])
    assert audit.search_sensitive_audit(audit.SensitiveAuditQuery(personnel_nom='OLD DATABASE'))==[]


@pytest.mark.parametrize('needle',['ADMIN_','ADMIN%','ADMIN=','ADMIN\\'])
def test_audit_lot32_literal_search_characters(journal,needle):
    _,insert=journal;insert(actor='ADMINOTHER');insert(actor=needle)
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(actor=needle))
    assert [row['actor'] for row in rows]==[needle]

def test_audit_lot32_all_filters_are_combined_before_limit(journal):
    from datetime import date
    _,insert=journal
    insert(actor='ALPHA',pid='12',nom='ÉLOI',detail='SELECTED',ts='2026-10-01 12:00:00')
    insert(actor='BETA',pid='112',nom='BETA',detail='SELECTED',ts='2026-10-01 13:00:00')
    insert(actor='ALPHA',pid='12',nom='ÉLOI',detail='SELECTED',month='2026-08')
    insert(actor='ALPHA',pid='12',nom='ÉLOI',detail='SELECTED',module='auth')
    query=audit.SensitiveAuditQuery(actor='alpha',personnel_id='12',personnel_nom='eloi',sme_mois='2026-09',text='selected',
        action='dossier_diplome_add',date_from=date(2026,10,1),date_to=date(2026,10,1),limit=1)
    rows=audit.search_sensitive_audit(query)
    assert len(rows)==1 and rows[0]['personnel_id']=='12' and rows[0]['sme_mois_context']=='2026-09'

@pytest.mark.parametrize('field,value',[('action','dossier_diplome'),('personnel_id','GEN-'),('sme_mois_context','2026-09')])
def test_audit_lot32_free_text_searches_visible_metadata(journal,field,value):
    _,insert=journal;insert(pid='GEN-1')
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(text=value))
    assert len(rows)==1 and value in rows[0][field]

def test_audit_lot32_inclusive_local_date_bounds(journal):
    from datetime import date,timedelta
    from sme_gend.repositories.app_config_repository import audit_date_bounds
    day=date(2026,10,2);start,end=audit_date_bounds(day,day)
    _,insert=journal
    insert(pid='BEFORE',ts=start-timedelta(milliseconds=1));insert(pid='START',ts=start)
    insert(pid='LAST',ts=end-timedelta(milliseconds=1));insert(pid='AFTER',ts=end)
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(date_from=day,date_to=day))
    assert [row['personnel_id'] for row in rows]==['LAST','START']

def test_audit_lot32_year_filter_is_sme_context_not_action_date(journal):
    _,insert=journal;insert(month='2025-02');insert(month='2026-09')
    rows=audit.search_sensitive_audit(audit.SensitiveAuditQuery(sme_mois='2025'))
    assert len(rows)==1 and rows[0]['sme_mois_context']=='2025-02' and rows[0]['ts_utc'].startswith('2026')

def test_audit_lot32_missing_table_is_explicit_not_local_fallback(journal,isolated_mysql):
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE audit_journal')
    with pytest.raises(RuntimeError,match='journal central'):
        audit.search_sensitive_audit(audit.SensitiveAuditQuery())

@pytest.mark.parametrize('batch',[False,True])
def test_audit_lot32_aware_write_normalizes_to_utc(journal,batch):
    from datetime import timedelta
    repo=AppConfigRepository(journal[0])
    entry=dict(action='dossier_diplome_add',module='sensitive_data',ts_utc=datetime(2026,10,2,0,30,tzinfo=timezone(timedelta(hours=1))),payload=dict(detail='SYNTHETIC'))
    if batch:repo.append_audit_batch([entry])
    else:repo.append_audit(**entry)
    row=audit.search_sensitive_audit(audit.SensitiveAuditQuery())[0]
    assert row['ts_utc']=='2026-10-01T23:30:00+00:00'

def test_audit_lot32_same_timestamp_has_stable_order(journal):
    _,insert=journal;insert(pid='FIRST');insert(pid='SECOND')
    assert [row['personnel_id'] for row in audit.search_sensitive_audit(audit.SensitiveAuditQuery())]==['SECOND','FIRST']

def test_audit_lot32_auth_consumer_keeps_existing_flat_details(journal):
    from sme_gend.services.audit_log import search_auth_activity
    _,insert=journal;insert(module='auth',actor='SYNTHETIC USER',detail='LOGIN SYNTHETIC')
    rows=search_auth_activity(text='SYNTHETIC USER')
    assert len(rows)==1 and rows[0]['detail']=='LOGIN SYNTHETIC'
    assert rows[0]['payload']['detail']=='LOGIN SYNTHETIC'
