#!/usr/bin/env python3
"""Export Excel commission — identification du sexe des agents restants."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

DEFAULT_CSV = _ROOT / "data" / "sexe_sync_reports" / "agents_sans_sexe.csv"
DEFAULT_XLSX = _ROOT / "data" / "sexe_sync_reports" / "COMMISSION_agents_sans_sexe.xlsx"

# En-têtes visibles pour la commission
COLUMNS = (
    ("no", "N°", 5),
    ("noms", "Nom(s) et prénom(s)", 38),
    ("grade", "Grade", 22),
    ("mat_actuel", "Matricule militaire (actuel)", 20),
    ("ref_sexe", "Suggestion sexe (réf.)", 14),
    ("ref_matricule", "Matricule référence", 18),
    ("ref_noms", "Nom dans fichier référence", 34),
    ("match_score", "Score rapprochement", 12),
    ("sexe_correct", "SEXE À RENSEIGNER (M ou F)", 22),
    ("note", "Observations commission", 28),
    ("legacy_id", "Réf. système (ne pas modifier)", 14),
)

THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL_HEADER = PatternFill("solid", fgColor="1F4E79")
FILL_TITLE = PatternFill("solid", fgColor="D6E4F0")
FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")
FILL_ALT = PatternFill("solid", fgColor="F7F9FC")
FILL_WHITE = PatternFill("solid", fgColor="FFFFFF")
FILL_REF = PatternFill("solid", fgColor="E2EFDA")

FONT_TITLE = Font(name="Calibri", size=14, bold=True, color="1F4E79")
FONT_SUB = Font(name="Calibri", size=10, italic=True, color="404040")
FONT_HDR = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
FONT_BODY = Font(name="Calibri", size=10)
FONT_INPUT = Font(name="Calibri", size=11, bold=True)
FONT_TECH = Font(name="Calibri", size=9, color="808080")


def _load_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))


def _style_cell(cell, *, fill=None, font=None, align=None, border=True):
    if fill:
        cell.fill = fill
    cell.font = font or FONT_BODY
    cell.alignment = align or Alignment(vertical="center", wrap_text=True)
    if border:
        cell.border = BORDER


def export_commission(csv_path: Path, xlsx_path: Path) -> int:
    rows = _load_rows(csv_path)
    wb = Workbook()

    # --- Feuille instructions ---
    ws_info = wb.active
    ws_info.title = "Instructions"
    ws_info.column_dimensions["A"].width = 90
    today = date.today().strftime("%d/%m/%Y")
    instructions = [
        ("IDENTIFICATION DU SEXE — AGENTS SANS RENSEIGNEMENT", FONT_TITLE),
        ("", FONT_BODY),
        (f"Date d'édition : {today}  |  Effectif : {len(rows)} agent(s)", FONT_SUB),
        ("", FONT_BODY),
        ("Objectif", Font(name="Calibri", size=11, bold=True)),
        (
            "Ce document recense les agents dont le sexe n'est pas encore renseigné dans le "
            "Système de Gestion des Impressions (S.G.I DPF). La commission est invitée à "
            "indiquer le sexe de chaque agent dans la colonne « SEXE À RENSEIGNER (M ou F) » "
            "de la feuille « Liste agents ».",
            FONT_BODY,
        ),
        ("", FONT_BODY),
        ("Consignes de saisie", Font(name="Calibri", size=11, bold=True)),
        ("• Saisir uniquement M (Masculin) ou F (Féminin) — une lettre majuscule.", FONT_BODY),
        ("• Les colonnes « Suggestion sexe / Matricule référence / Nom référence » sont "
         "indicatives (fichiers Excel de référence) : à valider ou infirmer.", FONT_BODY),
        ("• La colonne « Observations commission » est libre (source, doute, remarque…).", FONT_BODY),
        ("• Ne pas modifier les colonnes d'identification ni la « Réf. système ».", FONT_BODY),
        ("", FONT_BODY),
        ("Retour du fichier", Font(name="Calibri", size=11, bold=True)),
        (
            "Renvoyer le classeur complété au service informatique pour intégration en base.",
            FONT_BODY,
        ),
    ]
    for i, (text, font) in enumerate(instructions, start=1):
        c = ws_info.cell(i, 1, value=text)
        c.font = font
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws_info.row_dimensions[1].height = 28

    # --- Feuille données ---
    ws = wb.create_sheet("Liste agents")
    ncol = len(COLUMNS)
    keys = [k for k, _, _ in COLUMNS]

    # Titre
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    t = ws.cell(1, 1, value="COMMISSION — Identification du sexe (agents sans renseignement)")
    t.font = FONT_TITLE
    t.fill = FILL_TITLE
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    s = ws.cell(
        2, 1,
        value=f"Effectif : {len(rows)} agent(s)  —  Colonne jaune : saisie obligatoire (M ou F)",
    )
    s.font = FONT_SUB
    s.fill = FILL_TITLE
    s.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 22

    header_row = 4
    for col, (key, label, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
        cell = ws.cell(header_row, col, value=label)
        fill = FILL_INPUT if key == "sexe_correct" else FILL_HEADER
        font = FONT_HDR if key != "sexe_correct" else Font(
            name="Calibri", size=10, bold=True, color="7F6000",
        )
        _style_cell(cell, fill=fill, font=font, align=Alignment(horizontal="center", vertical="center", wrap_text=True))
    ws.row_dimensions[header_row].height = 36

    data_start = header_row + 1
    sexe_col_letter = get_column_letter(keys.index("sexe_correct") + 1)
    note_col_letter = get_column_letter(keys.index("note") + 1)

    for i, row in enumerate(rows):
        r = data_start + i
        alt = i % 2 == 1
        for col, (key, _, _) in enumerate(COLUMNS, start=1):
            val = (row.get(key) or "").strip()
            if key == "match_score" and val:
                try:
                    val = float(val.replace(",", "."))
                except ValueError:
                    pass
            elif key == "no" and val:
                try:
                    val = int(val)
                except ValueError:
                    pass
            cell = ws.cell(r, col, value=val or None)
            if key == "sexe_correct":
                fill = FILL_INPUT
                font = FONT_INPUT
            elif key == "note":
                fill = FILL_INPUT if not alt else PatternFill("solid", fgColor="FFF8E1")
                font = FONT_BODY
            elif key in ("ref_sexe", "ref_matricule", "ref_noms", "match_score") and val:
                fill = FILL_REF
                font = FONT_BODY
            elif key == "legacy_id":
                fill = FILL_ALT if alt else FILL_WHITE
                font = FONT_TECH
            else:
                fill = FILL_ALT if alt else FILL_WHITE
                font = FONT_BODY
            align = Alignment(
                horizontal="center" if key in ("no", "ref_sexe", "match_score", "sexe_correct", "legacy_id") else "left",
                vertical="center",
                wrap_text=key in ("noms", "ref_noms", "note"),
            )
            _style_cell(cell, fill=fill, font=font, align=align)

    if rows:
        dv = DataValidation(
            type="list",
            formula1='"M,F"',
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorTitle="Valeur invalide",
            error="Saisir M (Masculin) ou F (Féminin) uniquement.",
        )
        dv.add(f"{sexe_col_letter}{data_start}:{sexe_col_letter}{data_start + len(rows) - 1}")
        ws.add_data_validation(dv)
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncol)}{data_start + len(rows) - 1}"

    ws.freeze_panes = ws.cell(data_start, 1)
    ws.sheet_view.showGridLines = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f"{header_row}:{header_row}"

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Export Excel commission — agents sans sexe.")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--sortie", type=Path, default=DEFAULT_XLSX)
    args = p.parse_args()
    n = export_commission(args.csv.resolve(), args.sortie.resolve())
    print(f"Export commission : {args.sortie.resolve()}")
    print(f"  {n} agent(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
