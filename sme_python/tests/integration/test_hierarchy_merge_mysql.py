"""Only disposable MySQL schemas: hierarchy, harmonization and commit signals."""
from dataclasses import replace
import json
import pytest
from test_restore_mysql import isolated_mysql,_lot7_dossier
from sme_gend.services.data_sync import read_data_revision


def setup(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_subdiv_hierarchy_repository import MySQLSubdivHierarchyRepository
    from sme_gend.services.subdiv_hierarchy_service import SubdivHierarchyService
    from sme_gend.services.referentiel_coherence_service import ReferentielCoherenceService
    settings,connect,params,*_=_lot7_dossier(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda **kw:'SYNTHETIC')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            ids={}
            for code in ('KEEP','SOURCE'):
                cur.execute('INSERT INTO ref_structure(code,designation) VALUES(%s,%s)',(code,code));ids[code]=cur.lastrowid
            from sme_gend.services.mysql_ref_etl import _ensure_subdiv_root
            _ensure_subdiv_root(cur)
            parent=1
            for code in ('SYN-U','SYN-D','SYN-T'):
                cur.execute('INSERT INTO ref_subdivision_noeud(parent_id,code,libelle) VALUES(%s,%s,%s)',(parent,code,code));ids[code]=cur.lastrowid;parent=cur.lastrowid
            cur.execute("INSERT INTO ref_subdivision_noeud(parent_id,code,libelle) VALUES(%s,'SYN-T2','SYN-T2')",(ids['SYN-D'],));ids['T2']=cur.lastrowid
            for key,structure,leaf,label in [('A','KEEP','SYN-T','ALPHA'),('B','KEEP','T2','BETA'),('S','SOURCE','SYN-T','ALPHA')]:
                cur.execute('INSERT INTO ref_subdivision_arme(structure_id,noeud_feuille_id,libelle) VALUES(%s,%s,%s)',(ids[structure],ids[leaf],label));ids[key]=cur.lastrowid
            cur.execute("UPDATE carriere_situation SET structure_id=%s,structure_code='SOURCE',subdivision_arme_id=%s,subdivision_arme_libelle='ALPHA' WHERE id=1",(ids['SOURCE'],ids['S']))
            cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,structure_id,structure_code,subdivision_arme_id,subdivision_arme_libelle) VALUES(2,1,%s,'KEEP',%s,'BETA')",(ids['KEEP'],ids['B']))
            cur.execute("INSERT INTO agent_affectation(agent_id,structure_id,structure_code,date_affectation) VALUES(1,%s,'SOURCE','2026-09-01')",(ids['SOURCE'],))
            cur.execute("INSERT INTO ref_fonction(libelle) VALUES('SYNTHETIC FUNCTION')");ids['function']=cur.lastrowid
            cur.execute('INSERT INTO ref_fonction_structure(structure_id,fonction_id,ordre_tri) VALUES(%s,%s,7)',(ids['SOURCE'],ids['function']))
            cur.execute("INSERT INTO structure_organigram_node(structure_id,titre,rsa_id) VALUES(%s,'SYNTHETIC NODE',%s)",(ids['SOURCE'],ids['S']));ids['org']=cur.lastrowid
            payload={'sections':[{'id':'keep','order':0,'structure_code':'KEEP','subdivision_order':['ALPHA','BETA'],'subdivision_rsa_order':[ids['A'],ids['B']]},{'id':'source','order':1,'structure_code':'SOURCE'}]}
            cur.execute("INSERT INTO app_config(config_key,payload) VALUES('liste_present_layout',%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload)",(json.dumps(payload),))
    return settings,connect,params,ids,MySQLSubdivHierarchyRepository(settings),SubdivHierarchyService(settings),ReferentielCoherenceService(settings)


def expected(rows):
    token=getattr(rows,'write_snapshot',None)
    return {'expected':token} if token is not None else {}


def node_call(repo,nid,label,rows):return repo.update_node(nid,'SYN-T',label,None,**expected(rows))


