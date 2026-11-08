"""Automatic maintenance against disposable MySQL schemas only."""
import importlib.util
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql,_lot7_dossier
from sme_gend.services.data_sync import read_data_revision


def setup(isolated_mysql,monkeypatch):
    settings,connect,params,*_=_lot7_dossier(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda **kw:'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.sot_maintenance._active_mois',lambda:'2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            # The shared writer initializes this coordination row even on its first no-op.
            cur.execute("INSERT INTO app_config(config_key,payload) VALUES('shared_data_revision','{\"token\":\"\"}')")
            cur.execute("INSERT INTO ref_structure(id,code,designation) VALUES(1,'SYN','ORIGINAL')")
            cur.execute("UPDATE carriere_situation SET structure_id=1,structure_code='SYN' WHERE id=1")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,structure_id,structure_code,grade_libelle) VALUES(2,2,1,1,'SYN','MDL')")
    return settings,connect,params


def state(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            out=[]
            for table in ('ref_structure','carriere_situation','agent_diplome','agent_passation_grade','app_config'):
                cur.execute('SELECT * FROM '+table+' ORDER BY 1');out.append(cur.fetchall())
            return out


def minimal_options():return dict(migrate_org=False,backfill_rsa=False,dedupe_rsa=False,rebuild_layout=False,
    resync_dossier=False,resync_fk=False,backfill_fonction_structure=False,mois='2026-09')


def test_automatic_lot31_late_layout_failure_rolls_back_first_stage(isolated_mysql,monkeypatch):
    from sme_gend.services import referentiel_maintenance as maintenance
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    before=state(connect,params)
    def mutate(cur,*a):cur.execute("UPDATE ref_structure SET designation='CHANGED'");return 1
    def fail(*a,**kw):raise RuntimeError('synthetic layout failure')
    monkeypatch.setattr(maintenance,'migrate_misplaced_org_nodes',mutate)
    monkeypatch.setattr(maintenance,'rebuild_liste_present_layout',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        maintenance.run_referentiel_maintenance(settings,**(minimal_options()|dict(migrate_org=True,rebuild_layout=True)))
    assert state(connect,params)==before


def test_automatic_lot31_projection_is_one_transaction_for_all_agents(isolated_mysql,monkeypatch):
    from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("INSERT INTO agent_diplome(agent_id,libelle_libre,date_obtention) VALUES(1,'NEW SYNTHETIC DIPLOMA','2020-01-01')")
    before=state(connect,params);original=MySQLAgentDossierRepository.sync_carriere_from_dossier
    def fail_second(self,aid,cid,**kw):
        if aid==2:raise RuntimeError('synthetic second agent failure')
        return original(self,aid,cid,**kw)
    monkeypatch.setattr(MySQLAgentDossierRepository,'sync_carriere_from_dossier',fail_second)
    with pytest.raises(RuntimeError,match='synthetic'):
        project_carriere_from_dossier_batch(settings,mois='2026-09',only_est_actuelle=False)
    assert state(connect,params)==before


def test_automatic_lot31_changed_pipeline_publishes_shared_revision(isolated_mysql,monkeypatch):
    from sme_gend.services import referentiel_maintenance as maintenance
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=read_data_revision(settings)
    def mutate(cur,*a):cur.execute("UPDATE ref_structure SET designation='CHANGED'");return 1
    monkeypatch.setattr(maintenance,'migrate_misplaced_org_nodes',mutate)
    maintenance.run_referentiel_maintenance(settings,**(minimal_options()|dict(migrate_org=True)))
    assert read_data_revision(settings)!=before


def test_automatic_lot31_purge_notifies_other_clients(isolated_mysql,monkeypatch):
    from sme_gend.services.sot_maintenance import purge_phantom_carriere_segments
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('INSERT INTO carriere_situation(agent_id,est_actuelle) VALUES(1,0)')
    before=read_data_revision(settings);out=purge_phantom_carriere_segments(settings)
    assert out['segments_removed']==1
    assert read_data_revision(settings)!=before


def dossier_cli():
    path=Path(__file__).resolve().parents[2]/'scripts/run_dossier_maintenance.py'
    spec=importlib.util.spec_from_file_location('dossier_maintenance_cli',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_automatic_lot31_dedupe_keeps_distinct_diplomas_on_the_same_date(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre) VALUES(1,'CIVIL','A'),(1,'MILITAIRE','B')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,obs) VALUES(1,'MDL','ACT A'),(1,'MDC','ACT B')")
    before=state(connect,params);out=dossier_cli().dedupe_dossier_tables(settings)
    assert out==dict(passations_supprimees=0,diplomes_supprimes=0)
    assert state(connect,params)==before



def job():
    from sme_gend.services.referentiel_maintenance import run_referentiel_maintenance
    return run_referentiel_maintenance


def writer(monkeypatch):
    def mutate(cur,*args):cur.execute("UPDATE ref_structure SET designation='CHANGED'");return 1
    monkeypatch.setattr('sme_gend.services.referentiel_maintenance.migrate_misplaced_org_nodes',mutate)
    return minimal_options()|dict(migrate_org=True)


def test_automatic_lot31_plan_is_read_only(isolated_mysql,monkeypatch):
    from sme_gend.services.automatic_maintenance import prepare_call
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params)
    plan=prepare_call(settings,job(),**minimal_options())
    assert len(plan.token)==64 and not plan.summary()['writes_applied']
    assert state(connect,params)==before


@pytest.mark.parametrize('table',['ref_structure','agent_diplome','carriere_situation','sme_periode'])
def test_automatic_lot31_stale_plan_refuses_before_modifying(isolated_mysql,monkeypatch,table):
    from sme_gend.services.automatic_maintenance import prepare_call
    settings,connect,params=setup(isolated_mysql,monkeypatch);options=writer(monkeypatch)
    plan=prepare_call(settings,job(),**options)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if table=='ref_structure':cur.execute("UPDATE ref_structure SET designation='OTHER'")
            elif table=='agent_diplome':cur.execute("INSERT INTO agent_diplome(agent_id,libelle_libre) VALUES(1,'OTHER DIPLOMA')")
            elif table=='carriere_situation':cur.execute("UPDATE carriere_situation SET obs='OTHER' WHERE id=1")
            else:cur.execute("UPDATE sme_periode SET statut='Ferme'")
    before=state(connect,params)
    with pytest.raises(ValueError,match='modifiees'):job()(settings,expected=plan,**options)
    assert state(connect,params)==before


@pytest.mark.parametrize('wrong',['database','options','action'])
def test_automatic_lot31_plan_cannot_be_reused_for_another_operation(isolated_mysql,monkeypatch,wrong):
    from dataclasses import replace
    from sme_gend.services.automatic_maintenance import prepare_call
    settings,connect,params=setup(isolated_mysql,monkeypatch);options=writer(monkeypatch)
    plan=prepare_call(settings,job(),**options);before=state(connect,params)
    if wrong=='database':plan=replace(plan,database=('other',123,'other'))
    elif wrong=='action':plan=replace(plan,action='other')
    else:options=options|dict(rebuild_layout=True)
    with pytest.raises(ValueError,match='incompatible'):job()(settings,expected=plan,**options)
    assert state(connect,params)==before


def test_automatic_lot31_noop_does_not_publish(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params)
    publication=[]
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',lambda *a,**kw:publication.append(True))
    job()(settings,**minimal_options())
    assert state(connect,params)==before and publication==[]


def test_automatic_lot31_signal_failure_rolls_back(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params);options=writer(monkeypatch)
    def fail(*a,**kw):raise RuntimeError('synthetic signal failure')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',fail)
    with pytest.raises(RuntimeError,match='synthetic'):job()(settings,**options)
    assert state(connect,params)==before


@pytest.mark.parametrize('commit',[True,False])
def test_automatic_lot31_visibility_and_caches_follow_real_commit(isolated_mysql,monkeypatch,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params);options=writer(monkeypatch)
    invalidations=[];monkeypatch.setattr('sme_gend.services.shared_reference_write._invalidate_reference_caches',lambda:invalidations.append(True))
    class Abort(Exception):pass
    try:
        with mysql_import_transaction(settings):
            job()(settings,**options)
            assert state(connect,params)==before and not invalidations
            if not commit:raise Abort()
    except Abort:pass
    if commit:assert state(connect,params)!=before and invalidations==[True]
    else:assert state(connect,params)==before and not invalidations


def test_automatic_lot31_old_repeatable_read_is_not_used_for_calculations(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params=setup(isolated_mysql,monkeypatch);options=writer(monkeypatch)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('SELECT * FROM ref_structure');cur.fetchall()
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE ref_structure SET designation='OTHER COMMIT'")
        with pytest.raises(ValueError,match='lecture ancienne'):job()(settings,**options)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT designation FROM ref_structure');assert cur.fetchone()==('OTHER COMMIT',)


def test_automatic_lot31_caught_child_failure_aborts_the_job(isolated_mysql,monkeypatch):
    from sme_gend.services.automatic_maintenance import automatic_write
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params)
    @automatic_write
    def child(settings):raise RuntimeError('synthetic nested failure')
    @automatic_write
    def parent(settings):
        from sme_gend.repositories.mysql_connection import MySQLConnection
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE ref_structure SET designation='CHANGED'")
        try:child(settings)
        except RuntimeError:pass
    with pytest.raises(RuntimeError,match='lot entier'):parent(settings)
    assert state(connect,params)==before


def test_automatic_lot31_two_same_plans_have_one_winner(isolated_mysql,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.services.automatic_maintenance import prepare_call
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params=setup(isolated_mysql,monkeypatch);options=writer(monkeypatch)
    plan=prepare_call(settings,job(),**options);barrier=Barrier(2)
    def apply(_):
        barrier.wait(timeout=5)
        try:job()(settings,expected=plan,**options);return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(apply,[1,2]))
    assert sorted(results)==[False,True] and read_data_revision(settings)


def test_automatic_lot31_dedupe_only_removes_exact_duplicates(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome(agent_id,libelle_libre) VALUES(1,'a'),(1,'a'),(1,'A')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,obs) VALUES(1,'MDL','ACT'),(1,'MDL','ACT')")
    assert dossier_cli().dedupe_dossier_tables(settings)==dict(passations_supprimees=1,diplomes_supprimes=1)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT libelle_libre FROM agent_diplome ORDER BY id');assert cur.fetchall()==(('a',),('A',))


def test_automatic_lot31_ddl_option_is_refused_in_the_dml_pipeline(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch);before=state(connect,params)
    with pytest.raises(ValueError,match='etape separee'):job()(settings,**minimal_options(),drop_legacy=True)
    assert state(connect,params)==before


def test_automatic_lot31_real_archive_alignment_is_idempotent(isolated_mysql,monkeypatch):
    from sme_gend.services.referentiel_maintenance import run_archives_referentiel_alignment
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    run_archives_referentiel_alignment(settings)
    before=state(connect,params);revision=read_data_revision(settings)
    run_archives_referentiel_alignment(settings)
    assert state(connect,params)==before and read_data_revision(settings)==revision



def test_automatic_lot31_historical_carry_does_not_change_active_month_flags(isolated_mysql,monkeypatch):
    from sme_gend.services.sot_maintenance import carry_forward_orphan_carriere
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ferme' WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-10','2026-10-31','Ouvert')")
            cur.execute('UPDATE carriere_situation SET est_actuelle=0')
            cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,est_actuelle,structure_code) VALUES(1,2,1,'SYN')")
    carry_forward_orphan_carriere(settings,mois='2026-09',policy='all')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sme_periode_id,est_actuelle FROM carriere_situation ORDER BY id')
            assert cur.fetchall()==((1,0),(1,0),(2,1))



