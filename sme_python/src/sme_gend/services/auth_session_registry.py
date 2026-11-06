"""Registre MySQL des sessions actives (limite de connexions simultanées par compte)."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sme_gend.config.auth_config import AuthConfig, clamp_concurrent_sessions_limit
from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection

_log = logging.getLogger(__name__)

_GRACE_MINUTES = 5


@dataclass(frozen=True, slots=True)
class ActiveSessionRow:
    session_token: str
    username: str
    client_instance_id: str
    machine_label: str
    session_rev: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


def ensure_auth_sessions_table(settings: Settings) -> None:
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    path = project_root() / "data" / "schema" / "mysql_auth_sessions.sql"
    if not path.is_file():
        return
    mc = MySQLConnection(settings)
    sql = path.read_text(encoding="utf-8")
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "duplicate" in err or "already exists" in err or "1061" in err:
                        continue
                    raise


def _table_ready(settings: Settings) -> bool:
    try:
        from sme_gend.repositories.app_config_repository import AppConfigRepository

        ok = AppConfigRepository(settings).table_exists("auth_active_session")
        if not ok:
            from sme_gend.repositories.mysql_connection import has_import_transaction
            # MySQL DDL implicitly commits: never create this table inside an
            # account change that must roll back together with its audit.
            if has_import_transaction(settings):
                return False
            ensure_auth_sessions_table(settings)
            ok = AppConfigRepository(settings).table_exists("auth_active_session")
        return ok
    except Exception as exc:
        _log.debug("auth_active_session indisponible : %s", exc)
        return False


def concurrent_policy(cfg: AuthConfig) -> tuple[str, int]:
    """Retourne (mode, max_postes) avec mode in {off, single, limited}."""
    mode = (getattr(cfg, "concurrent_sessions_mode", None) or "off").strip().lower()
    if mode not in ("off", "single", "limited"):
        mode = "off"
    limit = clamp_concurrent_sessions_limit(getattr(cfg, "max_concurrent_sessions", 2))
    if mode == "single":
        limit = 1
    return mode, limit


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _server_utc_now(cur) -> datetime:
    cur.execute('SELECT UTC_TIMESTAMP(6)')
    return _as_utc(cur.fetchone()[0])


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return _as_utc(val)
    s = str(val).strip()
    if not s:
        return None
    try:
        return _as_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def purge_stale_sessions(settings: Settings, *, idle_minutes: int) -> None:
    if not _table_ready(settings):
        return
    idle = max(30, int(idle_minutes) + _GRACE_MINUTES)
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            now = _server_utc_now(cur)
            cutoff = now - timedelta(minutes=idle)
            cur.execute(
                """
                DELETE FROM auth_active_session
                WHERE expires_at < %s OR last_seen_at < %s
                """,
                (now, cutoff),
            )


def _count_other_active(
    cur: Any,
    *,
    username: str,
    client_instance_id: str,
    idle_cutoff: datetime,
    now: datetime,
) -> int:
    cur.execute(
        """
        SELECT COUNT(DISTINCT client_instance_id) FROM auth_active_session
        WHERE username = %s AND client_instance_id <> %s
          AND last_seen_at >= %s AND expires_at > %s
        """,
        (username, client_instance_id, idle_cutoff, now),
    )
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def try_register_session(
    settings: Settings,
    cfg: AuthConfig,
    *,
    username: str,
    session_rev: int,
    client_instance_id: str,
    machine_label: str,
    expires_at: datetime,
) -> tuple[str | None, str | None]:
    """
    Enregistre une session. Retourne (token, None) ou (None, message_erreur).
    """
    mode, limit = concurrent_policy(cfg)
    token = secrets.token_urlsafe(32)
    if not _table_ready(settings):
        return None, "Registre des sessions indisponible. Vérifiez la connexion à la base."

    idle_min = int(cfg.session_timeout_minutes or 480) + _GRACE_MINUTES
    purge_stale_sessions(settings, idle_minutes=cfg.session_timeout_minutes)
    remaining = _as_utc(expires_at) - _utc_now()
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            now = _server_utc_now(cur)
            exp = now + remaining
            idle_cutoff = now - timedelta(minutes=idle_min)
            # A login must never revoke another running application's token.
            # Several processes may share one local profile or a copied client id.
            if mode != "off" and limit > 0:
                others = _count_other_active(
                    cur,
                    username=username,
                    client_instance_id=client_instance_id,
                    idle_cutoff=idle_cutoff,
                    now=now,
                )
            else:
                others = 0
            if mode != "off" and others >= limit:
                cur.execute(
                    """
                    SELECT machine_label, last_seen_at FROM auth_active_session
                    WHERE username = %s AND client_instance_id <> %s
                      AND last_seen_at >= %s AND expires_at > %s
                    ORDER BY last_seen_at DESC LIMIT 3
                    """,
                    (username, client_instance_id, idle_cutoff, now),
                )
                hints = []
                for lbl, seen in cur.fetchall() or []:
                    label = (lbl or "Autre poste").strip()
                    ts = _parse_dt(seen)
                    if ts:
                        label += f" ({ts.strftime('%d/%m/%Y %H:%M')})"
                    hints.append(label)
                extra = ""
                if hints:
                    extra = " Connexion(s) active(s) : " + "; ".join(hints) + "."
                if mode == "single":
                    msg = (
                        "Ce compte est déjà connecté sur un autre poste."
                        + extra
                        + " Déconnectez l'autre session ou demandez à un administrateur "
                        "de la clôturer."
                    )
                else:
                    msg = (
                        f"Nombre maximal de postes simultanés atteint ({limit})."
                        + extra
                    )
                return None, msg
            cur.execute(
                """
                INSERT INTO auth_active_session (
                  session_token, username, client_instance_id, machine_label,
                  session_rev, last_seen_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    token,
                    username,
                    client_instance_id,
                    (machine_label or "")[:256] or None,
                    int(session_rev or 0),
                    now.replace(tzinfo=None),
                    exp.replace(tzinfo=None),
                ),
            )
    return token, None


