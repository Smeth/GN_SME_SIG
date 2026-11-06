"""Optional profile extension for exported archives; old 19-column files work."""
from contextlib import closing
from dataclasses import replace

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sexe import normalize_sexe

TABLE = "SGI_AgentProfile"
FIELDS = ("sexe", "telephone", "adresse_residence")
_COLUMNS = {"sexe": "sexe", "telephone": "telephone",
            "adresseresidence": "adresse_residence", "adresse_residence": "adresse_residence"}


def _local_table_exists(cur):
    for row in cur.tables(table=TABLE):
        name, kind = (row.table_name, row.table_type) if hasattr(row, "table_name") else (row[2], row[3])
        if str(name).casefold() == TABLE.casefold():
            if str(kind).upper() != "TABLE":
                raise ValueError("Les profils Access doivent être dans une table locale.")
            return True
    return False


def _profile(values):
    sex, phone, address = values
    normalized = normalize_sexe(sex)
    if sex is not None and str(sex).strip() and normalized is None:
        raise ValueError("Profil Access invalide : sexe non reconnu.")
    phone = str(phone).strip() if phone is not None else None
    address = str(address).strip() if address is not None else None
    if phone is not None and len(phone) > 64 or address is not None and len(address) > 512:
        raise ValueError("Profil Access invalide : coordonnées trop longues.")
    return normalized, phone or None, address or None


def read_profiles(conn):
    with closing(conn.cursor()) as cur:
        if not _local_table_exists(cur):
            return {}
        cur.execute("SELECT [AccessId],[Sexe],[Telephone],[AdresseResidence] FROM [SGI_AgentProfile]")
        result = {}
        for key, sex, phone, address in cur.fetchall():
            key = str(key or "").strip()
            if not key or len(key) > 128 or key in result:
                raise ValueError("Profils Access ambigus ou invalides.")
            result[key] = _profile((sex, phone, address))
        return result


def profile_select(cur, base_query):
    # Some newer archives already carry these fields directly. Metadata column
    # names are accepted only from this closed list, never arbitrary SQL text.
    optional = []
    for column in cur.columns(table="ListeGenerale"):
        name = str(column.column_name if hasattr(column, "column_name") else column[3])
        field = _COLUMNS.get(name.casefold())
        if field is not None:
            if any(item[1] == field for item in optional):
                raise ValueError("Colonnes de profil Access ambiguës.")
            optional.append((name, field))
    if optional:
        suffix = "," + ",".join("[" + name + "]" for name, _ in optional) + " FROM ListeGenerale"
        base_query = base_query.replace("FROM ListeGenerale", suffix, 1)
    return base_query, tuple(field for _, field in optional)


def decode_rows(raw_rows, optional, profiles, *, complete=False):
    result = []
    seen = set()
    for raw in raw_rows:
        row = ListeGeneraleRow.from_sequence(raw)
        seen.add(row.id)
        supplied = dict(zip(optional, raw[19:]))
        if row.id in profiles:
            supplied.update(zip(FIELDS, profiles[row.id]))
        if supplied:
            values = _profile(tuple(supplied.get(field) for field in FIELDS))
            row = replace(row, **{field: value for field, value in zip(FIELDS, values) if field in supplied},
                          access_profile_fields=tuple(supplied))
        result.append(row)
    if complete and set(profiles) - seen:
        raise ValueError("Un profil Access ne correspond à aucun agent de l'archive.")
    return result


def write_profiles(cur, rows, id_map):
    values = []
    for item in rows:
        key = id_map[item.agent_id]
        values.append((key, *_profile((item.row.sexe, item.row.telephone, item.row.adresse_residence))))
    if not _local_table_exists(cur):
        # Added to the staged output only. Keep legacy ListeGenerale unchanged
        # so older Access applications can still read its original columns.
        cur.execute("CREATE TABLE [SGI_AgentProfile] ([AccessId] TEXT(128) NOT NULL, "
            "[Sexe] TEXT(1),[Telephone] TEXT(64),[AdresseResidence] MEMO, "
            "CONSTRAINT [PK_SGI_AgentProfile] PRIMARY KEY ([AccessId]))")
    else:
        cur.execute("DELETE FROM [SGI_AgentProfile]")
    for value in values:
        cur.execute("INSERT INTO [SGI_AgentProfile] ([AccessId],[Sexe],[Telephone],[AdresseResidence]) VALUES (?,?,?,?)", value)


def profile_field_supplied(row, field):
    # Plain DTO callers may already supply non-null profile values. Only a
    # declared source field makes an explicit blank an instruction to clear it.
    return field in (row.access_profile_fields or ()) or getattr(row, field) is not None
