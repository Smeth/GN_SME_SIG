"""Service métier — organigramme structures / subdivisions."""

from __future__ import annotations

from functools import wraps
from sme_gend.services.action_permissions import requires_any_permission
from sme_gend.services.shared_reference_write import shared_reference_write


def _atomic_layout_reference_write(method):
    @wraps(method)
    def write(self,*args,**kwargs):
        from sme_gend.services.action_permissions import structure_layout_sync
        with structure_layout_sync(), shared_reference_write(self._settings):
            return method(self,*args,**kwargs)
    return write

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.models.organigram import ReferentielUsage, StructureOrganigramNode
from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.subdivision_repository import SubdivisionRepository
from sme_gend.services.layout_config_service import LayoutConfigService


@dataclass(frozen=True, slots=True)
class OrganigramQualitySummary:
    structure_count: int
    subdiv_ref_count: int
    subdiv_data_only_count: int
    total_agents: int


class StructureOrganigramService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._org = MySQLOrganigramRepository(settings)
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._layout = LayoutConfigService(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._org = MySQLOrganigramRepository(settings)
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._layout = LayoutConfigService(settings)

    def fetch_tree(self) -> list[StructureOrganigramNode]:
        return self._org.fetch_organigram()

    def subdivision_editor_snapshot(self):
        from sme_gend.repositories.hierarchy_merge_guard import load_snapshot
        return load_snapshot(self._settings, 'hierarchy')

    @requires_any_permission('structures.codes')
    def change_subdivision(self, action, structure_id, structure_code, *, label='', rsa_id=None, leaf_id=None, expected=None, hierarchy_path=None):
        """Versioned editor intent, including layout and dependent-row checks."""
        from sme_gend.repositories.hierarchy_merge_guard import maintenance_write, hierarchy_rows
        from sme_gend.services.shared_reference_write import mark_reference_write
        from sme_gend.services.business_audit import record_business_action
        label = (label or '').strip()
        if action not in {'create', 'rename', 'delete'}:
            raise ValueError('Action de subdivision inconnue.')
        if action != 'delete' and (not label or len(label)>512):
            raise ValueError('Libellé requis, 512 caractères maximum.')
        with maintenance_write(self._settings,expected,'hierarchy') as (cur,snapshot):
            cur.execute('SELECT code FROM ref_structure WHERE id=%s FOR UPDATE',(int(structure_id),))
            found=cur.fetchone()
            if not found or str(found[0]).strip()!=structure_code:
                raise ValueError('Structure modifiée ou supprimée. Actualisez.')
            before=next((row for row in snapshot.parts[1] if row[0]==rsa_id and row[1]==structure_id),None)
            if action != 'create' and before is None:
                raise ValueError('Subdivision modifiée ou supprimée. Actualisez.')
            if action != 'delete' and any(row[1]==structure_id and row[0]!=rsa_id and str(row[3] or '').strip().casefold()==label.casefold() for row in snapshot.parts[1]):
                raise ValueError('Ce libellé existe déjà dans cette structure.')
            if action=='create':
                if hierarchy_path is not None:
                    from sme_gend.services.subdivision_creation import create_path
                    from sme_gend.services.action_permissions import structure_layout_sync
                    leaf_id = create_path(cur, snapshot, hierarchy_path)
                    rsa_id = self._org.attach_subdivision_leaf(structure_id, leaf_id, label)
                    with structure_layout_sync():
                        cfg = self._layout.load()
                        cfg, _ = self._layout.sync_missing_subdivisions(cfg)
                        self._layout.save(cfg)
                else:
                    nodes, levels=hierarchy_rows(snapshot)
                    if leaf_id is None or levels.get(int(leaf_id))!=3:
                        raise ValueError('Choisissez une feuille T de la nomenclature.')
                    rsa_id=self.add_subdivision(structure_id,label,hierarchy_label=nodes[int(leaf_id)][3])
                    cur.execute('UPDATE ref_subdivision_arme SET noeud_feuille_id=%s WHERE id=%s',(int(leaf_id),rsa_id))
                    mark_reference_write(cur.rowcount>0)
            elif action=='rename':
                self.rename_subdivision(rsa_id,str(before[3]),label,structure_code)
            else:
                old=str(before[3] or '')
                cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE subdivision_arme_id=%s OR '
                    '(TRIM(subdivision_arme_libelle)=%s AND (structure_id=%s OR structure_code=%s))',
                    (rsa_id,old,structure_id,structure_code))
                uses=int(cur.fetchone()[0])
                cur.execute('SELECT COUNT(*) FROM agent_affectation WHERE TRIM(subdivision_arme_libelle)=%s '
                    'AND (structure_id=%s OR structure_code=%s)',(old,structure_id,structure_code))
                uses+=int(cur.fetchone()[0])
                if uses:
                    raise ValueError(f'Suppression refusée : {uses} situation(s) ou affectation(s), toutes périodes confondues. Réaffectez-les avant suppression.')
                self.delete_subdivision(rsa_id,structure_code,old)
            record_business_action(self._settings,action='subdivision_'+action,module='reference',entity_id=rsa_id,
                before=before,after=None if action=='delete' else dict(structure=structure_code,libelle=label,leaf_id=leaf_id,hierarchy_path=hierarchy_path))
            return rsa_id

    def quality_summary(self, nodes: list[StructureOrganigramNode] | None = None) -> OrganigramQualitySummary:
        tree = nodes if nodes is not None else self.fetch_tree()
        ref_n = 0
        data_n = 0
        agents = 0
        for st in tree:
            agents += st.agent_count
            for sub in st.subdivisions:
                if sub.from_referentiel:
                    ref_n += 1
                else:
                    data_n += 1
        return OrganigramQualitySummary(
            structure_count=len(tree),
            subdiv_ref_count=ref_n,
            subdiv_data_only_count=data_n,
            total_agents=agents,
        )

    @requires_any_permission('structures.codes')
    def run_cleanup_wizard(self) -> tuple[int, int, tuple[list[str], list[str]]]:
        """Backfill RSA, rattachement subdivisions données, sync mise en page."""
        n_backfill = self.backfill_rsa_from_carriere()
        n_attach = 0
        for node in self.fetch_tree():
            n_attach += self.import_all_data_subdivisions(node.structure_id, node.code)
        sync = self.sync_layout_from_database()
        return n_backfill, n_attach, sync

    def subdivisions_flat(self):
        return self._subdivisions.fetch_flat_hierarchy()

    def load_order_structures_snapshot(self):
        return self._org.load_order_structures_snapshot()

    def load_order_subdivisions_snapshot(self,structure_id):
        return self._org.load_order_subdivisions_snapshot(structure_id)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def reorder_structures(self, ordered_codes: list[str], *, expected=None) -> None:
        self._org.reorder_structures(ordered_codes, expected=expected)
        cfg = self._layout.load()
        order_map = {c: i for i, c in enumerate(ordered_codes)}
        for sec in cfg.sections:
            if sec.structure_code in order_map:
                sec.order = order_map[sec.structure_code]
        cfg.sections.sort(key=lambda s: s.order)
        self._layout.save(cfg)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def reorder_subdivisions(
        self, structure_code: str, structure_id: int, ordered_rsa_ids: list[int], ordered_labels: list[str], *, expected=None
    ) -> None:
        self._org.reorder_subdivisions(structure_id, ordered_rsa_ids, expected=expected)
        cfg = self._layout.load()
        sc = (structure_code or "").strip()
        rsa_clean = [int(x) for x in ordered_rsa_ids if x]
        for sec in cfg.sections:
            if sec.structure_code.strip() != sc:
                continue
            sec.subdivision_order = list(ordered_labels)
            sec.subdivision_rsa_order = rsa_clean if rsa_clean else None
            break
        self._layout.save(cfg)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def move_subdivision_to_structure(
        self,
        rsa_id: int,
        from_structure_code: str,
        to_structure_id: int,
        to_structure_code: str,
        libelle: str,
    ) -> None:
        lib = (libelle or "").strip()
        from_sc = (from_structure_code or "").strip()
        to_sc = (to_structure_code or "").strip()
        self._org.move_subdivision_to_structure(int(rsa_id), int(to_structure_id), to_sc)
        cfg = self._layout.load()
        if from_sc and lib:
            cfg = self._layout.remove_subdivision_from_layout(cfg, from_sc, lib)
        cfg, _ = self._layout.sync_missing_subdivisions(cfg)
        self._layout.save(cfg)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def add_subdivision(
        self, structure_id: int, libelle: str, *, hierarchy_label: str | None = None
    ) -> int:
        rsa_id = self._org.add_subdivision(
            structure_id, libelle, hierarchy_label=hierarchy_label
        )
        cfg = self._layout.load()
        cfg, _ = self._layout.sync_missing_subdivisions(cfg)
        self._layout.save(cfg)
        return rsa_id

    @requires_any_permission('structures.codes')
    def attach_data_subdivision(
        self, structure_id: int, libelle: str, *, hierarchy_label: str | None = None
    ) -> int:
        return self.add_subdivision(structure_id, libelle, hierarchy_label=hierarchy_label)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def rename_subdivision(
        self, rsa_id: int, old_libelle: str, new_libelle: str, structure_code: str
    ) -> int:
        n = self._org.rename_subdivision(rsa_id, new_libelle, structure_code)
        cfg = self._layout.load()
        sc = structure_code.strip()
        old_l = old_libelle.strip()
        new_l = new_libelle.strip()
        for sec in cfg.sections:
            if sec.structure_code.strip() != sc:
                continue
            if sec.subdivision_order:
                sec.subdivision_order = [
                    new_l if x.strip() == old_l else x for x in sec.subdivision_order
                ]
        self._layout.save(cfg)
        return n

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def delete_subdivision(self, rsa_id: int, structure_code: str, libelle: str) -> None:
        self._org.delete_subdivision(rsa_id)
        cfg = self._layout.load()
        cfg = self._layout.remove_subdivision_from_layout(cfg, structure_code, libelle)
        self._layout.save(cfg)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def delete_structure(self, code: str, *, expected=None) -> None:
        self._org.safe_delete_structure(code, expected=expected)
        cfg = self._layout.load()
        cfg = self._layout.remove_structure_from_layout(cfg, code)
        self._layout.save(cfg)

    @requires_any_permission('referentiels.grades')
    def delete_grade(self, code: str, *, expected=None) -> None:
        self._org.safe_delete_grade(code, expected=expected)

    def grade_usage(self, code: str) -> ReferentielUsage:
        return self._org.count_grade_usage(code)

    def structure_usage(self, code: str) -> ReferentielUsage:
        return self._org.count_structure_usage(code)

    def subdiv_usage(self, rsa_id: int) -> ReferentielUsage:
        return self._org.count_subdiv_usage(rsa_id)

    @requires_any_permission('structures.codes')
    @_atomic_layout_reference_write
    def structure_insert(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
    ) -> None:
        self._structures.insert(
            code, designation, ordre_tri, famille_unite=famille_unite
        )
        cfg = self._layout.load()
        cfg = self._layout.merge_missing_structures(cfg)
        self._layout.save(cfg)

    @requires_any_permission('structures.codes')
    def structure_update(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
        expected=None,
    ) -> None:
        self._structures.update(
            code, designation, ordre_tri, famille_unite=famille_unite, expected=expected
        )

    @requires_any_permission('structures.codes', 'effectifs.layout')
    @_atomic_layout_reference_write
    def sync_layout_from_database(self) -> tuple[list[str], list[str]]:
        cfg = self._layout.load()
        cfg, new_structs, new_subs = self._layout.sync_all_from_database(cfg)
        self._layout.save(cfg)
        return new_structs, new_subs

    @requires_any_permission('structures.codes')
    def backfill_rsa_from_carriere(self, *, mois: str | None = None) -> int:
        from sme_gend.repositories.mysql_connection import MySQLConnection
        from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db
        from sme_gend.services.referentiel_maintenance import backfill_rsa_from_carriere

        mc = MySQLConnection(self._settings)
        with mc.connect() as conn:
            with conn.cursor() as cur:
                cache = load_ref_cache_from_db(cur)
                n = backfill_rsa_from_carriere(cur, cache, mois=mois)
            conn.commit()
        cfg = self._layout.load()
        cfg, _ = self._layout.sync_missing_subdivisions(cfg)
        self._layout.save(cfg)
        return n

    @requires_any_permission('structures.codes')
    def import_all_data_subdivisions(self, structure_id: int, structure_code: str) -> int:
        tree = self.fetch_tree()
        node = next((s for s in tree if s.structure_id == structure_id), None)
        if node is None:
            return 0
        n = 0
        for sub in node.subdivisions:
            if sub.from_referentiel or not sub.libelle:
                continue
            self.attach_data_subdivision(structure_id, sub.libelle)
            n += 1
        return n
