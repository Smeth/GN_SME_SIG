"""Sauvegarde et restauration complète (MySQL + configuration utilisateur)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
import stat
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings, project_root
from sme_gend.config.sme_context import sme_context_file
from sme_gend.config.user_paths import (
    resolved_personnel_media_root,
    user_application_data_dir,
    user_paths_config_file,
)
from sme_gend.repositories.app_config_repository import AppConfigRepository, validate_config_rows
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.backup_sql import split_statements, target_table, is_safe_setting, validate_restore_statement
from sme_gend.services.safe_paths import relative_parts, contained_path, atomic_destination, atomic_write

_log = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = 4
MANIFEST_NAME = "manifest.json"
SQL_DUMP_NAME = "mysql_dump.sql"
APP_CONFIG_DUMP_NAME = "mysql_app_config.json"
CONFIG_DIR = "user_config"
DATA_DIR = "app_data"
PERSONNEL_MEDIA_DIR = "personnel_media"
AUDIT_JOURNAL_TABLE = "audit_journal"

# Modules audit_journal (résumé sauvegarde / restauration)
AUDIT_MODULE_SENSITIVE = "sensitive_data"
AUDIT_MODULE_DOC_GEN = "doc_gen"
AUDIT_MODULE_PERSONNEL = "personnel"

_AUDIT_MIRROR_DATA_FILES = frozenset({
    "sensitive_audit_log.jsonl",
    "doc_gen_log.jsonl",
    "user_activity.jsonl",
})

_MYSQL_PATHS_KEYS = (
    "mysql_host",
    "mysql_port",
    "mysql_database",
    "mysql_user",
    "mysql_password",
)
_SKIP_CONFIG_FILES = frozenset({"auth_config.json"})

# Présélections tables MySQL (sauvegarde / restauration partielle)
MYSQL_TABLE_PRESETS: dict[str, tuple[str, ...]] = {
    "referentiels": (
        "ref_structure",
        "ref_subdivision_arme",
        "ref_subdivision_noeud",
        "ref_grade",
        "ref_fonction",
        "ref_position_admin",
        "structure_organigram_node",
    ),
    "carriere": (
        "agent",
        "carriere_situation",
        "sme_periode",
        "agent_dossier",
    ),
    "app_config": ("app_config",),
    "audit": ("audit_journal",),
}


@dataclass(frozen=True, slots=True)
class BackupScope:
    """Contenu inclus dans une archive (complète ou partielle)."""

    include_mysql: bool = True
    mysql_tables: tuple[str, ...] | None = None
    include_user_config: bool = True
    include_app_data: bool = True
    include_app_config: bool = True
    include_media_photos: bool = True
    include_media_grades: bool = True
    include_publications: bool = True

    @classmethod
    def full(cls) -> BackupScope:
        return cls()

    def is_full(self) -> bool:
        return (
            self.include_mysql
            and not self.mysql_tables
            and self.include_user_config
            and self.include_app_data
            and self.include_app_config
            and self.include_media_photos
            and self.include_media_grades
            and self.include_publications
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_mysql": self.include_mysql,
            "mysql_tables": list(self.mysql_tables) if self.mysql_tables else None,
            "include_user_config": self.include_user_config,
            "include_app_data": self.include_app_data,
            "include_app_config": self.include_app_config,
            "include_media_photos": self.include_media_photos,
            "include_media_grades": self.include_media_grades,
            "include_publications": self.include_publications,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> BackupScope:
        if not d:
            return cls.full()
        raw_tables = d.get("mysql_tables")
        tables: tuple[str, ...] | None = None
        if isinstance(raw_tables, list) and raw_tables:
            tables = tuple(str(x) for x in raw_tables)
        return cls(
            include_mysql=bool(d.get("include_mysql", True)),
            mysql_tables=tables,
            include_user_config=bool(d.get("include_user_config", True)),
            include_app_data=bool(d.get("include_app_data", True)),
            include_app_config=bool(d.get("include_app_config", True)),
            include_media_photos=bool(d.get("include_media_photos", True)),
            include_media_grades=bool(d.get("include_media_grades", True)),
            include_publications=bool(d.get("include_publications", True)),
        )


@dataclass(frozen=True, slots=True)
class RestoreScope:
    """Éléments à restaurer depuis une archive."""

    restore_mysql: bool = True
    mysql_tables: tuple[str, ...] | None = None
    restore_user_config: bool = True
    restore_app_data: bool = True
    restore_app_config: bool = True
    restore_media_photos: bool = True
    restore_media_grades: bool = True
    preserve_mysql_connection: bool = True
    preserve_audit_journal: bool = True
    preserve_local_accounts: bool = True
    maintenance_confirmed: bool = False
    restore_publications: bool = True

    @classmethod
    def full(cls) -> RestoreScope:
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "restore_mysql": self.restore_mysql,
            "mysql_tables": list(self.mysql_tables) if self.mysql_tables else None,
            "restore_user_config": self.restore_user_config,
            "restore_app_data": self.restore_app_data,
            "restore_app_config": self.restore_app_config,
            "restore_media_photos": self.restore_media_photos,
            "restore_media_grades": self.restore_media_grades,
            "preserve_mysql_connection": self.preserve_mysql_connection,
            "preserve_audit_journal": self.preserve_audit_journal,
            "preserve_local_accounts": self.preserve_local_accounts,
            "maintenance_confirmed": self.maintenance_confirmed,
            "restore_publications": self.restore_publications,
        }


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: int
    created_at: str
    mysql_database: str
    mysql_host: str
    table_counts: dict[str, int]
    config_files: list[str]
    app_config_exported: bool = False
    app_version: str = "sme_gend"
    personnel_media_files: int = 0
    portable_restore: bool = True
    audit_journal_by_module: dict[str, int] | None = None
    app_data_files: list[str] | None = None
    backup_kind: str = "full"
    backup_scope: dict[str, Any] | None = None
    personnel_media_photos: int = 0
    personnel_media_grades: int = 0
    publication_files: list[str] | None = None
    snapshot_consistency: dict[str, Any] | None = None
    file_inventory: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "format_version": self.format_version,
            "created_at": self.created_at,
            "mysql_database": self.mysql_database,
            "mysql_host": self.mysql_host,
            "table_counts": self.table_counts,
            "config_files": self.config_files,
            "app_config_exported": self.app_config_exported,
            "app_version": self.app_version,
            "personnel_media_files": self.personnel_media_files,
            "portable_restore": self.portable_restore,
            "backup_kind": self.backup_kind,
        }
        if self.audit_journal_by_module:
            d["audit_journal_by_module"] = self.audit_journal_by_module
        if self.app_data_files:
            d["app_data_files"] = self.app_data_files
        if self.publication_files:
            d["publication_files"] = self.publication_files
        if self.backup_scope:
            d["backup_scope"] = self.backup_scope
        if self.snapshot_consistency is not None:
            d["snapshot_consistency"] = self.snapshot_consistency
        if self.file_inventory is not None:
            d["file_inventory"] = self.file_inventory
        if self.personnel_media_photos or self.personnel_media_grades:
            d["personnel_media_photos"] = self.personnel_media_photos
            d["personnel_media_grades"] = self.personnel_media_grades
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BackupManifest:
        tc = d.get("table_counts")
        aj = d.get("audit_journal_by_module")
        adf = d.get("app_data_files")
        bs = d.get("backup_scope")
        return cls(
            format_version=int(d.get("format_version", 0)),
            created_at=str(d.get("created_at", "")),
            mysql_database=str(d.get("mysql_database", "")),
            mysql_host=str(d.get("mysql_host", "")),
            table_counts=dict(tc) if isinstance(tc, dict) else {},
            config_files=[str(x) for x in d.get("config_files") or []],
            app_config_exported=bool(d.get("app_config_exported")),
            app_version=str(d.get("app_version", "sme_gend")),
            personnel_media_files=int(d.get("personnel_media_files", 0)),
            portable_restore=bool(d.get("portable_restore", True)),
            audit_journal_by_module=dict(aj) if isinstance(aj, dict) else None,
            app_data_files=[str(x) for x in adf] if isinstance(adf, list) else None,
            backup_kind=str(d.get("backup_kind", "full")),
            backup_scope=dict(bs) if isinstance(bs, dict) else None,
            personnel_media_photos=int(d.get("personnel_media_photos", 0)),
            personnel_media_grades=int(d.get("personnel_media_grades", 0)),
            publication_files=[str(name) for name in d.get("publication_files", [])],
            snapshot_consistency=d.get("snapshot_consistency"),
            file_inventory=d.get("file_inventory"),
        )

    def saved_scope(self) -> BackupScope:
        stored = dict(self.backup_scope or {})
        stored.setdefault("include_publications", bool(self.publication_files))
        return BackupScope.from_dict(stored)

    def has_mysql_dump(self) -> bool:
        return bool(self.table_counts)

    def has_user_config(self) -> bool:
        return bool(self.config_files)

    def has_app_data(self) -> bool:
        return bool(self.app_data_files)

    def has_publications(self) -> bool:
        return bool(self.publication_files)

    def has_app_config(self) -> bool:
        return self.app_config_exported

    def has_media(self) -> bool:
        return self.personnel_media_files > 0

    def audit_journal_total(self) -> int:
        if self.audit_journal_by_module:
            return sum(int(v) for v in self.audit_journal_by_module.values())
        return int(self.table_counts.get(AUDIT_JOURNAL_TABLE, 0))

    def audit_module_count(self, module: str) -> int:
        if self.audit_journal_by_module:
            return int(self.audit_journal_by_module.get(module, 0))
        return 0


def _fetch_audit_journal_by_module(settings: Settings) -> dict[str, int]:
    """Compte les entrées ``audit_journal`` par module (actions sensibles, docs, etc.)."""
    mc = MySQLConnection(settings)
    try:
        with mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT module, COUNT(*) FROM audit_journal
                    GROUP BY module
                    """
                )
                return {
                    str(row[0] or "—"): int(row[1])
                    for row in cur.fetchall()
                }
    except Exception:
        return {}


