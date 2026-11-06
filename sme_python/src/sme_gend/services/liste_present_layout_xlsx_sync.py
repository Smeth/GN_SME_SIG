"""Synchronise l'ordre des rubriques layout depuis une maquette Excel liste des présents."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.repositories.structure_repository import StructureRepository


def _is_data_row(val) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return True
    s = str(val).strip()
    return bool(s.isdigit())


def _is_sec1_row(height: float | None) -> bool:
    return bool(height and height >= 20.0)


def parse_presents_xlsx_sections(
    path: Path,
    *,
    sheet: str = "Liste",
) -> list[tuple[str, list[str]]]:
    """
    Extrait (titre sec1, rubriques sec2 dans l'ordre) depuis la maquette.

    Chaque sec2 est une rubrique telle qu'affichée dans le document (subdivision ou
    libellé structure lorsque l'effectif est rattaché directement).
    """
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.active

    sections: list[tuple[str, list[str]]] = []
    cur_title: str | None = None
    cur_sec2: list[str] = []

    for row_idx in range(4, ws.max_row + 1):
        val = ws.cell(row_idx, 1).value
        if val is None or str(val).strip() == "":
            continue
        if _is_data_row(val):
            continue
        text = str(val).strip()
        height = ws.row_dimensions[row_idx].height
        if _is_sec1_row(height):
            if cur_title is not None:
                sections.append((cur_title, cur_sec2))
            cur_title = text
            cur_sec2 = []
        elif cur_title is not None:
            cur_sec2.append(text)

    if cur_title is not None:
        sections.append((cur_title, cur_sec2))

    wb.close()
    return sections


def _norm(s: str) -> str:
    return " ".join((s or "").strip().casefold().split())


def _match_structure_code(
    sec1_title: str,
    by_designation: dict[str, str],
    by_code: dict[str, str],
) -> str | None:
    key = _norm(sec1_title)
    if key in by_designation:
        return by_designation[key]
    # Libellés tronqués dans Excel (ex. « … DE »)
    for desig_norm, code in by_designation.items():
        if desig_norm.startswith(key[:40]) or key.startswith(desig_norm[:40]):
            return code
    if key in by_code:
        return by_code[key]
    return None


def _rsa_index_for_structure(settings: Settings, structure_code: str) -> dict[str, int]:
    from sme_gend.repositories.mysql_bundle_repositories import (
        MySQLStructureSubdivisionRepository,
    )

    repo = MySQLStructureSubdivisionRepository(settings)
    labels, rsa_ids = repo.fetch_rsa_order_for_structure(structure_code)
    out: dict[str, int] = {}
    for lbl, rid in zip(labels, rsa_ids):
        n = _norm(lbl)
        if n and n not in out:
            out[n] = int(rid)
    return out


def _filter_structure_rubric_labels(
    sec2_labels: list[str],
    *,
    structure_code: str,
    designation: str | None,
    title_sub: str | None,
) -> list[str]:
    """
    Retire le libellé « structure » (title_sub / désignation courte) lorsqu'il n'est
    pas une subdivision RSA — ex. « Commandement Gendarmerie » sous COMGEND.
    """
    skip = {
        _norm(structure_code),
        _norm(designation or ""),
        _norm(title_sub or ""),
    }
    skip.discard("")
    out: list[str] = []
    for lbl in sec2_labels:
        if _norm(lbl) in skip:
            continue
        out.append(lbl)
    return out if out else list(sec2_labels)


def _resolve_rsa_order(
    labels: list[str],
    rsa_by_label: dict[str, int],
) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for lbl in labels:
        rid = rsa_by_label.get(_norm(lbl))
        if rid is not None and rid not in seen:
            ids.append(rid)
            seen.add(rid)
    return ids


def apply_xlsx_subdiv_order_to_layout(
    cfg: ListeLayoutConfig,
    settings: Settings,
    xlsx_path: Path,
    *,
    reorder_sections: bool = True,
) -> tuple[ListeLayoutConfig, list[str]]:
    """
    Met à jour ``subdivision_order`` / ``subdivision_rsa_order`` depuis la maquette.

    Retourne (config, messages d'avertissement).
    """
    parsed = parse_presents_xlsx_sections(xlsx_path)
    structures = StructureRepository(settings).fetch_all()
    by_desig = {_norm(s.designation or s.code): s.code for s in structures}
    by_code = {_norm(s.code): s.code for s in structures}
    sec_by_code = {s.structure_code.strip(): s for s in cfg.sections}

    warnings: list[str] = []
    updates: dict[str, tuple[list[str], list[int]]] = {}

    for sec1, sec2_raw in parsed:
        code = _match_structure_code(sec1, by_desig, by_code)
        if not code:
            warnings.append(f"Structure non reconnue pour sec1 : {sec1[:70]}")
            continue
        sec = sec_by_code.get(code)
        st = next((s for s in structures if s.code == code), None)
        title_sub = (sec.title_sub_override if sec else None) or (st.code if st else code)
        designation = st.designation if st else None
        labels = _filter_structure_rubric_labels(
            sec2_raw,
            structure_code=code,
            designation=designation,
            title_sub=title_sub,
        )
        rsa_map = _rsa_index_for_structure(settings, code)
        rsa_ids = _resolve_rsa_order(labels, rsa_map)
        unmatched = [lb for lb in labels if _norm(lb) not in rsa_map]
        if unmatched:
            warnings.append(
                f"[{code}] rubriques Excel sans RSA : {', '.join(unmatched[:5])}"
                + (f" (+{len(unmatched)-5})" if len(unmatched) > 5 else "")
            )
        updates[code] = (labels, rsa_ids)

    new_sections: list[SectionBlock] = []
    if reorder_sections:
        order_idx = 0
        used: set[str] = set()
        for sec1, _ in parsed:
            code = _match_structure_code(sec1, by_desig, by_code)
            if not code or code in used:
                continue
            used.add(code)
            sec = sec_by_code.get(code)
            if sec is None:
                continue
            labels, rsa_ids = updates.get(code, (sec.subdivision_order or [], sec.subdivision_rsa_order or []))
            new_sections.append(
                replace(
                    sec,
                    order=order_idx,
                    subdivision_order=labels or None,
                    subdivision_rsa_order=rsa_ids or None,
                )
            )
            order_idx += 1
        for sec in sorted(cfg.sections, key=lambda s: s.order):
            c = sec.structure_code.strip()
            if c in used:
                continue
            labels, rsa_ids = updates.get(c, (sec.subdivision_order, sec.subdivision_rsa_order))
            new_sections.append(
                replace(
                    sec,
                    order=order_idx,
                    subdivision_order=labels or sec.subdivision_order,
                    subdivision_rsa_order=rsa_ids or sec.subdivision_rsa_order,
                )
            )
            order_idx += 1
    else:
        for sec in cfg.sections:
            code = sec.structure_code.strip()
            if code not in updates:
                new_sections.append(sec)
                continue
            labels, rsa_ids = updates[code]
            new_sections.append(
                replace(
                    sec,
                    subdivision_order=labels or None,
                    subdivision_rsa_order=rsa_ids or None,
                )
            )

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
            sections=new_sections,
        ),
        warnings,
    )
