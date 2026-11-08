"""A database read/write barrier: parallel writers, rare exclusive publication."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessGateLease:
    database: tuple
    connection_id: int
    exclusive: bool
    available: bool


_lease = ContextVar('sme_publication_write_gate', default=None)
GATE_TABLE = 'sme_publication_write_gate'


def _available(connection):
    if getattr(connection, '_sme_publication_gate_available', False) is True:
        return True
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM information_schema.TABLES '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', (GATE_TABLE,))
        row = cur.fetchone()
    found = row is not None and int(row[0]) == 1
    if found:
        connection._sme_publication_gate_available = True
    return found


@contextmanager
def connection_business_gate(settings, connection, *, exclusive=False, required=False):
    """Lock before business rows; this function never creates schema or commits."""
    from sme_gend.repositories.mysql_connection import mysql_database_key
    database = mysql_database_key(settings)
    connection_id = int(connection.thread_id())
    active = _lease.get()
    if active is not None and active.database == database and active.connection_id == connection_id:
        if exclusive and not active.exclusive:
            raise RuntimeError('La barrière exclusive doit être prise avant la transaction métier.')
        if required and not active.available:
            raise RuntimeError('Migration des publications requise avant cette opération.')
        yield active
        return
    available = _available(connection)
    if not available and required:
        raise RuntimeError('Migration des publications requise avant cette opération.')
    if available:
        with connection.cursor() as cur:
            cur.execute('SELECT id FROM sme_publication_write_gate WHERE id=1 ' +
                        ('FOR UPDATE' if exclusive else 'FOR SHARE'))
            row = cur.fetchone()
            if row is None or int(row[0]) != 1:
                raise RuntimeError('Barrière de publication absente ; vérifiez la migration de la base.')
    lease = BusinessGateLease(database, connection_id, exclusive, available)
    token = _lease.set(lease)
    try:
        yield lease
    finally:
        _lease.reset(token)


def assert_exclusive_publication_barrier(settings):
    from sme_gend.repositories.mysql_connection import mysql_database_key, has_import_transaction
    lease = _lease.get()
    if (lease is None or lease.database != mysql_database_key(settings) or not lease.exclusive
            or not lease.available or not has_import_transaction(settings)):
        raise RuntimeError('Cette opération exige une barrière exclusive de publication dans la transaction courante.')
    return lease


@contextmanager
def publication_write_barrier(settings, *, exclusive=True):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, has_import_transaction
    if has_import_transaction(settings):
        if exclusive:
            assert_exclusive_publication_barrier(settings)
        yield _lease.get()
        return
    with mysql_import_transaction(settings, read_committed=True,
                                  business_gate='exclusive' if exclusive else 'shared'):
        if exclusive:
            assert_exclusive_publication_barrier(settings)
        yield _lease.get()
