"""Explicit, checksummed schema changes; capability checks never execute DDL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
from contextlib import contextmanager

from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction

LEDGER = "_sme_schema_migration"
PUBLICATION_TABLES = ("sme_publication_write_gate", "sme_publication_period", "sme_publication",
                      "sme_publication_artifact", "sme_publication_chunk")
TEMPORAL_INDEXES = (
    ("agent_position_event", "idx_sme_position_agent_date", ("agent_id", "date_debut", "id")),
    ("agent_passation_grade", "idx_sme_grade_date", ("date_passation", "id")),
    ("agent_diplome", "idx_sme_diploma_date", ("date_obtention", "id")),
    ("agent_affectation", "idx_sme_affectation_date", ("date_affectation", "id")),
    ("mouvement_effectif", "idx_sme_movement_date", ("date_reference", "id")),
    ("agent_mission", "idx_sme_mission_date", ("date_debut", "id")),
    ("parcours_stage", "idx_sme_stage_date", ("date_debut", "id")),
    ("agent_malade", "idx_sme_illness_date", ("date_debut", "id")),
    ("agent_desertion", "idx_sme_desertion_date", ("date_desertion", "id")),
    ("agent_conge", "idx_sme_leave_date", ("date_debut", "id")),
)


class SchemaMigrationRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    capability: str
    filename: str
    tables: tuple[str, ...]
    indexes: tuple[tuple[str, str, tuple[str, ...]], ...] = ()


def migrations():
    return (Migration("0001", "publication_registry", "0001_publication_registry.sql", PUBLICATION_TABLES),
            Migration("0002", "temporal_projection", "0002_temporal_projection.sql",
                      ("sme_temporal_change", "sme_projection_dirty"),
                      TEMPORAL_INDEXES),
            Migration("0003", "dated_movements", "0003_dated_movements.sql",
                      ("agent_effectif_movement", "sme_month_membership")))


def _source(migration):
    from sme_gend.config.settings import project_root
    path = project_root() / "data" / "schema" / "migrations" / migration.filename
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    from sme_gend.services.schema_contracts import definition as schema_definition
    definition = raw + "\n" + json.dumps({"indexes": migration.indexes, "contract": schema_definition((LEDGER, *migration.tables))},
                                         sort_keys=True, separators=(",", ":"))
    return raw, sha256(definition.encode("utf-8")).hexdigest()


def schema_migration_status(settings):
    """Read current capability state, including partial/modified migrations."""
    with MySQLConnection(settings).connect(read_only=True) as connection:
        from sme_gend.services.schema_contracts import inspect_schema, errors
        schema = inspect_schema(connection)
        tables = schema["tables"]
        ledger_errors = errors((LEDGER,), schema) if LEDGER in tables else []
        indexes = {key: list(value[1]) for key, value in schema["indexes"].items()}
        with connection.cursor() as cursor:
            ledger = {}
            if LEDGER in tables and not ledger_errors:
                cursor.execute(f"SELECT version,checksum,state FROM `{LEDGER}`")
                ledger = {str(version): (checksum, state) for version, checksum, state in cursor.fetchall()}
    result = []
    for migration in migrations():
        _, checksum = _source(migration)
        recorded = ledger.get(migration.version)
        state = "pending" if recorded is None else recorded[1]
        if ledger_errors:
            state = "ledger_incompatible"
        if recorded is not None and recorded[0] != checksum:
            state = "modified"
        missing = [name for name in migration.tables if tables.get(name) != "INNODB"]
        missing_indexes = [table + "." + name for table, name, columns in migration.indexes
                           if indexes.get((table, name)) != list(columns)]
        contract_errors = [*ledger_errors, *errors(migration.tables, schema)]
        if state == "applied" and (missing or missing_indexes or contract_errors):
            state = "incomplete"
        result.append({"version": migration.version, "capability": migration.capability,
                       "state": state, "checksum": checksum, "missing_tables": missing,
                       "missing_indexes": missing_indexes, "contract_errors": contract_errors})
    return result


def capability_available(settings, capability):
    from sme_gend.repositories.mysql_connection import transaction_local_cache, mysql_database_key
    cache = transaction_local_cache("schema_capabilities")
    key = mysql_database_key(settings)
    if cache is None:
        status = schema_migration_status(settings)
    else:
        if key not in cache:
            cache[key] = schema_migration_status(settings)
        status = cache[key]
    return any(row["capability"] == capability and row["state"] == "applied" for row in status)


def require_capability(settings, capability):
    if not capability_available(settings, capability):
        raise SchemaMigrationRequired("Migration de la base requise : " + capability +
                                      ". Un administrateur doit appliquer le plan de migration explicite.")


@contextmanager
def _verified_safety_backup(settings, backup):
    proof = backup.snapshot_consistency
    if (not isinstance(proof, dict) or proof.get("sql_and_shared_config") != "same_transaction"
            or not backup.file_inventory):
        raise ValueError("Recréez une sauvegarde complète cohérente avec identité du serveur et inventaire vérifié.")
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(),@@server_uuid")
            database, server_uuid = cursor.fetchone()
            if proof.get("server_uuid") != server_uuid or proof.get("database_name") != database:
                raise ValueError("La sauvegarde provient d'une autre base ou d'un autre serveur.")
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'")
            from sme_gend.services.database_maintenance import INTERNAL_TABLES
            current = {str(row[0]) for row in cursor.fetchall()} - set(INTERNAL_TABLES)
            if not current.issubset(set(backup.table_counts)):
                raise ValueError("La sauvegarde ne contient pas toutes les tables actuelles de la base.")
    yield


@contextmanager
def _compatible_existing_schema(settings):
    from sme_gend.services.schema_contracts import inspect_schema, errors
    with MySQLConnection(settings).connect(read_only=True) as connection:
        schema = inspect_schema(connection)
    tables = (LEDGER, *(table for migration in migrations() for table in migration.tables))
    problems = errors(tables, schema, only_existing=True)
    for migration in migrations():
        for table, index, columns in migration.indexes:
            if schema["tables"].get(table) != "INNODB" or any((table, name) not in schema["columns"] for name in columns):
                problems.append(table + ": structure métier requise avant la migration des index")
            existing = schema["indexes"].get((table, index))
            if existing is not None and existing != (1, columns):
                problems.append(table + "." + index + ": index préexistant incompatible")
    if problems:
        raise ValueError("Une table préexistante est incompatible ; aucune adoption automatique : " + "; ".join(problems[:10]))
    yield


def apply_schema_migrations(settings, *, expected_database, safety_backup: Path,
                            maintenance_confirmed=False, allow_resume=False):
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    if not session_has_admin_role() or not session_has_permission("system.migrate"):
        raise PermissionError("La migration de schéma exige un administrateur autorisé.")
    if not maintenance_confirmed:
        raise ValueError("Confirmez la maintenance avant l'application du schéma.")
    if has_import_transaction(settings):
        raise RuntimeError("Une migration ne peut pas rejoindre une transaction métier.")
    if str(expected_database) != settings.mysql_database:
        raise ValueError("Le nom de base confirmé ne correspond pas à la configuration.")
    initial = schema_migration_status(settings)
    if any(row["state"] == "ledger_incompatible" for row in initial):
        raise ValueError("Le registre de migrations préexistant est incompatible ; examen manuel requis.")
    if all(row["state"] == "applied" for row in initial):
        return {"applied": [], "warnings": [], "backup": str(safety_backup)}
    if any(row["state"] == "modified" for row in initial):
        raise ValueError("Une migration déjà enregistrée a été modifiée ; examen manuel requis.")
    if not allow_resume and any(row["state"] in ("running", "failed", "incomplete") for row in initial):
        raise ValueError("Une migration interrompue exige une reprise explicite après vérification.")
    from sme_gend.services.backup_restore_service import inspect_backup
    backup_path = Path(safety_backup).resolve(strict=True)
    backup = inspect_backup(backup_path)
    if (backup.backup_kind != "full" or not backup.table_counts
            or backup.mysql_database != settings.mysql_database):
        raise ValueError("Une sauvegarde complète de la base cible est nécessaire avant sa migration.")
    from sme_gend.repositories.mysql_connection import schema_metadata_lock
    from sme_gend.services.database_maintenance import centralized_maintenance
    from sme_gend.repositories.mysql_common import _split_sql_statements
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    applied = []
    warnings = []
    with schema_metadata_lock(settings), _verified_safety_backup(settings, backup), _compatible_existing_schema(settings), \
            centralized_maintenance(settings, reason="Migration explicite du schéma SME"):
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(),@@lower_case_table_names,@@server_uuid")
                selected, insensitive, server_uuid = cursor.fetchone()
                if (server_uuid != backup.snapshot_consistency["server_uuid"] or
                        (selected != expected_database and not (insensitive in (1, 2) and str(selected).casefold() == expected_database.casefold()))):
                    raise ValueError("La connexion a changé de base ; migration refusée.")
                cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{LEDGER}` (
                    version VARCHAR(32) PRIMARY KEY, capability VARCHAR(64) NOT NULL,
                    checksum CHAR(64) NOT NULL, state ENUM('running','applied','failed') NOT NULL,
                    started_at DATETIME(6) NOT NULL, finished_at DATETIME(6) NULL,
                    actor VARCHAR(128) NOT NULL, safety_backup TEXT NOT NULL, error_detail TEXT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            connection.commit()
            plan = {row["version"]: row for row in schema_migration_status(settings)}
            for migration in migrations():
                state = plan[migration.version]["state"]
                if state == "applied":
                    continue
                if state == "modified":
                    raise ValueError("Une migration déjà enregistrée a été modifiée ; examen manuel requis.")
                if state in ("running", "failed", "incomplete") and not allow_resume:
                    raise ValueError("Une migration interrompue exige une reprise explicite après vérification.")
                sql, checksum = _source(migration)
                statements = _split_sql_statements(sql)
                # New migrations are additive. DDL commits are not transactional;
                # retries therefore require idempotent CREATE/INSERT IGNORE only.
                if any(not re.match(r"^(?:CREATE TABLE IF NOT EXISTS|INSERT IGNORE INTO)\b", statement, re.I)
                       for statement in statements):
                    raise ValueError("Migration non additive/non idempotente refusée.")
                actor = resolve_audit_actor()
                with connection.cursor() as cursor:
                    cursor.execute(f"INSERT INTO `{LEDGER}`(version,capability,checksum,state,started_at,actor,safety_backup) "
                        "VALUES(%s,%s,%s,'running',UTC_TIMESTAMP(6),%s,%s) "
                        "ON DUPLICATE KEY UPDATE state='running',started_at=UTC_TIMESTAMP(6),finished_at=NULL,error_detail=NULL",
                        (migration.version, migration.capability, checksum, actor, str(backup_path)))
                connection.commit()
                try:
                    with connection.cursor() as cursor:
                        for statement in statements:
                            cursor.execute(statement)
                        for table, index, columns in migration.indexes:
                            cursor.execute("SELECT column_name FROM information_schema.statistics "
                                           "WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s ORDER BY seq_in_index",
                                           (table, index))
                            existing = tuple(row[0] for row in cursor.fetchall())
                            if existing and existing != columns:
                                raise ValueError("Un index existant porte une définition différente : " + table + "." + index)
                            if not existing:
                                cursor.execute(f"ALTER TABLE `{table}` ADD INDEX `{index}` (" +
                                               ",".join("`" + column + "`" for column in columns) + ")")
                        from sme_gend.services.schema_contracts import inspect_schema, errors
                        shape_errors = errors(migration.tables, inspect_schema(connection))
                        if shape_errors:
                            raise ValueError("Le schéma créé ne respecte pas son contrat : " + "; ".join(shape_errors[:10]))
                        if migration.capability == "publication_registry":
                            from sme_gend.services.sme_publication_registry import validate_registry_snapshot
                            validate_registry_snapshot(settings)
                        cursor.execute(f"UPDATE `{LEDGER}` SET state='applied',finished_at=UTC_TIMESTAMP(6) WHERE version=%s", (migration.version,))
                    connection.commit()
                    applied.append(migration.version)
                    try:
                        AppConfigRepository(settings).append_audit(action="schema_migrated", module="schema_migration",
                            actor=actor, payload={"version": migration.version, "checksum": checksum,
                                                  "safety_backup": str(backup_path)})
                    except Exception:
                        warnings.append("Migration " + migration.version + " appliquée ; journal central à vérifier.")
                except BaseException as exc:
                    try:
                        connection.rollback()
                        with connection.cursor() as cursor:
                            cursor.execute(f"UPDATE `{LEDGER}` SET state='failed',finished_at=UTC_TIMESTAMP(6),error_detail=%s WHERE version=%s",
                                           (str(exc)[:1000], migration.version))
                        connection.commit()
                        try:
                            AppConfigRepository(settings).append_audit(action="schema_migration_failed", module="schema_migration",
                                actor=actor, payload={"version": migration.version, "checksum": checksum,
                                    "safety_backup": str(backup_path), "error": str(exc)[:1000]})
                        except Exception:
                            pass  # The durable ledger remains the recovery authority.
                    except Exception:
                        # A connection failure may leave the durable marker
                        # 'running'. Both states require explicit resume.
                        pass
                    raise RuntimeError("Migration interrompue ; sauvegarde conservée. Une reprise explicite est nécessaire.") from exc
    return {"applied": applied, "warnings": warnings, "backup": str(backup_path)}
