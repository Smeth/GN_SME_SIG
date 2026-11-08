"""Required shapes for explicit migrations, including pre-existing table collisions."""
from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class Column:
    kind: str
    nullable: bool = False
    length: int | None = None
    unsigned: bool = False
    default: str = "__unspecified__"
    auto: bool = False
    values: tuple[str, ...] = ()


C = Column
CONTRACTS = {
    "_sme_schema_migration": {"version": C("varchar", length=32), "capability": C("varchar", length=64),
        "checksum": C("char", length=64), "state": C("enum", values=("running", "applied", "failed")),
        "started_at": C("datetime"), "finished_at": C("datetime", nullable=True), "actor": C("varchar", length=128),
        "safety_backup": C("text"), "error_detail": C("text", nullable=True)},
    "sme_publication_write_gate": {"id": C("tinyint")},
    "sme_publication_period": {"mois": C("char", length=7), "next_revision": C("int", unsigned=True, default="1"),
        "current_validated_id": C("bigint", nullable=True, unsigned=True), "version": C("bigint", unsigned=True, default="0")},
    "sme_publication": {
        "id": C("bigint", unsigned=True, auto=True), "revision_id": C("varchar", length=64), "mois": C("char", length=7),
        "revision_no": C("int", unsigned=True), "status": C("enum", default="brouillon", values=("brouillon", "valide", "remplace")),
        "version": C("bigint", unsigned=True, default="1"), "created_at": C("datetime"), "created_by": C("varchar", length=128),
        "validated_at": C("datetime", nullable=True), "validated_by": C("varchar", nullable=True, length=128),
        "validation_note": C("text", nullable=True), "replaced_by_id": C("bigint", nullable=True, unsigned=True),
        "dataset_footprint": C("char", length=64), "manifest": C("json")},
    "sme_publication_artifact": {"publication_id": C("bigint", unsigned=True), "artifact_name": C("varchar", length=32),
        "byte_size": C("bigint", unsigned=True), "sha256": C("char", length=64), "chunk_count": C("int", unsigned=True)},
    "sme_publication_chunk": {"publication_id": C("bigint", unsigned=True), "artifact_name": C("varchar", length=32),
        "chunk_index": C("int", unsigned=True), "content": C("mediumblob")},
    "sme_temporal_change": {"id": C("bigint", auto=True), "change_uid": C("char", length=32), "agent_id": C("bigint"),
        "source_table": C("varchar", length=64), "source_id": C("bigint"), "mutation": C("varchar", length=32),
        "effective_from": C("date", nullable=True), "effective_until": C("date", nullable=True), "recorded_at": C("datetime"),
        "actor": C("varchar", length=128), "reason": C("text", nullable=True), "before_state": C("json", nullable=True),
        "after_state": C("json", nullable=True), "provenance": C("json", nullable=True)},
    "sme_projection_dirty": {"agent_id": C("bigint"), "component": C("varchar", length=32), "effective_from": C("date"),
        "requested_token": C("char", length=32), "requested_at": C("datetime"), "state": C("varchar", length=16, default="pending"),
        "processed_token": C("char", nullable=True, length=32), "processed_at": C("datetime", nullable=True),
        "last_error": C("text", nullable=True)},
    "agent_effectif_movement": {"id": C("bigint", auto=True), "agent_id": C("bigint"), "category": C("varchar", length=8),
        "movement_kind": C("varchar", length=64), "effective_date": C("date"), "recorded_at": C("datetime"),
        "recorded_by": C("varchar", length=128), "reference_note": C("varchar", nullable=True, length=256),
        "details": C("text", nullable=True), "version": C("bigint", default="1")},
    "sme_month_membership": {"agent_id": C("bigint"), "sme_periode_id": C("bigint"), "is_member": C("tinyint"),
        "baseline_member": C("tinyint", default="1"),
        "event_id": C("bigint", nullable=True), "updated_at": C("datetime")},
}
PRIMARY = {"_sme_schema_migration": ("version",), "sme_publication_write_gate": ("id",), "sme_publication_period": ("mois",), "sme_publication": ("id",),
    "sme_publication_artifact": ("publication_id", "artifact_name"),
    "sme_publication_chunk": ("publication_id", "artifact_name", "chunk_index"),
    "sme_temporal_change": ("id",), "sme_projection_dirty": ("agent_id", "component"),
    "agent_effectif_movement": ("id",), "sme_month_membership": ("agent_id", "sme_periode_id")}
UNIQUE = {"sme_publication": (("revision_id",), ("mois", "revision_no")), "sme_temporal_change": (("change_uid",),),
          "agent_effectif_movement": (("agent_id", "effective_date"),)}
FOREIGN = {
    "sme_publication": ((("mois",), "sme_publication_period", ("mois",), "RESTRICT"),),
    "sme_publication_artifact": ((("publication_id",), "sme_publication", ("id",), "RESTRICT"),),
    "sme_publication_chunk": ((("publication_id", "artifact_name"), "sme_publication_artifact", ("publication_id", "artifact_name"), "RESTRICT"),),
    "sme_projection_dirty": ((("agent_id",), "agent", ("id",), "CASCADE"),),
    "agent_effectif_movement": ((("agent_id",), "agent", ("id",), "CASCADE"),),
    "sme_month_membership": ((("agent_id",), "agent", ("id",), "CASCADE"),
        (("sme_periode_id",), "sme_periode", ("id",), "CASCADE"),
        (("event_id",), "agent_effectif_movement", ("id",), "SET NULL")),
}
CHECKS = {"agent_effectif_movement": ("category IN ('GAIN','PERTE')",),
          "sme_month_membership": ("is_member IN (0,1)", "baseline_member IN (0,1)")}


