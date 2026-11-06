#!/usr/bin/env python3
"""Cree sexe_sync_decisions.csv a remplir (colonnes decision + note)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

REPORT = _ROOT / "data" / "sexe_sync_reports"
RAPPROCHE = REPORT / "sexe_sync_rapprochement.csv"
OUT = REPORT / "sexe_sync_decisions.csv"

# Cas deja traites dans le dialogue (legacy_id -> decision)
ALREADY = {
    "25562": ("R", "FAIT"),
    "25572": ("R", "FAIT"),
    "23967": ("R", "FAIT"),
}

FIELDS = [
    "no",
    "categorie",
    "legacy_id",
    "noms",
    "sexe_actuel",
    "mat_actuel",
    "ref_sexe",
    "ref_matricule",
    "ref_noms",
    "decision",
    "note",
    "statut_applique",
]


def _target_id(row: dict[str, str]) -> str:
    leg = (row.get("legacy_id_base") or "").strip()
    if leg:
        return leg
    return (row.get("suggestion_legacy_id") or "").strip()


def main() -> int:
    if not RAPPROCHE.is_file():
        print(f"Manquant : {RAPPROCHE}", file=sys.stderr)
        return 1

    src = list(csv.DictReader(RAPPROCHE.open(encoding="utf-8-sig"), delimiter=";"))
    out_rows: list[dict[str, str]] = []

    for i, row in enumerate(src, start=1):
        leg = _target_id(row)
        prev = ALREADY.get(leg, ("", ""))
        out_rows.append(
            {
                "no": str(i),
                "categorie": row.get("categorie") or "",
                "legacy_id": leg,
                "noms": (row.get("noms_base") or row.get("ref_noms") or "").strip(),
                "sexe_actuel": (row.get("sexe_base") or "").strip(),
                "mat_actuel": (row.get("mat_base") or "").strip(),
                "ref_sexe": (row.get("ref_sexe") or "").strip(),
                "ref_matricule": (row.get("ref_matricule") or "").strip(),
                "ref_noms": (row.get("ref_noms") or "").strip(),
                "decision": prev[0],
                "note": "",
                "statut_applique": prev[1],
            }
        )

    REPORT.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        w.writerows(out_rows)

    n_conflict = sum(1 for r in out_rows if r["categorie"] == "CONFLIT_MAT")
    print(f"Cree : {OUT}")
    print(f"  Lignes : {len(out_rows)} (CONFLIT_MAT={n_conflict})")
    print("  Colonne decision : R = reference Excel | B = garder base | S = passer")
    print("  Puis : python scripts/apply_sexe_decisions.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
