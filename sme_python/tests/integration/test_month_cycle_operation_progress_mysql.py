"""Monthly progress and final COMMIT on the guarded disposable server only."""
from contextlib import contextmanager

import pytest

from test_restore_mysql import isolated_mysql
from test_month_transfer import source, live
from test_month_transfer_mysql import seeded
from test_career_alerts_mysql import alert_database
from test_month_fill_mysql import fill_database
from sme_gend.services.operation_progress import progress_reporting


@pytest.mark.parametrize('failure', [False, True])
def test_month_transfer_progress_waits_for_its_transaction(seeded, source, monkeypatch, failure):
    from sme_gend.services import month_transfer as service, active_month_guard
    settings, connect, params = seeded
    plan = service.preview_month(settings, source, '2026-01')
    before = {name: table['rows'] for name, table in service.read_database(settings).items()}
    original = active_month_guard.period_state_write
    snapshots = []
    @contextmanager
    def transition(*args, **kwargs):
        with original(*args, **kwargs) as change:
            yield change
            assert snapshots[-1].active_key == 'commit'
            assert not any(snapshot.percent == 100 for snapshot in snapshots)
            if failure:
                raise RuntimeError('synthetic refusal before COMMIT')
    monkeypatch.setattr(active_month_guard, 'period_state_write', transition)
    with progress_reporting(snapshots.append):
        if failure:
            with pytest.raises(RuntimeError, match='COMMIT'):
                service.apply_month(settings, plan)
        else:
            service.apply_month(settings, plan)
    assert (snapshots[-1].percent == 100) is not failure
    if failure:
        # InnoDB may advance AUTO_INCREMENT even after rollback; rows must stay intact.
        assert {name: table['rows'] for name, table in service.read_database(settings).items()} == before
    else:
        actual = service.extract_month(service.read_database(settings), '2026-01')
        assert actual['carriere_situation']['rows'][0]['obs'] == 'JAN ORIGINAL'
    assert any(snapshot.active_key == 'restore' and snapshot.current for snapshot in snapshots)


def test_month_fill_counts_rows_and_histories_before_final_confirmation(fill_database, monkeypatch):
    from sme_gend.services import month_fill as service, active_month_guard
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10', service.FillOptions(True, True, True, True))
    original = active_month_guard.period_state_write
    snapshots, legacy_steps = [], []
    @contextmanager
    def transition(*args, **kwargs):
        with original(*args, **kwargs) as change:
            yield change
            assert snapshots[-1].active_key == 'commit'
            assert snapshots[-1].percent < 100
    monkeypatch.setattr(active_month_guard, 'period_state_write', transition)
    with progress_reporting(snapshots.append):
        result = service.apply_month_fill(settings, plan, progress=legacy_steps.append)
    assert result['target'] == '2026-10'
    assert snapshots[-1].percent == 100
    assert len(legacy_steps) == service.month_fill_progress_steps(plan)
    assert any(snapshot.active_key == 'rows' and snapshot.current == snapshot.total and snapshot.total
               for snapshot in snapshots)
    assert any(snapshot.active_key == 'history' and snapshot.current == 6 for snapshot in snapshots)


@pytest.mark.parametrize('rollback', [False, True])
def test_register_publication_defers_completion_to_owning_external_transaction(isolated_mysql, tmp_path, monkeypatch, rollback):
    from test_publication_registry_mysql import migrate, revision
    from sme_gend.services import sme_publication_registry as registry
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings, connect, params, archive = migrate(isolated_mysql, tmp_path, monkeypatch)
    local = revision(settings, tmp_path/'revision')
    snapshots = []
    with progress_reporting(snapshots.append):
        try:
            with mysql_import_transaction(settings):
                result = registry.register_export(settings, local)
                assert snapshots[-1].active_key == 'commit'
                assert snapshots[-1].percent < 100
                assert snapshots[-1].steps[-1].state == 'running'
                if rollback:
                    raise RuntimeError('synthetic caller rollback')
        except RuntimeError:
            if not rollback:
                raise
    assert (snapshots[-1].percent == 100) is not rollback
    assert any(snapshot.active_key == 'store' and snapshot.current for snapshot in snapshots)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM sme_publication')
            assert cursor.fetchone()[0] == int(not rollback)


def test_document_closure_lists_projection_stages_and_finishes_after_commit(isolated_mysql, tmp_path, monkeypatch):
    from test_document_month_cycle_mysql import adopted, close
    from sme_gend.services import active_month_guard
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    original = active_month_guard.period_state_write
    snapshots = []
    @contextmanager
    def transition(*args, **kwargs):
        with original(*args, **kwargs) as change:
            yield change
            assert snapshots[-1].active_key == 'commit'
            assert not any(snapshot.percent == 100 for snapshot in snapshots)
    monkeypatch.setattr(active_month_guard, 'period_state_write', transition)
    with progress_reporting(snapshots.append):
        result = close(settings, workspace, publication)
    assert result.created_rows == result.projected_agents == 1
    assert workspace.get_active_mois() == '2026-12'
    assert snapshots[-1].percent == 100
    assert [step.key for step in snapshots[-1].steps] == ['context','publication','projection','periods','commit']
    assert any(snapshot.active_key == 'projection' and 'situation' in snapshot.detail for snapshot in snapshots)
