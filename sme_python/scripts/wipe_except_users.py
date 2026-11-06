"""Vidage complet MySQL + fichiers métier — garde les utilisateurs."""

from __future__ import annotations

import json
import sys


def main() -> int:
    from sme_gend.config.settings import get_settings
    from sme_gend.services.wipe_operational_data import wipe_all_except_auth_users

    settings = get_settings()
    print(
        f"Base cible : {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}",
        flush=True,
    )
    if "--yes" not in sys.argv:
        print(
            "ATTENTION : toutes les données métier seront supprimées "
            "(agents, SME, référentiels, journaux…).",
            flush=True,
        )
        print("Les comptes utilisateurs (auth) seront conservés.", flush=True)
        rep = input("Tapez OUI pour confirmer : ").strip()
        if rep.upper() != "OUI":
            print("Annulé.", flush=True)
            return 1

    report = wipe_all_except_auth_users(settings)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report.get("non_zero_tables_after"):
        print(
            "Certaines tables contiennent encore des lignes (auth / config attendu).",
            flush=True,
        )
    else:
        print("Toutes les tables MySQL sont à 0 ligne.", flush=True)
    print(
        f"Utilisateurs conservés : {report.get('auth_users_preserved', 0)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