@pytest.mark.parametrize('future_loss',[False,True])
def test_automatic_lot31_carry_uses_the_last_source_before_target_month(isolated_mysql,monkeypatch,future_loss):
    from sme_gend.services.sot_maintenance import carry_forward_orphan_carriere
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ferme' WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-10','2026-10-31','Ouvert'),(3,'2026-08','2026-08-31','Ferme')")
            cur.execute('DELETE FROM carriere_situation WHERE agent_id=2')
            cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle,est_actuelle) VALUES(2,3,'PAST',0),(2,2,'FUTURE',1)")
            if future_loss:cur.execute("INSERT INTO mouvement_effectif(agent_id,sme_periode_id,categorie_mouvement,motif) VALUES(2,2,'PERTE','MUTATION')")
    result=carry_forward_orphan_carriere(settings,mois='2026-09',policy='all')
    assert result['segments_created']==1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle,est_actuelle FROM carriere_situation WHERE agent_id=2 AND sme_periode_id=1')
            assert cur.fetchone()==('PAST',0)



def test_automatic_lot31_real_cli_preview_and_apply_use_the_same_revision(isolated_mysql,monkeypatch,capsys):
    import json
    from sme_gend.services import automatic_cli as cli
    settings,connect,params=setup(isolated_mysql,monkeypatch);writer(monkeypatch)
    monkeypatch.setattr(cli,'get_settings',lambda:settings)
    args=['--mois','2026-09','--skip-rsa','--skip-dedupe-rsa','--skip-layout','--skip-fk','--skip-dossier']
    before=state(connect,params)
    assert cli.run_cli('reference',args)==0
    plan=json.loads(capsys.readouterr().out);assert not plan['writes_applied'] and state(connect,params)==before
    assert cli.run_cli('reference',args+['--apply','--expected-revision',plan['revision']])==0
    result=json.loads(capsys.readouterr().out);assert result['writes_applied'] and state(connect,params)!=before


