"""Régénère `data/liste_present_layout.json` depuis `ordre_general.txt`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from sme_gend.config.settings import _project_root, liste_layout_config_path  # noqa: E402
from sme_gend.services.ordre_general_parser import (  # noqa: E402
    build_layout_config_from_ordre_general_path,
    default_ordre_general_paths,
)


def main() -> int:
    root = _project_root()
    src: Path | None = None
    for p in default_ordre_general_paths(root):
        if p.is_file():
            src = p
            break
    if src is None:
        print("Aucun ordre_general.txt trouvé (data/ ni dossier parent).", file=sys.stderr)
        return 1
    cfg = build_layout_config_from_ordre_general_path(src)
    out = liste_layout_config_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"OK: {src} -> {out} ({len(cfg.sections)} sections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
