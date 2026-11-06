"""Harmonisation manuelle référentiels (structures + SubArme), toutes archives."""

from __future__ import annotations

from dataclasses import dataclass
from sme_gend.repositories.hierarchy_merge_guard import maintenance_write

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_coherence_repository import (
    MySQLCoherenceRepository,
    StructureMergePreview,
    StructureVariantRow,
    SubdivMergePreview,
    SubdivVariantRow,
    coherence_repository,
)
from sme_gend.services.layout_config_service import LayoutConfigService


@dataclass(slots=True)
class SubdivMergeResult:
    carriere_updated: int
    rsa_merged: int


@dataclass(slots=True)
class StructureMergeResult:
    carriere_updated: int
    rsa_relinked: int
    structures_removed: int


class ReferentielCoherenceService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = coherence_repository(settings)
        self._layout = LayoutConfigService(settings)

    def subdiv_variants(self, structure_id: int, structure_code: str) -> list[SubdivVariantRow]:
        return self._repo.list_subdiv_variants(structure_id, structure_code)

    def structure_variants(self) -> list[StructureVariantRow]:
        return self._repo.list_structure_variants()

    def preview_subdiv_merge(
        self,
        structure_id: int,
        structure_code: str,
        *,
        keeper_rsa_id: int | None,
        target_libelle: str,
        merge_rsa_ids: list[int],
        merge_libelles: list[str],
    ) -> SubdivMergePreview:
        return self._repo.preview_subdiv_merge(
            structure_id,
            structure_code,
            keeper_rsa_id=keeper_rsa_id,
            target_libelle=target_libelle,
            merge_rsa_ids=merge_rsa_ids,
            merge_libelles=merge_libelles,
        )

    def apply_subdiv_merge(self,structure_id,structure_code,*,keeper_rsa_id,target_libelle,merge_rsa_ids,merge_libelles,expected=None):
        with maintenance_write(self._settings,expected,'merge_subdiv',(int(structure_id),structure_code.strip())):
            carriere_n,rsa_m=self._repo.apply_subdiv_merge(structure_id,structure_code,keeper_rsa_id=keeper_rsa_id,
                target_libelle=target_libelle,merge_rsa_ids=merge_rsa_ids,merge_libelles=merge_libelles,expected=expected)
            self._sync_layout_after_subdiv(structure_code,merge_libelles,target_libelle)
            return SubdivMergeResult(carriere_n,rsa_m)

    def preview_structure_merge(
        self,
        *,
        keeper_structure_id: int,
        keeper_code: str,
        absorb_structure_ids: list[int],
        absorb_text_codes: list[str],
    ) -> StructureMergePreview:
        return self._repo.preview_structure_merge(
            keeper_structure_id=keeper_structure_id,
            keeper_code=keeper_code,
            absorb_structure_ids=absorb_structure_ids,
            absorb_text_codes=absorb_text_codes,
        )

    def apply_structure_merge(self,*,keeper_structure_id,keeper_code,target_code,target_designation,absorb_structure_ids,
                              absorb_structure_codes,absorb_text_codes,expected=None):
        with maintenance_write(self._settings,expected,'merge_structure') as (cur,snapshot):
            actual={int(row[0]):str(row[1]) for row in snapshot.parts[2]}
            if actual.get(int(keeper_structure_id))!=keeper_code:raise ValueError('Structure gardienne incoherente. Actualisez les donnees.')
            actual_sources=[actual[int(sid)] for sid in absorb_structure_ids if int(sid)!=int(keeper_structure_id) and int(sid) in actual]
            if set(actual_sources)!=set(absorb_structure_codes):raise ValueError('Codes absorbes incoherents avec la selection.')
            carriere_n,rsa_n,removed=self._repo.apply_structure_merge(keeper_structure_id=keeper_structure_id,
                target_code=target_code,target_designation=target_designation,absorb_structure_ids=absorb_structure_ids,
                absorb_text_codes=absorb_text_codes,expected=expected)
            self._sync_layout_after_structure_merge(keeper_code=keeper_code,target_code=target_code.strip(),
                absorbed_codes=[*absorb_text_codes,*actual_sources])
            return StructureMergeResult(carriere_n,rsa_n,removed)

    def _sync_layout_after_subdiv(
        self, structure_code: str, old_labels: list[str], new_label: str
    ) -> None:
        sc = (structure_code or "").strip()
        new_l = (new_label or "").strip()
        cfg = self._layout.load()
        for sec in cfg.sections:
            if sec.structure_code.strip() != sc:
                continue
            if sec.subdivision_order:
                merged_order: list[str] = []
                seen: set[str] = set()
                for x in sec.subdivision_order:
                    rep = new_l if x.strip() in {o.strip() for o in old_labels} else x.strip()
                    if rep == new_l:
                        if new_l not in seen:
                            merged_order.append(new_l)
                            seen.add(new_l)
                    elif rep and rep not in seen:
                        merged_order.append(rep)
                        seen.add(rep)
                sec.subdivision_order = merged_order or None
        cfg, _ = self._layout.sync_missing_subdivisions(cfg)
        self._clean_rsa_order(cfg,{sc})
        self._layout.save(cfg)

    def _sync_layout_after_structure_merge(self,*,keeper_code,target_code,absorbed_codes):
        cfg=self._layout.load()
        codes={code.strip().casefold() for code in [keeper_code,*absorbed_codes] if code.strip()}
        selected=[section for section in cfg.sections if section.structure_code.strip().casefold() in codes]
        keeper=next((section for section in selected if section.structure_code.strip().casefold()==keeper_code.strip().casefold()),None)
        if keeper is None and selected:keeper=selected[0]
        if keeper is not None:
            labels=[];ids=[]
            for section in [keeper,*[section for section in selected if section is not keeper]]:
                labels.extend(section.subdivision_order or []);ids.extend(section.subdivision_rsa_order or [])
            keeper.structure_code=target_code
            keeper.subdivision_order=list(dict.fromkeys(labels)) or None
            keeper.subdivision_rsa_order=list(dict.fromkeys(ids)) or None
            cfg.sections=[section for section in cfg.sections if section is keeper or section not in selected]
        else:
            cfg,_=self._layout.sync_missing_subdivisions(cfg)
        self._clean_rsa_order(cfg,{target_code})
        self._layout.save(cfg)

    def _clean_rsa_order(self,cfg,codes):
        from sme_gend.repositories.mysql_connection import MySQLConnection
        with MySQLConnection(self._settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT s.code,r.id,r.libelle FROM ref_subdivision_arme r JOIN ref_structure s ON s.id=r.structure_id ORDER BY r.ordre_tri IS NULL,r.ordre_tri,r.id')
                by_code={}
                for code,rid,label in cur.fetchall():by_code.setdefault(code,[]).append((int(rid),str(label or '').strip()))
        for section in cfg.sections:
            if section.structure_code not in codes:continue
            pairs=by_code.get(section.structure_code,[]);valid={rid for rid,_ in pairs}
            if section.subdivision_rsa_order is not None:
                keep=list(dict.fromkeys(rid for rid in section.subdivision_rsa_order if rid in valid))
                section.subdivision_rsa_order=keep+[rid for rid,_ in pairs if rid not in keep] or None
