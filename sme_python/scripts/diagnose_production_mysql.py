"""Read-only production report; no schema initialization, migrations or unlocks.

Run on the affected workstation with its usual Python environment:
    python scripts/diagnose_production_mysql.py
The report contains no password or personnel records.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def diagnose(settings):
    import pymysql
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.database_maintenance import (
        TABLE, _request_lock, lock_name, worker_lock_name,
    )
    report = {'target': f'{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}'}
    # Direct connection intentionally avoids startup hooks and repository DDL.
    with pymysql.connect(**MySQLConnection(settings).connect_params()) as conn:
        with conn.cursor() as cur:
            cur.execute('START TRANSACTION READ ONLY')
            cur.execute('SELECT DATABASE(), VERSION(), UTC_TIMESTAMP()')
            report['database'], report['server_version'], report['server_utc'] = cur.fetchone()
            cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE=%s', ('BASE TABLE',))
            tables = {row[0] for row in cur.fetchall()}
            if TABLE in tables:
                cur.execute(f'SELECT active,token,reason,started_at,owner_connection FROM `{TABLE}` WHERE id=1')
                row = cur.fetchone()
                report['maintenance'] = dict(zip(('active', 'token', 'reason', 'started_at', 'owner_connection'), row)) if row else {'error': 'missing state row'}
            else:
                report['maintenance'] = {'active': False, 'table_absent': True}
            report['live_locks'] = {}
            for label, name in [('request', _request_lock(settings)), ('barrier', lock_name(settings)), ('worker', worker_lock_name(settings))]:
                cur.execute('SELECT IS_USED_LOCK(%s)', (name,))
                report['live_locks'][label] = cur.fetchone()[0]
            report['counts'] = {}
            for table in ('agent', 'app_config', 'audit_journal'):
                if table in tables:
                    cur.execute(f'SELECT COUNT(*) FROM `{table}`')
                    report['counts'][table] = cur.fetchone()[0]
            if 'audit_journal' in tables:
                cur.execute("SELECT ts_utc,action FROM audit_journal WHERE action IN (%s,%s) ORDER BY ts_utc DESC LIMIT 5", ('application_reset', 'maintenance_recovered'))
                report['recent_reset_events'] = cur.fetchall()
        conn.rollback()
    return report


if __name__ == '__main__':
    from sme_gend.config import get_settings
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    try:
        result = diagnose(get_settings())
    except Exception as exc:
        print(json.dumps({'error': format_mysql_error_for_user(exc)}, ensure_ascii=True))
        sys.exit(1)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=True))
