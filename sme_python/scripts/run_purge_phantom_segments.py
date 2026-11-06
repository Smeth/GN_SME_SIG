#!/usr/bin/env python3
"""Purge segments carrière fantômes + vérifie l'effectif liste."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.services.sot_maintenance import purge_phantom_carriere_segments


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('purge',argv)


if __name__ == "__main__":
    raise SystemExit(main())
