"""Optional portable identity mapping; legacy Access archives remain readable."""
from dataclasses import replace
import re

TABLE = "SGI_AgentIdentity"
PERSON_TABLES = frozenset({"tabmission", "tabmalade", "tabdeserteur", "tabstagelocal",
    "tabstageex", "tabstageinterne", "tabgainperte", "tabconge"})


def validate_identity_pairs(rows):
    mapping, seen = {}, set()
    for external, internal in rows:
        external, internal = str(external or "").strip(), str(internal or "").strip()
        if not external or not re.fullmatch(r"GEN-[A-Za-z0-9][A-Za-z0-9_#-]{0,119}", internal):
            raise ValueError("Correspondance d'identité Access invalide.")
        if external in mapping or internal.casefold() in seen:
            raise ValueError("Correspondance d'identité Access ambiguë ou dupliquée.")
        mapping[external] = internal
        seen.add(internal.casefold())
    return mapping


def identity_table_exists(cur):
    for row in cur.tables(table=TABLE):
        name = getattr(row, "table_name", None)
        kind = getattr(row, "table_type", None)
        if name is None: name, kind = row[2], row[3]
        if str(name).casefold() == TABLE.casefold():
            if str(kind).upper() != "TABLE":
                raise ValueError("Les identités Access doivent être dans une table locale.")
            return True
    return False


def read_identity_map(connection):
    cur = connection.cursor()
    try:
        if not identity_table_exists(cur):
            return {}
        cur.execute("SELECT [AccessId], [InternalKey] FROM [SGI_AgentIdentity]")
        return validate_identity_pairs(cur.fetchall())
    finally:
        cur.close()


def map_liste_rows(rows, mapping):
    return [replace(row, id=mapping[row.id], access_join_id=mapping[row.id])
            if row.id in mapping else row for row in rows]


def map_person_query(query, rows, mapping):
    table = re.search(r"\bFROM\s+\[?(\w+)\]?", query, re.I)
    if not mapping or not table or table[1].casefold() not in PERSON_TABLES:
        return rows
    if re.fullmatch(r"\s*SELECT\s+TOP\s+1\s+\*\s+FROM\s+\[?\w+\]?\s*", query, re.I):
        return rows  # Existence probe; its values are never imported.
    if not re.match(r"\s*SELECT\s+\[?ID\]?\s*,", query, re.I):
        raise ValueError("Lecture d'identité satellite Access non prise en charge.")
    return [(mapping.get(str(row[0]).strip(), row[0]), *row[1:]) for row in rows]


def write_identity_map(cur, rows, id_map):
    pairs = [(id_map[item.agent_id], item.row.access_join_id or item.access_id)
             for item in rows if str(item.row.access_join_id or "").startswith("GEN-")]
    mapping = validate_identity_pairs(pairs)
    exists = identity_table_exists(cur)
    if not exists and not mapping:
        return
    if not exists:
        cur.execute("CREATE TABLE [SGI_AgentIdentity] ([AccessId] TEXT(128) NOT NULL, "
                    "[InternalKey] TEXT(128) NOT NULL, CONSTRAINT [PK_SGI_AgentIdentity] PRIMARY KEY ([AccessId]))")
    else:
        cur.execute("DELETE FROM [SGI_AgentIdentity]")
    for external, internal in mapping.items():
        cur.execute("INSERT INTO [SGI_AgentIdentity] ([AccessId],[InternalKey]) VALUES (?,?)", (external, internal))
