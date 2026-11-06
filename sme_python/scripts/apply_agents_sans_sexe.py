#!/usr/bin/env python3
"""Applique agents_sans_sexe.csv — colonnes sexe_correct / matricule_correct."""

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

DEFAULT = _ROOT / "data" / "sexe_sync_reports" / "agents_sans_sexe.csv"


def _valid_mat(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s or s in ("/", "-", "?"):
        return None
    return s


def main() -> int:
    p = argparse.ArgumentParser(
        description="Met a jour MySQL depuis sexe_correct / matricule_correct.",
    )
    p.add_argument("--fichier", type=Path, default=DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = args.fichier.resolve()
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    todo = [
        r for r in rows
        if normalize_sexe(r.get("sexe_correct") or r.get("sexe_manuel"))
        and (r.get("statut_applique") or "").upper() != "FAIT"
    ]

    if not todo:
        print("Aucune ligne avec sexe_correct (M/F) a appliquer.")
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
    cur = conn.cursor()
    ok = err = 0

    for r in todo:
        leg = (r.get("legacy_id") or "").strip()
        sexe = normalize_sexe(r.get("sexe_correct") or r.get("sexe_manuel"))
        mat = _valid_mat(r.get("matricule_correct"))
        if not leg or not sexe:
            r["statut_applique"] = "ERREUR: legacy_id ou sexe_correct invalide"
            err += 1
            continue
        if args.dry_run:
            print(f"  [{r.get('no')}] {leg} {r.get('noms')} -> sexe={sexe} mat={mat or '(inchange)'}")
            ok += 1
            continue
        if mat:
            cur.execute(
                "UPDATE agent SET sexe=%s, matricule_militaire=%s WHERE legacy_access_id=%s",
                (sexe, mat, leg),
            )
        else:
            cur.execute(
                "UPDATE agent SET sexe=%s WHERE legacy_access_id=%s",
                (sexe, leg),
            )
        r["statut_applique"] = "FAIT"
        ok += 1

    if not args.dry_run:
        conn.commit()
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(rows)
    conn.close()

    print(f"{'Dry-run — ' if args.dry_run else ''}{ok} applique(s), {err} erreur(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
