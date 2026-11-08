"""Progress against actual commits on the guarded disposable MySQL instance."""
import pytest
from test_restore_mysql import isolated_mysql
from test_publication_registry_mysql import migrate

from sme_gend.services.operation_progress import progress_reporting


def test_dated_movement_progress_finishes_after_commit_and_counts_periods(isolated_mysql,tmp_path,monkeypatch):
    from sme_gend.services.dated_effectif_movement import create_movement
    settings,connect,params,_=migrate(isolated_mysql,tmp_path,monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(annee_mois,date_dernier_jour_mois,statut) VALUES"
                           "('2025-10','2025-10-31','Fermé'),('2026-01','2026-01-31','Fermé')")
    events=[];visible=[]
    def observe(event):
        events.append(event)
        if event.percent==100:
            with connect(**params) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM agent_effectif_movement')
                    visible.append(cursor.fetchone()[0])
    with progress_reporting(observe):
        create_movement(settings,1,'PERTE','SYNTHETIC','2025-02-05',reason='Synthetic historical evidence')
    assert visible==[1] and events[-1].percent==100
    assert any(event.active_key=='project' and event.current==event.total==3 for event in events)


@pytest.mark.parametrize('rollback',[False,True])
def test_nested_movement_progress_waits_for_external_commit(isolated_mysql,tmp_path,monkeypatch,rollback):
    from sme_gend.services.dated_effectif_movement import create_movement
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,_=migrate(isolated_mysql,tmp_path,monkeypatch)
    events=[]
    with progress_reporting(events.append):
        try:
            with mysql_import_transaction(settings):
                create_movement(settings,1,'PERTE','SYNTHETIC','2026-11-01')
                assert all(event.percent<100 for event in events)
                if rollback:raise RuntimeError('synthetic caller rollback')
        except RuntimeError as error:
            if not rollback or str(error)!='synthetic caller rollback':raise
    assert (events[-1].percent==100) is not rollback
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent_effectif_movement')
            assert cursor.fetchone()[0]==int(not rollback)


def test_reconciliation_progress_and_grade_order_are_visible_only_after_commit(isolated_mysql,tmp_path,monkeypatch):
    from test_sme_export_snapshot_mysql import seed
    from sme_gend.services.carriere_reconciliation import reconcile_carriere_referentiels
    settings,connect,params=seed(isolated_mysql)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:True)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ref_grade(code,libelle,pref_ord) VALUES('CNE','CNE',77) "
                           "ON DUPLICATE KEY UPDATE pref_ord=77")
            cursor.execute('UPDATE carriere_situation SET ordre_tri=0,presseance=0 WHERE id=1')
    events=[];visible=[]
    def observe(event):
        events.append(event)
        if event.percent==100:
            with connect(**params) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT ordre_tri,presseance FROM carriere_situation WHERE id=1')
                    visible.append(cursor.fetchone())
    with progress_reporting(observe):
        result=reconcile_carriere_referentiels(settings,mois='2026-11',log_unresolved=False)
    assert result.ordre_tri_updated==1
    assert visible==[(77,77)] and events[-1].percent==100
