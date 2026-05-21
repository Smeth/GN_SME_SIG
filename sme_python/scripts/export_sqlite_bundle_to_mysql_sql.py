#!/usr/bin/env python3
"""
Export vers INSERT MySQL (schéma data/schema/mysql_sme_cible.sql).

Mode 1 — une base SQLite + mois explicite ::
    python scripts/export_sqlite_bundle_to_mysql_sql.py \\
        --sqlite \"C:/chemin/liste.sqlite\" --periode 2026-04 --out donnees.sql

Mode 2 — toutes les archives importées (dossier sme_archives de l'app) ::
    python scripts/export_sqlite_bundle_to_mysql_sql.py --archives --out tout.sql

    Les fichiers ``sme_bundle_*.{sqlite,db,mdb,accdb}`` sont fusionnés : référentiels
    et personnes uniques, un ``import_lot`` par fichier, ``carriere_situation`` et
    satellites par mois / archive.

    Dossier par défaut : ``resolved_sme_archives_directory()`` (paths.json, ou
    %LOCALAPPDATA%/SME_Gendarmerie/sme_archives). Surcharge : ``--archives-dir``.

Lancer depuis le répertoire ``sme_python`` (ou avec PYTHONPATH=src).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.services.mysql_sql_export import (  # noqa: E402
    archive_backend,
    export_archives_directory,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export SME (SQLite / archives) → script INSERT MySQL"
    )
    ap.add_argument(
        "--archives",
        action="store_true",
        help="Parcourir le dossier des archives clôturées/importées (sme_bundle_*)",
    )
    ap.add_argument(
        "--archives-dir",
        type=Path,
        default=None,
        help="Dossier des archives (défaut : emplacement applicatif comme dans l'app)",
    )
    ap.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Un fichier bundle SQLite (mode fichier unique)",
    )
    ap.add_argument(
        "--periode",
        default=None,
        help="Mois SME YYYY-MM (obligatoire avec --sqlite)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("donnees_sme_export.sql"),
        help="Fichier SQL de sortie",
    )
    ap.add_argument(
        "--database",
        default="sme_gend_cible",
        help="Nom de la base MySQL cible (ex. smegend)",
    )
    args = ap.parse_args()
    db_name = (args.database or "sme_gend_cible").strip()

    if args.archives:
        export_archives_directory(
            args.out.expanduser().resolve(),
            arch_dir=args.archives_dir,
            database_name=db_name,
        )
        return

    if not args.sqlite or not args.periode:
        ap.error("Sans --archives : indiquez --sqlite et --periode YYYY-MM")

    ym = re.match(r"^(\d{4})-(\d{2})$", (args.periode or "").strip())
    if not ym:
        raise SystemExit("--periode doit être YYYY-MM")
    m = int(ym.group(2))
    if not (1 <= m <= 12):
        raise SystemExit("mois invalide")

    p = args.sqlite.expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"Fichier introuvable : {p}")

    kind = archive_backend(p)
    if kind != "sqlite":
        raise SystemExit(
            "Pour un fichier .mdb/.accdb seul, utilisez plutôt --archives "
            "(import copié dans sme_archives) ou ajoutez le fichier dans ce dossier "
            "avec le nommage sme_bundle_<mois>_*.mdb"
        )

    export_archives_directory(
        args.out.expanduser().resolve(),
        jobs_override=[(p, args.periode.strip(), "sqlite")],
        database_name=db_name,
    )


if __name__ == "__main__":
    main()
