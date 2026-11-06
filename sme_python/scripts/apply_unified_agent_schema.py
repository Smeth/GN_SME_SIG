#!/usr/bin/env python3
"""Applique le schéma unifié agent et réimporte Access si besoin."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.repositories.mysql_common import apply_mysql_schema
from sme_gend.services.mysql_schema_migrate import (
    snapshot_liste_generale,
    import_liste_snapshot_to_agent,
)
from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access


def main() -> None:
    s = get_settings()
    snap = snapshot_liste_generale(s)
    print(f"Snapshot liste_generale : {len(snap)} lignes")
    apply_mysql_schema(s)
    print("Schéma unifié appliqué.")
    if snap:
        a, c = import_liste_snapshot_to_agent(s, snap)
        print(f"Import snapshot vers agent : {a} agents, {c} carrieres")
    with MySQLConnection(s).connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent")
            n = int(cur.fetchone()[0])
    if n == 0 and s.database_path.is_file():
        from sme_gend.config.sme_context import load_sme_context

        mois = (load_sme_context().mois_actif or "").strip() or None
        if not mois:
            import argparse
            from datetime import date

            p = argparse.ArgumentParser()
            p.add_argument("--mois", help="Période YYYY-MM de l'import Access")
            args, _ = p.parse_known_args()
            mois = args.mois or date.today().strftime("%Y-%m")
        print(f"Import Access (période {mois})…")
        r = import_full_mysql_bundle_from_access(s, s, mois=mois)
        print(
            f"Import {r.get('sme_mois')} : +{r['liste_inserted']} / ~{r['liste_updated']} mises à jour"
        )
    with MySQLConnection(s).connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent")
            print("Agents :", cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM carriere_situation WHERE est_actuelle = 1")
            print("Carrières actuelles :", cur.fetchone()[0])


if __name__ == "__main__":
    main()
