"""Large synthetic rehearsal, guarded by the disposable instance fixture."""
from dataclasses import replace
from datetime import date
import json
from time import perf_counter
import pytest
from test_restore_mysql import isolated_mysql
from test_sme_export_snapshot_mysql import seed
from test_publication_registry_mysql import install_registry


@pytest.mark.parametrize('dated_membership',[False,True])
def test_twelve_month_projection_for_six_thousand_agents_is_batched_and_reconciles(isolated_mysql,monkeypatch,tmp_path,dated_membership):
    import pymysql
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.migration_reconciliation import capture_month_reconciliation
    from sme_gend.services.sme_month_closure import shift_sme_month_yyyy_mm
    settings,connect,params=seed(isolated_mysql)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO agent(id,legacy_access_id,noms) VALUES(%s,%s,%s)',
                [(number,str(100000+number),f'SYNTHETIC {number:05}') for number in range(2,6001)])
            cursor.executemany("INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle,structure_code,position_admin_libelle,date_debut,date_fin) VALUES(%s,1,'MDL','DPF','Présent','2026-11-01','2026-11-30')",
                [(number,) for number in range(2,6001)])
            # One genuinely new dated promotion. It must not change November.
            cursor.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(2,'ADC','2026-12-15')")
    install_registry(settings,tmp_path,monkeypatch)
    if dated_membership:
        from sme_gend.services.membership_projection import rebuild_imported_membership
        with connect(**params) as connection:
            connection.begin()
            with connection.cursor() as cursor:
                cursor.executemany('INSERT INTO agent_effectif_movement(agent_id,category,movement_kind,effective_date,'
                    'recorded_at,recorded_by) VALUES(%s,\'GAIN\',\'SYNTHETIC\',\'2026-11-01\',UTC_TIMESTAMP(6),\'test\')',
                    [(aid,) for aid in range(1,6001)])
            connection.commit()
        with publication_write_barrier(settings):
            rebuild_imported_membership(settings,'2026-11',[1])
    before=capture_month_reconciliation(settings,'2026-11')
    queries=[]
    original=pymysql.cursors.Cursor.execute
    def measured(cursor,query,args=None):
        queries.append(str(query).split(None,1)[0])
        return original(cursor,query,args)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',measured)
    timings=[]
    source='2026-11'
    for index in range(12):
        target=shift_sme_month_yyyy_mm(source,delta_months=1)
        count_before=len(queries); started=perf_counter()
        with publication_write_barrier(settings):
            with period_state_write(settings,reason='synthetic_rehearsal',mois=target):
                result=prepare_month_projection(settings,target,source)
        elapsed=perf_counter()-started
        assert result.created_rows==6000 and result.projected_agents==6000
        assert len(queries)-count_before<500,'Projection performs per-agent SQL instead of batches.'
        timings.append({'month':target,'seconds':round(elapsed,3),'queries':len(queries)-count_before})
        source=target
    unchanged=capture_month_reconciliation(settings,'2026-11')
    assert before.fingerprint==unchanged.fingerprint and len(before.rows)==6000
    december=capture_month_reconciliation(settings,'2026-12')
    assert december.rows['2']['grade']=='ADC' and before.rows['2']['grade']=='MDL'
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent_passation_grade')
            assert cursor.fetchone()[0]==1,'Preparing months duplicated source events.'
            cursor.execute('SELECT COUNT(*) FROM agent_effectif_movement')
            assert cursor.fetchone()[0]==(6000 if dated_membership else 0)
    report={'agents':6000,'months':12,'historical_source_unchanged':True,'events_duplicated':False,
            'dated_membership_events':6000 if dated_membership else 0,
            'timings':timings,'total_seconds':round(sum(item['seconds'] for item in timings),3)}
    path=tmp_path/'projection_performance.reconciliation.json'
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('REHEARSAL_METRICS '+json.dumps(report))
