"""Atomic shared data + revision; reference API retained for existing callers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from uuid import uuid4

from sme_gend.repositories.mysql_connection import (
    MySQLConnection, after_mysql_commit, has_import_transaction,
    mysql_database_key, mysql_import_transaction,
)


@dataclass
class _ReferenceScope:
    database: tuple
    changed: bool = False
    failed: bool = False
    reference_locked: bool = False


_scope: ContextVar[_ReferenceScope | None] = ContextVar('shared_reference_write', default=None)


def has_reference_write_lock(settings) -> bool:
    state=_scope.get()
    return state is not None and state.database==mysql_database_key(settings) and state.reference_locked


def mark_shared_data_write(changed: bool) -> None:
    state = _scope.get()
    if state is None:
        raise RuntimeError('Écriture partagée sans transaction de synchronisation.')
    state.changed |= changed


def _invalidate_reference_caches() -> None:
    from sme_gend.services.grade_media import invalidate_grade_media_cache
    invalidate_grade_media_cache()
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.padmin_present import invalidate_padmin_presence_cache
    # References are global: invalidate all cached view-month variants locally.
    invalidate_liste_cache()
    invalidate_padmin_presence_cache()


def _savepoint(settings, command: str, name: str) -> None:
    # name is generated locally from uuid4().hex, never supplied by a user.
    if not has_import_transaction(settings):
        raise RuntimeError('Transaction partagée interrompue.')
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'{command} {name}')


@contextmanager
def shared_data_write(settings, *, reason: str, mois: str | None = None):
    description = 'référentiel' if reason == 'reference_write' else 'lot métier'
    database = mysql_database_key(settings)
    active = _scope.get()
    if active is not None and active.database == database:
        if not has_import_transaction(settings):
            raise RuntimeError('Transaction partagée absente.')
        try:
            yield
        except BaseException:
            active.failed = True
            raise
        return

    joined = has_import_transaction(settings)
    state = _ReferenceScope(database)
    with mysql_import_transaction(settings, read_committed=True):
        token = _scope.set(state)
        name = 'sme_ref_' + uuid4().hex if joined else None
        established = False
        try:
            if name is not None:
                _savepoint(settings, 'SAVEPOINT', name)
                established = True
            try:
                yield
                if state.failed:
                    raise RuntimeError(f'Une écriture imbriquée a échoué ; modifications du {description} annulées.')
                if state.changed:
                    from sme_gend.services.data_sync import publish_data_change
                    publish_data_change(settings, reason=reason, mois=mois)
                if established:
                    _savepoint(settings, 'RELEASE SAVEPOINT', name)
                    established = False
            except BaseException as original:
                if established:
                    try:
                        _savepoint(settings, 'ROLLBACK TO SAVEPOINT', name)
                        _savepoint(settings, 'RELEASE SAVEPOINT', name)
                    except BaseException:
                        logging.getLogger(__name__).error('Annulation du %s incertaine ; transaction externe à interrompre.', description)
                        raise RuntimeError(
                            f'Annulation du {description} impossible ; interrompez la transaction en cours '
                            'et vérifiez son résultat avant toute nouvelle saisie.'
                        ) from original
                raise
            else:
                if state.changed:
                    after_mysql_commit(_invalidate_reference_caches)
        finally:
            _scope.reset(token)



def mark_reference_write(changed: bool) -> None:
    mark_shared_data_write(changed)


@contextmanager
def shared_reference_write(settings):
    with shared_data_write(settings, reason='reference_write', mois=settings.mysql_view_mois):
        state = _scope.get()
        if not state.reference_locked:
            from sme_gend.services.data_sync import KEY_DATA_REVISION
            with MySQLConnection(settings).connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO app_config(config_key,payload) VALUES(%s,%s) "
                        "ON DUPLICATE KEY UPDATE config_key=VALUES(config_key)",
                        (KEY_DATA_REVISION,'{"token":""}'))
            state.reference_locked = True
        yield
