#!/usr/bin/env python3
"""Remise en cohérence SoT pour un mois SME (maintenance + projection complète)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.services.referentiel_maintenance import run_referentiel_maintenance
from sme_gend.services.sot_maintenance import (
    project_carriere_from_dossier_batch,
    resync_fk_referentiels,
)
from sme_gend.services.sot_status_service import fetch_sot_status_summary


def _log(msg: str) -> None:
    print(msg, flush=True)


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('coherence',argv)


if __name__ == "__main__":
    raise SystemExit(main())
