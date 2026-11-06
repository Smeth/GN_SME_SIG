"""Export Excel des attributions médailles / décorations (effectif liste générale)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sme_gend.config.settings import Settings
from sme_gend.models.distinction_ref import DISTINCTION_KIND_MEDAILLE
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.agent_dossier_service import AgentDossierService
from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell
from sme_gend.services.grade_label_format import format_grade_display


@dataclass(slots=True)
class DistinctionExportLine:
    legacy_id: str
    noms: str
    grade: str
    structure: str
    kind: str
    code: str
    libelle: str
    date_attribution: date | None
    date_publication_journal: date | None
    numero_arrete: str | None
    date_arrete: date | None
    citation: str | None
    source: str | None


def collect_distinction_export_lines(
    settings: Settings,
    *,
    structure_code: str | None = None,
) -> list[DistinctionExportLine]:
    from sme_gend.repositories.mysql_distinction_repository import MySQLDistinctionRepository
    from sme_gend.services.referentiel_service import ReferentielService

    if not MySQLDistinctionRepository(settings).table_ready():
        return []
    liste = ListeGeneraleRepository(settings).fetch_all()
    dossier = AgentDossierService(settings)
    ref = ReferentielService(settings)
    med_by_id = {m.id: m for m in ref.medailles()}
    dec_by_id = {d.id: d for d in ref.decorations()}
    struct_filter = (structure_code or "").strip().upper()
    lines: list[DistinctionExportLine] = []
    for row in liste:
        if struct_filter and (row.structure or "").strip().upper() != struct_filter:
            continue
        leg = str(row.satellite_join_key() or row.id or "").strip()
        try:
            dists = dossier.list_distinctions(row)
        except Exception:
            continue
        for d in dists:
            code = ""
            if d.kind == DISTINCTION_KIND_MEDAILLE and d.ref_medaille_id:
                ref_row = med_by_id.get(d.ref_medaille_id)
                code = ref_row.code if ref_row else ""
            elif d.ref_decoration_id:
                ref_row = dec_by_id.get(d.ref_decoration_id)
                code = ref_row.code if ref_row else ""
            lines.append(
                DistinctionExportLine(
                    legacy_id=leg,
                    noms=row.noms or "",
                    grade=row.grade or "",
                    structure=row.structure or "",
                    kind=d.kind,
                    code=code,
                    libelle=d.libelle or "",
                    date_attribution=d.date_attribution,
                    date_publication_journal=d.date_publication_journal,
                    numero_arrete=d.numero_arrete,
                    date_arrete=d.date_arrete,
                    citation=d.citation,
                    source=d.source,
                )
            )
    lines.sort(key=lambda x: (x.structure, x.noms, x.date_attribution or date.min))
    return lines


def _fmt_d(d: date | None) -> str:
    if d is None:
        return ""
    return d.strftime("%d/%m/%Y")


def _autosize(ws, headers: tuple[str, ...], header_row: int, last_row: int) -> None:
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{last_row}"
    ws.freeze_panes = f"A{header_row + 1}"
    for col in range(1, len(headers) + 1):
        width = len(headers[col - 1])
        for i in range(header_row, last_row + 1):
            v = ws.cell(row=i, column=col).value
            if v is not None:
                width = max(width, len(str(v)))
        ws.column_dimensions[get_column_letter(col)].width = min(52, width + 2)


def export_distinctions_workbook(
    dest: Path,
    *,
    lines: list[DistinctionExportLine],
    grades=None,
    settings=None,
    title: str = "Attributions médailles et décorations",
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Attributions"
    write_literal_cell_value(ws, 1, 1, title)
    writable_cell(ws, 1, 1).font = Font(bold=True, size=12)

    headers = (
        "Id liste",
        "Nom",
        "Grade",
        "Structure",
        "Type",
        "Code réf.",
        "Libellé distinction",
        "Date attribution",
        "Publication JO",
        "N° arrêté",
        "Date arrêté",
        "Citation",
        "Origine",
    )
    header_row = 3
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, header_row, col, h)
        writable_cell(ws, header_row, col).font = Font(bold=True)

    for i, ln in enumerate(lines, start=header_row + 1):
        kind = "Médaille" if ln.kind == DISTINCTION_KIND_MEDAILLE else "Décoration / ordre"
        vals = (
            ln.legacy_id,
            ln.noms,
            format_grade_display(ln.grade, grades),
            ln.structure,
            kind,
            ln.code,
            ln.libelle,
            _fmt_d(ln.date_attribution),
            _fmt_d(ln.date_publication_journal),
            ln.numero_arrete or "",
            _fmt_d(ln.date_arrete),
            (ln.citation or "")[:500],
            ln.source or "",
        )
        for col, v in enumerate(vals, start=1):
            write_literal_cell_value(ws, i, col, v)

    last = max(header_row, header_row + len(lines))
    _autosize(ws, headers, header_row, last)
    wb.save(dest)
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, lines, doc_type="distinctions", settings=settings)


def write_distinction_import_template(dest: Path, *, settings=None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Attributions"
    headers = (
        "id_liste",
        "matricule",
        "noms",
        "type",
        "code_distinction",
        "date_attribution",
        "date_publication_jo",
        "numero_arrete",
        "date_arrete",
        "citation",
    )
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 1, col, h)
        writable_cell(ws, 1, col).font = Font(bold=True)
    write_literal_cell_value(ws, 2, 1, "AGENT-001")
    write_literal_cell_value(ws, 2, 4, "medaille")
    write_literal_cell_value(ws, 2, 5, "MM")
    write_literal_cell_value(ws, 2, 6, "15/01/2020")

    ws2 = wb.create_sheet("Medailles_ref")
    ref_h = ("code", "libelle_court", "libelle_long", "categorie", "ordre_preseance", "actif")
    for col, h in enumerate(ref_h, start=1):
        write_literal_cell_value(ws2, 1, col, h)
    write_literal_cell_value(ws2, 2, 1, "EXEMPLE")
    write_literal_cell_value(ws2, 2, 2, "Médaille exemple")

    ws3 = wb.create_sheet("Decorations_ref")
    for col, h in enumerate(ref_h, start=1):
        write_literal_cell_value(ws3, 1, col, h)
    write_literal_cell_value(ws3, 2, 1, "EXEMPLE_DEC")
    write_literal_cell_value(ws3, 2, 2, "Décoration exemple")

    wb.save(dest)
    from sme_gend.services.doc_gen_log import log_saved_list
    log_saved_list(dest, [], doc_type="distinction_template", settings=settings)
