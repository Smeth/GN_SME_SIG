from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from test_month_backup_mysql import setup, snapshot


def test_full_backup_month_selection_preserves_unselected_month_and_accounts(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.services.backup_month_selection import prepare_backup_months, restore_selected_backup_months
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path)
    archive = tmp_path / 'full.zip'
    backup.create_full_backup(settings, archive)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET fonction_libelle='CURRENT AUGUST' WHERE sme_periode_id=2")
            cur.execute("UPDATE carriere_situation SET fonction_libelle='BROKEN SEPTEMBER' WHERE sme_periode_id=1")
    before = snapshot(connect, params)
    prepared = prepare_backup_months(settings, archive)
    assert prepared.months == ('2026-08', '2026-09')
    # Reading the archive must never publish the staged tables.
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT fonction_libelle FROM carriere_situation WHERE id=1')
            assert cur.fetchone()[0] == 'BROKEN SEPTEMBER'
    result = restore_selected_backup_months(settings, prepared, ['2026-09'])
    assert result['months'] == ('2026-09',)
    assert Path(result['backup']).is_file()
    assert snapshot(connect, params) == before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT fonction_libelle FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0] != 'BROKEN SEPTEMBER'


def test_selected_months_roll_back_together_on_second_failure(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services import backup_restore_service as backup, month_backup
    from sme_gend.services.backup_month_selection import prepare_backup_months, restore_selected_backup_months
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path)
    archive = tmp_path / 'full.zip'
    backup.create_full_backup(settings, archive)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET fonction_libelle='KEEP CURRENT AUGUST' WHERE sme_periode_id=2")
    prepared = prepare_backup_months(settings, archive)
    original = month_backup.restore_month_backup
    calls = []
    def restore(*args, **kwargs):
        calls.append(kwargs['confirmed_month'])
        if len(calls) == 2:
            raise RuntimeError('second month failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(month_backup, 'restore_month_backup', restore)
    before = snapshot(connect, params)
    with pytest.raises(RuntimeError, match='second month'):
        restore_selected_backup_months(settings, prepared, prepared.months)
    assert calls == ['2026-08', '2026-09']
    assert snapshot(connect, params) == before
