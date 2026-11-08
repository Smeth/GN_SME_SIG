"""Chargement / enregistrement de la mise en page liste des présents (MySQL app_config)."""

from __future__ import annotations

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
        """Libellés SubArme pour une structure (ref_subdivision_arme)."""
        from sme_gend.repositories.structure_subdivision_repository import (
            StructureSubdivisionRepository,
        )

        return StructureSubdivisionRepository(self._settings).fetch_subdiv_labels_for_structure(
            structure_code
        )

    def path(self) -> Path:
        return liste_layout_config_path()

    def load(self, *, read_only: bool = False) -> ListeLayoutConfig:
        from sme_gend.services.layout_write_guard import load_layout_payload
        from sme_gend.repositories.reference_collection_guard import fingerprint
        from sme_gend.repositories.reference_write_guard import ReferenceConflictError
        from sme_gend.repositories.mysql_connection import has_import_transaction
        from sme_gend.services.shared_config_write import may_write_config

        raw=load_layout_payload(self._settings)
        if raw is not None:
            cfg=ListeLayoutConfig.from_dict(raw);cfg.write_token=fingerprint(raw)
            return cfg
        cfg=self.build_default_from_database();cfg.write_token=fingerprint(None)
        # A writer already owning a transaction will save its final layout once.
        if not read_only and may_write_config('liste_present_layout') and not has_import_transaction(self._settings):
            try:self.save(cfg)
            except ReferenceConflictError:
                raw=load_layout_payload(self._settings)
                if raw is None:raise
                cfg=ListeLayoutConfig.from_dict(raw);cfg.write_token=fingerprint(raw)
        return cfg

    def save(self, cfg: ListeLayoutConfig) -> None:
        from sme_gend.services.layout_write_guard import save_layout
        save_layout(self._settings,cfg)

    def build_default_from_database(self) -> ListeLayoutConfig:
        """Une section par ref_structure (ordre_tri) avec subdivisions RSA."""
        from sme_gend.repositories.mysql_bundle_repositories import (
            MySQLStructureSubdivisionRepository,
        )

        rows = self._structures.fetch_all()
        rsa_repo = MySQLStructureSubdivisionRepository(self._settings)
        sections: list[SectionBlock] = []
        for i, st in enumerate(rows):
            labels, rsa_ids = rsa_repo.fetch_rsa_order_for_structure(st.code)
            sections.append(
                SectionBlock(
                    id=f"auto-{st.code}-{i}",
                    order=i,
                    structure_code=st.code,
                    title_main_override=None,
                    title_sub_override=None,
                    group_by_subdivision=True,
                    subdivision_order=labels or None,
                    subdivision_rsa_order=rsa_ids or None,
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
            write_token=cfg.write_token,
            _pending_write=cfg._pending_write,
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
        Réconcilie le layout avec ref_structure + subdivisions base.
        Retourne (cfg mis à jour, nouvelles structures, nouveaux libellés SubArme).
        """
        cfg, report = self.reconcile_with_database(cfg, append_missing=True)
        return cfg, report.added_structure_codes, report.subdivisions_appended

    def structure_row(self, code: str) -> StructureRow | None:
        """Référentiel TabStructure pour un code, ou None."""
        return self._structures.fetch_by_code(code)

    def all_structure_designations(self) -> dict[str, str]:
        """Code → désignation (ou code si absent) pour tous les enregistrements TabStructure."""
        return {s.code: (s.designation or s.code) for s in self._structures.fetch_all()}

    def present_counts_by_structure(self) -> dict[str, int]:
        """Nombre de présents par code structure (pour affichage dans l'éditeur de mise en page)."""
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        counts: dict[str, int] = {}
        for r in self._liste_generale.fetch_for_presence_mode(
                'present', mois_ym=resolve_export_mois(self._settings)):
            code = (r.structure or "").strip()
            if code:
                counts[code] = counts.get(code, 0) + 1
        return counts

    def count_subarme_occurrences(
        self, subarme: str, structure_code: str | None = None
    ) -> int:
        """Compte les lignes ListeGenerale avec ce SubArme exact (optionnellement par structure)."""
        search = (subarme or "").strip()
        if not search:
            return 0
        from sme_gend.ui.personnel_list_filters import structure_matches_filter

        count = 0
        for r in self._liste_generale.fetch_all():
            if (r.subdivision_arme or "").strip() == search:
                if structure_code is None or structure_matches_filter(
                    r.structure, structure_code
                ):
                    count += 1
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
        from copy import deepcopy
        from sme_gend.services.shared_reference_write import shared_reference_write
        total = 0
        candidate = deepcopy(cfg)
        with shared_reference_write(self._settings):
            for code in dict.fromkeys(sec.structure_code for sec in cfg.sections):
                n, candidate = self.rename_subarme_in_database(candidate, code, old_label, new_label)
                total += n
        return total, candidate

    def update_structure_referentiel(
        self, code: str, designation: str, ordre_tri: int | None,
        *, expected=None,
    ) -> None:
        """Met à jour désignation et ordre de tri dans TabStructure (code inchangé)."""
        self._structures.update(code, designation, ordre_tri, famille_unite=expected.famille_unite if expected else None, expected=expected)

    def rename_structure_code(
        self, cfg: ListeLayoutConfig, old_code: str, new_code: str,
        *, expected=None,
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
        self._structures.rename_code(old_c, new_c, expected=expected)
        self._liste_generale.update_structure_column(old_c, new_c)
        self._liste_generale.backfill_structure_id_for_code(new_c)
        for sec in cfg.sections:
            if sec.structure_code.strip() == old_c:
                sec.structure_code = new_c
        return cfg

    def edit_structure_referentiel(
        self, cfg: ListeLayoutConfig, old_code: str, new_code: str,
        designation: str, ordre_tri: int | None, *, expected=None,
    ) -> ListeLayoutConfig:
        from copy import deepcopy
        from sme_gend.services.shared_reference_write import shared_reference_write

        edited=deepcopy(cfg)
        snapshot=expected
        with shared_reference_write(self._settings):
            if new_code!=old_code:
                edited=self.rename_structure_code(edited,old_code,new_code,expected=snapshot)
                # The renamed row is still locked by this transaction. This is
                # our own change, not an automatic rebase onto another writer.
                snapshot=self.structure_row(new_code)
            self.update_structure_referentiel(new_code,designation,ordre_tri,expected=snapshot)
        return edited

    def rename_subarme_in_database(
        self, cfg: ListeLayoutConfig, structure_code: str, old_label: str, new_label: str
    ) -> tuple[int, ListeLayoutConfig]:
        """
        Met à jour SubArme dans ListeGenerale et remplace le libellé dans subdivision_order
        de la section concernée (si présent).
        """
        return self.merge_subarmes_in_database(cfg, structure_code, [old_label], new_label)

    def subdivision_write_preview(self, structure_code, sources):
        """Exact current-context count; do not silently hide database errors."""
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        labels = {str(label or '').strip() for label in sources}
        month = resolve_export_mois(self._settings)
        if not month:
            raise ValueError('Choisissez un mois SME avant de modifier une subdivision.')
        rows = self._liste_generale.fetch_all_at_mois(month)
        count = sum((row.structure or '').strip() == structure_code.strip()
                    and ((row.subdivision_arme or '').strip() or '(Sans subdivision)') in labels
                    for row in rows)
        return month, count

    def merge_subarmes_in_database(self, cfg, structure_code, sources, target):
        """All renames, layout and activity entry commit together or roll back."""
        from copy import deepcopy
        from sme_gend.services.shared_reference_write import shared_reference_write
        from sme_gend.services.business_audit import record_business_action
        from sme_gend.services.liste_present_subdiv_tools import merge_subdiv_labels
        from sme_gend.services.auth_guard import session_has_permission, session_can_modify_personnel
        if not session_has_permission('effectifs.layout') or not session_can_modify_personnel():
            raise PermissionError('Le renommage en base exige les droits de mise en page et de modification du personnel.')
        candidate = deepcopy(cfg)
        sources = list(dict.fromkeys(str(label).strip() for label in sources))
        target = str(target or '').strip()
        if not target:
            raise ValueError('Le libellé cible est obligatoire.')
        total = 0
        with shared_reference_write(self._settings):
            from sme_gend.config.liste_generale_scope import resolve_export_mois
            month = resolve_export_mois(self._settings)
            if not month:
                raise ValueError('Choisissez un mois SME avant de modifier une subdivision.')
            from sme_gend.repositories.mysql_connection import MySQLConnection
            from sme_gend.services.sme_editing_policy import is_statut_ferme
            with MySQLConnection(self._settings).connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT statut FROM sme_periode WHERE annee_mois=%s FOR SHARE', (month,))
                    period = cursor.fetchone()
                    if period is None or is_statut_ferme(period[0] or ''):
                        raise PermissionError('Ce mois est absent ou clôturé. Réouvrez-le avant de modifier ses situations.')
            affected = [row for row in self._liste_generale.fetch_all_at_mois(month)
                        if (row.structure or '').strip() == structure_code.strip()
                        and ((row.subdivision_arme or '').strip() or '(Sans subdivision)') in sources]
            for source in sources:
                if source == target:
                    continue
                total += self._liste_generale.rename_subarme_for_structure(structure_code, source, target, mois_ym=month)
            for section in candidate.sections:
                if section.structure_code.strip() == structure_code.strip():
                    section.subdivision_order = merge_subdiv_labels(
                        list(section.subdivision_order or []), sources, target) or None
                    # Resolve the new label order instead of retaining outdated RSA IDs.
                    section.subdivision_rsa_order = None
            self.save(candidate)
            record_business_action(self._settings, action='subdivisions_merge' if len(sources) > 1 else 'subdivision_rename',
                module='liste_present', entity_id=structure_code,
                before={'subdivisions': sources}, after={'subdivision': target, 'situations_modifiees': total,
                    'personnel_ids': [str(row.id) for row in affected],
                    'persons': [row.noms or '' for row in affected]})
        return total, candidate

    def remove_structure_from_layout(
        self, cfg: ListeLayoutConfig, structure_code: str
    ) -> ListeLayoutConfig:
        sc = (structure_code or "").strip()
        cfg.sections = [s for s in cfg.sections if s.structure_code.strip() != sc]
        for i, sec in enumerate(sorted(cfg.sections, key=lambda x: x.order)):
            sec.order = i
        return cfg

    def remove_subdivision_from_layout(
        self, cfg: ListeLayoutConfig, structure_code: str, libelle: str
    ) -> ListeLayoutConfig:
        sc = (structure_code or "").strip()
        lb = (libelle or "").strip()
        for sec in cfg.sections:
            if sec.structure_code.strip() != sc:
                continue
            if sec.subdivision_order:
                sec.subdivision_order = [x for x in sec.subdivision_order if x != lb]
            if sec.subdivision_rsa_order:
                labels = self._rsa_labels(sec.subdivision_rsa_order)
                kept = [
                    rsa
                    for rsa, lbl in zip(sec.subdivision_rsa_order, labels)
                    if lbl != lb
                ]
                sec.subdivision_rsa_order = kept or None
        return cfg

    def database_structure_codes(self) -> set[str]:
        return {(s.code or "").strip() for s in self._structures.fetch_all() if (s.code or "").strip()}

    def reconcile_with_database(
        self, cfg: ListeLayoutConfig, *, append_missing: bool = True
    ) -> tuple[ListeLayoutConfig, "LayoutReconcileReport"]:
        """Aligne layout ↔ ref_structure + subdivisions RSA / carrière."""
        from sme_gend.repositories.mysql_bundle_repositories import (
            MySQLStructureSubdivisionRepository,
        )
        from sme_gend.services.liste_present_layout_coherence import (
            LayoutReconcileReport,
            reconcile_layout_with_database,
        )

        rsa_repo = MySQLStructureSubdivisionRepository(self._settings)
        structures = self._structures.fetch_all()

        def _subs(code: str) -> tuple[list[str], list[int]]:
            db_rsa = rsa_repo.fetch_rsa_order_for_structure(code)
            if db_rsa[0]:
                return db_rsa
            return self.subarme_labels_for_structure(code), []

        cfg_out, report = reconcile_layout_with_database(
            cfg,
            structures,
            db_subdivisions_for=_subs,
            append_missing_structures=append_missing,
            merge_subdivisions=True,
        )
        return cfg_out, report

    def effective_subdivisions_for_section(self, sec: SectionBlock) -> list[str]:
        """Ordre effectif : layout configuré fusionné avec libellés base."""
        from sme_gend.services.liste_present_layout_coherence import (
            merge_subdivisions_for_structure,
        )

        code = sec.structure_code.strip()
        db_labels = self.subarme_labels_for_structure(code)
        if not db_labels and sec.subdivision_rsa_order:
            db_labels = self._rsa_labels(sec.subdivision_rsa_order)
        template = sec.subdivision_order
        if not template and sec.subdivision_rsa_order:
            template = self._rsa_labels(sec.subdivision_rsa_order)
        return merge_subdivisions_for_structure(code, template, db_labels)

    def resolve_subdivision_order_labels(self, sec: SectionBlock) -> list[str] | None:
        """Ordre effectif des subdivisions (layout configuré + base, cohérent RG / RSA)."""
        labels = self.effective_subdivisions_for_section(sec)
        return labels or None

    def _rsa_labels(self, rsa_ids: list[int]) -> list[str]:
        if not rsa_ids:
            return []
        from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
        repo = MySQLAgentRepository(self._settings)
        with repo._connect() as conn:
            with conn.cursor() as cur:
                out: list[str] = []
                for rid in rsa_ids:
                    cur.execute(
                        "SELECT libelle FROM ref_subdivision_arme WHERE id = %s",
                        (int(rid),),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        out.append(str(row[0]).strip())
                return out
