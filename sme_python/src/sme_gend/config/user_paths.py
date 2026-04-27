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


def user_application_data_dir() -> Path:
    """Dossier applicatif utilisateur : ``paths.json``, ``sme_context.json``, etc."""
    return _config_dir()


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
    sqlite_liste_path: str | None = None
    use_sqlite_liste_generale: bool = False
    # Dossier archives clôture (vide = défaut profil sme_archives).
    sme_archives_path: str | None = None
    # Racine médias fiches photos/ et grades/ (vide = défaut personnel_media).
    personnel_media_root_path: str | None = None


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
    sqlt = raw.get("sqlite_liste_path")
    use_sql = raw.get("use_sqlite_liste_generale")
    if isinstance(use_sql, str):
        use_sqlite = use_sql.strip().lower() in ("1", "true", "yes", "oui")
    elif use_sql is None:
        use_sqlite = False
    else:
        use_sqlite = bool(use_sql)
    arch = raw.get("sme_archives_path")
    media = raw.get("personnel_media_root_path")
    return UserPathsOverrides(
        database_path=str(db).strip() if db else None,
        template_excel_path=str(tpl).strip() if tpl else None,
        template_numerique_path=str(tnum).strip() if tnum else None,
        template_detaches_path=str(tdet).strip() if tdet else None,
        sqlite_liste_path=str(sqlt).strip() if sqlt else None,
        use_sqlite_liste_generale=use_sqlite,
        sme_archives_path=str(arch).strip() if arch else None,
        personnel_media_root_path=str(media).strip() if media else None,
    )


def save_user_paths(
    database_path: str | None,
    template_excel_path: str | None,
    template_numerique_path: str | None = None,
    template_detaches_path: str | None = None,
    *,
    sqlite_liste_path: str | None = None,
    use_sqlite_liste_generale: bool | None = None,
    sme_archives_path: str | None = None,
    personnel_media_root_path: str | None = None,
) -> None:
    """
    Enregistre les chemins. Chaînes vides ou None : la clé n'est pas stockée.
    Si le fichier devient vide, il est supprimé.
    """
    path = user_paths_config_file()
    data: dict[str, str | bool] = {}
    if database_path and str(database_path).strip():
        data["database_path"] = str(database_path).strip()
    if template_excel_path and str(template_excel_path).strip():
        data["template_excel_path"] = str(template_excel_path).strip()
    if template_numerique_path and str(template_numerique_path).strip():
        data["template_numerique_path"] = str(template_numerique_path).strip()
    if template_detaches_path and str(template_detaches_path).strip():
        data["template_detaches_path"] = str(template_detaches_path).strip()
    if sqlite_liste_path and str(sqlite_liste_path).strip():
        data["sqlite_liste_path"] = str(sqlite_liste_path).strip()
    if use_sqlite_liste_generale is not None:
        data["use_sqlite_liste_generale"] = bool(use_sqlite_liste_generale)
    if sme_archives_path and str(sme_archives_path).strip():
        data["sme_archives_path"] = str(sme_archives_path).strip()
    if personnel_media_root_path and str(personnel_media_root_path).strip():
        data["personnel_media_root_path"] = str(personnel_media_root_path).strip()
    if not data:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def resolved_sme_archives_directory() -> Path:
    """
    Dossier des archives de clôture. Priorité : ``SME_ARCHIVES_PATH``, puis ``paths.json``,
    puis ``%LOCALAPPDATA%/SME_Gendarmerie/sme_archives``.
    """
    env = os.environ.get("SME_ARCHIVES_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    u = load_user_paths()
    if u.sme_archives_path and str(u.sme_archives_path).strip():
        return Path(u.sme_archives_path).expanduser().resolve()
    return (user_application_data_dir() / "sme_archives").resolve()


def resolved_personnel_media_root() -> Path:
    """
    Racine des médias fiches (sous-dossiers ``photos/``, ``grades/``).
    Priorité : ``SME_PERSONNEL_MEDIA_PATH``, puis ``paths.json``, puis profil par défaut.
    """
    env = os.environ.get("SME_PERSONNEL_MEDIA_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    u = load_user_paths()
    if u.personnel_media_root_path and str(u.personnel_media_root_path).strip():
        return Path(u.personnel_media_root_path).expanduser().resolve()
    return (user_application_data_dir() / "personnel_media").resolve()
