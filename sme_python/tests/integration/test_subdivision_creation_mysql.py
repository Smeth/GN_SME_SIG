"""Atomic creation on a verified disposable native MySQL only."""
from dataclasses import replace
import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier
from sme_gend.services.structure_organigram_service import StructureOrganigramService


@pytest.fixture
def editor(isolated_mysql, monkeypatch):
    settings, connect, params, *_ = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_structure(code,designation) VALUES('SYN','Structure fictive')")
            structure_id = cur.lastrowid
            cur.execute("UPDATE carriere_situation SET structure_id=%s,structure_code='SYN',"
                        "subdivision_arme_libelle='Subdivision fictive' WHERE id=1", (structure_id,))
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois) VALUES(2,'2025-09','2025-09-30')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,structure_id,structure_code,"
                        "subdivision_arme_libelle) VALUES(3,1,2,%s,'SYN','Subdivision fictive')", (structure_id,))
    return StructureOrganigramService(settings), structure_id, connect, params


def path():
    return [dict(code=c,libelle='Fictif ' + c) for c in ('NEW-U','NEW-D','NEW-T')]


def create(editor, **kwargs):
    service, sid, *_ = editor
    return service.change_subdivision('create', sid, 'SYN', label='Subdivision fictive',
        hierarchy_path=path(), expected=service.subdivision_editor_snapshot(), **kwargs)


def rows(editor):
    _, _, connect, params = editor
    with connect(**params) as conn:
        with conn.cursor() as cur:
            result = []
            for table in ('ref_subdivision_noeud','ref_subdivision_arme','carriere_situation','app_config','audit_journal'):
                cur.execute('SELECT * FROM ' + table + ' ORDER BY 1')
                result.append(cur.fetchall())
            return result


def test_complete_creation_links_exact_leaf_and_only_current_month(editor):
    rsa_id = create(editor)
    service, sid, connect, params = editor
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT t.code,d.code,u.code FROM ref_subdivision_arme r '
                        'JOIN ref_subdivision_noeud t ON t.id=r.noeud_feuille_id '
                        'JOIN ref_subdivision_noeud d ON d.id=t.parent_id '
                        'JOIN ref_subdivision_noeud u ON u.id=d.parent_id WHERE r.id=%s', (rsa_id,))
            assert cur.fetchone() == ('NEW-T','NEW-D','NEW-U')
            cur.execute('SELECT id,subdivision_arme_id FROM carriere_situation WHERE id IN(1,3) ORDER BY id')
            assert cur.fetchall() == ((1,rsa_id),(3,None))
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='subdivision_create'")
            assert cur.fetchone()[0] == 1


def test_layout_failure_rolls_back_hierarchy_link_personnel_and_audit(editor, monkeypatch):
    service, *_ = editor
    # Initialize layout before the failure to test the versioned rollback too.
    service._layout.load()
    before = rows(editor)
    def fail(*args):
        raise RuntimeError('SYNTHETIC layout failure')
    monkeypatch.setattr(service._layout,'save',fail)
    with pytest.raises(RuntimeError, match='SYNTHETIC'):
        create(editor)
    assert rows(editor) == before


def test_stale_editor_creates_no_partial_hierarchy(editor):
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    service, sid, connect, params = editor
    stale = service.subdivision_editor_snapshot()
    create(editor)
    before = rows(editor)
    with pytest.raises(ReferenceConflictError):
        service.change_subdivision('create',sid,'SYN',label='Autre',hierarchy_path=path(),expected=stale)
    assert rows(editor) == before


def test_existing_path_cannot_be_attached_twice(editor):
    service, sid, *_ = editor
    create(editor)
    snapshot = service.subdivision_editor_snapshot()
    from sme_gend.repositories.hierarchy_merge_guard import hierarchy_rows
    nodes, levels = hierarchy_rows(snapshot)
    existing = [next(nid for nid,row in nodes.items() if row[2] == code) for code in ('NEW-U','NEW-D','NEW-T')]
    before = rows(editor)
    with pytest.raises(ValueError, match='déjà rattaché'):
        service.change_subdivision('create',sid,'SYN',label='Autre',hierarchy_path=existing,expected=snapshot)
    assert rows(editor) == before


def test_only_missing_level_is_created_under_selected_existing_parents(editor):
    service, sid, connect, params = editor
    create(editor)
    snapshot = service.subdivision_editor_snapshot()
    from sme_gend.repositories.hierarchy_merge_guard import hierarchy_rows
    nodes, _ = hierarchy_rows(snapshot)
    uid = next(nid for nid,row in nodes.items() if row[2] == 'NEW-U')
    did = next(nid for nid,row in nodes.items() if row[2] == 'NEW-D')
    rsa_id = service.change_subdivision('create',sid,'SYN',label='Deuxième subdivision',
        hierarchy_path=[uid,did,dict(code='NEW-T2',libelle='Deuxième T')],expected=snapshot)
    after, _ = hierarchy_rows(service.subdivision_editor_snapshot())
    assert len(after) == len(nodes) + 1
    leaf_id = next(nid for nid,row in after.items() if row[2] == 'NEW-T2')
    assert after[leaf_id][1] == did
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT noeud_feuille_id FROM ref_subdivision_arme WHERE id=%s',(rsa_id,))
            assert cur.fetchone() == (leaf_id,)
