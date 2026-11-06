"""Instance AuthService partagée (session) pour l'audit et la traçabilité."""

from __future__ import annotations

from sme_gend.services.auth_service import AuthService
from contextlib import contextmanager
from contextvars import ContextVar

_app_auth: AuthService | None = None
_UNBOUND = object()
_actor_identity = ContextVar("audit_actor_identity", default=_UNBOUND)


def capture_audit_identity() -> str:
    """Freeze only identity, never permissions or a database transaction."""
    captured = _actor_identity.get()
    if captured is not _UNBOUND:
        return captured
    session = _app_auth.current_session() if _app_auth is not None else None
    return (session.username or session.display_name or "").strip() if session is not None else ""


@contextmanager
def audit_identity_scope(identity):
    token = _actor_identity.set(identity)
    try:
        yield
    finally:
        _actor_identity.reset(token)


def resolve_write_actor(requested: str | None = None) -> str:
    """A connected operator cannot be replaced by an editable form signature."""
    return capture_audit_identity() or (requested or "").strip() or resolve_audit_actor()



def set_app_auth(auth: AuthService | None) -> None:
    global _app_auth
    _app_auth = auth


def get_app_auth() -> AuthService | None:
    return _app_auth


def resolve_audit_actor(*, fallback: str | None = None) -> str:
    """
    Identifiant humain pour les journaux :
    session auth → signataire SME (sme_context) → paramètre fallback → « non identifié ».
    Fonctionne que l'authentification soit activée ou non.
    """
    identity = capture_audit_identity()
    if identity:
        return identity
    from sme_gend.config.sme_context import peek_editeur_defaut

    ed = peek_editeur_defaut()
    if ed:
        return ed
    fb = (fallback or "").strip()
    return fb or "non identifié"
