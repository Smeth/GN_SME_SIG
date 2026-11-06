"""Connexions MySQL.

Thread principal (UI) : une connexion persistante réutilisée (comme avant).
Tout autre thread (tâches ``run_in_background``) : pool borné emprunt/restitution
— jamais de connexion qui reste ouverte indéfiniment après une tâche, jamais
non plus de reconnexion complète (TCP + authentification) à chaque tâche.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import hashlib
import secrets
from contextlib import contextmanager
from typing import Any, Generator

import pymysql
from pymysql.connections import Connection

from sme_gend.config.settings import Settings

_log = logging.getLogger(__name__)

_MAIN_THREAD = threading.main_thread()
_POOL_MAX_SIZE = 5
_PING_INTERVAL_SECONDS = 30
_POOL_ACQUIRE_TIMEOUT = 30
_CREDENTIAL_KEY = secrets.token_bytes(32)

_registry_lock = threading.Lock()
_thread_local = threading.local()  # connexions persistantes du thread principal
_bg_pools: dict[str, "_BoundedPool"] = {}


def _settings_key(settings: Settings) -> str:
    s = settings
    return (
        f"{s.mysql_host}|{s.mysql_port}|{s.mysql_database}|"
        f"{s.mysql_user}|{s.mysql_charset}"
    )


def _pool_key(settings: Settings, *, read_only: bool = False) -> str:
    credential = hashlib.blake2b(settings.mysql_password.encode(), key=_CREDENTIAL_KEY, digest_size=16).hexdigest()
    return f"{_settings_key(settings)}|credential={credential}|read_only={int(read_only)}"


def _connect_params(settings: Settings, *, read_only: bool = False) -> dict[str, Any]:
    s = settings
    return {
        "host": s.mysql_host,
        "port": s.mysql_port,
        "user": s.mysql_user,
        "password": s.mysql_password,
        "database": s.mysql_database,
        "charset": s.mysql_charset,
        "autocommit": read_only,
        "cursorclass": pymysql.cursors.Cursor,
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def _is_alive(conn: Connection) -> bool:
    try:
        conn.ping(False)
        return True
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return False


def _new_connection(settings: Settings, *, read_only: bool) -> Connection:
    conn = pymysql.connect(**_connect_params(settings, read_only=read_only))
    if not read_only:
        return conn
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
    except BaseException:
        _close_discarded_connection(conn, preserve_error=True)
        raise
    return conn


def _close_discarded_connection(conn: Connection, *, preserve_error: bool = False) -> None:
    """Preserve an active failure; normal cleanup must still allow interruption."""
    try:
        conn.close()
    except BaseException as error:
        _log.warning("Fermeture MySQL impossible ; connexion exclue du pool.")
        if not preserve_error and not isinstance(error, Exception):
            raise


def _discard_main_connection(key: str, conn: Connection) -> None:
    bag = getattr(_thread_local, "conns", {})
    if bag.get(key) is conn:
        bag.pop(key, None)
        getattr(_thread_local, "checked_at", {}).pop(key, None)
    _close_discarded_connection(conn, preserve_error=True)


class _BoundedPool:
    """Pool borné pour un thread principal donné — emprunt (acquire) / restitution (release)."""

    def __init__(
        self,
        settings: Settings,
        *,
        read_only: bool = False,
        max_size: int = _POOL_MAX_SIZE,
    ) -> None:
        self._settings = settings
        self._read_only = read_only
        self._max_size = max_size
        self._idle: queue.Queue[Connection] = queue.Queue(maxsize=max_size)
        self._created = 0
        self._lock = threading.Lock()
        self._last_ping: dict[int, float] = {}
        self._closed = False

    def _is_alive(self, conn: Connection) -> bool:
        now = time.monotonic()
        checked_at = self._last_ping.get(id(conn), 0.0)
        if now - checked_at < _PING_INTERVAL_SECONDS:
            return True
        if not _is_alive(conn):
            self._last_ping.pop(id(conn), None)
            return False
        self._last_ping[id(conn)] = now
        return True

    def _checked_alive(self, conn: Connection) -> bool:
        try:
            return self._is_alive(conn)
        except BaseException:
            # The connection was removed from the idle queue before probing it.
            self.discard(conn, preserve_error=True)
            raise

    def acquire(self) -> Connection:
        with self._lock:
            if self._closed:
                raise RuntimeError("Le pool MySQL a été remplacé ; relancez l'opération.")
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                break
            if self._checked_alive(conn):
                return conn
            with self._lock:
                self._created -= 1
        with self._lock:
            if self._created < self._max_size:
                self._created += 1
                make_new = True
            else:
                make_new = False
        if make_new:
            try:
                conn = _new_connection(self._settings, read_only=self._read_only)
            except BaseException:
                with self._lock:
                    self._created -= 1
                raise
            self._last_ping[id(conn)] = time.monotonic()
            return conn
        # Plafond atteint : attendre qu'une connexion se libère plutôt que d'en
        # ouvrir une de plus (c'est le compromis d'un pool borné).
        try:
            conn = self._idle.get(timeout=_POOL_ACQUIRE_TIMEOUT)
        except queue.Empty as exc:
            raise TimeoutError("Délai d'attente d'une connexion MySQL dépassé.") from exc
        if not self._checked_alive(conn):
            with self._lock:
                self._created -= 1
            return self.acquire()
        return conn

    def release(self, conn: Connection) -> None:
        with self._lock:
            if not self._closed:
                self._last_ping[id(conn)] = time.monotonic()
                try:
                    self._idle.put_nowait(conn)
                    return
                except queue.Full:
                    pass
        self.discard(conn)

    def discard(self, conn: Connection, *, preserve_error: bool = False) -> None:
        self._last_ping.pop(id(conn), None)
        try:
            _close_discarded_connection(conn, preserve_error=preserve_error)
        finally:
            with self._lock:
                self._created = max(0, self._created - 1)

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            self._created = 0
        self._last_ping.clear()
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return
            try:
                conn.close()
            except Exception:
                pass


def _get_bg_pool(settings: Settings, *, read_only: bool = False) -> _BoundedPool:
    key = _pool_key(settings, read_only=read_only)
    with _registry_lock:
        pool = _bg_pools.get(key)
        if pool is None:
            pool = _BoundedPool(settings, read_only=read_only)
            _bg_pools[key] = pool
        return pool


def _main_thread_connection(settings: Settings, *, read_only: bool = False) -> Connection:
    key = _pool_key(settings, read_only=read_only)
    bag = getattr(_thread_local, "conns", None)
    if bag is None:
        bag = {}
        _thread_local.conns = bag
    checked = getattr(_thread_local, "checked_at", None)
    if checked is None:
        checked = {}
        _thread_local.checked_at = checked
    conn = bag.get(key)
    if conn is not None:
        checked_at = checked.get(key, 0.0)
        try:
            alive = time.monotonic() - checked_at < _PING_INTERVAL_SECONDS or _is_alive(conn)
        except BaseException:
            _discard_main_connection(key, conn)
            raise
        if alive:
            checked[key] = time.monotonic()
            return conn
        bag.pop(key, None)
        checked.pop(key, None)
    conn = _new_connection(settings, read_only=read_only)
    bag[key] = conn
    checked[key] = time.monotonic()
    return conn


def invalidate_mysql_pool(settings: Settings | None = None) -> None:
    """Ferme les connexions concernées : thread principal courant + pool(s) d'arrière-plan (best effort)."""
    with _registry_lock:
        if settings is None:
            pools = list(_bg_pools.values())
            _bg_pools.clear()
        else:
            prefix = _settings_key(settings) + "|"
            pools = [
                pool for key, pool in _bg_pools.items() if key.startswith(prefix)
            ]
            for key in [key for key in _bg_pools if key.startswith(prefix)]:
                del _bg_pools[key]
    for pool in pools:
        pool.close_all()

    bag = getattr(_thread_local, "conns", None)
    if bag is None:
        return
    if settings is None:
        items = list(bag.items())
        bag.clear()
        checked = getattr(_thread_local, "checked_at", {})
        checked.clear()
    else:
        prefix = _settings_key(settings) + "|"
        keys = [key for key in bag if key.startswith(prefix)]
        items = [(key, bag.pop(key, None)) for key in keys]
        checked = getattr(_thread_local, "checked_at", {})
        for key in keys:
            checked.pop(key, None)
    for _key, conn in items:
        if conn is None:
            continue
        try:
            conn.close()
        except Exception as exc:
            _log.debug("Fermeture connexion MySQL : %s", exc)


