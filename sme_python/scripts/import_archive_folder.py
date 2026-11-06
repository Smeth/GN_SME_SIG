#!/usr/bin/env python3
"""Importe les archives SME (2025–2026) : .mdb complet, .xlsx liste générale seule."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

_MOIS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}


def _norm_token(s: str) -> str:
    t = unicodedata.normalize("NFD", s.strip().lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def mois_from_archive_name(name: str) -> str | None:
    stem = Path(name).stem
    m = re.match(r"^([a-z]+)_(\d{4})$", _norm_token(stem.replace("-", "_")))
    if not m:
        return None
    month_name, year = m.group(1), int(m.group(2))
    mm = _MOIS.get(month_name)
    if not mm:
        return None
    return f"{year:04d}-{mm:02d}"


def collect_jobs(archive_root: Path, years: list[int]) -> list[tuple[str, Path]]:
    jobs: list[tuple[str, Path]] = []
    for year in years:
        folder = archive_root / str(year)
        if not folder.is_dir():
            print(f"Dossier absent : {folder}", file=sys.stderr)
            continue
        for fp in sorted(folder.iterdir()):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in (".mdb", ".accdb", ".xlsx"):
                continue
            ym = mois_from_archive_name(fp.name)
            if not ym:
                print(f"Ignoré (nom non reconnu) : {fp.name}", file=sys.stderr)
                continue
            jobs.append((ym, fp))
    jobs.sort(key=lambda x: x[0])
    return jobs


def _filter_from_mois(jobs: list[tuple[str, Path]], from_mois: str) -> list[tuple[str, Path]]:
    fm = (from_mois or "").strip()
    if not fm:
        return jobs
    return [(ym, fp) for ym, fp in jobs if ym >= fm]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "archive_root",
        type=Path,
        nargs="?",
        default=_ROOT / "Archive",
        help="Racine Archive (sous-dossiers par année)",
    )
    p.add_argument("--years", default="2025,2026", help="Années à importer (ex. 2025,2026)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--from-mois",
        default="",
        help="Reprendre à partir de ce mois YYYY-MM (inclus)",
    )
    args = p.parse_args()

    root = args.archive_root.expanduser().resolve()
    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    jobs = _filter_from_mois(collect_jobs(root, years), args.from_mois)
    if not jobs:
        print("Aucun fichier à importer.", file=sys.stderr)
        return 1

    print(f"Archive : {root}")
    print(f"Fichiers ({len(jobs)}) :")
    for ym, fp in jobs:
        kind = "MDB complet" if fp.suffix.lower() in (".mdb", ".accdb") else "XLSX liste seule"
        print(f"  {ym}  {fp.name}  ({kind})")

    if args.dry_run:
        return 0

    from sme_gend.config import get_settings
    from sme_gend.config.settings import Settings
    from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access
    from sme_gend.services.sme_excel_bundle_import import import_excel_bundle_for_month

    mysql_st = get_settings()
    results: list[dict] = []

    for i, (ym, fp) in enumerate(jobs, 1):
        print(f"\n=== [{i}/{len(jobs)}] {ym} — {fp.name} ===", flush=True)

        def progress(msg: str) -> None:
            print(f"  ... {msg}", flush=True)

        try:
            if fp.suffix.lower() in (".mdb", ".accdb"):
                access_st = Settings(
                    database_path=fp,
                    mysql_host=mysql_st.mysql_host,
                    mysql_port=mysql_st.mysql_port,
                    mysql_database=mysql_st.mysql_database,
                    mysql_user=mysql_st.mysql_user,
                    mysql_password=mysql_st.mysql_password,
                )
                r = import_full_mysql_bundle_from_access(
                    access_st,
                    mysql_st,
                    mois=ym,
                    progress=progress,
                )
                results.append({"mois": ym, "file": fp.name, "type": "mdb", **r})
                print(
                    f"OK MDB — insérés {r.get('liste_inserted', 0)}, "
                    f"MAJ {r.get('liste_updated', 0)}",
                    flush=True,
                )
            else:
                r = import_excel_bundle_for_month(
                    mysql_st,
                    fp,
                    mois=ym,
                    progress=progress,
                    liste_generale_only=True,
                )
                results.append({"mois": ym, "file": fp.name, "type": "xlsx", **r})
                print(
                    f"OK XLSX LG — {r.get('liste_resolved', 0)}/{r.get('liste_read', 0)} agents, "
                    f"+{r.get('liste_inserted', 0)} / ~{r.get('liste_updated', 0)} MAJ",
                    flush=True,
                )
        except Exception as exc:
            print(f"ERREUR {ym} {fp.name}: {exc}", file=sys.stderr, flush=True)
            results.append({"mois": ym, "file": fp.name, "error": str(exc)})
            return 2

    last_mois = jobs[-1][0]
    try:
        from sme_gend.config.sme_context import load_sme_context, save_sme_context
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService

        ctx = load_sme_context()
        ctx.mois_actif = last_mois
        save_sme_context(ctx)
        ws = SmeWorkspaceService(mysql_st)
        payload = ws.load_workspace_payload()
        payload["mois_actif"] = last_mois
        ws.save_workspace_payload(payload)
        print(f"\nMois actif SME défini sur {last_mois}.", flush=True)
    except Exception as exc:
        print(f"Note : mois actif non mis à jour ({exc})", file=sys.stderr)

    ok = sum(1 for r in results if "error" not in r)
    print(f"\nTerminé : {ok}/{len(jobs)} imports réussis.", flush=True)
    return 0 if ok == len(jobs) else 2


if __name__ == "__main__":
    raise SystemExit(main())
