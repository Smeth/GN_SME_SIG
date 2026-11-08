"""Progress counters and final acknowledgement on the guarded disposable MySQL."""
from contextlib import contextmanager

from test_restore_mysql import isolated_mysql


def test_dump_reports_snapshot_row_counts_and_restore_waits_for_atomic_switch(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services import backup_restore_service as backup, mysql_restore_staging as staging
    from sme_gend.services.operation_progress import progress_reporting
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE agent(id INT PRIMARY KEY,noms VARCHAR(50)) ENGINE=InnoDB')
            cur.executemany('INSERT INTO agent VALUES(%s,%s)', [(index, 'SYNTHETIC') for index in range(2500)])
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'backups')
    scope = backup.BackupScope(include_user_config=False, include_app_config=False, include_app_data=False,
        include_media_photos=False, include_media_grades=False, include_publications=False)
    archive = tmp_path/'snapshot.zip'
    snapshots = []
    with progress_reporting(snapshots.append):
        manifest = backup.create_backup(settings, archive, scope=scope)
    assert manifest.table_counts == {'agent': 2500}
    assert snapshots[-1].percent == 100
    assert any('1,000/2,500' in snapshot.detail for snapshot in snapshots)
    assert any('2,000/2,500' in snapshot.detail for snapshot in snapshots)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET noms='CURRENT'")
    original_publish = staging.PreparedRestore.publish
    snapshots.clear()
    def publish(prepared):
        assert snapshots[-1].active_key == 'publish'
        assert snapshots[-1].percent < 100
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT noms FROM agent WHERE id=1')
                assert cur.fetchone()[0] == 'CURRENT'
        return original_publish(prepared)
    monkeypatch.setattr(staging.PreparedRestore, 'publish', publish)
    restore_scope = backup.RestoreScope(restore_user_config=False, restore_app_config=False,
        restore_app_data=False, restore_media_photos=False, restore_media_grades=False,
        preserve_local_accounts=False, preserve_audit_journal=False,
        maintenance_confirmed=True, restore_publications=False)
    with progress_reporting(snapshots.append):
        backup.restore_backup(settings, archive, scope=restore_scope)
    assert snapshots[-1].percent == 100
    assert all(step.state == 'done' for step in snapshots[-1].steps)
    assert any('2,500 ligne(s) contrôlée(s)' in snapshot.detail for snapshot in snapshots)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT noms,COUNT(*) FROM agent GROUP BY noms')
            assert cur.fetchall() == (('SYNTHETIC', 2500),)


def test_month_restore_progress_finishes_after_real_transaction_commit(isolated_mysql, monkeypatch, tmp_path):
    from test_month_backup_mysql import setup, snapshot
    from sme_gend.services import month_backup
    from sme_gend.services.operation_progress import progress_reporting
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    archive = tmp_path/'month.zip'
    month_backup.create_month_backup(settings, '2026-09', archive)
    before = snapshot(connect, params)
    original_transaction = month_backup.mysql_import_transaction
    snapshots = []
    committed = []
    @contextmanager
    def transaction(*args, **kwargs):
        with original_transaction(*args, **kwargs):
            yield
            assert snapshots[-1].active_key == 'commit'
            assert snapshots[-1].percent < 100
            assert snapshots[-1].steps[-1].state == 'running'
        committed.append(True)
    monkeypatch.setattr(month_backup, 'mysql_import_transaction', transaction)
    with progress_reporting(snapshots.append):
        result = month_backup.restore_month_backup(settings, archive, confirmed_month='2026-09')
    assert result['month'] == '2026-09'
    assert committed == [True]
    assert snapshots[-1].percent == 100
    assert snapshot(connect, params) == before
    assert any(snapshot.active_key == 'rows' and snapshot.current and snapshot.total
               for snapshot in snapshots)