@contextmanager
def pooled_connection(
    settings: Settings, *, read_only: bool = False
) -> Generator[Connection, None, None]:
    from contextlib import nullcontext
    from sme_gend.services.database_maintenance import coordinated_write
    if threading.current_thread() is _MAIN_THREAD:
        key = _pool_key(settings, read_only=read_only)
        conn = _main_thread_connection(settings, read_only=read_only)
        handled = False
        try:
            with nullcontext() if read_only else coordinated_write(settings, conn):
                try:
                    yield conn
                    if not read_only:
                        conn.commit()
                except BaseException:
                    handled = True
                    if read_only:
                        _discard_main_connection(key, conn)
                    else:
                        try:
                            conn.rollback()
                        except BaseException:
                            _log.warning("Annulation MySQL impossible ; connexion retirée avant toute réutilisation.")
                            _discard_main_connection(key, conn)
                        else:
                            checked = getattr(_thread_local, "checked_at", {})
                            checked[key] = 0.0
                    raise
        except BaseException:
            if not handled or getattr(conn, '_sme_retire_after_write', False) is True:
                _discard_main_connection(key, conn)
            raise
        if getattr(conn, '_sme_retire_after_write', False) is True:
            _discard_main_connection(key, conn)
        return

    pool = _get_bg_pool(settings, read_only=read_only)
    conn = pool.acquire()
    discarded = False
    try:
        with nullcontext() if read_only else coordinated_write(settings, conn):
            try:
                yield conn
                if not read_only:
                    conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except BaseException:
                    _log.warning("Annulation MySQL impossible ; connexion retirée avant toute réutilisation.")
                pool.discard(conn, preserve_error=True)
                discarded = True
                raise
    except BaseException:
        if not discarded:
            pool.discard(conn, preserve_error=True)
        raise
    else:
        if getattr(conn, '_sme_retire_after_write', False) is True:
            pool.discard(conn)
        else:
            pool.release(conn)