def format_audit_journal_summary(manifest: BackupManifest) -> list[str]:
    """Lignes lisibles pour inspection / journal de sauvegarde."""
    total = manifest.audit_journal_total()
    if total <= 0:
        return ["Journal d'audit (MySQL) : aucune entrée"]
    lines = [f"Journal d'audit (MySQL) : {total} entrée(s) au total"]
    n_sensitive = manifest.audit_module_count(AUDIT_MODULE_SENSITIVE)
    n_doc = manifest.audit_module_count(AUDIT_MODULE_DOC_GEN)
    n_personnel = manifest.audit_module_count(AUDIT_MODULE_PERSONNEL)
    if n_sensitive:
        lines.append(f"  · Actions sensibles : {n_sensitive}")
    if n_doc:
        lines.append(f"  · Générations de documents : {n_doc}")
    if n_personnel:
        lines.append(f"  · Modifications personnel : {n_personnel}")
    other = total - n_sensitive - n_doc - n_personnel
    if other > 0:
        lines.append(f"  · Autres traces (imports, auth, système…) : {other}")
    return lines


def _sql_statement_affects_table(stmt: str, table: str) -> bool:
    """True si l'instruction SQL cible explicitement ``table``."""
    actual = target_table(stmt)
    return actual is not None and actual.casefold() == table.casefold()


def _is_audit_mirror_data_file(filename: str) -> bool:
    name = (filename or "").strip()
    if name in _AUDIT_MIRROR_DATA_FILES:
        return True
    return name.startswith("personnel_audit_") and name.endswith(".jsonl")


