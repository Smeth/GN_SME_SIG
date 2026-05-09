"""Export Excel de la liste générale — liste filtrée et par position administrative."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

# Colonnes exportables (key, en-tête, largeur colonne).
# Les colonnes supplémentaires sont incluses mais filtrées par hidden_keys dans les exports.
EXPORT_COLS: list[tuple[str, str, int]] = [
    ("id",                  "N°",               8),
    ("noms",                "Nom et prénom",    32),
    ("grade",               "Grade",            12),
    ("fonction",            "Fonction",         22),
    ("subdivision_arme",    "Subdivision",      28),
    ("position_admin",      "Position adm.",    16),
    ("structure",           "Structure",        12),
    ("matricule_militaire", "Matricule mil.",   15),
    ("matricule_solde",     "Matricule solde",  15),
    ("date_naissance",      "Date naissance",   14),
    ("lieu_naissance",      "Lieu naissance",   18),
    ("date_grade",          "Date de grade",    14),
    ("date_service",        "Date de service",  14),
    ("ndecision",           "N° décision",      14),
    ("diplome",             "Diplôme",          20),
    ("date_obtention",      "Date diplôme",     14),
    ("obs",                 "Observations",     30),
    ("presseance",          "Préséance",        10),
    ("ordre_tri",           "Ordre tri",        10),
]

_HDR_FILL  = PatternFill("solid", fgColor="1A237E")
_HDR_FONT  = Font(color="FFFFFF", bold=True, name="Calibri", size=10)
_HDR_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_THIN      = Side(style="thin", color="AAAAAA")
_BORDER    = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_EVEN_FILL = PatternFill("solid", fgColor="EEF2FF")
_ODD_FILL  = PatternFill("solid", fgColor="FFFFFF")
_CELL_FONT = Font(name="Calibri", size=10)
_CELL_ALIGN= Alignment(vertical="center", wrap_text=False)


def _resolve(row: ListeGeneraleRow, key: str, flat: Sequence[SubdivisionFlatRow] | None) -> str:
    if key == "subdivision_arme" and flat is not None:
        return format_subdivision_arme_display(row.subdivision_arme, flat) or ""
    val = getattr(row, key, None)
    return "" if val is None else str(val).strip()


def _safe_sheet_name(name: str) -> str:
    for ch in r"\/?*[]:":
        name = name.replace(ch, "-")
    name = name.strip()
    return name[:31] if name else "Feuille"


def _write_header(ws, cols: list[tuple[str, str, int]]) -> None:
    ws.row_dimensions[1].height = 30
    for ci, (_, header, width) in enumerate(cols, 1):
        c = ws.cell(1, ci, header)
        c.font   = _HDR_FONT
        c.fill   = _HDR_FILL
        c.alignment = _HDR_ALIGN
        c.border = _BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width


def _write_data_rows(
    ws,
    rows: list[ListeGeneraleRow],
    cols: list[tuple[str, str, int]],
    flat: Sequence[SubdivisionFlatRow] | None,
    start_row: int = 2,
) -> None:
    for ri, row in enumerate(rows, start_row):
        fill = _EVEN_FILL if ri % 2 == 0 else _ODD_FILL
        ws.row_dimensions[ri].height = 15
        for ci, (key, _, _) in enumerate(cols, 1):
            c = ws.cell(ri, ci, _resolve(row, key, flat))
            c.font      = _CELL_FONT
            c.fill      = fill
            c.alignment = _CELL_ALIGN
            c.border    = _BORDER


def export_liste_filtered(
    rows: list[ListeGeneraleRow],
    path: Path,
    *,
    hidden_keys: set[str] | None = None,
    flat: Sequence[SubdivisionFlatRow] | None = None,
    sheet_title: str = "Liste générale",
) -> int:
    """Exporte la liste filtrée dans un seul onglet. Retourne le nombre de lignes."""
    hidden = hidden_keys or set()
    cols = [(k, h, w) for k, h, w in EXPORT_COLS if k not in hidden]
    wb = Workbook()
    ws = wb.active
    ws.title = _safe_sheet_name(sheet_title)
    ws.freeze_panes = "A2"
    _write_header(ws, cols)
    _write_data_rows(ws, rows, cols, flat)
    wb.save(str(path))
    return len(rows)


def export_liste_by_padmin(
    rows: list[ListeGeneraleRow],
    path: Path,
    *,
    hidden_keys: set[str] | None = None,
    flat: Sequence[SubdivisionFlatRow] | None = None,
) -> dict[str, int]:
    """Une feuille par position administrative. Retourne {padmin: nb_lignes}."""
    hidden = hidden_keys or set()
    cols = [(k, h, w) for k, h, w in EXPORT_COLS if k not in hidden]

    groups: dict[str, list[ListeGeneraleRow]] = {}
    for r in rows:
        key = (r.position_admin or "").strip() or "(Non renseigné)"
        groups.setdefault(key, []).append(r)

    wb = Workbook()
    default = wb.active
    counts: dict[str, int] = {}
    first = True

    for padmin in sorted(groups.keys(), key=str.lower):
        padmin_rows = groups[padmin]
        if first:
            ws = default
            ws.title = _safe_sheet_name(padmin)
            first = False
        else:
            ws = wb.create_sheet(_safe_sheet_name(padmin))
        ws.freeze_panes = "A2"
        _write_header(ws, cols)
        _write_data_rows(ws, padmin_rows, cols, flat)
        counts[padmin] = len(padmin_rows)

    if not groups:
        default.title = "Vide"

    # Onglet récapitulatif
    ws_recap = wb.create_sheet("Récapitulatif")
    ws_recap.cell(1, 1, "Position administrative").font = Font(bold=True, name="Calibri", size=10)
    ws_recap.cell(1, 2, "Effectif").font = Font(bold=True, name="Calibri", size=10)
    ws_recap.column_dimensions["A"].width = 28
    ws_recap.column_dimensions["B"].width = 12
    total = 0
    for ri, (padmin, n) in enumerate(
        sorted(counts.items(), key=lambda x: -x[1]), 2
    ):
        ws_recap.cell(ri, 1, padmin).font = Font(name="Calibri", size=10)
        ws_recap.cell(ri, 2, n).font = Font(name="Calibri", size=10)
        total += n
    last = len(counts) + 2
    ws_recap.cell(last, 1, "TOTAL").font = Font(bold=True, name="Calibri", size=10)
    ws_recap.cell(last, 2, total).font = Font(bold=True, name="Calibri", size=10)

    wb.save(str(path))
    return counts
