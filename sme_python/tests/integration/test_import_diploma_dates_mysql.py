"""Legacy date/place values against the disposable native MySQL instance."""
from datetime import date

import pytest
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database


def test_exact_reported_value_no_longer_raises_1292(alert_database):
    from pymysql import MySQLError
    from sme_gend.services.month_purge_plan import legacy_diploma_date_matches_sql
    settings, connect, params = alert_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome (agent_id,libelle_libre,date_obtention,obs) VALUES (1,'BIA','2023-08-22','Import archive 2026-01')")
            with pytest.raises(MySQLError) as error:
                cur.execute("UPDATE agent_diplome SET date_obtention=%s", ('(22/08/2023 CONGO)',))
            assert error.value.args[0] == 1292
            predicate = legacy_diploma_date_matches_sql('%s', 'date_obtention')
            cur.execute(f'UPDATE agent_diplome SET lieu_obtention=%s WHERE {predicate}', ('CONGO', '(22/08/2023 CONGO)'))
            cur.execute('SELECT date_obtention,lieu_obtention FROM agent_diplome')
            assert cur.fetchone() == (date(2023, 8, 22), 'CONGO')


@pytest.mark.parametrize('raw,expected_date,expected_place', [
    ('(22/08/2023 CONGO)', date(2023, 8, 22), 'CONGO'),
    ('28/07/29 Congo', None, 'Congo'),
    ('31/02/2023 Congo', None, 'Congo'),
])
def test_real_import_keeps_diploma_source_and_place_idempotently(alert_database, raw, expected_date, expected_place):
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    from sme_gend.services.import_diploma_date import interpret_diploma_date, source_observation
    settings, connect, params = alert_database
    repo = MySQLAgentDossierRepository.__new__(MySQLAgentDossierRepository)
    repo._settings = settings
    repo._connect = lambda **kw: connect(**params)
    repo.insert_diplome = lambda *a, **kw: MySQLAgentDossierRepository.insert_diplome.__wrapped__(repo, *a, **kw)
    parsed = interpret_diploma_date(raw)
    for _ in range(2):
        MySQLAgentDossierRepository.insert_diplome_if_absent.__wrapped__(repo, 1,
            libelle='BIA', date_obtention=parsed.value, lieu=parsed.place,
            obs=source_observation('Import archive 2026-01', parsed))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT date_obtention,lieu_obtention,obs FROM agent_diplome')
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][:2] == (expected_date, expected_place)
            assert rows[0][2].count(raw) == 1
