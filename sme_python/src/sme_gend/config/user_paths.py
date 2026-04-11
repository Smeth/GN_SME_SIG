"""Fichier utilisateur : chemins base Access et maquette Excel (réseau / partage)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "SME_Gendarmerie"
    return Path.home() / ".config" / "sme-gendarmerie"


def user_paths_config_file() -> Path:
    """Fichier JSON persistant (hors dépôt Git)."""
    return _config_dir() / "paths.json"


@dataclass(slots=True)
class UserPathsOverrides:
    """Valeurs lues depuis paths.json (chaînes brutes)."""

    database_path: str | None = None
    template_excel_path: str | None = None
    template_numerique_path: str | None = None
    template_detaches_path: str | None = None


def load_user_paths() -> UserPathsOverrides:
    p = user_paths_config_file()
    if not p.is_file():
        return UserPathsOverrides()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return UserPathsOverrides()
    if not isinstance(raw, dict):
        return UserPathsOverrides()
    db = raw.get("database_path")
    tpl = raw.get("template_excel_path")
    tnum = raw.get("template_numerique_path")
    tdet = raw.get("template_detaches_path")
    return UserPathsOverrides(
        database_path=str(db).strip() if db else None,
        template_excel_path=str(tpl).strip() if tpl else None,
        template_numerique_path=str(tnum).strip() if tnum else None,
        template_detaches_path=str(tdet).strip() if tdet else None,
    )


def save_user_paths(
    database_path: str | None,
    template_excel_path: str | None,
    template_numerique_path: str | None = None,
    template_detaches_path: str | None = None,
) -> None:
    """
    Enregistre les chemins. Chaînes vides ou None : la clé n'est pas stockée.
    Si le fichier devient vide, il est supprimé.
    """
    path = user_paths_config_file()
    data: dict[str, str] = {}
    if database_path and str(database_path).strip():
        data["database_path"] = str(database_path).strip()
    if template_excel_path and str(template_excel_path).strip():
        data["template_excel_path"] = str(template_excel_path).strip()
    if template_numerique_path and str(template_numerique_path).strip():
        data["template_numerique_path"] = str(template_numerique_path).strip()
    if template_detaches_path and str(template_detaches_path).strip():
        data["template_detaches_path"] = str(template_detaches_path).strip()
    if not data:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
