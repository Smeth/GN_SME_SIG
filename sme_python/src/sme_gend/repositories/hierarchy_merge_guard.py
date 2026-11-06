"""Displayed maintenance scopes, bound to their database and checked before writes."""
from contextlib import contextmanager
from dataclasses import dataclass,field
import json,re
from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_database_key
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import shared_reference_write,mark_reference_write,has_reference_write_lock


@dataclass(frozen=True,slots=True)
class MaintenanceSnapshot:
    kind:str
    key:tuple
    database:tuple=field(repr=False)
    revision:str=field(repr=False)
    parts:tuple=field(repr=False)


class VersionedRows(list):
    def __init__(self,rows,snapshot):
        super().__init__(rows)
        self.write_snapshot=snapshot


def _plans(kind):
    plans=[('id,parent_id,code,libelle,pri_out','ref_subdivision_noeud',''),
           ('id,structure_id,noeud_feuille_id,libelle,ordre_tri,actif','ref_subdivision_arme','')]
    if kind=='hierarchy':return plans
    plans += [('id,code,designation,ordre_tri,famille_unite','ref_structure',''),
              ('id,structure_id,structure_code,subdivision_arme_id,subdivision_arme_libelle','carriere_situation','')]
    if kind=='propagation':return plans
    if kind not in ('merge_subdiv','merge_structure'):raise ValueError('Operation de maintenance inconnue.')
    return plans+[
        ('id,structure_id,structure_code','agent_affectation',''),
        ('id,structure_id,fonction_id,ordre_tri','ref_fonction_structure',''),
        ('id,structure_id,parent_id,titre,titulaire,rsa_id,ordre_tri,pos_x,pos_y,actif','structure_organigram_node',''),
        ('config_key,payload','app_config'," WHERE config_key='liste_present_layout'")]


def _snapshot(settings,kind,key,parts):
    if kind in ('merge_subdiv','merge_structure'):
        # JSON_ARRAY embeds a JSON column as an object; native cursor reads
        # return its encoded text. Compare their semantic payload identically.
        parts=list(parts)
        parts[7]=[(row[0],json.loads(row[1]) if isinstance(row[1],(str,bytes)) else row[1]) for row in (parts[7] or [])]
    parts=tuple(tuple(sorted((tuple(row) for row in (part or [])),key=lambda row:json.dumps(row,ensure_ascii=False))) for part in parts)
    return MaintenanceSnapshot(kind,tuple(key),mysql_database_key(settings),fingerprint(parts),parts)


def load_snapshot(settings,kind,key=(),*,cursor=None,locking=False):
    plans=_plans(kind)
    if locking:
        if cursor is None or not has_reference_write_lock(settings):raise RuntimeError('Verrou de maintenance absent.')
        parts=[]
        for fields,table,where in plans:
            cursor.execute('SELECT '+fields+' FROM '+table+where+' ORDER BY 1 FOR UPDATE')
            parts.append(cursor.fetchall())
        return _snapshot(settings,kind,key,parts)
    # A single statement gives the tree, choices, dependants and layout from
    # one committed state. Identifiers come only from the closed plans above.
    parts=['(SELECT JSON_ARRAYAGG(JSON_ARRAY('+fields+')) FROM '+table+where+')' for fields,table,where in plans]
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT '+','.join(parts));raw=cur.fetchone()
    return _snapshot(settings,kind,key,[json.loads(v) if isinstance(v,(str,bytes)) else v for v in raw])


def require_snapshot(settings,expected,kind,key=()):
    if (not isinstance(expected,MaintenanceSnapshot) or expected.kind!=kind or expected.key!=tuple(key)
        or expected.database!=mysql_database_key(settings) or re.fullmatch('[0-9a-f]{64}',expected.revision or '') is None
        or fingerprint(expected.parts)!=expected.revision):
        raise ValueError('Version de maintenance indisponible. Actualisez les donnees avant de recommencer.')


def compare(expected,current):
    if current.revision!=expected.revision:
        raise ReferenceConflictError('Ces donnees ont ete modifiees depuis leur chargement. Aucune modification enregistree. Actualisez avant de reprendre ; votre saisie est conservee.')


def assert_permission():
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission('structures.codes'):raise PermissionError('Permission structures.codes requise.')


@contextmanager
def maintenance_write(settings,expected,kind,key=()):
    assert_permission();require_snapshot(settings,expected,kind,key)
    with shared_reference_write(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                before=load_snapshot(settings,kind,key,cursor=cur,locking=True);compare(expected,before)
                yield cur,before
                after=load_snapshot(settings,kind,key,cursor=cur,locking=True)
                mark_reference_write(after.revision!=before.revision)


def versioned_variants(kind):
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def load(self,*args,**kwargs):
            key=(int(args[0] if args else kwargs['structure_id']),str(args[1] if len(args)>1 else kwargs['structure_code']).strip()) if kind=='merge_subdiv' else ()
            before=load_snapshot(self._settings,kind,key)
            rows=fn(self,*args,**kwargs)
            compare(before,load_snapshot(self._settings,kind,key))
            return VersionedRows(rows,before)
        return load
    return decorate


def hierarchy_rows(snapshot):
    nodes={int(row[0]):row for row in snapshot.parts[0]}
    if not nodes:nodes={1:(1,None,'ROOT','Racine',None)}
    if 1 not in nodes or nodes[1][1] is not None:raise ValueError('Racine de hierarchie invalide.')
    levels={1:0}
    for nid in nodes:
        current=nid;seen=set();level=0
        while current!=1:
            if current in seen or current not in nodes:raise ValueError('Hierarchie cyclique ou parent absent.')
            seen.add(current);level+=1
            if level>3:raise ValueError('La hierarchie comporte uniquement les niveaux U, D et T.')
            parent=nodes[current][1]
            if parent is None:raise ValueError('Parent de hierarchie absent.')
            current=int(parent)
        levels[nid]=level
    return nodes,levels
