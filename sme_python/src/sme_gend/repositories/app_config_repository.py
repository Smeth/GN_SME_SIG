"""Configuration et préférences applicatives en MySQL (multi-poste)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection


def audit_date_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive local calendar dates, converted separately across DST boundaries."""
    if any(value is not None and type(value) is not date for value in (date_from, date_to)):
        raise ValueError("Les dates de recherche sont invalides.")
    if date_from and date_to and date_from > date_to:
        raise ValueError("La date de début doit précéder la date de fin.")
    def boundary(day: date) -> datetime:
        return datetime.combine(day, time.min).astimezone(timezone.utc).replace(tzinfo=None)
    try:
        return (boundary(date_from) if date_from else None,
                boundary(date_to + timedelta(days=1)) if date_to else None)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Les dates de recherche sont hors de la plage prise en charge.") from exc


def _audit_contains(expression: str) -> str:
    # LIKE applies the accent-insensitive collation; an explicit escape keeps literals literal.
    collation = " COLLATE utf8mb4_unicode_ci"
    return ("CONVERT(COALESCE(" + expression + ", '') USING utf8mb4)" + collation +
            " LIKE CONVERT(%s USING utf8mb4)" + collation + " ESCAPE '='")


def _audit_needle(value: str) -> str:
    return "%" + value.replace("=", "==").replace("%", "=%").replace("_", "=_") + "%"


