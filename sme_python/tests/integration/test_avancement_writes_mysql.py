"""Avancement : isolated native MySQL, no real career/archive data."""
from dataclasses import replace
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql


@pytest.fixture
def advancement(isolated_mysql,monkeypatch):
    from sme_gend.repositories import mysql_common
    from sme_gend.services import auth_context
    from sme_gend.services.avancement_service import AvancementService
    from sme_gend.models.avancement import default_rules
    from sme_gend.repositories.mysql_avancement_repository import _regle_params
    settings,connect,params=isolated_mysql
    monkeypatch.setattr(mysql_common,'ensure_mysql_schema',lambda settings:None)
    monkeypatch.setattr(auth_context,'get_app_auth',lambda:None)
    monkeypatch.setattr(auth_context,'resolve_audit_actor',lambda:'SYNTHETIC')
    sql=(Path(__file__).resolve().parents[2]/'data/schema/mysql_avancement.sql').read_text(encoding='utf-8')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(mysql_common._split_sql_statements(sql)[0])
            cur.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,updated_by VARCHAR(128),updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB')
            row=default_rules()[1]
            cur.execute('INSERT INTO avancement_regle VALUES('+','.join(['%s']*15)+')',_regle_params(row))
    return settings,connect,params,AvancementService(settings)


def save(service,new,expected):
    return service.save_regle(new,expected=expected)


def reset(service,book):
    return service.reset_regles(expected=book.snapshot)


def test_avancement_lot28_stale_rule_cannot_replace_another_posts_change(advancement):
    settings,connect,params,service=advancement
    shown=service.regles()['MDC']
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE avancement_regle SET annees_grade=7 WHERE grade_cible='MDC'")
    with pytest.raises(ValueError,match='modifi|chang'):save(service,replace(shown,annees_grade=5),shown)
    assert service.regles()['MDC'].annees_grade==7


def test_avancement_lot28_reset_requires_the_displayed_collection(advancement):
    settings,connect,params,service=advancement
    shown=service.regles()
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE avancement_regle SET annees_grade=7 WHERE grade_cible='MDC'")
    with pytest.raises(ValueError,match='modifi|chang'):reset(service,shown)
    assert service.regles()['MDC'].annees_grade==7


def test_avancement_lot28_rule_commit_publishes_a_shared_revision(advancement):
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,service=advancement
    before=read_data_revision(settings);shown=service.regles()['MDC']
    save(service,replace(shown,annees_grade=5),shown)
    assert read_data_revision(settings)!=before


def test_avancement_lot28_rule_version_is_required_even_for_direct_calls(advancement):
    settings,connect,params,service=advancement
    shown=service.regles()['MDC']
    with pytest.raises(ValueError,match='Version'):service._repo.save_regle(replace(shown,annees_grade=6))
    assert service.regles()['MDC'].annees_grade==4



