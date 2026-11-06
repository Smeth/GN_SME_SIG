#!/usr/bin/env python3
"""Aligne liste_present_layout (MySQL) sur l'ordre des rubriques d'une maquette Excel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.config.settings import default_modele_path, project_root
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_layout_xlsx_sync import apply_xlsx_subdiv_order_to_layout


def _default_xlsx() -> Path:
    for candidate in (
        project_root() / "liste_des_presents_mai.xlsx",
        default_modele_path("liste_des_presents.xlsx"),
    ):
        if candidate.is_file():
            return candidate
    return project_root() / "liste_des_presents_mai.xlsx"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", type=Path, default=None, help="Maquette Excel de référence")
    ap.add_argument(
        "--no-reorder-sections",
        action="store_true",
        help="Ne pas réordonner les sections selon l'Excel",
    )
    args = ap.parse_args()
    xlsx = args.xlsx or _default_xlsx()
    if not xlsx.is_file():
        print(f"Maquette introuvable : {xlsx}", file=sys.stderr)
        return 1

    settings = get_settings()
    layout_svc = LayoutConfigService(settings)
    cfg = layout_svc.load()
    updated, warnings = apply_xlsx_subdiv_order_to_layout(
        cfg,
        settings,
        xlsx,
        reorder_sections=not args.no_reorder_sections,
    )
    layout_svc.save(updated)
    n_sub = sum(len(s.subdivision_order or []) for s in updated.sections)
    print(f"Layout enregistré — {len(updated.sections)} sections, {n_sub} rubriques subdiv.")
    print(f"Source : {xlsx}")
    if warnings:
        print(f"\n{len(warnings)} avertissement(s) :")
        for w in warnings[:20]:
            print(f"  • {w}")
        if len(warnings) > 20:
            print(f"  … +{len(warnings) - 20}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
