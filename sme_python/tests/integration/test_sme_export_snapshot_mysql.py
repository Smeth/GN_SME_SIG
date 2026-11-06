"""Fresh and coherent monthly exports on an explicitly disposable MySQL only."""
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook
from test_restore_mysql import isolated_mysql

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_read_snapshot


def seed(isolated_mysql):
    from sme_gend.repositories.mysql_common import ensure_mysql_schema
    settings, connect, params = isolated_mysql
    settings = replace(settings, mysql_view_mois="2026-11")
    ensure_mysql_schema(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) "
                        "VALUES(1,'2026-11','2026-11-30','Ouvert')")
            cur.execute("INSERT INTO import_lot(id,sme_periode_id,chemin_fichier,checksum) "
                        "VALUES(1,1,'archive_synthetique_novembre.mdb','HASH-SOURCE-TEST')")
            cur.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES(1,'42','NOM INITIAL')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,"
                        "structure_code,position_admin_libelle,import_lot_id,date_debut,date_fin) "
                        "VALUES(1,1,1,'CNE','DPF','Mission',1,'2026-11-01','2026-11-30')")
            cur.execute("INSERT INTO agent_mission(agent_id,sme_periode_id,date_debut,nature_libelle,lieu,reference_dossier) "
                        "VALUES(1,1,'2026-11-03','NATURE INITIALE','LIEU TEST','NOTE TEST')")
    return settings, connect, params


def test_read_snapshot_survives_concurrent_commit_and_cannot_write(isolated_mysql):
    settings, connect, params = isolated_mysql
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE sample(id INT PRIMARY KEY, value VARCHAR(40)) ENGINE=InnoDB")
            cur.execute("INSERT INTO sample VALUES(1,'before')")
    # A server/client configured READ COMMITTED must still get a repeatable export.
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
    with mysql_read_snapshot(settings):
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT value FROM sample")
                assert cur.fetchone()[0] == "before"
        with connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("UPDATE sample SET value='after'")
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT value FROM sample")
                assert cur.fetchone()[0] == "before"
                with pytest.raises(Exception, match="read.only|READ ONLY"):
                    cur.execute("UPDATE sample SET value='forbidden'")
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT value FROM sample")
            assert cur.fetchone()[0] == "after"


def test_capture_ignores_ui_cache_and_does_not_mix_two_instants(isolated_mysql, monkeypatch):
    from sme_gend.services.numerique_excel_export import _capture_numerique_workbook_data
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    settings, connect, params = seed(isolated_mysql)
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.cached_liste_rows",
                        lambda *args: pytest.fail("UI cache used for publication"))
    original = ListeGeneraleRepository.fetch_all_at_mois
    def concurrent_change(self, month):
        result = original(self, month)
        with connect(**params) as other:
            with other.cursor() as cur:
                cur.execute("UPDATE agent SET noms='NOM MODIFIE'")
                cur.execute("UPDATE agent_mission SET nature_libelle='NATURE MODIFIEE'")
        return result
    monkeypatch.setattr(ListeGeneraleRepository, "fetch_all_at_mois", concurrent_change)
    dataset, source = _capture_numerique_workbook_data(settings, "2026-11", ["CNE"], settings)
    assert dataset["rows"][0].noms == "NOM INITIAL"
    assert dataset["details"]["missions"]["42"].nature == "NATURE INITIALE"
    assert source["imports"][0]["source_checksum"] == "HASH-SOURCE-TEST"
    assert source["database"] == settings.mysql_database
    monkeypatch.setattr(ListeGeneraleRepository, "fetch_all_at_mois", original)
    later, _ = _capture_numerique_workbook_data(settings, "2026-11", ["CNE"], settings)
    assert later["rows"][0].noms == "NOM MODIFIE"
    assert later["details"]["missions"]["42"].nature == "NATURE MODIFIEE"


def test_export_retains_model_provenance_snapshot_exact_file_and_journal(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.numerique_excel_export import export_numerique_workbook
    from sme_gend.services.sme_export_revision import read_export_dataset
    settings, connect, params = seed(isolated_mysql)
    template = Path(__file__).resolve().parents[3] / "SME_GEND_model.xlsx"
    monkeypatch.setattr("sme_gend.services.sme_export_revision.revision_root", lambda settings: tmp_path / "versions")
    # Any old Access configured for import remains untouched.
    settings.database_path.write_bytes(b"ARCHIVE ACCESS ORIGINALE")
    dest = tmp_path / "sme.xlsx"
    revision = export_numerique_workbook(settings, template, dest, export_mois="2026-11")
    assert dest.read_bytes() == revision.workbook.read_bytes()
    assert (revision.folder / "modele.xlsx").read_bytes() == template.read_bytes()
    assert settings.database_path.read_bytes() == b"ARCHIVE ACCESS ORIGINALE"
    assert read_export_dataset(revision.folder)["details"]["missions"]["42"].refnds == "NOTE TEST"
    wb = load_workbook(dest, read_only=True, data_only=True)
    try:
        assert wb["MISSION"]["E4"].value == "NATURE INITIALE"
        assert wb["NUMERIQUE"]["T8"].value == 1
    finally:
        wb.close()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.meta.revision_id')) "
                        "FROM audit_journal WHERE action='doc_gen'")
            assert cur.fetchone()[0] == revision.revision_id


def test_real_full_backup_and_restore_include_revision_files_and_month_data(isolated_mysql, monkeypatch, tmp_path):
    from test_sme_export_revision import save
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.services import sme_publication_backup as publications
    settings, connect, params = seed(isolated_mysql)
    root = tmp_path / "versions"
    revision = save(root / ("c" * 20))
    original = {path.name: path.read_bytes() for path in revision.folder.iterdir()}
    monkeypatch.setattr(publications, "storage_root", lambda: root)
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "application")
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "default_backup_directory", lambda: tmp_path / "backups")
    archive = tmp_path / "full.zip"
    manifest = backup.create_full_backup(settings, archive)
    assert len(manifest.publication_files) == 4
    for path in revision.folder.iterdir():
        path.unlink()
    revision.folder.rmdir()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET noms='APRES SAUVEGARDE'")
    safety = tmp_path / "safety.zip"
    backup.create_full_backup(settings, safety)
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *a, **kw: safety)
    backup.restore_full_backup(settings, archive, restore_user_config=False, restore_app_data=False,
        restore_personnel_media=False, preserve_local_accounts=False, preserve_audit_journal=False,
        maintenance_confirmed=True, restore_publications=True)
    assert {path.name: path.read_bytes() for path in revision.folder.iterdir()} == original
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT noms FROM agent WHERE id=1")
            assert cur.fetchone()[0] == "NOM INITIAL"
