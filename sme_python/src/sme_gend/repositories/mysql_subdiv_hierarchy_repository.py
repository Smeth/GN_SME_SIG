"""Versioned CRUD of the shared U/D/T hierarchy."""
from collections import Counter
import math
from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.repositories.hierarchy_merge_guard import (
    load_snapshot,maintenance_write,hierarchy_rows,VersionedRows,
)


def _values(code,libelle,pri_out):
    code=(code or '').strip().upper();libelle=(libelle or '').strip()
    if not code or not libelle:raise ValueError('Code et libelle obligatoires.')
    if len(code)>64 or len(libelle)>512:raise ValueError('Code ou libelle trop long.')
    if pri_out is not None and not math.isfinite(float(pri_out)):raise ValueError('Priorite non finie.')
    return code,libelle,pri_out


class MySQLSubdivHierarchyRepository(BaseMySQLRepository):
    def fetch_tree(self):
        snapshot=load_snapshot(self._settings,'hierarchy')
        nodes,levels=hierarchy_rows(snapshot)
        counts=Counter(int(row[2]) for row in snapshot.parts[1])
        by_parent={}
        for nid,row in nodes.items():by_parent.setdefault(row[1],[]).append(nid)
        def build(nid):
            _,parent,code,label,pri=nodes[nid]
            children=sorted((build(i) for i in by_parent.get(nid,[])),key=lambda item:item.code)
            return SubdivHierarchyNode(nid,parent,str(code or '').strip(),str(label or '').strip(),
                float(pri) if pri is not None else None,levels[nid],counts[nid] if levels[nid]==3 else 0,children)
        return VersionedRows([build(nid) for nid,row in sorted(nodes.items()) if row[1]==1],snapshot)

    def insert_node(self,parent_id,code,libelle,pri_out=None,*,expected=None):
        values=_values(code,libelle,pri_out);pid=int(parent_id)
        with maintenance_write(self._settings,expected,'hierarchy') as (cur,snapshot):
            nodes,levels=hierarchy_rows(snapshot)
            if pid not in nodes or levels[pid]>=3:raise ValueError('Parent absent ou feuille T : aucun niveau supplementaire permis.')
            cur.execute('SELECT id FROM ref_subdivision_noeud WHERE parent_id=%s AND code=%s',(pid,values[0]))
            if cur.fetchone():raise ValueError('Ce code existe deja a ce niveau.')
            if not snapshot.parts[0]:
                from sme_gend.services.mysql_ref_etl import _ensure_subdiv_root
                _ensure_subdiv_root(cur)
            cur.execute('INSERT INTO ref_subdivision_noeud(parent_id,code,libelle,pri_out) VALUES(%s,%s,%s,%s)',(pid,*values))
            return int(cur.lastrowid)

    def update_node(self,node_id,code,libelle,pri_out=None,*,expected=None):
        values=_values(code,libelle,pri_out);nid=int(node_id)
        with maintenance_write(self._settings,expected,'hierarchy') as (cur,snapshot):
            nodes,_=hierarchy_rows(snapshot)
            if nid==1 or nid not in nodes:raise ValueError('Noeud absent ou racine technique non modifiable.')
            cur.execute('SELECT id FROM ref_subdivision_noeud WHERE parent_id=%s AND code=%s AND id<>%s',(nodes[nid][1],values[0],nid))
            if cur.fetchone():raise ValueError('Ce code existe deja a ce niveau.')
            cur.execute('UPDATE ref_subdivision_noeud SET code=%s,libelle=%s,pri_out=%s WHERE id=%s',(*values,nid))

    def delete_node(self,node_id,*,expected=None):
        nid=int(node_id)
        with maintenance_write(self._settings,expected,'hierarchy') as (cur,snapshot):
            nodes,_=hierarchy_rows(snapshot)
            if nid==1 or nid not in nodes:raise ValueError('Noeud absent ou racine technique non supprimable.')
            if any(row[1]==nid for row in nodes.values()):raise ValueError('Ce noeud a des enfants.')
            if any(row[2]==nid for row in snapshot.parts[1]):raise ValueError('Cette feuille est liee a des subdivisions.')
            cur.execute('DELETE FROM ref_subdivision_noeud WHERE id=%s',(nid,))

    def node_usage(self,node_id):
        snapshot=load_snapshot(self._settings,'hierarchy');nid=int(node_id)
        return sum(row[1]==nid for row in snapshot.parts[0]),sum(row[2]==nid for row in snapshot.parts[1])
