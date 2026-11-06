"""Pilote ODBC Microsoft Access — détection et messages utilisateur."""

from __future__ import annotations

from sme_gend.config.ui_labels import IMPORT_MONTH_MENU_PATH

import shutil
import sys
import uuid
from pathlib import Path

_PREFERRED_DRIVERS: tuple[str, ...] = (
    "Microsoft Access Driver (*.mdb, *.accdb)",
    "Microsoft Access Driver (*.mdb)",
    "Microsoft Access Driver",
)


def installed_odbc_drivers() -> list[str]:
    try:
        import pyodbc

        return list(pyodbc.drivers())
    except Exception:
        return []


def pick_access_driver(preferred: str | None = None) -> str | None:
    """Retourne le nom exact du pilote ODBC Access installé, ou None."""
    drivers = installed_odbc_drivers()
    pref = (preferred or "").strip()
    if pref and pref in drivers:
        return pref
    for candidate in _PREFERRED_DRIVERS:
        if candidate in drivers:
            return candidate
    for name in drivers:
        low = name.casefold()
        if "access" in low and "driver" in low:
            return name
        if "microsoft" in low and ("mdb" in low or "accdb" in low):
            return name
    for name in drivers:
        if "jet" in name.casefold():
            return name
    return None


def build_access_connection_string(
    mdb_path: Path,
    *,
    preferred_driver: str | None = None,
) -> tuple[str, str]:
    """
    Construit la chaîne ODBC.

    Retourne (connection_string, driver_name).
    Lève ``RuntimeError`` si aucun pilote Access n'est disponible.
    """
    driver = pick_access_driver(preferred_driver)
    if not driver:
        raise RuntimeError(_no_driver_message())
    p = mdb_path.resolve()
    return f"DRIVER={{{driver}}};DBQ={p};", driver


def preflight_access_file(
    mdb_path: Path | str,
    *,
    preferred_driver: str | None = None,
) -> tuple[bool, str]:
    """Vérifie fichier + pilote + ouverture ODBC rapide."""
    path = Path(mdb_path)
    if not path.is_file():
        return False, f"Fichier introuvable :\n{path}"
    try:
        cs, driver = build_access_connection_string(path, preferred_driver=preferred_driver)
    except RuntimeError as e:
        return False, str(e)
    try:
        import pyodbc

        conn = pyodbc.connect(cs, autocommit=True, timeout=8)
        conn.close()
    except Exception as e:
        return False, format_access_odbc_error(e, mdb_path=path, driver_used=driver)
    return True, f"Pilote ODBC : {driver}"


def format_access_odbc_error(
    exc: BaseException,
    *,
    mdb_path: Path | None = None,
    driver_used: str | None = None,
) -> str:
    """Message lisible (pilote 64 bits, fichier verrouillé, etc.)."""
    text = str(exc or "").strip()
    low = text.casefold()
    bits = struct_bits_label()
    lines: list[str] = []

    if mdb_path is not None:
        lines.append(f"Fichier : {mdb_path}")
    if driver_used:
        lines.append(f"Pilote demandé : {driver_used}")

    if "im002" in low or "data source name not found" in low or "driver" in low and "not found" in low:
        lines.append(
            "Le pilote ODBC Microsoft Access n'est pas installé pour la version "
            f"Python de l'application ({bits})."
        )
        installed = installed_odbc_drivers()
        if installed:
            access_like = [d for d in installed if "access" in d.casefold() or "jet" in d.casefold()]
            if access_like:
                lines.append("Pilotes Access/Jet détectés : " + ", ".join(access_like))
            else:
                lines.append(
                    "Pilotes ODBC présents (sans Access) : " + ", ".join(installed[:8])
                    + ("…" if len(installed) > 8 else "")
                )
        lines.append(
            "Installez « Microsoft Access Database Engine 2016 Redistributable » "
            "en **64 bits** (même architecture que SME_Gendarmerie.exe)."
        )
        lines.append(
            f"Menu utile : {IMPORT_MONTH_MENU_PATH} "
            "(import MySQL, archivage et clôture optionnels — pilote ODBC requis)."
        )
        return "\n\n".join(lines)

    if "locked" in low or "verrouill" in low or "could not use" in low:
        lines.append(
            "Le fichier est peut-être ouvert dans Microsoft Access ou un autre programme. "
            "Fermez-le puis réessayez."
        )
        return "\n\n".join(lines) if lines else text

    if "hyt00" in low or "hyt01" in low or "timeout expired" in low or "query timeout" in low:
        lines.append(
            "La lecture du fichier Access a dépassé le délai autorisé — le pilote semble "
            "bloqué (fichier volumineux ou fragmenté, index endommagé)."
        )
        lines.append(
            "Essayez d'abord : ouvrez le fichier dans Microsoft Access → "
            "Outils de base de données → « Compacter et réparer la base de données », "
            "puis réessayez l'import."
        )
        return "\n\n".join(lines)

    if "password" in low or "mot de passe" in low:
        lines.append("La base Access semble protégée par mot de passe (non géré par l'import).")
        return "\n\n".join(lines)

    if lines:
        lines.append(f"Détail technique : {text}")
        return "\n\n".join(lines)
    return text or "Erreur ODBC Access."


