"""Suppression et preuves partagées, exclusivement sur MySQL jetable."""
import pytest
from datetime import date
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database
from sme_gend.services.mysql_import_period import purge_sme_periode_for_reimport
from sme_gend.services.month_purge_plan import month_purge_plan


def test_import_completes_unique_unknown_date_and_keeps_civil_diploma_distinct(alert_database):
    settings, connect, params = alert_database
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    repo = MySQLAgentDossierRepository.__new__(MySQLAgentDossierRepository)
    repo._settings = settings
    repo._connect = lambda **kw: connect(**params)
    repo.insert_diplome = lambda *a, **kw: MySQLAgentDossierRepository.insert_diplome.__wrapped__(repo, *a, **kw)
    add = lambda **kw: MySQLAgentDossierRepository.insert_diplome_if_absent.__wrapped__(repo, 1, **kw)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome (agent_id,categorie,libelle_libre,obs) VALUES (1,'MILITAIRE','Brevet  A','Import archive 2026-01')")
    assert add(libelle='brevet\u00a0A', date_obtention=date(2020, 1, 1)) is None
    assert add(libelle='BREVET A', date_obtention=None) is None
    assert add(libelle='BREVET A', date_obtention=date(2020, 1, 1)) is None
    assert add(libelle='BREVET A', categorie='CIVIL', date_obtention=date(2020, 1, 1))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT categorie,date_obtention FROM agent_diplome ORDER BY id')
            assert cur.fetchall() == (('MILITAIRE', date(2020, 1, 1)), ('CIVIL', date(2020, 1, 1)))


@pytest.mark.parametrize('imported_only', [True, False])
@pytest.mark.parametrize('legacy_date', ['2020-01-01', '(01/01/2020 CONGO)'])
def test_shared_diploma_survives_origin_month_and_manual_scope_is_explicit(alert_database, monkeypatch, imported_only, legacy_date):
    settings, connect, params = alert_database
    from sme_gend.services.position_admin_events import apply_position_event_schema
    from sme_gend.services.mysql_import_period import apply_agent_conge_schema
    apply_position_event_schema(settings)
    apply_agent_conge_schema(settings)
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.invalidate_liste_cache', lambda _: None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-01','2026-01-31'),(2,'2026-02','2026-02-28')")
            cur.execute("INSERT INTO import_lot (id,sme_periode_id) VALUES (1,1),(2,2)")
            cur.execute("INSERT INTO carriere_situation (agent_id,sme_periode_id,diplome_libelle,date_obtention,structure_code,grade_libelle,date_grade,fonction_libelle,subdivision_arme_libelle,import_lot_id) VALUES (1,1,'BIA','2020-01-01','A','LT','2019-01-01','Chef','X',1),(1,2,'BIA','2020-01-01','A','LT','2019-01-01','Chef','X',2)")
            # Un changement de fonction ne doit pas faire disparaître la preuve
            # de continuité dans la même structure.
            cur.execute("UPDATE carriere_situation SET fonction_libelle='Adjoint' WHERE sme_periode_id=2")
            cur.execute('UPDATE carriere_situation SET date_obtention=%s', (legacy_date,))
            cur.execute("INSERT INTO agent_diplome (agent_id,libelle_libre,date_obtention,import_lot_id,obs) VALUES (1,'BIA','2020-01-01',1,'Import archive 2026-01'),(1,'MANUEL','2026-01-15',NULL,'Saisie applicative')")
            cur.execute("UPDATE agent_diplome SET obs=CONCAT(obs, CHAR(10), %s) WHERE libelle_libre='BIA'", ('Source Access : ' + legacy_date,))
            cur.execute("INSERT INTO agent_affectation (agent_id,structure_code,date_affectation,sme_periode_mois,import_lot_id,obs,fonction_libelle,subdivision_arme_libelle) VALUES (1,'A','2026-01-01','2026-01',1,'Import archive 2026-01','Chef','X')")
            cur.execute("INSERT INTO agent_passation_grade (agent_id,grade_libelle,date_passation,obs) VALUES (1,'LT','2019-01-01','Import archive 2026-01')")
            cur.execute("INSERT INTO agent_mission (agent_id,sme_periode_id,nature_libelle) VALUES (1,1,'MANUEL')")
            cur.execute("INSERT INTO agent_mission (agent_id,sme_periode_id,nature_libelle,import_lot_id,source_ligne_legacy) VALUES (1,1,'IMPORTE',1,'101')")
            expected = {}
            for item in month_purge_plan(1, '2026-01', imported_only=imported_only):
                cur.execute(f'SELECT COUNT(*) FROM {item.table} d WHERE {item.predicate}', item.params)
                expected[item.table] = cur.fetchone()[0]
    counts = purge_sme_periode_for_reimport(settings, '2026-01', imported_only=imported_only)
    for table, value in expected.items():
        assert counts[table] == value
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT libelle_libre,obs,import_lot_id FROM agent_diplome ORDER BY id')
            rows = cur.fetchall()
            assert rows[0] == ('BIA', 'Import archive 2026-02\nSource Access : ' + legacy_date, None)
            assert len(rows) == (2 if imported_only else 1)
            cur.execute('SELECT obs,import_lot_id,sme_periode_mois FROM agent_affectation')
            assert cur.fetchone() == ('Import archive 2026-02', None, '2026-02')
            cur.execute('SELECT obs FROM agent_passation_grade')
            assert cur.fetchone() == ('Import archive 2026-02',)
            cur.execute('SELECT nature_libelle FROM agent_mission')
            assert cur.fetchall() == ((('MANUEL',),) if imported_only else ())
            # Vérifie aussi que les clés étrangères ne bloquent pas la suppression des lots.
            cur.execute('DELETE FROM import_lot WHERE sme_periode_id=1')
            cur.execute('SELECT COUNT(*) FROM agent')
            assert cur.fetchone()[0] == 1
            cur.execute('SELECT sme_periode_id FROM carriere_situation')
            assert cur.fetchall() == ((2,),)
    # La dernière preuve supprimée entraîne la suppression de l'événement importé.
    purge_sme_periode_for_reimport(settings, '2026-02', imported_only=imported_only)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_diplome WHERE libelle_libre='BIA'")
            assert cur.fetchone()[0] == 0


def test_return_to_same_structure_does_not_support_an_earlier_assignment(alert_database, monkeypatch):
    settings, connect, params = alert_database
    from sme_gend.services.position_admin_events import apply_position_event_schema
    from sme_gend.services.mysql_import_period import apply_agent_conge_schema
    apply_position_event_schema(settings)
    apply_agent_conge_schema(settings)
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.invalidate_liste_cache', lambda _: None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois) VALUES (1,'2026-01','2026-01-31'),(2,'2026-02','2026-02-28'),(3,'2026-03','2026-03-31')")
            cur.execute("INSERT INTO carriere_situation (agent_id,sme_periode_id,structure_code) VALUES (1,1,'A'),(1,2,'B'),(1,3,'A')")
            cur.execute("INSERT INTO agent_affectation (agent_id,structure_code,date_affectation,sme_periode_mois,obs) VALUES (1,'A','2026-01-01','2026-01','Import archive 2026-01'),(1,'A','2026-03-01','2026-03','Import archive 2026-03')")
    result = purge_sme_periode_for_reimport(settings, '2026-01', imported_only=False)
    assert result['agent_affectation'] == 1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sme_periode_mois FROM agent_affectation')
            assert cur.fetchall() == (('2026-03',),)
