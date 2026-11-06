"""Small, explicit central traces written in the caller's business transaction."""
from dataclasses import fields,is_dataclass
from datetime import date,datetime,timezone


def audit_values(value):
    if is_dataclass(value):
        return {field.name:audit_values(getattr(value,field.name)) for field in fields(value)
                if not field.name.startswith("_") and field.name not in ("write_token",)}
    if isinstance(value,dict):
        return {str(key):audit_values(item) for key,item in value.items() if not str(key).startswith("_") and key!="write_token"}
    if isinstance(value,(list,tuple)):return [audit_values(item) for item in value]
    if isinstance(value,(datetime,date)):return value.isoformat()
    return value


def record_business_action(settings, *, action, module, entity_id=None, before=None, after=None, actor=None, mois=None):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.repositories.mysql_connection import has_import_transaction
    from sme_gend.services.auth_context import resolve_write_actor
    if not has_import_transaction(settings):
        raise RuntimeError("Trace métier sans transaction ; opération non confirmée.")
    AppConfigRepository(settings).append_audit(action=action,module=module,
        actor=resolve_write_actor(actor),ts_utc=datetime.now(timezone.utc),
        sme_mois_context=mois if mois is not None else settings.mysql_view_mois,
        payload={"entity_id":str(entity_id) if entity_id is not None else None,
                 "before":audit_values(before),"after":audit_values(after)})