def snapshot(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            result=[]
            for table in ('ref_subdivision_noeud','ref_structure','ref_subdivision_arme','carriere_situation','agent_affectation','ref_fonction_structure','structure_organigram_node','app_config'):
                cur.execute('SELECT * FROM '+table+' ORDER BY 1');result.append(cur.fetchall())
            return result


def sub_args(ids):return dict(keeper_rsa_id=ids['A'],target_libelle='UNIFIED',merge_rsa_ids=[ids['A'],ids['B']],merge_libelles=['ALPHA','BETA'])


def test_hierarchy_lot30_stale_node_edit_is_rejected(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=repo.fetch_tree();node_call(repo,ids['SYN-T'],'FIRST',rows)
    with pytest.raises(ValueError,match='modifi|périm|Actualisez'):
        node_call(repo,ids['SYN-T'],'SECOND',rows)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT libelle FROM ref_subdivision_noeud WHERE id=%s',(ids['SYN-T'],));assert cur.fetchone()==('FIRST',)


def test_hierarchy_lot30_edit_publishes_shared_revision(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    before=read_data_revision(settings);node_call(repo,ids['SYN-T'],'FIRST',repo.fetch_tree())
    assert read_data_revision(settings)!=before


def test_hierarchy_lot30_leaf_cannot_receive_a_fourth_level(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=repo.fetch_tree()
    with pytest.raises(ValueError,match='niveau|feuille'):
        repo.insert_node(ids['SYN-T'],'BAD','BAD',None,**expected(rows))


def test_merge_lot30_layout_failure_rolls_back_reference_and_signal(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.subdiv_variants(ids['KEEP'],'KEEP');before=snapshot(connect,params)
    def fail(*args,**kwargs):raise RuntimeError('synthetic layout failure')
    monkeypatch.setattr(merge,'_sync_layout_after_subdiv',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids),**expected(rows))
    assert snapshot(connect,params)==before


def test_merge_lot30_stale_subdivision_selection_is_rejected(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.subdiv_variants(ids['KEEP'],'KEEP')
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE ref_subdivision_arme SET libelle='CHANGED' WHERE id=%s",(ids['B'],))
    before=snapshot(connect,params)
    with pytest.raises(ValueError,match='modifi|périm|Actualisez'):
        merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids),**expected(rows))
    assert snapshot(connect,params)==before


def test_merge_lot30_structure_merge_preserves_dependent_links(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.structure_variants();before=read_data_revision(settings)
    merge.apply_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',target_code='KEEP',target_designation=None,
        absorb_structure_ids=[ids['SOURCE']],absorb_structure_codes=['SOURCE'],absorb_text_codes=[],**expected(rows))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT structure_id,structure_code FROM agent_affectation');assert cur.fetchall()==((ids['KEEP'],'KEEP'),)
            cur.execute('SELECT structure_id,fonction_id,ordre_tri FROM ref_fonction_structure');assert cur.fetchall()==((ids['KEEP'],ids['function'],7),)
            cur.execute('SELECT structure_id,rsa_id FROM structure_organigram_node WHERE id=%s',(ids['org'],));assert cur.fetchone()==(ids['KEEP'],ids['A'])
            cur.execute('SELECT structure_id,structure_code FROM carriere_situation ORDER BY id');assert all(row==(ids['KEEP'],'KEEP') for row in cur.fetchall())
    assert read_data_revision(settings)!=before


@pytest.mark.parametrize('kind',['hierarchy','merge_structure'])
def test_guard_lot30_displayed_and_locking_reads_agree(isolated_mysql,monkeypatch,kind):
    from sme_gend.repositories.hierarchy_merge_guard import load_snapshot
    from sme_gend.services.shared_reference_write import shared_reference_write
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    before=load_snapshot(settings,kind)
    with shared_reference_write(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:current=load_snapshot(settings,kind,cursor=cur,locking=True)
        assert before.parts==current.parts
        assert before.revision==current.revision


@pytest.mark.parametrize('operation',['node','subdivision','structure'])
def test_maintenance_lot30_missing_version_never_writes(isolated_mysql,monkeypatch,operation):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch);before=snapshot(connect,params)
    with pytest.raises(ValueError,match='Version'):
        if operation=='node':repo.update_node(ids['SYN-T'],'SYN-T','BAD')
        elif operation=='subdivision':merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids))
        else:merge._repo.apply_structure_merge(keeper_structure_id=ids['KEEP'],target_code='KEEP',target_designation=None,absorb_structure_ids=[ids['SOURCE']],absorb_text_codes=[])
    assert snapshot(connect,params)==before


@pytest.mark.parametrize('change',['leaf','targets','career'])
def test_propagation_lot30_detects_changes_during_confirmation(isolated_mysql,monkeypatch,change):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    token=svc.prepare_propagation(ids['SYN-T'],**expected(repo.fetch_tree()))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if change=='leaf':cur.execute("UPDATE ref_subdivision_noeud SET libelle='OTHER' WHERE id=%s",(ids['SYN-T'],))
            elif change=='targets':cur.execute("INSERT INTO ref_structure(code) VALUES('LATE')")
            else:cur.execute("UPDATE carriere_situation SET subdivision_arme_libelle='LATE' WHERE id=1")
    before=snapshot(connect,params)
    with pytest.raises(ValueError,match='modifi'):
        svc.propagate_leaf_rsa(ids['SYN-T'],all_structures=True,expected=token)
    assert snapshot(connect,params)==before


def test_propagation_lot30_noop_has_no_new_revision(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    def apply():return svc.propagate_leaf_rsa(ids['SYN-T'],all_structures=True,expected=svc.prepare_propagation(ids['SYN-T'],**expected(repo.fetch_tree())))
    before=read_data_revision(settings);assert apply()==2
    revision=read_data_revision(settings);assert revision!=before
    assert apply()==0
    assert read_data_revision(settings)==revision


@pytest.mark.parametrize('operation',['propagation','subdivision','structure'])
def test_maintenance_lot30_signal_failure_rolls_back_everything(isolated_mysql,monkeypatch,operation):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    tree=repo.fetch_tree();sub=merge.subdiv_variants(ids['KEEP'],'KEEP');struct=merge.structure_variants()
    token=svc.prepare_propagation(ids['SYN-T'],**expected(tree));before=snapshot(connect,params)
    def fail(*a,**kw):raise RuntimeError('synthetic publication failure')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        if operation=='propagation':svc.propagate_leaf_rsa(ids['SYN-T'],all_structures=True,expected=token)
        elif operation=='subdivision':merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids),**expected(sub))
        else:merge.apply_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',target_code='FINAL',target_designation=None,
            absorb_structure_ids=[ids['SOURCE']],absorb_structure_codes=['SOURCE'],absorb_text_codes=[],**expected(struct))
    assert snapshot(connect,params)==before


