"""Record the monthly consequences of one dated correction atomically."""


def log_temporal_projection(agent_id, component, effective_date, changes):
    from sme_gend.services.business_audit import audit_values
    from sme_gend.services.sensitive_audit_log import _append_entry
    from sme_gend.repositories.mysql_connection import MySQLConnection, current_transaction_settings

    personnel_id = str(agent_id)
    settings = current_transaction_settings()
    if settings is not None:
        with MySQLConnection(settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT legacy_access_id FROM agent WHERE id=%s', (int(agent_id),))
                row = cur.fetchone()
                if row and row[0]:
                    personnel_id = str(row[0])

    _append_entry(
        action='dated_history_projection',
        personnel_id=personnel_id,
        detail=f'Correction datée : {component}',
        payload=audit_values({'agent_id': agent_id, 'component': component,
                              'effective_date': effective_date, 'months': changes}),
    )