def definition(tables):
    return {table: {"columns": {name: asdict(column) for name, column in CONTRACTS[table].items()},
        "primary": PRIMARY[table], "unique": UNIQUE.get(table, ()), "foreign": FOREIGN.get(table, ()),
        "checks": CHECKS.get(table, ())} for table in tables}


def _check_expression(value):
    value = str(value).replace("\\'", "'").lower()
    value = re.sub(r"_[a-z0-9]+\s*(?=')", "", value)
    return re.sub(r"[`\s()]", "", value)


def inspect_schema(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name,engine FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'")
        tables = {str(table): str(engine).upper() for table, engine in cursor.fetchall()}
        cursor.execute("SELECT table_name,column_name,data_type,is_nullable,character_maximum_length,column_type,column_default,extra "
                       "FROM information_schema.columns WHERE table_schema=DATABASE()")
        columns = {(str(row[0]), str(row[1])): row[2:] for row in cursor.fetchall()}
        cursor.execute("SELECT table_name,index_name,non_unique,GROUP_CONCAT(column_name ORDER BY seq_in_index),"
                       "GROUP_CONCAT(COALESCE(sub_part,0) ORDER BY seq_in_index) "
                       "FROM information_schema.statistics WHERE table_schema=DATABASE() GROUP BY table_name,index_name,non_unique")
        indexes = {(str(table), str(name)): (int(non_unique), tuple(column if prefix == "0" else column + "(" + prefix + ")"
                    for column, prefix in zip(str(names).split(","), str(prefixes).split(","))))
                   for table, name, non_unique, names, prefixes in cursor.fetchall()}
        cursor.execute("SELECT table_name,constraint_name,delete_rule FROM information_schema.referential_constraints "
                       "WHERE constraint_schema=DATABASE()")
        delete_rules = {(str(table), str(name)): "RESTRICT" if rule == "NO ACTION" else str(rule)
                        for table, name, rule in cursor.fetchall()}
        cursor.execute("SELECT table_name,constraint_name,referenced_table_name,"
                       "GROUP_CONCAT(column_name ORDER BY ordinal_position),"
                       "GROUP_CONCAT(referenced_column_name ORDER BY ordinal_position) "
                       "FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND referenced_table_name IS NOT NULL "
                       "GROUP BY table_name,constraint_name,referenced_table_name")
        foreign = {}
        for table, name, other, local, remote in cursor.fetchall():
            foreign.setdefault(str(table), []).append((tuple(str(local).split(",")), str(other), tuple(str(remote).split(",")),
                                                        delete_rules.get((str(table), str(name)))))
        cursor.execute("SELECT t.table_name,c.check_clause,t.enforced FROM information_schema.table_constraints t "
                       "JOIN information_schema.check_constraints c ON c.constraint_schema=t.constraint_schema "
                       "AND c.constraint_name=t.constraint_name WHERE t.table_schema=DATABASE() AND t.constraint_type='CHECK'")
        checks = {}
        for table, clause, enforced in cursor.fetchall():
            if str(enforced).upper() == "YES":
                checks.setdefault(str(table), []).append(_check_expression(clause))
        gate = None
        if ("sme_publication_write_gate", "id") in columns:
            cursor.execute("SELECT id FROM sme_publication_write_gate")
            gate = [row[0] for row in cursor.fetchall()]
    return {"tables": tables, "columns": columns, "indexes": indexes, "foreign": foreign, "checks": checks, "gate": gate}


def errors(tables, schema, *, only_existing=False):
    problems = []
    for table in tables:
        if table not in schema["tables"] and only_existing:
            continue
        if schema["tables"].get(table) != "INNODB":
            problems.append(table + ": table InnoDB requise")
            continue
        for name, expected in CONTRACTS[table].items():
            actual = schema["columns"].get((table, name))
            if actual is None:
                problems.append(table + "." + name + ": colonne absente")
                continue
            kind, nullable, length, declaration, default, extra = actual
            invalid = (kind != expected.kind or (nullable == "YES") != expected.nullable
                or (expected.length is not None and (length is None or length < expected.length))
                or (expected.unsigned and "unsigned" not in declaration.lower())
                or (expected.auto and "auto_increment" not in extra.lower())
                or (expected.default != "__unspecified__" and str(default) != expected.default)
                or any("'" + value + "'" not in declaration for value in expected.values))
            if invalid:
                problems.append(table + "." + name + ": définition incompatible")
        if schema["indexes"].get((table, "PRIMARY")) != (0, PRIMARY[table]):
            problems.append(table + ": clé primaire incompatible")
        for required in UNIQUE.get(table, ()):
            if not any(owner == table and specification == (0, required)
                       for (owner, _), specification in schema["indexes"].items()):
                problems.append(table + ": unicité manquante " + ",".join(required))
        for required in FOREIGN.get(table, ()):
            if required not in schema["foreign"].get(table, ()):
                problems.append(table + ": liaison étrangère manquante")
        for required in CHECKS.get(table, ()):
            if _check_expression(required) not in schema["checks"].get(table, ()):
                problems.append(table + ": contrainte de valeurs incompatible : " +
                                repr(schema["checks"].get(table, ())))
    if "sme_publication_write_gate" in tables and schema["gate"] is not None:
        allowed = ([], [1]) if only_existing else ([1],)
        if schema["gate"] not in allowed:
            problems.append("sme_publication_write_gate: singleton invalide")
    return problems
