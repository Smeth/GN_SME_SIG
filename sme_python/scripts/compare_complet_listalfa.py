#!/usr/bin/env python3
"""Compare COMPLET_LISTALFA_GENERALE_FICHER.xls vs MySQL + dernier fichier SME."""

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
from openpyxl.worksheet.page import PageMargins
from sme_gend.config import get_settings
from sme_gend.config.liste_generale_scope import carriere_scope_sql, resolve_carriere_scope_mois
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.gain_perte import observ_is_deces
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.personnel_sexe import SEXE_F, normalize_sexe
from sme_gend.services.access_odbc import build_access_connection_string
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.services.personnel_sexe_reference_sync import (
    name_token_key,
    normalize_matricule_key,
    normalize_person_name,
)

DEFAULT_ALFA = _ROOT / "COMPLET_LISTALFA_GENERALE_FICHER.xls"
DEFAULT_OUT = _ROOT / "data" / "rapports" / "DECALAGE_COMPLET_LISTALFA_vs_SYSTEME.xlsx"
DEFAULT_SME = _ROOT / "smegend_mai.mdb"
DATA_START_ROW = 4  # ligne 5 Excel — après en-têtes ALFA

THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL_HDR = PatternFill("solid", fgColor="1F4E79")
FILL_TITLE = PatternFill("solid", fgColor="D6E4F0")
FILL_WARN = PatternFill("solid", fgColor="FCE4D6")
FILL_ALT = PatternFill("solid", fgColor="F7F9FC")
FILL_WHITE = PatternFill("solid", fgColor="FFFFFF")
FONT_TITLE = Font(name="Calibri", size=14, bold=True, color="1F4E79")
FONT_SUB = Font(name="Calibri", size=10, italic=True, color="404040")
FONT_HDR = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
FONT_BODY = Font(name="Calibri", size=10)
FONT_BODY_PRINT = Font(name="Calibri", size=9)
FONT_BOLD = Font(name="Calibri", size=10, bold=True)


@dataclass(slots=True)
class RefRow:
    idx: int
    no: str
    legacy_id: str
    matricule: str
    mat_key: str
    grade: str
    nom: str
    prenom: str
    noms_complet: str
    noms_norm: str
    tokens: str
    date_naissance: str
    date_service: str
    date_nomination: str
    fonction: str
    structure: str


@dataclass(slots=True)
class AgentRow:
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
    source: str = "MySQL"


@dataclass(slots=True)
class CompareBundle:
    matched: list[tuple[AgentRow, RefRow, str]] = field(default_factory=list)
    absent_ref: list[AgentRow] = field(default_factory=list)
    absent_other: list[RefRow] = field(default_factory=list)


def _fmt_date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    return s if s.lower() not in ("none", "nan") else ""


def _xls_cell(sh, r: int, c: int, book) -> str:
    cell = sh.cell(r, c)
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, book.datemode).strftime("%d/%m/%Y")
    v = cell.value
    if v is None or str(v).strip() == "":
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def load_alfa(path: Path) -> list[RefRow]:
    book = xlrd.open_workbook(str(path))
    sh = book.sheet_by_index(0)
    out: list[RefRow] = []
    for r in range(DATA_START_ROW, sh.nrows):
        nom = _xls_cell(sh, r, 3, book)
        prenom = _xls_cell(sh, r, 4, book)
        mat = _xls_cell(sh, r, 1, book)
        if not nom and not prenom and not mat:
            continue
        if nom.upper().startswith("NOM"):
            continue
        noms_norm = normalize_person_name(" ".join(p for p in (nom, prenom) if p))
        if not noms_norm and not mat:
            continue
        out.append(
            RefRow(
                idx=len(out) + 1,
                no=_xls_cell(sh, r, 0, book),
                legacy_id="",
                matricule=mat,
                mat_key=normalize_matricule_key(mat),
                grade=_xls_cell(sh, r, 2, book),
                nom=nom,
                prenom=prenom,
                noms_complet=f"{nom} {prenom}".strip(),
                noms_norm=noms_norm,
                tokens=name_token_key(noms_norm),
                date_naissance=_xls_cell(sh, r, 5, book),
                date_service=_xls_cell(sh, r, 6, book),
                date_nomination=_xls_cell(sh, r, 7, book),
                fonction=_xls_cell(sh, r, 8, book),
                structure=_xls_cell(sh, r, 11, book),
            )
        )
    return out


def load_mysql(settings) -> tuple[list[AgentRow], str]:
    scope = resolve_carriere_scope_mois(settings)
    filt, params = carriere_scope_sql(scope, alias="c_pick")
    join = f"""INNER JOIN carriere_situation c ON c.id = (
        SELECT MAX(c_pick.id) FROM carriere_situation c_pick
        WHERE c_pick.agent_id = a.id AND {filt}
    )"""
    sql = f"""
    SELECT a.legacy_access_id, a.noms, a.sexe, a.date_naissance, a.lieu_naissance,
      IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle),
      c.date_grade, c.date_service, c.ndecision,
      a.matricule_militaire, a.matricule_solde,
      IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle),
      IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle),
      IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle),
      IF(c.structure_id IS NOT NULL, rs.code, c.structure_code),
      c.diplome_libelle, c.date_obtention, c.obs
    FROM agent a {join}
    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
    LEFT JOIN ref_grade rg ON rg.id = c.grade_id
    LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
    LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
    ORDER BY a.noms
    """
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=settings.mysql_database,
        charset="utf8mb4",
    )
    rows: list[AgentRow] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for r in cur.fetchall():
                noms = (r[1] or "").strip()
                mat = (r[9] or "").strip()
                rows.append(
                    AgentRow(
                        legacy_id=str(r[0] or "").strip(),
                        noms=noms,
                        noms_norm=normalize_person_name(noms),
                        tokens=name_token_key(noms),
                        sexe=(r[2] or "").strip(),
                        date_naissance=_fmt_date(r[3]),
                        lieu_naissance=(r[4] or "").strip(),
                        grade=(r[5] or "").strip(),
                        date_grade=_fmt_date(r[6]),
                        date_service=_fmt_date(r[7]) if r[7] else str(r[7] or "").strip(),
                        ndecision=(r[8] or "").strip(),
                        matricule=mat,
                        mat_key=normalize_matricule_key(mat),
                        matricule_solde=(r[10] or "").strip(),
                        position_admin=(r[11] or "").strip(),
                        fonction=(r[12] or "").strip(),
                        subdivision_arme=(r[13] or "").strip(),
                        structure=(r[14] or "").strip(),
                        diplome=(r[15] or "").strip(),
                        date_obtention=_fmt_date(r[16]) if r[16] else str(r[16] or "").strip(),
                        obs=(r[17] or "").strip(),
                        source="MySQL",
                    )
                )
    finally:
        conn.close()
    mois = scope or "est_actuelle=1"
    ctx = load_sme_context()
    label = f"MySQL actif — SME {ctx.mois_actif or mois}"
    return rows, label


