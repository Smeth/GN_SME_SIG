#!/usr/bin/env python3
"""Applique les decisions du fichier sexe_sync_decisions.csv."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.models.personnel_sexe import normalize_sexe

DEFAULT = _ROOT / "data" / "sexe_sync_reports" / "sexe_sync_decisions.csv"


def _norm_decision(raw: str) -> str:
    return (raw or "").strip().upper()[:1]


def main() -> int:
    p = argparse.ArgumentParser(description="Applique sexe_sync_decisions.csv")
    p.add_argument("--fichier", type=Path, default=DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = args.fichier.expanduser().resolve()
    if not path.is_file():
        print(f"Fichier introuvable : {path}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    todo = [r for r in rows if _norm_decision(r.get("decision", "")) in ("R", "B") and (r.get("statut_applique") or "").upper() != "FAIT"]

    if not todo:
        print("Aucune ligne avec decision R/B non encore FAIT.")
        return 0

    s = get_settings()
    applied = skipped = errors = 0

    if args.dry_run:
        print(f"Dry-run — {len(todo)} ligne(s) a traiter :")
        for r in todo[:20]:
            d = _norm_decision(r["decision"])
            print(f"  [{r.get('no')}] {d} {r.get('legacy_id')} {r.get('noms')}")
        if len(todo) > 20:
            print(f"  ... +{len(todo)-20}")
        return 0

    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        cur = conn.cursor()
        for r in todo:
            leg = (r.get("legacy_id") or "").strip()
            decision = _norm_decision(r.get("decision", ""))
            if not leg:
                r["statut_applique"] = "ERREUR: pas de legacy_id"
                errors += 1
                continue

            cur.execute(
                "SELECT id, sexe, matricule_militaire FROM agent WHERE legacy_access_id = %s",
                (leg,),
            )
            ag = cur.fetchone()
            if not ag:
                r["statut_applique"] = "ERREUR: agent introuvable"
                errors += 1
                continue

            cat = (r.get("categorie") or "").strip()
            ref_sexe = normalize_sexe(r.get("ref_sexe"))
            ref_mat = (r.get("ref_matricule") or "").strip()
            mat_actuel = (r.get("mat_actuel") or "").strip()

            sets: list[str] = []
            params: list[object] = []

            if decision == "B":
                r["statut_applique"] = "FAIT"
                skipped += 1
                continue

            # decision == R
            if cat in ("NOM_PROCHE_SANS_SEXE", "NON_RESOLU"):
                if ref_sexe:
                    sets.append("sexe = %s")
                    params.append(ref_sexe)
                if ref_mat:
                    sets.append("matricule_militaire = %s")
                    params.append(ref_mat)
            elif cat == "CONFLIT_MAT":
                if ref_mat:
                    sets.append("matricule_militaire = %s")
                    params.append(ref_mat)
            else:
                if ref_sexe:
                    sets.append("sexe = %s")
                    params.append(ref_sexe)
                if ref_mat:
                    sets.append("matricule_militaire = %s")
                    params.append(ref_mat)

            if not sets:
                r["statut_applique"] = "ERREUR: rien a appliquer"
                errors += 1
                continue

            params.append(leg)
            cur.execute(
                f"UPDATE agent SET {', '.join(sets)} WHERE legacy_access_id = %s",
                params,
            )
            r["statut_applique"] = "FAIT"
            applied += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-ecrit le CSV avec statuts mis a jour
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)

    print(f"Termine — applique: {applied}, conserve (B): {skipped}, erreurs: {errors}")
    print(f"Fichier mis a jour : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
