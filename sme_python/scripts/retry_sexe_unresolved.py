#!/usr/bin/env python3
"""2e passe : resout les NON_RESOLU sans legacy_id (match auto nom/matricule)."""

from __future__ import annotations

import argparse
import csv
import difflib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.models.personnel_sexe import normalize_sexe
from sme_gend.services.personnel_sexe_reference_sync import (
    name_token_key,
    normalize_matricule_key,
    normalize_person_name,
)

DEFAULT = _ROOT / "data" / "sexe_sync_reports" / "sexe_sync_decisions.csv"


def _load_agents(cur) -> tuple[list[dict], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]], list[str]]:
    cur.execute(
        "SELECT legacy_access_id, noms, sexe, matricule_militaire FROM agent ORDER BY noms"
    )
    agents: list[dict] = []
    by_mat: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    by_tokens: dict[str, list[dict]] = {}
    for leg, noms, sexe, mat in cur.fetchall():
        ag = {
            "legacy_id": str(leg or "").strip(),
            "noms_raw": str(noms or "").strip(),
            "noms": normalize_person_name(noms),
            "tokens": name_token_key(noms),
            "sexe": sexe,
            "mat": (mat or "").strip(),
            "mat_key": normalize_matricule_key(mat),
        }
        agents.append(ag)
        if ag["mat_key"]:
            by_mat.setdefault(ag["mat_key"], []).append(ag)
        by_name.setdefault(ag["noms"], []).append(ag)
        by_tokens.setdefault(ag["tokens"], []).append(ag)
    names = [a["noms"] for a in agents]
    return agents, by_mat, by_name, by_tokens, names


def _find_unique(
    ref_noms: str,
    ref_mat: str,
    *,
    by_mat: dict[str, list[dict]],
    by_name: dict[str, list[dict]],
    by_tokens: dict[str, list[dict]],
    names: list[str],
) -> tuple[dict | None, str]:
    mk = normalize_matricule_key(ref_mat)
    if mk:
        c = by_mat.get(mk) or []
        if len(c) == 1:
            return c[0], "matricule"
        if len(c) > 1:
            return None, "matricule_ambigu"

    rn = normalize_person_name(ref_noms)
    c = by_name.get(rn) or []
    if len(c) == 1:
        return c[0], "nom_exact"
    if len(c) > 1:
        return None, "nom_ambigu"

    tk = name_token_key(ref_noms)
    c = by_tokens.get(tk) or []
    if len(c) == 1:
        return c[0], "nom_tokens"
    if len(c) > 1:
        return None, "tokens_ambigu"

    close = difflib.get_close_matches(rn, names, n=2, cutoff=0.86)
    if len(close) == 1:
        ag = next(a for a in by_name[close[0]] if a["noms"] == close[0])
        return ag, "similarite"
    if len(close) > 1:
        return None, "similarite_ambigu"

    return None, "aucun"


def main() -> int:
    p = argparse.ArgumentParser(description="2e passe auto NON_RESOLU sans legacy_id")
    p.add_argument("--fichier", type=Path, default=DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = args.fichier.resolve()
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    pending = [
        r
        for r in rows
        if "ERREUR: pas de legacy_id" in (r.get("statut_applique") or "")
        or (
            (r.get("categorie") or "") == "NON_RESOLU"
            and not (r.get("legacy_id") or "").strip()
            and (r.get("decision") or "").upper().startswith("R")
            and (r.get("statut_applique") or "").upper() != "FAIT"
        )
    ]

    if not pending:
        print("Aucune ligne NON_RESOLU sans legacy_id.")
        return 0

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
    agents, by_mat, by_name, by_tokens, names = _load_agents(conn.cursor())

    ok = fail = 0
    cur = conn.cursor()

    for r in pending:
        ref_noms = (r.get("ref_noms") or r.get("noms") or "").strip()
        ref_mat = (r.get("ref_matricule") or "").strip()
        ref_sexe = normalize_sexe(r.get("ref_sexe"))

        ag, method = _find_unique(
            ref_noms,
            ref_mat,
            by_mat=by_mat,
            by_name=by_name,
            by_tokens=by_tokens,
            names=names,
        )

        if ag is None:
            fail += 1
            r["statut_applique"] = f"ERREUR: {method}"
            r["note"] = (r.get("note") or "") + f" [2e passe: {method}]".strip()
            if args.dry_run:
                print(f"  ECHEC [{r.get('no')}] {ref_noms} ({method})")
            continue

        if args.dry_run:
            print(
                f"  OK [{r.get('no')}] {ref_noms} -> [{ag['legacy_id']}] "
                f"{ag['noms_raw']} ({method})"
            )
            ok += 1
            continue

        sets = []
        params: list[object] = []
        if ref_sexe:
            sets.append("sexe = %s")
            params.append(ref_sexe)
        if ref_mat:
            sets.append("matricule_militaire = %s")
            params.append(ref_mat)
        if sets:
            params.append(ag["legacy_id"])
            cur.execute(
                f"UPDATE agent SET {', '.join(sets)} WHERE legacy_access_id = %s",
                params,
            )

        r["legacy_id"] = ag["legacy_id"]
        r["noms"] = ag["noms_raw"]
        r["sexe_actuel"] = ref_sexe or ""
        r["mat_actuel"] = ref_mat or ag["mat"]
        r["statut_applique"] = "FAIT"
        r["note"] = (r.get("note") or "") + f" [2e passe: {method}]".strip()
        ok += 1

    if not args.dry_run:
        conn.commit()
        fields = list(rows[0].keys())
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            w.writeheader()
            w.writerows(rows)

    conn.close()
    print(f"{'Dry-run — ' if args.dry_run else ''}2e passe : {ok} resolu(s), {fail} echec(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
