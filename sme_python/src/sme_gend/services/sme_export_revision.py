"""Append-only, independently verifiable copies of the monthly SME export.

An export revision is evidence of the generated document, not approval of a month.
This filesystem layer deliberately performs no schema migration or business write.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

FORMAT = "sgi-dpf-sme-export-v1"
ARTIFACTS = ("classeur.xlsx", "modele.xlsx", "donnees.json")


def _model_types():
    # Explicit whitelist: an archived JSON must never import arbitrary Python.
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.models.grade_ref import GradeRefRow
    from sme_gend.models.gain_perte import GainPerteRow
    from sme_gend.models.agent_evenement import AgentCongeRow
    from sme_gend.models.numerique_satellite import MissionDetail, MaladeDetail, DeserteurDetail, StageDetail
    return {kind.__name__: kind for kind in (
        ListeGeneraleRow, GradeRefRow, GainPerteRow, AgentCongeRow,
        MissionDetail, MaladeDetail, DeserteurDetail, StageDetail,
    )}


def _encode(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        # Pairs preserve the tuple keys used by typed leave/position registries.
        return {"type": "mapping", "items": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if is_dataclass(value) and type(value).__name__ in _model_types():
        return {"type": "record", "model": type(value).__name__, "fields": {
            field.name: _encode(getattr(value, field.name)) for field in fields(value)
            if not field.name.startswith("_") and field.name != "write_token"
        }}
    raise TypeError(f"Type de donnée SME non pris en charge : {type(value).__name__}.")


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    if kind == "datetime":
        return datetime.fromisoformat(value["value"])
    if kind == "date":
        return date.fromisoformat(value["value"])
    if kind == "decimal":
        return Decimal(value["value"])
    if kind == "tuple":
        return tuple(_decode(item) for item in value["items"])
    if kind == "mapping":
        return {_decode(k): _decode(v) for k, v in value["items"]}
    if kind == "record" and value.get("model") in _model_types():
        model = _model_types()[value["model"]]
        return model(**{key: _decode(item) for key, item in value["fields"].items()})
    raise ValueError("Type inconnu dans l'instantané SME.")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def encode_export_dataset(dataset: dict) -> bytes:
    return _json_bytes({"format": FORMAT, "dataset": _encode(dataset)})


def decode_export_dataset(payload: bytes) -> dict:
    raw = json.loads(payload)
    if raw.get("format") != FORMAT:
        raise ValueError("Format d'instantané SME non pris en charge.")
    result = _decode(raw["dataset"])
    if not isinstance(result, dict):
        raise ValueError("Instantané SME invalide.")
    return result


def revision_root(settings) -> Path:
    from sme_gend.config.user_paths import resolved_sme_archives_directory
    from sme_gend.repositories.mysql_connection import mysql_database_key
    # No password/user in paths or manifests. Separate databases cannot mix months.
    identity = _json_bytes(list(mysql_database_key(settings)))
    return resolved_sme_archives_directory() / "publications_sme" / sha256(identity).hexdigest()[:20]


def validate_export_destination(destination: Path, *, template: Path, root: Path) -> Path:
    from sme_gend.services.sme_publication_backup import storage_root
    dest = Path(destination).expanduser().resolve()
    if dest == Path(template).resolve():
        raise ValueError("Choisissez un autre fichier : le modèle SME doit être conservé.")
    if dest.is_relative_to(Path(root).resolve()) or dest.is_relative_to(storage_root().resolve()):
        raise ValueError("Une version SME archivée ne peut pas être remplacée. Choisissez un dossier d'export.")
    return dest


@dataclass(frozen=True)
class ExportRevision:
    revision_id: str
    folder: Path
    manifest: dict
    journal_warning: str = ""
    publication_id: int | None = None
    revision_no: int | None = None
    publication_status: str | None = None

    @property
    def workbook(self) -> Path:
        return self.folder / "classeur.xlsx"

    def journal_metadata(self) -> dict:
        return {
            "publication_id": self.publication_id, "revision_no": self.revision_no,
            "publication_status": self.publication_status,
            "revision_id": self.revision_id, "revision_folder": str(self.folder),
            "dataset_sha256": self.manifest["artifacts"]["donnees.json"]["sha256"],
            "workbook_sha256": self.manifest["artifacts"]["classeur.xlsx"]["sha256"],
            "template_sha256": self.manifest["artifacts"]["modele.xlsx"]["sha256"],
            "captured_at_utc": self.manifest["source"]["captured_at_utc"],
            "validation_officielle": False,
            "sheets": self.manifest["sheets"],
        }


def save_export_revision(*, root: Path, mois: str, template_bytes: bytes,
                         dataset: dict, source: dict, actor: str, renderer) -> ExportRevision:
    """Publish a complete new directory once; never reuse a previous revision.

    Capture and rendering happen before the directory becomes visible. A failed
    rendering leaves neither a revision nor a replacement of the requested file.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", mois):
        raise ValueError("Mois SME invalide.")
    date.fromisoformat(mois + "-01")
    from sme_gend import __version__
    now = datetime.now(timezone.utc)
    revision_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex
    month_root = Path(root).resolve() / mois
    month_root.mkdir(parents=True, exist_ok=True)
    folder = month_root / revision_id
    snapshot = encode_export_dataset(dataset)
    with tempfile.TemporaryDirectory(prefix=".preparation_", dir=month_root) as temporary:
        working = Path(temporary)
        (working / "modele.xlsx").write_bytes(template_bytes)
        (working / "donnees.json").write_bytes(snapshot)
        sheets = renderer(working / "modele.xlsx", working / "classeur.xlsx")
        artifacts = {}
        for name in ARTIFACTS:
            content = (working / name).read_bytes()
            artifacts[name] = {"sha256": sha256(content).hexdigest(), "size": len(content)}
        manifest = {
            "format": FORMAT, "revision_id": revision_id, "mois": mois,
            "generated_at_utc": now.isoformat(), "actor": actor,
            "application_version": __version__, "source": source,
            "validation_officielle": False, "artifacts": artifacts, "sheets": list(sheets),
        }
        (working / "manifest.json").write_bytes(_json_bytes(manifest))
        # Same filesystem, exclusive new UUID directory. No existing version is touched.
        from sme_gend.services.file_contention import retry_file_operation
        retry_file_operation(lambda: working.rename(folder), path=folder)
    return ExportRevision(revision_id, folder, manifest)


