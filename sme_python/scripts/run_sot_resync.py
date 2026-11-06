#!/usr/bin/env python3
"""Resync SoT : FK référentiels + projection dossier → carrière."""

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
from sme_gend.services.sot_maintenance import (
    project_carriere_from_dossier_batch,
    resync_fk_referentiels,
    run_full_sot_resync,
)


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('sot',argv)


if __name__ == "__main__":
    raise SystemExit(main())
