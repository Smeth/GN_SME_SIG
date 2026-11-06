"""Cooperative media gate: one actual MySQL schema, independent of host aliases."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
from threading import RLock

_media_file_lock = RLock()


def locked_media_stream(path):
    from sme_gend.services.local_audit_mirror import locked_mirror_stream
    return locked_mirror_stream(path, thread_lock=_media_file_lock)

import pymysql


class MediaStorageBusyError(TimeoutError):
    pass


class MediaStorageLockLostError(RuntimeError):
    pass


def media_lock_name(database: str) -> str:
    return 'sgi_media_root_' + hashlib.sha256(database.casefold().encode('utf-8')).hexdigest()[:40]


def decode_payload(raw):
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode('utf-8')
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError('Configuration centrale des médias invalide ; aucune bascule effectuée.')
    return data


@dataclass
class MediaStorageGate:
    connection: object
    name: str
    connection_id: int

    def check(self):
        try:
            with self.connection.cursor() as cur:
                cur.execute('SELECT IS_USED_LOCK(%s), CONNECTION_ID()', (self.name,))
                row = cur.fetchone()
            if not row or row != (self.connection_id, self.connection_id):
                raise MediaStorageLockLostError('Verrou des médias perdu ; opération interrompue.')
            settings = getattr(self, 'maintenance_settings', None)
            if settings is not None:
                from sme_gend.services.database_maintenance import check_owned_barrier
                check_owned_barrier(settings, self.connection)
        except MediaStorageLockLostError:
            raise
        except Exception as exc:
            raise MediaStorageLockLostError('Connexion de coordination des médias perdue ; opération interrompue.') from exc

    def payload(self, key="shared_personnel_media"):
        self.check()
        with self.connection.cursor() as cur:
            cur.execute('SELECT payload FROM app_config WHERE config_key=%s', (key,))
            row = cur.fetchone()
        return decode_payload(row[0]) if row else None


@contextmanager
def media_storage_gate(settings, *, timeout=5):
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    if has_import_transaction(settings):
        raise RuntimeError('La copie ou bascule des médias doit se faire hors transaction métier en cours.')
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 <= timeout <= 30:
        raise ValueError('Délai de coordination des médias invalide.')
    params = MySQLConnection(settings).connect_params()
    params.update(autocommit=True, read_timeout=max(15, timeout+5))
    connection = pymysql.connect(**params)
    acquired = False
    failed = False
    from sme_gend.services.database_maintenance import coordinated_write
    write_gate = coordinated_write(settings, connection)
    write_gate_entered = False
    try:
        write_gate.__enter__()
        write_gate_entered = True
        with connection.cursor() as cur:
            cur.execute('SELECT DATABASE(), CONNECTION_ID()')
            database, connection_id = cur.fetchone()
            if not database:
                raise RuntimeError('Base MySQL absente pour coordonner les médias.')
            name = media_lock_name(database)
            cur.execute('SELECT GET_LOCK(%s,%s)', (name,timeout))
            acquired = cur.fetchone() == (1,)
        if not acquired:
            raise MediaStorageBusyError(
                'Une copie ou bascule de dossier partagé est en cours sur un autre poste. '
                'Aucune nouvelle image copiée par cette demande ; réessayez après sa fin.'
            )
        from sme_gend.services.database_maintenance import _owner, worker_lock_name
        owner = _owner.get()
        if owner is not None:
            with connection.cursor() as cur:
                cur.execute('SELECT GET_LOCK(%s,0)', (worker_lock_name(settings),))
                if cur.fetchone() != (1,):
                    raise MediaStorageBusyError('Un traitement de restauration est encore actif.')
        try:
            gate = MediaStorageGate(connection,name,int(connection_id))
            if owner is not None:
                gate.maintenance_settings = settings
            yield gate
        except BaseException:
            failed = True
            raise
        finally:
            if acquired:
                try:
                    with connection.cursor() as cur:
                        cur.execute('SELECT RELEASE_LOCK(%s)', (name,))
                        if cur.fetchone() != (1,):
                            raise MediaStorageLockLostError('Verrou des médias perdu à la fin de l’opération.')
                except Exception:
                    if not failed:
                        raise
                    logging.getLogger(__name__).warning('Fin de coordination des médias interrompue ; connexion dédiée fermée.')
    finally:
        import sys
        try:
            if write_gate_entered:
                write_gate.__exit__(*sys.exc_info())
        finally:
            if connection.open:
                connection.close()
