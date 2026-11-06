#!/usr/bin/env python3
"""Synchronise sexe et matricule militaire depuis LISTE_GENERALE_SEXE_*.xls."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from sme_gend.config import get_settings
from sme_gend.services.personnel_sexe_reference_sync import (
    apply_sexe_sync_plan,
    build_sexe_sync_plan,
)


def _default_feminine() -> Path:
    for p in (
        _ROOT / "LISTE_GENERALE_SEXE_feminin.xls",
        _ROOT.parent / "LISTE_GENERALE_SEXE_feminin.xls",
    ):
        if p.is_file():
            return p
    return _ROOT / "LISTE_GENERALE_SEXE_feminin.xls"


def _default_masculine() -> Path:
    for p in (
        _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls",
        _ROOT.parent / "LISTE_GENERALE_SEXE_Masculin.xls",
    ):
        if p.is_file():
            return p
    return _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls"


def _write_report(plan, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    actions_path = out_dir / "sexe_sync_actions.csv"
    unresolved_path = out_dir / "sexe_sync_unresolved.csv"

    with actions_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(
            [
                "legacy_id",
                "noms",
                "match",
                "status",
                "old_sexe",
                "new_sexe",
                "old_matricule",
                "new_matricule",
                "note",
            ]
        )
        for a in plan.actions:
            w.writerow(
                [
                    a.legacy_access_id,
                    a.noms,
                    a.match_method,
                    a.status,
                    a.old_sexe or "",
                    a.new_sexe or "",
                    a.old_matricule,
                    a.new_matricule or "",
                    a.note,
                ]
            )

    with unresolved_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sexe", "matricule", "noms", "grade", "source"])
        for r in plan.unresolved:
            w.writerow([r.sexe, r.matricule_militaire, r.noms, r.grade, r.source_file])

    (out_dir / "sexe_sync_stats.json").write_text(
        json.dumps(plan.stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Mise à jour sexe / matricule militaire depuis les listes Excel de référence.",
    )
    p.add_argument("--feminin", type=Path, default=None, help="LISTE_GENERALE_SEXE_feminin.xls")
    p.add_argument("--masculin", type=Path, default=None, help="LISTE_GENERALE_SEXE_Masculin.xls")
    p.add_argument(
        "--report-dir",
        type=Path,
        default=_ROOT / "data" / "sexe_sync_reports",
        help="Dossier rapports CSV/JSON (dry-run et apply)",
    )
    p.add_argument("--apply", action="store_true", help="Appliquer les mises à jour en base")
    args = p.parse_args()

    feminin = (args.feminin or _default_feminine()).resolve()
    masculin = (args.masculin or _default_masculine()).resolve()
    for path in (feminin, masculin):
        if not path.is_file():
            print(f"Fichier introuvable : {path}", file=sys.stderr)
            return 1

    settings = get_settings()
    print(f"Lecture reference : {feminin.name} + {masculin.name}")
    plan = build_sexe_sync_plan(settings, feminin, masculin)
    _write_report(plan, args.report_dir.resolve())

    st = plan.stats
    print("\n=== Recapitulatif (dry-run) ===")
    print(f"  References lues     : {st.get('refs_total', 0)} (F={st.get('refs_f', 0)}, M={st.get('refs_m', 0)})")
    print(f"  Agents en base      : {st.get('agents_total', 0)}")
    print(f"  Appariements        : {st.get('matched', 0)}")
    print(f"  Non resolus         : {st.get('unresolved', 0)}")
    print(f"  Mises a jour prevues: {st.get('to_update', 0)}")
    print(f"    - sexe            : {st.get('update_sexe', 0)}")
    print(f"    - matricule       : {st.get('update_matricule', 0)}")
    print(f"  Conflits matricule  : {st.get('conflicts_mat', 0)}")
    print(f"  Doublons ref (F+M)  : {st.get('duplicate_ref_mat', 0)}")
    print(f"\nRapports : {args.report_dir.resolve()}")

    if not args.apply:
        print("\nDry-run uniquement. Relancez avec --apply apres verification des CSV.")
        return 0

    applied = apply_sexe_sync_plan(settings, plan)
    print(
        f"\nApplique — sexe: {applied['sexe']}, matricule: {applied['matricule']}, "
        f"conflits ignores: {applied['skipped_conflict']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