@pytest.mark.parametrize('commit',[False,True])
def test_hierarchy_lot30_visibility_and_caches_follow_real_commit(isolated_mysql,monkeypatch,commit):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    calls=[];monkeypatch.setattr('sme_gend.services.shared_reference_write._invalidate_reference_caches',lambda:calls.append('invalidate'))
    rows=repo.fetch_tree()
    def observe():
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT libelle FROM ref_subdivision_noeud WHERE id=%s',(ids['SYN-T'],));value=cur.fetchone()
                cur.execute("SELECT payload FROM app_config WHERE config_key='shared_data_revision'");return value,cur.fetchone()
    before=observe()
    class Abort(Exception):pass
    try:
        with mysql_import_transaction(settings):
            node_call(repo,ids['SYN-T'],'COMMITTED',rows)
            assert observe()==before and not calls
            if not commit:raise Abort()
    except Abort:pass
    if commit:assert observe()[0]==('COMMITTED',) and observe()[1]!=before[1] and calls==['invalidate']
    else:assert observe()==before and not calls


@pytest.mark.parametrize('kind',['node','merge'])
def test_maintenance_lot30_simultaneous_edits_have_one_winner(isolated_mysql,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=repo.fetch_tree() if kind=='node' else merge.subdiv_variants(ids['KEEP'],'KEEP')
    barrier=Barrier(2)
    def apply(index):
        barrier.wait(timeout=5)
        try:
            if kind=='node':node_call(repo,ids['SYN-T'],'WINNER '+str(index),rows)
            else:merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**(sub_args(ids)|{'target_libelle':'WINNER '+str(index)}),**expected(rows))
            return True
        except ReferenceConflictError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(apply,[1,2]))
    assert sorted(results)==[False,True]
    assert read_data_revision(settings)


