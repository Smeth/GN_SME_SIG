#!/usr/bin/env python3
"""Backfill ref_* + FK carrière sur la base MySQL configurée."""

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
from sme_gend.services.mysql_ref_backfill import run_mysql_ref_backfill


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill ETL ref_* / carrière / mouvements")
    parser.add_argument(
        "--no-mouvements",
        action="store_true",
        help="Ne pas reconstruire mouvement_effectif depuis tab_gain_perte",
    )
    args = parser.parse_args()
    settings = get_settings()
    stats = run_mysql_ref_backfill(settings, rebuild_mouvements=not args.no_mouvements)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
