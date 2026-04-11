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


def _resolve_database_path(user: UserPathsOverrides) -> Path:
    """
    Ordre de priorité :
    1. Variable d'environnement SMEDB_PATH (déploiement / administrateur)
    2. Fichier utilisateur paths.json (menu Configuration — pratique en réseau UNC)
    3. Emplacements par défaut (data/smegend.mdb, etc.)
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
    if default is None:
        data_dir = _project_root() / "data"
        cfg = user_paths_config_file_hint()
        raise FileNotFoundError(
            "Base Access introuvable.\n\n"
            f"• Placez smegend.mdb dans :\n  {data_dir}\n"
            "• Ou définissez le chemin dans le menu Fichier → Chemins des fichiers de données…\n"
            "\n"
            "• Ou variable d'environnement SMEDB_PATH vers le fichier .mdb.\n\n"
            f"Fichier de configuration utilisateur : {cfg}"
        )
    return default


def user_paths_config_file_hint() -> str:
    from sme_gend.config.user_paths import user_paths_config_file

    return str(user_paths_config_file())


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
    """Chemin du fichier Access."""

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

    Base : SMEDB_PATH → paths.json (LOCALAPPDATA) → data/smegend.mdb.

    Maquette : SME_TEMPLATE_PATH → paths.json → data/resultat.xlsx.

    Gabarit numérique : SME_NUMERIQUE_TEMPLATE_PATH → paths.json → data/templates/numerique.xlsx.

    Détachés : SME_DETACHES_PATH → paths.json → data/templates/DETACHES.xlsx.
    """
    user = load_user_paths()
    db_path = _resolve_database_path(user)
    tpl = _resolve_template_path(user)
    tnum = _resolve_numerique_template_path(user)
    tdet = _resolve_detaches_path(user)
    return Settings(
        database_path=db_path,
        template_liste_present_path=tpl,
        template_numerique_path=tnum,
        template_detaches_path=tdet,
    )