def _sql_escape(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        return "0x" + val.hex()
    s = str(val).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def _collect_config_paths() -> list[Path]:
    """Fichiers locaux par poste (connexion MySQL, préférences UI)."""
    paths: list[Path] = []
    user_dir = user_application_data_dir()
    if user_dir.is_dir():
        for fp in sorted(user_dir.iterdir()):
            if fp.is_file() and fp.suffix.lower() in (".json", ".jsonl"):
                if fp.name in _SKIP_CONFIG_FILES:
                    continue
                paths.append(fp)
    return paths


def _collect_personnel_media_roots(settings: Settings) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for root in (
        resolved_personnel_media_root(),
        project_root() / "data" / "personnel_media",
    ):
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _iter_personnel_media_files(
    settings: Settings,
    *,
    include_photos: bool = True,
    include_grades: bool = True,
) -> list[tuple[Path, str]]:
    """(chemin_absolu, arcname_relatif) pour photos/ et grades/."""
    out: list[tuple[Path, str]] = []
    seen_arcs: set[str] = set()
    subs: list[str] = []
    if include_photos:
        subs.append("photos")
    if include_grades:
        subs.append("grades")
    for root in _collect_personnel_media_roots(settings):
        for sub in subs:
            base = root / sub
            if not base.is_dir():
                continue
            for fp in sorted(base.rglob("*")):
                if not fp.is_file():
                    continue
                rel = fp.relative_to(root).as_posix()
                if rel in seen_arcs:
                    continue
                seen_arcs.add(rel)
                out.append((fp, rel))
    return out


def list_mysql_table_names(settings: Settings) -> list[str]:
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            from sme_gend.services.database_maintenance import INTERNAL_TABLES
            return [str(r[0]) for r in cur.fetchall() if str(r[0]) not in INTERNAL_TABLES]


def expand_mysql_preset(name: str, all_tables: list[str]) -> list[str]:
    key = (name or "").strip().casefold()
    preset = MYSQL_TABLE_PRESETS.get(key)
    if preset:
        allowed = set(all_tables)
        return [t for t in preset if t in allowed]
    return []


def describe_backup_scope(scope: BackupScope) -> list[str]:
    lines: list[str] = []
    if scope.include_mysql:
        if scope.mysql_tables:
            lines.append(f"MySQL : {len(scope.mysql_tables)} table(s)")
        else:
            lines.append("MySQL : toutes les tables")
    if scope.include_user_config:
        lines.append("Configuration utilisateur")
    if scope.include_app_data:
        lines.append("Fichiers data\\ (JSON / journaux locaux)")
    if scope.include_app_config:
        lines.append("Table app_config (export JSON)")
    media: list[str] = []
    if scope.include_media_photos:
        media.append("photos")
    if scope.include_media_grades:
        media.append("insignes")
    if scope.include_publications:
        lines.append("Versions archivées des classeurs SME")
    if media:
        lines.append(f"Médias personnel : {', '.join(media)}")
    return lines or ["(vide)"]


def archive_contents_summary(manifest: BackupManifest, zip_names: list[str]) -> dict[str, bool]:
    has_sql = SQL_DUMP_NAME in zip_names
    return {
        "mysql": has_sql,
        "user_config": any(n.startswith(CONFIG_DIR + "/") for n in zip_names),
        "app_data": any(n.startswith(DATA_DIR + "/") for n in zip_names),
        "app_config": APP_CONFIG_DUMP_NAME in zip_names,
        "media": any(n.startswith(PERSONNEL_MEDIA_DIR + "/") for n in zip_names),
        "publications": bool(manifest.publication_files),
    }


_LOCAL_PRESERVE_KEYS = _MYSQL_PATHS_KEYS + (
    "database_path",
    "template_excel_path",
    "template_numerique_path",
    "template_detaches_path",
    "sme_archives_path",
    "personnel_media_root_path",
    "personnel_media_local_override",
)


def _merged_paths_json(backup_data: dict[str, Any], target: Path, *, preserve_all_local_paths: bool = False) -> bytes:
    """Prepare the merged bytes without publishing configuration."""
    local: dict[str, Any] = {}
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Configuration locale des chemins invalide ; restauration refusée.")
            local = raw
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Configuration locale des chemins illisible ; restauration refusée.") from exc
    merged = dict(backup_data)
    for key in _LOCAL_PRESERVE_KEYS:
        if key in local:
            merged[key] = local[key]
        elif preserve_all_local_paths or key in _MYSQL_PATHS_KEYS or key == "personnel_media_local_override":
            merged.pop(key, None)
    return (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _merge_paths_json(backup_data: dict[str, Any], target: Path) -> None:
    atomic_write(target, _merged_paths_json(backup_data, target))


def _config_restore_target(filename: str) -> Path:
    user_dir = user_application_data_dir()
    if filename == "paths.json":
        return user_paths_config_file()
    if filename == "sme_ui_prefs.json":
        return sme_context_file()
    if filename == "sme_context.json":
        return user_dir / "sme_context.json"
    return user_dir / filename


def _export_mysql_sql(
    settings: Settings,
    *,
    tables: list[str] | None = None,
    snapshot_data: dict | None = None,
) -> tuple[str, dict[str, int]]:
    """Use the same consistent, typed dump as closure backups."""
    import tempfile
    from sme_gend.services.mysql_backup_service import _python_sql_dump
    from sme_gend.services.mysql_private_options import _restrict_folder

    with tempfile.TemporaryDirectory(prefix="sme_backup_dump_") as folder:
        directory = Path(folder)
        _restrict_folder(directory)
        dump = directory / "database.sql"
        options = {}
        if snapshot_data is not None:
            options["snapshot_reader"] = lambda connection, names: _capture_backup_sidecars(connection, names, snapshot_data)
        counts = _python_sql_dump(settings, dump, selected_tables=tables, **options)
        return dump.read_text(encoding="utf-8"), counts


def _capture_backup_sidecars(connection, table_names, result):
    """Read shared configuration and counts in the SQL dump's exact snapshot."""
    configs = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(),@@server_uuid")
        selected_database,server_uuid=cursor.fetchone()
        if result.get("include_app_config") and "app_config" in table_names:
            cursor.execute("SELECT config_key,payload,updated_by,updated_at FROM app_config ORDER BY config_key")
            for key, payload, actor, stamp in cursor.fetchall():
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    raise ValueError("Configuration partagée invalide : sauvegarde annulée.")
                configs.append(dict(config_key=str(key), payload=payload, updated_by=actor,
                    updated_at=stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp or "")))
        audit = {}
        if "audit_journal" in table_names:
            cursor.execute("SELECT module,COUNT(*) FROM audit_journal GROUP BY module")
            audit = {str(module or "—"): int(count) for module, count in cursor.fetchall()}
    result.update(captured=True, app_config_rows=configs, audit_by_module=audit,
        consistency={"database": "innodb-repeatable-read", "sql_and_shared_config": "same_transaction",
                     "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                     "database_name": str(selected_database), "server_uuid": str(server_uuid),
                     "external_files": "captured_separately_with_hashes"})


def _backup_file_inventory(archive):
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('hash', 'Calcul des empreintes des fichiers'),)) as operation:
        return _backup_file_inventory_with_progress(archive, operation)


