"""Cycle et tâches : MySQL jetable vérifié, aucune donnée réelle."""
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database
from sme_gend.services.month_cycle_snapshot import load_month_cycle
from sme_gend.services.workflow_task_service import WorkflowTaskService


def test_cycle_catalog_reads_real_period_statuses_and_active_month(alert_database):
    settings, connect, params = alert_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (annee_mois,date_dernier_jour_mois,statut) VALUES "
                        "('2026-01','2026-01-31','Fermé'),('2026-09','2026-09-30','Ouvert')")
    snapshot=load_month_cycle(settings)
    assert snapshot.active_month=='2026-09'
    assert {row.mois:row.statut for row in snapshot.sme_rows}=={'2026-01':'Fermé','2026-09':'Ouvert'}


def test_cycle_catalog_tasks_are_complete_and_keep_write_versions(alert_database):
    settings, connect, params = alert_database
    service=WorkflowTaskService(settings)
    service.list_open()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO workflow_task (mois,task_kind,title,detail) VALUES ('2026-09','manual',%s,'SYNTHETIC')",
                            [(f'SYNTHETIC TASK {i}',) for i in range(250)])
    rows=service.list_open(limit=None)
    assert len(rows)==250
    assert all(row.write_token and row._write_database for row in rows)
    assert len(service.list_open(limit=30))==30
