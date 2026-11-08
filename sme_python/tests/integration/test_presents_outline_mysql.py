"""The rubric-only preview reads references without personnel or configuration writes."""
from dataclasses import replace
import json
import re

import pytest
from test_restore_mysql import isolated_mysql


@pytest.mark.parametrize('configured', [True, False])
def test_outline_reads_only_references_and_keeps_database_unchanged(isolated_mysql, monkeypatch, configured):
    from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
    from sme_gend.repositories.grade_repository import GradeRepository
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    from sme_gend.repositories.mysql_common import ensure_mysql_schema
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_outline import capture_outline, render_outline
    from sme_gend.services.liste_present_service import ListePresentService

    settings, connect, params = isolated_mysql
    settings = replace(settings, mysql_view_mois='2026-11')
    ensure_mysql_schema(settings)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ref_structure(code,designation,ordre_tri) VALUES('SYN_OUTLINE','SYNTHETIC DIRECTION',1)")
            structure_id = cursor.lastrowid
            cursor.execute("INSERT INTO ref_subdivision_noeud(parent_id,code,libelle) VALUES(NULL,'SYN-ROOT','SYNTHETIC ROOT')")
            parent = cursor.lastrowid
            for code, label in [('SYN-U', 'SYNTHETIC UNIT'), ('SYN-D', 'SYNTHETIC GROUP')]:
                cursor.execute('INSERT INTO ref_subdivision_noeud(parent_id,code,libelle) VALUES(%s,%s,%s)', (parent, code, label))
                parent = cursor.lastrowid
            for index, label in enumerate(['SYNTHETIC ALPHA', 'SYNTHETIC BETA']):
                cursor.execute('INSERT INTO ref_subdivision_noeud(parent_id,code,libelle) VALUES(%s,%s,%s)',
                               (parent, 'SYN-T-' + str(index), label))
                leaf = cursor.lastrowid
                cursor.execute('INSERT INTO ref_subdivision_arme(structure_id,noeud_feuille_id,libelle,ordre_tri) VALUES(%s,%s,%s,%s)',
                               (structure_id, leaf, label, index))
            cursor.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES(1,'SYNTHETIC-OUTLINE-ID','SYNTHETIC AGENT NEVER LOADED')")
            cursor.execute("INSERT INTO ref_grade(code,libelle) VALUES('SYN_OUTLINE','SYNTHETIC GRADE NEVER LOADED')")
            if configured:
                layout = ListeLayoutConfig(sections=[SectionBlock('outline', 0, 'SYN_OUTLINE',
                    title_main_override='SYNTHETIC CUSTOM TITLE', subdivision_order=['SYNTHETIC BETA', 'SYNTHETIC ALPHA'])])
                cursor.execute("INSERT INTO app_config(config_key,payload) VALUES('liste_present_layout',%s)",
                               (json.dumps(layout.to_dict()),))

    def database_state():
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME')
                tables = [row[0] for row in cursor.fetchall()]
                result = {}
                for table in tables:
                    cursor.execute('SELECT * FROM `' + table + '`')
                    result[table] = sorted(repr(row) for row in cursor.fetchall())
                return result

    before = database_state()
    def forbidden(*args, **kwargs):
        raise AssertionError('Rubric preview must not read personnel, grades or counts')
    monkeypatch.setattr(ListeGeneraleRepository, 'fetch_all', forbidden)
    monkeypatch.setattr(ListeGeneraleRepository, 'fetch_all_at_mois', forbidden)
    monkeypatch.setattr(ListeGeneraleRepository, 'fetch_for_presence_mode', forbidden)
    monkeypatch.setattr(GradeRepository, 'fetch_all', forbidden)
    monkeypatch.setattr(LayoutConfigService, 'present_counts_by_structure', forbidden)
    monkeypatch.setattr(LayoutConfigService, 'present_counts_by_subdivision', forbidden)
    monkeypatch.setattr(LayoutConfigService, 'save', forbidden)
    import pymysql.cursors
    original_execute = pymysql.cursors.Cursor.execute
    observed = []
    def record_execute(cursor, query, args=None):
        observed.append(str(query))
        return original_execute(cursor, query, args)
    service = ListePresentService(settings, LayoutConfigService(settings))
    with monkeypatch.context() as capture_patch:
        capture_patch.setattr(pymysql.cursors.Cursor, 'execute', record_execute)
        outline = capture_outline(service)
        html = render_outline(outline)
    assert 'SYNTHETIC AGENT NEVER LOADED' not in html
    assert 'SYNTHETIC GRADE NEVER LOADED' not in html
    assert 'SYNTHETIC ALPHA' in html and 'SYNTHETIC BETA' in html
    assert 'Mois SME : 2026-11' in html
    if configured:
        assert 'SYNTHETIC CUSTOM TITLE' in html
        assert html.index('SYNTHETIC BETA') < html.index('SYNTHETIC ALPHA')
    else:
        assert 'SYNTHETIC DIRECTION' in html
        assert html.index('SYNTHETIC ALPHA') < html.index('SYNTHETIC BETA')
    assert not any(re.search(r'\b(?:FROM|JOIN)\s+`?(?:agent|carriere_situation|ref_grade)`?\b', query, re.I) for query in observed)
    assert not any(re.match(r'\s*(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE)\b', query, re.I) for query in observed)
    assert database_state() == before
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_config WHERE config_key='liste_present_layout'")
            assert cursor.fetchone()[0] == int(configured)
