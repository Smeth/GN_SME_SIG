#!/usr/bin/env python3
"""Compare LISTE_GENERALE_FICHIER.xls avec le personnel MySQL actuel."""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
import xlrd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sme_gend.config import get_settings
from sme_gend.config.liste_generale_scope import carriere_scope_sql, resolve_carriere_scope_mois
from sme_gend.services.personnel_sexe_reference_sync import (
    name_token_key,
    normalize_matricule_key,
    normalize_person_name,
)

DEFAULT_XLS = _ROOT / "LISTE_GENERALE_FICHIER.xls"
DEFAULT_OUT = _ROOT / "data" / "rapports" / "COMPARAISON_LISTE_GENERALE_FICHIER.xlsx"

# --- Styles Excel ---
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL_HDR = PatternFill("solid", fgColor="1F4E79")
FILL_TITLE = PatternFill("solid", fgColor="D6E4F0")
FILL_WARN = PatternFill("solid", fgColor="FCE4D6")
FILL_OK = PatternFill("solid", fgColor="E2EFDA")
FILL_ALT = PatternFill("solid", fgColor="F7F9FC")
FILL_WHITE = PatternFill("solid", fgColor="FFFFFF")
FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")

FONT_TITLE = Font(name="Calibri", size=14, bold=True, color="1F4E79")
FONT_SUB = Font(name="Calibri", size=10, italic=True, color="404040")
FONT_HDR = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
FONT_BODY = Font(name="Calibri", size=10)
FONT_BOLD = Font(name="Calibri", size=10, bold=True)


@dataclass(slots=True)
class FichierRow:
    idx: int
    no: str
    matricule: str
    mat_key: str
    grade: str
    nom: str
    prenom: str
    noms_ref: str
    nom_norm: str
    prenom_norm: str
    tokens: str
    date_naissance: str
    date_service: str
    date_nomination: str
    fonction: str
    structure: str


@dataclass(slots=True)
class DbRow:
    legacy_id: str
    noms: str
    noms_norm: str
    tokens: str
    sexe: str
    grade: str
    matricule: str
    mat_key: str
    matricule_solde: str
    date_naissance: str
    lieu_naissance: str
    date_grade: str
    date_service: str
    ndecision: str
    position_admin: str
    fonction: str
    subdivision_arme: str
    structure: str
    diplome: str
    date_obtention: str
    obs: str


@dataclass(slots=True)
class MatchPair:
    db: DbRow
    ref: FichierRow
    method: str


@dataclass(slots=True)
class CompareResult:
    db_rows: list[DbRow] = field(default_factory=list)
    file_rows: list[FichierRow] = field(default_factory=list)
    matched: list[MatchPair] = field(default_factory=list)
    absent_fichier: list[DbRow] = field(default_factory=list)
    absent_base: list[FichierRow] = field(default_factory=list)
    prenom_ecarts: list[dict] = field(default_factory=list)
    nom_ecarts: list[dict] = field(default_factory=list)
    mois_scope: str = ""


def _fmt_date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    return s if s.lower() != "none" else ""


def _xls_cell(sh, r: int, c: int, book) -> str:
    cell = sh.cell(r, c)
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, book.datemode).strftime("%d/%m/%Y")
    v = cell.value
    if v is None or str(v).strip() == "":
        return ""
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return str(v).strip()
    return str(v).strip()


def load_fichier(path: Path) -> list[FichierRow]:
    book = xlrd.open_workbook(str(path))
    sh = book.sheet_by_index(0)
    out: list[FichierRow] = []
    for r in range(3, sh.nrows):
        nom = _xls_cell(sh, r, 3, book)
        prenom = _xls_cell(sh, r, 4, book)
        mat = _xls_cell(sh, r, 1, book)
        if not nom and not prenom and not mat:
            continue
        if nom.upper() in ("NOM(S) ET PRENOM (S)", "NOM(S)  ET PRENOM(S)"):
            continue
        noms_ref = normalize_person_name(" ".join(p for p in (nom, prenom) if p))
        if not noms_ref and not mat:
            continue
        out.append(
            FichierRow(
                idx=len(out) + 1,
                no=_xls_cell(sh, r, 0, book),
                matricule=mat,
                mat_key=normalize_matricule_key(mat),
                grade=_xls_cell(sh, r, 2, book),
                nom=nom,
                prenom=prenom,
                noms_ref=noms_ref,
                nom_norm=normalize_person_name(nom),
                prenom_norm=normalize_person_name(prenom),
                tokens=name_token_key(noms_ref),
                date_naissance=_xls_cell(sh, r, 5, book),
                date_service=_xls_cell(sh, r, 6, book),
                date_nomination=_xls_cell(sh, r, 7, book),
                fonction=_xls_cell(sh, r, 8, book),
                structure=_xls_cell(sh, r, 11, book),
            )
        )
    return out


