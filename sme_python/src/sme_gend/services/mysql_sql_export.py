"""
Export des bundles SME (SQLite ou archives Access copiées dans sme_archives/)
vers un script d'INSERT MySQL aligné sur data/schema/mysql_sme_cible.sql.

Utilisé par scripts/export_sqlite_bundle_to_mysql_sql.py.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from sme_gend.config.user_paths import resolved_sme_archives_directory
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm


def sql_str(value: str | None) -> str:
    if value is None:
        return "NULL"
    esc = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "''")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return "'" + esc + "'"


def sql_date(d: date | None) -> str:
    if d is None:
        return "NULL"
    return f"DATE '{d.isoformat()}'"


def sql_int(n: int | None) -> str:
    if n is None:
        return "NULL"
    return str(int(n))


def sql_bool(b: bool) -> str:
    return "1" if b else "0"


def norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def optional_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def parse_any_date(
    raw: Any,
    *,
    ctx: str,
    lot_note: str,
    errors: list[tuple[int | None, str, str, str]],
    import_lot_id: int | None,
) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    if s.isdigit() and len(s) in (5, 6, 7):
        try:
            n = int(s)
            if n > 2000:
                base = date(1899, 12, 30)
                return date.fromordinal(base.toordinal() + n)
        except ValueError:
            pass
    errors.append((import_lot_id, ctx, lot_note, f"date invalide: {s!r}"))
    return None


def last_day_of_month(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_cdsme_to_annee_mois(cdsme: str) -> tuple[int, int] | None:
    t = (cdsme or "").strip()
    if len(t) != 6 or not t.isdigit():
        return None
    mo, ye = int(t[:2]), int(t[2:])
    if ye < 100:
        ye += 2000
    if not (1 <= mo <= 12):
        return None
    return ye, mo


def infer_periode_from_archive_path(path: Path) -> str | None:
    """Déduit YYYY-MM du nom sme_bundle_<slug>_<stamp>.ext."""
    stem = path.stem
    if not stem.lower().startswith("sme_bundle_"):
        return None
    rest = stem[11:]
    rest = re.sub(r"_\d{8}T\d{6}Z$", "", rest, flags=re.IGNORECASE)
    if not rest:
        return None
    slug = rest.replace("_", "-")
    return canonical_sme_mois_yyyy_mm(slug) or canonical_sme_mois_yyyy_mm(rest)


def discover_bundle_archives(arch_dir: Path) -> list[Path]:
    arch_dir = arch_dir.expanduser().resolve()
    found: list[Path] = []
    for ext in ("sqlite", "db", "mdb", "accdb"):
        found.extend(arch_dir.glob(f"sme_bundle_*.{ext}"))
    return sorted(found, key=lambda p: (p.name.lower(), p.stat().st_mtime))


def archive_backend(path: Path) -> Literal["sqlite", "mdb"]:
    ext = path.suffix.lower()
    if ext in (".sqlite", ".db"):
        return "sqlite"
    return "mdb"


def _mdb_connect(path: Path) -> Any:
    import pyodbc

    drivers = (
        "Microsoft Access Driver (*.mdb, *.accdb)",
        "Microsoft Access Driver (*.mdb)",
    )
    p = str(path.resolve())
    for d in drivers:
        try:
            return pyodbc.connect(f"DRIVER={{{d}}};DBQ={p};")
        except pyodbc.Error:
            continue
    raise RuntimeError(
        "Impossible d'ouvrir la base Access (pilote ODBC introuvable). "
        "Installez le « Microsoft Access Database Engine » ou exportez l'archive en .sqlite."
    )


def db_query_dicts(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(sql, params)
    if not cur.description:
        return []
    names = [c[0] for c in cur.description]
    rows_out: list[dict[str, Any]] = []
    for row in cur.fetchall():
        if isinstance(row, sqlite3.Row):
            rows_out.append({names[i]: row[i] for i in range(len(names))})
        else:
            rows_out.append(dict(zip(names, tuple(row), strict=False)))
    return rows_out


def fetch_subdivision_flat(conn: Any, kind: Literal["sqlite", "mdb"]) -> list[SubdivisionFlatRow]:
    if kind == "sqlite":
        q = """
        SELECT u.code_u, u.secu_design, d.code_d, d.secd_design, t.code_t, t.sect_design
        FROM tab_secun u
        JOIN tab_secdeux d ON d.code_u = u.code_u
        JOIN tab_sectrois t ON t.code_d = d.code_d
        ORDER BY u.code_u, d.code_d, t.code_t
        """
    else:
        q = """
        SELECT u.CodeU, u.SecuDesign, d.CodeD, d.SecdDesign, t.CodeT, t.SectDesign
        FROM (TabSecun AS u
        INNER JOIN TabSecdeux AS d ON u.CodeU = d.CodeU)
        INNER JOIN TabSectrois AS t ON d.CodeD = t.CodeD
        ORDER BY u.CodeU, d.CodeD, t.CodeT
        """
    rows = db_query_dicts(conn, q)
    out: list[SubdivisionFlatRow] = []
    for r in rows:
        if kind == "sqlite":
            seq = (r.get("code_u"), r.get("secu_design"), r.get("code_d"), r.get("secd_design"), r.get("code_t"), r.get("sect_design"))
        else:
            seq = (r.get("CodeU"), r.get("SecuDesign"), r.get("CodeD"), r.get("SecdDesign"), r.get("CodeT"), r.get("SectDesign"))
        out.append(SubdivisionFlatRow.from_sequence(seq))
    return out


def tab_sme_hint_for_month(
    conn: Any, kind: Literal["sqlite", "mdb"], y: int, m: int
) -> tuple[str | None, str | None]:
    prefix = f"{y:04d}-{m:02d}"
    if kind == "sqlite":
        q = "SELECT codsme, mois, statut FROM tab_sme WHERE mois IS NOT NULL AND trim(mois) != ''"
        rows = db_query_dicts(conn, q)
        for r in rows:
            mois_s = str(r.get("mois") or "").strip()
            if mois_s.startswith(prefix):
                return (
                    str(r.get("codsme") or "").strip() or None,
                    str(r.get("statut") or "").strip() or None,
                )
    else:
        q = "SELECT Codsme, mois, Statut FROM TabSme WHERE mois IS NOT NULL"
        rows = db_query_dicts(conn, q)
        for r in rows:
            mois_s = str(r.get("mois") or "").strip()
            if mois_s.startswith(prefix):
                return (
                    str(r.get("Codsme") or "").strip() or None,
                    str(r.get("Statut") or "").strip() or None,
                )
    return None, None


def resolve_periode_for_archive(path: Path, kind: Literal["sqlite", "mdb"]) -> str | None:
    ym = infer_periode_from_archive_path(path)
    if ym:
        return ym
    try:
        if kind == "sqlite":
            conn = sqlite3.connect(str(path))
            try:
                rows = db_query_dicts(conn, "SELECT mois FROM tab_sme WHERE mois IS NOT NULL")
            finally:
                conn.close()
        else:
            conn = _mdb_connect(path)
            try:
                rows = db_query_dicts(conn, "SELECT mois FROM TabSme WHERE mois IS NOT NULL")
            finally:
                conn.close()
        for r in rows:
            v = r.get("mois")
            if v:
                ym2 = canonical_sme_mois_yyyy_mm(str(v))
                if ym2:
                    return ym2
    except Exception:
        return None
    return None


PADMIN_SEED: list[tuple[str, int, int]] = [
    ("Présent", 1, 1),
    ("Stage interne", 1, 2),
    ("Détaché", 0, 3),
    ("Congé maladie", 0, 4),
    ("Permission", 0, 5),
    ("Absent", 0, 6),
    ("En mission", 0, 7),
    ("Stage extérieur", 0, 8),
    ("Déserteur", 0, 9),
    ("Retraite", 0, 10),
    ("Suspendu", 0, 11),
]


@dataclass
class MergedCatalog:
    structures: dict[str, tuple[str | None, int | None]] = field(default_factory=dict)
    grades: dict[str, tuple[str | None, int | None, int | None]] = field(default_factory=dict)
    secun: dict[str, str | None] = field(default_factory=dict)
    secdeux: dict[tuple[str, str], tuple[str | None, float | None]] = field(default_factory=dict)
    sectrois: dict[tuple[str, str], tuple[str | None, float | None]] = field(default_factory=dict)
    # Clé (code_structure, code_u, code_d, code_t) observée sur les listes (subdivision ⊆ structure).
    subdivisions_arme: dict[tuple[str, str, str, str], tuple[str | None, int | None]] = field(
        default_factory=dict
    )
    padmin: dict[str, tuple[int, int | None]] = field(
        default_factory=lambda: {lb: (cp, ot) for lb, cp, ot in PADMIN_SEED}
    )
    fonctions: set[str] = field(default_factory=set)


SUBDIVISION_ROOT_NODE_ID = 1


@dataclass
class SubdivisionNoeudExportPlan:
    """INSERT ordonnés pour ref_subdivision_noeud + index feuille (U,D,T) → id."""

    insert_rows: list[tuple[int, int | None, str, str | None, float | None]]
    t_node_by_triple: dict[tuple[str, str, str], int]


def build_subdivision_noeud_export_plan(cat: MergedCatalog) -> SubdivisionNoeudExportPlan:
    """Construit l’arbre U→D→T à partir du catalogue fusionné (ex-TabSec*)."""
    rows: list[tuple[int, int | None, str, str | None, float | None]] = [
        (
            SUBDIVISION_ROOT_NODE_ID,
            None,
            "__root__",
            "Racine hiérarchique (interne, non affichée)",
            None,
        )
    ]
    next_id = 2
    u_node_id: dict[str, int] = {}
    for cu in sorted(cat.secun.keys()):
        u_node_id[cu] = next_id
        rows.append((next_id, SUBDIVISION_ROOT_NODE_ID, cu, cat.secun[cu], None))
        next_id += 1
    d_node_id: dict[tuple[str, str], int] = {}
    for (cu, cd) in sorted(cat.secdeux.keys()):
        d_node_id[(cu, cd)] = next_id
        des, pr = cat.secdeux[(cu, cd)]
        rows.append((next_id, u_node_id[cu], cd, des, pr))
        next_id += 1
    t_node_by_triple: dict[tuple[str, str, str], int] = {}
    for (cu, cd) in sorted(cat.secdeux.keys()):
        for (d2, ct) in sorted(cat.sectrois.keys()):
            if d2 != cd:
                continue
            cu_s, cd_s, ct_s = cu.strip(), cd.strip(), str(ct).strip()
            t_node_by_triple[(cu_s, cd_s, ct_s)] = next_id
            t_des, pr = cat.sectrois[(cd, ct)]
            rows.append((next_id, d_node_id[(cu, cd)], ct_s, t_des, pr))
            next_id += 1
    return SubdivisionNoeudExportPlan(insert_rows=rows, t_node_by_triple=t_node_by_triple)


def _subdivision_libelle_for_triple(
    cat: MergedCatalog,
    cu_s: str,
    cd_s: str,
    ct_s: str,
) -> str | None:
    t_des, _pr = cat.sectrois[(cd_s, ct_s)]
    t_des_s = (t_des or "").strip()
    if ct_s and t_des_s:
        return f"{ct_s} {t_des_s}".strip()
    if t_des_s:
        return t_des_s
    u_des = (cat.secun.get(cu_s) or "").strip()
    d_des, _ = cat.secdeux[(cu_s, cd_s)]
    d_des_s = (d_des or "").strip()
    return " — ".join(x for x in (u_des, d_des_s, ct_s) if x) or None


def resolve_structure_code(
    struct_text: str | None,
    conn: Any,
    kind: Literal["sqlite", "mdb"],
    structure_by_code: dict[str, int],
) -> str | None:
    """Retourne le ``code`` canonique présent dans ``structure_by_code``, ou None."""
    if not struct_text:
        return None
    s = str(struct_text).strip()
    if not s:
        return None
    if s in structure_by_code:
        return s
    if kind == "sqlite":
        q = "SELECT code FROM tab_structure WHERE designation = ? OR code = ? LIMIT 1"
    else:
        q = "SELECT code FROM TabStructure WHERE designation = ? OR code = ?"
    rows = db_query_dicts(conn, q, (s, s))
    if rows:
        c = str(rows[0].get("code") or "").strip()
        if c in structure_by_code:
            return c
    return None


def collect_subdivisions_arme_observed(
    jobs: list[tuple[Path, str, Literal["sqlite", "mdb"]]],
    cat: MergedCatalog,
    structure_by_code: dict[str, int],
    errors: list[tuple[int | None, str, str, str]],
) -> None:
    """
    Remplit ``subdivisions_arme`` à partir des listes : couples (structure, U–D–T) résolus.
    Une structure sans subdivision listée n'a aucune ligne ``ref_subdivision_arme``.
    """
    out: dict[tuple[str, str, str, str], tuple[str | None, int | None]] = {}
    for path, _ym, kind in jobs:
        conn = _open_conn(path, kind)
        try:
            flat = fetch_subdivision_flat(conn, kind)
            if kind == "sqlite":
                rows = db_query_dicts(conn, "SELECT structure, sub_arme FROM liste_generale")
            else:
                rows = db_query_dicts(
                    conn,
                    "SELECT Structure AS structure, SubArme AS sub_arme FROM ListeGenerale",
                )
            for r in rows:
                sub_raw = r.get("sub_arme")
                sub_s = str(sub_raw).strip() if sub_raw is not None and str(sub_raw).strip() else None
                if not sub_s:
                    continue
                st_raw = r.get("structure")
                st_s = str(st_raw).strip() if st_raw is not None and str(st_raw).strip() else None
                sc = resolve_structure_code(st_s, conn, kind, structure_by_code)
                if not sc:
                    continue
                triple = resolve_subdivision_arme_key(sub_s, flat)
                if not triple:
                    continue
                cu, cd, ct = triple
                if (cu, cd) not in cat.secdeux or (cd, ct) not in cat.sectrois:
                    continue
                key = (sc, cu, cd, ct)
                lib = _subdivision_libelle_for_triple(cat, cu, cd, ct)
                prev = out.get(key)
                if prev is None or (lib and not prev[0]):
                    out[key] = (lib, None)
        except Exception as e:
            errors.append((None, "subdivision_collect", path.name, str(e)))
        finally:
            conn.close()
    cat.subdivisions_arme = out


def _merge_sqlite_catalog(conn: sqlite3.Connection, cat: MergedCatalog) -> None:
    for r in db_query_dicts(conn, "SELECT code, designation, ordre_tri FROM tab_structure"):
        code = str(r.get("code") or "").strip()
        if code:
            cat.structures[code] = (
                str(r.get("designation")).strip() if r.get("designation") is not None else None,
                optional_int(r.get("ordre_tri")),
            )
    for r in db_query_dicts(conn, "SELECT code_g, grade, pref_ord, effectif_g FROM tab_grade"):
        cg = str(r.get("code_g") or "").strip()
        if cg:
            gr = r.get("grade")
            cat.grades[cg] = (
                str(gr).strip() if gr is not None else None,
                optional_int(r.get("pref_ord")),
                optional_int(r.get("effectif_g")),
            )
    for r in db_query_dicts(conn, "SELECT code_u, secu_design FROM tab_secun"):
        cu = str(r.get("code_u") or "").strip()
        if cu:
            sd = r.get("secu_design")
            cat.secun[cu] = str(sd).strip() if sd is not None else None
    for r in db_query_dicts(conn, "SELECT code_u, code_d, secd_design, pri_out_d FROM tab_secdeux"):
        cu, cd = str(r.get("code_u") or "").strip(), str(r.get("code_d") or "").strip()
        if cu and cd:
            des = r.get("secd_design")
            pr = r.get("pri_out_d")
            cat.secdeux[(cu, cd)] = (
                str(des).strip() if des is not None else None,
                float(pr) if pr is not None else None,
            )
    for r in db_query_dicts(conn, "SELECT code_d, code_t, sect_design, pri_out_t FROM tab_sectrois"):
        cd, ct = str(r.get("code_d") or "").strip(), str(r.get("code_t") or "").strip()
        if cd and ct:
            des = r.get("sect_design")
            pr = r.get("pri_out_t")
            cat.sectrois[(cd, ct)] = (
                str(des).strip() if des is not None else None,
                float(pr) if pr is not None else None,
            )
    try:
        for r in db_query_dicts(
            conn, "SELECT libelle, compte_comme_present, ordre_tri FROM tab_padmin"
        ):
            lb = str(r.get("libelle") or "").strip()
            if lb:
                cat.padmin[lb] = (int(r.get("compte_comme_present") or 0), optional_int(r.get("ordre_tri")))
    except sqlite3.DatabaseError:
        pass
    for r in db_query_dicts(
        conn,
        "SELECT DISTINCT fonction FROM liste_generale WHERE fonction IS NOT NULL AND trim(fonction) != ''",
    ):
        fn = r.get("fonction")
        if fn and str(fn).strip():
            cat.fonctions.add(str(fn).strip())


def _merge_mdb_catalog(conn: Any, cat: MergedCatalog) -> None:
    for r in db_query_dicts(conn, "SELECT code, designation, OderTri FROM TabStructure"):
        code = str(r.get("code") or "").strip()
        if code:
            des = r.get("designation")
            cat.structures[code] = (
                str(des).strip() if des is not None else None,
                optional_int(r.get("OderTri")),
            )
    for r in db_query_dicts(conn, "SELECT CodeG, Grade, PrefOrd, EffectifG FROM TabGrade"):
        cg = str(r.get("CodeG") or "").strip()
        if cg:
            gr = r.get("Grade")
            cat.grades[cg] = (
                str(gr).strip() if gr is not None else None,
                optional_int(r.get("PrefOrd")),
                optional_int(r.get("EffectifG")),
            )
    for r in db_query_dicts(conn, "SELECT CodeU, SecuDesign FROM TabSecun"):
        cu = str(r.get("CodeU") or "").strip()
        if cu:
            sd = r.get("SecuDesign")
            cat.secun[cu] = str(sd).strip() if sd is not None else None
    for r in db_query_dicts(conn, "SELECT CodeU, CodeD, SecdDesign, PriOutD FROM TabSecdeux"):
        cu, cd = str(r.get("CodeU") or "").strip(), str(r.get("CodeD") or "").strip()
        if cu and cd:
            des = r.get("SecdDesign")
            pr = r.get("PriOutD")
            cat.secdeux[(cu, cd)] = (
                str(des).strip() if des is not None else None,
                float(pr) if pr is not None else None,
            )
    for r in db_query_dicts(conn, "SELECT CodeD, CodeT, SectDesign, PriOutT FROM TabSectrois"):
        cd, ct = str(r.get("CodeD") or "").strip(), str(r.get("CodeT") or "").strip()
        if cd and ct:
            des = r.get("SectDesign")
            pr = r.get("PriOutT")
            cat.sectrois[(cd, ct)] = (
                str(des).strip() if des is not None else None,
                float(pr) if pr is not None else None,
            )
    try:
        for r in db_query_dicts(
            conn, "SELECT DISTINCT Padmin FROM ListeGenerale WHERE Padmin IS NOT NULL"
        ):
            lb = str(r.get("Padmin") or "").strip()
            if lb and lb not in cat.padmin:
                cat.padmin[lb] = (0, None)
    except Exception:
        pass
    for r in db_query_dicts(
        conn,
        "SELECT DISTINCT Fonction FROM ListeGenerale WHERE Fonction IS NOT NULL",
    ):
        fn = r.get("Fonction")
        if fn and str(fn).strip():
            cat.fonctions.add(str(fn).strip())


def merge_catalog_from_file(path: Path, kind: Literal["sqlite", "mdb"], cat: MergedCatalog) -> None:
    if kind == "sqlite":
        conn = sqlite3.connect(str(path))
        try:
            _merge_sqlite_catalog(conn, cat)
        finally:
            conn.close()
    else:
        conn = _mdb_connect(path)
        try:
            _merge_mdb_catalog(conn, cat)
        finally:
            conn.close()


def emit_ref_sql(lines: list[str], cat: MergedCatalog, ids: dict[str, Any]) -> None:
    structure_by_code: dict[str, int] = ids["structure_by_code"]
    grade_by_code: dict[str, int] = ids["grade_by_code"]
    padmin_by_libelle: dict[str, int] = ids["padmin_by_libelle"]
    fonction_by_libelle: dict[str, int] = ids["fonction_by_libelle"]

    buf = []
    for code in sorted(cat.structures.keys()):
        sid = structure_by_code[code]
        des, ot = cat.structures[code]
        buf.append(f"({sid},{sql_str(code)},{sql_str(des)},{sql_int(ot)})")
    if buf:
        lines.append("INSERT INTO ref_structure (id, code, designation, ordre_tri) VALUES")
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucune structure)")

    buf = []
    for cg in sorted(cat.grades.keys()):
        gid = grade_by_code[cg]
        lib, po, eff = cat.grades[cg]
        buf.append(f"({gid},{sql_str(cg)},{sql_str(lib)},{sql_int(po)},{sql_int(eff)})")
    if buf:
        lines.append("INSERT INTO ref_grade (id, code, libelle, pref_ord, effectif_g) VALUES")
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucun grade)")

    plan: SubdivisionNoeudExportPlan = ids["subdivision_noeud_plan"]
    buf = []
    for nid, pid, code, lib, pr in plan.insert_rows:
        buf.append(
            f"({nid},{sql_int(pid) if pid is not None else 'NULL'},{sql_str(code)},"
            f"{sql_str(lib)},{float(pr) if pr is not None else 'NULL'})"
        )
    if buf:
        lines.append("INSERT INTO ref_subdivision_noeud (id, parent_id, code, libelle, pri_out) VALUES")
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucun nœud subdivision)")

    subdivision_arme_by_key: dict[tuple[str, str, str, str], int] = ids["subdivision_arme_by_key"]
    buf = []
    for (sc, cu, cd, ct) in sorted(cat.subdivisions_arme.keys()):
        rid = subdivision_arme_by_key[(sc, cu, cd, ct)]
        stru_id = structure_by_code[sc]
        leaf_id = plan.t_node_by_triple[(cu, cd, ct)]
        lib, ot = cat.subdivisions_arme[(sc, cu, cd, ct)]
        buf.append(
            f"({rid},{stru_id},{leaf_id},{sql_str(lib)},{sql_int(ot)},{sql_bool(True)})"
        )
    if buf:
        lines.append(
            "INSERT INTO ref_subdivision_arme (id, structure_id, noeud_feuille_id, libelle, ordre_tri, actif) VALUES"
        )
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucune subdivision d'arme)")

    buf = []
    for lb in sorted(cat.padmin.keys()):
        pid = padmin_by_libelle[lb]
        cp, ot = cat.padmin[lb]
        buf.append(f"({pid},{sql_str(lb)},{sql_bool(bool(cp))},{sql_int(ot)})")
    if buf:
        lines.append(
            "INSERT INTO ref_position_admin (id, libelle, compte_comme_present, ordre_tri) VALUES"
        )
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucun padmin)")

    buf = []
    for fn in sorted(cat.fonctions):
        fid = fonction_by_libelle[fn]
        buf.append(f"({fid},{sql_str(fn)})")
    if buf:
        lines.append("INSERT INTO ref_fonction (id, libelle) VALUES")
        lines.append(",\n".join(buf) + ";")
    else:
        lines.append("-- (aucune fonction)")


def _strip_common_data_errors(s: str) -> str:
    tsc = " ".join(s.split()).strip()
    if re.match(r"YIE\s+ESCADRON", tsc, re.IGNORECASE):
        tsc = re.sub(r"^YIE\s+", "", tsc, count=1, flags=re.IGNORECASE).strip()
    return tsc


def _match_subdivision_row(
    raw: str | None,
    flat: Sequence[SubdivisionFlatRow],
) -> SubdivisionFlatRow | None:
    if raw is None:
        return None
    s = _strip_common_data_errors(" ".join(str(raw).split()))
    if not s or s == "(Sans subdivision)":
        return None
    if not flat:
        return None
    s_cf = s.casefold()

    for r in flat:
        dt = (r.design_t or "").strip()
        if dt and s_cf == dt.casefold():
            return r

    candidates: list[SubdivisionFlatRow] = []
    for r in flat:
        dt = (r.design_t or "").strip()
        if len(dt) >= 4 and s_cf.endswith(dt.casefold()):
            candidates.append(r)
    if candidates:
        return max(candidates, key=lambda x: len((x.design_t or "").strip()))

    best: SubdivisionFlatRow | None = None
    best_len = 0
    for r in flat:
        dt = (r.design_t or "").strip()
        if len(dt) >= 6 and dt.casefold() in s_cf and len(dt) > best_len:
            best = r
            best_len = len(dt)
    if best:
        return best

    best_ct: SubdivisionFlatRow | None = None
    best_ct_len = 0
    toks = s_cf.split()
    for r in flat:
        ct_raw = (r.code_t or "").strip()
        if not ct_raw:
            continue
        ctf = ct_raw.casefold()
        if s_cf == ctf or ctf in toks:
            if len(ct_raw) > best_ct_len:
                best_ct = r
                best_ct_len = len(ct_raw)
    if best_ct:
        return best_ct

    seen_d: dict[str, SubdivisionFlatRow] = {}
    seen_u: dict[str, SubdivisionFlatRow] = {}
    for r in flat:
        if r.code_d and r.code_d.strip().casefold() not in seen_d:
            seen_d[r.code_d.strip().casefold()] = r
        if r.code_u and r.code_u.strip().casefold() not in seen_u:
            seen_u[r.code_u.strip().casefold()] = r
    if s_cf in seen_d:
        return seen_d[s_cf]
    if s_cf in seen_u:
        return seen_u[s_cf]

    return None


def resolve_subdivision_arme_key(
    raw: str | None,
    flat: Sequence[SubdivisionFlatRow],
) -> tuple[str, str, str] | None:
    """Retourne (code_u, code_d, code_t) si la chaîne liste résout vers une ligne plate U–D–T."""
    r = _match_subdivision_row(raw, flat)
    if not r:
        return None
    cu, cd, ct = (r.code_u or "").strip(), (r.code_d or "").strip(), (r.code_t or "").strip()
    if not (cu and cd and ct):
        return None
    return (cu, cd, ct)


def parse_stage_interval(
    raw: str | None,
    *,
    person_key: str,
    errors: list[tuple[int | None, str, str, str]],
    import_lot_id: int | None,
) -> tuple[date | None, date | None]:
    if raw is None:
        return None, None
    s = " ".join(str(raw).split()).strip()
    if not s:
        return None, None
    for sep in [" au ", " – ", " — ", " - ", "à", " to "]:
        if sep.strip() and sep in s:
            a, b = s.split(sep, 1)
            d0 = parse_any_date(
                a.strip(), ctx=f"stage({person_key})", lot_note="debut", errors=errors, import_lot_id=import_lot_id
            )
            d1 = parse_any_date(
                b.strip(), ctx=f"stage({person_key})", lot_note="fin", errors=errors, import_lot_id=import_lot_id
            )
            return d0, d1
    m = re.match(
        r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*[-–—/]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})$",
        s,
    )
    if m:
        d0 = parse_any_date(
            m.group(1), ctx=f"stage({person_key})", lot_note="debut", errors=errors, import_lot_id=import_lot_id
        )
        d1 = parse_any_date(
            m.group(2), ctx=f"stage({person_key})", lot_note="fin", errors=errors, import_lot_id=import_lot_id
        )
        return d0, d1
    d0 = parse_any_date(s, ctx=f"stage({person_key})", lot_note="seul", errors=errors, import_lot_id=import_lot_id)
    return d0, None


def _open_conn(path: Path, kind: Literal["sqlite", "mdb"]) -> Any:
    if kind == "sqlite":
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    return _mdb_connect(path)


def absorb_personnes_from_archive(
    path: Path,
    kind: Literal["sqlite", "mdb"],
    personne_order: list[str],
    personne_data: dict[str, dict[str, Any]],
    errors: list[tuple[int | None, str, str, str]],
    import_lot_id: int | None,
) -> None:
    conn = _open_conn(path, kind)
    seen_in_file: set[str] = set()
    try:
        if kind == "sqlite":
            rows = db_query_dicts(conn, "SELECT id, id_acces, noms, date_n, lieu_n FROM liste_generale ORDER BY id")
            for r in rows:
                legacy = norm_id(r.get("id_acces")) or f"SQLITE:{r.get('id')}"
                noms, dn_raw, ln = r.get("noms"), r.get("date_n"), r.get("lieu_n")
                if legacy in seen_in_file:
                    errors.append(
                        (
                            import_lot_id,
                            "personne",
                            legacy,
                            "doublon legacy_access_id/id dans cette archive",
                        )
                    )
                    continue
                seen_in_file.add(legacy)
                dn = parse_any_date(
                    dn_raw,
                    ctx=f"liste_generale.date_n legacy={legacy}",
                    lot_note="naissance",
                    errors=errors,
                    import_lot_id=import_lot_id,
                )
                noms_s = str(noms) if noms is not None else None
                ln_s = str(ln).strip() if ln is not None and str(ln).strip() else None
                if legacy not in personne_data:
                    personne_order.append(legacy)
                    personne_data[legacy] = {"noms": noms_s, "dn": dn, "ln": ln_s}
                else:
                    ex = personne_data[legacy]
                    if ex["dn"] is None and dn is not None:
                        ex["dn"] = dn
                    if not ex.get("noms") and noms_s:
                        ex["noms"] = noms_s
                    if not ex.get("ln") and ln_s:
                        ex["ln"] = ln_s
        else:
            rows = db_query_dicts(
                conn,
                "SELECT Id, noms, DateN, LieuN FROM ListeGenerale ORDER BY OderTri, Id",
            )
            for r in rows:
                rid = r.get("Id")
                legacy = norm_id(rid)
                if not legacy:
                    continue
                if legacy in seen_in_file:
                    errors.append(
                        (
                            import_lot_id,
                            "personne",
                            legacy,
                            "doublon Id Access dans cette archive",
                        )
                    )
                    continue
                seen_in_file.add(legacy)
                noms, dn_raw, ln = r.get("noms"), r.get("DateN"), r.get("LieuN")
                dn = parse_any_date(
                    dn_raw,
                    ctx=f"ListeGenerale.DateN legacy={legacy}",
                    lot_note="naissance",
                    errors=errors,
                    import_lot_id=import_lot_id,
                )
                noms_s = str(noms) if noms is not None else None
                ln_s = str(ln).strip() if ln is not None and str(ln).strip() else None
                if legacy not in personne_data:
                    personne_order.append(legacy)
                    personne_data[legacy] = {"noms": noms_s, "dn": dn, "ln": ln_s}
                else:
                    ex = personne_data[legacy]
                    if ex["dn"] is None and dn is not None:
                        ex["dn"] = dn
                    if not ex.get("noms") and noms_s:
                        ex["noms"] = noms_s
                    if not ex.get("ln") and ln_s:
                        ex["ln"] = ln_s
    finally:
        conn.close()


def export_archives_directory(
    out_path: Path,
    *,
    arch_dir: Path | None = None,
    jobs_override: list[tuple[Path, str, Literal["sqlite", "mdb"]]] | None = None,
    database_name: str = "sme_gend_cible",
) -> None:
    """
    Fusionne toutes les archives ``sme_bundle_*`` du dossier (SQLite + Access)
    et produit un fichier SQL d'INSERT pour MySQL.

    Si ``jobs_override`` est fourni (ex. un seul fichier + période), la découverte
    automatique du dossier est ignorée.
    """
    errors: list[tuple[int | None, str, str, str]] = []
    if jobs_override is not None:
        jobs = [(p.expanduser().resolve(), ym, k) for p, ym, k in jobs_override]
        jobs.sort(key=lambda t: (t[1], t[0].stat().st_mtime))
        base = jobs[0][0].parent if jobs else Path.cwd()
    else:
        base = (arch_dir or resolved_sme_archives_directory()).expanduser().resolve()
        files = discover_bundle_archives(base)
        if not files:
            raise SystemExit(f"Aucune archive sme_bundle_* dans : {base}")

        jobs = []
        for p in files:
            kind = archive_backend(p)
            ym = resolve_periode_for_archive(p, kind)
            if not ym:
                errors.append((None, "archive", p.name, "période SME indéterminée (nom + TabSme) — ignorée"))
                continue
            jobs.append((p.resolve(), ym, kind))
        jobs.sort(key=lambda t: (t[1], t[0].stat().st_mtime))

    if not jobs:
        raise SystemExit("Aucune archive exploitable (période introuvable sur tous les fichiers).")

    cat = MergedCatalog()
    for path, _, kind in jobs:
        try:
            merge_catalog_from_file(path, kind, cat)
        except Exception as e:
            errors.append((None, "catalog", path.name, f"fusion référentiels : {e}"))

    structure_by_code = {c: i + 1 for i, c in enumerate(sorted(cat.structures.keys()))}
    collect_subdivisions_arme_observed(jobs, cat, structure_by_code, errors)

    subdivision_noeud_plan = build_subdivision_noeud_export_plan(cat)
    grade_by_code = {c: i + 1 for i, c in enumerate(sorted(cat.grades.keys()))}
    grade_by_libelle_cf: dict[str, int] = {}
    for cg, gid in grade_by_code.items():
        lib, _, _ = cat.grades[cg]
        if lib:
            grade_by_libelle_cf[lib.casefold()] = gid
    padmin_by_libelle = {lb: i + 1 for i, lb in enumerate(sorted(cat.padmin.keys()))}
    fonction_by_libelle = {fn: i + 1 for i, fn in enumerate(sorted(cat.fonctions))}
    subdivision_arme_by_key: dict[tuple[str, str, str, str], int] = {
        k: i + 1 for i, k in enumerate(sorted(cat.subdivisions_arme.keys()))
    }

    personne_order: list[str] = []
    personne_accum: dict[str, dict[str, Any]] = {}
    for idx, (path, ym, kind) in enumerate(jobs, start=1):
        absorb_personnes_from_archive(path, kind, personne_order, personne_accum, errors, idx)

    personne_by_legacy = {leg: i + 1 for i, leg in enumerate(personne_order)}

    unique_months = sorted({ym for _, ym, _ in jobs})
    sme_periode_id: dict[str, int] = {ym: i + 1 for i, ym in enumerate(unique_months)}

    db = (database_name or "sme_gend_cible").strip() or "sme_gend_cible"
    lines: list[str] = [
        f"-- Export multi-archives SME → MySQL ({db})",
        f"-- Dossier archives : {base}",
        f"-- Fichiers retenus : {len(jobs)}",
        "SET NAMES utf8mb4;",
        f"USE {db};",
        "SET FOREIGN_KEY_CHECKS = 0;",
    ]

    id_bundle: dict[str, Any] = {
        "structure_by_code": structure_by_code,
        "grade_by_code": grade_by_code,
        "grade_by_libelle_cf": grade_by_libelle_cf,
        "padmin_by_libelle": padmin_by_libelle,
        "fonction_by_libelle": fonction_by_libelle,
        "subdivision_noeud_plan": subdivision_noeud_plan,
        "subdivision_arme_by_key": subdivision_arme_by_key,
    }
    emit_ref_sql(lines, cat, id_bundle)

    periode_rows = []
    for ym in unique_months:
        yy, mm = int(ym[:4]), int(ym[5:7])
        fin = last_day_of_month(yy, mm)
        hint_path = next(p for p, y, _ in jobs if y == ym)
        conn0 = _open_conn(hint_path, archive_backend(hint_path))
        try:
            ch, st = tab_sme_hint_for_month(conn0, archive_backend(hint_path), yy, mm)
        finally:
            conn0.close()
        if not ch:
            ch = f"{mm:02d}{yy}"
        periode_rows.append(
            f"({sme_periode_id[ym]}, {sql_str(ym)}, {sql_date(fin)}, {sql_str(ch)}, {sql_str(st)})"
        )
    lines.append(
        "INSERT INTO sme_periode (id, annee_mois, date_dernier_jour_mois, codsme, statut) VALUES\n"
        + ",\n".join(periode_rows)
        + ";"
    )

    lot_lines = []
    for lot_id, (path, ym, _) in enumerate(jobs, start=1):
        lot_lines.append(
            f"({lot_id}, {sme_periode_id[ym]}, {sql_str(str(path.resolve()))}, NULL, CURRENT_TIMESTAMP)"
        )
    lines.append(
        "INSERT INTO import_lot (id, sme_periode_id, chemin_fichier, checksum, importe_le) VALUES\n"
        + ",\n".join(lot_lines)
        + ";"
    )

    pbuf = []
    for legacy in sorted(personne_by_legacy.keys(), key=lambda k: personne_by_legacy[k]):
        pid = personne_by_legacy[legacy]
        info = personne_accum.get(legacy, {})
        pbuf.append(
            f"({pid},{sql_str(legacy)},{sql_str(info.get('noms'))},"
            f" NULL, {sql_date(info.get('dn'))}, {sql_str(info.get('ln'))}, CURRENT_TIMESTAMP)"
        )
    if pbuf:
        lines.append(
            "INSERT INTO personne (id, legacy_access_id, nom_affichage, prenoms, date_naissance, "
            "lieu_naissance, cree_le) VALUES"
        )
        lines.append(",\n".join(pbuf) + ";")
    else:
        lines.append("-- (aucune personne)")

    cid = 1
    sid = 1
    mid = 1
    eid = 1
    did = 1
    mid2 = 1
    pdid = 1
    carriere_chunks: list[str] = []
    diplome_chunks: list[str] = []

    def resolve_grade_id(grade_text: str | None) -> int | None:
        if not grade_text:
            return None
        g = str(grade_text).strip()
        if not g:
            return None
        if g in grade_by_code:
            return grade_by_code[g]
        return grade_by_libelle_cf.get(g.casefold())

    def resolve_structure_id(struct_text: str | None, conn: Any, kind: Literal["sqlite", "mdb"]) -> int | None:
        c = resolve_structure_code(struct_text, conn, kind, structure_by_code)
        return structure_by_code.get(c) if c else None

    def person_id_for_satellite(row_id: str) -> int | None:
        k = norm_id(row_id)
        if not k:
            return None
        if k in personne_by_legacy:
            return personne_by_legacy[k]
        return personne_by_legacy.get(f"SQLITE:{k}")

    for lot_id, (path, ym, kind) in enumerate(jobs, start=1):
        yy, mm = int(ym[:4]), int(ym[5:7])
        debut = date(yy, mm, 1)
        fin = last_day_of_month(yy, mm)
        conn = _open_conn(path, kind)
        try:
            flat = fetch_subdivision_flat(conn, kind)
            if kind == "sqlite":
                rows_lg = db_query_dicts(conn, "SELECT * FROM liste_generale ORDER BY id")
                cols = {k for r in rows_lg for k in r} if rows_lg else set()
            else:
                rows_lg = db_query_dicts(
                    conn,
                    """
                    SELECT Id AS id, Id AS id_acces, noms, DateN AS date_n, LieuN AS lieu_n, Grade AS grade,
                    DateGrade AS date_grade, DateService AS date_service, Ndecision AS ndecision,
                    Matmilitaire AS matmilitaire, Matsolde AS matsolde, Padmin AS padmin, Fonction AS fonction,
                    SubArme AS sub_arme, Diplome AS diplome, DateObtention AS date_obtention,
                    Structure AS structure, Obs AS obs, OderTri AS ordre_tri, presseance AS presseance
                    FROM ListeGenerale ORDER BY OderTri, Id
                    """,
                )
                cols = {k for r in rows_lg for k in r} if rows_lg else set()

            for r in rows_lg:
                id_acces = norm_id(r.get("id_acces")) if "id_acces" in cols else ""
                legacy = id_acces if id_acces else f"SQLITE:{r.get('id')}"
                pid = personne_by_legacy.get(legacy)
                if pid is None:
                    continue
                grade_col = r.get("grade") if "grade" in cols else None

                def txt(col: str) -> str | None:
                    if col not in r:
                        return None
                    v = r[col]
                    if v is None:
                        return None
                    st = str(v).strip()
                    return st or None

                parse_any_date(
                    r.get("date_grade") if "date_grade" in cols else None,
                    ctx=f"date_grade legacy={legacy}",
                    lot_note="grade",
                    errors=errors,
                    import_lot_id=lot_id,
                )
                sub_raw = txt("sub_arme")
                struct_txt = txt("structure")
                sc = resolve_structure_code(struct_txt, conn, kind, structure_by_code)
                sub_triple = resolve_subdivision_arme_key(sub_raw, flat)
                sub_arme_id = (
                    subdivision_arme_by_key.get((sc, *sub_triple))
                    if sc and sub_triple
                    else None
                )
                fn_id = None
                fn = txt("fonction")
                if fn:
                    fn_id = fonction_by_libelle.get(fn)
                d_obt = parse_any_date(
                    r.get("date_obtention") if "date_obtention" in cols else None,
                    ctx=f"date_obtention legacy={legacy}",
                    lot_note="diplome",
                    errors=errors,
                    import_lot_id=lot_id,
                )
                pa = txt("padmin")
                carriere_chunks.append(
                    "("
                    f"{cid},{pid},{sql_date(debut)},{sql_date(fin)},"
                    f"{sql_int(resolve_structure_id(txt('structure'), conn, kind))},"
                    f"{sql_int(resolve_grade_id(grade_col))},"
                    f"{sql_int(padmin_by_libelle.get(pa) if pa else None)},"
                    f"{sql_int(sub_arme_id)},{sql_str(sub_raw)},"
                    f"{sql_int(fn_id)},"
                    f"{sql_str(txt('matmilitaire'))},"
                    f"{sql_str(txt('matsolde'))},"
                    f"{sql_str(txt('ndecision'))},"
                    f"{sql_str(txt('diplome'))},"
                    f"{sql_date(d_obt)},"
                    f"{sql_int(optional_int(r.get('ordre_tri') if 'ordre_tri' in cols else None))},"
                    f"{sql_int(optional_int(r.get('presseance') if 'presseance' in cols else None))},"
                    f"{sql_str(txt('obs'))},"
                    f"{lot_id}, {sme_periode_id[ym]})"
                )
                cid += 1

                dip_txt = txt("diplome")
                if dip_txt or d_obt is not None:
                    diplome_chunks.append(
                        "("
                        f"{pdid},{pid},NULL,"
                        f"{sql_str(dip_txt) if dip_txt else 'NULL'},"
                        f"{sql_date(d_obt)},NULL,NULL,NULL,NULL,NULL,"
                        f"{lot_id})"
                    )
                    pdid += 1

            stage_sql = (
                ("tab_stage_local", "LOCAL"),
                ("tab_stage_ex", "EXTERIEUR"),
            )
            if kind == "mdb":
                stage_sql = (("TabStageLocal", "LOCAL"), ("TabStageEx", "EXTERIEUR"))
            st_chunks: list[str] = []
            for table, tcode in stage_sql:
                if kind == "sqlite":
                    q = f"SELECT id, nature_stage, lieu_stage, date_debut_fin FROM {table}"
                else:
                    q = f"SELECT ID AS id, Naturestage AS nature_stage, LieuStage AS lieu_stage, DateDebutFin AS date_debut_fin FROM [{table}]"
                for row in db_query_dicts(conn, q):
                    sat_id = norm_id(row.get("id"))
                    prs = person_id_for_satellite(sat_id)
                    if prs is None:
                        errors.append((lot_id, table, sat_id, "personne inconnue pour stage"))
                        continue
                    d0, d1 = parse_stage_interval(
                        row.get("date_debut_fin"),
                        person_key=sat_id,
                        errors=errors,
                        import_lot_id=lot_id,
                    )
                    st_chunks.append(
                        "("
                        f"{sid},{prs},{sql_str(tcode)},{sql_date(d0)},{sql_date(d1)},"
                        f"{sql_str(str(row.get('lieu_stage')) if row.get('lieu_stage') else None)},"
                        f"{sql_str(str(row.get('nature_stage')) if row.get('nature_stage') else None)},"
                        f" NULL, {sql_str(table.upper())}, NULL, {lot_id})"
                    )
                    sid += 1
            if st_chunks:
                lines.append(
                    "INSERT INTO parcours_stage (id, personne_id, type_stage_code, date_debut, date_fin, "
                    "lieu, nature_libelle, reference_dossier, source_ligne_legacy, observation, import_lot_id) VALUES "
                    + ",\n".join(st_chunks)
                    + ";"
                )

            if kind == "sqlite":
                mq = "SELECT id, nature, lieu_mission, refnds, date_depart FROM tab_mission"
            else:
                mq = "SELECT ID AS id, Nature AS nature, LieuMission AS lieu_mission, Refnds AS refnds, DateDepart AS date_depart FROM TabMission"
            mbuf = []
            for row in db_query_dicts(conn, mq):
                sat_id = norm_id(row.get("id"))
                prs = person_id_for_satellite(sat_id)
                if prs is None:
                    errors.append((lot_id, "tab_mission", sat_id, "personne inconnue pour mission"))
                    continue
                dd = parse_any_date(
                    row.get("date_depart"),
                    ctx=f"mission({sat_id})",
                    lot_note="depart",
                    errors=errors,
                    import_lot_id=lot_id,
                )
                mbuf.append(
                    "("
                    f"{mid},{prs},{sql_str(str(row.get('nature')) if row.get('nature') else None)},"
                    f"{sql_str(str(row.get('lieu_mission')) if row.get('lieu_mission') else None)},"
                    f"{sql_str(str(row.get('refnds')) if row.get('refnds') else None)},"
                    f"{sql_date(dd)}, {lot_id})"
                )
                mid += 1
            if mbuf:
                lines.append(
                    "INSERT INTO mission_operationnelle (id, personne_id, nature, lieu_mission, refnds, "
                    "date_depart, import_lot_id) VALUES " + ",\n".join(mbuf) + ";"
                )

            if kind == "sqlite":
                lq = "SELECT id, date_constat, pathologie, obs FROM tab_malade"
            else:
                lq = "SELECT ID AS id, DateConstat AS date_constat, Pathologie AS pathologie, Obs AS obs FROM TabMalade"
            lbuf = []
            for row in db_query_dicts(conn, lq):
                sat_id = norm_id(row.get("id"))
                prs = person_id_for_satellite(sat_id)
                if prs is None:
                    errors.append((lot_id, "tab_malade", sat_id, "personne inconnue pour malade"))
                    continue
                dc = parse_any_date(
                    row.get("date_constat"),
                    ctx=f"malade({sat_id})",
                    lot_note="constat",
                    errors=errors,
                    import_lot_id=lot_id,
                )
                lbuf.append(
                    f"({eid},{prs},{sql_date(dc)},"
                    f"{sql_str(str(row.get('pathologie')) if row.get('pathologie') else None)},"
                    f"{sql_str(str(row.get('obs')) if row.get('obs') else None)}, {lot_id})"
                )
                eid += 1
            if lbuf:
                lines.append(
                    "INSERT INTO evenement_malade (id, personne_id, date_constat, pathologie, obs, "
                    "import_lot_id) VALUES " + ",\n".join(lbuf) + ";"
                )

            if kind == "sqlite":
                dq = "SELECT id, dat_desertion FROM tab_deserteur"
            else:
                dq = "SELECT ID AS id, DatDesertion AS dat_desertion FROM TabDeserteur"
            dbuf = []
            for row in db_query_dicts(conn, dq):
                sat_id = norm_id(row.get("id"))
                prs = person_id_for_satellite(sat_id)
                if prs is None:
                    errors.append((lot_id, "tab_deserteur", sat_id, "personne inconnue pour deserteur"))
                    continue
                dd = parse_any_date(
                    row.get("dat_desertion"),
                    ctx=f"deserteur({sat_id})",
                    lot_note="desertion",
                    errors=errors,
                    import_lot_id=lot_id,
                )
                dbuf.append(f"({did},{prs},{sql_date(dd)}, {lot_id})")
                did += 1
            if dbuf:
                lines.append(
                    "INSERT INTO evenement_deserteur (id, personne_id, date_desertion, import_lot_id) VALUES "
                    + ",\n".join(dbuf)
                    + ";"
                )

            target_cdsme = f"{mm:02d}{yy}"
            if kind == "sqlite":
                gq = "SELECT id, grade, nom, objet, cdsme, observ FROM tab_gain_perte ORDER BY rowid"
            else:
                gq = "SELECT ID AS id, Grade AS grade, nom AS nom, Objet AS objet, Cdsme AS cdsme, Observ AS observ FROM TabGainPerte"
            gbuf = []
            for row in db_query_dicts(conn, gq):
                cdsme = str(row.get("cdsme") or "").strip()
                if cdsme and cdsme != target_cdsme:
                    parsed = parse_cdsme_to_annee_mois(cdsme)
                    if parsed and (parsed[0], parsed[1]) != (yy, mm):
                        continue
                sat_id = norm_id(row.get("id"))
                prs = person_id_for_satellite(sat_id)
                if prs is None:
                    errors.append((lot_id, "tab_gain_perte", sat_id, "personne inconnue pour gain_perte"))
                    continue
                obj = str(row.get("objet") or "").strip().upper()
                mv = "GAIN" if obj == "GAIN" else ("PERTE" if obj == "PERTE" else obj or "INCONNU")
                ident_ligne = f"{sat_id}|{cdsme or target_cdsme}"
                gbuf.append(
                    "("
                    f"{mid2}, {sme_periode_id[ym]}, {prs}, "
                    f"{sql_str(ident_ligne)},"
                    f"{sql_str(str(row.get('grade')) if row.get('grade') else None)},"
                    f"{sql_str(str(row.get('nom')) if row.get('nom') else None)},"
                    f"{sql_str(mv)},{sql_str(cdsme or target_cdsme)},"
                    f"{sql_str(str(row.get('observ')) if row.get('observ') else None)},"
                    f"{sql_date(fin)}, {lot_id})"
                )
                mid2 += 1
            if gbuf:
                lines.append(
                    "INSERT INTO mouvement_effectif (id, sme_periode_id, personne_id, identifiant_ligne, "
                    "grade_snapshot, nom_snapshot, type_mouvement, cdsme_derive, observ, date_reference, "
                    "import_lot_id) VALUES " + ",\n".join(gbuf) + ";"
                )
        finally:
            conn.close()

    if carriere_chunks:
        lines.append(
            "INSERT INTO carriere_situation (id, personne_id, date_debut, date_fin, structure_id, grade_id, "
            "position_admin_id, subdivision_arme_id, subdivision_libelle_legacy, fonction_id, "
            "matricule_militaire, matricule_solde, ndecision, diplome_libelle, date_diplome_obtenu, "
            "ordre_tri, presseance, obs, import_lot_id, sme_periode_reference_id) VALUES"
        )
        lines.append(",\n".join(carriere_chunks) + ";")
    else:
        lines.append("-- (pas de carrière)")

    if diplome_chunks:
        lines.append(
            "INSERT INTO personne_diplome (id, personne_id, ref_diplome_id, libelle_libre, date_obtention, "
            "lieu_obtention, mention, numero_dossier, etablissement, obs, import_lot_id) VALUES"
        )
        lines.append(",\n".join(diplome_chunks) + ";")

    if errors:
        ebuf = []
        for i, (lot, tbl, k, msg) in enumerate(errors, start=1):
            lid = lot if lot is not None else 1
            ebuf.append(f"({i}, {lid}, {sql_str(tbl)}, {sql_str(k)}, {sql_str(msg)})")
        lines.append(
            "INSERT INTO etl_erreur (id, import_lot_id, table_cible, cle_legacy, message) VALUES "
            + ",\n".join(ebuf)
            + ";"
        )

    lines.append("SET FOREIGN_KEY_CHECKS = 1;")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Écrit : {out_path} ({len(jobs)} archive(s), {len(errors)} ligne(s) ETL)")
