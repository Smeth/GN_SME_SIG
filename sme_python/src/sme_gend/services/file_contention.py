"""Bounded Windows sharing retries; no fallback to in-place writes or other paths."""
from collections.abc import Callable
from pathlib import Path
from time import monotonic as _now, sleep as _pause
from typing import TypeVar
import logging
import os
import stat

T = TypeVar('T')
_WINDOWS = os.name == 'nt'
_RETRY_TIMEOUT = 1.0
_RETRY_INTERVAL = 0.025
_MAX_ATTEMPTS = 41
_LOG = logging.getLogger(__name__)


def _known_permanent_target(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) or bool(
        getattr(info,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_READONLY',1)))


def retry_file_operation(operation: Callable[[], T], *, path: Path,
                         before_attempt: Callable[[], None] | None = None) -> T:
    """Retry the syscall only. Preconditions run again before every attempt.

    Code 5 can also mean an ACL denial: never change target permissions, and
    propagate the real error after the budget. Read-only/directory targets fail
    immediately. Disconnected shares, missing files, collisions and disk errors
    are not retried. The budget cannot bound an OS call stalled by the network.
    """
    deadline = _now() + _RETRY_TIMEOUT
    for attempt in range(_MAX_ATTEMPTS):
        if before_attempt is not None:
            before_attempt()  # Validation/lease failures must never be retried.
        try:
            result = operation()
        except OSError as error:
            code = getattr(error,'winerror',None)
            if (not _WINDOWS or code not in (5,32,33) or isinstance(error,FileExistsError)
                    or (code == 5 and _known_permanent_target(path))):
                raise
            remaining = deadline - _now()
            if remaining <= 0 or attempt + 1 >= _MAX_ATTEMPTS:
                add_note = getattr(error,'add_note',None)
                if callable(add_note):
                    add_note('Attente de partage Windows expirée ; vérifiez les fichiers ouverts et les droits. '
                             'Aucun remplacement en place ni changement de permissions tenté.')
                raise
            _pause(min(_RETRY_INTERVAL,remaining))
        else:
            if attempt:
                _LOG.debug('Opération de fichier réussie après %d reprise(s) de partage Windows.',attempt)
            return result
    raise AssertionError('Unreachable retry state')


def cleanup_owned_temporary(path: Path) -> None:
    """Only for a unique temporary created by the caller, never a user target."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    attributes = getattr(info,'st_file_attributes',0)
    if (_WINDOWS and stat.S_ISREG(info.st_mode)
            and attributes & getattr(stat,'FILE_ATTRIBUTE_READONLY',1)
            and not attributes & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024)):
        # copy2 may inherit a read-only source. Clear it only on our unpublished temp.
        path.chmod(info.st_mode | stat.S_IWRITE)
    retry_file_operation(lambda:path.unlink(missing_ok=True),path=path)