def _backup_file_inventory_with_progress(archive, operation):
    from copy import copy
    result = {}
    total = sum(info.file_size for info in archive.infolist() if info.filename != MANIFEST_NAME)
    operation.start('hash', total=total)
    processed = 0
    for info in archive.infolist():
        if info.filename == MANIFEST_NAME:
            continue
        digest = hashlib.sha256()
        # ZipInfo.from_file on Windows retains a backslash orig_filename even
        # though write() stores a normalized '/' local header. Reopening the ZIP
        # would normalize this too; use an isolated read descriptor here.
        read_info = copy(info)
        read_info.orig_filename = info.filename
        with archive.open(read_info) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                processed += len(block)
                operation.update('hash', current=processed, total=total, detail=f'{info.filename} ; {processed:,}/{total:,} octet(s) contrôlé(s)')
        result[info.filename] = {"sha256": digest.hexdigest(), "size": info.file_size}
    operation.finish('hash', detail=f'{len(result)} fichier(s) vérifié(s)')
    return result


def _verify_backup_file_inventory(archive, inventory):
    if inventory is None:
        return  # Existing v1-v4 backups remain readable.
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('hash', 'Lecture des fichiers et calcul des empreintes'),
                             ('check', 'Comparaison des empreintes de sécurité'))) as operation:
        return _verify_backup_file_inventory_with_progress(archive, inventory, operation)


def _verify_backup_file_inventory_with_progress(archive, inventory, operation):
    names = {info.filename for info in archive.infolist() if info.filename != MANIFEST_NAME}
    if not isinstance(inventory, dict) or set(inventory) != names:
        raise ValueError("Inventaire des fichiers de sauvegarde incomplet ou ambigu.")
    total = sum(info.file_size for info in archive.infolist() if info.filename != MANIFEST_NAME)
    operation.start('hash', total=total)
    processed = 0
    digests = {}
    for info in archive.infolist():
        if info.filename == MANIFEST_NAME:
            continue
        item = inventory[info.filename]
        if (not isinstance(item, dict) or type(item.get("size")) is not int
                or item["size"] != info.file_size or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))):
            raise ValueError("Inventaire des fichiers de sauvegarde invalide.")
        digest = hashlib.sha256()
        with archive.open(info) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                processed += len(block)
                operation.update('hash', current=processed, total=total, detail=f'{info.filename} ; {processed:,}/{total:,} octet(s) contrôlé(s)')
        digests[info.filename] = digest.hexdigest()
    operation.finish('hash', detail=f'{len(names)} empreinte(s) calculée(s)')
    operation.start('check', total=len(digests))
    for index, (name, digest) in enumerate(digests.items(), 1):
        if digest != inventory[name]['sha256']:
            raise ValueError("Sauvegarde altérée : " + name)
        operation.update('check', current=index, total=len(digests), detail=f'{index}/{len(digests)} empreinte(s) vérifiée(s) ; {name}')
    operation.finish('check', detail=f'{len(names)} fichier(s) intègre(s)')


def _split_sql_statements(sql: str) -> list[str]:
    """Découpe le dump en instructions (gère les ; en fin de ligne)."""
    return split_statements(sql)


def _mysql_tables_for_restore(
    manifest: BackupManifest,
    restore_scope: RestoreScope,
) -> set[str] | None:
    """Ensemble de tables autorisées, ou ``None`` = toutes celles du dump."""
    saved = manifest.saved_scope()
    if saved.mysql_tables:
        base = set(saved.mysql_tables)
    else:
        base = set(manifest.table_counts.keys()) if manifest.table_counts else None
    if restore_scope.mysql_tables:
        pick = set(restore_scope.mysql_tables)
        if base is not None:
            return base & pick
        return pick
    return base


def _app_config_rows_for_restore(
    rows: list[dict[str, Any]],
    *,
    preserve_local_accounts: bool,
    preserve_runtime_state: bool = False,
) -> list[dict[str, Any]]:
    """Keep local accounts and live transition metadata when requested."""
    if preserve_runtime_state:
        from sme_gend.services.configuration_restore import operational_key
        rows = [row for row in rows if not operational_key(str(row.get("config_key", "")))]
    if not preserve_local_accounts:
        return rows
    from sme_gend.services.app_config_store import KEY_AUTH

    return [
        row
        for row in rows
        if str(row.get("config_key", "")).strip().casefold() != KEY_AUTH.casefold()
    ]


def _snapshot_local_auth(settings: Settings) -> dict[str, Any] | None:
    from sme_gend.services.app_config_store import KEY_AUTH

    # Une panne de lecture ne doit pas devenir « aucun compte à préserver ».
    raw = AppConfigRepository(settings).get_json(KEY_AUTH)
    if isinstance(raw, dict) and raw.get("users"):
        return raw
    return None


def _restore_local_auth(settings: Settings, payload: dict[str, Any]) -> None:
    from sme_gend.services.app_config_store import KEY_AUTH, save_config_dict

    save_config_dict(settings, KEY_AUTH, payload, updated_by="restore_keep_accounts")


def _should_run_sql_statement(
    stmt: str,
    *,
    allowed_tables: set[str] | None,
    preserve_audit_journal: bool,
) -> bool:
    s = stmt.strip()
    if not s or s.startswith("--"):
        return False
    validate_restore_statement(s)
    if is_safe_setting(s):
        return True
    if target_table(s) is None:
        raise ValueError("Instruction SQL non prise en charge dans cette sauvegarde.")
    from sme_gend.services.database_maintenance import INTERNAL_TABLES
    if any(_sql_statement_affects_table(s, table) for table in INTERNAL_TABLES):
        return False
    if preserve_audit_journal and _sql_statement_affects_table(s, AUDIT_JOURNAL_TABLE):
        return False
    if allowed_tables is None:
        return True
    return any(_sql_statement_affects_table(s, tbl) for tbl in allowed_tables)


def _media_rel_allowed(rel: str, scope: RestoreScope) -> bool:
    r = rel.replace("\\", "/")
    if r.startswith("photos/"):
        return scope.restore_media_photos
    if r.startswith("grades/"):
        return scope.restore_media_grades
    return False


def create_backup(
    settings: Settings,
    dest_zip: Path,
    *,
    scope: BackupScope | None = None,
) -> BackupManifest:
    """Crée une archive .zip (complète ou partielle selon ``scope``)."""
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('prepare', 'Préparation de la sauvegarde'),
                             ('sql', 'Export cohérent de la base MySQL'),
                             ('files', 'Copie des paramètres et des médias'),
                             ('publications', 'Sauvegarde des documents publiés'),
                             ('verify', 'Vérification de l’intégrité des fichiers'),
                             ('publish', 'Enregistrement de la sauvegarde'))) as operation:
        return _create_backup_with_progress(settings, dest_zip, scope=scope, operation=operation)


