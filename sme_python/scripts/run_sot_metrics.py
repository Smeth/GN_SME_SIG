#!/usr/bin/env python3
"""Métriques SoT — CHECKLIST_CONVERGENCE_SOT §5."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.services.sot_maintenance import collect_sot_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Métriques de contrôle SoT (MySQL)")
    parser.add_argument("--mois", help="Mois SME YYYY-MM (défaut : mois actif session)")
    args = parser.parse_args()
    report = collect_sot_metrics(get_settings(), mois=args.mois)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
