"""Validated append-only export revisions in full/application-data backups."""
from hashlib import sha256
import json
from pathlib import Path
import re

from sme_gend.services.safe_paths import contained_path, relative_parts
from sme_gend.services.sme_export_revision import ARTIFACTS, FORMAT, verify_export_revision

PREFIX = "sme_publications"
FILES = (*ARTIFACTS, "manifest.json")  # Manifest last: incomplete folders are never revisions.


def storage_root():
    from sme_gend.config.user_paths import resolved_sme_archives_directory
    return resolved_sme_archives_directory() / "publications_sme"


def _parts(name):
    parts = relative_parts(name)
    if (len(parts) != 5 or parts[0] != PREFIX or not re.fullmatch("[0-9a-f]{20}", parts[1])
            or not re.fullmatch(r"\d{4}-\d{2}", parts[2])
            or not re.fullmatch(r"\d{8}T\d{12}Z_[0-9a-f]{32}", parts[3]) or parts[4] not in FILES):
        raise ValueError("Chemin de version SME invalide dans la sauvegarde.")
    from datetime import date
    date.fromisoformat(parts[2] + "-01")
    return parts


def restore_target(name):
    return contained_path(storage_root(), "/".join(_parts(name)[1:]))


def receiving_archive_paths(payload: dict, target: Path) -> dict:
    """Retain the receiving archive root even if SQL credentials are replaced."""
    current = json.loads(target.read_bytes()) if target.is_file() else {}
    if not isinstance(current, dict):
        raise ValueError("Configuration locale des chemins invalide.")
    result = dict(payload)
    result.pop("sme_archives_path", None)
    if "sme_archives_path" in current:
        result["sme_archives_path"] = current["sme_archives_path"]
    return result


def _check_group(parts, payloads):
    if set(payloads) != set(FILES):
        raise ValueError("Version SME incomplète dans la sauvegarde.")
    manifest = json.loads(payloads["manifest.json"])
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
            or manifest.get("mois") != parts[2] or manifest.get("revision_id") != parts[3]
            or set(manifest.get("artifacts", {})) != set(ARTIFACTS)):
        raise ValueError("Manifeste de version SME invalide dans la sauvegarde.")
    for name in ARTIFACTS:
        artifact = manifest["artifacts"][name]
        payload = payloads[name]
        if (not isinstance(artifact, dict) or len(payload) != artifact.get("size")
                or sha256(payload).hexdigest() != artifact.get("sha256")):
            raise ValueError("Version SME altérée dans la sauvegarde : " + name)


def add_to_backup(settings, archive) -> list[str]:
    base = storage_root()
    if not base.exists():
        return []
    if base.is_symlink():
        raise ValueError("Le dossier des versions SME ne peut pas être un lien.")
    names = []
    # Include restored source identities too, even when the receiving server or
    # database name differs from the server which originally created a version.
    for root in sorted(base.iterdir()):
        if not re.fullmatch("[0-9a-f]{20}", root.name):
            continue
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Dossier de versions SME invalide.")
        for marker in sorted(root.glob("*/*/manifest.json")):
            folder = marker.parent
            paths = {name: folder / name for name in FILES}
            if any(path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in paths.values()):
                raise ValueError("Version SME hors du dossier d'archives autorisé.")
            manifest = verify_export_revision(folder)
            payloads = {name: path.read_bytes() for name, path in paths.items()}
            prefix = "/".join((PREFIX, root.name, manifest["mois"], manifest["revision_id"]))
            _check_group(_parts(prefix + "/manifest.json"), payloads)
            for name in FILES:
                arcname = prefix + "/" + name
                archive.writestr(arcname, payloads[name])
                names.append(arcname)
    return names


def validate_in_backup(archive, listed) -> list[str]:
    names = [name for name in archive.namelist() if name.startswith(PREFIX + "/") and not name.endswith("/")]
    if sorted(names) != sorted(listed or []):
        raise ValueError("Inventaire des versions SME incomplet ou différent du contenu sauvegardé.")
    groups = {}
    for name in names:
        parts = _parts(name)
        groups.setdefault(parts[:-1], {})[parts[-1]] = archive.read(name)
    ordered = []
    for parts, payloads in sorted(groups.items()):
        _check_group(parts, payloads)
        ordered.extend("/".join((*parts, name)) for name in FILES)
    return ordered


def restore_plan(archive, listed):
    from sme_gend.services.restore_files import digest
    plan = []
    for name in validate_in_backup(archive, listed):
        target = restore_target(name)
        incoming = sha256(archive.read(name)).hexdigest()
        existing = digest(target)
        if existing is not None:
            if existing != incoming:
                raise ValueError("Conflit de version SME : une version existante différente ne sera pas remplacée.")
            continue
        plan.append((name, target, "publication"))
    return plan


def locate_revision(metadata: dict, mois: str = "") -> Path:
    """A restored journal can retain a path belonging to its original machine."""
    original = Path(str(metadata.get("revision_folder") or ""))
    if original.is_dir() and (original / "manifest.json").is_file():
        return original
    revision = str(metadata.get("revision_id") or "")
    if not re.fullmatch(r"\d{8}T\d{12}Z_[0-9a-f]{32}", revision):
        raise ValueError("Identifiant de version SME invalide.")
    pattern = "*/" + (mois if re.fullmatch(r"\d{4}-\d{2}", mois or "") else "*") + "/" + revision
    found = [folder for folder in storage_root().glob(pattern) if (folder / "manifest.json").is_file()]
    if len(found) != 1:
        raise FileNotFoundError("Version SME non disponible sur ce poste. Vérifiez le dossier partagé ou restaurez ses fichiers.")
    return found[0]
