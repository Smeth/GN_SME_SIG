"""Global filters and subdivision transactions on disposable native MySQL only."""
from dataclasses import replace
from datetime import date
import json
import pytest
from test_sensitive_audit_mysql import journal
from test_restore_mysql import isolated_mysql, _lot7_dossier


def test_global_combines_modules_and_filters_before_limit(journal):
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    settings, insert = journal
    insert(module='personnel', action='update', actor='ALICE', month='2026-09', pid='12', nom='SYN ALPHA')
    insert(module='auth', action='login', actor='BOB', month='2026-10', pid='')
    insert(module='doc_gen', action='doc_gen', actor='ALICE', month='2026-09', pid='',
           payload={'persons': ['12 — SYN ALPHA'], 'meta': {'personnel_ids': ['12']}})
    assert len(search_activity_audit(ActivityAuditQuery(), settings=settings)) == 3
    assert len(search_activity_audit(ActivityAuditQuery(actor='ALICE', sme_mois='2026-09', personnel_id='12',
                                                       personnel_nom='ALPHA'), settings=settings)) == 2
    rows = search_activity_audit(ActivityAuditQuery(module='auth', action='login', limit=1), settings=settings)
    assert len(rows) == 1 and rows[0]['actor'] == 'BOB'
    assert search_activity_audit(ActivityAuditQuery(date_from=date(2027,1,1)), settings=settings) == []


@pytest.fixture
def subdivisions(isolated_mysql, monkeypatch):
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
    settings, connect, params, _, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant', lambda: '2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_write_actor', lambda *a, **kw: 'SYN OPERATOR')
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda *a: True, raising=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-08','2026-08-31','Fermé')")
            cur.execute("INSERT INTO ref_structure(id,code,designation) VALUES(1,'SYN','SYN STRUCTURE')")
            cur.execute("INSERT INTO ref_subdivision_noeud(id,code,libelle) VALUES(1,'SYN_A','SOURCE A'),(2,'SYN_B','SOURCE B'),(3,'SYN_T','TARGET')")
            cur.execute("INSERT INTO ref_subdivision_arme(id,structure_id,noeud_feuille_id,libelle) VALUES(1,1,1,'SOURCE A'),(2,1,2,'SOURCE B'),(3,1,3,'TARGET')")
            cur.execute("UPDATE carriere_situation SET structure_id=1, subdivision_arme_id=1 WHERE id=1")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,structure_id,subdivision_arme_id) VALUES(2,2,1,1,2),(3,1,2,1,1)")
    service = LayoutConfigService(settings)
    cfg = ListeLayoutConfig(sections=[SectionBlock(id='syn', order=0, structure_code='SYN', subdivision_order=['SOURCE A','SOURCE B'])])
    # Initialize then reload the version token used by concurrent write protection.
    from sme_gend.repositories.reference_collection_guard import fingerprint
    cfg.write_token = fingerprint(None)
    service.save(cfg)
    return settings, connect, params, service, cfg


