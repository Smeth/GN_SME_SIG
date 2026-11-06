"""Fichier utilisateur : chemins base Access et maquette Excel (réseau / partage)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _config_dir() -> Path:
    from sme_gend.config.branding import LEGACY_USER_DATA_DIR_NAME, USER_DATA_DIR_NAME

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            new_dir = Path(base) / USER_DATA_DIR_NAME
            legacy_dir = Path(base) / LEGACY_USER_DATA_DIR_NAME
            if legacy_dir.is_dir() and not new_dir.is_dir():
                return legacy_dir
            return new_dir
    return Path.home() / ".config" / "sgi-dpf"


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
    mysql_host: str | None = None
    mysql_port: int | None = None
    mysql_database: str | None = None
    mysql_user: str | None = None
    mysql_password: str | None = None
    # Dossier archives clôture (vide = défaut profil sme_archives).
    sme_archives_path: str | None = None
    # Racine médias fiches photos/ et grades/ (vide = défaut personnel_media).
    personnel_media_root_path: str | None = None
    personnel_media_local_override: bool = False


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
    arch = raw.get("sme_archives_path")
    media = raw.get("personnel_media_root_path")
    mhost = raw.get("mysql_host")
    mport = raw.get("mysql_port")
    mdb = raw.get("mysql_database")
    muser = raw.get("mysql_user")
    mpwd = raw.get("mysql_password")
    port_i: int | None = None
    if mport is not None:
        try:
            port_i = int(mport)
        except (TypeError, ValueError):
            port_i = None
    return UserPathsOverrides(
        database_path=str(db).strip() if db else None,
        template_excel_path=str(tpl).strip() if tpl else None,
        template_numerique_path=str(tnum).strip() if tnum else None,
        template_detaches_path=str(tdet).strip() if tdet else None,
        sme_archives_path=str(arch).strip() if arch else None,
        personnel_media_root_path=str(media).strip() if media else None,
        personnel_media_local_override=raw.get("personnel_media_local_override") is True,
        mysql_host=str(mhost).strip() if mhost else None,
        mysql_port=port_i,
        mysql_database=str(mdb).strip() if mdb else None,
        mysql_user=str(muser).strip() if muser else None,
        mysql_password=str(mpwd) if mpwd is not None else None,
    )


def save_user_paths(
    database_path: str | None,
    template_excel_path: str | None,
    template_numerique_path: str | None = None,
    template_detaches_path: str | None = None,
    *,
    sme_archives_path: str | None = None,
    personnel_media_root_path: str | None = None,
    personnel_media_local_override: bool = False,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_user: str | None = None,
    mysql_password: str | None = None,
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
    if sme_archives_path and str(sme_archives_path).strip():
        data["sme_archives_path"] = str(sme_archives_path).strip()
    if personnel_media_root_path and str(personnel_media_root_path).strip():
        data["personnel_media_root_path"] = str(personnel_media_root_path).strip()
        if personnel_media_local_override:
            data["personnel_media_local_override"] = True
    if mysql_host and str(mysql_host).strip():
        data["mysql_host"] = str(mysql_host).strip()
    if mysql_port is not None:
        data["mysql_port"] = int(mysql_port)
    if mysql_database and str(mysql_database).strip():
        data["mysql_database"] = str(mysql_database).strip()
    if mysql_user and str(mysql_user).strip():
        data["mysql_user"] = str(mysql_user).strip()
    if mysql_password is not None and str(mysql_password):
        data["mysql_password"] = str(mysql_password)
    if not data:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    from sme_gend.services.safe_paths import atomic_write
    atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def user_has_mysql_profile() -> bool:
    """True si paths.json ou SME_MYSQL_* définit au moins l'hôte MySQL."""
    if any(
        os.environ.get(k, "").strip()
        for k in (
            "SME_MYSQL_HOST",
            "SME_MYSQL_DATABASE",
            "SME_MYSQL_USER",
            "SME_MYSQL_PASSWORD",
        )
    ):
        return True
    u = load_user_paths()
    return bool(
        (u.mysql_host or "").strip()
        or (u.mysql_database or "").strip()
        or (u.mysql_user or "").strip()
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


def _bundled_personnel_media_root() -> Path | None:
    """Dossier ``personnel_media`` livré avec l'exécutable portable (insignes de grade)."""
    if not getattr(sys, "frozen", False):
        return None
    candidates: list[Path] = []
    try:
        from sme_gend.config.settings import project_root

        candidates.append(project_root() / "personnel_media")
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "personnel_media")
    for root in candidates:
        grades = root / "grades"
        if grades.is_dir() and any(grades.iterdir()):
            return root.resolve()
    return None


def resolved_personnel_media_root() -> Path:
    """
    Racine des médias fiches (sous-dossiers ``photos/``, ``grades/``).
    Priorité : ``SME_PERSONNEL_MEDIA_PATH``, puis ``paths.json``, puis insignes livrés
    avec l'exe portable, puis profil Windows par défaut.
    """
    env = os.environ.get("SME_PERSONNEL_MEDIA_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    u = load_user_paths()
    from sme_gend.services.shared_media import shared_media_root
    if u.personnel_media_local_override and u.personnel_media_root_path:
        return Path(u.personnel_media_root_path).expanduser().resolve()
    shared = shared_media_root()
    if shared is not None:
        return shared
    if u.personnel_media_root_path and str(u.personnel_media_root_path).strip():
        return Path(u.personnel_media_root_path).expanduser().resolve()
    bundled = _bundled_personnel_media_root()
    if bundled is not None:
        return bundled
    return (user_application_data_dir() / "personnel_media").resolve()