def _load_db_rows(settings) -> tuple[list[DbRow], str]:
    scope = resolve_carriere_scope_mois(settings)
    filt, params = carriere_scope_sql(scope, alias="c_pick")
    join = f"""INNER JOIN carriere_situation c ON c.id = (
        SELECT MAX(c_pick.id) FROM carriere_situation c_pick
        WHERE c_pick.agent_id = a.id AND {filt}
    )"""
    sql = f"""
    SELECT
      a.legacy_access_id, a.noms, a.sexe, a.date_naissance, a.lieu_naissance,
      IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle),
      c.date_grade, c.date_service, c.ndecision,
      a.matricule_militaire, a.matricule_solde,
      IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle),
      IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle),
      IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdiv_arme_libelle),
      IF(c.structure_id IS NOT NULL, rs.code, c.structure_code),
      c.diplome_libelle, c.date_obtention, c.obs
    FROM agent a
    {join}
    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
    LEFT JOIN ref_grade rg ON rg.id = c.grade_id
    LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
    LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
    ORDER BY a.noms
    """
    # fix typo subdiv_arme -> subdivision_arme
    sql = sql.replace("c.subdiv_arme_libelle", "c.subdivision_arme_libelle")

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
    )
    rows: list[DbRow] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for r in cur.fetchall():
                noms = (r[1] or "").strip()
                mat = (r[9] or "").strip()
                nn = normalize_person_name(noms)
                rows.append(
                    DbRow(
                        legacy_id=str(r[0] or "").strip(),
                        noms=noms,
                        noms_norm=nn,
                        tokens=name_token_key(noms),
                        sexe=(r[2] or "").strip(),
                        date_naissance=_fmt_date(r[3]),
                        lieu_naissance=(r[4] or "").strip(),
                        grade=(r[5] or "").strip(),
                        date_grade=_fmt_date(r[6]),
                        date_service=_fmt_date(r[7]) if r[7] else (str(r[7] or "").strip()),
                        ndecision=(r[8] or "").strip(),
                        matricule=mat,
                        mat_key=normalize_matricule_key(mat),
                        matricule_solde=(r[10] or "").strip(),
                        position_admin=(r[11] or "").strip(),
                        fonction=(r[12] or "").strip(),
                        subdivision_arme=(r[13] or "").strip(),
                        structure=(r[14] or "").strip(),
                        diplome=(r[15] or "").strip(),
                        date_obtention=_fmt_date(r[16]) if r[16] else (str(r[16] or "").strip()),
                        obs=(r[17] or "").strip(),
                    )
                )
    finally:
        conn.close()
    mois = scope or "mois actif (est_actuelle=1)"
    return rows, mois


def _extract_prenom_db(db_noms: str, ref_nom: str) -> tuple[str, str]:
    db_disp = (db_noms or "").strip()
    ref_disp = (ref_nom or "").strip()
    db_u = db_disp.upper()
    ref_u = ref_disp.upper()
    if ref_u and db_u.startswith(ref_u + " "):
        return ref_disp, db_disp[len(ref_disp) :].strip()
    if ref_u and db_u == ref_u:
        return ref_disp, ""

    nom_toks = normalize_person_name(ref_nom).split()
    db_toks = normalize_person_name(db_noms).split()
    parts = db_disp.split()
    if nom_toks and len(db_toks) > len(nom_toks) and db_toks[: len(nom_toks)] == nom_toks:
        if len(parts) > len(nom_toks):
            return " ".join(parts[: len(nom_toks)]), " ".join(parts[len(nom_toks) :])
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return ref_disp, db_disp


