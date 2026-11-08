"""Measured monthly operations: real units, named steps, no success on failure."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_month_transfer import source
from sme_gend.config.settings import Settings
from sme_gend.services import month_transfer, pre_closure_service, sme_month_closure
from sme_gend.services.operation_progress import progress_reporting


@pytest.mark.parametrize('suffix', ['.sql', '.zip'])
def test_month_archive_progress_keeps_the_destination_until_atomic_publication(source, tmp_path, suffix):
    target = tmp_path/('month' + suffix)
    snapshots, published = [], []
    def observe(snapshot):
        snapshots.append(snapshot)
        if snapshot.percent == 100:
            published.append(target.is_file())
    with progress_reporting(observe):
        month_transfer.write_month(target, source, '2026-01')
    assert published and all(published)
    assert snapshots[-1].percent == 100
    assert [step.key for step in snapshots[-1].steps] == ['scope', 'write', 'publish']
    assert all(step.state == 'done' for step in snapshots[-1].steps)
    assert [s.percent for s in snapshots] == sorted(s.percent for s in snapshots)
    with progress_reporting(snapshots.append):
        loaded = month_transfer.load_source(target)
    assert month_transfer.months_in(loaded) == ['2026-01']
    assert snapshots[-1].percent == 100


def test_month_archive_publication_failure_never_reports_success(source, tmp_path, monkeypatch):
    from sme_gend.services import safe_paths
    def deny_publication(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(safe_paths.os, 'link', deny_publication)
    monkeypatch.setattr(safe_paths.os, 'rename', deny_publication)
    target = tmp_path/'month.sql'
    snapshots = []
    with progress_reporting(snapshots.append), pytest.raises(OSError, match='synthetic disk'):
        month_transfer.write_month(target, source, '2026-01')
    assert not target.exists()
    assert snapshots[-1].percent < 100
    assert snapshots[-1].active_key == 'publish'
    assert snapshots[-1].steps[-1].state == 'running'


def test_invalid_sql_rows_do_not_complete_the_verification_stage():
    sql = ('CREATE TABLE agent(id INT, PRIMARY KEY(id)) ENGINE=InnoDB;\n'
           'INSERT INTO agent VALUES(1); INSERT INTO agent VALUES(1);')
    snapshots = []
    with progress_reporting(snapshots.append), pytest.raises(ValueError, match='dupliquée'):
        month_transfer.read_sql(sql)
    assert snapshots[-1].active_key == 'verify'
    assert snapshots[-1].percent < 100
    assert snapshots[-1].steps[-1].state == 'running'


def test_pre_closure_report_lists_the_actual_check_families(tmp_path, monkeypatch):
    service = pre_closure_service
    monkeypatch.setattr(service, 'load_sme_context', lambda: SimpleNamespace(mois_actif='2026-09'))
    monkeypatch.setattr(service, 'SmeWorkspaceService', lambda st: SimpleNamespace(get_active_mois=lambda:'2026-09'))
    monkeypatch.setattr(service, 'evaluate_editing_state', lambda *a, **k: SimpleNamespace(locked=False))
    monkeypatch.setattr(service, 'SmeRepository', lambda st: SimpleNamespace(table_exists=lambda:True,
        fetch_all=lambda:[SimpleNamespace(mois='2026-09',statut='Ouvert')]))
    monkeypatch.setattr(service, 'ListeGeneraleRepository', lambda st: SimpleNamespace(
        fetch_all=lambda:[SimpleNamespace(position_admin='Présent')]))
    monkeypatch.setattr(service, 'padmin_counts_as_present', lambda *a, **k:True)
    monkeypatch.setattr(service, 'GainPerteRepository', lambda st: SimpleNamespace(fetch_for_month=lambda month:[]))
    monkeypatch.setattr('sme_gend.services.gain_perte_coherence.load_unsignaled_pertes', lambda *a:[])
    monkeypatch.setattr(service, 'check_mysql_connectivity', lambda st:(True,'SYNTHETIC'))
    monkeypatch.setattr(service, 'find_latest_backup_zip', lambda:None)
    monkeypatch.setattr(service, 'find_archive_for_month', lambda month:None)
    snapshots = []
    with progress_reporting(snapshots.append):
        result = service.run_pre_closure_checks(Settings(database_path=tmp_path/'unused'), '2026-09', '2026-10')
    assert not result.blocking
    assert [step.key for step in snapshots[-1].steps] == ['period','personnel','movements','mysql','backup','report']
    assert snapshots[-1].percent == 100
    assert all(step.state == 'done' for step in snapshots[-1].steps)


@pytest.mark.parametrize('failure', [False, True])
def test_legacy_closure_finishes_only_after_the_transition_returns(tmp_path, monkeypatch, failure):
    closure = sme_month_closure
    monkeypatch.setattr('sme_gend.services.action_permissions.assert_any_permission', lambda *a:None)
    monkeypatch.setattr('sme_gend.services.document_month_cycle.assert_legacy_closure', lambda st:None)
    monkeypatch.setattr('sme_gend.services.active_month_guard.load_active_month_snapshot', lambda st:object())
    monkeypatch.setattr(closure, 'sme_archives_dir', lambda:tmp_path/'archives')
    monkeypatch.setattr(closure, '_snapshot_layout_from_central', lambda *a:None)
    backup = tmp_path/'prior.sql'; backup.write_bytes(b'SYNTHETIC BACKUP')
    monkeypatch.setattr('sme_gend.services.mysql_backup_service.backup_mysql_database', lambda *a:(backup,[]))
    snapshots = []
    def transition(*a, **k):
        assert snapshots[-1].active_key == 'commit'
        assert snapshots[-1].percent < 100
        if failure:
            raise RuntimeError('synthetic COMMIT failure')
        return [], 1, 1, True
    monkeypatch.setattr(closure, '_commit_closure_transition', transition)
    with progress_reporting(snapshots.append):
        if failure:
            with pytest.raises(RuntimeError, match='COMMIT'):
                closure.run_piste_a_month_closure(Settings(database_path=tmp_path/'unused'), '2026-09', '2026-10')
        else:
            result = closure.run_piste_a_month_closure(Settings(database_path=tmp_path/'unused'), '2026-09', '2026-10')
            assert result.tabsme_rows_closed == result.tabsme_rows_opened == 1
    assert (snapshots[-1].percent == 100) is not failure