def _document_search_date_bounds(query):
    import re
    raw = str(query).strip()
    formats = [(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", "%d/%m/%Y", timedelta(days=1)),
        (r"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}", "%d/%m/%Y %H:%M", timedelta(minutes=1)),
        (r"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}", "%d/%m/%Y %H:%M:%S", timedelta(seconds=1))]
    for pattern, fmt, span in formats:
        if re.fullmatch(pattern, raw):
            try:
                local = datetime.strptime(raw, fmt)
                return (local.astimezone(timezone.utc).replace(tzinfo=None),
                    (local + span).astimezone(timezone.utc).replace(tzinfo=None))
            except (ValueError, OverflowError, OSError):
                return None
    return None


def _doc_gen_where(*, doc_types=None, mois_filter="", search_query="", type_labels=None):
    clauses = ["(module = 'doc_gen' OR action LIKE 'doc=gen%%' ESCAPE '=')"]
    params = []
    dtype = "JSON_UNQUOTE(JSON_EXTRACT(payload, '$.doc_type'))"
    if doc_types is not None:
        if not doc_types:
            clauses.append("0=1")
        else:
            clauses.append(dtype + " IN (" + ",".join(["%s"] * len(doc_types)) + ")")
            params.extend(doc_types)
    if mois_filter:
        clauses.append("LEFT(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.mois')), %s) = %s")
        params.extend((len(mois_filter), mois_filter))
    if search_query:
        from sme_gend.services.search_text import normalize_search_text
        fields = ["actor"] + ["JSON_UNQUOTE(JSON_EXTRACT(payload, '$." + key + "'))"
            for key in ("doc_type", "format", "mois", "persons", "dest", "count", "meta")]
        predicates = [_audit_contains(field) for field in fields]
        params.extend([_audit_needle(search_query)] * len(predicates))
        if type_labels:
            case = "CASE " + dtype
            for key, label in type_labels.items():
                case += " WHEN %s THEN %s"
                params.extend((key, label))
            case += " ELSE " + dtype + " END"
            predicates.append(_audit_contains("CONCAT(" + case + ", ' (', UPPER(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.format'))), ')')"))
            params.append(_audit_needle(search_query))
        bounds = _document_search_date_bounds(search_query)
        if bounds is not None:
            predicates.append("(ts_utc >= %s AND ts_utc < %s)")
            params.extend(bounds)
        labels = [key for key,label in (type_labels or {}).items()
                  if normalize_search_text(search_query) in normalize_search_text(label)]
        if labels:
            predicates.append(dtype + " IN (" + ",".join(["%s"] * len(labels)) + ")")
            params.extend(labels)
        clauses.append("(" + " OR ".join(predicates) + ")")
    return " AND ".join(clauses), params


def _doc_gen_entries(rows):
    out: list[dict[str, Any]] = []
    for r in rows:
        pl = r[3]
        if isinstance(pl, str):
            try:
                pl = json.loads(pl)
            except json.JSONDecodeError:
                pl = {}
        elif not isinstance(pl, dict):
            pl = {}
        ts = r[0]
        if isinstance(ts, datetime):
            ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        out.append(
            {
                "ts_utc": ts_s,
                "doc_type": pl.get("doc_type", r[1]),
                "format": pl.get("format", ""),
                "count": pl.get("count", 1),
                "persons": pl.get("persons"),
                "dest": pl.get("dest"),
                "mois": pl.get("mois"),
                "meta": pl.get("meta"),
                "actor": r[2] or "",
            }
        )
    return out


def validate_config_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the whole batch before restoring any configuration key."""
    if not isinstance(rows, list):
        raise ValueError("Configuration partagée invalide dans la sauvegarde.")
    keys: set[str] = set()
    validated = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Ligne de configuration invalide dans la sauvegarde.")
        key, payload = raw.get("config_key"), raw.get("payload")
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > 64 or not isinstance(payload, dict):
            raise ValueError("Clé ou contenu de configuration invalide dans la sauvegarde.")
        key = key.strip()
        if key.casefold() in keys:
            raise ValueError("Clé de configuration dupliquée dans la sauvegarde.")
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        keys.add(key.casefold())
        validated.append({**raw, "config_key": key})
    return validated


class AppConfigRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mc = MySQLConnection(settings)

    def table_exists(self, name: str) -> bool:
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1
                    """,
                    (name,),
                )
                return cur.fetchone() is not None

    def ensure_tables(self) -> None:
        from sme_gend.config.settings import project_root

        from sme_gend.repositories.mysql_common import _split_sql_statements

        schema_files = (
            project_root() / "data" / "schema" / "mysql_app_config.sql",
            project_root() / "data" / "schema" / "mysql_workflow.sql",
            project_root() / "data" / "schema" / "mysql_career_alerts.sql",
            project_root() / "data" / "schema" / "mysql_auth_sessions.sql",
        )
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                for p in schema_files:
                    if not p.is_file():
                        continue
                    sql = p.read_text(encoding="utf-8")
                    for stmt in _split_sql_statements(sql):
                        try:
                            cur.execute(stmt)
                        except Exception as e:
                            err = str(e).lower()
                            if "duplicate" in err or "already exists" in err or "1061" in err:
                                continue
                            raise

    def get_json(self, config_key: str) -> dict[str, Any] | None:
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM app_config WHERE config_key = %s",
                    (config_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                raw = row[0]
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8")
                if isinstance(raw, str):
                    data = json.loads(raw)
                elif isinstance(raw, dict):
                    data = raw
                else:
                    return None
                return data if isinstance(data, dict) else None

    def set_json(
        self,
        config_key: str,
        payload: dict[str, Any],
        *,
        updated_by: str | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False)
        actor = (updated_by or "").strip() or None
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_config (config_key, payload, updated_by)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      payload = VALUES(payload),
                      updated_by = VALUES(updated_by),
                      updated_at = CURRENT_TIMESTAMP(3)
                    """,
                    (config_key, body, actor),
                )

    def set_json_if_revision(
        self, config_key: str, payload: dict[str, Any], *,
        expected_revision: int, updated_by: str | None = None,
    ) -> None:
        """Compare puis écrit sous verrou de ligne, sans écraser un autre poste."""
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app_config (config_key, payload) VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE config_key=VALUES(config_key)""",
                    (config_key, "{}"),
                )
                cur.execute("SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE", (config_key,))
                raw = cur.fetchone()[0]
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8")
                current = json.loads(raw) if isinstance(raw, str) else raw
                revision = int((current or {}).get("config_rev", 0))
                if revision != expected_revision:
                    raise ValueError(
                        "Les utilisateurs ou les droits ont été modifiés sur un autre poste. "
                        "Rechargez la page puis réessayez ; votre modification n'a pas été enregistrée."
                    )
                cur.execute(
                    """UPDATE app_config SET payload=%s, updated_by=%s,
                       updated_at=CURRENT_TIMESTAMP(3) WHERE config_key=%s""",
                    (json.dumps(payload, ensure_ascii=False), updated_by, config_key),
                )

    def has_key(self, config_key: str) -> bool:
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM app_config WHERE config_key = %s LIMIT 1",
                    (config_key,),
                )
                return cur.fetchone() is not None

    def get_user_pref(self, username: str, pref_key: str) -> dict[str, Any] | None:
        uname = (username or "").strip() or "_anonymous"
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload FROM app_user_preference
                    WHERE username = %s AND pref_key = %s
                    """,
                    (uname, pref_key),
                )
                row = cur.fetchone()
                if not row:
                    return None
                raw = row[0]
                if isinstance(raw, str):
                    data = json.loads(raw)
                elif isinstance(raw, dict):
                    data = raw
                else:
                    return None
                return data if isinstance(data, dict) else None

    def set_user_pref(
        self,
        username: str,
        pref_key: str,
        payload: dict[str, Any],
    ) -> None:
        uname = (username or "").strip() or "_anonymous"
        body = json.dumps(payload, ensure_ascii=False)
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_user_preference (username, pref_key, payload)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      payload = VALUES(payload),
                      updated_at = CURRENT_TIMESTAMP(3)
                    """,
                    (uname, pref_key, body),
                )

    def append_audit(
        self,
        *,
        action: str,
        actor: str | None = None,
        module: str | None = None,
        personnel_id: str | None = None,
        sme_mois_context: str | None = None,
        payload: dict[str, Any] | None = None,
        ts_utc: datetime | None = None,
        mirror: tuple | None = None,
    ) -> None:
        if not self.table_exists("audit_journal"):
            raise RuntimeError("Journal central absent : aucune trace confirmée.")
        ts = ts_utc or datetime.now(timezone.utc)
        ts_naive = ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
        ts_naive = ts_naive.replace(microsecond=(ts_naive.microsecond // 1000) * 1000)
        body = json.dumps(payload or {}, ensure_ascii=False, default=str) if payload else None
        pending = []
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_journal
                      (ts_utc, action, actor, module, personnel_id, sme_mois_context, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ts_naive,
                        action,
                        (actor or "").strip() or None,
                        (module or "").strip() or None,
                        (personnel_id or "").strip() or None,
                        (sme_mois_context or "").strip() or None,
                        body,
                    ),
                )
            if mirror is not None:
                from sme_gend.services.audit_mirror_recovery import enqueue_on_connection
                pending = enqueue_on_connection(conn, *mirror)

        if pending:
            from sme_gend.services.audit_mirror_recovery import schedule_delivery
            schedule_delivery(self._settings, pending)

    def append_audit_batch(self, entries: list[dict[str, Any]], *, mirror: tuple | None = None) -> None:
        """Insertions groupées dans ``audit_journal`` (imports liste générale)."""
        if not entries:
            return
        if not self.table_exists("audit_journal"):
            raise RuntimeError("Journal central absent : aucune trace confirmée.")
        rows: list[tuple] = []
        for item in entries:
            ts = item.get("ts_utc") or datetime.now(timezone.utc)
            if isinstance(ts, datetime):
                ts_naive = ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
                ts_naive = ts_naive.replace(microsecond=(ts_naive.microsecond // 1000) * 1000)
            else:
                ts_naive = ts
            payload = item.get("payload")
            body = (
                json.dumps(payload, ensure_ascii=False, default=str)
                if payload
                else None
            )
            rows.append(
                (
                    ts_naive,
                    str(item.get("action") or "update"),
                    (str(item.get("actor") or "").strip() or None),
                    (str(item.get("module") or "").strip() or None),
                    (str(item.get("personnel_id") or "").strip() or None),
                    (str(item.get("sme_mois_context") or "").strip() or None),
                    body,
                )
            )
        if not rows:
            return
        pending = []
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO audit_journal
                      (ts_utc, action, actor, module, personnel_id, sme_mois_context, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            if mirror is not None:
                from sme_gend.services.audit_mirror_recovery import enqueue_on_connection
                pending = enqueue_on_connection(conn, *mirror)
        if pending:
            from sme_gend.services.audit_mirror_recovery import schedule_delivery
            schedule_delivery(self._settings, pending)

    def export_all_config_rows(self) -> list[dict[str, Any]]:
        """Export complet de ``app_config`` pour sauvegarde / restauration."""
        if not self.table_exists("app_config"):
            return []
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT config_key, payload, updated_by, updated_at
                    FROM app_config ORDER BY config_key
                    """
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for key, payload, updated_by, updated_at in rows:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {"_raw": payload}
            if not isinstance(payload, dict):
                payload = {}
            ts = updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or "")
            out.append(
                {
                    "config_key": str(key),
                    "payload": payload,
                    "updated_by": updated_by,
                    "updated_at": ts,
                }
            )
        return out

    def import_config_rows(self, rows: list[dict[str, Any]], *, updated_by: str | None = None) -> int:
        """Réinjecte des lignes ``app_config`` (clé existante = écrasée)."""
        from sme_gend.repositories.mysql_connection import mysql_import_transaction

        rows = validate_config_rows(rows)
        if not rows:
            return 0
        if not self.table_exists("app_config"):
            raise RuntimeError("Restauration de configuration impossible : table app_config absente.")
        actor = (updated_by or "").strip() or None
        with mysql_import_transaction(self._settings):
            for raw in rows:
                self.set_json(raw["config_key"], raw["payload"], updated_by=actor)
        return len(rows)

    def list_personnel_delete_audit(self, *, limit: int = 400) -> list[dict[str, Any]]:
        """Suppressions personnel les plus récentes (``audit_journal``, module personnel)."""
        if not self.table_exists("audit_journal"):
            return []
        lim = max(1, min(int(limit), 5000))
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts_utc, action, actor, personnel_id, sme_mois_context, payload
                    FROM audit_journal
                    WHERE module = 'personnel' AND action = 'delete'
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (lim,),
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            pl = r[5]
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except json.JSONDecodeError:
                    pl = {}
            elif not isinstance(pl, dict):
                pl = {}
            ts = r[0]
            if isinstance(ts, datetime):
                ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
            ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            out.append(
                {
                    **pl,
                    "ts_utc": ts_s,
                    "action": r[1],
                    "edited_by": r[2] or "",
                    "personnel_id": (r[3] or "").strip(),
                    "sme_mois_context": r[4] or "",
                }
            )
        return out

    def read_personnel_audit_for(
        self,
        personnel_id: str,
        *,
        action: str = "update",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Événements personnel depuis ``audit_journal`` (plus récent en dernier)."""
        if not self.table_exists("audit_journal"):
            return []
        pid = (personnel_id or "").strip()
        if not pid:
            return []
        lim = max(1, min(int(limit), 5000))
        from sme_gend.services.agent_identity_alias import aliases_for
        identities = aliases_for(self._settings, pid)
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts_utc, action, actor, sme_mois_context, payload
                    FROM audit_journal
                    WHERE personnel_id IN ({}) AND action = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """.format(",".join(["%s"] * len(identities))),
                    tuple(identities) + (action, lim),
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            pl = r[4]
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except json.JSONDecodeError:
                    pl = {}
            elif not isinstance(pl, dict):
                pl = {}
            ts = r[0]
            if isinstance(ts, datetime):
                ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
            ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            entry: dict[str, Any] = {
                **pl,
                "ts_utc": ts_s,
                "action": r[1],
                "personnel_id": pid,
                "sme_mois_context": r[3] or "",
                "edited_by": r[2] or "",
            }
            out.append(entry)
        return out

    def list_doc_gen_months(self, *, doc_types=None) -> list[str]:
        if not self.table_exists("audit_journal"):
            raise RuntimeError("Le journal central est absent.")
        where, params = _doc_gen_where(doc_types=doc_types)
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT JSON_UNQUOTE(JSON_EXTRACT(payload, '$.mois')) "
                    "FROM audit_journal WHERE " + where + " ORDER BY 1 DESC", tuple(params))
                return [str(row[0]) for row in cur.fetchall() if row[0]]

    def read_doc_gen_candidates(self, timestamps) -> list[dict[str, Any]]:
        """Current central rows in the CSV's one-second fingerprint windows, no LIMIT."""
        values = sorted(set(timestamps))
        if not values:
            return []
        if not self.table_exists("audit_journal"):
            raise RuntimeError("Le journal central est absent.")
        where, _ = _doc_gen_where()
        rows = []
        with self._mc.connect() as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(values), 200):
                    ranges, params = [], []
                    for value in values[start:start + 200]:
                        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
                        ranges.append("(ts_utc >= %s AND ts_utc < %s)")
                        params.extend((dt, dt + timedelta(seconds=1)))
                    cursor.execute("SELECT ts_utc, action, actor, payload FROM audit_journal WHERE " + where +
                        " AND (" + " OR ".join(ranges) + ") FOR SHARE", tuple(params))
                    rows.extend(cursor.fetchall())
        return _doc_gen_entries(rows)

    def read_doc_gen_tail(self, limit: int = 500, *, doc_types=None,
                          mois_filter="", search_query="", type_labels=None,
                          require_table=False, offset=0) -> list[dict[str, Any]]:
        """Central document search, restricted and filtered before LIMIT."""
        if not self.table_exists("audit_journal"):
            if require_table:
                raise RuntimeError("Le journal central est absent.")
            return []
        lim = max(1, min(int(limit), 50000))
        offset = int(offset)
        if offset < 0:
            raise ValueError("La position dans l'historique est invalide.")
        where, params = _doc_gen_where(doc_types=doc_types, mois_filter=mois_filter,
            search_query=search_query, type_labels=type_labels)
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ts_utc, action, actor, payload
                    FROM audit_journal
                    WHERE {where}
                    ORDER BY ts_utc DESC, id DESC
                    LIMIT %s {"OFFSET %s" if offset else ""}
                    """,
                    tuple(params + [lim] + ([offset] if offset else [])),
                )
                rows = cur.fetchall()
        return _doc_gen_entries(rows)

    def read_audit_tail(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.table_exists("audit_journal"):
            return []
        lim = max(1, min(int(limit), 2000))
        with self._mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts_utc, action, actor, module, personnel_id,
                           sme_mois_context, payload
                    FROM audit_journal
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (lim,),
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            pl = r[6]
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except json.JSONDecodeError:
                    pl = {}
            elif not isinstance(pl, dict):
                pl = {}
            ts = r[0]
            ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            out.append(
                {
                    "ts_utc": ts_s,
                    "action": r[1],
                    "actor": r[2] or "?",
                    "module": r[3] or "",
                    "personnel_id": r[4] or "",
                    "sme_mois_context": r[5] or "",
                    **pl,
                }
            )
        return out

    def list_audit_operations(self):
        """Discover all recorded operation/module codes, independently of row limits."""
        with self._mc.connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DISTINCT module, action FROM audit_journal ORDER BY module, action")
                return [(str(row[0] or ''), str(row[1] or '')) for row in cursor.fetchall()]

    def audit_personnel_keys(self, career_id):
        """Resolve a displayed career ID to historical IDs and the legacy journal key."""
        keys = {str(career_id)}
        legacy = {str(career_id)}
        if not str(career_id).isascii() or not str(career_id).isdecimal():
            return {'career': sorted(keys), 'legacy': sorted(legacy)}
        if not self.table_exists('carriere_situation') or not self.table_exists('agent'):
            return {'career': sorted(keys), 'legacy': sorted(legacy)}
        has_aliases = self.table_exists('agent_identity_alias')
        with self._mc.connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT a.id, a.legacy_access_id FROM agent a "
                               "JOIN carriere_situation c ON c.agent_id=a.id WHERE c.id=%s", (int(career_id),))
                row = cursor.fetchone()
                if row:
                    legacy = {str(row[1])} if row[1] else set(keys)
                    cursor.execute("SELECT id FROM carriere_situation WHERE agent_id=%s", (row[0],))
                    keys.update(str(r[0]) for r in cursor.fetchall())
                    if has_aliases:
                        cursor.execute("SELECT alias_legacy_id FROM agent_identity_alias WHERE canonical_agent_id=%s", (row[0],))
                        legacy.update(str(r[0]) for r in cursor.fetchall())
        return {'career': sorted(keys), 'legacy': sorted(legacy)}

    def search_audit_journal(
        self,
        *,
        module: str | None = None,
        action: str | None = None,
        actor: str | None = None,
        personnel_id: str | None = None,
        sme_mois: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        text: str | None = None,
        personnel_nom: str | None = None,
        limit: int = 500,
        require_table: bool = False,
        entity_id: str | None = None,
        broad_agent_search: bool = False,
        personnel_keys: dict[str, list[str]] | None = None,
        cursor_before: tuple[datetime, int] | None = None,
        max_id: int | None = None,
        include_id: bool = False,
    ) -> list[dict[str, Any]]:
        """Search once in MySQL, applying every filter before the display limit."""
        start, end = audit_date_bounds(date_from, date_to)
        lim = max(1, min(int(limit), 2001 if include_id else 2000))
        if not self.table_exists("audit_journal"):
            if require_table:
                raise RuntimeError("Le journal central est absent de cette base.")
            return []
        clauses = ["1=1"]
        params: list[Any] = []
        if max_id is not None:
            clauses.append("id <= %s")
            params.append(int(max_id))
        if cursor_before is not None:
            stamp, row_id = cursor_before
            clauses.append("(ts_utc < %s OR (ts_utc = %s AND id < %s))")
            params.extend((stamp, stamp, int(row_id)))
        if entity_id is not None:
            clauses.append("JSON_UNQUOTE(JSON_EXTRACT(payload, '$.entity_id')) = %s")
            params.append(str(entity_id))
        for column, value in (("module", module), ("action", action)):
            value = (value or "").strip()
            if value:
                clauses.append(column + " = %s")
                params.append(value)
        actr = (actor or "").strip()
        if actr:
            clauses.append(_audit_contains("actor"))
            params.append(_audit_needle(actr))
        pid = (personnel_id or "").strip()
        if pid:
            if broad_agent_search:
                kinds = personnel_keys or {'career': [pid], 'legacy': [pid]}
                career_keys = sorted(set(kinds['career']))
                legacy_keys = sorted(set(kinds['legacy']))
                # The specialized sensitive journal stores career IDs; personnel
                # traces store the legacy identity key. Never mix their namespaces.
                matches = ["(module NOT IN ('personnel','sensitive_data') AND BINARY personnel_id=BINARY %s)"]
                params.append(pid)
                for module_name, keys in (('personnel', legacy_keys), ('sensitive_data', career_keys)):
                    for key in keys:
                        matches.append('(module=%s AND BINARY personnel_id=BINARY %s)')
                        params.extend((module_name, key))
                for key in legacy_keys:
                    matches.append("(action='personnel_dossier_opened' AND BINARY JSON_UNQUOTE(JSON_EXTRACT(payload,'$.legacy_agent_id'))=BINARY %s)")
                    params.append(key)
                for key in career_keys:
                    matches.append("(JSON_CONTAINS(JSON_EXTRACT(payload, '$.meta.personnel_ids'), %s) OR JSON_CONTAINS(JSON_EXTRACT(payload, '$.after.personnel_ids'), %s))")
                    params.extend((json.dumps(key), json.dumps(key)))
                clauses.append('(' + ' OR '.join(matches) + ')')
            else:
                clauses.append("BINARY personnel_id = BINARY %s")
                params.append(pid)
        legacy_month = "JSON_UNQUOTE(JSON_EXTRACT(payload, '$.sme_mois'))"
        month_column = (f"CASE WHEN module='system' AND action='mysql_bundle_import_access' "
                        f"AND {legacy_month} REGEXP '^[0-9]{{4}}-(0[1-9]|1[0-2])$' "
                        f"THEN {legacy_month} ELSE sme_mois_context END")
        mois = (sme_mois or "").strip()
        if mois:
            clauses.append(f"LEFT({month_column}, %s) = %s")
            params.extend((len(mois), mois))
        if start is not None:
            clauses.append("ts_utc >= %s")
            params.append(start)
        if end is not None:
            clauses.append("ts_utc < %s")
            params.append(end)
        nom = (personnel_nom or "").strip()
        if nom:
            paths = ('$.personnel_nom', '$.before.noms', '$.after.noms', '$.persons', '$.after.persons') if broad_agent_search else ('$.personnel_nom',)
            clauses.append('(' + ' OR '.join(_audit_contains(f"JSON_UNQUOTE(JSON_EXTRACT(payload, '{path}'))") for path in paths) + ')')
            params.extend([_audit_needle(nom)] * len(paths))
        txt = (text or "").strip()
        if txt:
            fields = ("action", "actor", "personnel_id", "sme_mois_context", "CAST(payload AS CHAR CHARACTER SET utf8mb4)")
            clauses.append("(" + " OR ".join(_audit_contains(field) for field in fields) + ")")
            params.extend([_audit_needle(txt)] * len(fields))
        where = " AND ".join(clauses)
        sql = f"""
            SELECT ts_utc, action, actor, module, personnel_id,
                   {month_column}, payload{', id' if include_id else ''}
            FROM audit_journal
            WHERE {where}
            ORDER BY ts_utc DESC, id DESC
            LIMIT %s
        """
        params.append(lim)
        with self._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            pl = r[6]
            if isinstance(pl, (bytes, bytearray)):
                pl = pl.decode("utf-8")
            if isinstance(pl, str):
                pl = json.loads(pl)
            if not isinstance(pl, dict):
                pl = {}
            ts = r[0]
            if isinstance(ts, datetime):
                ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                ts_s = ts.isoformat()
            else:
                ts_s = str(ts or "")
            # Payload keys must never impersonate the journal's authoritative columns.
            out.append({**pl,
                "ts_utc": ts_s, "action": r[1], "actor": r[2] or "",
                "module": r[3] or "", "personnel_id": r[4] or "",
                "sme_mois_context": r[5] or "", "payload": pl,
                **({"journal_id": int(r[7])} if include_id else {}),
            })
        return out
