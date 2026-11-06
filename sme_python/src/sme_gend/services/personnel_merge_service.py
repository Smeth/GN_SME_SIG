"""Rapprochement explicite de deux identités, sans écrasement des historiques."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import hashlib
import json
import re

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key
from sme_gend.services.auth_guard import session_has_admin_role, session_can_modify_personnel, session_can_delete_personnel
from sme_gend.services.personnel_identity_assessment import assess_identity, identity_value


IDENTITY_FIELDS = ("noms", "date_naissance", "lieu_naissance", "sexe", "telephone", "adresse_residence",
                   "matricule_militaire", "matricule_solde")
STRICT_FIELDS = ("noms", "date_naissance", "matricule_militaire", "matricule_solde")


def _permission():
    if not session_has_admin_role() or not session_can_modify_personnel() or not session_can_delete_personnel():
        raise PermissionError("Le rapprochement nécessite un administrateur habilité à modifier et retirer des fiches.")


def _identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise RuntimeError("Identifiant SQL non pris en charge : rapprochement refusé.")
    return f"`{value}`"


def _serial(value):
    if isinstance(value, bytes):
        return value.hex()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _token(value):
    return hashlib.sha256(json.dumps(value, default=_serial, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _rows(cursor, query, params=()):
    cursor.execute(query, params)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _same(a, b):
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


@dataclass(frozen=True)
class PersonnelMergePreview:
    source: str
    target: str
    source_name: str
    target_name: str
    counts: dict
    months: tuple
    conflicts: tuple
    notes: tuple
    fills: dict
    token: str
    database: tuple


def _snapshot(settings, source, target, *, lock=False):
    if not source or not target or source.casefold() == target.casefold():
        raise ValueError("Choisissez deux identifiants de fiche distincts.")
    suffix = " FOR UPDATE" if lock else ""
    conflicts, notes = [], []
    with MySQLConnection(settings).connect(read_only=not lock) as connection:
        with connection.cursor() as cursor:
            agents = _rows(cursor, "SELECT * FROM agent WHERE legacy_access_id IN (%s,%s) ORDER BY id" + suffix, (source, target))
            by_legacy = {str(row["legacy_access_id"]).casefold(): row for row in agents}
            if source.casefold() not in by_legacy or target.casefold() not in by_legacy:
                raise ValueError("Une fiche est introuvable ou a déjà été rapprochée. Actualisez les alertes.")
            src, dst = by_legacy[source.casefold()], by_legacy[target.casefold()]
            source, target = str(src["legacy_access_id"]), str(dst["legacy_access_id"])
            identity = assess_identity(src, dst)
            if not identity.eligible:
                conflicts.append(identity.label + ". " + identity.explanation)
            cursor.execute("SELECT id,annee_mois FROM sme_periode")
            periods = dict(cursor.fetchall())
            fills = {}
            for field in IDENTITY_FIELDS:
                old, kept = src.get(field), dst.get(field)
                a = identity_value(field, old) if field in STRICT_FIELDS else old
                b = identity_value(field, kept) if field in STRICT_FIELDS else kept
                if a not in (None, "") and b in (None, ""):
                    fills[field] = old
                elif a not in (None, "") and b not in (None, "") and not _same(a, b):
                    if field in STRICT_FIELDS:
                        continue  # Divergences déjà détaillées par l'évaluation commune.
                    else:
                        notes.append(f"{field} : la valeur cible « {kept} » est conservée ; la valeur source « {old} » reste dans l’archive de rapprochement.")
            if "date_naissance" in fills:
                from sme_gend.services.auth_guard import session_may_edit_age
                if not session_may_edit_age():
                    conflicts.append("Compléter la date de naissance nécessite la permission de modification de l’âge.")
            cursor.execute("SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() "
                           "AND REFERENCED_TABLE_NAME='agent'")
            foreign_keys = list(cursor.fetchall())
            for table, column in foreign_keys:
                if column != "agent_id" and (table, column) != ("agent_identity_alias", "canonical_agent_id"):
                    conflicts.append(f"Lien non pris en charge : {table}.{column}. Aucun rapprochement automatique possible.")
            cursor.execute("SELECT c.TABLE_NAME,t.ENGINE FROM information_schema.COLUMNS c JOIN information_schema.TABLES t "
                           "ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
                           "WHERE c.TABLE_SCHEMA=DATABASE() AND c.COLUMN_NAME='agent_id' ORDER BY c.TABLE_NAME")
            tables = list(cursor.fetchall())
            histories, indexes = {}, {}
            for table, engine in tables:
                name = _identifier(table)
                if str(engine).upper() != "INNODB" or (table, "agent_id") not in foreign_keys:
                    conflicts.append(f"Table {table} : lien ou transaction non garanti ; contrôle manuel nécessaire.")
                    continue
                rows = _rows(cursor, f"SELECT * FROM {name} WHERE agent_id IN (%s,%s) ORDER BY id" + suffix, (src["id"], dst["id"]))
                histories[table] = rows
                source_rows = [row for row in rows if row["agent_id"] == src["id"]]
                target_rows = [row for row in rows if row["agent_id"] == dst["id"]]
                point_field = {"agent_affectation": "date_affectation", "agent_passation_grade": "date_passation",
                               "agent_desertion": "date_desertion"}.get(table)
                for old in source_rows:
                    for kept in target_rows:
                        if point_field and old.get(point_field) and old.get(point_field) == kept.get(point_field):
                            conflicts.append(f"Deux événements {table} à la même date {old[point_field]} (source {old['id']}, cible {kept['id']}). Vérifiez les historiques.")
                        if table in {"agent_mission", "agent_malade", "agent_conge", "parcours_stage", "agent_position_event"}:
                            if old.get("date_debut") and kept.get("date_debut"):
                                if old["date_debut"] <= (kept.get("date_fin") or date.max) and kept["date_debut"] <= (old.get("date_fin") or date.max):
                                    conflicts.append(f"Périodes d’événements qui se chevauchent : {table}, source {old['id']}, cible {kept['id']}. Vérifiez les deux événements avant rapprochement.")
                            else:
                                notes.append(f"Dates incomplètes dans {table} (source {old['id']}, cible {kept['id']}) : absence de chevauchement non vérifiable automatiquement.")
                if table != "carriere_situation":
                    technical = {"id", "agent_id", "cree_le", "updated_le", "created_at", "updated_at", "import_lot_id", "source_ligne_legacy"}
                    target_facts = {_token({k: v for k, v in row.items() if k not in technical}): row["id"]
                                    for row in rows if row["agent_id"] == dst["id"]}
                    for row in rows:
                        fact = _token({k: v for k, v in row.items() if k not in technical})
                        if row["agent_id"] == src["id"] and fact in target_facts:
                            conflicts.append(f"Événement possiblement déjà présent : {table}, source {row['id']}, cible {target_facts[fact]}. Vérifiez les deux historiques.")
                cursor.execute("SELECT INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX FROM information_schema.STATISTICS "
                               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND NON_UNIQUE=0 ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,))
                unique = {}
                for index, column, _ in cursor.fetchall():
                    unique.setdefault(index, []).append(column)
                indexes[table] = unique
                for index, columns in unique.items():
                    if any(column is None for column in columns):
                        if any(row["agent_id"] == src["id"] for row in rows):
                            conflicts.append(f"Index fonctionnel non pris en charge : {table}.{index}.")
                        continue
                    if "agent_id" not in columns:
                        continue
                    others = [column for column in columns if column != "agent_id"]
                    comparisons = " AND ".join(f"s.{_identifier(col)}=t.{_identifier(col)}" for col in others) or "1=1"
                    cursor.execute(f"SELECT s.id,t.id FROM {name} s JOIN {name} t ON t.agent_id=%s "
                                   f"AND {comparisons} WHERE s.agent_id=%s", (dst["id"], src["id"]))
                    for a, b in cursor.fetchall():
                        conflicts.append(f"Conflit {table} ({index}) : historique source {a} et cible {b}. Aucune ligne ne sera écrasée.")
            career = histories.get("carriere_situation", [])
            if sum(bool(row.get("est_actuelle")) for row in career) > 1:
                conflicts.append("Plusieurs situations sont marquées actuelles. Corrigez les indicateurs avant de rapprocher les fiches.")
            left = [row for row in career if row["agent_id"] == src["id"]]
            right = [row for row in career if row["agent_id"] == dst["id"]]
            for a in left:
                for b in right:
                    if a.get("sme_periode_id") is not None and a.get("sme_periode_id") == b.get("sme_periode_id"):
                        conflicts.append(f"Même période {periods.get(a['sme_periode_id'], a['sme_periode_id'])} sur les deux fiches : situations {a['id']} et {b['id']}.")
                    if a.get("date_debut") and b.get("date_debut"):
                        if a["date_debut"] <= (b.get("date_fin") or date.max) and b["date_debut"] <= (a.get("date_fin") or date.max):
                            conflicts.append(f"Dates de carrière qui se chevauchent : situations {a['id']} et {b['id']}.")
            months = tuple(sorted({periods.get(row.get("sme_periode_id"), "Sans mois") for row in left}))
            aliases = _rows(cursor, "SELECT * FROM agent_identity_alias WHERE canonical_agent_id IN (%s,%s) "
                            "ORDER BY alias_legacy_id" + suffix, (src["id"], dst["id"]))
            # Dates/ids/source_ligne_legacy sont conservés : seul le propriétaire change.
            state = {"agents": agents, "histories": histories, "indexes": indexes, "aliases": aliases,
                     "foreign_keys": sorted(foreign_keys), "tables": tables, "periods": periods}
            counts = {table: sum(row["agent_id"] == src["id"] for row in rows) for table, rows in histories.items()}
            notes.append("Les journaux et acquittements existants restent inchangés, sous leurs identifiants d’origine. Les anciens identifiants sont conservés comme alias.")
            preview = PersonnelMergePreview(source, target, src.get("noms") or "", dst.get("noms") or "", counts, months,
                tuple(sorted(set(conflicts))), tuple(notes), fills, _token(state), mysql_database_key(settings))
    return preview, state, src, dst


def preview_personnel_merge(settings, source, target):
    _permission()
    return _snapshot(settings, str(source).strip(), str(target).strip())[0]


def apply_personnel_merge(settings, preview, *, reason):
    _permission()
    if preview.database != mysql_database_key(settings):
        raise ValueError("La base a changé. Recalculez l’aperçu.")
    if not str(reason or "").strip():
        raise ValueError("Le motif du rapprochement est obligatoire.")
    if len(str(reason).strip()) > 2000:
        raise ValueError("Le motif du rapprochement est limité à 2000 caractères.")
    if preview.conflicts:
        raise ValueError("Le rapprochement est bloqué par les conflits de l’aperçu.")
    from sme_gend.repositories.mysql_connection import has_import_transaction
    if has_import_transaction(settings):
        raise RuntimeError("Le rapprochement doit être lancé hors d’une autre opération en cours.")
    from sme_gend.services.backup_restore_service import create_backup_to_default_dir
    backup = create_backup_to_default_dir(settings)
    if backup is None or not Path(backup).is_file():
        raise RuntimeError("Rapprochement annulé : la sauvegarde préalable n’est pas disponible.")
    from sme_gend.services.shared_reference_write import shared_data_write, mark_shared_data_write
    from sme_gend.services.business_audit import record_business_action
    from sme_gend.services.auth_context import resolve_audit_actor
    with shared_data_write(settings, reason="personnel_merge"):
        _permission()
        fresh, state, source, target = _snapshot(settings, preview.source, preview.target, lock=True)
        if fresh.token != preview.token or fresh.conflicts:
            raise RuntimeError("Les données ont changé ou un conflit est apparu. Aucun transfert : recalculez l’aperçu.")
        _permission()
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                for table, count in fresh.counts.items():
                    if count:
                        sample = state["histories"][table][0]
                        preserve = "".join(f",{_identifier(name)}={_identifier(name)}" for name in ("updated_le", "updated_at") if name in sample)
                        cursor.execute(f"UPDATE {_identifier(table)} SET agent_id=%s{preserve} WHERE agent_id=%s", (target["id"], source["id"]))
                if fresh.fills:
                    assignments = ",".join(f"{_identifier(field)}=%s" for field in fresh.fills)
                    cursor.execute(f"UPDATE agent SET {assignments} WHERE id=%s", tuple(fresh.fills.values()) + (target["id"],))
                cursor.execute("UPDATE agent_identity_alias SET canonical_agent_id=%s WHERE canonical_agent_id=%s", (target["id"], source["id"]))
                cursor.execute("INSERT INTO agent_identity_alias (alias_legacy_id,canonical_agent_id,source_agent_id,source_snapshot,merged_by,reason) "
                               "VALUES (%s,%s,%s,%s,%s,%s)", (fresh.source, target["id"], source["id"],
                               json.dumps(source, default=_serial, ensure_ascii=False), resolve_audit_actor(), str(reason).strip()))
                cursor.execute("DELETE FROM agent WHERE id=%s", (source["id"],))
        record_business_action(settings, action="personnel_merge", module="personnel", entity_id=fresh.target,
            before={"source": source, "target": target}, after={"source_alias": fresh.source,
            "target": fresh.target, "transferred": fresh.counts, "months": fresh.months,
            "filled_fields": fresh.fills, "reason": str(reason).strip(), "backup": str(backup)})
        from sme_gend.services.audit_log import append_personnel_audit
        before = {**target, "access_join_id": fresh.target}
        after = {**before, **fresh.fills}
        changes = {"identity_merge": {"old": fresh.source, "new": fresh.target}}
        changes.update({field: {"old": target.get(field), "new": value} for field, value in fresh.fills.items()})
        append_personnel_audit(action="update", personnel_id=fresh.target,
            changes=json.loads(json.dumps(changes, default=_serial)),
            snapshot_before=json.loads(json.dumps(before, default=_serial)),
            snapshot_after=json.loads(json.dumps(after, default=_serial)),
            edited_by=resolve_audit_actor(),
            edit_motif=f"Rapprochement de la fiche {fresh.source} vers {fresh.target} : {str(reason).strip()}")
        mark_shared_data_write(True)
    return {"source": fresh.source, "target": fresh.target, "transferred": fresh.counts, "backup": str(backup)}
