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

    def rebind_settings(self, settings: Settings) -> None:
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

    def sync_missing_subdivisions(
        self, cfg: ListeLayoutConfig
    ) -> tuple[ListeLayoutConfig, list[str]]:
        """
        Pour chaque section dont ``group_by_subdivision`` est activé, ajoute en fin de
        ``subdivision_order`` les libellés SubArme présents en base mais absents de la liste.
        Retourne (cfg mis à jour, liste des nouveaux libellés ajoutés sous forme 'CODE: libellé').
        """
        new_items: list[str] = []
        for sec in cfg.sections:
            if not sec.group_by_subdivision:
                continue
            db_subs = set(self.subarme_labels_for_structure(sec.structure_code))
            if not db_subs:
                continue
            known = set(sec.subdivision_order or [])
            missing = sorted(db_subs - known)
            if missing:
                new_items.extend(f"[{sec.structure_code}] {s}" for s in missing)
                sec.subdivision_order = list(sec.subdivision_order or []) + missing
        return cfg, new_items

    def sync_all_from_database(
        self, cfg: ListeLayoutConfig
    ) -> tuple[ListeLayoutConfig, list[str], list[str]]:
        """
        Fusionne les structures manquantes et les subdivisions manquantes.
        Retourne (cfg mis à jour, nouvelles structures ajoutées, nouveaux libellés SubArme ajoutés).
        """
        known_before = {s.structure_code for s in cfg.sections}
        cfg = self.merge_missing_structures(cfg)
        new_structs = [
            s.structure_code for s in cfg.sections
            if s.structure_code not in known_before
        ]
        cfg, new_subs = self.sync_missing_subdivisions(cfg)
        return cfg, new_structs, new_subs

    def structure_row(self, code: str) -> StructureRow | None:
        """Référentiel TabStructure pour un code, ou None."""
        return self._structures.fetch_by_code(code)

    def all_structure_designations(self) -> dict[str, str]:
        """Code → désignation (ou code si absent) pour tous les enregistrements TabStructure."""
        return {s.code: (s.designation or s.code) for s in self._structures.fetch_all()}

    def present_counts_by_structure(self) -> dict[str, int]:
        """Nombre de présents par code structure (pour affichage dans l'éditeur de mise en page)."""
        from sme_gend.padmin_present import padmin_counts_as_present
        counts: dict[str, int] = {}
        try:
            for r in self._liste_generale.fetch_all():
                if padmin_counts_as_present(r.position_admin):
                    code = (r.structure or "").strip()
                    if code:
                        counts[code] = counts.get(code, 0) + 1
        except Exception:
            pass
        return counts

    def count_subarme_occurrences(
        self, subarme: str, structure_code: str | None = None
    ) -> int:
        """Compte les lignes ListeGenerale avec ce SubArme exact (optionnellement par structure)."""
        search = (subarme or "").strip()
        if not search:
            return 0
        count = 0
        try:
            for r in self._liste_generale.fetch_all():
                if (r.subdivision_arme or "").strip() == search:
                    if structure_code is None or (r.structure or "").strip() == structure_code:
                        count += 1
        except Exception:
            pass
        return count

    def rename_subarme_global(
        self,
        cfg: ListeLayoutConfig,
        old_label: str,
        new_label: str,
        structure_code: str | None = None,
    ) -> tuple[int, ListeLayoutConfig]:
        """Renomme SubArme dans toutes les structures (ou une seule) + met à jour la config JSON."""
        if structure_code is not None:
            return self.rename_subarme_in_database(cfg, structure_code, old_label, new_label)
        total = 0
        for sec in list(cfg.sections):
            n, cfg = self.rename_subarme_in_database(cfg, sec.structure_code, old_label, new_label)
            total += n
        return total, cfg

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
