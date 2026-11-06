"""Displayed month versions, explicit intent and restore compatibility."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError,replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import pytest
from sme_gend.config.settings import Settings
from sme_gend.config import sme_context
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services import active_month_guard as guard
from sme_gend.services.sme_workspace_service import SmeWorkspaceService,WorkspacePreferences
from sme_gend.services.shared_config_write import bind_config_version


def snapshot(settings=None):
    settings=settings or Settings(database_path=Path('unused.mdb'))
    return guard.ActiveMonthSnapshot(mysql_database_key(settings),((1,'2026-09','Ouvert','SME-202609'),(2,'2026-08','Réouvert','SME-202608')),'a'*64)


@pytest.mark.parametrize('invalid',['missing','wrong_type','wrong_database'])
def test_active_month_mutation_requires_its_loaded_database_before_sql(monkeypatch,invalid):
    settings=Settings(database_path=Path('unused.mdb'));service=SmeWorkspaceService.__new__(SmeWorkspaceService);service._settings=settings
    expected=None if invalid=='missing' else object() if invalid=='wrong_type' else snapshot(replace(settings,mysql_database='other'))
    monkeypatch.setattr(guard,'period_state_write',lambda *a,**kw:pytest.fail('Unversioned intent reached SQL'))
    with pytest.raises(ValueError,match='Version du mois actif indisponible'):service.set_active_mois('2026-10',expected=expected)


@pytest.mark.parametrize('month',['2026-13','2026-00','invalid'])
def test_active_month_calendar_is_checked_before_the_write_gate(monkeypatch,month):
    settings=Settings(database_path=Path('unused.mdb'));service=SmeWorkspaceService.__new__(SmeWorkspaceService);service._settings=settings
    monkeypatch.setattr(guard,'period_state_write',lambda *a,**kw:pytest.fail('Invalid month reached SQL'))
    with pytest.raises(ValueError):service.set_active_mois(month,expected=snapshot(settings))


def test_month_snapshot_is_immutable_and_ignores_reopened_months():
    cfg=snapshot()
    assert cfg.active_month=='2026-09' and 'a'*64 not in repr(cfg)
    with pytest.raises(FrozenInstanceError):cfg.revision='b'*64
    with pytest.raises(ValueError,match='Plusieurs'):replace(cfg,rows=cfg.rows+((3,'2026-10','Ouvert','NEXT'),)).active_month
    assert replace(cfg,rows=()).active_month==''


def test_month_context_metadata_does_not_enter_legacy_exports():
    ctx=sme_context.SmeContext(_month_snapshot=snapshot(),_pending_month_change=object())
    assert '_month_snapshot' not in ctx.to_dict() and '_pending_month_change' not in ctx.to_dict()
    assert 'a'*64 not in repr(ctx)


def test_coherent_month_load_uses_one_statement(monkeypatch):
    settings=Settings(database_path=Path('unused.mdb'));cursor=MagicMock()
    cursor.fetchone.return_value=(json.dumps({'token':'synthetic'}),json.dumps(snapshot(settings).rows))
    connection=MagicMock();connection.cursor.return_value.__enter__.return_value=cursor
    @contextmanager
    def connect(**kw):yield connection
    monkeypatch.setattr(guard,'MySQLConnection',lambda *a:SimpleNamespace(connect=connect))
    cfg=guard.load_active_month_snapshot(settings)
    assert cfg.active_month=='2026-09' and cursor.execute.call_count==1
    assert 'JSON_ARRAYAGG' in cursor.execute.call_args.args[0]


@pytest.mark.parametrize('marker',['invalid','null',[],{'token':7}])
def test_invalid_transition_marker_is_never_replaced_with_a_default(marker):
    with pytest.raises((ValueError,TypeError)):guard._snapshot(Settings(database_path=Path('unused.mdb')),snapshot().rows,marker)


def test_stale_month_is_checked_before_workspace_preferences(monkeypatch):
    settings=Settings(database_path=Path('unused.mdb'))
    prefs=WorkspacePreferences();bind_config_version(prefs,'sme_workspace',prefs.to_dict(),settings)
    ctx=sme_context.SmeContext(mois_actif='2026-10',_loaded_mois_actif='2026-09',_workspace_snapshot=prefs,_month_snapshot=snapshot(settings))
    ctx.editeur_defaut='NEW';service=MagicMock()
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.sme_workspace_service.SmeWorkspaceService',lambda *a:service)
    monkeypatch.setattr(sme_context,'_save_local_ui_prefs',lambda ctx:None)
    @contextmanager
    def rejected(*a,**kw):
        raise ReferenceConflictError('synthetic stale month')
        yield
    monkeypatch.setattr(guard,'period_state_write',rejected)
    with pytest.raises(ReferenceConflictError):sme_context.save_sme_context(ctx)
    service.save_workspace_payload.assert_not_called();service.set_active_mois.assert_not_called()


@pytest.mark.parametrize('full_restore',[False,True])
def test_configuration_restore_does_not_rewind_live_month_transition_marker(full_restore):
    from sme_gend.services.backup_restore_service import _app_config_rows_for_restore
    rows=[{'config_key':'active_month_revision','payload':{'token':'OLD'}},{'config_key':'sme_workspace','payload':{'jour_cloture':25}}]
    selected=_app_config_rows_for_restore(rows,preserve_local_accounts=False,preserve_runtime_state=not full_restore)
    assert ('active_month_revision' in {row['config_key'] for row in selected}) is full_restore
    assert any(row['config_key']=='sme_workspace' for row in selected)


def test_existing_import_status_is_preserved_while_new_historical_open_is_closed(monkeypatch):
    settings=Settings(database_path=Path('unused.mdb'))
    original=snapshot(settings);scope=guard.PeriodChange(mysql_database_key(settings),original,lambda:None)
    token=guard._period_change.set(scope);calls=[]
    monkeypatch.setattr('sme_gend.services.mysql_import_period.ensure_sme_periode',lambda conn,month,**kw:calls.append((month,kw)))
    try:
        guard.preserved_import_period(None,'2026-08',statut='Fermé',selected_month='2026-08')
        guard.preserved_import_period(None,'2026-10',statut='Ouvert',selected_month='2026-10')
    finally:guard._period_change.reset(token)
    assert calls[0][1]['statut']=='Réouvert' and calls[1][1]['statut']=='Fermé'



def test_archive_workflow_reports_that_a_committed_import_is_not_rolled_back(monkeypatch,tmp_path):
    from sme_gend.services import sme_month_closure as closure
    settings=Settings(database_path=tmp_path/'source.mdb');settings.database_path.write_bytes(b'SYNTHETIC ACCESS')
    monkeypatch.setattr(guard,'load_active_month_snapshot',lambda *a,**kw:snapshot(settings))
    monkeypatch.setattr(closure,'sme_archives_dir',lambda:tmp_path)
    monkeypatch.setattr(closure,'_snapshot_file',lambda *a:tmp_path/'archive.mdb')
    def imported(*a,**kw):
        kw['on_month_committed'](snapshot(settings))
        return {'referentials':{'warnings':[]},'sme_mois':'2026-09'}
    monkeypatch.setattr('sme_gend.services.mysql_bundle_import.import_full_mysql_bundle_from_access',imported)
    def failed(*a,**kw):raise ReferenceConflictError('synthetic competing activation')
    monkeypatch.setattr(closure,'_commit_closure_transition',failed)
    with pytest.raises(ReferenceConflictError,match="import MySQL est enregistré") as error:
        closure.import_existing_db_for_month(settings,settings.database_path,'2026-09','2026-10')
    assert isinstance(error.value.__cause__,ReferenceConflictError)
