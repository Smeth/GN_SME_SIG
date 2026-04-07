"""Configuration centralisée (chemins base Access, maquette Excel)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    """Racine du projet `sme_python` (dossier qui contient `src/` et `data/`)."""
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


def liste_layout_config_path() -> Path:
    """Fichier JSON : ordre des sections, titres, liaison structures."""
    return _project_root() / "data" / "liste_present_layout.json"


def _resolve_template_path() -> Path | None:
    env = os.environ.get("SME_TEMPLATE_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    for candidate in _candidate_template_paths():
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

    odbc_driver: str = "Microsoft Access Driver (*.mdb, *.accdb)"

    def connection_string(self) -> str:
        p = self.database_path.resolve()
        drv = self.odbc_driver
        return f"DRIVER={{{drv}}};DBQ={p};"


def get_settings() -> Settings:
    """
    Charge la configuration.

    Base : `SMEDB_PATH`, puis `data/smegend.mdb`, puis `Impression/smegend.mdb`.

    Maquette liste présents : `SME_TEMPLATE_PATH`, puis `data/resultat.xlsx`,
    puis `Impression/resultat.xlsx`.
    """
    env = os.environ.get("SMEDB_PATH", "").strip()
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"SMEDB_PATH ne pointe pas vers un fichier valide : {path}")
        db_path = path
    else:
        default = _default_mdb_path()
        if default is None:
            data_dir = _project_root() / "data"
            raise FileNotFoundError(
                "Base Access introuvable. Placez smegend.mdb dans le dossier :\n"
                f"  {data_dir}\n"
                "ou définissez la variable d'environnement SMEDB_PATH vers le fichier .mdb."
            )
        db_path = default

    tpl = _resolve_template_path()
    return Settings(database_path=db_path, template_liste_present_path=tpl)