def _create_backup_with_progress(settings, dest_zip, *, scope, operation):
    operation.start('prepare')
    sc = scope or BackupScope.full()
    dest_zip = Path(dest_zip).expanduser().resolve()
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.suffix.lower() != ".zip":
        dest_zip = dest_zip.with_suffix(".zip")

    counts: dict[str, int] = {}
    sql_text = ""
    snapshot_data = {"include_app_config": sc.include_app_config}
    operation.finish('prepare')
    operation.start('sql')
    if sc.include_mysql:
        tbl_list = list(sc.mysql_tables) if sc.mysql_tables else None
        sql_text, counts = _export_mysql_sql(settings, tables=tbl_list, snapshot_data=snapshot_data)
        if not snapshot_data.get("captured"):
            raise RuntimeError("L'instantané SQL et ses configurations n'ont pas été confirmés ensemble.")
    operation.finish('sql', detail=f'{len(counts)} table(s), {sum(counts.values()):,} ligne(s)' if sc.include_mysql else 'Base MySQL non sélectionnée')
    audit_by_module = snapshot_data.get("audit_by_module", {})
    config_paths = _collect_config_paths() if sc.include_user_config else []
    media_files = (
        _iter_personnel_media_files(
            settings,
            include_photos=sc.include_media_photos,
            include_grades=sc.include_media_grades,
        )
        if (sc.include_media_photos or sc.include_media_grades)
        else []
    )
    media_photos = sum(1 for _p, rel in media_files if rel.startswith("photos/"))
    media_grades = sum(1 for _p, rel in media_files if rel.startswith("grades/"))
    created = datetime.now(timezone.utc).isoformat()

    app_config_rows: list[dict[str, Any]] = []
    if sc.include_app_config:
        if sc.include_mysql:
            app_config_rows = snapshot_data["app_config_rows"]
        else:
            try:
                app_config_rows = AppConfigRepository(settings).export_all_config_rows()
            except Exception as exc:
                raise RuntimeError("Sauvegarde interrompue : configuration partagée inaccessible.") from exc

    config_arcs: list[str] = []
    app_data_arcs: list[str] = []
    data_root = project_root() / "data"
    data_files = [fp for fp in data_root.iterdir() if fp.is_file() and fp.suffix.lower() in (".json", ".jsonl")
                  and fp.name != "paths.json"] if sc.include_app_data and data_root.is_dir() else []
    total_files = len(config_paths) + len(media_files) + len(data_files) + int(bool(sc.include_mysql and sql_text)) + int(bool(sc.include_app_config and app_config_rows))
    operation.start('files', total=total_files)
    copied_files = 0
    def copied(name):
        nonlocal copied_files
        copied_files += 1
        operation.update('files', current=copied_files, total=total_files, detail=f'{copied_files}/{total_files} fichier(s) ; {name}')
    with atomic_destination(dest_zip, temporary_suffix=".zip") as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            if sc.include_mysql and sql_text:
                zf.writestr(SQL_DUMP_NAME, sql_text.encode("utf-8"))
                copied(SQL_DUMP_NAME)
            if sc.include_app_config and app_config_rows:
                zf.writestr(
                    APP_CONFIG_DUMP_NAME,
                    json.dumps(app_config_rows, indent=2, ensure_ascii=False).encode(
                        "utf-8"
                    ),
                )
                copied(APP_CONFIG_DUMP_NAME)
            for cfg in config_paths:
                arc = f"{CONFIG_DIR}/{cfg.name}"
                if cfg.name == "paths.json":
                    safe_config = json.loads(cfg.read_text(encoding="utf-8"))
                    if not isinstance(safe_config, dict):
                        raise ValueError("Configuration des chemins invalide : sauvegarde annulée.")
                    safe_config.pop("mysql_password", None)
                    zf.writestr(arc, json.dumps(safe_config, ensure_ascii=False).encode("utf-8"))
                else:
                    zf.write(cfg, arcname=arc)
                config_arcs.append(arc)
                copied(arc)
            for fp, rel in media_files:
                zf.write(fp, arcname=f"{PERSONNEL_MEDIA_DIR}/{rel}")
                copied(f"{PERSONNEL_MEDIA_DIR}/{rel}")
            for fp in data_files:
                arc = f"{DATA_DIR}/{fp.name}"
                zf.write(fp, arcname=arc)
                app_data_arcs.append(fp.name)
                copied(arc)
            operation.finish('files')
            operation.start('publications')
            from sme_gend.services.sme_publication_backup import add_to_backup
            publication_files = add_to_backup(settings, zf) if sc.include_publications else []
            operation.finish('publications', detail=f'{len(publication_files)} fichier(s) de publication')
            operation.start('verify')
            kind = "full" if sc.is_full() else "partial"
            manifest = BackupManifest(
                format_version=BACKUP_FORMAT_VERSION,
                created_at=created,
                mysql_database=settings.mysql_database,
                mysql_host=settings.mysql_host,
                table_counts=counts,
                config_files=config_arcs,
                app_config_exported=bool(app_config_rows),
                personnel_media_files=len(media_files),
                audit_journal_by_module=audit_by_module or None,
                app_data_files=app_data_arcs or None,
                backup_kind=kind,
                backup_scope=sc.to_dict(),
                personnel_media_photos=media_photos,
                personnel_media_grades=media_grades,
                publication_files=publication_files or None,
                snapshot_consistency=snapshot_data.get("consistency"),
                file_inventory=_backup_file_inventory(zf),
            )
            zf.writestr(MANIFEST_NAME, json.dumps(manifest.to_dict(), indent=2).encode("utf-8"))
            operation.finish('verify')
            operation.start('publish', detail='Fermeture et enregistrement atomique de l’archive')
        if not sc.include_mysql and not config_arcs and not media_files and not app_data_arcs and not app_config_rows and not publication_files:
            raise ValueError("Sauvegarde vide : sélectionnez au moins un composant.")
    operation.finish('publish', detail=f'Archive enregistrée : {dest_zip.name}')
    _log.info(
        "Sauvegarde %s créée : %s (%s tables, %s médias)",
        kind,
        dest_zip,
        len(counts),
        len(media_files),
    )
    return manifest


def create_full_backup(settings: Settings, dest_zip: Path) -> BackupManifest:
    """Crée une archive .zip de sauvegarde complète."""
    return create_backup(settings, dest_zip, scope=BackupScope.full())


