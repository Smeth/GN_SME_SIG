"""Targeted dossier reads preserve month scope, fallback and write revisions."""
from dataclasses import replace
import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier


@pytest.mark.parametrize('month', ['2026-09', None])
def test_single_agent_satellite_reads_equal_bulk_projection(isolated_mysql, monkeypatch, month):
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.config import liste_generale_scope
    settings, connect, params, _, row = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois=month)
    monkeypatch.setattr(liste_generale_scope, 'resolve_export_mois', lambda _: month)
    monkeypatch.setattr(MySQLNumeriqueRepository, '_export_mois', lambda _: month)
    with connect(**params) as connection:
        with connection.cursor() as cur:
            cur.execute("INSERT IGNORE INTO ref_type_stage(code,libelle) VALUES('INTERNE','Stage interne')")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois) VALUES(2,'2026-08','2026-08-31')")
            for agent in (1, 2):
                for period, stamp in ((1, '2026-09-03'), (2, '2026-08-04')):
                    cur.execute('INSERT INTO agent_mission(agent_id,sme_periode_id,date_debut,nature_libelle) VALUES(%s,%s,%s,%s)',
                                (agent, period, stamp, f'SYN MISSION {agent} {period}'))
                    cur.execute('INSERT INTO agent_malade(agent_id,sme_periode_id,date_debut,pathologie) VALUES(%s,%s,%s,%s)',
                                (agent, period, stamp, f'SYN MALADE {agent} {period}'))
                    for kind in ('LOCAL', 'EXTERIEUR', 'INTERNE'):
                        cur.execute('INSERT INTO parcours_stage(agent_id,sme_periode_id,type_stage_code,date_debut,nature_libelle) VALUES(%s,%s,%s,%s,%s)',
                                    (agent, period, kind, stamp, f'SYN STAGE {agent} {period}'))
    repository = MySQLNumeriqueRepository(settings)
    for name in ('load_missions', 'load_malades', 'load_stages_loc', 'load_stages_ex', 'load_stages_internal'):
        method = getattr(repository, name)
        bulk = method()
        single = method(legacy_id='42')
        assert set(single) == {'42'}
        assert single['42'] == bulk['42']
        assert single['42'].write_token == bulk['42'].write_token
        assert method(legacy_id='ABSENT') == {}
    from sme_gend.services import dossier_read_snapshot
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    def unexpected_scan(*_):
        raise AssertionError('A full alert scan must not delay the initial dossier projection')
    monkeypatch.setattr(dossier_read_snapshot, 'alerts_for_agent', unexpected_scan)
    snapshot = dossier_read_snapshot.load_dossier_read_snapshot(settings, row, include_alerts=False)
    assert snapshot['alerts_pending'] and snapshot['alerts'] == []
