from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from test_month_backup_mysql import setup, snapshot


@pytest.mark.parametrize('full_archive', [False, True])
def test_month_restore_keeps_stage_session_and_desertion_note(isolated_mysql, monkeypatch, tmp_path, full_archive):
    from sme_gend.services.month_backup import create_month_backup, restore_month_backup, inspect_month_backup
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_type_stage(code,libelle) VALUES('INTERNE','Interne') ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)")
            cur.execute("INSERT INTO parcours_stage(agent_id,sme_periode_id,type_stage_code,date_debut,nature_libelle,reference_dossier,session) VALUES(1,1,'INTERNE','2026-09-01','TEST','STAGE/42','SESSION B')")
            cur.execute("INSERT INTO agent_desertion(agent_id,sme_periode_id,date_desertion,reference_dossier) VALUES(1,1,'2026-09-15','DESERTION/42')")
    before = snapshot(connect, params)
    archive = tmp_path / 'month-fields.zip'
    create_month_backup(settings, '2026-09', archive)
    payload = inspect_month_backup(archive)
    assert payload['tables']['parcours_stage'][0]['session'] == 'SESSION B'
    assert payload['tables']['agent_desertion'][0]['reference_dossier'] == 'DESERTION/42'
    prepared = None
    if full_archive:
        from sme_gend.services import backup_restore_service as backup
        from sme_gend.services.backup_month_selection import prepare_backup_months
        monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path)
        full = tmp_path / 'full-fields.zip'
        backup.create_full_backup(settings, full)
        prepared = prepare_backup_months(settings, full)
        assert prepared.months == ('2026-08', '2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE parcours_stage SET session='BROKEN',reference_dossier=NULL")
            cur.execute("DELETE FROM agent_desertion")
    if prepared is not None:
        from sme_gend.services.backup_month_selection import restore_selected_backup_months
        restore_selected_backup_months(settings, prepared, ['2026-09'])
        restore_selected_backup_months(settings, prepared, ['2026-09'])
    else:
        restore_month_backup(settings, archive, confirmed_month='2026-09')
        restore_month_backup(settings, archive, confirmed_month='2026-09')
    assert snapshot(connect, params) == before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT session,reference_dossier FROM parcours_stage')
            assert cur.fetchall() == (('SESSION B', 'STAGE/42'),)
            cur.execute('SELECT reference_dossier FROM agent_desertion')
            assert cur.fetchall() == (('DESERTION/42',),)


def test_optional_fields_insert_update_read_and_migrate_existing_schema(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.mysql_import_period import _ensure_mysql_migrations_unlocked
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.models.numerique_satellite import StageDetail, DeserteurDetail
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE parcours_stage DROP COLUMN session')
            cur.execute('ALTER TABLE agent_desertion DROP COLUMN reference_dossier')
    _ensure_mysql_migrations_unlocked(settings)
    _ensure_mysql_migrations_unlocked(settings)
    repo = MySQLNumeriqueRepository(settings)
    repo.upsert_stage('42', StageDetail('TEST', 'École', None, reference_dossier='NOTE/1', session='SESSION A'),
                      type_code='INTERNE', date_debut='2026-09-01')
    stage = repo.load_stages_internal()['42']
    assert stage.session == 'SESSION A' and stage.reference_dossier == 'NOTE/1'
    repo.upsert_stage('42', StageDetail('TEST', 'École', None, reference_dossier='NOTE/2', session='SESSION B'),
                      type_code='INTERNE', date_debut='2026-09-01', expected=stage)
    assert repo.load_stages_internal()['42'].session == 'SESSION B'
    repo.upsert_desertion('42', DeserteurDetail('2026-09-15', reference_dossier='DES/1'))
    desertion = repo.load_deserteurs()['42']
    assert desertion.reference_dossier == 'DES/1'
    repo.upsert_desertion('42', DeserteurDetail('2026-09-16', reference_dossier='DES/2'), expected=desertion)
    assert repo.load_deserteurs()['42'].reference_dossier == 'DES/2'
    assert repo.list_stages_for_agent('42')[0].session == 'SESSION B'
    assert repo.list_desertions_for_agent('42')[0].reference_dossier == 'DES/2'