def _resolve_latest_sme_path(settings, explicit: Path | None) -> tuple[Path, str]:
    if explicit and explicit.is_file():
        ctx = load_sme_context()
        return explicit.resolve(), ctx.mois_actif or "2026-05"
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=settings.mysql_database, charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.annee_mois, il.chemin_fichier
                FROM import_lot il
                JOIN sme_periode p ON p.id = il.sme_periode_id
                WHERE il.chemin_fichier LIKE '%.mdb' AND il.chemin_fichier NOT LIKE '%test%'
                ORDER BY p.annee_mois DESC, il.importe_le DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if row and row[1]:
                p = Path(str(row[1]))
                if p.is_file():
                    return p.resolve(), str(row[0])
    finally:
        conn.close()
    if DEFAULT_SME.is_file():
        return DEFAULT_SME.resolve(), load_sme_context().mois_actif or "2026-05"
    raise FileNotFoundError("Aucun fichier SME .mdb trouvé.")


def load_sme_mdb(path: Path) -> list[AgentRow]:
    import pyodbc

    cs, _ = build_access_connection_string(path)
    conn = pyodbc.connect(cs, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT Id, noms, DateN, LieuN, Grade, DateGrade, DateService, Ndecision,
               Matmilitaire, Matsolde, Padmin, Fonction, SubArme, Structure, Obs
        FROM ListeGenerale ORDER BY Structure, Grade, noms
        """
    )
    rows: list[AgentRow] = []
    for r in cur.fetchall():
        noms = (r[1] or "").strip()
        mat = (r[8] or "").strip()
        rows.append(
            AgentRow(
                legacy_id=str(r[0] or "").strip(),
                noms=noms,
                noms_norm=normalize_person_name(noms),
                tokens=name_token_key(noms),
                sexe="",
                date_naissance=_fmt_date(r[2]),
                lieu_naissance=(r[3] or "").strip(),
                grade=(r[4] or "").strip(),
                date_grade=_fmt_date(r[5]),
                date_service=_fmt_date(r[6]) if r[6] else str(r[6] or "").strip(),
                ndecision=(r[7] or "").strip(),
                matricule=mat,
                mat_key=normalize_matricule_key(mat),
                matricule_solde=(r[9] or "").strip(),
                position_admin=(r[10] or "").strip(),
                fonction=(r[11] or "").strip(),
                subdivision_arme=(r[12] or "").strip(),
                structure=(r[13] or "").strip(),
                diplome="",
                date_obtention="",
                obs=(r[14] or "").strip(),
                source=path.name,
            )
        )
    conn.close()
    return rows


# Seuil similarité noms (rapprochement UNIQUEMENT par nom, jamais matricule)
NAME_SIMILARITY_CUTOFF = 0.92
# Seuil plus bas pour la feuille « ALFA rapprochement » (aide à la décision manuelle)
SUGGESTION_NAME_CUTOFF = 0.78
# Feuilles TOTAL absent ALFA : score > 70 % ⇒ considéré présent dans ALFA
ALFA_PRESENT_SCORE = 0.70
FEMMES_ALFA_PRESENT_SCORE = ALFA_PRESENT_SCORE  # alias rétrocompat.

FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")
FILL_SUGG_IDENT = PatternFill("solid", fgColor="FCE4D6")
FILL_SUGG_CLOSE = PatternFill("solid", fgColor="E2EFDA")
FILL_SUGG_NONE = PatternFill("solid", fgColor="F2F2F2")
FILL_RETRAITE = PatternFill("solid", fgColor="DDEBF7")

CATEGORY_LABELS = {
    "nom_identique_libre": "Nom identique — système libre",
    "nom_identique_deja_pris": "Nom identique — déjà rapproché",
    "nom_proche": "Nom proche (à valider)",
    "nom_tres_proche": "Très proche — non rapproché",
    "nom_tres_proche_deja_pris": "Très proche — déjà rapproché",
    "aucune_piste": "Aucune piste nom",
}


def _free_refs(cands: list[RefRow], used_ref: set[int]) -> list[RefRow]:
    return [c for c in cands if id(c) not in used_ref]


def compare_agents_to_refs(agents: list[AgentRow], refs: list[RefRow]) -> CompareBundle:
    """
    Rapproche MySQL/SME ↔ ALFA **uniquement par nom** :
    1) nom normalisé exact  2) tokens triés  3) similarité (≥ NAME_SIMILARITY_CUTOFF)
    Le matricule n'est jamais utilisé pour matcher.
    """
    bundle = CompareBundle()
    by_name: dict[str, list[RefRow]] = {}
    by_tokens: dict[str, list[RefRow]] = {}
    for ref in refs:
        if ref.noms_norm:
            by_name.setdefault(ref.noms_norm, []).append(ref)
        if ref.tokens:
            by_tokens.setdefault(ref.tokens, []).append(ref)
    used_ref: set[int] = set()

    for ag in agents:
        ref = None
        method = ""
        if ag.noms_norm:
            cands = _free_refs(by_name.get(ag.noms_norm, []), used_ref)
            if len(cands) == 1:
                ref, method = cands[0], "nom_exact"
        if ref is None and ag.tokens:
            cands = _free_refs(by_tokens.get(ag.tokens, []), used_ref)
            if len(cands) == 1:
                ref, method = cands[0], "nom_tokens"
        if ref is None and ag.noms_norm:
            available_keys = [k for k in by_name if _free_refs(by_name[k], used_ref)]
            close = difflib.get_close_matches(
                ag.noms_norm, available_keys, n=1, cutoff=NAME_SIMILARITY_CUTOFF,
            )
            if close:
                cands = _free_refs(by_name.get(close[0], []), used_ref)
                if len(cands) == 1:
                    ref, method = cands[0], "similarite"
        if ref:
            used_ref.add(id(ref))
            bundle.matched.append((ag, ref, method))
        else:
            bundle.absent_ref.append(ag)

    for ref in refs:
        if id(ref) not in used_ref:
            bundle.absent_other.append(ref)
    return bundle


def compare_agent_lists(left: list[AgentRow], right: list[AgentRow]) -> CompareBundle:
    """MySQL ↔ dernier SME — rapprochement par nom uniquement."""
    refs = [
        RefRow(
            idx=i + 1, no="", legacy_id=a.legacy_id, matricule=a.matricule, mat_key=a.mat_key,
            grade=a.grade, nom=a.noms.split()[0] if a.noms else "",
            prenom=" ".join(a.noms.split()[1:]) if a.noms else "",
            noms_complet=a.noms, noms_norm=a.noms_norm, tokens=a.tokens,
            date_naissance=a.date_naissance, date_service=a.date_service, date_nomination="",
            fonction=a.fonction, structure=a.structure,
        )
        for i, a in enumerate(right)
    ]
    return compare_agents_to_refs(left, refs)


def _load_grade_helper(settings) -> GradeSortHelper:
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=settings.mysql_database, charset="utf8mb4",
    )
    grades: list[GradeRefRow] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code, libelle, pref_ord, effectif_g FROM ref_grade ORDER BY pref_ord, libelle"
            )
            for r in cur.fetchall():
                grades.append(GradeRefRow.from_sequence(r))
    finally:
        conn.close()
    return GradeSortHelper(grades)


def _sort_agents(agents: list[AgentRow], gh: GradeSortHelper) -> list[AgentRow]:
    return sorted(agents, key=lambda a: (gh.sort_key(a.grade), a.noms_norm, a.legacy_id))


def _sort_refs(refs: list[RefRow], gh: GradeSortHelper) -> list[RefRow]:
    return sorted(refs, key=lambda r: (gh.sort_key(r.grade), r.noms_norm, r.matricule))


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(a=a, b=b).ratio()


def _mysql_name_index(rows: list[AgentRow]) -> dict[str, list[AgentRow]]:
    idx: dict[str, list[AgentRow]] = {}
    for ag in rows:
        if ag.noms_norm:
            idx.setdefault(ag.noms_norm, []).append(ag)
    return idx


def _ref_name_index(refs: list[RefRow]) -> dict[str, list[RefRow]]:
    idx: dict[str, list[RefRow]] = {}
    for ref in refs:
        if ref.noms_norm:
            idx.setdefault(ref.noms_norm, []).append(ref)
    return idx


def _find_similar_suggestion(
    noms_norm: str,
    by_name: dict[str, list],
    name_keys: list[str],
    matched_ids: set,
    get_id,
    *,
    empty_detail: str = "",
    homonyme_label: str = "dans la cible",
    name_cutoff: float | None = None,
) -> tuple[object | None, float, str, str]:
    """Meilleur candidat par nom exact puis similarité."""
    cutoff = SUGGESTION_NAME_CUTOFF if name_cutoff is None else name_cutoff
    if noms_norm and noms_norm in by_name:
        cands = by_name[noms_norm]
        item = cands[0]
        detail = f"{len(cands)} homonyme(s) {homonyme_label}" if len(cands) > 1 else ""
        cat = (
            "nom_identique_deja_pris"
            if get_id(item) in matched_ids
            else "nom_identique_libre"
        )
        return item, 1.0, cat, detail

    if not noms_norm:
        return None, 0.0, "aucune_piste", empty_detail

    close = difflib.get_close_matches(
        noms_norm, name_keys, n=5, cutoff=cutoff,
    )
    if not close:
        return None, 0.0, "aucune_piste", ""

    best_key = max(close, key=lambda k: _name_similarity(noms_norm, k))
    score = _name_similarity(noms_norm, best_key)
    item = by_name[best_key][0]
    if score >= NAME_SIMILARITY_CUTOFF:
        cat = (
            "nom_tres_proche_deja_pris"
            if get_id(item) in matched_ids
            else "nom_tres_proche"
        )
    else:
        cat = "nom_proche"
    return item, score, cat, ""


def _find_mysql_suggestion(
    ref: RefRow,
    mysql_by_name: dict[str, list[AgentRow]],
    mysql_name_keys: list[str],
    matched_mysql_ids: set[int],
) -> tuple[AgentRow | None, float, str, str]:
    """Meilleur candidat MySQL pour un agent ALFA absent du rapprochement principal."""
    ag, score, cat, detail = _find_similar_suggestion(
        ref.noms_norm,
        mysql_by_name,
        mysql_name_keys,
        matched_mysql_ids,
        lambda a: a.legacy_id,
        empty_detail="Nom ALFA vide",
        homonyme_label="dans le système",
    )
    return ag, score, cat, detail


def _find_alfa_suggestion(
    ag: AgentRow,
    alfa_by_name: dict[str, list[RefRow]],
    alfa_name_keys: list[str],
    matched_alfa_idx: set[int],
    *,
    name_cutoff: float | None = None,
) -> tuple[RefRow | None, float, str, str]:
    """Meilleur candidat ALFA pour un agent MySQL absent du rapprochement principal."""
    ref, score, cat, detail = _find_similar_suggestion(
        ag.noms_norm,
        alfa_by_name,
        alfa_name_keys,
        matched_alfa_idx,
        lambda r: r.idx,
        empty_detail="Nom système vide",
        homonyme_label="dans ALFA",
        name_cutoff=name_cutoff,
    )
    return ref, score, cat, detail


def _build_alfa_rapprochement_rows(
    absent_alfa: list[RefRow],
    mysql_rows: list[AgentRow],
    matched_pairs: list[tuple[AgentRow, RefRow, str]],
    grade_helper: GradeSortHelper,
) -> list[dict]:
    mysql_by_name = _mysql_name_index(mysql_rows)
    mysql_name_keys = list(mysql_by_name.keys())
    matched_mysql_ids = {ag.legacy_id for ag, _, _ in matched_pairs}
    matched_mysql_methods = {ag.legacy_id: method for ag, _, method in matched_pairs}

    rows: list[dict] = []
    for i, ref in enumerate(_sort_refs(absent_alfa, grade_helper)):
        ag, score, cat, detail = _find_mysql_suggestion(
            ref, mysql_by_name, mysql_name_keys, matched_mysql_ids,
        )
        deja = ""
        methode_principale = ""
        if ag is not None:
            if ag.legacy_id in matched_mysql_ids:
                deja = "Oui"
                methode_principale = matched_mysql_methods.get(ag.legacy_id, "")
            else:
                deja = "Non"

        rows.append({
            "no": i + 1,
            "categorie": CATEGORY_LABELS.get(cat, cat),
            "score_pct": f"{score * 100:.1f} %" if score > 0 else "",
            "grade_alfa": ref.grade,
            "nom_alfa": ref.noms_complet,
            "matricule_alfa": ref.matricule,
            "structure_alfa": ref.structure,
            "nom_systeme": ag.noms if ag else "",
            "matricule_systeme": ag.matricule if ag else "",
            "structure_systeme": ag.structure if ag else "",
            "ref_systeme": ag.legacy_id if ag else "",
            "deja_rapproche": deja,
            "methode_match": methode_principale,
            "detail": detail,
            "decision": "",
            "note": "",
            "_cat_key": cat,
        })
    return rows


def _build_mysql_rapprochement_rows(
    absent_mysql: list[AgentRow],
    all_alfa: list[RefRow],
    matched_pairs: list[tuple[AgentRow, RefRow, str]],
    grade_helper: GradeSortHelper,
    *,
    extra_from_agent=None,
    suggestion_cutoff: float | None = None,
) -> list[dict]:
    alfa_by_name = _ref_name_index(all_alfa)
    alfa_name_keys = list(alfa_by_name.keys())
    matched_alfa_idx = {ref.idx for _, ref, _ in matched_pairs}
    matched_alfa_methods = {ref.idx: method for _, ref, method in matched_pairs}

    rows: list[dict] = []
    for i, ag in enumerate(_sort_agents(absent_mysql, grade_helper)):
        ref, score, cat, detail = _find_alfa_suggestion(
            ag, alfa_by_name, alfa_name_keys, matched_alfa_idx,
            name_cutoff=suggestion_cutoff,
        )
        deja = ""
        methode_principale = ""
        if ref is not None:
            if ref.idx in matched_alfa_idx:
                deja = "Oui"
                methode_principale = matched_alfa_methods.get(ref.idx, "")
            else:
                deja = "Non"

        row = {
            "no": i + 1,
            "categorie": CATEGORY_LABELS.get(cat, cat),
            "score_pct": f"{score * 100:.1f} %" if score > 0 else "",
            "grade_systeme": ag.grade,
            "nom_systeme": ag.noms,
            "matricule_systeme": ag.matricule,
            "structure_systeme": ag.structure,
            "ref_systeme": ag.legacy_id,
            "grade_alfa": ref.grade if ref else "",
            "nom_alfa": ref.noms_complet if ref else "",
            "matricule_alfa": ref.matricule if ref else "",
            "structure_alfa": ref.structure if ref else "",
            "no_alfa": ref.no if ref else "",
            "deja_rapproche": deja,
            "methode_match": methode_principale,
            "detail": detail,
            "decision": "",
            "note": "",
            "_cat_key": cat,
            "_score": score,
        }
        if extra_from_agent:
            row.update(extra_from_agent(ag))
        rows.append(row)
    return rows


def _is_retraite(position_admin: str, obs: str) -> bool:
    p = (position_admin or "").strip().casefold()
    o = (obs or "").strip().casefold()
    return "retrait" in p or p == "retraite" or "retrait" in o


def _motif_sortie_label(ag: AgentRow) -> str | None:
    """Motif explicite si agent décédé ou retraité (position admin. ou observations)."""
    padmin = (ag.position_admin or "").strip()
    obs = (ag.obs or "").strip()
    if _is_retraite(padmin, obs):
        if padmin and "retrait" in padmin.casefold():
            return f"Retraite — {padmin}"
        if obs and "retrait" in obs.casefold():
            return f"Retraite — {obs}"
        return f"Retraite — {padmin or obs or 'position admin.'}"
    if observ_is_deces(padmin) or observ_is_deces(obs):
        return f"Décès — {obs or padmin}"
    p_cf = padmin.casefold()
    if any(k in p_cf for k in ("deced", "décéd", "deces", "décès")):
        return f"Décès — {padmin}"
    return None


def _filter_femmes_absent_alfa(absent_mysql: list[AgentRow]) -> list[AgentRow]:
    return [ag for ag in absent_mysql if normalize_sexe(ag.sexe) == SEXE_F]


def _filter_deces_retraites_absent_alfa(absent_mysql: list[AgentRow]) -> list[AgentRow]:
    return [ag for ag in absent_mysql if _motif_sortie_label(ag)]


def _filter_total_absent_alfa_rows(
    rappro_rows: list[dict],
    *,
    present_score: float = ALFA_PRESENT_SCORE,
) -> tuple[list[dict], int]:
    """
    Personnel réellement absent d'ALFA : exclut score > present_score
    (considéré présent dans ALFA malgré l'écart de rapprochement strict).
    """
    kept: list[dict] = []
    for row in rappro_rows:
        if row.get("_score", 0.0) > present_score:
            continue
        kept.append(dict(row))
    for i, row in enumerate(kept, 1):
        row["no"] = i
    excluded = len(rappro_rows) - len(kept)
    return kept, excluded


def _build_total_femmes_absent_rows(
    femmes_rows: list[dict],
    *,
    present_score: float = ALFA_PRESENT_SCORE,
) -> tuple[list[dict], int]:
    return _filter_total_absent_alfa_rows(femmes_rows, present_score=present_score)


RAPPROCHEMENT_HEADERS_ALFA = [
    ("no", "N°", 4),
    ("categorie", "Catégorie suggestion", 28),
    ("score_pct", "Score", 8),
    ("grade_alfa", "Grade ALFA", 16),
    ("nom_alfa", "Nom ALFA", 34),
    ("matricule_alfa", "Mle ALFA", 12),
    ("structure_alfa", "Struct. ALFA", 10),
    ("nom_systeme", "Nom système MySQL", 34),
    ("matricule_systeme", "Mle système", 12),
    ("structure_systeme", "Struct. système", 10),
    ("ref_systeme", "Réf. syst.", 8),
    ("deja_rapproche", "Déjà rapproché ?", 14),
    ("methode_match", "Méthode match", 14),
    ("detail", "Détail", 22),
    ("decision", "Décision", 16),
    ("note", "Note", 20),
]

RAPPROCHEMENT_HEADERS_MYSQL = [
    ("no", "N°", 4),
    ("categorie", "Catégorie suggestion", 28),
    ("score_pct", "Score", 8),
    ("grade_systeme", "Grade système", 16),
    ("nom_systeme", "Nom système MySQL", 34),
    ("matricule_systeme", "Mle système", 12),
    ("structure_systeme", "Struct. système", 10),
    ("ref_systeme", "Réf. syst.", 8),
    ("grade_alfa", "Grade ALFA", 16),
    ("nom_alfa", "Nom ALFA", 34),
    ("matricule_alfa", "Mle ALFA", 12),
    ("structure_alfa", "Struct. ALFA", 10),
    ("no_alfa", "N° ALFA", 8),
    ("deja_rapproche", "Déjà rapproché ?", 14),
    ("methode_match", "Méthode match", 14),
    ("detail", "Détail", 22),
    ("decision", "Décision", 16),
    ("note", "Note", 20),
]

RAPPROCHEMENT_HEADERS_FEMMES = [
    ("no", "N°", 4),
    ("sexe", "Sx", 4),
    ("categorie", "Catégorie suggestion", 26),
    ("score_pct", "Score", 8),
    ("grade_systeme", "Grade système", 16),
    ("nom_systeme", "Nom système MySQL", 32),
    ("matricule_systeme", "Mle système", 12),
    ("structure_systeme", "Struct. système", 10),
    ("ref_systeme", "Réf. syst.", 8),
    ("grade_alfa", "Grade ALFA", 16),
    ("nom_alfa", "Nom ALFA", 32),
    ("matricule_alfa", "Mle ALFA", 12),
    ("structure_alfa", "Struct. ALFA", 10),
    ("no_alfa", "N° ALFA", 8),
    ("deja_rapproche", "Déjà rapproché ?", 12),
    ("methode_match", "Méthode match", 12),
    ("detail", "Détail", 18),
    ("decision", "Décision", 14),
    ("note", "Note", 18),
]

RAPPROCHEMENT_HEADERS_TOTAL_FEMMES = [
    ("no", "N°", 4),
    ("grade_systeme", "Grade", 12),
    ("nom_systeme", "Nom et prénom(s)", 26),
    ("matricule_systeme", "Mle militaire", 11),
    ("position_admin", "P. admin.", 11),
    ("structure_systeme", "Struct.", 7),
    ("score_pct", "Score ALFA", 8),
]

RAPPROCHEMENT_HEADERS_TOTAL_ABSENT = RAPPROCHEMENT_HEADERS_TOTAL_FEMMES

RAPPROCHEMENT_HEADERS_SORTIS = [
    ("no", "N°", 4),
    ("motif_sortie", "Motif (décès / retraite)", 24),
    ("categorie", "Catégorie suggestion", 24),
    ("score_pct", "Score", 8),
    ("grade_systeme", "Grade système", 14),
    ("nom_systeme", "Nom système MySQL", 30),
    ("matricule_systeme", "Mle système", 12),
    ("structure_systeme", "Struct. syst.", 10),
    ("ref_systeme", "Réf. syst.", 8),
    ("position_admin", "Position admin.", 14),
    ("obs", "Observations", 18),
    ("grade_alfa", "Grade ALFA", 14),
    ("nom_alfa", "Nom ALFA", 30),
    ("matricule_alfa", "Mle ALFA", 12),
    ("structure_alfa", "Struct. ALFA", 10),
    ("no_alfa", "N° ALFA", 8),
    ("deja_rapproche", "Déjà rapproché ?", 12),
    ("methode_match", "Méthode match", 12),
    ("detail", "Détail", 16),
    ("decision", "Décision", 14),
    ("note", "Note", 16),
]


def _rapprochement_subtitle(rows: list[dict]) -> str:
    n_identique = sum(1 for r in rows if r["_cat_key"].startswith("nom_identique"))
    n_proche = sum(
        1 for r in rows
        if r["_cat_key"] in ("nom_proche", "nom_tres_proche", "nom_tres_proche_deja_pris")
    )
    n_aucune = sum(1 for r in rows if r["_cat_key"] == "aucune_piste")
    return (
        f"{len(rows)} agent(s) · {n_identique} nom identique · "
        f"{n_proche} nom proche · {n_aucune} sans piste · "
        f"colonnes Décision/Note à remplir · tri grade puis nom"
    )


def _write_rapprochement_table(
    ws,
    title: str,
    subtitle: str,
    headers: list[tuple[str, str, float]],
    rows: list[dict],
    start_row: int = 4,
    *,
    portrait: bool = False,
) -> None:
    ncol = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    t = ws.cell(1, 1, title)
    t.font, t.fill = FONT_TITLE, FILL_TITLE
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    s = ws.cell(2, 1, subtitle)
    s.font, s.fill = FONT_SUB, FILL_TITLE
    s.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 28

    keys = [h[0] for h in headers]
    input_keys = {"decision", "note"}
    wrap_keys = {
        "nom_alfa", "nom_systeme", "categorie", "detail", "decision", "note", "methode_match",
        "motif_sortie", "position_admin", "obs", "statut_alfa",
    }
    center_keys = {
        "no", "score_pct", "matricule_alfa", "matricule_systeme", "sexe",
        "structure_alfa", "structure_systeme", "ref_systeme", "no_alfa",
        "deja_rapproche", "methode_match", "position_admin",
    }

    for col, (_, label, width) in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
        c = ws.cell(start_row, col, label)
        _style(
            c, fill=FILL_HDR, font=FONT_HDR,
            align=Alignment(horizontal="center", vertical="center", wrap_text=True),
        )
    ws.row_dimensions[start_row].height = 36

    cat_fills = {
        "nom_identique_libre": FILL_SUGG_IDENT,
        "nom_identique_deja_pris": FILL_SUGG_IDENT,
        "nom_proche": FILL_SUGG_CLOSE,
        "nom_tres_proche": FILL_SUGG_CLOSE,
        "nom_tres_proche_deja_pris": FILL_SUGG_CLOSE,
        "aucune_piste": FILL_SUGG_NONE,
    }

    for i, row in enumerate(rows):
        r = start_row + 1 + i
        alt = i % 2 == 1
        ws.row_dimensions[r].height = 18
        cat_key = row.get("_cat_key", "")
        row_fill = cat_fills.get(cat_key, FILL_WHITE)
        for col, key in enumerate(keys, 1):
            val = row.get(key, "")
            c = ws.cell(r, col, val if val != "" else None)
            if key in input_keys:
                fill = FILL_INPUT
            elif key == "motif_sortie" and val:
                fill = FILL_SUGG_IDENT if str(val).startswith("Décès") else FILL_RETRAITE
            elif key == "categorie":
                fill = row_fill
            else:
                fill = FILL_ALT if alt else FILL_WHITE
            halign = "center" if key in center_keys else "left"
            _style(
                c,
                fill=fill,
                font=FONT_BODY_PRINT,
                align=Alignment(horizontal=halign, vertical="center", wrap_text=key in wrap_keys),
            )

    if rows:
        ws.freeze_panes = ws.cell(start_row + 1, 1)
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(ncol)}{start_row + len(rows)}"
    last_data_row = start_row + len(rows) if rows else start_row
    _apply_print_setup(
        ws, header_row=start_row, last_row=last_data_row, last_col=ncol, portrait=portrait,
    )


def _style(cell, *, fill=FILL_WHITE, font=FONT_BODY, align=None):
    cell.fill = fill
    cell.font = font
    cell.border = BORDER
    cell.alignment = align or Alignment(vertical="center", wrap_text=True)


def _apply_print_setup(
    ws, *, header_row: int, last_row: int, last_col: int, portrait: bool = False,
) -> None:
    """Mise en page A4, titres répétés, ajustée à 1 page de large."""
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "portrait" if portrait else "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    if portrait:
        ws.page_margins = PageMargins(
            left=0.5, right=0.5, top=0.55, bottom=0.5, header=0.2, footer=0.25,
        )
    else:
        ws.page_margins = PageMargins(
            left=0.35, right=0.35, top=0.45, bottom=0.45, header=0.2, footer=0.25,
        )
    ws.print_title_rows = f"{header_row}:{header_row}"
    if last_row >= header_row and last_col >= 1:
        ws.print_area = f"A1:{get_column_letter(last_col)}{last_row}"
    ws.oddFooter.center.text = "Page &P / &N"
    ws.oddFooter.right.text = "&D &T"


def _write_table(ws, title: str, subtitle: str, headers: list[tuple[str, str, float]], rows: list[dict], start_row: int = 4):
    ncol = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    t = ws.cell(1, 1, title)
    t.font, t.fill = FONT_TITLE, FILL_TITLE
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    s = ws.cell(2, 1, subtitle)
    s.font, s.fill = FONT_SUB, FILL_TITLE
    s.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 22
    keys = [h[0] for h in headers]
    for col, (_, label, width) in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
        c = ws.cell(start_row, col, label)
        _style(
            c, fill=FILL_HDR, font=FONT_HDR,
            align=Alignment(horizontal="center", vertical="center", wrap_text=True),
        )
    ws.row_dimensions[start_row].height = 32
    center_keys = {"no", "sexe", "legacy_id", "date_naissance", "date_grade", "date_service", "ndecision", "structure", "mois_sme"}
    for i, row in enumerate(rows):
        r = start_row + 1 + i
        alt = i % 2 == 1
        ws.row_dimensions[r].height = 17
        for col, key in enumerate(keys, 1):
            val = row.get(key, "")
            c = ws.cell(r, col, val if val != "" else None)
            halign = "center" if key in center_keys else "left"
            _style(
                c,
                fill=FILL_ALT if alt else FILL_WHITE,
                font=FONT_BODY_PRINT,
                align=Alignment(horizontal=halign, vertical="center", wrap_text=key in ("noms", "fonction", "obs", "noms_complet", "statut", "categorie")),
            )
    if rows:
        ws.freeze_panes = ws.cell(start_row + 1, 1)
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(ncol)}{start_row + len(rows)}"
    last_data_row = start_row + len(rows) if rows else start_row
    _apply_print_setup(ws, header_row=start_row, last_row=last_data_row, last_col=ncol)


def _agent_dict(i: int, ag: AgentRow, *, extra: dict | None = None) -> dict:
    d = {
        "no": i,
        "legacy_id": ag.legacy_id,
        "noms": ag.noms,
        "sexe": ag.sexe,
        "grade": ag.grade,
        "matricule": ag.matricule,
        "matricule_solde": ag.matricule_solde,
        "date_naissance": ag.date_naissance,
        "lieu_naissance": ag.lieu_naissance,
        "date_grade": ag.date_grade,
        "date_service": ag.date_service,
        "ndecision": ag.ndecision,
        "position_admin": ag.position_admin,
        "fonction": ag.fonction,
        "subdivision_arme": ag.subdivision_arme,
        "structure": ag.structure,
        "diplome": ag.diplome,
        "date_obtention": ag.date_obtention,
        "obs": ag.obs,
    }
    if extra:
        d.update(extra)
    return d


AGENT_HEADERS = [
    ("no", "N°", 4),
    ("legacy_id", "Réf.", 8),
    ("grade", "Grade", 18),
    ("noms", "Nom(s) et prénom(s)", 32),
    ("sexe", "Sx", 4),
    ("matricule", "Mle militaire", 14),
    ("matricule_solde", "Mle solde", 11),
    ("date_naissance", "Naiss.", 10),
    ("lieu_naissance", "Lieu naiss.", 14),
    ("date_grade", "Dt grade", 10),
    ("date_service", "Dt service", 10),
    ("ndecision", "N° déc.", 10),
    ("position_admin", "P. admin", 12),
    ("fonction", "Fonction", 22),
    ("subdivision_arme", "Subdivision", 18),
    ("structure", "Struct.", 10),
    ("diplome", "Diplôme", 12),
    ("obs", "Obs.", 16),
]


def export_workbook(
    *,
    out_path: Path,
    alfa_path: Path,
    mysql_rows: list[AgentRow],
    mysql_label: str,
    alfa_cmp: CompareBundle,
    sme_path: Path,
    sme_mois: str,
    sme_rows: list[AgentRow],
    sme_cmp: CompareBundle,
    grade_helper: GradeSortHelper,
) -> None:
    wb = Workbook()
    today = date.today().strftime("%d/%m/%Y")

    all_alfa_refs = [ref for _, ref, _ in alfa_cmp.matched] + list(alfa_cmp.absent_other)
    femmes_absent = _filter_femmes_absent_alfa(alfa_cmp.absent_ref)
    sortis_absent = _filter_deces_retraites_absent_alfa(alfa_cmp.absent_ref)
    femmes_rows_70 = _build_mysql_rapprochement_rows(
        femmes_absent,
        all_alfa_refs,
        alfa_cmp.matched,
        grade_helper,
        extra_from_agent=lambda ag: {
            "sexe": ag.sexe or "F",
            "position_admin": ag.position_admin,
        },
        suggestion_cutoff=ALFA_PRESENT_SCORE,
    )
    total_femmes_rows, n_femmes_exclues = _build_total_femmes_absent_rows(femmes_rows_70)
    absent_rows_70 = _build_mysql_rapprochement_rows(
        alfa_cmp.absent_ref,
        all_alfa_refs,
        alfa_cmp.matched,
        grade_helper,
        extra_from_agent=lambda ag: {"position_admin": ag.position_admin},
        suggestion_cutoff=ALFA_PRESENT_SCORE,
    )
    total_absent_rows, n_absent_exclues = _filter_total_absent_alfa_rows(absent_rows_70)

    ws0 = wb.active
    ws0.title = "Synthèse"
    ws0.column_dimensions["A"].width = 48
    ws0.column_dimensions["B"].width = 14
    ws0.column_dimensions["C"].width = 44
    stats = [
        ("DÉCALAGE LISTALFA vs SYSTÈME SME", "", ""),
        (f"Date : {today}", "", ""),
        (f"Fichier référence ALFA : {alfa_path.name}", "", ""),
        (f"Système : {mysql_label}", "", ""),
        (f"Dernier SME importé : {sme_path.name} ({sme_mois})", "", ""),
        ("Méthode rapprochement", "Nom uniquement", "Exact → tokens → similarité ≥ 92% (sans matricule)"),
        ("", "", ""),
        ("COMPARAISON ALFA vs MySQL", "Nombre", "Commentaire"),
        ("Effectif fichier ALFA", len(alfa_cmp.matched) + len(alfa_cmp.absent_other), ""),
        ("Effectif MySQL actif", len(mysql_rows), ""),
        ("Correspondances ALFA ↔ MySQL", len(alfa_cmp.matched), ""),
        ("En MySQL, ABSENTS du fichier ALFA", len(alfa_cmp.absent_ref), "Feuille « Absent ALFA »"),
        (
            "MySQL absent — noms proches ALFA",
            len(alfa_cmp.absent_ref),
            "Feuille « MySQL rapprochement » (décision manuelle)",
        ),
        (
            "Femmes système — absent ALFA",
            len(femmes_absent),
            "Feuille « Femmes absent ALFA »",
        ),
        (
            "TOTAL femmes absent ALFA (score ≤ 70 %)",
            len(total_femmes_rows),
            (
                f"Feuille « TOTAL Femmes absent ALFA » · "
                f"{n_femmes_exclues} exclue(s) (score > 70 % = présentes ALFA)"
            ),
        ),
        (
            "TOTAL absent ALFA (score ≤ 70 %)",
            len(total_absent_rows),
            (
                f"Feuille « TOTAL Absent ALFA » · "
                f"{n_absent_exclues} exclu(s) (score > 70 % = présents ALFA)"
            ),
        ),
        (
            "Décès & retraites — absent ALFA",
            len(sortis_absent),
            "Feuille « Décès retraites absent ALFA »",
        ),
        ("Dans ALFA, ABSENTS de MySQL", len(alfa_cmp.absent_other), "Feuille « Absent MySQL »"),
        (
            "ALFA absent — noms proches système",
            len(alfa_cmp.absent_other),
            "Feuille « ALFA rapprochement » (décision manuelle)",
        ),
        ("", "", ""),
        (f"COMPARAISON SME {sme_mois} vs MySQL", "Nombre", ""),
        ("Effectif fichier SME", len(sme_rows), ""),
        ("Correspondances SME ↔ MySQL", len(sme_cmp.matched), ""),
        ("En MySQL, absents du dernier SME", len(sme_cmp.absent_ref), "Feuille « Comparatif SME »"),
        ("Dans SME, absents de MySQL", len(sme_cmp.absent_other), "Feuille « Comparatif SME »"),
    ]
    for i, (a, b, c) in enumerate(stats, 1):
        ws0.cell(i, 1, a).font = FONT_BOLD if i in (1, 8, 21) else FONT_BODY
        ws0.cell(i, 2, b if b != "" else None)
        ws0.cell(i, 3, c)

    ws1 = wb.create_sheet("Absent ALFA")
    absent_alfa = _sort_agents(alfa_cmp.absent_ref, grade_helper)
    rows1 = [_agent_dict(i + 1, ag, extra={"statut": "MySQL — absent ALFA"}) for i, ag in enumerate(absent_alfa)]
    hdr1 = [("statut", "Statut", 16)] + list(AGENT_HEADERS)
    _write_table(
        ws1,
        "PERSONNEL SYSTÈME ABSENT DU FICHIER ALFA",
        f"{len(rows1)} agent(s) — tri : grade (préséance) puis nom · impression A4 paysage",
        hdr1, rows1,
    )

    ws1b = wb.create_sheet("MySQL rapprochement")
    mysql_rappro_rows = _build_mysql_rapprochement_rows(
        alfa_cmp.absent_ref, all_alfa_refs, alfa_cmp.matched, grade_helper,
    )
    _write_rapprochement_table(
        ws1b,
        "MYSQL ABSENT DU FICHIER ALFA — RAPPROCHEMENT PAR NOM",
        _rapprochement_subtitle(mysql_rappro_rows),
        RAPPROCHEMENT_HEADERS_MYSQL,
        mysql_rappro_rows,
    )

    ws1c = wb.create_sheet("Femmes absent ALFA")
    femmes_rows = _build_mysql_rapprochement_rows(
        femmes_absent,
        all_alfa_refs,
        alfa_cmp.matched,
        grade_helper,
        extra_from_agent=lambda ag: {"sexe": ag.sexe or "F"},
    )
    _write_rapprochement_table(
        ws1c,
        "PERSONNEL FÉMININ SYSTÈME — ABSENT DU FICHIER ALFA",
        (
            f"{len(femmes_rows)} femme(s) en MySQL absentes d'ALFA · "
            f"{_rapprochement_subtitle(femmes_rows).split(' · ', 1)[-1]}"
        ),
        RAPPROCHEMENT_HEADERS_FEMMES,
        femmes_rows,
    )

    ws1e = wb.create_sheet("TOTAL Femmes absent ALFA")
    _write_rapprochement_table(
        ws1e,
        "TOTAL FEMMES SYSTÈME — ABSENTES DU FICHIER ALFA",
        (
            f"{len(total_femmes_rows)} femme(s) réellement absentes d'ALFA "
            f"(score ≤ 70 %) · {n_femmes_exclues} exclue(s) car présentes ALFA · "
            f"impression A4 portrait"
        ),
        RAPPROCHEMENT_HEADERS_TOTAL_FEMMES,
        total_femmes_rows,
        portrait=True,
    )

    ws1f = wb.create_sheet("TOTAL Absent ALFA")
    _write_rapprochement_table(
        ws1f,
        "TOTAL PERSONNEL SYSTÈME — ABSENT DU FICHIER ALFA",
        (
            f"{len(total_absent_rows)} agent(s) réellement absent(s) d'ALFA "
            f"(score ≤ 70 %) · {n_absent_exclues} exclu(s) car présents ALFA · "
            f"impression A4 portrait"
        ),
        RAPPROCHEMENT_HEADERS_TOTAL_ABSENT,
        total_absent_rows,
        portrait=True,
    )

    ws1d = wb.create_sheet("Décès retraites absent ALFA")
    sortis_rows = _build_mysql_rapprochement_rows(
        sortis_absent,
        all_alfa_refs,
        alfa_cmp.matched,
        grade_helper,
        extra_from_agent=lambda ag: {
            "motif_sortie": _motif_sortie_label(ag) or "",
            "position_admin": ag.position_admin,
            "obs": ag.obs,
        },
    )
    n_deces = sum(1 for r in sortis_rows if str(r.get("motif_sortie", "")).startswith("Décès"))
    n_retraites = len(sortis_rows) - n_deces
    if sortis_rows:
        sortis_sub = (
            f"{len(sortis_rows)} agent(s) · {n_deces} décès · {n_retraites} retraite(s) · "
            f"motif explicite · {_rapprochement_subtitle(sortis_rows).split(' · ', 1)[1]}"
        )
    else:
        sortis_sub = (
            "Aucun décès ni retraité absent d'ALFA dans le périmètre MySQL actuel "
            "(position admin. ou obs. DECES / RETRAITE) — feuille prête pour les prochains cas"
        )
    _write_rapprochement_table(
        ws1d,
        "DÉCÈS & RETRAITES SYSTÈME — ABSENTS DU FICHIER ALFA",
        sortis_sub,
        RAPPROCHEMENT_HEADERS_SORTIS,
        sortis_rows,
    )

    ws2 = wb.create_sheet("Absent MySQL")
    absent_mysql = _sort_refs(alfa_cmp.absent_other, grade_helper)
    rows2 = [
        {
            "no": i + 1,
            "statut": "ALFA — absent MySQL",
            "grade": ref.grade,
            "noms_complet": ref.noms_complet,
            "matricule": ref.matricule,
            "date_naissance": ref.date_naissance,
            "date_service": ref.date_service,
            "fonction": ref.fonction,
            "structure": ref.structure,
        }
        for i, ref in enumerate(absent_mysql)
    ]
    hdr2 = [
        ("no", "N°", 4), ("statut", "Statut", 16), ("grade", "Grade", 18),
        ("noms_complet", "Nom complet ALFA", 36), ("matricule", "Mle militaire", 14),
        ("date_naissance", "Naiss.", 10), ("date_service", "Dt service", 10),
        ("fonction", "Fonction", 24), ("structure", "Struct.", 10),
    ]
    _write_table(
        ws2,
        "PERSONNEL ALFA ABSENT DU SYSTÈME",
        f"{len(rows2)} agent(s) — tri : grade puis nom · impression A4 paysage",
        hdr2, rows2,
    )

    ws2b = wb.create_sheet("ALFA rapprochement")
    rappro_rows = _build_alfa_rapprochement_rows(
        alfa_cmp.absent_other, mysql_rows, alfa_cmp.matched, grade_helper,
    )
    _write_rapprochement_table(
        ws2b,
        "ALFA ABSENT DU SYSTÈME — RAPPROCHEMENT PAR NOM",
        _rapprochement_subtitle(rappro_rows),
        RAPPROCHEMENT_HEADERS_ALFA,
        rappro_rows,
    )

    ws3 = wb.create_sheet("Comparatif SME")
    mysql_only = _sort_agents(sme_cmp.absent_ref, grade_helper)
    matched_sme_legs = {ref.legacy_id for _, ref, _ in sme_cmp.matched}
    sme_only_agents = _sort_agents(
        [ag for ag in sme_rows if ag.legacy_id not in matched_sme_legs],
        grade_helper,
    )
    rows3 = []
    n = 0
    for ag in mysql_only:
        n += 1
        rows3.append(_agent_dict(n, ag, extra={
            "categorie": "MySQL — absent dernier SME",
            "fichier_sme": sme_path.name,
            "mois_sme": sme_mois,
        }))
    for ag in sme_only_agents:
        n += 1
        rows3.append(_agent_dict(n, ag, extra={
            "categorie": "SME — absent MySQL",
            "fichier_sme": sme_path.name,
            "mois_sme": sme_mois,
        }))
    hdr3 = [
        ("categorie", "Catégorie écart", 24), ("mois_sme", "Mois SME", 10),
        ("fichier_sme", "Fichier SME", 36),
    ] + list(AGENT_HEADERS)
    _write_table(
        ws3,
        f"COMPARATIF DERNIER SME ({sme_mois}) vs MySQL",
        f"{len(mysql_only)} MySQL hors SME · {len(sme_only_agents)} SME hors MySQL — tri grade puis nom",
        hdr3, rows3,
    )

    # Synthèse : mise en page portrait
    ws0.page_setup.orientation = "portrait"
    ws0.page_setup.paperSize = ws0.PAPERSIZE_A4
    ws0.page_margins = PageMargins(left=0.5, right=0.5, top=0.6, bottom=0.6)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--alfa", type=Path, default=DEFAULT_ALFA)
    p.add_argument("--sme", type=Path, default=None)
    p.add_argument("--sortie", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    alfa_path = args.alfa.resolve()
    if not alfa_path.is_file():
        alt = _ROOT.parent / "COMPLET_LISTALFA_GENERALE_FICHER.xls"
        alfa_path = alt if alt.is_file() else alfa_path
    if not alfa_path.is_file():
        print(f"Fichier ALFA introuvable : {args.alfa}")
        return 1

    settings = get_settings()
    alfa_rows = load_alfa(alfa_path)
    mysql_rows, mysql_label = load_mysql(settings)
    alfa_cmp = compare_agents_to_refs(mysql_rows, alfa_rows)

    sme_path, sme_mois = _resolve_latest_sme_path(settings, args.sme)
    sme_rows = load_sme_mdb(sme_path)
    sme_cmp = compare_agent_lists(mysql_rows, sme_rows)
    matched_sme_legs = {ref.legacy_id for _, ref, _ in sme_cmp.matched}
    sme_only_count = sum(1 for ag in sme_rows if ag.legacy_id not in matched_sme_legs)
    grade_helper = _load_grade_helper(settings)

    export_workbook(
        out_path=args.sortie.resolve(),
        alfa_path=alfa_path,
        mysql_rows=mysql_rows,
        mysql_label=mysql_label,
        alfa_cmp=alfa_cmp,
        sme_path=sme_path,
        sme_mois=sme_mois,
        sme_rows=sme_rows,
        sme_cmp=sme_cmp,
        grade_helper=grade_helper,
    )

    print(f"ALFA : {alfa_path.name} ({len(alfa_rows)} agents)")
    print(f"MySQL actif : {len(mysql_rows)}")
    print(f"  Absents fichier ALFA : {len(alfa_cmp.absent_ref)}")
    mysql_rappro = _build_mysql_rapprochement_rows(
        alfa_cmp.absent_ref,
        [ref for _, ref, _ in alfa_cmp.matched] + list(alfa_cmp.absent_other),
        alfa_cmp.matched,
        grade_helper,
    )
    n_mysql_piste = sum(1 for r in mysql_rappro if r["_cat_key"] != "aucune_piste")
    print(f"  MySQL absent — avec piste nom ALFA : {n_mysql_piste}/{len(mysql_rappro)}")
    femmes_absent = _filter_femmes_absent_alfa(alfa_cmp.absent_ref)
    sortis_absent = _filter_deces_retraites_absent_alfa(alfa_cmp.absent_ref)
    print(f"  Femmes système absent ALFA : {len(femmes_absent)}")
    all_alfa = [ref for _, ref, _ in alfa_cmp.matched] + list(alfa_cmp.absent_other)
    femmes_70 = _build_mysql_rapprochement_rows(
        femmes_absent, all_alfa, alfa_cmp.matched, grade_helper,
        suggestion_cutoff=ALFA_PRESENT_SCORE,
    )
    total_f, excl_f = _build_total_femmes_absent_rows(femmes_70)
    print(
        f"  TOTAL femmes absent ALFA (score <= 70 %) : {len(total_f)} "
        f"({excl_f} exclues, score > 70 % = presentes ALFA)"
    )
    absent_70 = _build_mysql_rapprochement_rows(
        alfa_cmp.absent_ref, all_alfa, alfa_cmp.matched, grade_helper,
        extra_from_agent=lambda ag: {"position_admin": ag.position_admin},
        suggestion_cutoff=ALFA_PRESENT_SCORE,
    )
    total_a, excl_a = _filter_total_absent_alfa_rows(absent_70)
    print(
        f"  TOTAL absent ALFA (score <= 70 %) : {len(total_a)} "
        f"({excl_a} exclus, score > 70 % = presents ALFA)"
    )
    print(f"  Décès & retraites absent ALFA : {len(sortis_absent)}")
    print(f"  Absents MySQL (ALFA seul) : {len(alfa_cmp.absent_other)}")
    rappro = _build_alfa_rapprochement_rows(
        alfa_cmp.absent_other, mysql_rows, alfa_cmp.matched, grade_helper,
    )
    n_proche = sum(1 for r in rappro if r["_cat_key"] != "aucune_piste")
    print(f"  ALFA absent — avec piste nom système : {n_proche}/{len(rappro)}")
    print(f"SME {sme_mois} : {sme_path.name} ({len(sme_rows)} agents)")
    print(f"  MySQL absents SME : {len(sme_cmp.absent_ref)}")
    print(f"  SME absents MySQL : {sme_only_count}")
    print(f"Export : {args.sortie.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