def test_merge_lot30_foreign_subdivision_id_is_refused(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.subdiv_variants(ids['KEEP'],'KEEP');before=snapshot(connect,params)
    with pytest.raises(ValueError,match='hors'):
        merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**(sub_args(ids)|{'merge_rsa_ids':[ids['A'],ids['S']]}),**expected(rows))
    assert snapshot(connect,params)==before


@pytest.mark.parametrize('kind',['layout','function_link','affectation','organigram'])
def test_merge_lot30_changed_dependencies_invalidate_selection(isolated_mysql,monkeypatch,kind):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.structure_variants()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if kind=='layout':cur.execute("UPDATE app_config SET payload=JSON_SET(payload,'$.ncols',19) WHERE config_key='liste_present_layout'")
            elif kind=='function_link':cur.execute('UPDATE ref_fonction_structure SET ordre_tri=99')
            elif kind=='affectation':cur.execute("UPDATE agent_affectation SET structure_code='OTHER'")
            else:cur.execute("UPDATE structure_organigram_node SET titre='OTHER'")
    before=snapshot(connect,params)
    with pytest.raises(ValueError,match='modifi'):
        merge.apply_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',target_code='KEEP',target_designation=None,
            absorb_structure_ids=[ids['SOURCE']],absorb_structure_codes=['SOURCE'],absorb_text_codes=[],**expected(rows))
    assert snapshot(connect,params)==before


def test_merge_lot30_can_take_the_code_of_an_absorbed_structure(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    merge.apply_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',target_code='SOURCE',target_designation=None,
        absorb_structure_ids=[ids['SOURCE']],absorb_structure_codes=['SOURCE'],absorb_text_codes=[],**expected(merge.structure_variants()))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT code FROM ref_structure');assert cur.fetchall()==(('SOURCE',),)
            cur.execute("SELECT payload FROM app_config WHERE config_key='liste_present_layout'");layout=json.loads(cur.fetchone()[0])
            assert [s['structure_code'] for s in layout['sections']]==['SOURCE']
            assert layout['sections'][0]['subdivision_rsa_order']==[ids['A'],ids['B']]


def test_hierarchy_lot30_empty_database_gets_a_root_in_the_first_write(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_common import apply_mysql_schema
    from sme_gend.repositories.mysql_subdiv_hierarchy_repository import MySQLSubdivHierarchyRepository
    settings,connect,params=isolated_mysql;apply_mysql_schema(settings)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda **kw:'SYNTHETIC')
    repo=MySQLSubdivHierarchyRepository(settings);rows=repo.fetch_tree();assert rows==[]
    uid=repo.insert_node(1,'FIRST','FIRST',**expected(rows));tree=repo.fetch_tree()
    assert tree[0].node_id==uid and tree[0].level==1 and read_data_revision(settings)



