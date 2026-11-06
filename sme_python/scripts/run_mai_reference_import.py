#!/usr/bin/env python3
"""
Import de référence Access → MySQL (mai) avec purge ciblée période + référentiels.

Usage :
  python scripts/run_mai_reference_import.py
  python scripts/run_mai_reference_import.py --mdb smegend_mai.mdb --mois 2026-05
  python scripts/run_mai_reference_import.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DEFAULT_MDB = _ROOT / "smegend_mai.mdb"
DEFAULT_MOIS = "2026-05"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Purge ciblée + import bundle Access (liste, référentiels, satellites)"
    )
    parser.add_argument(
        "--mdb",
        type=Path,
        default=DEFAULT_MDB,
        help=f"Fichier Access source (défaut : {DEFAULT_MDB.name})",
    )
    parser.add_argument("--mois", default=DEFAULT_MOIS, help="Mois SME YYYY-MM (défaut : 2026-05)")
    parser.add_argument(
        "--no-purge",
        action="store_true",
        help="Ne pas effacer la période MySQL avant import",
    )
    parser.add_argument(
        "--no-set-active",
        action="store_true",
        help="Ne pas mettre à jour sme_context.mois_actif",
    )
    parser.add_argument("--dry-run", action="store_true", help="Vérifications uniquement")
    args = parser.parse_args()

    mdb = args.mdb.expanduser().resolve()
    if not mdb.is_file():
        print(f"ERREUR : fichier Access introuvable : {mdb}", file=sys.stderr)
        return 1

    from sme_gend.config import get_settings
    from sme_gend.config.sme_context import load_sme_context, save_sme_context
    from sme_gend.services.access_odbc import preflight_access_file
    from sme_gend.services.mysql_import_period import (
        normalize_import_mois,
        purge_sme_periode_for_reimport,
    )

    ym = normalize_import_mois(args.mois)
    ok, msg = preflight_access_file(str(mdb))
    if not ok:
        print(f"ERREUR ODBC : {msg}", file=sys.stderr)
        return 1

    settings = get_settings()
    access_settings = replace(settings, database_path=mdb)

    print(f"Source Access : {mdb}")
    print(f"Mois import   : {ym}")
    print(f"MySQL         : {settings.mysql_host}/{settings.mysql_database}")

    if args.dry_run:
        print("Dry-run OK — aucune modification.")
        return 0

    purge_stats: dict | None = None
    if not args.no_purge:
        print(f"\n--- Purge MySQL ({ym}) ---")
        purge_stats = purge_sme_periode_for_reimport(settings, ym)
        print(json.dumps(purge_stats, indent=2, ensure_ascii=False))

    if not args.no_set_active:
        ctx = load_sme_context()
        ctx.mois_actif = ym
        if ym not in (ctx.mois_fermes or []):
            pass
        save_sme_context(ctx, promote_operational_month=True)
        print(f"Mois actif session -> {ym}")

    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.mysql_import_period import ensure_sme_periode

    with MySQLConnection(settings).connect() as conn:
        ensure_sme_periode(conn, ym, statut="Ouvert")

    print(f"\n--- Import bundle ({ym}) ---")
    from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access

    def _progress(label: str) -> None:
        print(f"  … {label}")

    result = import_full_mysql_bundle_from_access(
        access_settings,
        settings,
        progress=_progress,
        mois=ym,
    )
    keys = (
        "sme_mois",
        "sme_periode_id",
        "import_lot_id",
        "historical",
        "liste_inserted",
        "liste_updated",
        "referentials",
        "carriere_reconciliation",
        "dossier_projection",
    )
    summary = {k: result.get(k) for k in keys if k in result}
    ref_counts = (result.get("referentials") or {}).get("counts")
    if ref_counts:
        summary["ref_counts"] = ref_counts

    print("\n--- Résultat import ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    from sme_gend.services.sot_maintenance import collect_sot_metrics

    metrics = collect_sot_metrics(settings, mois=ym)
    print("\n--- Métriques SoT ---")
    print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False, default=str))

    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