def _match_all(db_rows: list[DbRow], file_rows: list[FichierRow]) -> CompareResult:
    res = CompareResult(db_rows=db_rows, file_rows=file_rows)
    by_mat: dict[str, list[FichierRow]] = {}
    by_name: dict[str, list[FichierRow]] = {}
    by_tokens: dict[str, list[FichierRow]] = {}
    for fr in file_rows:
        if fr.mat_key and fr.mat_key != "0":
            by_mat.setdefault(fr.mat_key, []).append(fr)
        if fr.noms_ref:
            by_name.setdefault(fr.noms_ref, []).append(fr)
        if fr.tokens:
            by_tokens.setdefault(fr.tokens, []).append(fr)

    used_file: set[int] = set()

    def pick(cands: list[FichierRow]) -> FichierRow | None:
        free = [c for c in cands if c.idx not in used_file]
        if len(free) == 1:
            return free[0]
        return None

    for db in db_rows:
        ref: FichierRow | None = None
        method = ""
        if db.mat_key and db.mat_key != "0":
            ref = pick(by_mat.get(db.mat_key, []))
            if ref:
                method = "matricule"
        if ref is None and db.noms_norm:
            ref = pick(by_name.get(db.noms_norm, []))
            if ref:
                method = "nom_exact"
        if ref is None and db.tokens:
            ref = pick(by_tokens.get(db.tokens, []))
            if ref:
                method = "nom_tokens"
        if ref is None and db.noms_norm:
            close = difflib.get_close_matches(db.noms_norm, list(by_name.keys()), n=1, cutoff=0.92)
            if close:
                ref = pick(by_name.get(close[0], []))
                if ref:
                    method = "similarite_nom"
        if ref:
            used_file.add(ref.idx)
            res.matched.append(MatchPair(db=db, ref=ref, method=method))
        else:
            res.absent_fichier.append(db)

    for fr in file_rows:
        if fr.idx not in used_file:
            res.absent_base.append(fr)

    for mp in res.matched:
        db, ref = mp.db, mp.ref
        _, prenom_db = _extract_prenom_db(db.noms, ref.nom)
        prenom_db_norm = normalize_person_name(prenom_db)
        if ref.prenom_norm and prenom_db_norm != ref.prenom_norm:
            ratio = difflib.SequenceMatcher(None, prenom_db_norm, ref.prenom_norm).ratio()
            res.prenom_ecarts.append(
                {
                    "legacy_id": db.legacy_id,
                    "matricule_db": db.matricule,
                    "matricule_ref": ref.matricule,
                    "noms_db": db.noms,
                    "nom_ref": ref.nom,
                    "prenom_db": prenom_db,
                    "prenom_ref": ref.prenom,
                    "noms_complet_ref": f"{ref.nom} {ref.prenom}".strip(),
                    "grade_db": db.grade,
                    "grade_ref": ref.grade,
                    "structure_db": db.structure,
                    "structure_ref": ref.structure,
                    "methode_rapprochement": mp.method,
                    "score_prenom": round(ratio, 3),
                    "type_ecart": "orthographe_prenom",
                }
            )
        if ref.nom_norm and db.noms_norm:
            nom_db_extract, _ = _extract_prenom_db(db.noms, ref.nom)
            nom_db_norm = normalize_person_name(nom_db_extract)
            if nom_db_norm != ref.nom_norm and normalize_person_name(db.noms) != ref.noms_ref:
                ratio = difflib.SequenceMatcher(None, nom_db_norm, ref.nom_norm).ratio()
                if ratio < 0.98:
                    res.nom_ecarts.append(
                        {
                            "legacy_id": db.legacy_id,
                            "matricule_db": db.matricule,
                            "matricule_ref": ref.matricule,
                            "noms_db": db.noms,
                            "nom_db": nom_db_extract,
                            "nom_ref": ref.nom,
                            "prenom_ref": ref.prenom,
                            "noms_complet_ref": f"{ref.nom} {ref.prenom}".strip(),
                            "grade_db": db.grade,
                            "structure_db": db.structure,
                            "methode_rapprochement": mp.method,
                            "score_nom": round(ratio, 3),
                            "type_ecart": "orthographe_nom",
                        }
                    )
    return res


