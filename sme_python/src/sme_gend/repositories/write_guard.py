"""Short shared transactions and consistent locking for operational writes.

Schema creation, imports and restore retain their own explicit outer boundary.
No session setting or schema migration is needed for history concurrency.
"""
from functools import wraps
from inspect import signature

from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction

EVENT_TABLES = frozenset({
    "agent_diplome", "agent_passation_grade", "agent_affectation", "agent_distinction",
    "agent_mission", "agent_malade", "agent_desertion", "parcours_stage", "agent_conge",
})


def lock_agent(settings, *, agent_id=None, legacy_id=None, event_table=None, event_id=None, carriere_id=None):
    """Lock the parent first, including when the child will be inserted/deleted.

    FOR UPDATE OF a avoids acquiring a child lock before the parent. Every
    interpolated SQL identifier comes from the closed table allowlist.
    """
    if not has_import_transaction(settings):
        raise RuntimeError("Écriture historique sans transaction partagée.")
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            if event_table is not None:
                if event_table not in EVENT_TABLES:
                    raise ValueError("Table historique non autorisée.")
                cur.execute(
                    f"SELECT a.id FROM agent a INNER JOIN {event_table} e ON e.agent_id=a.id "
                    "WHERE e.id=%s FOR UPDATE OF a", (int(event_id),),
                )
            elif carriere_id is not None:
                cur.execute("SELECT a.id FROM agent a INNER JOIN carriere_situation c ON c.agent_id=a.id "
                            "WHERE c.id=%s FOR UPDATE OF a", (int(carriere_id),))
            elif agent_id is not None:
                cur.execute("SELECT a.id FROM agent a WHERE a.id=%s FOR UPDATE", (int(agent_id),))
            else:
                cur.execute("SELECT a.id FROM agent a WHERE a.legacy_access_id=%s FOR UPDATE",
                            (str(legacy_id or "").strip(),))
            record = cur.fetchone()
            return int(record[0]) if record else None


def history_read_sql(settings, sql, alias):
    """A save must compare against the current row, even in an outer RR import."""
    if alias not in EVENT_TABLES | {"d", "m", "ps", "c"}:
        raise ValueError("Alias historique non autorisé.")
    return sql + (f" FOR UPDATE OF {alias}" if has_import_transaction(settings) else "")


def atomic_repository_write(*, agent_arg=None, legacy_arg=None, event_table=None, id_arg=None, carriere_arg=None, row_arg=None):
    """Also protect direct repository callers; nested services keep ownership."""
    if event_table is not None and event_table not in EVENT_TABLES:
        raise ValueError("Table historique non autorisée.")
    def decorate(method):
        contract = signature(method)
        @wraps(method)
        def write(self, *args, **kwargs):
            from sme_gend.services.personnel_service import _personnel_write
            bound = contract.bind(self, *args, **kwargs).arguments
            with _personnel_write(self._settings, reason="repository_write"):
                aid = None
                if row_arg or carriere_arg:
                    career = bound[row_arg].id if row_arg else bound[carriere_arg]
                    aid = lock_agent(self._settings, carriere_id=career)
                elif agent_arg:
                    aid = lock_agent(self._settings, agent_id=bound[agent_arg])
                elif legacy_arg:
                    aid = lock_agent(self._settings, legacy_id=bound[legacy_arg])
                elif event_table:
                    aid = lock_agent(self._settings, event_table=event_table, event_id=bound[id_arg])
                from sme_gend.services.temporal_changes import journal_repository_mutation
                with journal_repository_mutation(self._settings, aid, method.__name__):
                    return method(self, *args, **kwargs)
        return write
    return decorate
