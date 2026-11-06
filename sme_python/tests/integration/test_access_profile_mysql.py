"""Profile fields absent from old archives must survive a career import."""
from dataclasses import replace
import pytest
from test_restore_mysql import isolated_mysql,_lot7_dossier


def test_access_lot29_legacy_import_does_not_clear_profile_fields(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET sexe='F',telephone='SYNTHETIC PHONE',adresse_residence='SYNTHETIC ADDRESS' WHERE id=1")
            cur.execute('INSERT INTO import_lot(sme_periode_id) VALUES(1)');lot=cur.lastrowid
    repo=MySQLAgentRepository(settings)
    repo.bulk_import_from_access_rows([replace(row,id='42',access_join_id=None)],sme_periode_id=1,import_lot_id=lot,mois='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sexe,telephone,adresse_residence FROM agent WHERE id=1')
            assert cur.fetchone()==('F','SYNTHETIC PHONE','SYNTHETIC ADDRESS')



@pytest.mark.parametrize('supplied', [('sexe',),('telephone',),('adresse_residence',),('sexe','telephone','adresse_residence')])
def test_access_lot29_explicit_blank_clears_only_declared_profile_fields(isolated_mysql,monkeypatch,supplied):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET sexe='F',telephone='SYNTHETIC PHONE',adresse_residence='SYNTHETIC ADDRESS' WHERE id=1")
            cur.execute('INSERT INTO import_lot(sme_periode_id) VALUES(1)');lot=cur.lastrowid
    incoming=replace(row,id='42',access_join_id=None,access_profile_fields=supplied)
    MySQLAgentRepository(settings).bulk_import_from_access_rows([incoming],sme_periode_id=1,import_lot_id=lot,mois='2026-09')
    expected=tuple(None if field in supplied else value for field,value in zip(('sexe','telephone','adresse_residence'),('F','SYNTHETIC PHONE','SYNTHETIC ADDRESS')))
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT sexe,telephone,adresse_residence FROM agent WHERE id=1');assert cur.fetchone()==expected


def test_access_lot29_programmatic_nonnull_profile_values_still_import(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings,connect,params,service,row=_lot7_dossier(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET sexe='M',telephone='SYNTHETIC PHONE',adresse_residence='SYNTHETIC ADDRESS' WHERE id=1")
            cur.execute('INSERT INTO import_lot(sme_periode_id) VALUES(1)');lot=cur.lastrowid
    incoming=replace(row,id='42',access_join_id=None,sexe='F')
    MySQLAgentRepository(settings).bulk_import_from_access_rows([incoming],sme_periode_id=1,import_lot_id=lot,mois='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sexe,telephone,adresse_residence FROM agent WHERE id=1');assert cur.fetchone()==('F','SYNTHETIC PHONE','SYNTHETIC ADDRESS')
