from dataclasses import replace
import csv
import json
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.services.activity_audit_log import ActivityAuditQuery,search_activity_page,export_activity_csv
from sme_gend.repositories.mysql_connection import MySQLConnection


def seed(settings,count):
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO audit_journal(ts_utc,action,actor,module,payload) VALUES('2026-11-02 12:00:00','SYNTHETIC',%s,'system',%s)",
                [('=1+1',json.dumps({'index':i,'complete':'x'*700})) for i in range(count)])


def test_pages_are_stable_for_equal_timestamps_and_new_inserts(journal):
    settings,_=journal;seed(settings,7)
    query=ActivityAuditQuery(limit=3)
    first=search_activity_page(query,settings=settings)
    assert first.has_more and len(first)==3
    seed(settings,1)
    second=search_activity_page(replace(query,cursor_before=first.next_cursor,snapshot_id=first.snapshot_id),settings=settings)
    third=search_activity_page(replace(query,cursor_before=second.next_cursor,snapshot_id=first.snapshot_id),settings=settings)
    ids=[r['journal_id'] for page in (first,second,third) for r in page]
    assert len(ids)==7 and len(set(ids))==7 and ids==sorted(ids,reverse=True)
    assert not third.has_more


def test_export_includes_all_pages_full_payload_and_neutralizes_formulas(journal,tmp_path):
    settings,_=journal;seed(settings,2005)
    dest=tmp_path/'journal.csv'
    assert export_activity_csv(ActivityAuditQuery(limit=50),dest,settings=settings)==2005
    with dest.open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.reader(stream,delimiter=';'))
    assert len(rows)==2006
    assert all(row[4]=="'=1+1" for row in rows[1:])
    assert len(json.loads(rows[-1][-1])['complete'])==700


def test_export_failure_leaves_existing_file_untouched(journal,tmp_path,monkeypatch):
    settings,_=journal;seed(settings,5)
    dest=tmp_path/'journal.csv';dest.write_text('ORIGINAL',encoding='utf-8')
    def failed(*a,**kw):raise RuntimeError('SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.activity_audit_log.search_activity_page',failed)
    with pytest.raises(RuntimeError):export_activity_csv(ActivityAuditQuery(),dest,settings=settings)
    assert dest.read_text(encoding='utf-8')=='ORIGINAL'
    assert not list(tmp_path.glob('.journal_*'))


def test_dossier_opening_is_logged_with_user_agent_and_month(journal,monkeypatch):
    from types import SimpleNamespace
    from sme_gend.services.activity_audit_log import log_dossier_opened,search_activity_audit
    settings,_=journal
    settings=replace(settings,mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYNTHETIC USER')
    row=SimpleNamespace(id='7',noms='SYNTHETIC AGENT',satellite_join_key=lambda:'LEGACY-7')
    log_dossier_opened(settings,row)
    entries=search_activity_audit(ActivityAuditQuery(personnel_id='7'),settings=settings)
    assert len(entries)==1
    assert entries[0]['actor']=='SYNTHETIC USER'
    assert entries[0]['action']=='personnel_dossier_opened'
    assert entries[0]['sme_mois_context']=='2026-09'
    assert entries[0]['legacy_agent_id']=='LEGACY-7'
