#!/usr/bin/env python3
"""Régénère data/liste_present_layout.json depuis ref_structure + ref_subdivision_arme."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.config.settings import liste_layout_config_path
from sme_gend.services.referentiel_maintenance import rebuild_liste_present_layout


def main() -> int:
    settings = get_settings()
    cfg = rebuild_liste_present_layout(settings)
    out = liste_layout_config_path()
    n_sub = sum(len(s.subdivision_order or []) for s in cfg.sections)
    print(
        json.dumps(
            {
                "path": str(out),
                "sections": len(cfg.sections),
                "subdivisions": n_sub,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