def test_hierarchy_lot30_check_bypasses_old_repeatable_read_snapshot(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch);rows=repo.fetch_tree()
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('SELECT * FROM ref_subdivision_noeud');cur.fetchall()
        with connect(**params) as conn:
            with conn.cursor() as cur:cur.execute("UPDATE ref_subdivision_noeud SET libelle='OTHER COMMIT' WHERE id=%s",(ids['SYN-T'],))
        with pytest.raises(ValueError,match='modifi'):node_call(repo,ids['SYN-T'],'STALE',rows)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT libelle FROM ref_subdivision_noeud WHERE id=%s',(ids['SYN-T'],));assert cur.fetchone()==('OTHER COMMIT',)
    assert not read_data_revision(settings)


def test_merge_lot30_caught_failure_uses_savepoint_in_external_transaction(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=merge.subdiv_variants(ids['KEEP'],'KEEP');before=snapshot(connect,params)
    def fail(*a,**kw):raise RuntimeError('synthetic layout failure')
    monkeypatch.setattr(merge,'_sync_layout_after_subdiv',fail)
    with mysql_import_transaction(settings):
        with pytest.raises(RuntimeError,match='synthetic'):
            merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids),**expected(rows))
        assert snapshot(connect,params)==before
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute("INSERT INTO ref_fonction(libelle) VALUES('UNRELATED OUTER CHANGE')")
    assert snapshot(connect,params)==before


def test_merge_lot30_subdivision_preview_includes_id_linked_rows_with_missing_text(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE carriere_situation SET subdivision_arme_libelle=NULL WHERE agent_id=2')
    preview=merge.preview_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids))
    assert preview.carriere_rows==1


def test_merge_lot30_structure_preview_counts_a_selected_row_once(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    preview=merge.preview_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',absorb_structure_ids=[ids['SOURCE'],ids['SOURCE']],absorb_text_codes=['SOURCE','SOURCE'])
    assert preview.carriere_rows==1 and preview.rsa_moved==1 and preview.structures_removed==1



def test_merge_lot30_unresolved_external_structure_is_not_relabelled(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE carriere_situation SET structure_id=NULL,structure_code='EXTERNAL' WHERE agent_id=2")
    rows=merge.subdiv_variants(ids['KEEP'],'KEEP');before=snapshot(connect,params)
    with pytest.raises(ValueError,match='autre structure'):
        merge.apply_subdiv_merge(ids['KEEP'],'KEEP',**sub_args(ids),**expected(rows))
    assert snapshot(connect,params)==before


def test_merge_lot30_existing_function_order_and_org_parent_are_preserved(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO ref_fonction_structure(structure_id,fonction_id,ordre_tri) VALUES(%s,%s,2)',(ids['KEEP'],ids['function']))
            cur.execute("INSERT INTO structure_organigram_node(structure_id,parent_id,titre,pos_x,pos_y) VALUES(%s,%s,'CHILD',12,34)",(ids['SOURCE'],ids['org']));child=cur.lastrowid
    merge.apply_structure_merge(keeper_structure_id=ids['KEEP'],keeper_code='KEEP',target_code='KEEP',target_designation=None,
        absorb_structure_ids=[ids['SOURCE']],absorb_structure_codes=['SOURCE'],absorb_text_codes=[],**expected(merge.structure_variants()))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT structure_id,fonction_id,ordre_tri FROM ref_fonction_structure');assert cur.fetchall()==((ids['KEEP'],ids['function'],2),)
            cur.execute('SELECT structure_id,parent_id,titre,pos_x,pos_y FROM structure_organigram_node WHERE id=%s',(child,));assert cur.fetchone()==(ids['KEEP'],ids['org'],'CHILD',12,34)


def test_hierarchy_lot30_foreign_database_token_is_refused_before_writing(isolated_mysql,monkeypatch):
    settings,connect,params,ids,repo,svc,merge=setup(isolated_mysql,monkeypatch)
    rows=repo.fetch_tree();token=replace(rows.write_snapshot,database=('other',9999,'other'))
    before=snapshot(connect,params)
    with pytest.raises(ValueError,match='Version'):
        repo.update_node(ids['SYN-T'],'SYN-T','BAD',expected=token)
    assert snapshot(connect,params)==before
