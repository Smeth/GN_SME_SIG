"""Selected-month backups retain both hidden careers and real dated evidence."""
from datetime import date
from pathlib import Path
import hashlib
import json
from zipfile import ZipFile

import pytest
from test_restore_mysql import isolated_mysql
from test_publication_registry_mysql import migrate
from sme_gend.services import month_backup as months
from sme_gend.services import dated_effectif_movement as movements


def setup(isolated_mysql,tmp_path,monkeypatch):
    settings,connect,params,_ = migrate(isolated_mysql,tmp_path,monkeypatch)
    from sme_gend.services import backup_restore_service as backup
    monkeypatch.setattr(backup,'_collect_personnel_media_roots',lambda s:[])
    def safety(settings):
        path=tmp_path/'safety-month.zip'
        backup.create_full_backup(settings,path)
        return path
    monkeypatch.setattr(backup,'create_backup_to_default_dir',safety)
    return settings,connect,params


def test_loss_hidden_career_and_global_fact_are_portable_and_remapped(isolated_mysql,tmp_path,monkeypatch):
    settings,connect,params = setup(isolated_mysql,tmp_path,monkeypatch)
    original=movements.create_movement(settings,1,'PERTE','RETRAITE','2025-02-05',reason='Pièce historique')
    path=tmp_path/'november.zip'
    months.create_month_backup(settings,'2026-11',path)
    payload=months.inspect_month_backup(path)
    assert len(payload['tables']['carriere_situation'])==1  # Physical evidence is kept despite the loss.
    assert payload['tables']['sme_month_membership'][0]['is_member']==0
    assert payload['tables']['sme_month_membership'][0]['baseline_member']==1
    assert payload['tables']['agent_effectif_movement'][0]['effective_date']=={'$date':'2025-02-05'}
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM agent_effectif_movement')
            cursor.execute('DELETE FROM sme_month_membership')
            cursor.execute('DELETE FROM carriere_situation')
    months.restore_month_backup(settings,path,confirmed_month='2026-11')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,effective_date FROM agent_effectif_movement')
            new_id, stamp=cursor.fetchone()
            assert new_id!=original.id and stamp==date(2025,2,5)
            cursor.execute('SELECT is_member,baseline_member,event_id FROM sme_month_membership')
            assert cursor.fetchone()==(0,1,new_id)
            cursor.execute('SELECT COUNT(*) FROM carriere_situation')
            assert cursor.fetchone()[0]==1
            cursor.execute('SELECT annee_mois FROM sme_periode')
            assert cursor.fetchall()==(('2026-11',),)
    # Restoring the same bundle again reuses the real fact, without duplicating it.
    months.restore_month_backup(settings,path,confirmed_month='2026-11')
    assert len(movements.list_movements(settings))==1


@pytest.mark.parametrize('change',['opposite','extra'])
def test_restore_refuses_conflicting_or_extra_current_real_facts(isolated_mysql,tmp_path,monkeypatch,change):
    settings,connect,params = setup(isolated_mysql,tmp_path,monkeypatch)
    original=movements.create_movement(settings,1,'PERTE','RETRAITE','2026-11-01')
    path=tmp_path/'november.zip';months.create_month_backup(settings,'2026-11',path)
    if change=='opposite':
        movements.update_movement(settings,original,category='GAIN',movement_kind='RÉINTÉGRATION',
            effective_date='2026-11-01',reason='Correction réelle postérieure à la sauvegarde')
    else:
        movements.create_movement(settings,1,'GAIN','RÉINTÉGRATION','2026-11-02')
    before=movements.list_movements(settings)
    with pytest.raises(ValueError,match='mouvements réels|mouvement réel'):
        months.restore_month_backup(settings,path,confirmed_month='2026-11')
    assert movements.list_movements(settings)==before


def test_legacy_month_bundle_reapplies_current_real_loss_without_erasing_career(isolated_mysql,tmp_path,monkeypatch):
    settings,connect,params = setup(isolated_mysql,tmp_path,monkeypatch)
    path=tmp_path/'legacy.zip';months.create_month_backup(settings,'2026-11',path)
    payload=months.inspect_month_backup(path)
    payload['tables'].pop('agent_effectif_movement',None)
    payload['tables'].pop('sme_month_membership',None)
    payload.pop('dated_movement_agents',None)
    data=json.dumps(payload).encode('utf-8')
    with ZipFile(path,'w') as archive:
        archive.writestr(months.MEMBER,data)
        archive.writestr('month_sha256.txt',hashlib.sha256(data).hexdigest())
    real=movements.create_movement(settings,1,'PERTE','RETRAITE','2026-11-01')
    months.restore_month_backup(settings,path,confirmed_month='2026-11')
    assert movements.list_movements(settings)==[real]
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT is_member,baseline_member FROM sme_month_membership')
            assert cursor.fetchone()==(0,1)
            cursor.execute('SELECT COUNT(*) FROM carriere_situation')
            assert cursor.fetchone()[0]==1


@pytest.mark.parametrize('correction',['delete','move'])
def test_old_month_archive_cannot_resurrect_globally_retracted_fact(isolated_mysql,tmp_path,monkeypatch,correction):
    settings,connect,params = setup(isolated_mysql,tmp_path,monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-10','2026-10-31','Fermé')")
            cursor.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle) VALUES(1,2,'CNE')")
    original=movements.create_movement(settings,1,'PERTE','RETRAITE','2026-10-01',reason='Pièce historique')
    path=tmp_path/'october.zip';months.create_month_backup(settings,'2026-10',path)
    if correction=='delete':
        movements.delete_movement(settings,original,reason='Mouvement annulé après vérification')
    else:
        movements.update_movement(settings,original,category='PERTE',movement_kind='RETRAITE',
            effective_date='2026-11-01',reason='Date réelle corrigée')
    before=movements.list_movements(settings)
    with pytest.raises(ValueError,match='supprimé ou corrigé'):
        months.restore_month_backup(settings,path,confirmed_month='2026-10')
    assert movements.list_movements(settings)==before
