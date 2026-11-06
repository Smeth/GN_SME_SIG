import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sme_gend.services import automatic_cli as cli


@pytest.fixture
def plan_cli(monkeypatch):
    captured=[];settings=object()
    monkeypatch.setattr(cli,'get_settings',lambda:settings)
    monkeypatch.setattr(cli,'active_month',lambda value:'2026-09')
    def prepare(st,function,**options):
        assert st is settings;captured.append((function.__name__,options))
        return SimpleNamespace(summary=lambda:dict(mode='apercu',revision='a'*64,writes_applied=False))
    monkeypatch.setattr(cli,'prepare_call',prepare)
    return captured


@pytest.mark.parametrize('action',['reference','coherence','dossier','sot','purge','fix'])
def test_legacy_cli_is_read_only_by_default_and_uses_current_month(plan_cli,capsys,action):
    assert cli.run_cli(action,[])==0
    out=json.loads(capsys.readouterr().out)
    assert out['mode']=='apercu' and out['writes_applied'] is False
    assert len(plan_cli)==1
    if action!='purge':assert plan_cli[0][1]['mois']=='2026-09'


@pytest.mark.parametrize('action,args',[
    ('reference',['--apply']),('dossier',['--mois','2026-13']),('coherence',['--mois','2026-5']),
    ('revert',[]),('revert',['--mois','2026-09']),
    ('sot',['--fk-only','--dossier-only']),('reference',['--all-archives','--skip-rsa']),
    ('reference',['--drop-legacy','--mois','2026-09']),
    ('reference',['--drop-legacy','--apply','--expected-revision','a'*64]),
])
def test_cli_invalid_or_ambiguous_options_fail_before_loading_settings(monkeypatch,action,args):
    settings=Mock(side_effect=AssertionError('no configuration or SQL expected'))
    monkeypatch.setattr(cli,'get_settings',settings)
    with pytest.raises(SystemExit) as error:cli.run_cli(action,args)
    assert error.value.code==2 and not settings.called


def test_revert_cli_has_explicit_target_and_cutoff(plan_cli,capsys):
    assert cli.run_cli('revert',['--mois','2026-09','--keep-from','2026-08'])==0
    assert plan_cli[0][1]==dict(mois='2026-09',keep_from_mois='2026-08')


def test_purge_cli_does_not_print_personnel_in_task_logs(monkeypatch,capsys):
    from sme_gend.services import sot_maintenance as sot
    settings=object();monkeypatch.setattr(cli,'get_settings',lambda:settings)
    def apply(st,**options):
        assert st is settings and options==dict(expected='a'*64)
        return dict(segments_removed=1,removed_agents=[dict(noms='PRIVATE SYNTHETIC NAME',legacy_access_id='PRIVATE-ID')])
    monkeypatch.setattr(sot,'purge_phantom_carriere_segments',apply)
    assert cli.run_cli('purge',['--apply','--expected-revision','a'*64])==0
    text=capsys.readouterr().out
    assert 'PRIVATE' not in text and json.loads(text)['result']['removed_count']==1


def test_cli_conflict_is_explicit_and_does_not_retry(monkeypatch,capsys):
    from sme_gend.services import sot_maintenance as sot
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    monkeypatch.setattr(cli,'get_settings',lambda:object())
    apply=Mock(side_effect=ReferenceConflictError('synthetic conflict'))
    monkeypatch.setattr(sot,'purge_phantom_carriere_segments',apply)
    assert cli.run_cli('purge',['--apply','--expected-revision','a'*64])==2
    assert apply.call_count==1 and 'synthetic conflict' in capsys.readouterr().err



@pytest.mark.parametrize('operation',['reference','projection','ddl'])
def test_maintenance_permission_refuses_before_any_sql(monkeypatch,tmp_path,operation):
    from pathlib import Path
    from sme_gend.config.settings import Settings
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.referentiel_maintenance import run_referentiel_maintenance
    from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch
    from sme_gend.services.legacy_cleanup import run_legacy_cleanup
    settings=Settings(database_path=tmp_path/'unused.mdb')
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    connection=Mock(side_effect=AssertionError('No SQL before permission check'))
    monkeypatch.setattr(MySQLConnection,'connect',connection)
    with pytest.raises(PermissionError):
        if operation=='reference':run_referentiel_maintenance(settings,mois='2026-09')
        elif operation=='projection':project_carriere_from_dossier_batch(settings,mois='2026-09')
        else:run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected='a'*64)
    assert not connection.called



def test_cli_default_month_change_during_preview_is_refused(plan_cli,monkeypatch,capsys):
    values=iter(['2026-09','2026-10'])
    monkeypatch.setattr(cli,'active_month',lambda settings:next(values))
    assert cli.run_cli('coherence',[])==2
    captured=capsys.readouterr()
    assert not captured.out and 'mois actif a change' in captured.err