def _validate_backup_zip(zip_path: Path) -> BackupManifest:
    with zipfile.ZipFile(zip_path, "r") as zf:
        seen = set()
        for entry in zf.infolist():
            parts = relative_parts(entry.filename)
            token = "/".join(parts).casefold()
            if token in seen or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Archive ambiguë : doublon de chemin ou lien symbolique.")
            seen.add(token)
        if zf.testzip() is not None:
            raise ValueError("Archive endommagée : contrôle CRC invalide.")
        names = zf.namelist()
        if MANIFEST_NAME not in names and "month_backup.json" in names:
            raise ValueError("Cette archive contient un seul mois. Utilisez le bouton Restaurer un mois.")
        if MANIFEST_NAME not in names:
            raise ValueError("Archive invalide : manifest.json absent.")
        raw = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        _verify_backup_file_inventory(zf, raw.get("file_inventory"))
    manifest = BackupManifest.from_dict(raw)
    from sme_gend.services.sme_publication_backup import PREFIX, validate_in_backup
    with zipfile.ZipFile(zip_path, "r") as publications:
        validate_in_backup(publications, manifest.publication_files)
    if manifest.format_version not in (1, 2, 3, BACKUP_FORMAT_VERSION):
        raise ValueError(
            f"Version de sauvegarde non supportée : {manifest.format_version}"
        )
    has_payload = (
        SQL_DUMP_NAME in names
        or any(n.startswith(CONFIG_DIR + "/") for n in names)
        or any(n.startswith(DATA_DIR + "/") for n in names)
        or APP_CONFIG_DUMP_NAME in names
        or any(n.startswith(PERSONNEL_MEDIA_DIR + "/") for n in names)
        or any(n.startswith(PREFIX + "/") for n in names)
    )
    if manifest.format_version <= 3 and SQL_DUMP_NAME not in names:
        raise ValueError("Archive invalide : mysql_dump.sql absent.")
    if manifest.format_version >= 4 and not has_payload:
        raise ValueError("Archive invalide : aucun composant sauvegardé.")
    return manifest


def _execute_restore_sql(settings: Settings, statements: list[str]) -> None:
    """Connexion dédiée : aucun état de session de restauration ne rejoint le pool."""
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('safety', 'Sauvegarde de sécurité'),
                             ('restore', 'Exécution de la restauration SQL'),
                             ('verify', 'Contrôle de la restauration'),
                             ('commit', 'Validation et publication'))) as operation:
        return _execute_restore_sql_with_progress(settings, statements, operation)


def _execute_restore_sql_with_progress(settings, statements, operation):
    import pymysql

    operation.start('safety')
    safety_backup = ensure_recent_backup(settings, max_age_days=0.0)
    if safety_backup is None:
        raise RuntimeError("Restauration annulée : sauvegarde préalable impossible.")
    operation.finish('safety')
    operation.start('restore', total=len(statements))
    if any(statement.lstrip().upper().startswith(("DROP", "CREATE")) for statement in statements):
        from sme_gend.services.mysql_restore_staging import prepared_restore
        from sme_gend.services.mysql_private_options import _restrict_folder
        from uuid import uuid4
        workspace = default_backup_directory() / "restore_sessions" / uuid4().hex
        workspace.mkdir(parents=True)
        _restrict_folder(workspace)
        try:
            with prepared_restore(settings, statements, workspace) as prepared:
                operation.finish('restore')
                operation.start('verify')
                prepared.validate()
                operation.finish('verify')
                operation.start('commit', detail='Bascule atomique des tables ; attente du serveur')
                try:
                    prepared.publish()
                except Exception:
                    prepared.rollback()
                    raise
        except Exception as exc:
            raise RuntimeError(
                "Restauration interrompue avant finalisation. Vérifiez les copies conservées. "
                f"Journal de récupération : {workspace}"
            ) from exc
        operation.finish('commit', detail='Tables publiées et contrôlées')
        return
    conn = pymysql.connect(**MySQLConnection(settings).connect_params())
    original_fk = None
    from sme_gend.services.database_maintenance import coordinated_write
    write_gate = coordinated_write(settings, conn)
    gate_entered = False
    try:
        write_gate.__enter__()
        gate_entered = True
        with conn.cursor() as cur:
            cur.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
            original_fk = int(cur.fetchone()[0])
            number = 0
            try:
                for number, statement in enumerate(statements, 1):
                    cur.execute(statement)
                    if number % 250 == 0 or number == len(statements):
                        operation.update('restore', current=number, total=len(statements),
                            detail=f'{number:,}/{len(statements):,} instruction(s) exécutée(s)')
                operation.finish('restore')
                operation.start('verify')
                operation.finish('verify', detail='Toutes les instructions ont été acceptées par MySQL')
                operation.start('commit', detail='Validation de la transaction ; attente du serveur')
                conn.commit()
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    _log.warning("Rollback de restauration impossible ; connexion dédiée abandonnée.", exc_info=True)
                raise RuntimeError(
                    f"Restauration SQL interrompue à l'instruction {number}. "
                    "Des opérations de schéma peuvent déjà avoir été appliquées. "
                    f"Sauvegarde préalable : {safety_backup}"
                ) from exc
            finally:
                # Même une erreur de nettoyage ne doit pas laisser une connexion
                # de restauration persistante dans le pool applicatif.
                try:
                    cur.execute("SET SESSION FOREIGN_KEY_CHECKS = %s", (original_fk,))
                except Exception:
                    _log.warning("État de restauration non rétabli ; connexion dédiée fermée.", exc_info=True)
    finally:
        try:
            import sys
            try:
                if gate_entered:
                    write_gate.__exit__(*sys.exc_info())
            finally:
                conn.close()
        except Exception:
            _log.warning("Connexion de restauration déjà interrompue.", exc_info=True)
    operation.finish('commit', detail='Transaction validée')


def inspect_backup(zip_path: Path) -> BackupManifest:
    return _validate_backup_zip(Path(zip_path).resolve())


def restore_backup(
    settings: Settings,
    zip_path: Path,
    *,
    scope: RestoreScope | None = None,
) -> BackupManifest:
    """
    Restaure les composants sélectionnés depuis une archive (complète ou partielle).
    """
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('read', 'Vérification de la sauvegarde'),
                             ('files', 'Préparation des fichiers à restaurer'),
                             ('safety', 'Sauvegarde de sécurité'),
                             ('restore', 'Restauration des données'),
                             ('verify', 'Contrôles de cohérence'),
                             ('publish', 'Application et finalisation'))) as operation:
        return _restore_backup_with_progress(settings, zip_path, scope=scope, operation=operation)


