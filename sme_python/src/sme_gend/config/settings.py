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
    """Racine applicative (``data/``, schémas SQLite, etc.)."""
    return _project_root()


def _candidate_mdb_paths() -> list[Path]:
    root = _project_root()
    return [
        root / "data" / "smegend.mdb",
        root.parent / "smegend.mdb",
    ]


def _default_mdb_path() -> Path | None:
    for candidate in _candidate_mdb_paths():
        if candidate.is_file():
            return candidate
    return None


def _candidate_template_paths() -> list[Path]:
    """Maquette `resultat.xlsx` (liste des présents)."""
    root = _project_root()
    return [
        root / "data" / "resultat.xlsx",
        root.parent / "resultat.xlsx",
    ]


def _candidate_numerique_template_paths() -> list[Path]:
    """Gabarit `numerique.xlsx` (situation numérique + états)."""
    root = _project_root()
    return [
        root / "data" / "templates" / "numerique.xlsx",
        root.parent / "numerique.xlsx",
        root.parent / "numérique.xlsx",
    ]


def _candidate_detaches_paths() -> list[Path]:
    """Fichier officiel `DETACHES.xlsx` (liste des détachés)."""
    root = _project_root()
    return [
        root / "data" / "templates" / "DETACHES.xlsx",
        root.parent / "DETACHES.xlsx",
    ]


def liste_layout_config_path() -> Path:
    """Fichier JSON : ordre des sections, titres, liaison structures."""
    return _project_root() / "data" / "liste_present_layout.json"


def detaches_config_path() -> Path:
    """Fichier JSON : sigles → titres Excel, ordre, options génération feuille DETACHE."""
    return _project_root() / "data" / "detaches_config.json"


def _resolve_database_path(
    user: UserPathsOverrides,
    *,
    sqlite_bundle_mode: bool,
) -> Path:
    """
    Chemin du fichier Access (import depuis l’ancien système, ou mode liste 100 % Access).

    Ordre de priorité :
    1. SMEDB_PATH (doit exister si défini)
    2. paths.json → database_path (si le fichier existe)
    3. data/smegend.mdb ou voisin si présent
    4. En mode **bundle SQLite** : chemin enregistré même absent, ou emplacement par défaut
       (le fichier sert surtout aux imports ; l’exploitation courante lit le .sqlite).
    """
    env = os.environ.get("SMEDB_PATH", "").strip()
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"SMEDB_PATH ne pointe pas vers un fichier valide :\n{path}"
            )
        return path

    if user.database_path:
        p = Path(user.database_path).expanduser()
        if p.is_file():
            return p

    default = _default_mdb_path()
    if default is not None:
        return default

    if sqlite_bundle_mode:
        if user.database_path and str(user.database_path).strip():
            return Path(user.database_path).expanduser()
        return _project_root() / "data" / "smegend.mdb"

    data_dir = _project_root() / "data"
    cfg = user_paths_config_file_hint()
    raise FileNotFoundError(
        "Base Access introuvable (mode liste Access : ce fichier est obligatoire).\n\n"
        f"• Placez smegend.mdb dans :\n  {data_dir}\n"
        "• Ou activez le <b>mode SQLite</b> (données principales dans le fichier .sqlite) "
        "et utilisez Access uniquement pour l’import.\n"
        "• Ou définissez le chemin Access dans Fichier → Chemins des fichiers de données…\n\n"
        "• Variable d'environnement SMEDB_PATH vers le fichier .mdb.\n\n"
        f"Fichier de configuration utilisateur : {cfg}"
    )


def user_paths_config_file_hint() -> str:
    from sme_gend.config.user_paths import user_paths_config_file

    return str(user_paths_config_file())


def _default_sqlite_liste_path() -> Path:
    return _project_root() / "data" / "liste_generale.sqlite"


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


def _resolve_sqlite_liste_path(user: UserPathsOverrides) -> Path:
    env = os.environ.get("SME_SQLITE_LISTE_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    if user.sqlite_liste_path:
        p = Path(user.sqlite_liste_path).expanduser()
        if str(p).strip():
            return p
    return _default_sqlite_liste_path()


def _resolve_template_path(user: UserPathsOverrides) -> Path | None:
    """
    Ordre :
    1. SME_TEMPLATE_PATH
    2. chemins enregistrés dans paths.json
    3. data/resultat.xlsx, etc.
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
    3. data/templates/numerique.xlsx, dossier parent, etc.
    """
    env = os.environ.get("SME_NUMERIQUE_TEMPLATE_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if user.template_numerique_path:
        p = Path(user.template_numerique_path).expanduser()
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
    3. data/templates/DETACHES.xlsx, dossier parent, etc.
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
    """Fichier .mdb/.accdb : import depuis l’ancien système, ou base liste si mode Access seul."""

    template_liste_present_path: Path | None = None
    """Maquette Excel `resultat.xlsx` si trouvée (`data/` ou dossier Impression)."""

    template_numerique_path: Path | None = None
    """Gabarit `numerique.xlsx` (situation numérique) si trouvé."""

    template_detaches_path: Path | None = None
    """Fichier officiel `DETACHES.xlsx` (détachés) si trouvé."""

    sqlite_liste_path: Path | None = None
    """Fichier SQLite liste générale (ex. ``data/liste_generale.sqlite``)."""

    use_sqlite_liste_generale: bool = False
    """Si vrai : données opérationnelles dans le .sqlite ; Access sert surtout aux imports depuis l’ancien outil."""

    odbc_driver: str = "Microsoft Access Driver (*.mdb, *.accdb)"

    def connection_string(self) -> str:
        p = self.database_path.resolve()
        drv = self.odbc_driver
        return f"DRIVER={{{drv}}};DBQ={p};"


def get_settings() -> Settings:
    """
    Charge la configuration.

    Données principales (mode SQLite) : SME_SQLITE_LISTE_PATH → paths.json → data/liste_generale.sqlite.

    Access : SMEDB_PATH → paths.json → data/smegend.mdb (import ou mode Access liste seule).

    Maquette : SME_TEMPLATE_PATH → paths.json → data/resultat.xlsx.

    Gabarit numérique : SME_NUMERIQUE_TEMPLATE_PATH → paths.json → data/templates/numerique.xlsx.

    Détachés : SME_DETACHES_PATH → paths.json → data/templates/DETACHES.xlsx.

    SME_USE_SQLITE_LISTE force le mode bundle SQLite vs liste Access.
    """
    user = load_user_paths()
    sqlite_p = _resolve_sqlite_liste_path(user)
    use_sqlite = user.use_sqlite_liste_generale
    sqlite_mode_env = _env_bool_override("SME_USE_SQLITE_LISTE")
    if sqlite_mode_env is not None:
        use_sqlite = sqlite_mode_env
    db_path = _resolve_database_path(user, sqlite_bundle_mode=use_sqlite)
    tpl = _resolve_template_path(user)
    tnum = _resolve_numerique_template_path(user)
    tdet = _resolve_detaches_path(user)
    return Settings(
        database_path=db_path,
        template_liste_present_path=tpl,
        template_numerique_path=tnum,
        template_detaches_path=tdet,
        sqlite_liste_path=sqlite_p,
        use_sqlite_liste_generale=use_sqlite,
    )