def labels(connect, params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,subdivision_arme_id,subdivision_arme_libelle FROM carriere_situation ORDER BY id')
            return cur.fetchall()


def test_subdivision_merge_commits_labels_layout_and_global_trace(subdivisions):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params, service, cfg = subdivisions
    assert service.subdivision_write_preview('SYN', ['SOURCE A','SOURCE B']) == ('2026-09', 2)
    count, merged = service.merge_subarmes_in_database(cfg, 'SYN', ['SOURCE A','SOURCE B'], 'TARGET')
    assert count == 2 and merged.sections[0].subdivision_order == ['TARGET']
    assert labels(connect, params) == ((1,3,'TARGET'),(2,3,'TARGET'),(3,1,None))
    assert service.load().sections[0].subdivision_order == ['TARGET']
    entries = AppConfigRepository(settings).search_audit_journal(module='liste_present', personnel_id='1', broad_agent_search=True)
    assert len(entries) == 1 and entries[0]['action'] == 'subdivisions_merge', AppConfigRepository(settings).search_audit_journal(module='liste_present')
    assert AppConfigRepository(settings).audit_personnel_keys('1') == {'career': ['1','3'], 'legacy': ['42']}
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    from sme_gend.services import auth_guard
    # Local mode allows read-only verification of the newly written operation.
    entries = search_activity_audit(ActivityAuditQuery(personnel_id='1'), settings=settings)
    assert any(entry['action'] == 'subdivisions_merge' for entry in entries)
    repository = AppConfigRepository(settings)
    repository.append_audit(action='update', module='personnel', personnel_id='42', payload={'personnel_nom': 'SYNTHETIC'})
    repository.append_audit(action='personnel_sensitive_update', module='sensitive_data', personnel_id='42', payload={'personnel_nom': 'OTHER PERSON'})
    repository.append_audit(action='doc_gen', module='doc_gen', payload={'meta': {'personnel_ids': ['42']}, 'persons': ['OTHER PERSON']})
    entries = search_activity_audit(ActivityAuditQuery(personnel_id='1'), settings=settings)
    assert {entry['action'] for entry in entries} == {'subdivisions_merge', 'update'}


@pytest.mark.parametrize('failure', ['second_rename', 'save', 'audit'])
def test_subdivision_merge_failure_rolls_back_everything(subdivisions, monkeypatch, failure):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params, service, cfg = subdivisions
    before = labels(connect, params)
    old_layout = AppConfigRepository(settings).get_json('liste_present_layout')
    def fail(*a, **kw): raise RuntimeError('SYNTHETIC FAILURE')
    if failure == 'second_rename':
        original = service._liste_generale.rename_subarme_for_structure
        def rename(structure, source, target, **kw):
            if source == 'SOURCE B': fail()
            return original(structure, source, target, **kw)
        monkeypatch.setattr(service._liste_generale, 'rename_subarme_for_structure', rename)
    elif failure == 'save': monkeypatch.setattr(service, 'save', fail)
    else: monkeypatch.setattr('sme_gend.services.business_audit.record_business_action', fail)
    with pytest.raises(RuntimeError, match='SYNTHETIC FAILURE'):
        service.merge_subarmes_in_database(cfg, 'SYN', ['SOURCE A','SOURCE B'], 'TARGET')
    assert labels(connect, params) == before
    assert AppConfigRepository(settings).get_json('liste_present_layout') == old_layout
    assert cfg.sections[0].subdivision_order == ['SOURCE A','SOURCE B']
    assert AppConfigRepository(settings).search_audit_journal(module='liste_present') == []


def test_closed_month_rejects_subdivision_changes(subdivisions):
    _, connect, params, service, cfg = subdivisions
    before = labels(connect, params)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=1")
    with pytest.raises(PermissionError, match='clôturé'):
        service.merge_subarmes_in_database(cfg, 'SYN', ['SOURCE A','SOURCE B'], 'TARGET')
    assert labels(connect, params) == before


def test_present_read_explicit_month_ignores_stale_current_flags(subdivisions):
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    settings, _, _, _, _ = subdivisions
    rows = ListeGeneraleRepository(settings).fetch_for_presence_mode('present', mois_ym='2026-09')
    # Empty administrative position counts as present, consistently with the
    # Python presence rule. The August situation must remain excluded.
    assert {row.id for row in rows} == {'1', '2'}


def test_global_rename_is_atomic_across_structures(subdivisions, monkeypatch):
    from sme_gend.models.liste_layout import SectionBlock
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params, service, cfg = subdivisions
    cfg.sections.append(SectionBlock(id='other', order=1, structure_code='OTHER', subdivision_order=['SOURCE A']))
    service.save(cfg)
    before = labels(connect, params)
    original = service.rename_subarme_in_database
    def rename(candidate, structure, source, target):
        if structure == 'OTHER': raise RuntimeError('SYN SECOND STRUCTURE')
        return original(candidate, structure, source, target)
    monkeypatch.setattr(service, 'rename_subarme_in_database', rename)
    with pytest.raises(RuntimeError, match='SECOND STRUCTURE'):
        service.rename_subarme_global(cfg, 'SOURCE A', 'TARGET')
    assert labels(connect, params) == before
    assert service.load().sections[0].subdivision_order == ['SOURCE A','SOURCE B']
    assert AppConfigRepository(settings).search_audit_journal(module='liste_present') == []
