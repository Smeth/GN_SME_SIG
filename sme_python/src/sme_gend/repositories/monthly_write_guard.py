"""Compare monthly editor snapshots without a destructive schema migration."""
from sme_gend.repositories.write_guard import EVENT_TABLES

UNSET = object()
FIELDS = {
    "agent_mission": ("id", "agent_id", "sme_periode_id", "date_debut", "date_fin", "nature_libelle", "lieu", "reference_dossier", "observation", "source_ligne_legacy", "import_lot_id"),
    "agent_malade": ("id", "agent_id", "sme_periode_id", "date_debut", "date_fin", "pathologie", "observation", "source_ligne_legacy", "import_lot_id"),
    "agent_desertion": ("id", "agent_id", "sme_periode_id", "date_desertion", "reference_dossier", "observation", "source_ligne_legacy", "import_lot_id"),
    "parcours_stage": ("id", "agent_id", "sme_periode_id", "type_stage_code", "date_debut", "date_fin", "nature_libelle", "lieu", "reference_dossier", "session", "observation", "source_ligne_legacy", "import_lot_id"),
}


def revision_sql(table, alias="e"):
    if table not in FIELDS or alias not in {"e", "m", "d", "ps"}:
        raise ValueError("Identifiant SQL non autorisé.")
    fields = ",".join(f"{alias}.{name}" for name in FIELDS[table])
    return f"SHA2(CAST(JSON_ARRAY({fields}) AS CHAR),256)"


def token_from_row(row, index):
    if len(row) < index + 3:
        return None  # Historical test/import DTOs remain readable.
    return (int(row[index]), int(row[index+1]) if row[index+1] is not None else None, str(row[index+2]))


def checked_target(cur, table, agent_id, period_id, legacy_id, expected, *, type_code=None):
    """Return the displayed row to update, or None for an absent month.

    Parent locking is owned by atomic_repository_write. Current locking reads
    also work when an importer owns an outer REPEATABLE READ transaction.
    """
    if expected is UNSET:
        return UNSET
    token = getattr(expected, "write_token", None)
    if expected is not None and (token is None or len(token) != 3):
        raise ValueError("Version de fiche indisponible. Rechargez les données avant de modifier.")
    sql = (f"SELECT e.id,e.sme_periode_id,{revision_sql(table)},e.source_ligne_legacy "
           f"FROM {table} e WHERE e.agent_id=%s")
    params = [agent_id]
    if table == "parcours_stage":
        sql += " AND e.type_stage_code=%s"
        params.append(type_code)
    sql += " FOR UPDATE"
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    # Exactly the scope used by the previous month replacement operation.
    scoped = [r for r in rows if r[1] == period_id and
              (table == "agent_desertion" or r[3] == legacy_id or
               table in {"agent_malade", "parcours_stage"} and r[3] is None)]
    conflict = False
    if token is not None:
        current = next((r for r in rows if int(r[0]) == token[0]), None)
        conflict = current is None or current[1] != token[1] or str(current[2]) != token[2]
        target = token[0] if token[1] == period_id else None
        # A fallback from a previous month may create the selected month only
        # while it is still empty; a concurrent creation must be reviewed.
        if target is None and scoped:
            conflict = True
    else:
        target = None
        conflict = bool(scoped)
    if conflict:
        raise ValueError("Cette fiche a été modifiée ou supprimée sur un autre poste. "
                         "Rechargez les données avant de recommencer.")
    return target
