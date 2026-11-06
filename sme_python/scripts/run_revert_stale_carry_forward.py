#!/usr/bin/env python3
"""Annule les reports carriere depuis un historique trop ancien (sorties / deces)."""

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
from sme_gend.services.sot_maintenance import collect_sot_metrics, revert_stale_carry_forward


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('revert',argv)


if __name__ == "__main__":
    raise SystemExit(main())
