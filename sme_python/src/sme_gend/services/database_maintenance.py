"""Concurrent writers with exclusive admission only during actual maintenance.

Each write owns a separate session lock. Maintenance closes admission and waits
for these locks to drain; ordinary clients never hold one global lock to work.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

TABLE = "_sme_maintenance_state"
WRITERS_TABLE = "_sme_active_writer"
INTERNAL_TABLES = frozenset((TABLE, WRITERS_TABLE))
_writer = ContextVar("sme_database_writer", default=None)
_owner = ContextVar("sme_maintenance_owner", default=None)


class MaintenanceActiveError(RuntimeError):
    pass


class WriteCoordinationBusyError(TimeoutError):
    """Brief admission contention, distinct from a real maintenance state."""


def lock_name(settings):
    return "sme_restore_" + sha256(settings.mysql_database.strip().casefold().encode()).hexdigest()[:48]


def _request_lock(settings):
    return "sme_maint_" + sha256(lock_name(settings).encode()).hexdigest()[:48]


def worker_lock_name(settings):
    return "sme_worker_" + sha256(lock_name(settings).encode()).hexdigest()[:48]


def check_owned_barrier(settings, connection):
    owner = _owner.get()
    if owner is None or owner.database != _key(settings) or _scalar(connection, 'SELECT IS_USED_LOCK(%s)', (lock_name(settings),)) != owner.connection_id:
        raise MaintenanceActiveError('Connexion de maintenance perdue ; bascule refusée, vérifiez la récupération avant toute reprise.')


def _key(settings):
    from sme_gend.repositories.mysql_connection import mysql_database_key
    return mysql_database_key(settings)


def _scalar(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return row[0] if row else None


def _ensure(connection):
    if getattr(connection, "_sme_coordination_version", None) == 2:
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{TABLE}` (
                id TINYINT PRIMARY KEY, active TINYINT NOT NULL DEFAULT 0,
                token VARCHAR(32) NOT NULL DEFAULT '', reason VARCHAR(255) NOT NULL DEFAULT '',
                started_at DATETIME(3) NULL, owner_connection BIGINT NULL
            ) ENGINE=InnoDB""")
        cursor.execute(f"SELECT id FROM `{TABLE}` WHERE id=1")
        if cursor.fetchone() is None:
            cursor.execute(f"INSERT IGNORE INTO `{TABLE}` (id) VALUES (1)")
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (WRITERS_TABLE,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{WRITERS_TABLE}` (
                token CHAR(32) NOT NULL PRIMARY KEY,
                lock_name VARCHAR(64) NOT NULL,
                connection_id BIGINT NOT NULL,
                created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ) ENGINE=InnoDB""")
    connection.commit()
    connection._sme_maintenance_initialized = True
    connection._sme_coordination_version = 2


def _state(connection):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT active,token,reason,started_at,owner_connection FROM `{TABLE}` WHERE id=1")
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("État de maintenance indisponible ; écriture refusée.")
    return dict(active=bool(row[0]), token=row[1], reason=row[2], started_at=str(row[3] or ""), owner_connection=row[4])


def _release(connection, name):
    if not connection.open:
        return  # Closing a session releases its named locks on the server.
    if _scalar(connection, "SELECT RELEASE_LOCK(%s)", (name,)) != 1:
        connection.close()
        raise RuntimeError("Verrou d'écriture perdu ; connexion fermée, vérifiez le résultat avant de réessayer.")


@contextmanager
def coordinated_write(settings, connection):
    """Register one writer, without serializing its business transaction."""
    name = lock_name(settings)
    owner = _owner.get()
    writer = _writer.get()
    if owner is not None and owner.database == _key(settings):
        check_owned_barrier(settings, connection)
        yield
        return
    connection_id = connection.thread_id()
    if writer is not None and writer.database == _key(settings) and writer.connection_id == connection_id:
        if _scalar(connection, "SELECT IS_USED_LOCK(%s)", (writer.writer_lock,)) != connection_id:
            raise MaintenanceActiveError("Le verrou partagé a été perdu ; opération interrompue.")
        yield
        return
    from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
    if connection.server_status & SERVER_STATUS_IN_TRANS:
        raise RuntimeError("Transaction MySQL préexistante non partagée : écriture refusée avant initialisation du contrôle de maintenance.")
    state_ready = getattr(connection, '_sme_maintenance_initialized', False) is True
    if not state_ready:
        state_ready = _scalar(connection, 'SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', (TABLE,)) == 1
        connection.rollback()
        if state_ready:
            connection._sme_maintenance_initialized = True
    if state_ready:
        state = _state(connection)
        connection.rollback()
        if state['active']:
            raise MaintenanceActiveError("Maintenance centralisée en cours : les écritures sont suspendues. " + state['reason'])
    if _scalar(connection, "SELECT GET_LOCK(%s,5)", (name,)) != 1:
        if getattr(connection, '_sme_maintenance_initialized', False) is True:
            state = _state(connection)
            connection.rollback()
            if state['active']:
                raise MaintenanceActiveError("Maintenance centralisée en cours : les écritures sont suspendues. " + state['reason'])
        raise WriteCoordinationBusyError("Coordination d'une écriture momentanément occupée. Réessayez ; aucune maintenance n'a été confirmée.")
    scope = None
    lease = None
    registered = False
    admitted = False
    try:
        _ensure(connection)
        # Finish the control read before a caller's SET TRANSACTION command.
        state = _state(connection)
        connection.rollback()
        if state["active"]:
            raise MaintenanceActiveError("Maintenance centralisée en cours : aucune modification n'est autorisée. " + state["reason"])
        token = uuid4().hex
        individual_lock = 'sme_w_' + sha256(name.encode()).hexdigest()[:20] + '_' + token
        if _scalar(connection, 'SELECT GET_LOCK(%s,0)', (individual_lock,)) != 1:
            raise RuntimeError("Enregistrement de l'écriture impossible ; aucune modification appliquée.")
        lease = Lease(_key(settings), connection_id, token, writer_lock=individual_lock)
        with connection.cursor() as cursor:
            cursor.execute(f'INSERT INTO `{WRITERS_TABLE}` (token,lock_name,connection_id) VALUES (%s,%s,%s)',
                           (token, individual_lock, connection_id))
        connection.commit()  # Registration only: business work has not begun.
        registered = True
        _release(connection, name)
        admitted = True
        scope = _writer.set(lease)
        yield
    finally:
        if scope is not None:
            _writer.reset(scope)
        import sys
        import logging
        failed = sys.exc_info()[0] is not None
        unfinished = False
        try:
            if lease is not None and connection.open:
                if connection.server_status & SERVER_STATUS_IN_TRANS:
                    connection.rollback()
                    unfinished = True
                # The pool commits/rolls back business data before reaching here.
                # Release first: a stale registry row never blocks maintenance.
                _release(connection, lease.writer_lock)
                if registered:
                    with connection.cursor() as cursor:
                        cursor.execute(f'DELETE FROM `{WRITERS_TABLE}` WHERE token=%s', (lease.token,))
                    connection.commit()
        except Exception:
            # Business outcome is already known; do not invite a duplicate retry
            # because registry cleanup failed. Closing retires the session lock.
            connection._sme_retire_after_write = True
            logging.getLogger(__name__).warning('Nettoyage de coordination différé ; connexion à retirer du pool.')
        finally:
            if not admitted:
                try:
                    _release(connection, name)
                except Exception:
                    connection.close()
                    if not failed:
                        raise
        if unfinished and not failed:
            raise RuntimeError("Transaction non validée avant la fin de l'écriture ; modifications annulées.")


@dataclass
class Lease:
    database: tuple
    connection_id: int
    token: str
    retained: bool = False
    writer_lock: str = ""

    def keep_blocked(self):
        self.retained = True


def _wait_for_writers(connection, *, deadline):
    import time
    while True:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT token,lock_name,connection_id FROM `{WRITERS_TABLE}`')
            rows = cursor.fetchall()
        live = False
        for token, name, connection_id in rows:
            if _scalar(connection, 'SELECT IS_USED_LOCK(%s)', (name,)) == connection_id:
                live = True
            else:
                with connection.cursor() as cursor:
                    cursor.execute(f'DELETE FROM `{WRITERS_TABLE}` WHERE token=%s', (token,))
        if not live:
            return
        if time.monotonic() >= deadline:
            raise WriteCoordinationBusyError('Des écritures sont encore en cours ; restauration annulée avant toute bascule.')
        time.sleep(0.05)


@contextmanager
def centralized_maintenance(settings, *, reason="Restauration de la base", recovery_token=None):
    existing = _owner.get()
    if existing is not None and existing.database == _key(settings):
        yield existing
        return
    import pymysql
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    if has_import_transaction(settings) or _writer.get() is not None:
        raise RuntimeError("La maintenance doit commencer hors transaction métier.")
    params = MySQLConnection(settings).connect_params()
    params.update(autocommit=True, read_timeout=30)
    connection = pymysql.connect(**params)
    request = _request_lock(settings)
    requested = locked = marked = False
    lease = None
    token = None
    recovering = False
    try:
        if _scalar(connection, "SELECT GET_LOCK(%s,0)", (request,)) != 1:
            raise MaintenanceActiveError("Une maintenance est déjà en cours sur cette base.")
        requested = True
        _ensure(connection)
        current = _state(connection)
        recovering = bool(recovery_token) and current['active'] and current['token'] == recovery_token
        if current['active'] and not recovering:
            raise MaintenanceActiveError("Maintenance interrompue à vérifier avant de reprendre les écritures.")
        if recovery_token and not recovering:
            raise MaintenanceActiveError('La maintenance de récupération a changé ; actualisez son état.')
        # Refuse a legacy/manual maintenance owner immediately. It cannot
        # release its barrier while synchronously waiting for this operation.
        if _scalar(connection, 'SELECT IS_USED_LOCK(%s)', (lock_name(settings),)) is not None:
            raise MaintenanceActiveError('Une restauration est déjà en cours sur cette base.')
        lease = Lease(_key(settings), _scalar(connection, "SELECT CONNECTION_ID()"),
                      recovery_token if recovering else uuid4().hex)
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE `{TABLE}` SET active=1,token=%s,reason=%s,started_at=UTC_TIMESTAMP(3),owner_connection=%s WHERE id=1",
                           (lease.token, reason, lease.connection_id))
        marked = True
        import time
        deadline = time.monotonic() + 15
        if _scalar(connection, "SELECT GET_LOCK(%s,15)", (lock_name(settings),)) != 1:
            raise WriteCoordinationBusyError("Des écritures sont encore en cours ; restauration annulée avant toute bascule.")
        locked = True
        _wait_for_writers(connection, deadline=deadline)
        token = _owner.set(lease)
        yield lease
    finally:
        if recovering and lease is not None and __import__('sys').exc_info()[0] is not None:
            lease.keep_blocked()
        if token is not None:
            _owner.reset(token)
        try:
            if marked and not lease.retained:
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE `{TABLE}` SET active=0,reason='',owner_connection=NULL WHERE id=1 AND token=%s", (lease.token,))
            if locked:
                _release(connection, lock_name(settings))
            if requested:
                _release(connection, request)
        finally:
            connection.close()


def maintenance_status(settings):
    from sme_gend.repositories.mysql_connection import MySQLConnection
    with MySQLConnection(settings).connect(read_only=True) as connection:
        if not _scalar(connection, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,)):
            return dict(active=False, token="", reason="", started_at="", owner_connection=None)
        return _state(connection)


def end_interrupted_maintenance(settings, *, expected_token, recovery_verified=False):
    """Explicit operator recovery, never clear a live restore or a newer session."""
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("system.restore"):
        raise PermissionError("Permission de restauration refusée.")
    if not recovery_verified or not expected_token:
        raise ValueError("Vérifiez la récupération avant de lever la maintenance.")
    import pymysql
    from sme_gend.repositories.mysql_connection import MySQLConnection
    connection = pymysql.connect(**(MySQLConnection(settings).connect_params() | {"autocommit": True}))
    request = _request_lock(settings)
    requested = locked = worker_locked = False
    scope = None
    try:
        if _scalar(connection, "SELECT GET_LOCK(%s,0)", (request,)) != 1:
            raise MaintenanceActiveError("La restauration est encore active ; reprise refusée.")
        requested = True
        if _scalar(connection, "SELECT GET_LOCK(%s,0)", (lock_name(settings),)) != 1:
            raise MaintenanceActiveError("Une opération est encore active ; reprise refusée.")
        locked = True
        _wait_for_writers(connection, deadline=__import__('time').monotonic())
        if _scalar(connection, "SELECT GET_LOCK(%s,0)", (worker_lock_name(settings),)) != 1:
            raise MaintenanceActiveError("Un traitement de restauration est encore actif ; reprise refusée.")
        worker_locked = True
        state = _state(connection)
        if not state["active"] or state["token"] != expected_token:
            raise RuntimeError("La maintenance a changé ; actualisez son état.")
        scope = _owner.set(Lease(_key(settings), _scalar(connection, "SELECT CONNECTION_ID()"), expected_token))
        from sme_gend.repositories.app_config_repository import AppConfigRepository
        from sme_gend.services.auth_context import resolve_audit_actor
        AppConfigRepository(settings).append_audit(action="maintenance_recovered", module="system",
            actor=resolve_audit_actor(), payload={"detail": "Reprise des écritures après vérification de la récupération", "maintenance_token": expected_token})
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE `{TABLE}` SET active=0,reason='',owner_connection=NULL WHERE id=1 AND token=%s", (expected_token,))
    finally:
        if scope is not None:
            _owner.reset(scope)
        try:
            if worker_locked:
                _release(connection, worker_lock_name(settings))
            if locked:
                _release(connection, lock_name(settings))
            if requested:
                _release(connection, request)
        finally:
            connection.close()
