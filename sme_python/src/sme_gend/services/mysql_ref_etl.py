"""ETL : synchronisation tab_* → ref_* et résolution des FK carrière."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow


def _cf(s: str | None) -> str:
    return (s or "").strip().casefold()


def _norm_code(s: str | None) -> str:
    return (s or "").strip()


def cdsme_to_annee_mois(cdsme: str | None) -> str | None:
    """``MMYYYY`` Access → ``YYYY-MM``."""
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    direct = canonical_sme_mois_yyyy_mm((cdsme or "").strip())
    if direct:
        return direct
    digits = re.sub(r"\D", "", (cdsme or "").strip())
    if len(digits) != 6:
        return None
    mm, yyyy = int(digits[:2]), int(digits[2:])
    if 1 <= mm <= 12 and 2000 <= yyyy <= 2100:
        return f"{yyyy:04d}-{mm:02d}"
    return None


@dataclass
class RefEtlCache:
    """Caches en mémoire après sync tab → ref."""

    structure_by_code: dict[str, int] = field(default_factory=dict)
    structure_code_by_id: dict[int, str] = field(default_factory=dict)
    grade_by_code: dict[str, int] = field(default_factory=dict)
    grade_by_libelle: dict[str, int] = field(default_factory=dict)
    grade_libelle_by_id: dict[int, str] = field(default_factory=dict)
    padmin_by_libelle: dict[str, int] = field(default_factory=dict)
    padmin_libelle_by_id: dict[int, str] = field(default_factory=dict)
    fonction_by_libelle: dict[str, int] = field(default_factory=dict)
    fonction_libelle_by_id: dict[int, str] = field(default_factory=dict)
    leaf_node_by_design_t: dict[str, int] = field(default_factory=dict)
    leaf_node_by_flat_key: dict[str, int] = field(default_factory=dict)
    subdiv_arme_by_struct_leaf: dict[tuple[int, int], int] = field(default_factory=dict)
    subdiv_arme_libelle_by_id: dict[int, str] = field(default_factory=dict)
    periode_by_ym: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    misc_node_ids: dict[str, int] = field(default_factory=dict)


def _ensure_subdiv_root(cur: Any) -> None:
    cur.execute(
        """
        INSERT INTO ref_subdivision_noeud (id, parent_id, code, libelle)
        SELECT 1, NULL, 'ROOT', 'Racine'
        FROM DUAL
        WHERE NOT EXISTS (SELECT 1 FROM ref_subdivision_noeud WHERE id = 1)
        """
    )


def _upsert_ref_structure(cur: Any, code: str, designation: str | None, ordre_tri: int | None) -> int:
    c = _norm_code(code)
    if not c:
        raise ValueError("code structure vide")
    cur.execute(
        """
        INSERT INTO ref_structure (code, designation, ordre_tri)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          designation = COALESCE(VALUES(designation), designation),
          ordre_tri = COALESCE(VALUES(ordre_tri), ordre_tri)
        """,
        (c, designation, ordre_tri),
    )
    cur.execute("SELECT id FROM ref_structure WHERE code = %s", (c,))
    row = cur.fetchone()
    return int(row[0])


def _upsert_ref_grade(
    cur: Any, code: str, libelle: str | None, pref_ord: int | None, effectif_g: int | None
) -> int:
    c = _norm_code(code)
    if not c:
        raise ValueError("code grade vide")
    cur.execute(
        """
        INSERT INTO ref_grade (code, libelle, pref_ord, effectif_g)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          libelle = COALESCE(VALUES(libelle), libelle),
          pref_ord = COALESCE(VALUES(pref_ord), pref_ord),
          effectif_g = COALESCE(VALUES(effectif_g), effectif_g)
        """,
        (c, libelle, pref_ord, effectif_g),
    )
    cur.execute("SELECT id FROM ref_grade WHERE code = %s", (c,))
    return int(cur.fetchone()[0])


def _upsert_ref_padmin(
    cur: Any, libelle: str, compte_present: int, ordre_tri: int | None
) -> int:
    lb = libelle.strip()
    cur.execute(
        """
        INSERT INTO ref_position_admin (libelle, compte_comme_present, ordre_tri)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          compte_comme_present = compte_comme_present,
          ordre_tri = COALESCE(VALUES(ordre_tri), ordre_tri)
        """,
        (lb, compte_present, ordre_tri),
    )
    cur.execute("SELECT id FROM ref_position_admin WHERE libelle = %s", (lb,))
    return int(cur.fetchone()[0])


def _upsert_ref_fonction(cur: Any, libelle: str) -> int:
    lb = libelle.strip()
    cur.execute(
        """
        INSERT INTO ref_fonction (libelle) VALUES (%s)
        ON DUPLICATE KEY UPDATE libelle = libelle
        """,
        (lb,),
    )
    cur.execute("SELECT id FROM ref_fonction WHERE libelle = %s", (lb,))
    return int(cur.fetchone()[0])


def _upsert_subdiv_node(
    cur: Any, *, parent_id: int | None, code: str, libelle: str | None, pri_out: float | None
) -> int:
    c = _norm_code(code)
    cur.execute(
        """
        INSERT INTO ref_subdivision_noeud (parent_id, code, libelle, pri_out)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          libelle = COALESCE(VALUES(libelle), libelle),
          pri_out = COALESCE(VALUES(pri_out), pri_out)
        """,
        (parent_id, c, libelle, pri_out),
    )
    cur.execute(
        "SELECT id FROM ref_subdivision_noeud WHERE parent_id <=> %s AND code = %s",
        (parent_id, c),
    )
    return int(cur.fetchone()[0])


def _compose_flat_label(r: SubdivisionFlatRow) -> str:
    parts = [r.design_u, r.design_d, r.design_t]
    return " / ".join(p for p in parts if p.strip())


def import_referentials_from_access(
    cur: Any,
    pull: Any,
) -> RefEtlCache:
    """Charge TabStructure / TabGrade / TabSec* directement dans ref_* (sans tab_*)."""
    cache = RefEtlCache()
    counts = cache.counts

    rows, _ = pull("SELECT code, designation, OderTri FROM TabStructure")
    for code, designation, ordre_tri in rows:
        c = _norm_code(str(code))
        if not c:
            continue
        rid = _upsert_ref_structure(cur, c, str(designation or "") or None, ordre_tri)
        cache.structure_by_code[_cf(c)] = rid
        cache.structure_code_by_id[rid] = c
    counts["ref_structure"] = len(cache.structure_by_code)

    rows, _ = pull("SELECT CodeG, Grade, PrefOrd, EffectifG FROM TabGrade")
    for code_g, grade, pref_ord, effectif_g in rows:
        code = _norm_code(str(code_g))
        if not code:
            continue
        lib = (str(grade) if grade is not None else "").strip() or code
        rid = _upsert_ref_grade(cur, code, lib, pref_ord, effectif_g)
        cache.grade_by_code[_cf(code)] = rid
        cache.grade_by_libelle[_cf(lib)] = rid
        cache.grade_libelle_by_id[rid] = lib
    counts["ref_grade"] = len(cache.grade_by_code)

    _ensure_subdiv_root(cur)
    u_ids: dict[str, int] = {}
    rows, _ = pull("SELECT CodeU, SecuDesign FROM TabSecun")
    for code_u, design in rows:
        cu = _norm_code(str(code_u))
        u_ids[cu] = _upsert_subdiv_node(cur, parent_id=1, code=cu, libelle=str(design or ""), pri_out=None)

    d_ids: dict[tuple[str, str], int] = {}
    rows, _ = pull("SELECT CodeU, CodeD, SecdDesign, PriOutD FROM TabSecdeux")
    for code_u, code_d, design, pri in rows:
        cu, cd = _norm_code(str(code_u)), _norm_code(str(code_d))
        parent = u_ids.get(cu)
        if parent is None:
            continue
        d_ids[(cu, cd)] = _upsert_subdiv_node(
            cur, parent_id=parent, code=cd, libelle=str(design or ""), pri_out=pri
        )

    rows, _ = pull(
        "SELECT d.CodeU, d.CodeD, t.CodeT, t.SectDesign, t.PriOutT "
        "FROM TabSecdeux d INNER JOIN TabSectrois t ON t.CodeD = d.CodeD"
    )
    for code_u, code_d, code_t, design, pri in rows:
        cu, cd, ct = _norm_code(str(code_u)), _norm_code(str(code_d)), _norm_code(str(code_t))
        parent = d_ids.get((cu, cd))
        if parent is None:
            continue
        tid = _upsert_subdiv_node(
            cur, parent_id=parent, code=ct, libelle=str(design or ""), pri_out=pri
        )
        cache.leaf_node_by_flat_key[_cf(f"{cu}|{cd}|{ct}")] = tid
        dt = _cf(str(design or ""))
        if dt:
            cache.leaf_node_by_design_t[dt] = tid
    counts["ref_subdivision_noeud"] = len(cache.leaf_node_by_flat_key)

    cur.execute("SELECT id, annee_mois FROM sme_periode")
    for pid, ym in cur.fetchall():
        cache.periode_by_ym[str(ym)] = int(pid)
    return cache


def sync_tab_referentials_to_ref(cur: Any) -> RefEtlCache:
    """
    Copie tab_structure, tab_grade, tab_padmin et l'arbre U→D→T vers ref_*.
    Reconstruit les caches de résolution.
    """
    cache = RefEtlCache()
    counts = cache.counts

    cur.execute("SELECT code, designation, ordre_tri FROM tab_structure")
    for code, designation, ordre_tri in cur.fetchall():
        rid = _upsert_ref_structure(cur, str(code), designation, ordre_tri)
        cache.structure_by_code[_cf(str(code))] = rid
    counts["ref_structure"] = len(cache.structure_by_code)

    cur.execute("SELECT code_g, grade, pref_ord, effectif_g FROM tab_grade")
    for code_g, grade, pref_ord, effectif_g in cur.fetchall():
        code = _norm_code(str(code_g))
        lib = (str(grade) if grade is not None else "").strip() or code
        rid = _upsert_ref_grade(cur, code, lib, pref_ord, effectif_g)
        cache.grade_by_code[_cf(code)] = rid
        if lib:
            cache.grade_by_libelle[_cf(lib)] = rid
    counts["ref_grade"] = len(cache.grade_by_code)

    cur.execute("SELECT libelle, compte_comme_present, ordre_tri FROM tab_padmin")
    for libelle, present, ordre in cur.fetchall():
        lb = str(libelle or "").strip()
        if not lb:
            continue
        rid = _upsert_ref_padmin(cur, lb, int(present or 0), ordre)
        cache.padmin_by_libelle[_cf(lb)] = rid
    counts["ref_position_admin"] = len(cache.padmin_by_libelle)

    _ensure_subdiv_root(cur)
    u_ids: dict[str, int] = {}
    cur.execute("SELECT code_u, secu_design FROM tab_secun")
    for code_u, design in cur.fetchall():
        cu = _norm_code(str(code_u))
        uid = _upsert_subdiv_node(cur, parent_id=1, code=cu, libelle=str(design or ""), pri_out=None)
        u_ids[cu] = uid

    d_ids: dict[tuple[str, str], int] = {}
    cur.execute("SELECT code_u, code_d, secd_design, pri_out_d FROM tab_secdeux")
    for code_u, code_d, design, pri in cur.fetchall():
        cu, cd = _norm_code(str(code_u)), _norm_code(str(code_d))
        parent = u_ids.get(cu)
        if parent is None:
            continue
        did = _upsert_subdiv_node(
            cur, parent_id=parent, code=cd, libelle=str(design or ""), pri_out=pri
        )
        d_ids[(cu, cd)] = did

    flat_rows = load_subdiv_flat_rows(cur)

    cur.execute(
        """
        SELECT d.code_u, t.code_d, t.code_t, t.sect_design, t.pri_out_t
        FROM tab_sectrois t
        INNER JOIN tab_secdeux d ON d.code_d = t.code_d
        """
    )
    for code_u, code_d, code_t, design, pri in cur.fetchall():
        cu, cd, ct = _norm_code(str(code_u)), _norm_code(str(code_d)), _norm_code(str(code_t))
        parent = d_ids.get((cu, cd))
        if parent is None:
            continue
        tid = _upsert_subdiv_node(
            cur, parent_id=parent, code=ct, libelle=str(design or ""), pri_out=pri
        )
        dt = _cf(str(design or ""))
        if dt:
            cache.leaf_node_by_design_t[dt] = tid
        for fr in flat_rows:
            if fr.code_u == cu and fr.code_d == cd and fr.code_t == ct:
                cache.leaf_node_by_flat_key[_cf(f"{cu}|{cd}|{ct}")] = tid
                lbl = _compose_flat_label(fr)
                if lbl:
                    cache.leaf_node_by_design_t[_cf(lbl)] = tid
    counts["ref_subdivision_noeud"] = len(cache.leaf_node_by_flat_key)

    cur.execute("SELECT id, annee_mois FROM sme_periode")
    for pid, ym in cur.fetchall():
        cache.periode_by_ym[str(ym)] = int(pid)

    return cache


def ensure_ref_fonction(cur: Any, cache: RefEtlCache, libelle: str | None) -> int | None:
    lb = (libelle or "").strip()
    if not lb:
        return None
    key = _cf(lb)
    if key in cache.fonction_by_libelle:
        return cache.fonction_by_libelle[key]
    rid = _upsert_ref_fonction(cur, lb)
    cache.fonction_by_libelle[key] = rid
    cache.fonction_libelle_by_id[rid] = lb
    return rid


_ORG_U_CODE = "_ORG"


def _org_subdiv_codes(libelle: str) -> tuple[str, str]:
    digest = hashlib.sha1(libelle.encode("utf-8")).hexdigest()[:16].upper()
    return f"D{digest[:12]}", f"T{digest[:12]}"


def _ensure_org_subdiv_u(cur: Any, cache: RefEtlCache) -> int:
    """Nœud U réservé aux subdivisions organisationnelles hors nomenclature officielle."""
    hit = cache.misc_node_ids.get("org_u")
    if hit:
        return hit
    _ensure_subdiv_root(cur)
    uid = _upsert_subdiv_node(
        cur,
        parent_id=1,
        code=_ORG_U_CODE,
        libelle="Organisationnel (hors nomenclature)",
        pri_out=None,
    )
    cache.misc_node_ids["org_u"] = uid
    return uid


def _ensure_org_subdiv_leaf(cur: Any, cache: RefEtlCache, libelle: str) -> int:
    """Feuille T synthétique U→D→T pour SubArme hors référentiel TabSec*."""
    lb = libelle.strip()
    key = _cf(lb)
    hit = cache.leaf_node_by_design_t.get(key)
    if hit:
        return hit
    org_u = _ensure_org_subdiv_u(cur, cache)
    d_code, t_code = _org_subdiv_codes(lb)
    did = _upsert_subdiv_node(
        cur,
        parent_id=org_u,
        code=d_code,
        libelle=lb,
        pri_out=None,
    )
    tid = _upsert_subdiv_node(
        cur,
        parent_id=did,
        code=t_code,
        libelle=lb,
        pri_out=None,
    )
    cache.leaf_node_by_design_t[key] = tid
    return tid


def ensure_ref_subdiv_arme(
    cur: Any,
    cache: RefEtlCache,
    *,
    structure_id: int | None,
    subdivision_libelle: str | None,
    flat_rows: list[SubdivisionFlatRow] | None = None,
    auto_create: bool = False,
) -> int | None:
    if structure_id is None:
        return None
    lib = (subdivision_libelle or "").strip()
    if not lib or lib == "(Sans subdivision)":
        return None
    leaf_id = _resolve_leaf_node_id(cache, lib, flat_rows)
    if leaf_id is None and auto_create:
        leaf_id = _ensure_org_subdiv_leaf(cur, cache, lib)
    if leaf_id is None:
        return None
    key = (structure_id, leaf_id)
    if key in cache.subdiv_arme_by_struct_leaf:
        return cache.subdiv_arme_by_struct_leaf[key]
    cur.execute(
        """
        INSERT INTO ref_subdivision_arme (structure_id, noeud_feuille_id, libelle, actif)
        VALUES (%s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE libelle = COALESCE(VALUES(libelle), libelle)
        """,
        (structure_id, leaf_id, lib),
    )
    cur.execute(
        """
        SELECT id FROM ref_subdivision_arme
        WHERE structure_id = %s AND noeud_feuille_id = %s
        """,
        (structure_id, leaf_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    rid = int(row[0])
    cache.subdiv_arme_by_struct_leaf[key] = rid
    cache.subdiv_arme_libelle_by_id[rid] = lib
    return rid


def _resolve_leaf_node_id(
    cache: RefEtlCache,
    raw: str | None,
    flat_rows: list[SubdivisionFlatRow] | None,
) -> int | None:
    s = (raw or "").strip()
    if not s or s == "(Sans subdivision)":
        return None
    hit = cache.leaf_node_by_design_t.get(_cf(s))
    if hit:
        return hit
    if flat_rows:
        scf = _cf(s)
        for r in flat_rows:
            dt = (r.design_t or "").strip()
            if dt and _cf(dt) == scf:
                return cache.leaf_node_by_flat_key.get(_cf(f"{r.code_u}|{r.code_d}|{r.code_t}"))
            if dt and len(dt) >= 4 and scf.endswith(_cf(dt)):
                return cache.leaf_node_by_flat_key.get(_cf(f"{r.code_u}|{r.code_d}|{r.code_t}"))
            for code in (r.code_t, r.code_d, r.code_u):
                if code and _cf(code) == scf:
                    return cache.leaf_node_by_flat_key.get(_cf(f"{r.code_u}|{r.code_d}|{r.code_t}"))
    return None


def resolve_carriere_fk_ids(
    cur: Any,
    cache: RefEtlCache,
    row: ListeGeneraleRow,
    *,
    flat_rows: list[SubdivisionFlatRow] | None = None,
    auto_create: bool = False,
) -> dict[str, int | None]:
    """Résout structure_id, grade_id, position_admin_id, fonction_id, subdivision_arme_id."""
    struct_code = _norm_code(row.structure)
    structure_id = cache.structure_by_code.get(_cf(struct_code)) if struct_code else None

    grade_id = None
    gtxt = (row.grade or "").strip()
    if gtxt:
        grade_id = cache.grade_by_libelle.get(_cf(gtxt)) or cache.grade_by_code.get(_cf(gtxt))
        if grade_id is None and auto_create:
            code = gtxt[:64]
            grade_id = _upsert_ref_grade(cur, code, gtxt, None, None)
            cache.grade_by_libelle[_cf(gtxt)] = grade_id
            cache.grade_by_code[_cf(code)] = grade_id
            cache.grade_libelle_by_id[grade_id] = gtxt

    padmin_id = None
    pat = (row.position_admin or "").strip()
    if pat:
        padmin_id = cache.padmin_by_libelle.get(_cf(pat))
        if padmin_id is None and auto_create:
            from sme_gend.padmin_present import infer_compte_comme_present

            padmin_id = _upsert_ref_padmin(cur, pat, int(infer_compte_comme_present(pat)), None)
            cache.padmin_by_libelle[_cf(pat)] = padmin_id
            cache.padmin_libelle_by_id[padmin_id] = pat

    fonction_id = ensure_ref_fonction(cur, cache, row.fonction) if auto_create else (
        cache.fonction_by_libelle.get(_cf((row.fonction or "").strip()))
        if (row.fonction or "").strip()
        else None
    )
    subdiv_id = ensure_ref_subdiv_arme(
        cur,
        cache,
        structure_id=structure_id,
        subdivision_libelle=row.subdivision_arme,
        flat_rows=flat_rows,
        auto_create=auto_create,
    )
    return {
        "structure_id": structure_id,
        "grade_id": grade_id,
        "position_admin_id": padmin_id,
        "fonction_id": fonction_id,
        "subdivision_arme_id": subdiv_id,
    }


def _ref_label(cur: Any, table: str, column: str, row_id: int | None) -> str | None:
    if not row_id:
        return None
    cur.execute(f"SELECT {column} FROM {table} WHERE id = %s LIMIT 1", (int(row_id),))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    text = str(row[0]).strip()
    return text or None


def _cached_label(cache: RefEtlCache, labels: dict[int, str], row_id: int | None) -> str | None:
    if not row_id:
        return None
    label = labels.get(int(row_id))
    if label is not None:
        label = label.strip()
        return label or None
    return None


def prepare_carriere_write_fields(
    cur: Any,
    cache: RefEtlCache,
    row: ListeGeneraleRow,
    *,
    flat_rows: list[SubdivisionFlatRow] | None = None,
    auto_create: bool = False,
) -> dict[str, int | str | None]:
    """
    Résout les FK puis dérive les libellés depuis le référentiel (FK-first).

    Les colonnes ``*_libelle`` / ``structure_code`` stockées reflètent le ref
    lorsque la FK est résolue ; sinon la saisie ``row`` est conservée.
    """
    fk = resolve_carriere_fk_ids(cur, cache, row, flat_rows=flat_rows, auto_create=auto_create)
    grade = (row.grade or "").strip() or None
    structure = (row.structure or "").strip() or None
    padmin = (row.position_admin or "").strip() or None
    fonction = (row.fonction or "").strip() or None
    subdiv = (row.subdivision_arme or "").strip() or None

    if fk["grade_id"]:
        grade = _cached_label(cache, cache.grade_libelle_by_id, fk["grade_id"]) or _ref_label(
            cur, "ref_grade", "libelle", fk["grade_id"]
        ) or grade
    if fk["structure_id"]:
        structure = _cached_label(cache, cache.structure_code_by_id, fk["structure_id"]) or _ref_label(
            cur, "ref_structure", "code", fk["structure_id"]
        ) or structure
    if fk["position_admin_id"]:
        padmin = _cached_label(cache, cache.padmin_libelle_by_id, fk["position_admin_id"]) or _ref_label(
            cur, "ref_position_admin", "libelle", fk["position_admin_id"]
        ) or padmin
    if fk["fonction_id"]:
        fonction = _cached_label(cache, cache.fonction_libelle_by_id, fk["fonction_id"]) or _ref_label(
            cur, "ref_fonction", "libelle", fk["fonction_id"]
        ) or fonction
    if fk["subdivision_arme_id"]:
        subdiv = _cached_label(
            cache, cache.subdiv_arme_libelle_by_id, fk["subdivision_arme_id"]
        ) or _ref_label(cur, "ref_subdivision_arme", "libelle", fk["subdivision_arme_id"]) or subdiv

    return {
        **fk,
        "grade_libelle": grade,
        "structure_code": structure,
        "position_admin_libelle": padmin,
        "fonction_libelle": fonction,
        "subdivision_arme_libelle": subdiv,
    }


def load_subdiv_flat_rows_from_ref(cur: Any) -> list[SubdivisionFlatRow]:
    """Hiérarchie U→D→T depuis ``ref_subdivision_noeud`` (racine id=1)."""
    cur.execute(
        """
        SELECT u.code, u.libelle, d.code, d.libelle, t.code, t.libelle
        FROM ref_subdivision_noeud u
        INNER JOIN ref_subdivision_noeud d ON d.parent_id = u.id
        INNER JOIN ref_subdivision_noeud t ON t.parent_id = d.id
        WHERE u.parent_id = 1
        ORDER BY u.code, d.code, t.code
        """
    )
    return [SubdivisionFlatRow.from_sequence(r) for r in cur.fetchall()]


def load_ref_cache_from_db(cur: Any) -> RefEtlCache:
    """Cache des id ref_* déjà en base (sans re-sync tab)."""
    cache = RefEtlCache()
    cur.execute("SELECT code, id FROM ref_structure")
    for code, rid in cur.fetchall():
        cache.structure_by_code[_cf(str(code))] = int(rid)
        cache.structure_code_by_id[int(rid)] = str(code or "").strip()
    cur.execute("SELECT code, libelle, id FROM ref_grade")
    for code, lib, rid in cur.fetchall():
        cache.grade_by_code[_cf(str(code))] = int(rid)
        if lib:
            cache.grade_by_libelle[_cf(str(lib))] = int(rid)
            cache.grade_libelle_by_id[int(rid)] = str(lib).strip()
    cur.execute("SELECT libelle, id FROM ref_position_admin")
    for lib, rid in cur.fetchall():
        cache.padmin_by_libelle[_cf(str(lib))] = int(rid)
        cache.padmin_libelle_by_id[int(rid)] = str(lib or "").strip()
    cur.execute("SELECT libelle, id FROM ref_fonction")
    for lib, rid in cur.fetchall():
        cache.fonction_by_libelle[_cf(str(lib))] = int(rid)
        cache.fonction_libelle_by_id[int(rid)] = str(lib or "").strip()
    cur.execute("SELECT structure_id, noeud_feuille_id, id FROM ref_subdivision_arme")
    for sid, nid, rid in cur.fetchall():
        cache.subdiv_arme_by_struct_leaf[(int(sid), int(nid))] = int(rid)
    cur.execute("SELECT id, libelle FROM ref_subdivision_arme")
    for rid, lib in cur.fetchall():
        if lib:
            cache.subdiv_arme_libelle_by_id[int(rid)] = str(lib).strip()
    cur.execute("SELECT id, annee_mois FROM sme_periode")
    for pid, ym in cur.fetchall():
        cache.periode_by_ym[str(ym)] = int(pid)
    cur.execute(
        """
        SELECT u.code, u.libelle, d.code, d.libelle, t.code, t.libelle, t.id
        FROM ref_subdivision_noeud u
        INNER JOIN ref_subdivision_noeud d ON d.parent_id = u.id
        INNER JOIN ref_subdivision_noeud t ON t.parent_id = d.id
        WHERE u.parent_id = 1
        ORDER BY u.code, d.code, t.code
        """
    )
    for row in cur.fetchall():
        fr = SubdivisionFlatRow.from_sequence(row[:6])
        tid = int(row[6])
        key = _cf(f"{fr.code_u}|{fr.code_d}|{fr.code_t}")
        cache.leaf_node_by_flat_key[key] = tid
        dt = _cf(fr.design_t or "")
        if dt:
            cache.leaf_node_by_design_t[dt] = tid
        lbl = _compose_flat_label(fr)
        if lbl:
            cache.leaf_node_by_design_t[_cf(lbl)] = tid
    return cache


def _table_exists(cur: Any, table_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def ensure_ref_etl_cache(cur: Any) -> RefEtlCache:
    """Charge ref_* depuis la base (sans resynchroniser les tables tab_* legacy)."""
    return load_ref_cache_from_db(cur)


def load_subdiv_flat_rows(cur: Any) -> list[SubdivisionFlatRow]:
    """Arbre U→D→T depuis ``ref_subdivision_noeud`` uniquement."""
    return load_subdiv_flat_rows_from_ref(cur)


def import_gain_perte_to_mouvement_effectif(
    cur: Any,
    cache: RefEtlCache,
    *,
    sme_periode_id: int,
    import_lot_id: int | None,
    mois_import: str,
    replace_period_only: bool,
) -> int:
    """
    Charge tab_gain_perte → mouvement_effectif avec date_reference = dernier jour du mois SME.
    """
    from sme_gend.models.gain_perte import observ_is_deces
    from sme_gend.services.mysql_import_period import (
        first_day_of_month,
        last_day_of_month,
        normalize_import_mois,
    )

    ym_import = normalize_import_mois(mois_import)
    fin_import = last_day_of_month(ym_import)

    if not _table_exists(cur, "tab_gain_perte"):
        return 0

    if replace_period_only:
        cur.execute("DELETE FROM mouvement_effectif WHERE sme_periode_id = %s", (sme_periode_id,))
    else:
        cur.execute("DELETE FROM mouvement_effectif")

    cur.execute("SELECT id, grade, nom, objet, cdsme, observ FROM tab_gain_perte")
    n = 0
    for leg, grade, nom, objet, cdsme, observ in cur.fetchall():
        leg_s = str(leg or "").strip()
        if not leg_s:
            continue
        cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg_s, leg_s))
        ag = cur.fetchone()
        if not ag:
            continue
        agent_id = int(ag[0])
        row_ym = cdsme_to_annee_mois(str(cdsme or "")) or ym_import
        pid = cache.periode_by_ym.get(row_ym)
        if pid is None:
            from sme_gend.services.mysql_import_period import last_day_of_month as ldom

            cur.execute(
                """
                INSERT INTO sme_periode (annee_mois, date_dernier_jour_mois, codsme)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE codsme = COALESCE(VALUES(codsme), codsme)
                """,
                (row_ym, ldom(row_ym), str(cdsme or "").strip() or None),
            )
            cur.execute("SELECT id FROM sme_periode WHERE annee_mois = %s", (row_ym,))
            got = cur.fetchone()
            if got:
                pid = int(got[0])
                cache.periode_by_ym[row_ym] = pid
        if pid is None:
            pid = sme_periode_id
        observ_s = str(observ or "").strip() or None
        ym_ref = row_ym or ym_import
        if observ_is_deces(observ_s):
            ref_date = first_day_of_month(ym_ref)
        else:
            ref_date = last_day_of_month(ym_ref) if ym_ref else fin_import
        cur.execute(
            """
            INSERT INTO mouvement_effectif (
              sme_periode_id, agent_id, identifiant_ligne, grade_snapshot, nom_snapshot,
              type_mouvement, cdsme_derive, observ, date_reference, import_lot_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                pid,
                agent_id,
                leg_s,
                str(grade or "").strip() or None,
                str(nom or "").strip() or None,
                str(objet or "").strip() or None,
                str(cdsme or "").strip() or None,
                observ_s,
                ref_date,
                import_lot_id,
            ),
        )
        n += 1
    return n
