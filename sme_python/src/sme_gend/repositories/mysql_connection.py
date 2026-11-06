"""Connexion MySQL (PyMySQL)."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

import pymysql
from pymysql.connections import Connection

from sme_gend.config.settings import Settings


_initialization_scope = threading.local()

_import_transaction: ContextVar[tuple[tuple[str, int, str], Any] | None] = ContextVar(
    "mysql_import_transaction", default=None,
)
_transaction_settings: ContextVar[Settings | None] = ContextVar("mysql_transaction_settings", default=None)
_commit_hooks: ContextVar[list | None] = ContextVar("mysql_commit_hooks", default=None)
_read_snapshot: ContextVar[bool] = ContextVar("mysql_read_snapshot", default=False)


class CommittedMirrorError(RuntimeError):
    """The database commit succeeded; retrying the business write is unsafe."""
    committed = True


def after_mysql_commit(callback):
    callbacks = _commit_hooks.get()
    if callbacks is None:
        callback()
    else:
        callbacks.append(callback)



def current_transaction_settings() -> Settings | None:
    return _transaction_settings.get()


def has_import_transaction(settings: Settings) -> bool:
    active = _import_transaction.get()
    return active is not None and active[0] == mysql_database_key(settings)


class _DeferredCommitConnection:
    """Les commits des dépôts sont différés jusqu'à la fin de l'import complet."""
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self):
        pass


@contextmanager
def mysql_read_snapshot(settings: Settings):
    """Read repositories at one committed instant without locking business writes.

    Explicitly select REPEATABLE READ: a server configured READ COMMITTED would
    otherwise silently give each SELECT a different view. The read-only server
    transaction also rejects an accidental write/DDL from a repository loader.
    """
    active = _import_transaction.get()
    if active is not None:
        if _read_snapshot.get() and active[0] == mysql_database_key(settings):
            yield active[1]
            return
        raise RuntimeError("Une lecture figée ne peut pas publier une transaction en cours.")
    with MySQLConnection(settings).connect(read_only=True) as connection:
        from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
        if connection.server_status & SERVER_STATUS_IN_TRANS:
            raise RuntimeError("Une transaction MySQL précédente est encore ouverte.")
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        transaction_token = _import_transaction.set((
            mysql_database_key(settings), _DeferredCommitConnection(connection),
        ))
        settings_token = _transaction_settings.set(settings)
        snapshot_token = _read_snapshot.set(True)
        try:
            yield _import_transaction.get()[1]
        finally:
            _read_snapshot.reset(snapshot_token)
            _transaction_settings.reset(settings_token)
            _import_transaction.reset(transaction_token)
            # End even a successful read before returning a pooled connection.
            connection.rollback()


@contextmanager
def mysql_import_transaction(settings: Settings, *, read_committed: bool = False):
    """Partage une transaction entre les dépôts synchrones du remplacement."""
    if has_import_transaction(settings):
        yield
        return
    callbacks = []
    with MySQLConnection(settings).connect() as connection:
        if read_committed:
            # Consume this one-transaction setting immediately. It must never
            # leak into the next operation on a pooled connection.
            from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
            if connection.server_status & SERVER_STATUS_IN_TRANS:
                raise RuntimeError("Transaction MySQL préexistante non partagée : écriture annulée.")
            with connection.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("START TRANSACTION")
        token = _import_transaction.set((
            mysql_database_key(settings), _DeferredCommitConnection(connection),
        ))
        settings_token = _transaction_settings.set(settings)
        hooks_token = _commit_hooks.set(callbacks)
        try:
            yield
        finally:
            _import_transaction.reset(token)
            _transaction_settings.reset(settings_token)
            _commit_hooks.reset(hooks_token)
    # Reached only after the owning connection has really committed. A nested
    # commit is deferred; an exception/rollback discards the entire callback list.
    failures = []
    for callback in callbacks:
        try:
            callback()
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise CommittedMirrorError(
            "Données enregistrées dans MySQL, mais copie locale du journal impossible. "
            "Ne recommencez pas la modification ; vérifiez l'accès au dossier des journaux."
        ) from failures[0]


def mysql_database_key(settings: Settings) -> tuple[str, int, str]:
    """Identité de la base, indépendante des options de vue comme ``mysql_view_mois``."""
    return (
        settings.mysql_host.strip().casefold(),
        settings.mysql_port,
        settings.mysql_database.strip(),
    )


@contextmanager
def mysql_initialization_lock(settings: Settings) -> Generator[None, None, None]:
    """Sérialise les initialisations de schéma/config entre processus clients."""
    key = mysql_database_key(settings)
    held = getattr(_initialization_scope, "held", None)
    if held is None:
        held = set()
        _initialization_scope.held = held
    if key in held:
        yield
        return
    identity = ":".join(str(value) for value in key)
    lock_name = f"sme_gend_init_{hashlib.sha256(identity.encode()).hexdigest()[:40]}"
    mc = MySQLConnection(settings)
    params = mc.connect_params()
    params["autocommit"] = True
    params["read_timeout"] = 65  # GET_LOCK may legitimately wait up to 60 s.
    conn = pymysql.connect(**params)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 60))
            acquired = cur.fetchone()
        if not acquired or acquired[0] != 1:
            raise TimeoutError(
                f"Impossible d'obtenir le verrou d'initialisation MySQL "
                f"pour {settings.mysql_database!r} en 60 secondes."
            )
        held.add(key)
        try:
            from sme_gend.services.database_maintenance import coordinated_write
            with coordinated_write(settings, conn):
                yield
        finally:
            held.discard(key)
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                released = cur.fetchone()
            if not released or released[0] != 1:
                raise RuntimeError(
                    f"Le verrou d'initialisation MySQL de "
                    f"{settings.mysql_database!r} n'a pas été libéré."
                )
    finally:
        conn.close()


class MySQLConnection:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def connect_params(self) -> dict[str, Any]:
        s = self._settings
        return {
            "host": s.mysql_host,
            "port": s.mysql_port,
            "user": s.mysql_user,
            "password": s.mysql_password,
            "database": s.mysql_database,
            "charset": s.mysql_charset,
            "autocommit": False,
            "cursorclass": pymysql.cursors.Cursor,
            "connect_timeout": 5,
            "read_timeout": 30,
            "write_timeout": 30,
        }

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Generator[Connection, None, None]:
        active = _import_transaction.get()
        if active is not None and active[0] == mysql_database_key(self._settings):
            # Les lectures doivent voir les lignes non encore validées de l'import.
            yield active[1]
            return
        from sme_gend.repositories.mysql_pool import pooled_connection

        with pooled_connection(self._settings, read_only=read_only) as conn:
            yield conn

    def test_connection(self) -> tuple[bool, str]:
        try:
            with self.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True, "Connexion MySQL réussie."
        except Exception as e:
            return False, str(e)
