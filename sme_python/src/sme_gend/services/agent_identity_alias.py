"""Résolution des anciens identifiants, sans modifier les archives source."""


def apply_identity_alias_schema(settings):
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    from sme_gend.repositories.mysql_common import _split_sql_statements
    if has_import_transaction(settings):
        raise RuntimeError("Initialisation des rapprochements interdite dans une transaction métier.")
    sql = (project_root() / "data/schema/mysql_agent_identity_alias.sql").read_text(encoding="utf-8")
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            for statement in _split_sql_statements(sql):
                cursor.execute(statement)


def load_identity_aliases(cursor):
    cursor.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() "
                   "AND table_name='agent_identity_alias'")
    if not cursor.fetchone():
        return {}
    cursor.execute("SELECT x.alias_legacy_id,a.id,a.legacy_access_id FROM agent_identity_alias x "
                   "JOIN agent a ON a.id=x.canonical_agent_id")
    return {str(old).strip().casefold(): (int(aid), str(current).strip()) for old, aid, current in cursor.fetchall()}


def resolve_agent_id(cursor, legacy):
    key = str(legacy or "").strip()
    cursor.execute("SELECT id FROM agent WHERE legacy_access_id = %s", (key,))
    row = cursor.fetchone()
    if row:
        return int(row[0])
    return load_identity_aliases(cursor).get(key.casefold(), (None, ""))[0]


def canonical_legacy_id(cursor, legacy):
    key = str(legacy or "").strip()
    return load_identity_aliases(cursor).get(key.casefold(), (None, key))[1]


def aliases_for(settings, legacy):
    from sme_gend.repositories.mysql_connection import MySQLConnection
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            mapping = load_identity_aliases(cursor)
    current = mapping.get(str(legacy).casefold(), (None, str(legacy)))[1]
    return [current] + sorted(old for old, (_, key) in mapping.items() if key.casefold() == current.casefold())


def identity_photo_path(settings, legacy):
    from sme_gend.services.personnel_media import person_photo_path
    photo = person_photo_path(str(legacy))
    if photo is not None:
        return photo
    for old in aliases_for(settings, legacy):
        photo = person_photo_path(old)
        if photo is not None:
            return photo
    return None