def _style_cell(cell, *, fill=None, font=None, align=None, border=True):
    cell.fill = fill or FILL_WHITE
    cell.font = font or FONT_BODY
    cell.alignment = align or Alignment(vertical="center", wrap_text=True)
    if border:
        cell.border = BORDER


def _write_table(ws, headers: list[tuple[str, str, float]], rows: list[dict], *, start_row: int = 1) -> int:
    """Écrit en-têtes + lignes. Retourne la dernière ligne écrite."""
    for col, (key, label, width) in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
        c = ws.cell(start_row, col, value=label)
        _style_cell(c, fill=FILL_HDR, font=FONT_HDR, align=Alignment(horizontal="center", vertical="center", wrap_text=True))
    ws.row_dimensions[start_row].height = 32
    keys = [h[0] for h in headers]
    for i, row in enumerate(rows):
        r = start_row + 1 + i
        alt = i % 2 == 1
        for col, key in enumerate(keys, start=1):
            val = row.get(key, "")
            cell = ws.cell(r, col, value=val if val != "" else None)
            _style_cell(cell, fill=FILL_ALT if alt else FILL_WHITE)
    ws.freeze_panes = ws.cell(start_row + 1, 1)
    if rows:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{start_row + len(rows)}"
    return start_row + len(rows)


def _sheet_title(ws, title: str, subtitle: str, ncol: int):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    t = ws.cell(1, 1, value=title)
    t.font = FONT_TITLE
    t.fill = FILL_TITLE
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    s = ws.cell(2, 1, value=subtitle)
    s.font = FONT_SUB
    s.fill = FILL_TITLE
    s.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20


