#!/usr/bin/env python3
"""Importe un bundle Access (.mdb) pour une période SME YYYY-MM."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from sme_gend.config import get_settings
from sme_gend.config.settings import Settings
from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access


def main() -> int:
    p = argparse.ArgumentParser(description="Import Access → MySQL pour un mois SME.")
    p.add_argument("mdb", type=Path, help="Chemin vers le fichier .mdb / .accdb")
    p.add_argument("--mois", required=True, help="Période SME YYYY-MM (ex. 2026-04)")
    args = p.parse_args()

    mdb = args.mdb.expanduser().resolve()
    if not mdb.is_file():
        print(f"Fichier introuvable : {mdb}", file=sys.stderr)
        return 1

    s = get_settings()
    access_st = Settings(
        database_path=mdb,
        mysql_host=s.mysql_host,
        mysql_port=s.mysql_port,
        mysql_database=s.mysql_database,
        mysql_user=s.mysql_user,
        mysql_password=s.mysql_password,
    )

    print(f"Import {mdb.name} -> MySQL {s.mysql_database} (periode {args.mois})...")

    def progress(msg: str) -> None:
        print(f"  ... {msg}")

    result = import_full_mysql_bundle_from_access(
        access_st,
        s,
        mois=args.mois,
        progress=progress,
    )
    print(
        f"Termine — {result.get('sme_mois')}: "
        f"+{result.get('liste_inserted', 0)} insérés, "
        f"~{result.get('liste_updated', 0)} mis à jour."
    )
    counts = result.get("ref_counts") or result.get("counts") or {}
    if isinstance(counts, dict):
        mv = counts.get("mouvement_effectif")
        if mv is not None:
            print(f"  mouvement_effectif importés : {mv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
