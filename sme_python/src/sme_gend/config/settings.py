"""Configuration centralisée (chemins base Access, maquette Excel)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sme_gend.config.user_paths import UserPathsOverrides, load_user_paths


def _project_root() -> Path:
    """
    Racine applicative : dossier qui contient `data/`.

    En développement : répertoire `sme_python` (parent de `src/`).
    Avec PyInstaller (portable) : dossier où se trouve l’exécutable `.exe`.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def project_root() -> Path:
    """Racine applicative (``data/``, ``modeles/``, schémas SQL, etc.)."""
    return _project_root()


def modeles_dir() -> Path:
    """Gabarits Excel/Word et fichiers métier statiques (maquettes d'impression)."""
    return _project_root() / "modeles"


def _legacy_modele_candidates(*names: str) -> list[Path]:
    """Ordre de recherche : ``modeles/`` puis anciens emplacements (migration)."""
    root = _project_root()
    parent = root.parent
    out: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        if not name:
            continue
        for candidate in (
            modeles_dir() / name,
            root / "data" / "templates" / name,
            root / "data" / name,
            parent / name,
        ):
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def resolve_modele_file(*names: str) -> Path | None:
    """Premier gabarit existant parmi les noms proposés (ex. ``numerique.xlsx``)."""
    for candidate in _legacy_modele_candidates(*names):
        if candidate.is_file():
            return candidate
    return None


def default_modele_path(name: str) -> Path:
    """Chemin cible recommandé pour un gabarit (dossier ``modeles/``)."""
    return modeles_dir() / name


def _candidate_mdb_paths() -> list[Path]:
    root = _project_root()
    return [
        root / "data" / "smegend.mdb",
        modeles_dir() / "smegend.mdb",
        root.parent / "smegend.mdb",
    ]


def _default_mdb_path() -> Path | None:
    for candidate in _candidate_mdb_paths():
        if candidate.is_file():
            return candidate
    return None


def resolve_access_template_mdb() -> Path:
    """Modèle .mdb vierge pour export bundle MySQL → Access."""
    found = _default_mdb_path()
    if found is not None:
        return found
    return modeles_dir() / "smegend.mdb"


def _candidate_template_paths() -> list[Path]:
    """Maquette liste des présents (``liste_des_presents.xlsx``, puis ``resultat.xlsx``)."""
    root = _project_root()
    extra = [root / "liste_des_presents_mai.xlsx"]
    return extra + _legacy_modele_candidates(
        "liste_des_presents.xlsx",
        "liste_des_presents_mai.xlsx",
        "resultat.xlsx",
    )


def _candidate_numerique_template_paths() -> list[Path]:
    """Official workbook, before the superseded numerical template."""
    root = _project_root()
    return [root.parent / "SME_GEND_model.xlsx", root / "SME_GEND_model.xlsx",
            *_legacy_modele_candidates("SME_GEND_model.xlsx", "numerique.xlsx", "numérique.xlsx")]


def _candidate_detaches_paths() -> list[Path]:
    """Fichier officiel ``DETACHES.xlsx`` (liste des détachés)."""
    return _legacy_modele_candidates("DETACHES.xlsx")


def liste_layout_config_path() -> Path:
    """Fichier JSON : ordre des sections, titres, liaison structures."""
    return _project_root() / "data" / "liste_present_layout.json"


def detaches_config_path() -> Path:
    """Fichier JSON : sigles → titres Excel, ordre, options génération feuille DETACHE."""
    return _project_root() / "data" / "detaches_config.json"


