"""Direct faithful workbook export works before the publication migrations."""
from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook
from test_restore_mysql import isolated_mysql


def test_direct_workbook_export_needs_no_publication_schema_or_right(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories.mysql_common import ensure_mysql_schema
    from sme_gend.services.numerique_excel_export import export_numerique_workbook
    settings,connect,params=isolated_mysql
    settings=replace(settings,mysql_view_mois='2026-11')
    ensure_mysql_schema(settings)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES"
                           "(1,'2026-11','2026-11-30','Ouvert'),(2,'2026-10','2026-10-31','Fermé')")
            cursor.execute("INSERT INTO import_lot(id,sme_periode_id,chemin_fichier,checksum) "
                           "VALUES(1,1,'synthetic_month.mdb','SYNTHETIC-SOURCE')")
            cursor.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES"
                           "(1,'SYN-1','SYNTHETIC MISSION'),(2,'SYN-2','SYNTHETIC PRESENT')")
            cursor.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,"
                           "structure_code,position_admin_libelle,import_lot_id,date_debut,date_fin) VALUES"
                           "(1,1,1,'CNE','DPF','Mission',1,'2026-11-01','2026-11-30'),"
                           "(2,2,1,'CNE','DPF','Présent',1,'2026-11-01','2026-11-30')")
            cursor.execute("INSERT INTO agent_mission(agent_id,sme_periode_id,date_debut,nature_libelle,lieu,reference_dossier) "
                           "VALUES(1,1,'2026-11-03','SYNTHETIC NATURE','SYNTHETIC LOCATION','SYNTHETIC NOTE')")
    def tables_and_business():
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME')
                tables=tuple(row[0] for row in cursor.fetchall())
                business={}
                for table in tables:
                    if table not in ('audit_journal','app_config'):
                        cursor.execute('SELECT * FROM `'+table+'`')
                        business[table]=sorted(repr(row) for row in cursor.fetchall())
                cursor.execute('SELECT config_key FROM app_config ORDER BY config_key')
                keys={row[0] for row in cursor.fetchall()}
                return tables,business,keys
    before_tables,before_business,before_keys=tables_and_business()
    assert '_sme_schema_migration' not in before_tables
    assert not any(name.startswith('sme_publication') for name in before_tables)
    def forbidden(*args,**kwargs):
        raise AssertionError('A direct export must not migrate, project or register a publication')
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_export_documents',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:permission=='effectifs.export')
    monkeypatch.setattr('sme_gend.services.schema_migrations.require_capability',forbidden)
    monkeypatch.setattr('sme_gend.services.month_projection.refresh_month_projection',forbidden)
    monkeypatch.setattr('sme_gend.services.numerique_excel_export._capture_publication_inputs',forbidden)
    monkeypatch.setattr('sme_gend.services.sme_export_revision.save_export_revision',forbidden)
    monkeypatch.setattr('sme_gend.services.sme_publication_registry.register_export',forbidden)
    template=Path(__file__).resolve().parents[3]/'SME_GEND_model.xlsx'
    original_template=template.read_bytes()
    settings.database_path.write_bytes(b'SYNTHETIC LEGACY ACCESS SOURCE')
    destination=tmp_path/'direct_sme.xlsx'
    result=export_numerique_workbook(settings,template,destination,export_mois='2026-11')
    assert result.path==destination and result.month=='2026-11'
    assert not result.journal_warning
    assert destination.is_file() and template.read_bytes()==original_template
    assert settings.database_path.read_bytes()==b'SYNTHETIC LEGACY ACCESS SOURCE'
    workbook=load_workbook(destination,data_only=True)
    original=load_workbook(template)
    try:
        assert len(workbook.sheetnames)==len(original.sheetnames)
        assert workbook.sheetnames==original.sheetnames
        assert [(sheet.title,sheet.sheet_state) for sheet in workbook]==[(sheet.title,sheet.sheet_state) for sheet in original]
        assert workbook['MISSION']['E4'].value=='SYNTHETIC NATURE'
        assert workbook['LISTE GENERALE']['B4'].value=='SYNTHETIC MISSION'
        assert workbook['LISTE GENERALE']['B5'].value=='SYNTHETIC PRESENT'
        assert workbook['NUMERIQUE']['T8'].value==2
        assert set(map(str,workbook['NUMERIQUE'].merged_cells.ranges))==set(map(str,original['NUMERIQUE'].merged_cells.ranges))
    finally:
        workbook.close();original.close()
    after_tables,after_business,after_keys=tables_and_business()
    assert after_tables==before_tables and after_business==before_business
    assert after_keys-before_keys<={'document_history_revision'}
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT action,module,sme_mois_context FROM audit_journal WHERE action='doc_gen'")
            assert cursor.fetchall()==(('doc_gen','doc_gen','2026-11'),)
