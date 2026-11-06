"""Keep administrative account changes and their audit in one transaction."""
from copy import deepcopy
from functools import wraps
from contextvars import ContextVar

_active = ContextVar("auth_change",default=None)


def atomic_auth_change(method):
    @wraps(method)
    def write(self,*args,**kwargs):
        if _active.get() is self:
            return method(self,*args,**kwargs)
        from sme_gend.repositories.mysql_connection import CommittedMirrorError
        from sme_gend.services.shared_reference_write import shared_data_write
        from sme_gend.services.auth_context import audit_identity_scope,capture_audit_identity
        settings=self._settings()
        before=(deepcopy(self._config),self._session,self._enforced)
        session=self.current_session()
        identity=capture_audit_identity() or ((session.username or "").strip() if session is not None else "")
        token=_active.set(self)
        try:
            with audit_identity_scope(identity),shared_data_write(settings,reason="auth_change"):
                return method(self,*args,**kwargs)
        except CommittedMirrorError:
            raise
        except BaseException:
            self._config,self._session,self._enforced=before
            raise
        finally:
            _active.reset(token)
    return write



def audited_login(method):
    """A missing login trace must never leave a newly authenticated session active."""
    @wraps(method)
    def login(self,*args,**kwargs):
        previous=self._session
        try:
            return method(self,*args,**kwargs)
        except (RuntimeError,OSError) as exc:
            created=self._session if self._session is not previous else None
            if created is not None:
                self._session=None
                if created.session_token:
                    try:
                        from sme_gend.services.auth_session_registry import unregister_session
                        unregister_session(self._settings(),created.session_token)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).error("Nettoyage du registre de connexion à reprendre ; aucune session locale active.")
            raise ValueError("Connexion non validée : vérifiez MySQL et le journal central avant de réessayer.") from exc
    return login
