"""Catalogue en lecture seule des fichiers de clôture : aucune création de dossier."""
from dataclasses import dataclass
from pathlib import Path
import re

ARCHIVE_TYPES = {".sql": "Sauvegarde MySQL", ".json": "Mise en page",
                 ".mdb": "Archive Access", ".accdb": "Archive Access", ".zip": "Archive ZIP (à vérifier)"}


def inspect_archive(path: Path) -> str:
    """Validation en lecture seule, sans extraction ni exécution."""
    import hashlib
    import json
    path = Path(path).resolve()
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if not before.st_size:
        raise ValueError("Le fichier est vide.")
    if path.suffix.lower() == ".zip":
        import zipfile
        with zipfile.ZipFile(path) as archive:
            monthly = "month.json" in archive.namelist()
        if monthly:
            from sme_gend.services.month_transfer import load_source, months_in
            tables = load_source(path)
            detail = "Archive mensuelle SME vérifiée (CRC et empreinte du contenu). Période : " + ", ".join(months_in(tables))
        else:
            from sme_gend.services.backup_restore_service import inspect_backup
            manifest = inspect_backup(path)
            detail = (f"Structure et CRC ZIP valides. Manifeste : version {manifest.format_version}, "
                  f"type {manifest.backup_kind}, créé le {manifest.created_at}.\n"
                  f"Base d'origine : {manifest.mysql_database}.\n"
                  "La restauration doit encore valider les données et leurs dépendances.")
    elif path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("Un objet JSON de mise en page est attendu.")
        detail = "JSON lisible. Ce fichier contient une configuration, pas les effectifs."
    else:
        detail = ("Empreinte calculée ; validité métier non vérifiée. "
                  "Le SQL n'est pas exécuté. Contrôlez les données Access dans l'assistant d'import.")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Le fichier a changé pendant sa vérification. Recommencez.")
    return f"{path.name}\n{detail}\n\nSHA-256 : {digest.hexdigest()}\nMois déduit du nom (non certifié) : {archive_month(path.name) or 'non identifié'}."


def archive_month(name: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{4})[-_](0[1-9]|1[0-2])(?!\d)", name)
    return f"{match[1]}-{match[2]}" if match else None


@dataclass(frozen=True)
class ArchiveCatalog:
    directory: Path
    entries: tuple[tuple[Path, str | None, int, float], ...]
    missing: bool = False
    unreadable: int = 0


def scan_archive_directory(directory: Path) -> ArchiveCatalog:
    directory = Path(directory).expanduser().resolve()
    if not directory.exists():
        return ArchiveCatalog(directory, (), missing=True)
    entries, unreadable = [], 0
    for path in directory.iterdir():
        try:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ARCHIVE_TYPES:
                continue
            stat = path.stat()
            entries.append((path, archive_month(path.name), stat.st_size, stat.st_mtime))
        except OSError:
            unreadable += 1
    entries.sort(key=lambda entry: (entry[3], entry[0].name), reverse=True)
    return ArchiveCatalog(directory, tuple(entries), unreadable=unreadable)
