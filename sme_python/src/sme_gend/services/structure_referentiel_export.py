"""Export Excel du référentiel structures (typologie, subdivisions, fonctions, effectifs)."""

from __future__ import annotations

from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell

from collections.abc import Mapping
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sme_gend.config.structure_famille import structure_famille_label, resolved_structure_famille
from sme_gend.models.structure import StructureRow


def _literal_cell(ws, row, col, value):
    write_literal_cell_value(ws, row, col, value)
    return writable_cell(ws, row, col)


def export_structures_referentiel(
    path: Path,
    structures: list[StructureRow],
    *,
    subdiv_counts: Mapping[str, int] | None = None,
    fonction_counts: Mapping[str, int] | None = None,
    effectif_counts: Mapping[str, int] | None = None,
    mois_label: str | None = None,
    settings=None,
) -> int:
    """Feuille unique : une ligne par structure."""
    subdiv_counts = subdiv_counts or {}
    fonction_counts = fonction_counts or {}
    effectif_counts = effectif_counts or {}

    wb = Workbook()
    ws = wb.active
    ws.title = "Structures"

    title = "Référentiel des structures"
    if (mois_label or "").strip():
        title = f"{title} — période {mois_label.strip()}"
    ws.merge_cells("A1:H1")
    ws["A1"] = title
    ws["A1"].font = Font(name="Times New Roman", size=12, bold=True)

    headers = (
        "Code",
        "Désignation",
        "Type d'unité",
        "Ordre tri",
        "Subdivisions RSA",
        "Fonctions rattachées",
        "Effectif (liste générale)",
    )
    hdr_row = 3
    for ci, h in enumerate(headers, 1):
        c = ws.cell(hdr_row, ci, h)
        c.font = Font(name="Times New Roman", size=9, bold=True)

    widths = (14, 36, 22, 10, 16, 18, 22)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    data_start = hdr_row + 1
    for ri, st in enumerate(structures, data_start):
        code = st.code
        _literal_cell(ws, ri, 1, code).font = Font(name="Times New Roman", size=9)
        _literal_cell(ws, ri, 2, st.designation or "").font = Font(name="Times New Roman", size=9)
        _literal_cell(ws, ri, 3, structure_famille_label(resolved_structure_famille(st.code,st.designation,st.famille_unite))).font = Font(
            name="Times New Roman", size=9
        )
        _literal_cell(ws, ri, 4, st.ordre_tri if st.ordre_tri is not None else "").font = Font(
            name="Times New Roman", size=9
        )
        _literal_cell(ws, ri, 5, subdiv_counts.get(code, 0)).font = Font(name="Times New Roman", size=9)
        _literal_cell(ws, ri, 6, fonction_counts.get(code, 0)).font = Font(name="Times New Roman", size=9)
        _literal_cell(ws, ri, 7, effectif_counts.get(code, 0)).font = Font(name="Times New Roman", size=9)

    last = max(data_start, data_start + len(structures) - 1)
    ws.auto_filter.ref = f"A{hdr_row}:G{last}"
    ws.freeze_panes = f"A{data_start}"

    wb.save(str(path))
    from sme_gend.services.doc_gen_log import log_doc_generation

    log_doc_generation(
        doc_type="referentiel_structures", settings=settings,
        format="xlsx",
        count=len(structures),
        dest_path=str(path),
        mois=mois_label,
    )
    return len(structures)
