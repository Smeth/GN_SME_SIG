"""Read-only capability checks for the optional dated workforce projection."""
from sme_gend.repositories.mysql_connection import (
    MySQLConnection, mysql_database_key, transaction_local_cache,
)


def membership_projection_enabled(settings, *, cursor=None):
    """Use the caller's database and transaction; never create schema objects."""
    cache = transaction_local_cache('membership_scope')
    key = mysql_database_key(settings)
    if cache is not None and key in cache:
        return cache[key]
    if cursor is None:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as current:
                return membership_projection_enabled(settings, cursor=current)
    cursor.execute(
        'SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() '
        "AND TABLE_NAME IN ('_sme_schema_migration','agent_effectif_movement','sme_month_membership')", ()
    )
    row = cursor.fetchone()
    enabled = bool(row and row[0] == 3)
    if enabled:
        cursor.execute("SELECT state FROM _sme_schema_migration WHERE version='0003'", ())
        row = cursor.fetchone()
        enabled = bool(row and row[0] == 'applied')
    if cache is not None:
        cache[key] = enabled
    return enabled


def visible_agent_clause(settings, *, cursor, agent_column, month=None):
    """Filter period-facing satellite reads; dossier history remains unfiltered."""
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    selected = canonical_sme_mois_yyyy_mm(month or resolve_export_mois(settings) or '')
    if not selected or not membership_projection_enabled(settings, cursor=cursor):
        return '', ()
    token = selected.replace('-', '')
    return (
        ' AND NOT EXISTS (SELECT 1 FROM sme_month_membership workforce_member '
        'JOIN sme_periode workforce_period ON workforce_period.id=workforce_member.sme_periode_id '
        f'WHERE workforce_member.agent_id={agent_column} AND workforce_member.is_member=0 '
        "AND TRIM(REPLACE(workforce_period.annee_mois,'-','')) IN (%s,%s))",
        (token, token[4:] + token[:4]),
    )
