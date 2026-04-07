"""Chargement / enregistrement du fichier JSON de mise en page (sections)."""

from __future__ import annotations

import json
from pathlib import Path

from sme_gend.config.settings import Settings, liste_layout_config_path
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.repositories.structure_repository import StructureRepository


class LayoutConfigService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)

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
