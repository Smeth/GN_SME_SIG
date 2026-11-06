"""Cohérence mise en page liste des présents ↔ référentiel base (ref_structure / RSA)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services.liste_present_rg_template import normalize, template_sub_matches_mdb


@dataclass
class LayoutReconcileReport:
    """Résultat d'une réconciliation layout / base."""

    removed_structure_codes: list[str] = field(default_factory=list)
    added_structure_codes: list[str] = field(default_factory=list)
    subdivisions_appended: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.removed_structure_codes
            or self.added_structure_codes
            or self.subdivisions_appended
        )


def merged_rg_subdivisions(
    structure_code: str,
    template_subs: list[str],
    mdb_subs: list[str],
) -> list[str]:
    """Ordre template + subdivisions MDB non couvertes en fin de bloc (§ 3.3)."""
    code = (structure_code or "").strip()
    result = list(template_subs)
    covered: set[str] = set()

    for ts in template_subs:
        for ms in mdb_subs:
            if template_sub_matches_mdb(code, ts, ms):
                covered.add(ms)

    seen_norm = {normalize(x) for x in result if normalize(x)}
    for ms in mdb_subs:
        ms = (ms or "").strip()
        if not ms or ms in covered:
            continue
        n = normalize(ms)
        if n not in seen_norm:
            result.append(ms)
            seen_norm.add(n)
    return result


def merge_subdivisions_for_structure(
    structure_code: str,
    template_subs: list[str] | None,
    db_subs: list[str],
) -> list[str]:
    """
    Fusionne l'ordre configuré (template / layout) avec les libellés en base.

    - Régions RG : matching flou ; nouvelles subdivisions MDB en fin de bloc.
    - Autres structures : ordre layout conservé ; libellés base absents du layout ajoutés en fin.
    """
    code = (structure_code or "").strip()
    template = [(x or "").strip() for x in (template_subs or []) if (x or "").strip()]
    db = [(x or "").strip() for x in (db_subs or []) if (x or "").strip()]

    if not template:
        return db
    if not db:
        return template
    if code.startswith("RG-"):
        return merged_rg_subdivisions(code, template, db)

    db_norm_to_label = {normalize(d): d for d in db}
    out: list[str] = []
    seen_norm: set[str] = set()

    for lbl in template:
        n = normalize(lbl)
        if n in seen_norm:
            continue
        out.append(lbl)
        seen_norm.add(n)
        if n in db_norm_to_label:
            seen_norm.add(n)

    for d in db:
        n = normalize(d)
        if n not in seen_norm:
            out.append(d)
            seen_norm.add(n)
    return out


def filter_layout_to_database(
    cfg: ListeLayoutConfig,
    db_structures: list[StructureRow],
) -> tuple[ListeLayoutConfig, list[str]]:
    """Retire les sections dont le code n'existe pas dans ref_structure."""
    db_codes = {(s.code or "").strip() for s in db_structures}
    valid: list[SectionBlock] = []
    removed: list[str] = []
    for sec in sorted(cfg.sections, key=lambda s: s.order):
        code = sec.structure_code.strip()
        if code in db_codes:
            valid.append(sec)
        else:
            removed.append(code)
    for i, sec in enumerate(valid):
        sec.order = i
    return (
        ListeLayoutConfig(
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
            sections=valid,
        ),
        removed,
    )


def reconcile_layout_with_database(
    cfg: ListeLayoutConfig,
    db_structures: list[StructureRow],
    *,
    db_subdivisions_for: Callable[[str], tuple[list[str], list[int]]],
    append_missing_structures: bool = True,
    merge_subdivisions: bool = True,
) -> tuple[ListeLayoutConfig, LayoutReconcileReport]:
    """
    Aligne le layout sur ref_structure :

    - supprime les codes structure absents de la base ;
    - ajoute en fin les structures base absentes du layout (ordre ref_structure) ;
    - complète les subdivisions depuis la base (sans écraser l'ordre configuré).
    """
    report = LayoutReconcileReport()
    cfg, removed = filter_layout_to_database(cfg, db_structures)
    report.removed_structure_codes = removed

    db_by_code = {(s.code or "").strip(): s for s in db_structures}
    known = {sec.structure_code.strip() for sec in cfg.sections}

    if append_missing_structures:
        order_idx = len(cfg.sections)
        for st in db_structures:
            code = (st.code or "").strip()
            if not code or code in known:
                continue
            labels, rsa_ids = db_subdivisions_for(code)
            cfg.sections.append(
                SectionBlock(
                    id=str(uuid4()),
                    order=order_idx,
                    structure_code=code,
                    group_by_subdivision=True,
                    subdivision_order=labels or None,
                    subdivision_rsa_order=rsa_ids or None,
                )
            )
            report.added_structure_codes.append(code)
            known.add(code)
            order_idx += 1

    if merge_subdivisions:
        for sec in cfg.sections:
            code = sec.structure_code.strip()
            db_labels, rsa_ids = db_subdivisions_for(code)
            if not db_labels:
                continue
            merged = merge_subdivisions_for_structure(
                code,
                sec.subdivision_order,
                db_labels,
            )
            before = set((sec.subdivision_order or []))
            if merged != list(sec.subdivision_order or []):
                added = [x for x in merged if x not in before]
                for item in added:
                    report.subdivisions_appended.append(f"[{code}] {item}")
                sec.subdivision_order = merged
                if rsa_ids and not code.startswith("RG-"):
                    sec.subdivision_rsa_order = rsa_ids

    return cfg, report
