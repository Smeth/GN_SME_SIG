"""Cross-module journal filters on disposable MySQL only."""
import json
from datetime import date
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services import audit_log,doc_gen_log

@pytest.fixture
def logs(journal,monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_auth_journal',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    return journal

def test_other_filters_lot33_auth_empty_central_result_is_empty(logs,monkeypatch,tmp_path):
    path=tmp_path/'auth.jsonl';path.write_text(json.dumps(dict(module='auth',actor='OLD BASE'))+'\n',encoding='utf-8')
    monkeypatch.setattr(audit_log,'_user_activity_path',lambda:path)
    assert audit_log.search_auth_activity(actor='NO MATCH')==[]

def test_other_filters_lot33_doc_view_finds_match_older_than_display_limit(logs,isolated_mysql,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views import doc_gen_log_view as ui
    app=QApplication.instance() or QApplication([])
    settings,connect,params=isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO audit_journal(ts_utc,action,module,payload) VALUES('2024-02-01','doc_gen','doc_gen',%s)",
                (json.dumps(dict(doc_type='attestation',mois='2024-02',persons=['ÉLOÏSE'])),))
            cur.executemany("INSERT INTO audit_journal(ts_utc,action,module,payload) VALUES('2026-10-01','doc_gen','doc_gen',%s)",
                [(json.dumps(dict(doc_type='numerique',mois='2026-09',persons=['OTHER'])),)]*2000)
    monkeypatch.setattr(ui,'run_in_background',lambda parent,work,done,**kw:done(work()),raising=False)
    view=ui.DocGenLogView();view._configure_access_ui()
    view._filter_type.setCurrentIndex(view._filter_type.findData('attestation'))
    view.refresh()
    assert view._model.rowCount()==1
    assert 'ÉLOÏSE' in view._model._rows[0][3]
    assert view._combo_year.findData(2024)>=0
    view.close()

def test_other_filters_lot33_document_order_uses_event_timestamp(logs):
    settings,insert=logs
    insert(module='doc_gen',action='doc_gen',ts='2026-10-02',payload=dict(doc_type='attestation',persons=['NEW']))
    insert(module='doc_gen',action='doc_gen',ts='2026-10-01',payload=dict(doc_type='attestation',persons=['OLD']))
    assert doc_gen_log.read_doc_log(max_entries=1)[0]['persons']==['NEW']


@pytest.mark.parametrize('filter_kwargs',[
    dict(type_key='attestation'),dict(mois_filter='2024-02'),dict(search_query='eloise'),
    dict(search_query='Attestation',type_labels={'attestation':'Attestation','numerique':'Situation numérique'}),
])
def test_other_filters_lot33_document_filters_run_before_limit(logs,filter_kwargs):
    settings,insert=logs
    insert(module='doc_gen',action='doc_gen',ts='2024-02-01',payload=dict(doc_type='attestation',mois='2024-02',persons=['ÉLOÏSE']))
    insert(module='doc_gen',action='doc_gen',ts='2026-10-01',payload=dict(doc_type='numerique',mois='2026-09',persons=['OTHER']))
    rows=doc_gen_log.read_doc_log(max_entries=1,**filter_kwargs)
    assert len(rows)==1 and rows[0]['persons']==['ÉLOÏSE']

def test_other_filters_lot33_history_access_is_scoped_before_limit_and_catalog(logs,monkeypatch):
    settings,insert=logs
    insert(module='doc_gen',action='doc_gen',ts='2024-02-01',payload=dict(doc_type='attestation',mois='2024-02',persons=['ALLOWED']))
    insert(module='doc_gen',action='doc_gen',ts='2026-10-01',payload=dict(doc_type='numerique',mois='2026-09',persons=['RESTRICTED']))
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:False)
    assert doc_gen_log.read_doc_log(max_entries=1)[0]['persons']==['ALLOWED']
    assert doc_gen_log.doc_log_months()==['2024-02']
    assert doc_gen_log.read_doc_log(type_key='numerique')==[]

@pytest.mark.parametrize('text',['A%','A_','A=','A\\'])
def test_other_filters_lot33_document_literal_search(logs,text):
    settings,insert=logs
    insert(module='doc_gen',action='doc_gen',payload=dict(doc_type='attestation',persons=[text]))
    insert(module='doc_gen',action='doc_gen',payload=dict(doc_type='attestation',persons=['AX']))
    rows=doc_gen_log.read_doc_log(search_query=text)
    assert len(rows)==1 and rows[0]['persons']==[text]

def test_other_filters_lot33_document_null_payload_and_actor(logs):
    settings,insert=logs;insert(module='doc_gen',action='doc_gen',actor='SYNTHETIC',payload={})
    rows=doc_gen_log.read_doc_log(search_query='SYNTHETIC')
    assert len(rows)==1 and rows[0]['actor']=='SYNTHETIC'

def test_other_filters_lot33_auth_filters_and_order_are_preserved(logs):
    settings,insert=logs
    insert(module='auth',action='login',actor='ÉLOÏSE',ts='2026-10-01 12:00:00',payload=dict(detail='LOGIN'))
    insert(module='auth',action='logout',actor='OTHER',ts='2026-10-02 12:00:00',payload=dict(detail='LOGOUT'))
    rows=audit_log.search_auth_activity(actor='eloise',action='login',date_from=date(2026,10,1),date_to=date(2026,10,1),limit=1)
    assert len(rows)==1 and rows[0]['actor']=='ÉLOÏSE'

def test_other_filters_lot33_reads_do_not_mutate_journal(logs,isolated_mysql):
    settings,insert=logs;insert(module='doc_gen',action='doc_gen',payload=dict(doc_type='attestation',mois='2026-09'))
    _,connect,params=isolated_mysql
    def snapshot():
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM audit_journal ORDER BY id');return cur.fetchall()
    before=snapshot()
    doc_gen_log.read_doc_log();doc_gen_log.doc_log_months();audit_log.search_auth_activity()
    assert snapshot()==before
