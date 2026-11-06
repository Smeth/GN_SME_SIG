"""Audit of local connection changes, independent of business write admission."""
import logging


def record_connection_config_saved(settings):
    from sme_gend.config.user_paths import user_application_data_dir
    from sme_gend.services.audit_log import audit_timestamp
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.local_audit_mirror import append_json_mirror
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    entry = dict(action="user_paths_mysql_settings_saved", module="system",
                 ts_utc=audit_timestamp().isoformat(), actor=resolve_audit_actor(),
                 mysql_host=settings.mysql_host, mysql_port=settings.mysql_port,
                 mysql_database=settings.mysql_database)
    warnings = []
    try:
        append_json_mirror(user_application_data_dir() / "connection_config_audit.jsonl", [entry])
    except Exception:
        logging.getLogger(__name__).warning("Trace locale de configuration indisponible.")
        warnings.append("La trace locale de ce changement n'a pas pu être écrite.")
    try:
        # Use the newly selected database, never the previous runtime profile.
        AppConfigRepository(settings).append_audit(
            action=entry['action'], module='system', actor=entry['actor'],
            ts_utc=audit_timestamp(entry['ts_utc']),
            payload={key: entry[key] for key in ('mysql_host', 'mysql_port', 'mysql_database')})
    except Exception:
        logging.getLogger(__name__).warning("Paramètres locaux enregistrés ; audit central indisponible.")
        warnings.append("Le journal central n'est pas accessible (connexion ou maintenance). "
                        "La base de production n'a pas été modifiée par cet enregistrement.")
    return ("Les paramètres sont enregistrés dans paths.json.\n\n" + "\n".join(warnings)) if warnings else ""