def verify_export_revision(folder: Path, *, expected: dict | None = None) -> dict:
    """Verify all archived bytes before consulting or retrieving a version."""
    folder = Path(folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("revision_id") != folder.name
            or manifest.get("mois") != folder.parent.name
            or not isinstance(manifest.get("artifacts"), dict)
            or set(manifest.get("artifacts", {})) != set(ARTIFACTS)):
        raise ValueError("Manifeste de version SME invalide.")
    if expected is not None:
        if expected.get("revision_id") != manifest["revision_id"]:
            raise ValueError("La version SME ne correspond pas au journal documentaire.")
        for name, key in (("classeur.xlsx", "workbook_sha256"), ("modele.xlsx", "template_sha256"),
                          ("donnees.json", "dataset_sha256")):
            if expected.get(key) != manifest["artifacts"][name].get("sha256"):
                raise ValueError("Les empreintes de la version SME diffèrent du journal documentaire.")
    for name in ARTIFACTS:
        path = folder / name
        if path.is_symlink() or path.resolve().parent != folder:
            raise ValueError("Un fichier de version SME a été remplacé par un lien.")
        content = path.read_bytes()
        artifact = manifest["artifacts"][name]
        if (not isinstance(artifact, dict) or len(content) != artifact.get("size")
                or sha256(content).hexdigest() != artifact.get("sha256")):
            raise ValueError(f"Version SME altérée ou incomplète : {name}.")
    return manifest


def read_export_dataset(folder: Path) -> dict:
    _require_export_permission()
    manifest = verify_export_revision(folder)
    payload = (Path(folder) / "donnees.json").read_bytes()
    if sha256(payload).hexdigest() != manifest["artifacts"]["donnees.json"]["sha256"]:
        raise ValueError("L'instantané SME a changé pendant sa lecture.")
    return decode_export_dataset(payload)


def _require_export_permission():
    from sme_gend.services.auth_guard import session_can_export_documents
    if not session_can_export_documents():
        raise PermissionError("Le droit d'exporter les effectifs est nécessaire.")


def retrieve_export_revision(folder: Path, destination: Path, *, expected: dict | None = None) -> None:
    """Retrieve the exact workbook without consulting today's mutable database."""
    folder = Path(folder).resolve()
    _require_export_permission()
    manifest = verify_export_revision(folder, expected=expected)
    dest = Path(destination).expanduser().resolve()
    from sme_gend.services.sme_publication_backup import storage_root
    if dest.is_relative_to(folder.parent.parent) or dest.is_relative_to(storage_root().resolve()):
        raise ValueError("Une version SME archivée ne peut pas être remplacée.")
    from sme_gend.services.safe_paths import atomic_destination
    import shutil
    with atomic_destination(dest) as staged:
        shutil.copyfile(folder / "classeur.xlsx", staged)
        content = staged.read_bytes()
        artifact = manifest["artifacts"]["classeur.xlsx"]
        if len(content) != artifact["size"] or sha256(content).hexdigest() != artifact["sha256"]:
            raise ValueError("Le classeur archivé a changé pendant sa copie ; le fichier choisi est conservé.")
