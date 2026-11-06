"""Permission checks at service boundaries, including direct action calls."""
from functools import wraps
from contextlib import contextmanager
from contextvars import ContextVar

_structure_layout_sync = ContextVar('structure_layout_sync', default=False)


@contextmanager
def structure_layout_sync():
    """Allow layout consistency updates inside an authorized structure mutation."""
    token = _structure_layout_sync.set(True)
    try:
        yield
    finally:
        _structure_layout_sync.reset(token)


def may_sync_structure_layout():
    from sme_gend.services.auth_guard import session_has_permission
    return _structure_layout_sync.get() and session_has_permission('structures.codes')


def assert_any_permission(*permissions):
    from sme_gend.services.auth_guard import session_has_permission
    if not any(session_has_permission(permission) for permission in permissions):
        raise PermissionError("Votre rôle ne permet pas cette action.")


def requires_any_permission(*permissions):
    def decorate(action):
        @wraps(action)
        def checked(*args, **kwargs):
            assert_any_permission(*permissions)
            return action(*args, **kwargs)
        return checked
    return decorate