def import_staging_dir() -> Path:
    """Dossier de copie temporaire pour la lecture ODBC d'un .mdb pendant l'import."""
    from sme_gend.config.user_paths import user_application_data_dir

    d = user_application_data_dir() / "import_staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_access_file_copy(source: Path | str) -> Path:
    """
    Copie ``source`` vers le dossier de staging avant lecture ODBC.

    Évite de garder le fichier original (potentiellement sur un partage réseau,
    ou modifiable par un autre poste) ouvert pendant toute la durée de l'import,
    qui peut prendre plusieurs minutes et enchaîne plusieurs connexions ODBC.
    L'appelant est responsable de supprimer la copie (voir ``cleanup_staged_copy``).
    """
    src = Path(source).resolve()
    dest = import_staging_dir() / f"{uuid.uuid4().hex}_{src.name}"
    from sme_gend.services.safe_paths import atomic_destination
    # shutil.copy (pas copy2) : on ne préserve pas la date de modification du
    # fichier source — sinon un .mdb ancien produit une copie de staging déjà
    # « vieille » dès sa création, que purge_stale_staged_copies() supprimerait
    # à tort pendant qu'un import est encore en cours de lecture.
    with atomic_destination(dest,replace_existing=False) as temporary:
        shutil.copy(src, temporary)
    return dest


def cleanup_staged_copy(staged: Path | None) -> None:
    """Supprime une copie de staging créée par ``stage_access_file_copy`` (best-effort)."""
    if staged is None:
        return
    try:
        Path(staged).unlink(missing_ok=True)
    except OSError:
        pass


def purge_stale_staged_copies(*, max_age_hours: float = 24.0) -> int:
    """
    Supprime les copies de staging orphelines plus vieilles que ``max_age_hours``.

    ``cleanup_staged_copy`` échoue à nettoyer si l'application est fermée de force
    pendant un import (cas réel constaté) — cette purge, appelée au démarrage,
    évite une accumulation silencieuse de copies de fichiers Access (potentiellement
    volumineuses) dans le dossier de staging au fil du temps.
    """
    import time

    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    d = import_staging_dir()
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    for p in entries:
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def struct_bits_label() -> str:
    return "64 bits" if sys.maxsize > 2**32 else "32 bits"


def _no_driver_message() -> str:
    bits = struct_bits_label()
    installed = installed_odbc_drivers()
    parts = [
        f"Aucun pilote ODBC Microsoft Access utilisable pour Python {bits}.",
        "",
        "Pour **importer les données d’un mois** (Access → MySQL), installez :",
        "Microsoft Access Database Engine 2016 Redistributable (64 bits).",
        "",
        "Pour **importer les données mensuelles** d’un fichier .mdb dans MySQL :",
        IMPORT_MONTH_MENU_PATH,
        "(pilote ODBC Access 64 bits requis).",
    ]
    if installed:
        parts.append("")
        parts.append("Pilotes ODBC actuellement installés :")
        parts.extend(f"  • {d}" for d in installed[:12])
        if len(installed) > 12:
            parts.append(f"  … et {len(installed) - 12} autre(s)")
    return "\n".join(parts)