def test_automatic_lot31_decimal_changes_are_not_lost_in_plan_hashes(isolated_mysql,monkeypatch):
    from sme_gend.services.automatic_maintenance import prepare_call
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE ref_structure ADD COLUMN synthetic_precision DECIMAL(30,20)')
            cur.execute("UPDATE ref_structure SET synthetic_precision='1.12345678901234567890'")
    plan=prepare_call(settings,job(),**minimal_options())
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE ref_structure SET synthetic_precision='1.12345678901234567891'")
    with pytest.raises(ValueError,match='modifiees'):job()(settings,expected=plan,**minimal_options())



@pytest.mark.parametrize('kind',['dossier','coherence'])
@pytest.mark.parametrize('category',['MILITAIRE',None])
def test_automatic_lot31_real_composite_jobs_publish_once(isolated_mysql,monkeypatch,kind,category):
    from sme_gend.services import data_sync
    from sme_gend.services.automatic_jobs import run_dossier_maintenance,run_month_coherence
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            # The SME projects military diplomas. Legacy NULL categories are
            # read as CIVIL and must not silently become military diplomas.
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) "
                        "VALUES(1,%s,'SYNTHETIC DIPLOMA','2020-01-01')",(category,))
    publications=[];original=data_sync.publish_data_change
    def publish(*a,**kw):publications.append(True);return original(*a,**kw)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    if kind=='dossier':out=run_dossier_maintenance(settings,mois='2026-09',skip_dedupe=True)
    else:out=run_month_coherence(settings,mois='2026-09',skip_maintenance=True)
    assert out and publications==[True]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT diplome_libelle FROM carriere_situation WHERE id=1')
            assert cur.fetchone()==('SYNTHETIC DIPLOMA' if category=='MILITAIRE' else None,)