def _heartbeat_session_write(
    settings: Settings,
    *,
    session_token: str,
    username: str,
    session_rev: int,
    expires_at: datetime | None = None,
) -> bool:
    """Prolonge last_seen. Retourne False si la session n'est plus valide."""
    if not (session_token or "").strip():
        return False
    if not _table_ready(settings):
        raise RuntimeError("Registre des sessions indisponible.")
    remaining = _as_utc(expires_at) - _utc_now() if expires_at is not None else None
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            now = _server_utc_now(cur)
            cur.execute(
                """
                SELECT session_rev, expires_at FROM auth_active_session
                WHERE session_token = %s AND username = %s
                FOR UPDATE
                """,
                (session_token, username),
            )
            row = cur.fetchone()
            if not row:
                return False
            db_rev = int(row[0] or 0)
            exp = _parse_dt(row[1])
            if db_rev != int(session_rev or 0):
                return False
            if exp is not None and exp < now:
                cur.execute(
                    "DELETE FROM auth_active_session WHERE session_token = %s",
                    (session_token,),
                )
                return False
            exp_sql = None
            if expires_at is not None:
                exp_sql = (now + remaining).replace(tzinfo=None)
            if exp_sql is not None:
                cur.execute(
                    """
                    UPDATE auth_active_session
                    SET last_seen_at = GREATEST(last_seen_at, %s),
                        expires_at = GREATEST(expires_at, %s), session_rev = %s
                    WHERE session_token = %s
                    """,
                    (now.replace(tzinfo=None), exp_sql, int(session_rev or 0), session_token),
                )
            else:
                cur.execute(
                    """
                    UPDATE auth_active_session
                    SET last_seen_at = GREATEST(last_seen_at, %s), session_rev = %s
                    WHERE session_token = %s
                    """,
                    (now.replace(tzinfo=None), int(session_rev or 0), session_token),
                )
    return True


