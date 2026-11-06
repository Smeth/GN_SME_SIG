#!/usr/bin/env python3
"""Importe un bundle Excel SME (.xlsx) pour une periode YYYY-MM."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from sme_gend.config import get_settings
from sme_gend.services.sme_excel_bundle_import import import_excel_bundle_for_month


def main() -> int:
    p = argparse.ArgumentParser(description="Import Excel SME -> MySQL (liste + gains/pertes).")
    p.add_argument("xlsx", type=Path, help="Chemin vers le fichier .xlsx")
    p.add_argument("--mois", required=True, help="Periode SME YYYY-MM (ex. 2026-02)")
    args = p.parse_args()

    xlsx = args.xlsx.expanduser().resolve()
    if not xlsx.is_file():
        print(f"Fichier introuvable : {xlsx}", file=sys.stderr)
        return 1

    s = get_settings()
    print(f"Import {xlsx.name} -> MySQL {s.mysql_database} (periode {args.mois})...")

    def progress(msg: str) -> None:
        print(f"  ... {msg}")

    result = import_excel_bundle_for_month(s, xlsx, mois=args.mois, progress=progress)
    print(
        f"Termine — {result['sme_mois']}: "
        f"liste {result['liste_resolved']}/{result['liste_read']} agents "
        f"(+{result['liste_inserted']} / ~{result['liste_updated']} MAJ), "
        f"mouvements {result['mouvement_effectif']} "
        f"({result['gains_read']} gains + {result['pertes_read']} pertes lus)."
    )
    if result.get("liste_skipped"):
        print(f"  Liste ignoree (ID non resolu) : {result['liste_skipped']}")
    unresolved = result.get("gain_perte_unresolved") or []
    if unresolved:
        print("  Gains/pertes non resolus :")
        for u in unresolved:
            print(f"    - {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