def export_workbook(result: CompareResult, out_path: Path, *, source_file: str) -> None:
    today = date.today().strftime("%d/%m/%Y")
    wb = Workbook()

    # --- Synthèse ---
    ws0 = wb.active
    ws0.title = "Synthèse"
    ws0.column_dimensions["A"].width = 42
    ws0.column_dimensions["B"].width = 16
    ws0.column_dimensions["C"].width = 50
    stats = [
        ("COMPARAISON LISTE GÉNÉRALE — SYNTHÈSE", ""),
        (f"Date : {today}", ""),
        (f"Fichier référence : {source_file}", ""),
        (f"Périmètre base : personnel actif MySQL ({result.mois_scope})", ""),
        ("", ""),
        ("Effectif fichier référence", len(result.file_rows)),
        ("Effectif base MySQL (actif)", len(result.db_rows)),
        ("Correspondances établies", len(result.matched)),
        ("", ""),
        ("ABSENTS DU FICHIER RÉFÉRENCE (en base, pas dans le .xls)", len(result.absent_fichier)),
        ("ABSENTS DE LA BASE (dans le .xls, pas en base active)", len(result.absent_base)),
        ("", ""),
        ("Prénoms incorrects (rapprochés, réf. = fichier)", len(result.prenom_ecarts)),
        ("Noms de famille divergents (rapprochés)", len(result.nom_ecarts)),
        ("", ""),
        ("Feuilles du classeur", "Contenu"),
        ("Absent fichier référence", "Agents MySQL sans correspondance dans LISTE_GENERALE_FICHIER.xls"),
        ("Absent base MySQL", "Agents du fichier .xls absents du personnel actif MySQL"),
        ("Prénoms à corriger", "Même agent rapproché — prénom(s) système ≠ prénom(s) fichier"),
        ("Noms divergents", "Même agent rapproché — nom de famille différent"),
        ("Méthodes rapprochement", "Détail des correspondances (matricule, nom, tokens…)"),
    ]
    for i, (a, b) in enumerate(stats, start=1):
        ca = ws0.cell(i, 1, a)
        cb = ws0.cell(i, 2, b if b != "" else None)
        ca.font = FONT_BOLD if i in (1, 9, 10, 13, 14) else FONT_BODY
        cb.font = FONT_BOLD if isinstance(b, int) else FONT_BODY
        if i == 1:
            ca.font = FONT_TITLE

    # --- Absent fichier ---
    ws1 = wb.create_sheet("Absent fichier référence")
    h1 = [
        ("no", "N°", 5),
        ("legacy_id", "Réf. système", 12),
        ("noms", "Nom(s) et prénom(s) — BASE", 36),
        ("grade", "Grade", 22),
        ("matricule", "Matricule militaire", 18),
        ("matricule_solde", "Matricule solde", 16),
        ("sexe", "Sexe", 8),
        ("date_naissance", "Date naissance", 14),
        ("lieu_naissance", "Lieu naissance", 18),
        ("date_grade", "Date grade", 14),
        ("date_service", "Date service", 14),
        ("ndecision", "N° décision", 14),
        ("position_admin", "Position admin.", 18),
        ("fonction", "Fonction", 28),
        ("subdivision_arme", "Subdivision arme", 18),
        ("structure", "Structure", 14),
        ("diplome", "Diplôme", 16),
        ("obs", "Observations", 24),
    ]
    _sheet_title(ws1, "ABSENTS DU FICHIER RÉFÉRENCE", f"{len(result.absent_fichier)} agent(s) en base MySQL non trouvé(s) dans le fichier", len(h1))
    rows1 = [
        {
            "no": i + 1,
            "legacy_id": d.legacy_id,
            "noms": d.noms,
            "grade": d.grade,
            "matricule": d.matricule,
            "matricule_solde": d.matricule_solde,
            "sexe": d.sexe,
            "date_naissance": d.date_naissance,
            "lieu_naissance": d.lieu_naissance,
            "date_grade": d.date_grade,
            "date_service": d.date_service,
            "ndecision": d.ndecision,
            "position_admin": d.position_admin,
            "fonction": d.fonction,
            "subdivision_arme": d.subdivision_arme,
            "structure": d.structure,
            "diplome": d.diplome,
            "obs": d.obs,
        }
        for i, d in enumerate(result.absent_fichier)
    ]
    _write_table(ws1, h1, rows1, start_row=4)

    # --- Absent base ---
    ws2 = wb.create_sheet("Absent base MySQL")
    h2 = [
        ("no", "N°", 5),
        ("no_fichier", "N° fichier", 10),
        ("nom", "Nom(s) — FICHIER", 24),
        ("prenom", "Prénom(s) — FICHIER", 28),
        ("noms_complet", "Nom complet", 36),
        ("grade", "Grade", 12),
        ("matricule", "Matricule militaire", 18),
        ("date_naissance", "Date naissance", 14),
        ("date_service", "Date entrée service", 16),
        ("date_nomination", "Date nomination", 14),
        ("fonction", "Fonction", 32),
        ("structure", "Structure", 14),
    ]
    _sheet_title(ws2, "ABSENTS DE LA BASE MYSQL", f"{len(result.absent_base)} agent(s) du fichier non trouvé(s) dans le personnel actif", len(h2))
    rows2 = [
        {
            "no": i + 1,
            "no_fichier": f.no,
            "nom": f.nom,
            "prenom": f.prenom,
            "noms_complet": f"{f.nom} {f.prenom}".strip(),
            "grade": f.grade,
            "matricule": f.matricule,
            "date_naissance": f.date_naissance,
            "date_service": f.date_service,
            "date_nomination": f.date_nomination,
            "fonction": f.fonction,
            "structure": f.structure,
        }
        for i, f in enumerate(result.absent_base)
    ]
    _write_table(ws2, h2, rows2, start_row=4)

    # --- Prénoms ---
    ws3 = wb.create_sheet("Prénoms à corriger")
    h3 = [
        ("no", "N°", 5),
        ("legacy_id", "Réf. système", 12),
        ("matricule_db", "Matricule base", 16),
        ("matricule_ref", "Matricule fichier", 16),
        ("nom_ref", "Nom (fichier réf.)", 22),
        ("prenom_db", "Prénom(s) ACTUEL — système", 28),
        ("prenom_ref", "Prénom(s) CORRECT — fichier", 28),
        ("noms_db", "Libellé complet base", 36),
        ("noms_complet_ref", "Libellé complet fichier", 36),
        ("grade_db", "Grade base", 20),
        ("structure_db", "Structure base", 12),
        ("methode_rapprochement", "Rapprochement", 14),
        ("score_prenom", "Score similarité", 12),
    ]
    _sheet_title(
        ws3,
        "PRÉNOMS INCORRECTS DANS LE SYSTÈME",
        f"{len(result.prenom_ecarts)} cas — référence orthographe = colonne prénom du fichier LISTE_GENERALE_FICHIER.xls",
        len(h3),
    )
    rows3 = [{"no": i + 1, **e} for i, e in enumerate(sorted(result.prenom_ecarts, key=lambda x: x["noms_db"]))]
    _write_table(ws3, h3, rows3, start_row=4)
    for r in range(5, 5 + len(rows3)):
        ws3.cell(r, 6).fill = FILL_WARN
        ws3.cell(r, 7).fill = FILL_OK

    # --- Noms divergents ---
    ws4 = wb.create_sheet("Noms divergents")
    h4 = [
        ("no", "N°", 5),
        ("legacy_id", "Réf. système", 12),
        ("matricule_db", "Matricule base", 16),
        ("nom_db", "Nom ACTUEL — système", 24),
        ("nom_ref", "Nom CORRECT — fichier", 24),
        ("prenom_ref", "Prénom(s) fichier", 28),
        ("noms_db", "Libellé complet base", 36),
        ("noms_complet_ref", "Libellé complet fichier", 36),
        ("methode_rapprochement", "Rapprochement", 14),
        ("score_nom", "Score similarité", 12),
    ]
    _sheet_title(ws4, "NOMS DE FAMILLE DIVERGENTS", f"{len(result.nom_ecarts)} cas rapprochés par matricule/nom proche", len(h4))
    rows4 = [{"no": i + 1, **e} for i, e in enumerate(sorted(result.nom_ecarts, key=lambda x: x["noms_db"]))]
    _write_table(ws4, h4, rows4, start_row=4)

    # --- Méthodes rapprochement (stats) ---
    ws5 = wb.create_sheet("Méthodes rapprochement")
    from collections import Counter

    ctr = Counter(mp.method for mp in result.matched)
    h5 = [("methode", "Méthode", 20), ("count", "Nombre", 12)]
    _sheet_title(ws5, "CORRESPONDANCES ÉTABLIES", f"{len(result.matched)} rapprochement(s)", 2)
    rows5 = [{"methode": k, "count": v} for k, v in sorted(ctr.items(), key=lambda x: -x[1])]
    _write_table(ws5, h5, rows5, start_row=4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main() -> int:
    p = argparse.ArgumentParser(description="Compare LISTE_GENERALE_FICHIER.xls vs personnel MySQL.")
    p.add_argument("--fichier", type=Path, default=DEFAULT_XLS)
    p.add_argument("--sortie", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    xls_path = args.fichier.resolve()
    if not xls_path.is_file():
        alt = _ROOT.parent / "LISTE_GENERALE_FICHIER.xls"
        if alt.is_file():
            xls_path = alt
        else:
            print(f"Fichier introuvable : {args.fichier}")
            return 1

    settings = get_settings()
    file_rows = load_fichier(xls_path)
    db_rows, mois = _load_db_rows(settings)
    result = _match_all(db_rows, file_rows)
    result.mois_scope = mois

    export_workbook(result, args.sortie.resolve(), source_file=xls_path.name)

    print(f"Comparaison : {xls_path.name} vs MySQL ({mois})")
    print(f"  Fichier référence     : {len(file_rows)}")
    print(f"  Base MySQL (actif)    : {len(db_rows)}")
    print(f"  Correspondances       : {len(result.matched)}")
    print(f"  Absents fichier réf.  : {len(result.absent_fichier)}")
    print(f"  Absents base MySQL    : {len(result.absent_base)}")
    print(f"  Prénoms incorrects    : {len(result.prenom_ecarts)}")
    print(f"  Noms divergents       : {len(result.nom_ecarts)}")
    print(f"Export : {args.sortie.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
