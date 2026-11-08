"""Export Excel SME DPF — gabarit ``SME_DPF_*.xlsx`` alimenté depuis MySQL."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

import re
import shutil
from pathlib import Path

from openpyxl import load_workbook

from sme_gend.config.liste_generale_scope import build_export_mois_context
from sme_gend.config.settings import Settings, project_root
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.sme_dpf_row_format import row_to_dpf_liste_cells
from sme_gend.services.sme_dpf_service import SmeDpfBundle, SmeDpfService
from sme_gend.services.excel_style_helpers import clear_cell_value, write_cell_value

SHEET_LISTE_GNLE = "LISTE GNLE"
SHEET_LISTE_PRESENTS = "III- LISTE DE PRESENTS"
SHEET_INDISPONIBLES = "IV- INDISPONIBLES"
SHEET_CONTROLE = "V- CONTROLE SPECIAL DPF"
SHEET_STAGIAIRES = "VI-STAGIAIRES"
SHEET_SITUATION = "I- SITUATION NUMERIQUE DES EFF"


def resolve_sme_dpf_template(settings: Settings) -> Path | None:
    tpl = getattr(settings, "template_sme_dpf_path", None)
    if tpl is not None and Path(tpl).is_file():
        return Path(tpl)
    root = project_root()
    for name in (
        "SME_DPF_MAI_2026.xlsx",
        "SME_DPF.xlsx",
        "templates/SME_DPF_MAI_2026.xlsx",
    ):
        p = root / name
        if p.is_file():
            return p
    return None


def _clear_rows(ws, start_row: int, *, max_col: int = 16) -> None:
    for r in range(start_row, ws.max_row + 1):
        if ws.cell(r, 1).value is None and r > start_row + 2:
            break
        for c in range(1, max_col + 1):
            clear_cell_value(ws, r, c)


def _write_liste_block(ws, start_row: int, rows: list[ListeGeneraleRow], grades=None) -> int:
    row_idx = start_row
    for i, row in enumerate(rows, start=1):
        cells = row_to_dpf_liste_cells(row, i, grades=grades)
        for col, val in enumerate(cells, start=1):
            write_cell_value(ws, row_idx, col, val)
        row_idx += 1
    return row_idx


def _month_label_fr(mois: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})$", (mois or "").strip())
    if not m:
        return mois or ""
    names = (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    )
    y, mm = int(m.group(1)), int(m.group(2))
    if 1 <= mm <= 12:
        return f"{names[mm - 1]} {y}"
    return mois


@tracked_export
def export_sme_dpf_workbook(
    settings: Settings,
    destination: Path,
    *,
    bundle: SmeDpfBundle | None = None,
) -> Path:
    """Génère les tables DPF et contrôle spécial pour le mois consulté."""
    from sme_gend.services.clean_sme_exports import build_dpf
    from sme_gend.services.export_workbook_tables import export_details
    from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
    from sme_gend.config.liste_generale_scope import settings_for_chosen_export_mois

    ctx = build_export_mois_context(settings)
    mois = ctx.export_mois
    if not mois:
        raise ValueError("Choisissez le mois SME avant l'export DPF.")
    scoped = settings_for_chosen_export_mois(settings, mois)
    data = bundle or SmeDpfService(scoped).load_bundle(mois=mois)
    if data.mois != mois:
        raise ValueError("Les données DPF ne correspondent pas au mois choisi ; actualisez la vue.")
    grades = GradeRepository(scoped).fetch_all()
    details = export_details(NumeriqueSatelliteRepository(scoped))
    dest = Path(destination).resolve()
    export_phase('render', detail='Feuilles numériques et nominatives DPF')
    build_dpf(scoped, dest, data, grades, details)
    export_phase('finalize', detail='Historique du document DPF')
    from sme_gend.services.doc_gen_log import log_doc_generation
    log_doc_generation(settings=scoped, doc_type="sme_dpf", format="xlsx", persons=[f"DPF ({data.total} agents)"],
                        dest_path=str(dest), count=data.total, mois=mois)
    return dest


def default_export_filename(settings: Settings) -> str:
    ctx = build_export_mois_context(settings)
    mois = (ctx.export_mois or "mois").replace("-", "_")
    return f"SME_DPF_{mois}.xlsx"