def heartbeat_session(settings, *, session_token, username, session_rev, expires_at=None):
    """Maintenance suspends heartbeat writes, never validity/revocation checks."""
    from sme_gend.services.database_maintenance import MaintenanceActiveError
    try:
        return _heartbeat_session_write(settings, session_token=session_token, username=username,
                                       session_rev=session_rev, expires_at=expires_at)
    except MaintenanceActiveError:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                now = _server_utc_now(cursor)
                cursor.execute('SELECT session_rev,expires_at FROM auth_active_session WHERE session_token=%s AND username=%s',
                               (session_token, username))
                row = cursor.fetchone()
        return bool(row and int(row[0] or 0) == int(session_rev or 0)
                    and _parse_dt(row[1]) is not None and _parse_dt(row[1]) >= now)


def unregister_session(settings: Settings, session_token: str | None) -> None:
    tok = (session_token or "").strip()
    if not tok:
        return
    if not _table_ready(settings):
        raise RuntimeError("Révocation impossible : registre des sessions indisponible.")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM auth_active_session WHERE session_token = %s", (tok,))


def advance_session_revision(settings: Settings, *, session_token: str, username: str,
                             previous_revision: int, revision: int) -> None:
    """Keep the current verified session after an authorized account change."""
    if not _table_ready(settings):
        raise RuntimeError("Registre des sessions indisponible.")
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE auth_active_session SET session_rev=%s "
                        "WHERE session_token=%s AND username=%s AND session_rev=%s AND expires_at>UTC_TIMESTAMP(6)",
                        (revision, session_token, username, previous_revision))
            if cur.rowcount != 1:
                raise PermissionError("Session révoquée ou expirée : reconnectez-vous.")


def revoke_user_sessions(
    settings: Settings,
    username: str,
    *,
    except_token: str | None = None,
) -> int:
    uname = (username or "").strip()
    if not uname:
        return 0
    if not _table_ready(settings):
        raise RuntimeError("Révocation impossible : registre des sessions indisponible.")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            if (except_token or "").strip():
                cur.execute(
                    """
                    DELETE FROM auth_active_session
                    WHERE username = %s AND session_token <> %s
                    """,
                    (uname, except_token.strip()),
                )
            else:
                cur.execute(
                    "DELETE FROM auth_active_session WHERE username = %s",
                    (uname,),
                )
            return int(cur.rowcount or 0)


def list_active_sessions(
    settings: Settings,
    *,
    username: str | None = None,
) -> list[ActiveSessionRow]:
    if not _table_ready(settings):
        raise RuntimeError("Registre des sessions indisponible.")
    purge_stale_sessions(settings, idle_minutes=480 + _GRACE_MINUTES)
    mc = MySQLConnection(settings)
    rows: list[ActiveSessionRow] = []
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            if (username or "").strip():
                cur.execute(
                    """
                    SELECT session_token, username, client_instance_id, machine_label,
                           session_rev, created_at, last_seen_at, expires_at
                    FROM auth_active_session
                    WHERE username = %s AND expires_at > UTC_TIMESTAMP(6)
                    ORDER BY last_seen_at DESC
                    """,
                    (username.strip(),),
                )
            else:
                cur.execute(
                    """
                    SELECT session_token, username, client_instance_id, machine_label,
                           session_rev, created_at, last_seen_at, expires_at
                    FROM auth_active_session
                    WHERE expires_at > UTC_TIMESTAMP(6)
                    ORDER BY username, last_seen_at DESC
                    """,
                )
            for r in cur.fetchall() or []:
                created = _parse_dt(r[5])
                seen = _parse_dt(r[6])
                exp = _parse_dt(r[7])
                if created is None or seen is None or exp is None:
                    continue
                rows.append(
                    ActiveSessionRow(
                        session_token=str(r[0]),
                        username=str(r[1]),
                        client_instance_id=str(r[2]),
                        machine_label=str(r[3] or ""),
                        session_rev=int(r[4] or 0),
                        created_at=created,
                        last_seen_at=seen,
                        expires_at=exp,
                    )
                )
    return rows
