#!/usr/bin/env python3
"""
Maintenance référentiels post-migration :
  - migration nœuds org mal placés → branche _ORG
  - backfill RSA depuis carrière
  - régénération liste_present_layout.json
  - resync FK + projection dossier → carrière (P2.4)
  - suppression tables tab_* legacy (optionnel)
"""

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
from sme_gend.services.referentiel_maintenance import run_referentiel_maintenance


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('reference',argv)


if __name__ == "__main__":
    raise SystemExit(main())