def _resolve_database_path(user: UserPathsOverrides) -> Path:
    """
    Chemin du fichier Access (import depuis l’ancien système uniquement).

    Non obligatoire au démarrage : l’exploitation courante utilise MySQL.
    """
    env = os.environ.get("SMEDB_PATH", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path

    if user.database_path:
        p = Path(user.database_path).expanduser()
        if p.is_file():
            return p

    default = _default_mdb_path()
    if default is not None:
        return default

    if user.database_path and str(user.database_path).strip():
        return Path(user.database_path).expanduser()
    return _project_root() / "data" / "smegend.mdb"


def user_paths_config_file_hint() -> str:
    from sme_gend.config.user_paths import user_paths_config_file

    return str(user_paths_config_file())


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_bool_override(name: str) -> bool | None:
    """Si la variable est définie, retourne True/False ; sinon None (laisser paths.json)."""
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return None
    if v in ("1", "true", "yes", "oui"):
        return True
    if v in ("0", "false", "no", "non"):
        return False
    return None


def _user_mysql_config_in_paths_json(user: UserPathsOverrides) -> bool:
    """True si paths.json définit au moins un paramètre MySQL (prioritaire sur SME_MYSQL_*)."""
    return any(
        x is not None
        for x in (
            user.mysql_host,
            user.mysql_port,
            user.mysql_database,
            user.mysql_user,
            user.mysql_password,
        )
    )


def _resolve_mysql_connection(user: UserPathsOverrides) -> tuple[str, int, str, str, str]:
    """
    host, port, database, user, password.
    Priorité : paths.json (enregistré dans Paramètres) puis variables SME_MYSQL_*.
    """
    if _user_mysql_config_in_paths_json(user):
        return (
            (user.mysql_host or "127.0.0.1").strip(),
            int(user.mysql_port) if user.mysql_port is not None else 3306,
            (user.mysql_database or "smegend").strip(),
            (user.mysql_user or "root").strip(),
            user.mysql_password or "",
        )
    host = os.environ.get("SME_MYSQL_HOST", "").strip() or "127.0.0.1"
    port = _env_int("SME_MYSQL_PORT", 3306)
    database = os.environ.get("SME_MYSQL_DATABASE", "").strip() or "smegend"
    mysql_user = os.environ.get("SME_MYSQL_USER", "").strip() or "root"
    mysql_password = os.environ.get("SME_MYSQL_PASSWORD", "")
    return host, port, database, mysql_user, mysql_password


def mysql_config_source_label(user: UserPathsOverrides | None = None) -> str:
    """Libellé pour la barre d'état : origine des paramètres MySQL."""
    u = user if user is not None else load_user_paths()
    if _user_mysql_config_in_paths_json(u):
        return "profil utilisateur (paths.json)"
    if any(os.environ.get(k, "").strip() for k in (
        "SME_MYSQL_HOST", "SME_MYSQL_PORT", "SME_MYSQL_DATABASE",
        "SME_MYSQL_USER", "SME_MYSQL_PASSWORD",
    )):
        return "variables d'environnement SME_MYSQL_*"
    return "valeurs par défaut"


def _resolve_template_path(user: UserPathsOverrides) -> Path | None:
    """
    Ordre :
    1. SME_TEMPLATE_PATH
    2. chemins enregistrés dans paths.json
    3. ``modeles/liste_des_presents.xlsx`` (puis ``resultat.xlsx``, anciens emplacements)
    """
    env = os.environ.get("SME_TEMPLATE_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if user.template_excel_path:
        p = Path(user.template_excel_path).expanduser()
        if p.is_file():
            return p

    for candidate in _candidate_template_paths():
        if candidate.is_file():
            return candidate
    return None


def _resolve_numerique_template_path(user: UserPathsOverrides) -> Path | None:
    """
    Ordre :
    1. SME_NUMERIQUE_TEMPLATE_PATH
    2. paths.json → template_numerique_path
    3. ``SME_GEND_model.xlsx`` ; migration de l'ancien ``numerique.xlsx``
    """
    env = os.environ.get("SME_NUMERIQUE_TEMPLATE_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if user.template_numerique_path:
        p = Path(user.template_numerique_path).expanduser()
        if p.name.casefold() in ("numerique.xlsx", "numérique.xlsx"):
            for candidate in _candidate_numerique_template_paths():
                if candidate.name == "SME_GEND_model.xlsx" and candidate.is_file():
                    return candidate
        if p.is_file():
            return p

    for candidate in _candidate_numerique_template_paths():
        if candidate.is_file():
            return candidate
    return None


def _resolve_detaches_path(user: UserPathsOverrides) -> Path | None:
    """
    Ordre :
    1. SME_DETACHES_PATH
    2. paths.json → template_detaches_path
    3. ``modeles/DETACHES.xlsx`` (puis anciens emplacements)
    """
    env = os.environ.get("SME_DETACHES_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if user.template_detaches_path:
        p = Path(user.template_detaches_path).expanduser()
        if p.is_file():
            return p

    for candidate in _candidate_detaches_paths():
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class Settings:
    """Paramètres runtime (immutables)."""

    database_path: Path
    """Fichier .mdb/.accdb : import depuis l’ancien système uniquement."""

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "smegend"
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_charset: str = "utf8mb4"

    mysql_view_mois: str | None = None
    """Si défini (``YYYY-MM``), la liste générale lit la carrière de cette période."""

    template_liste_present_path: Path | None = None
    """Maquette Excel `resultat.xlsx` si trouvée (`data/` ou dossier Impression)."""

    template_numerique_path: Path | None = None
    """Gabarit `numerique.xlsx` (situation numérique) si trouvé."""

    template_detaches_path: Path | None = None
    """Fichier officiel `DETACHES.xlsx` (détachés) si trouvé."""

    odbc_driver: str = "Microsoft Access Driver (*.mdb, *.accdb)"

    def connection_string(self) -> str:
        p = self.database_path.resolve()
        drv = self.odbc_driver
        return f"DRIVER={{{drv}}};DBQ={p};"


def get_settings() -> Settings:
    """
    Charge la configuration.

    Données opérationnelles : MySQL (paths.json en priorité, sinon SME_MYSQL_*).

    Access : SMEDB_PATH → paths.json → data/smegend.mdb (imports uniquement).

    Maquette : SME_TEMPLATE_PATH → paths.json → data/resultat.xlsx.
    """
    user = load_user_paths()
    db_path = _resolve_database_path(user)
    tpl = _resolve_template_path(user)
    tnum = _resolve_numerique_template_path(user)
    tdet = _resolve_detaches_path(user)

    host, port, database, mysql_user, mysql_password = _resolve_mysql_connection(user)

    return Settings(
        database_path=db_path,
        template_liste_present_path=tpl,
        template_numerique_path=tnum,
        template_detaches_path=tdet,
        mysql_host=host,
        mysql_port=port,
        mysql_database=database,
        mysql_user=mysql_user,
        mysql_password=mysql_password,
    )