def _restore_backup_with_progress(settings, zip_path, *, scope, operation):
    operation.start('read')
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("system.restore"):
        raise PermissionError("Permission de restauration refusée.")
    from io import BytesIO
    from uuid import uuid4
    from sme_gend.services.restore_files import RestoreFiles

    zip_path = Path(zip_path).resolve()
    rs = scope or RestoreScope.full()
    workspace = default_backup_directory() / "restore_sessions" / uuid4().hex
    files = RestoreFiles(workspace)
    stable_archive = workspace / "source.zip"
    shutil.copyfile(zip_path, stable_archive)
    manifest = _validate_backup_zip(stable_archive)

    # Tout chemin et tout contenu structuré sont contrôlés avant les écritures.
    with zipfile.ZipFile(stable_archive, "r") as source:
        plan = []
        if rs.restore_publications:
            from sme_gend.services.sme_publication_backup import restore_plan
            plan.extend(restore_plan(source, manifest.publication_files))
        for entry in source.infolist():
            if entry.is_dir():
                continue
            parts = relative_parts(entry.filename)
            if parts[0] == PERSONNEL_MEDIA_DIR and _media_rel_allowed("/".join(parts[1:]), rs):
                # Resolve the destination later, while the media coordination is held.
                plan.append((entry.filename, None, "media"))
            elif parts[0] == CONFIG_DIR and rs.restore_user_config:
                if len(parts) != 2:
                    raise ValueError("Structure de configuration inattendue dans l'archive.")
                if parts[1] not in _SKIP_CONFIG_FILES:
                    target = _config_restore_target(parts[1])
                    plan.append((entry.filename, target, "config"))
                    if parts[1] == "paths.json":
                        raw_paths = json.loads(source.read(entry).decode("utf-8"))
                        if not isinstance(raw_paths, dict):
                            raise ValueError("Configuration des chemins invalide.")
            elif parts[0] == DATA_DIR and rs.restore_app_data:
                if len(parts) != 2:
                    raise ValueError("Structure de données locales inattendue dans l'archive.")
                if not (rs.preserve_audit_journal and _is_audit_mirror_data_file(parts[1])):
                    target = contained_path(project_root() / "data", parts[1])
                    plan.append((entry.filename, target, "data"))
        raw_cfg = None
        if rs.restore_app_config and APP_CONFIG_DUMP_NAME in source.namelist():
            raw_cfg = validate_config_rows(json.loads(source.read(APP_CONFIG_DUMP_NAME).decode("utf-8")))
        sql_text = source.read(SQL_DUMP_NAME).decode("utf-8") if rs.restore_mysql and SQL_DUMP_NAME in source.namelist() else ""
    allowed = _mysql_tables_for_restore(manifest, rs)
    statements = []
    for stmt in _split_sql_statements(sql_text):
        if _should_run_sql_statement(stmt, allowed_tables=allowed,
                                     preserve_audit_journal=rs.preserve_audit_journal):
            statements.append(stmt)
    created_tables = {target_table(stmt) for stmt in statements if stmt.lstrip().upper().startswith("CREATE")}
    from sme_gend.services.schema_migrations import PUBLICATION_TABLES
    publication_business_tables=set(PUBLICATION_TABLES)-{'sme_publication_write_gate'}
    if created_tables & publication_business_tables and not publication_business_tables <= created_tables:
        raise ValueError("Restaurez le registre des publications et tous ses fichiers SQL ensemble.")
    membership_tables = {'agent_effectif_movement', 'sme_month_membership'}
    if created_tables & membership_tables and not membership_tables <= created_tables:
        raise ValueError("Restaurez les mouvements datés et leur appartenance mensuelle ensemble.")
    if rs.restore_mysql and sql_text:
        expected_tables = set(manifest.table_counts) if allowed is None else set(allowed)
        from sme_gend.services.database_maintenance import INTERNAL_TABLES
        expected_tables.difference_update(INTERNAL_TABLES)
        if rs.preserve_audit_journal:
            expected_tables.discard(AUDIT_JOURNAL_TABLE)
        if expected_tables != created_tables:
            raise ValueError("Archive SQL incomplète : les tables ne correspondent pas au manifeste et à la sélection.")
    local_auth: dict[str, Any] | None = None
    if rs.preserve_local_accounts and (rs.restore_mysql or rs.restore_app_config):
        local_auth = _snapshot_local_auth(settings)
    if statements and not rs.maintenance_confirmed:
        raise RuntimeError("Restauration MySQL refusée : confirmez la maintenance et la fermeture des autres postes.")

    partial_requested = bool(rs.mysql_tables or manifest.saved_scope().mysql_tables)
    config_rows = _app_config_rows_for_restore(raw_cfg, preserve_local_accounts=local_auth is not None,
        preserve_runtime_state=not rs.restore_mysql or partial_requested) if raw_cfg is not None else []
    with zipfile.ZipFile(stable_archive, "r") as structured_source:
        for name, target, kind in plan:
            if kind in ("config", "data") and target.suffix.lower() == ".json":
                json.loads(structured_source.read(name).decode("utf-8"))
    if not statements:
        from sme_gend.services.configuration_restore import restore_configuration_components
        operation.finish('read')
        return restore_configuration_components(settings, stable_archive, manifest, rs, config_rows, plan, files,
                                                operation=operation)
    operation.finish('read', detail=f'{len(statements):,} instruction(s) SQL à restaurer')
    operation.start('files', total=len(plan))
    plan = [(name, contained_path(resolved_personnel_media_root(), "/".join(relative_parts(name)[1:])) if kind == "media" else target, kind)
            for name, target, kind in plan]
    with zipfile.ZipFile(stable_archive, "r") as source:
        for index, (name, target, kind) in enumerate(plan, 1):
            if kind == "config" and target.name == "paths.json" and (rs.preserve_mysql_connection or (rs.restore_publications and manifest.publication_files)):
                from sme_gend.services.restore_files import digest
                expected_paths = digest(target)
                restored_paths = json.loads(source.read(name).decode("utf-8"))
                if rs.restore_publications and manifest.publication_files:
                    from sme_gend.services.sme_publication_backup import receiving_archive_paths
                    restored_paths = receiving_archive_paths(restored_paths, target)
                content = (_merged_paths_json(restored_paths, target) if rs.preserve_mysql_connection else
                           json.dumps(restored_paths, ensure_ascii=False).encode("utf-8"))
                files.add(target, BytesIO(content), expected_before=expected_paths)
            else:
                with source.open(name) as payload:
                    if kind == "publication":
                        files.add(target, payload, expected_before=None)
                    else:
                        files.add(target, payload)
            operation.update('files', current=index, total=len(plan), detail=f'{index}/{len(plan)} fichier(s) préparé(s) ; {name}')
    operation.finish('files')

    if statements:
        from sme_gend.services.mysql_restore_staging import prepared_restore
        operation.start('safety')
        with operation.subtask('safety', current=0, total=2, detail='Création de la sauvegarde préalable'):
            safety = ensure_recent_backup(settings, max_age_days=0.0)
        if safety is None or not safety.is_file() or not safety.stat().st_size:
            raise RuntimeError("Restauration annulée : sauvegarde préalable absente ou vide.")
        retained_safety = workspace / "safety.zip"
        with operation.subtask('safety', current=1, total=2, detail='Conservation de la copie de récupération'):
            shutil.copyfile(safety, retained_safety)
            atomic_write(workspace / "safety_backup.txt", str(retained_safety).encode("utf-8"))
        operation.finish('safety', detail=f'Sauvegarde de sécurité conservée : {retained_safety.name}')
        operation.start('restore')
        try:
            with prepared_restore(settings, statements, workspace, manifest.table_counts) as prepared:
                from sme_gend.services.configuration_restore import apply_prepared_configuration
                apply_prepared_configuration(prepared, config_rows, local_auth, created_tables=created_tables,
                    preserve_audit=rs.preserve_audit_journal, full_requested=not partial_requested)
                operation.finish('restore')
                operation.start('verify')
                prepared.validate()
                from dataclasses import replace
                from sme_gend.services.sme_publication_registry import validate_registry_snapshot
                # The prepared database contains either the retained registry
                # or a full restored family. Validate its bytes before switching.
                if publication_business_tables & set(prepared.prepared):
                    if not publication_business_tables <= set(prepared.prepared):
                        raise ValueError("Registre des publications incomplet dans la restauration préparée.")
                    validate_registry_snapshot(replace(settings,mysql_database=prepared.new))
                prepared.record_restore_audit(archive_name=zip_path.name, preserve_audit=rs.preserve_audit_journal)
                operation.finish('verify')
                operation.start('publish', detail='Bascule atomique de la base et publication des fichiers')
                try:
                    prepared.publish()
                    files.publish()
                    files.complete()
                    prepared.record("completed")
                except BaseException:
                    # Attempt both compensations even when one fails. Neither
                    # copy of the database is deleted on any error.
                    failures = []
                    for compensate in (files.rollback, prepared.rollback):
                        try: compensate()
                        except Exception as failure: failures.append(failure)
                    if failures:
                        raise RuntimeError("Récupération à terminer en maintenance ; ne rouvrez pas les postes.") from failures[0]
                    raise
        except BaseException as exc:
            from sme_gend.services.configuration_restore import IncompatibleRestoreError
            if isinstance(exc, IncompatibleRestoreError):
                raise
            raise RuntimeError(
                "Restauration interrompue. Les copies de récupération sont conservées. "
                f"Maintenez la maintenance et vérifiez le journal : {workspace}"
            ) from exc
        from sme_gend.repositories.mysql_pool import invalidate_mysql_pool
        from sme_gend.repositories.mysql_common import invalidate_mysql_schema_cache
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        invalidate_mysql_pool(settings)
        invalidate_mysql_schema_cache()
        invalidate_liste_cache(settings)
        operation.finish('publish', detail='Base, fichiers et caches actualisés')
        _log.info("Restauration préparée terminée ; récupération conservée dans %s", workspace)
        return manifest



