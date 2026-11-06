from datetime import date
import pytest
from test_restore_mysql import isolated_mysql,_lot7_dossier


def test_access_lot29_invalid_stage_dates_roll_back_the_period(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_import_transaction
    from sme_gend.services.mysql_access_numerique_import import import_stages_from_access
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_type_stage(code,libelle) VALUES('LOCAL','ORIGINAL LABEL') ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)")
            cur.execute("INSERT INTO parcours_stage(agent_id,sme_periode_id,type_stage_code,date_debut,date_fin,nature_libelle) VALUES(1,1,'LOCAL','2026-09-02','2026-09-08','ORIGINAL STAGE')")
            cur.execute('SELECT * FROM parcours_stage ORDER BY id');original=cur.fetchall()
    with pytest.raises(ValueError,match='Dates de stage Access invalides'):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    import_stages_from_access(cur,lambda query: ([(42,'STAGE','LIEU','17/09/2026 \u2014 07/09/2026')],None),
                        mois='2026-09',sme_periode_id=1,import_lot_id=None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM parcours_stage ORDER BY id');assert cur.fetchall()==original
            cur.execute("SELECT libelle FROM ref_type_stage WHERE code='LOCAL'");assert cur.fetchone()==('ORIGINAL LABEL',)
