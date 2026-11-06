"""Service métier — hiérarchie subdivisions U→D→T."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode
from sme_gend.repositories.mysql_subdiv_hierarchy_repository import MySQLSubdivHierarchyRepository
from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db

_ROOT_ID = 1


class SubdivHierarchyService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLSubdivHierarchyRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLSubdivHierarchyRepository(settings)

    def fetch_tree(self) -> list[SubdivHierarchyNode]:
        return self._repo.fetch_tree()

    def insert_u(self, code: str, libelle: str, pri_out: float | None = None, *, expected=None) -> int:
        return self._repo.insert_node(_ROOT_ID, code, libelle, pri_out, expected=expected)

    def insert_child(
        self,
        parent_id: int,
        code: str,
        libelle: str,
        pri_out: float | None = None,
        *, expected=None,
    ) -> int:
        return self._repo.insert_node(parent_id, code, libelle, pri_out, expected=expected)

    def update_node(
        self,
        node_id: int,
        code: str,
        libelle: str,
        pri_out: float | None = None,
        *, expected=None,
    ) -> None:
        self._repo.update_node(node_id, code, libelle, pri_out, expected=expected)

    def delete_node(self, node_id: int, *, expected=None) -> None:
        self._repo.delete_node(node_id, expected=expected)

    def prepare_propagation(self,leaf_node_id,*,expected=None):
        from sme_gend.repositories.hierarchy_merge_guard import load_snapshot,require_snapshot,compare
        from dataclasses import replace
        from sme_gend.repositories.reference_collection_guard import fingerprint
        require_snapshot(self._settings,expected,'hierarchy')
        snapshot=load_snapshot(self._settings,'propagation',(int(leaf_node_id),))
        tree=replace(snapshot,kind='hierarchy',key=(),parts=snapshot.parts[:2],revision=fingerprint(snapshot.parts[:2]))
        compare(expected,tree)
        return snapshot

    def propagate_leaf_rsa(self,leaf_node_id,*,libelle=None,structure_ids=None,all_structures=False,expected=None):
        from sme_gend.repositories.hierarchy_merge_guard import maintenance_write,hierarchy_rows
        from sme_gend.services.referentiel_maintenance import propagate_t_leaf_to_structures
        leaf=int(leaf_node_id)
        with maintenance_write(self._settings,expected,'propagation',(leaf,)) as (cur,snapshot):
            nodes,levels=hierarchy_rows(snapshot)
            if levels.get(leaf)!=3:raise ValueError('Seule une feuille de niveau T peut etre propagee.')
            known={int(row[0]) for row in snapshot.parts[2]}
            if structure_ids is not None and not set(map(int,structure_ids))<=known:
                raise ValueError('Une structure cible est absente. Actualisez les donnees.')
            if libelle is not None and len(libelle.strip())>512:raise ValueError('Libelle trop long.')
            cache=load_ref_cache_from_db(cur)
            propagate_t_leaf_to_structures(cur,cache,leaf_node_id=leaf,libelle=libelle,
                structure_ids=structure_ids,only_structures_with_carriere=not all_structures)
            before={row[0]:row for row in snapshot.parts[1]}
            cur.execute('SELECT id,structure_id,noeud_feuille_id,libelle,ordre_tri,actif FROM ref_subdivision_arme')
            return sum(tuple(row)!=before.get(row[0]) for row in cur.fetchall())

    def node_usage(self, node_id: int) -> tuple[int, int]:
        return self._repo.node_usage(node_id)