def restore_full_backup(
    settings: Settings,
    zip_path: Path,
    *,
    restore_user_config: bool = True,
    restore_app_data: bool = True,
    restore_personnel_media: bool = True,
    preserve_mysql_connection: bool = True,
    preserve_audit_journal: bool = True,
    preserve_local_accounts: bool = True,
    maintenance_confirmed: bool = False,
    restore_publications: bool = True,
) -> BackupManifest:
    """Restauration complète (API historique — préférer ``restore_backup`` + ``RestoreScope``)."""
    scope = RestoreScope(
        restore_mysql=True,
        restore_user_config=restore_user_config,
        restore_app_data=restore_app_data,
        restore_app_config=True,
        restore_media_photos=restore_personnel_media,
        restore_media_grades=restore_personnel_media,
        preserve_mysql_connection=preserve_mysql_connection,
        preserve_audit_journal=preserve_audit_journal,
        preserve_local_accounts=preserve_local_accounts,
        maintenance_confirmed=maintenance_confirmed,
        restore_publications=restore_publications,
    )
    return restore_backup(settings, zip_path, scope=scope)


def default_backup_filename(*, partial: bool = False) -> str:
    from uuid import uuid4
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S%f") + "_" + uuid4().hex[:12]
    if partial:
        return f"sme_backup_partial_{stamp}.zip"
    return f"sme_backup_{stamp}.zip"


def default_backup_directory() -> Path:
    from sme_gend.services.app_health_service import backup_storage_dir

    return backup_storage_dir()


def create_backup_to_default_dir(settings: Settings) -> Path:
    """Sauvegarde rapide dans le dossier ``%LocalAppData%/SME_Gendarmerie/backups``."""
    dest = default_backup_directory() / default_backup_filename()
    create_full_backup(settings, dest)
    return dest


def prune_old_backups(*, keep: int = 15, directory: Path | None = None) -> int:
    """Supprime les archives les plus anciennes au-delà de ``keep`` fichiers."""
    from sme_gend.services.app_health_service import list_backup_archives

    keep = max(1, int(keep))
    archives = list_backup_archives()
    if directory is not None:
        archives = [p for p in archives if p.parent.resolve() == directory.resolve()]
    removed = 0
    for p in archives[keep:]:
        try:
            p.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            _log.warning("Suppression sauvegarde %s : %s", p, exc)
    return removed


def ensure_recent_backup(
    settings: Settings,
    *,
    max_age_days: float = 7.0,
    keep_after_prune: int = 15,
) -> Path | None:
    """
    Crée une sauvegarde si aucune archive récente n'existe.
    Retourne le chemin de l'archive (existante ou nouvelle).
    """
    from sme_gend.services.app_health_service import backup_age_days, find_latest_backup_zip

    latest = find_latest_backup_zip()
    age = backup_age_days(latest)
    if latest is not None and age is not None and age <= max_age_days:
        return latest
    dest = create_backup_to_default_dir(settings)
    prune_old_backups(keep=keep_after_prune, directory=dest.parent)
    return dest
