#!/usr/bin/env python3
"""2e passe elargie : agents sans sexe -> reference Excel."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.services.personnel_sexe_reference_sync import (
    load_sexe_reference_bundle,
    name_token_key,
    normalize_matricule_key,
    normalize_person_name,
)


def _valid_mat_key(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s or s in ("/", "-", "0", "?"):
        return ""
    mk = normalize_matricule_key(s)
    return mk if mk and mk != "0" else ""


def _valid_mat_value(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s or s in ("/", "-", "?"):
        return None
    return s


def _find_ref(
    ag: dict,
    *,
    by_mat: dict,
    by_name: dict,
    by_tokens: dict,
    ref_names: list[str],
    ref_by_name: dict,
) -> tuple[object | None, str]:
    if ag["mat_key"]:
        c = by_mat.get(ag["mat_key"]) or []
        if len(c) == 1:
            return c[0], "matricule"
    c = by_name.get(ag["noms"]) or []
    if len(c) == 1:
        return c[0], "nom_exact"
    c = by_tokens.get(ag["tokens"]) or []
    if len(c) == 1:
        return c[0], "nom_tokens"
    close = difflib.get_close_matches(ag["noms"], ref_names, n=1, cutoff=0.88)
    if close:
        return ref_by_name[close[0]], "similarite"
    return None, "aucun"


def main() -> int:
    p = argparse.ArgumentParser(description="Remplit sexe/mat des agents sans sexe via Excel")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    feminin = _ROOT / "LISTE_GENERALE_SEXE_feminin.xls"
    masculin = _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls"
    refs, _ = load_sexe_reference_bundle(feminin, masculin)

    by_mat: dict[str, list] = {}
    by_name: dict[str, list] = {}
    by_tokens: dict[str, list] = {}
    ref_by_name: dict = {}
    for r in refs:
        ref_by_name[r.noms] = r
        mk = _valid_mat_key(r.matricule_militaire)
        if mk:
            by_mat.setdefault(mk, []).append(r)
        by_name.setdefault(r.noms, []).append(r)
        by_tokens.setdefault(r.nom_tokens, []).append(r)
    ref_names = list(ref_by_name.keys())

    s = get_settings()
    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT legacy_access_id, noms, matricule_militaire
        FROM agent
        WHERE sexe IS NULL OR TRIM(sexe) = ''
        """
    )
    pending = cur.fetchall()
    ok = fail = 0

    for leg, noms, mat in pending:
        ag = {
            "legacy_id": str(leg or "").strip(),
            "noms": normalize_person_name(noms),
            "tokens": name_token_key(noms),
            "mat_key": _valid_mat_key(mat),
        }
        ref, method = _find_ref(
            ag,
            by_mat=by_mat,
            by_name=by_name,
            by_tokens=by_tokens,
            ref_names=ref_names,
            ref_by_name=ref_by_name,
        )
        if ref is None:
            fail += 1
            continue

        ref_mat = _valid_mat_value(ref.matricule_militaire)
        agent_mat = _valid_mat_value(mat)

        if args.dry_run:
            print(
                f"  OK [{ag['legacy_id']}] {noms} -> sexe={ref.sexe} "
                f"mat={ref_mat or agent_mat or '(inchange)'} ({method})"
            )
            ok += 1
            continue

        if ref_mat and (not agent_mat or agent_mat != ref_mat):
            cur.execute(
                "UPDATE agent SET sexe = %s, matricule_militaire = %s WHERE legacy_access_id = %s",
                (ref.sexe, ref_mat, ag["legacy_id"]),
            )
        else:
            cur.execute(
                "UPDATE agent SET sexe = %s WHERE legacy_access_id = %s",
                (ref.sexe, ag["legacy_id"]),
            )
        ok += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(
        f"{'Dry-run — ' if args.dry_run else ''}Agents sans sexe : "
        f"{ok} resolu(s), {fail} echec(s) sur {len(pending)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
