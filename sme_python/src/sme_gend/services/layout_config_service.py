"""Chargement / enregistrement du fichier JSON de mise en page (sections)."""

from __future__ import annotations

import json
from pathlib import Path

from sme_gend.config.settings import Settings, liste_layout_config_path
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository


class LayoutConfigService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._liste_generale = ListeGeneraleRepository(settings)

    def subarme_labels_for_structure(self, structure_code: str) -> list[str]:
        """Libellés SubArme distincts pour une structure (liste générale), triés comme en base."""
        return self._liste_generale.fetch_distinct_subarme_for_structure(structure_code)

    def path(self) -> Path:
        return liste_layout_config_path()

    def load(self) -> ListeLayoutConfig:
        p = self.path()
        if not p.is_file():
            cfg = self.build_default_from_database()
            self.save(cfg)
            return cfg
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return ListeLayoutConfig.from_dict(data)

    def save(self, cfg: ListeLayoutConfig) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)

    def build_default_from_database(self) -> ListeLayoutConfig:
        """Une section par entrée TabStructure (ordre OderTri)."""
        rows = self._structures.fetch_all()
        sections: list[SectionBlock] = []
        for i, st in enumerate(rows):
            sections.append(
                SectionBlock(
                    id=f"auto-{st.code}-{i}",
                    order=i,
                    structure_code=st.code,
                    title_main_override=None,
                    title_sub_override=None,
                    group_by_subdivision=True,
                )
            )
        return ListeLayoutConfig(sections=sections)

    def merge_missing_structures(self, cfg: ListeLayoutConfig) -> ListeLayoutConfig:
        """Ajoute en fin les codes présents en base mais absents de la config."""
        known = {s.structure_code for s in cfg.sections}
        db_codes = {s.code for s in self._structures.fetch_all()}
        max_order = max((s.order for s in cfg.sections), default=-1)
        new_secs = list(cfg.sections)
        o = max_order + 1
        for code in sorted(db_codes - known):
            from uuid import uuid4

            new_secs.append(
                SectionBlock(
                    id=str(uuid4()),
                    order=o,
                    structure_code=code,
                )
            )
            o += 1
        return ListeLayoutConfig(
            version=cfg.version,
            template_sheet=cfg.template_sheet,
            output_sheet=cfg.output_sheet,
            header_row=cfg.header_row,
            style_row_section=cfg.style_row_section,
            style_row_sub=cfg.style_row_sub,
            style_row_data=cfg.style_row_data,
            ncols=cfg.ncols,
            sections=new_secs,
        )

    def structure_row(self, code: str) -> StructureRow | None:
        """Référentiel TabStructure pour un code, ou None."""
        return self._structures.fetch_by_code(code)

    def update_structure_referentiel(
        self, code: str, designation: str, ordre_tri: int | None
    ) -> None:
        """Met à jour désignation et ordre de tri dans TabStructure (code inchangé)."""
        self._structures.update(code, designation, ordre_tri)

    def rename_structure_code(
        self, cfg: ListeLayoutConfig, old_code: str, new_code: str
    ) -> ListeLayoutConfig:
        """
        Renomme le code structure dans TabStructure, ListeGenerale.Structure
        et dans les sections de la mise en page (objet `cfg` modifié).
        """
        old_c = (old_code or "").strip()
        new_c = (new_code or "").strip()
        if not old_c or not new_c or old_c == new_c:
            raise ValueError("Codes invalides.")
        clash = self._structures.fetch_by_code(new_c)
        if clash is not None and clash.code != old_c:
            raise ValueError(f"Le code « {new_c} » existe déjà dans TabStructure.")
        self._structures.rename_code(old_c, new_c)
        self._liste_generale.update_structure_column(old_c, new_c)
        for sec in cfg.sections:
            if sec.structure_code.strip() == old_c:
                sec.structure_code = new_c
        return cfg

    def rename_subarme_in_database(
        self, cfg: ListeLayoutConfig, structure_code: str, old_label: str, new_label: str
    ) -> tuple[int, ListeLayoutConfig]:
        """
        Met à jour SubArme dans ListeGenerale et remplace le libellé dans subdivision_order
        de la section concernée (si présent).
        """
        n = self._liste_generale.rename_subarme_for_structure(
            structure_code, old_label, new_label
        )
        sc = (structure_code or "").strip()
        old_l = (old_label or "").strip()
        new_l = (new_label or "").strip()
        for sec in cfg.sections:
            if sec.structure_code.strip() != sc:
                continue
            if sec.subdivision_order:
                sec.subdivision_order = [
                    new_l if x == old_l else x for x in sec.subdivision_order
                ]
        return n, cfg
