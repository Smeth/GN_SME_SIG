"""Recherche historique complète, sur le serveur jetable vérifié par la fixture."""
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database

from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository


def test_historical_search_finds_latest_grade_and_structure_without_silent_cap(alert_database):
    settings, connect, params = alert_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_grade (id,code,libelle) VALUES (9001,'QA_G','GRADE UNIQUE QA')")
            cur.execute("INSERT INTO ref_structure (id,code,designation) VALUES (9001,'QA_S','STRUCTURE UNIQUE QA')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES "
                        "(9001,'2026-01','2026-01-31'),(9002,'2026-02','2026-02-28')")
            cur.executemany("INSERT INTO agent (id,legacy_access_id,noms) VALUES (%s,%s,%s)",
                            [(i, str(i), 'ARCHIVE QA ' + str(i)) for i in range(1000, 1085)])
            cur.executemany("INSERT INTO carriere_situation (agent_id,est_actuelle,sme_periode_id,grade_id,structure_id) "
                            "VALUES (%s,0,9002,9001,9001)", [(i,) for i in range(1000, 1085)])
            # L'ancienne affectation n'est pas la structure du dossier le plus récent.
            cur.execute("INSERT INTO carriere_situation (agent_id,est_actuelle,sme_periode_id,grade_libelle,structure_code) "
                        "VALUES (1000,0,9001,'GRADE OBSOLETE QA','STRUCTURE OBSOLETE QA')")
    repo = MySQLAgentRepository(settings)
    for term in ('ARCHIVE QA', 'GRADE UNIQUE QA', 'QA_S', 'STRUCTURE UNIQUE QA'):
        hits = repo.search_historical_personnel(term)
        assert len(hits) == 85
        assert {item[1].satellite_join_key() for item in hits} == {str(i) for i in range(1000, 1085)}
    assert repo.search_historical_personnel('GRADE OBSOLETE QA') == []
    assert repo.search_historical_personnel('STRUCTURE OBSOLETE QA') == []
    assert len(repo.search_historical_personnel('ARCHIVE QA', limit=2)) == 2


def test_current_search_also_finds_structure_designation(alert_database):
    settings, connect, params = alert_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_structure (id,code,designation) VALUES (9001,'QA_S','DESIGNATION UNIQUE QA')")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (9001,'2026-09','2026-09-30')")
            cur.execute("INSERT INTO carriere_situation (agent_id,est_actuelle,sme_periode_id,structure_id) VALUES (1,1,9001,9001)")
    assert [row.satellite_join_key() for row in MySQLAgentRepository(settings).search_current_personnel('DESIGNATION UNIQUE QA')] == ['101']
