#!/usr/bin/env python3
"""Applique les saisies commission depuis COMMISSION_agents_sans_sexe.xlsx."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from openpyxl import load_workbook
from sme_gend.config import get_settings
from sme_gend.models.personnel_sexe import normalize_sexe

DEFAULT = _ROOT / "data" / "sexe_sync_reports" / "COMMISSION_agents_sans_sexe.xlsx"
SHEET = "Liste agents"
HEADER_ROW = 4


def _read_todo(path: Path) -> list[dict[str, str]]:
    wb = load_workbook(path, data_only=True)
    if SHEET not in wb.sheetnames:
        wb.close()
        raise ValueError(f"Feuille « {SHEET} » absente.")
    ws = wb[SHEET]
    headers = [str(c.value or "").strip() for c in ws[HEADER_ROW]]
    idx_sexe = next(i for i, h in enumerate(headers) if "RENSEIGNER" in h.upper())
    idx_leg = next(i for i, h in enumerate(headers) if "syst" in h.lower())
    idx_noms = next((i for i, h in enumerate(headers) if "prénom" in h.lower() or "prenom" in h.lower()), 1)
    idx_note = next((i for i, h in enumerate(headers) if "observation" in h.lower()), None)

    todo: list[dict[str, str]] = []
    for r in range(HEADER_ROW + 1, ws.max_row + 1):
        row = [ws.cell(r, c + 1).value for c in range(len(headers))]
        if not any(row):
            continue
        sexe = normalize_sexe(str(row[idx_sexe] or ""))
        leg = str(row[idx_leg] or "").strip()
        if sexe and leg:
            todo.append(
                {
                    "legacy_id": leg,
                    "sexe_correct": sexe,
                    "noms": str(row[idx_noms] or "").strip(),
                    "note": str(row[idx_note] or "").strip() if idx_note is not None else "",
                }
            )
    wb.close()
    return todo


def main() -> int:
    p = argparse.ArgumentParser(description="Applique saisies commission Excel -> MySQL.")
    p.add_argument("--fichier", type=Path, default=DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    path = args.fichier.resolve()
    if not path.is_file():
        print(f"Fichier introuvable : {path}")
        return 1

    todo = _read_todo(path)
    if not todo:
        print("Aucune saisie M/F dans le fichier commission.")
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
        leg = r["legacy_id"]
        sexe = r["sexe_correct"]
        if args.dry_run:
            print(f"  {leg} {r['noms']} -> sexe={sexe}")
            ok += 1
            continue
        cur.execute(
            "UPDATE agent SET sexe=%s WHERE legacy_access_id=%s",
            (sexe, leg),
        )
        if cur.rowcount == 0:
            print(f"  ERREUR: legacy_id {leg} introuvable ({r['noms']})")
            err += 1
        else:
            ok += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"{'Dry-run — ' if args.dry_run else ''}{ok} applique(s), {err} erreur(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