def observe(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM avancement_regle ORDER BY grade_cible');rules=cur.fetchall()
            cur.execute("SELECT payload FROM app_config WHERE config_key='shared_data_revision'");row=cur.fetchone()
    import json
    revision=json.loads(row[0]).get('token','') if row else ''
    return rules,revision


@pytest.mark.parametrize('invalid',['missing','database','grade','malformed'])
def test_avancement_lot28_invalid_row_versions_are_rejected_before_sql(advancement,monkeypatch,invalid):
    from unittest.mock import MagicMock
    settings,connect,params,service=advancement;shown=service.regles()['MDC']
    proposed=replace(shown,annees_grade=6)
    if invalid=='missing':shown=None
    elif invalid=='database':shown=replace(shown,_write_database=('other',))
    elif invalid=='grade':shown=replace(shown,grade_cible='ADJ')
    else:shown=replace(shown,write_token='broken')
    entry=MagicMock(side_effect=AssertionError('Version check reached SQL'))
    monkeypatch.setattr(service._repo,'_connect',entry)
    with pytest.raises(ValueError):save(service,proposed,shown)
    entry.assert_not_called()


@pytest.mark.parametrize('operation',['save','reset'])
def test_avancement_lot28_permission_is_enforced_in_the_repository(advancement,monkeypatch,operation):
    from unittest.mock import MagicMock
    settings,connect,params,service=advancement;shown=service.regles()
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    entry=MagicMock(side_effect=AssertionError('Permission check reached SQL'))
    monkeypatch.setattr(service._repo,'_connect',entry)
    with pytest.raises(PermissionError):
        if operation=='save':save(service,replace(shown['MDC'],annees_grade=5),shown['MDC'])
        else:reset(service,shown)
    entry.assert_not_called()


@pytest.mark.parametrize('operation',['save','reset'])
@pytest.mark.parametrize('commit',[False,True])
def test_avancement_lot28_rule_data_and_revision_follow_the_outer_commit(advancement,operation,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,service=advancement;shown=service.regles();before=observe(connect,params)
    try:
        with mysql_import_transaction(settings):
            if operation=='save':save(service,replace(shown['MDC'],annees_grade=5),shown['MDC'])
            else:reset(service,shown)
            assert observe(connect,params)==before
            if not commit:raise ValueError('synthetic caller rollback')
    except ValueError:assert not commit
    after=observe(connect,params)
    if commit:assert after[0]!=before[0] and after[1]!=before[1]
    else:assert after==before


@pytest.mark.parametrize('operation',['save','reset'])
def test_avancement_lot28_notification_failure_rolls_back_all_rule_changes(advancement,monkeypatch,operation):
    settings,connect,params,service=advancement;shown=service.regles();before=observe(connect,params)
    def fail(*args,**kwargs):raise OSError('synthetic revision failure')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',fail)
    with pytest.raises(OSError):
        if operation=='save':save(service,replace(shown['MDC'],annees_grade=5),shown['MDC'])
        else:reset(service,shown)
    assert observe(connect,params)==before


def test_avancement_lot28_reset_insert_failure_restores_rules_deleted_earlier(advancement):
    settings,connect,params,service=advancement;shown=service.regles();before=observe(connect,params)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TRIGGER synthetic_rule_veto BEFORE INSERT ON avancement_regle FOR EACH ROW BEGIN IF NEW.grade_cible='COL' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic reset veto'; END IF; END")
    with pytest.raises(Exception,match='synthetic reset veto'):reset(service,shown)
    assert observe(connect,params)==before


def test_avancement_lot28_caught_failure_uses_savepoint_without_losing_other_outer_work(advancement,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params,service=advancement;shown=service.regles();before=observe(connect,params)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('CREATE TABLE synthetic_outer(id INT PRIMARY KEY) ENGINE=InnoDB')
    def fail(*args,**kwargs):raise OSError('synthetic revision failure')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',fail)
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('INSERT INTO synthetic_outer VALUES(1)')
        with pytest.raises(OSError):save(service,replace(shown['MDC'],annees_grade=5),shown['MDC'])
    assert observe(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT id FROM synthetic_outer');assert cur.fetchall()==((1,),)


@pytest.mark.parametrize('target',['MDC','ADJ'])
def test_avancement_lot28_two_simultaneous_edits_have_exactly_one_winner(advancement,monkeypatch,target):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services import data_sync
    settings,connect,params,service=advancement;shown=service.regles()[target];barrier=Barrier(2)
    original=data_sync.publish_data_change;notifications=[]
    def publish(*args,**kwargs):notifications.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',publish)
    def write(value):
        barrier.wait(timeout=10)
        try:save(service,replace(shown,annees_grade=value),shown);return ('saved',value)
        except ReferenceConflictError:return ('conflict',value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(write,value) for value in (5,7)];results=[job.result(timeout=20) for job in jobs]
    assert sorted(row[0] for row in results)==['conflict','saved'] and len(notifications)==1
    assert service.regles()[target].annees_grade==next(row[1] for row in results if row[0]=='saved')


def test_avancement_lot28_independent_grades_can_be_saved_from_the_same_display(advancement):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    settings,connect,params,service=advancement;shown=service.regles();barrier=Barrier(2)
    def write(key):barrier.wait(timeout=10);save(service,replace(shown[key],annees_grade=6),shown[key])
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(write,key) for key in ('MDC','ADJ')]
        for job in jobs:job.result(timeout=20)
    assert all(service.regles()[key].annees_grade==6 for key in ('MDC','ADJ'))


def test_avancement_lot28_old_repeatable_read_snapshot_cannot_authorize_a_stale_write(advancement):
    from concurrent.futures import ThreadPoolExecutor
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,service=advancement
    with mysql_import_transaction(settings):
        shown=service.regles()['MDC']
        with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(save,service,replace(shown,annees_grade=7),shown).result(timeout=20)
        with pytest.raises(ValueError,match='modifi'):save(service,replace(shown,annees_grade=5),shown)
    assert service.regles()['MDC'].annees_grade==7


def test_avancement_lot28_noop_rule_and_fresh_default_reset_do_not_notify(advancement,monkeypatch):
    settings,connect,params,service=advancement;shown=service.regles()
    notifications=[]
    from sme_gend.services import data_sync
    publish=data_sync.publish_data_change
    def counted(*args,**kwargs):notifications.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',counted)
    save(service,shown['MDC'],shown['MDC']);assert notifications==[]
    reset(service,service.regles());assert len(notifications)==1
    reset(service,service.regles());assert len(notifications)==1


def test_avancement_lot28_deleted_override_cannot_be_resurrected_by_an_old_form(advancement):
    settings,connect,params,service=advancement;shown=service.regles()['MDC']
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("DELETE FROM avancement_regle WHERE grade_cible='MDC'")
    with pytest.raises(ValueError,match='supprim'):save(service,replace(shown,annees_grade=7),shown)
    assert service.regles()['MDC'].annees_grade==4
    assert observe(connect,params)[0]==()


@pytest.mark.parametrize('change',['added','deleted'])
def test_avancement_lot28_reset_detects_collection_insertions_and_deletions(advancement,change):
    settings,connect,params,service=advancement;shown=service.regles()
    if change=='added':save(service,replace(shown['ADJ'],annees_grade=6),shown['ADJ'])
    else:
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("DELETE FROM avancement_regle WHERE grade_cible='MDC'")
    before=observe(connect,params)
    with pytest.raises(ValueError,match='modifi'):reset(service,shown)
    assert observe(connect,params)==before


def test_avancement_lot28_multiple_changes_share_one_notification_when_batched(advancement,monkeypatch):
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.services import data_sync
    settings,connect,params,service=advancement;shown=service.regles();notifications=[]
    publish=data_sync.publish_data_change
    def counted(*args,**kwargs):notifications.append(True);return publish(*args,**kwargs)
    monkeypatch.setattr(data_sync,'publish_data_change',counted)
    with shared_reference_write(settings):
        save(service,replace(shown['MDC'],annees_grade=6),shown['MDC'])
        save(service,replace(shown['ADJ'],annees_grade=6),shown['ADJ'])
    assert len(notifications)==1



@pytest.mark.parametrize('invalid',['missing','database','malformed'])
def test_avancement_lot28_reset_rejects_missing_or_foreign_snapshot(advancement,invalid):
    settings,connect,params,service=advancement;expected=service.regles().snapshot
    if invalid=='missing':expected=None
    elif invalid=='database':expected=replace(expected,database=('foreign',))
    else:expected=replace(expected,revision='bad')
    before=observe(connect,params)
    with pytest.raises(ValueError):service._repo.reset_regles(expected=expected)
    assert observe(connect,params)==before


def test_avancement_lot28_a_second_old_reset_cannot_replace_the_first_commit(advancement):
    settings,connect,params,service=advancement;shown=service.regles()
    reset(service,shown);before=observe(connect,params)
    with pytest.raises(ValueError,match='modifi'):reset(service,shown)
    assert observe(connect,params)==before


def test_avancement_lot28_initialization_does_not_seed_or_rewrite_rules(advancement):
    from sme_gend.services.mysql_import_period import apply_avancement_schema
    settings,connect,params,service=advancement;before=observe(connect,params)
    apply_avancement_schema(settings)
    assert observe(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('avancement_campagne','avancement_quota','avancement_ligne')")
            assert cur.fetchone()==(0,)


def test_avancement_lot28_empty_database_still_shows_default_rules_without_writing_them(advancement):
    from sme_gend.models.avancement import default_rules
    from sme_gend.services.mysql_import_period import apply_avancement_schema
    settings,connect,params,service=advancement
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE avancement_regle')
    apply_avancement_schema(settings)
    assert observe(connect,params)[0]==()
    shown=service.regles();assert tuple(shown.values())==default_rules()
    save(service,replace(shown['MDC'],annees_grade=7),shown['MDC'])
    assert service.regles()['MDC'].annees_grade==7


def test_avancement_lot28_legacy_records_are_neither_altered_nor_used(advancement,monkeypatch):
    from datetime import date
    from sme_gend.models.avancement import AgentAvancementInput
    from sme_gend.services.mysql_import_period import apply_avancement_schema
    settings,connect,params,service=advancement
    # Old-style tables deliberately lack the later trimestre column. The active
    # initializer must not migrate, delete or revive any of their contents.
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE avancement_campagne(id INT PRIMARY KEY,annee INT,statut VARCHAR(20)) ENGINE=InnoDB')
            cur.execute("INSERT INTO avancement_campagne VALUES(1,2022,'cloturee')")
            cur.execute('CREATE TABLE avancement_quota(id INT PRIMARY KEY,campagne_id INT,grade_cible VARCHAR(16),quota INT) ENGINE=InnoDB')
            cur.execute("INSERT INTO avancement_quota VALUES(1,1,'MDC',1)")
            cur.execute('CREATE TABLE avancement_ligne(id INT PRIMARY KEY,campagne_id INT,agent_id INT,grade_cible VARCHAR(16),statut VARCHAR(20)) ENGINE=InnoDB')
            cur.execute("INSERT INTO avancement_ligne VALUES(1,1,1,'MDC','nomme')")
    synthetic=AgentAvancementInput(1,'SYNTHETIC','MDL',date(2018,1,1),date(2016,1,1),date(1990,1,1),diplomes=('OPJ',),maniere_servir='favorable')
    monkeypatch.setattr(service._repo,'load_agents',lambda:[synthetic])
    result=service.vigilance(annee=2022,trimestre=1,on=date(2022,1,1))
    assert result[0].statut=='eligible'
    shown=service.regles();save(service,replace(shown['MDC'],annees_grade=5),shown['MDC'])
    reset(service,service.regles());apply_avancement_schema(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM avancement_campagne');assert cur.fetchall()==((1,2022,'cloturee'),)
            cur.execute('SELECT * FROM avancement_quota');assert cur.fetchall()==((1,1,'MDC',1),)
            cur.execute('SELECT * FROM avancement_ligne');assert cur.fetchall()==((1,1,1,'MDC','nomme'),)


def test_avancement_lot28_schema_failure_is_explicit_and_leaves_data_unchanged(advancement,monkeypatch):
    from sme_gend.services import mysql_import_period as init
    settings,connect,params,service=advancement;before=observe(connect,params)
    monkeypatch.setattr('sme_gend.repositories.mysql_common._split_sql_statements',lambda text:[])
    with pytest.raises(RuntimeError,match='indisponible'):init.apply_avancement_schema(settings)
    assert observe(connect,params)==before
