"""Archives d'un mois : données paramétrées, jamais d'exécution du SQL importé.

Les identités et référentiels existants ne sont jamais écrasés. Les changements
de statut des périodes, les comptes et les journaux sont hors du périmètre.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import base64
import json
import re
import zipfile

from sme_gend.services.backup_sql import split_statements, target_table, is_safe_setting, validate_restore_statement
from sme_gend.services.mysql_restore_staging import validate_staged_statement, _create_body_end
from sme_gend.services.mysql_backup_service import _literal
from sme_gend.services.mysql_import_period import normalize_import_mois as _normalize_import_mois
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.services.operation_progress import operation_progress

FORMAT = "sgi-dpf-month-v1"
EXCLUDED = {"audit_journal", "workflow_task", "career_alert_ack", "career_alert_ack_occurrence",
            "app_config", "app_user_preference", "agent_identity_alias"}
MONTH_COLUMNS = ("sme_periode_mois", "mois_sme")
DATED = {"agent_diplome":("date_obtention","obs"), "agent_distinction":("date_attribution","source"),
         "agent_passation_grade":("date_passation","obs"), "agent_affectation":("date_affectation","obs")}
TECHNICAL = {"cree_le", "updated_le", "created_at", "updated_at"}
IDENT = r"(?:`(?:``|[^`])+`|[A-Za-z_][A-Za-z0-9_$]*)"
TABLE_LABELS = {"sme_periode":"Période SME", "agent":"Identités des agents",
    "carriere_situation":"Situations du mois", "import_lot":"Provenance des imports",
    "etl_erreur":"Constats d’import", "agent_diplome":"Diplômes du dossier",
    "agent_distinction":"Distinctions", "agent_affectation":"Affectations",
    "agent_passation_grade":"Historique des grades", "agent_position_event":"Changements de position",
    "agent_mission":"Missions", "agent_malade":"Maladies", "agent_desertion":"Désertions",
    "agent_conge":"Congés", "parcours_stage":"Stages", "mouvement_effectif":"Gains et pertes",
    "ref_grade":"Référentiel des grades", "ref_fonction":"Référentiel des fonctions",
    "ref_structure":"Référentiel des structures", "ref_position_admin":"Positions administratives"}


def table_label(name):
    return TABLE_LABELS.get(name,name.replace("ref_","Référentiel ").replace("_"," ").capitalize())


def normalize_import_mois(value):
    month = _normalize_import_mois(value)
    date.fromisoformat(month+"-01")
    return month


def quote(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
        raise ValueError("Identifiant de table ou colonne non pris en charge.")
    return "`" + name + "`"


def portable(value):
    if isinstance(value, (date, datetime, time, Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"binary":base64.b64encode(bytes(value)).decode("ascii")}
    return value


def sql_value(value):
    if isinstance(value, dict) and set(value) == {"binary"}:
        return base64.b64decode(value["binary"], validate=True)
    if value is not None and not isinstance(value, (str, int, float)):
        raise ValueError("Valeur d'archive invalide.")
    return value


def _parts(text):
    """Séparation des listes SQL en respectant parenthèses et chaînes."""
    out, start, depth, quoted, i = [], 0, 0, None, 0
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == "\\" and quoted != "`": i += 1
            elif ch == quoted:
                if i+1 < len(text) and text[i+1] == quoted: i += 1
                else: quoted = None
        elif ch in "'\"`": quoted = ch
        elif ch == "(": depth += 1
        elif ch == ")": depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i].strip()); start = i+1
        i += 1
    if quoted or depth:
        raise ValueError("Liste SQL incomplète.")
    if text[start:].strip(): out.append(text[start:].strip())
    return out


def _name(token):
    value = token.strip().strip("`").replace("``", "`")
    quote(value)
    return value


def _names(text):
    return [_name(item) for item in _parts(text)]


def schema_from_ddl(statement):
    validate_staged_statement(statement)
    name = target_table(statement)
    quote(name)
    body = statement[statement.index("(")+1:_create_body_end(statement)-1]
    columns, primary, foreign, auto, unique, types = [], [], [], None, [], {}
    for part in _parts(body):
        pk = re.match(r"PRIMARY\s+KEY\s*\(([^)]+)\)",part,re.I)
        fk = re.search(r"FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+("+IDENT+r")\s*\(([^)]+)\)",part,re.I)
        uk = re.match(r"UNIQUE\s+(?:KEY|INDEX)\s+"+IDENT+r"\s*\(",part,re.I)
        if pk: primary = _names(pk[1])
        elif fk: foreign.append((_names(fk[1]),_name(fk[2]),_names(fk[3])))
        elif uk:
            specs = []
            for item in _parts(part[part.index('(')+1:_create_body_end(part)-1]):
                match = re.fullmatch(r"("+IDENT+r")(?:\s*\((\d+)\))?",item)
                if not match: raise ValueError("Index unique non pris en charge.")
                specs.append(_name(match[1])+(":"+match[2] if match[2] else ""))
            unique.append(specs)
        elif not re.match(r"(?:CONSTRAINT|UNIQUE|KEY|INDEX|CHECK|FULLTEXT|SPATIAL)\b",part,re.I):
            match = re.match(IDENT,part)
            if not match: raise ValueError("Définition de colonne inconnue.")
            column = _name(match[0])
            if re.search(r"\bGENERATED\b",part,re.I):
                raise ValueError("Colonnes calculées non prises en charge pour l'archive mensuelle.")
            columns.append(column)
            type_match = re.match(r"\s*([A-Za-z]+(?:\s*\([^)]*\))?(?:\s+unsigned)?)",part[match.end():],re.I)
            if not type_match: raise ValueError("Type de colonne inconnu.")
            types[column] = re.sub(r"\s+","",type_match[1]).casefold()
            if re.search(r"\bAUTO_INCREMENT\b",part,re.I): auto = column
    if not primary or not columns or len(set(columns)) != len(columns):
        raise ValueError(f"Clé primaire ou colonnes invalides : {name}.")
    return name, dict(ddl=statement.rstrip(";"), columns=columns, primary=primary, foreign=foreign, auto=auto, unique=unique, types=types, rows=[])


def _decode_literal(raw):
    if raw.upper() == "NULL": return None
    if raw.lower().startswith("_utf8mb4"):
        return bytes.fromhex(re.split("0x",raw,maxsplit=1,flags=re.I)[1]).decode("utf-8")
    if raw.lower().startswith("0x"):
        return portable(bytes.fromhex(raw[2:]))
    if raw.startswith("'"):
        text, out, i = raw[1:-1], [], 0
        escapes = {"0":"\0", "n":"\n", "r":"\r", "t":"\t", "b":"\b", "Z":"\x1a"}
        while i < len(text):
            if text[i] == "\\":
                i += 1; out.append(escapes.get(text[i],text[i]))
            elif text[i:i+2] == "''": out.append("'"); i += 1
            else: out.append(text[i])
            i += 1
        return "".join(out)
    return int(raw) if re.fullmatch(r"[+-]?\d+", raw) else str(Decimal(raw))


def _native_directives(sql):
    """Retire seulement les réglages de session de mysqldump hors des littéraux.

    Ces réglages ne sont jamais exécutés. Les routines/événements/déclencheurs et
    les modes modifiant l'interprétation des chaînes restent refusés.
    """
    result, i, quoted = [], 0, None
    while i < len(sql):
        ch = sql[i]
        if quoted:
            result.append(ch)
            if ch == "\\" and quoted != "`" and i+1 < len(sql):
                i += 1; result.append(sql[i])
            elif ch == quoted:
                if i+1 < len(sql) and sql[i+1] == quoted:
                    i += 1; result.append(sql[i])
                else: quoted = None
        elif ch in "'\"`": quoted = ch; result.append(ch)
        elif sql.startswith("--",i) or ch == "#":
            end = sql.find("\n",i)
            end = len(sql) if end < 0 else end
            result.append(sql[i:end]); i = end; continue
        elif sql.startswith("/*",i):
            end = sql.find("*/",i+2)
            if end < 0: raise ValueError("Commentaire SQL incomplet.")
            if sql.startswith("/*!",i):
                directive = re.sub(r"^\d+\s*","",sql[i+3:end]).strip()
                setting = re.match(r"SET\s+(@(?:OLD|SAVED)_\w+|@saved_cs_client|CHARACTER_SET_CLIENT|CHARACTER_SET_RESULTS|COLLATION_CONNECTION|TIME_ZONE|SQL_MODE|SQL_NOTES|UNIQUE_CHECKS|FOREIGN_KEY_CHECKS|NAMES)\b",directive,re.I)
                keys = re.fullmatch(r"ALTER\s+TABLE\s+"+IDENT+r"\s+(?:DISABLE|ENABLE)\s+KEYS",directive,re.I)
                if (not setting and not keys) or "NO_BACKSLASH_ESCAPES" in directive.upper() or "ANSI_QUOTES" in directive.upper():
                    raise ValueError("Directive native SQL non prise en charge pour une restauration mensuelle.")
                result.append(" ")
            else: result.append(sql[i:end+2])
            i = end+2; continue
        else: result.append(ch)
        i += 1
    return "".join(result)


def read_sql(sql):
    """Dialecte interne validé ; aucune instruction n'est transmise à MySQL."""
    with operation_progress((('parse', 'Analyse des instructions du fichier SQL'),
                             ('verify', 'Contrôle des données de l’archive'))) as operation:
        return _read_sql_with_progress(sql, operation)


def _read_sql_with_progress(sql, operation):
    operation.start('parse', detail='Découpage du SQL ; aucune instruction ne sera exécutée')
    tables = {}
    statements = split_statements(_native_directives(sql))
    for index, statement in enumerate(statements, 1):
        if index % 250 == 0 or index == len(statements):
            operation.update('parse', current=index - 1, total=len(statements), detail=f'{index - 1:,}/{len(statements):,} instruction(s) analysée(s)')
        if is_safe_setting(statement): continue
        if re.fullmatch(r"(?:LOCK\s+TABLES\s+"+IDENT+r"\s+WRITE|UNLOCK\s+TABLES)\s*;?",statement,re.I): continue
        validate_restore_statement(statement)
        command = statement.lstrip().split(None,1)[0].upper()
        name = target_table(statement)
        if command == "CREATE":
            name, schema = schema_from_ddl(statement)
            if name in tables: raise ValueError("Définition de table en double.")
            tables[name] = schema
        elif command == "INSERT":
            if name not in tables: raise ValueError("Données sans définition de table.")
            match = re.match(r"\s*INSERT\s+INTO\s+"+IDENT+r"\s*(?:\(([^)]+)\))?\s*VALUES\s*(.*?);?\s*$",statement,re.I|re.S)
            if not match: raise ValueError("INSERT non pris en charge.")
            columns = _names(match[1]) if match[1] else tables[name]["columns"]
            if columns != tables[name]["columns"]:
                raise ValueError("Le dump doit inclure toutes les colonnes, dans l'ordre du schéma.")
            for raw_row in _parts(match[2].removesuffix(";").strip()):
                values = [_decode_literal(v) for v in _parts(raw_row[1:-1])]
                if len(values) != len(columns): raise ValueError("Nombre de colonnes incohérent.")
                tables[name]["rows"].append(dict(zip(columns,values)))
        elif command != "DROP": raise ValueError("Instruction SQL non prise en charge.")
    operation.finish('parse', detail=f'{len(statements):,} instruction(s), {len(tables)} table(s)')
    operation.start('verify')
    validate_dataset(tables)
    operation.finish('verify')
    return tables


def validate_dataset(tables):
    for name, table in tables.items():
        quote(name)
        if not table["primary"] or not set(table["primary"]) <= set(table["columns"]):
            raise ValueError("Clé primaire absente ou invalide.")
        seen = set()
        for row in table["rows"]:
            if set(row) != set(table["columns"]): raise ValueError("Colonnes d'archive incohérentes.")
            for value in row.values(): sql_value(value)
            key = tuple(row[c] for c in table["primary"])
            if None in key or key in seen: raise ValueError(f"Clé primaire absente ou dupliquée : {name}.")
            seen.add(key)


def months_in(tables):
    return sorted({normalize_import_mois(r["annee_mois"]) for r in tables.get("sme_periode",{}).get("rows",[])})


def scope_tables(tables):
    return {name for name,t in tables.items() if name not in EXCLUDED and
        ("sme_periode_id" in t["columns"] or any(c in t["columns"] for c in MONTH_COLUMNS)
         or name in DATED or name == "etl_erreur")}


def ownership(row, name, periods, lots):
    values = set()
    if row.get("sme_periode_id") is not None:
        if row["sme_periode_id"] not in periods: raise ValueError("Période référencée absente.")
        values.add(periods[row["sme_periode_id"]])
    for column in MONTH_COLUMNS:
        if row.get(column): values.add(normalize_import_mois(row[column]))
    if row.get("import_lot_id") is not None:
        if row["import_lot_id"] not in lots: raise ValueError("Lot d'import référencé absent.")
        period = lots[row["import_lot_id"]]
        if period is not None:
            if period not in periods: raise ValueError("Période du lot absente.")
            values.add(periods[period])
    if name in DATED and not values:
        date_col, origin_col = DATED[name]
        origin = row.get(origin_col) or ""
        if origin.startswith("Import archive "):
            values.add(normalize_import_mois(origin[len("Import archive "):]))
        elif row.get(date_col):
            values.add(normalize_import_mois(str(row[date_col])[:7]))
    if len(values) > 1: raise ValueError(f"Provenances mensuelles contradictoires : {name}.")
    return next(iter(values),None)


def extract_month(tables, month):
    month = normalize_import_mois(month)
    validate_dataset(tables)
    periods = {r["id"]:normalize_import_mois(r["annee_mois"]) for r in tables.get("sme_periode",{}).get("rows",[])}
    if list(periods.values()).count(month) != 1: raise ValueError("Le mois choisi est absent ou ambigu dans la source.")
    lots = {r["id"]:r.get("sme_periode_id") for r in tables.get("import_lot",{}).get("rows",[])}
    scoped = scope_tables(tables)
    selected = {name:[] for name in scoped | {"sme_periode"}}
    selected["sme_periode"] = [r for r in tables["sme_periode"]["rows"] if normalize_import_mois(r["annee_mois"]) == month]
    for name in scoped:
        selected[name] = [r for r in tables[name]["rows"] if ownership(r,name,periods,lots) == month]
    # Fermer les dépendances par clés étrangères, jamais par proximité de date.
    changed = True
    while changed:
        changed = False
        for name, rows in list(selected.items()):
            if not rows:
                continue
            for columns,parent,parent_cols in tables[name]["foreign"]:
                for row in list(rows):
                    key = tuple(row[c] for c in columns)
                    if any(v is None for v in key): continue
                    if parent not in tables: raise ValueError(f"Table de dépendance absente : {parent}.")
                    matches = [r for r in tables[parent]["rows"] if tuple(r[c] for c in parent_cols) == key]
                    if len(matches) != 1: raise ValueError(f"Dépendance absente ou ambiguë : {name} → {parent}.")
                    match = matches[0]
                    if parent in EXCLUDED or (parent not in scoped and parent not in ("agent","sme_periode") and not parent.startswith("ref_")):
                        raise ValueError(f"Dépendance non mensuelle non prise en charge : {parent}.")
                    if parent == "sme_periode" and periods[match["id"]] != month or parent in scoped and ownership(match,parent,periods,lots) != month:
                        raise ValueError("Une dépendance appartient à un autre mois. Extraction refusée.")
                    if match not in selected.setdefault(parent,[]):
                        selected[parent].append(match); changed = True
    for name,table in tables.items():
        for cols,parent,pcols in table["foreign"]:
            if parent not in scoped: continue
            selected_keys = {tuple(r[c] for c in pcols) for r in selected.get(parent,[])}
            if any(tuple(r[c] for c in cols) in selected_keys and r not in selected.get(name,[]) for r in table["rows"]):
                raise ValueError(f"Des données liées au mois dans {name} ne peuvent pas être isolées sans ambiguïté.")
    return {name:{**tables[name],"rows":rows} for name,rows in sorted(selected.items())}


def dataset_token(tables):
    normalized = {}
    for name,table in sorted(tables.items()):
        normalized[name] = {**table,"rows":sorted(table["rows"],key=lambda r:json.dumps(r,sort_keys=True,ensure_ascii=False))}
    return fingerprint(normalized)


def load_source(path):
    with operation_progress((('read', 'Lecture et contrôle du fichier source'),
                             ('analyse', 'Analyse des données mensuelles'))) as operation:
        return _load_source_with_progress(path, operation)


def _load_source_with_progress(path, operation):
    operation.start('read')
    path = Path(path)
    if path.suffix.lower() == ".sql":
        if path.stat().st_size > 512*1024*1024:
            raise ValueError("SQL trop volumineux pour l'analyse mensuelle (limite 512 Mo).")
        sql = path.read_text(encoding="utf-8-sig")
        operation.finish('read', detail=f'{path.stat().st_size:,} octet(s) lus')
        operation.start('analyse')
        result = read_sql(sql)
        operation.finish('analyse')
        return result
    with zipfile.ZipFile(path) as archive:
        from sme_gend.services.backup_restore_service import SQL_DUMP_NAME, inspect_backup
        if SQL_DUMP_NAME in archive.namelist():
            if archive.getinfo(SQL_DUMP_NAME).file_size > 512*1024*1024:
                raise ValueError("SQL contenu dans l'archive trop volumineux (limite 512 Mo).")
            inspect_backup(path)
            sql = archive.read(SQL_DUMP_NAME).decode("utf-8-sig")
            operation.finish('read')
            operation.start('analyse')
            result = read_sql(sql)
            operation.finish('analyse')
            return result
        if archive.namelist() != ["month.json"] or archive.getinfo("month.json").file_size > 256*1024*1024:
            raise ValueError("Archive mensuelle non reconnue. Choisissez un SQL global compatible ou une archive mensuelle SME.")
        if archive.testzip() is not None: raise ValueError("Archive mensuelle endommagée.")
        payload = json.loads(archive.read("month.json").decode("utf-8"))
    operation.finish('read')
    operation.start('analyse')
    if not isinstance(payload,dict) or payload.get("format") != FORMAT or not isinstance(payload.get("tables"),dict):
        raise ValueError("Format d'archive mensuelle inconnu.")
    tables = payload["tables"]
    # Le schéma annoncé est relu depuis sa définition, pas accepté comme code.
    for index, (name,table) in enumerate(tables.items(), 1):
        if not isinstance(table,dict): raise ValueError("Table d'archive invalide.")
        parsed_name,definition = schema_from_ddl(table["ddl"])
        if parsed_name != name or any(definition[key] != table[key] for key in ("columns","primary","auto","unique","types")):
            raise ValueError("Schéma mensuel incohérent.")
        if json.dumps(definition["foreign"]) != json.dumps(table["foreign"]): raise ValueError("Clés étrangères incohérentes.")
        operation.update('analyse', current=index, total=len(tables) + 1, detail=f'{index}/{len(tables)} schéma(s) vérifié(s) ; {table_label(name)}')
    if dataset_token(tables) != payload.get("sha256"): raise ValueError("Empreinte d'archive incorrecte.")
    selected = extract_month(tables,payload["month"])
    if dataset_token(selected) != dataset_token(tables): raise ValueError("L'archive contient des données hors du mois annoncé.")
    operation.finish('analyse')
    return tables


def write_month(path, tables, month):
    with operation_progress((('scope', 'Isolation et contrôle du mois'),
                             ('write', 'Écriture de l’archive mensuelle'),
                             ('publish', 'Enregistrement du fichier'))) as operation:
        return _write_month_with_progress(path, tables, month, operation)


def _write_month_with_progress(path, tables, month, operation):
    from sme_gend.services.safe_paths import atomic_destination
    operation.start('scope')
    if Path(path).suffix.lower() not in (".zip",".sql"):
        raise ValueError("Choisissez une extension .zip ou .sql pour l'archive mensuelle.")
    tables = extract_month(tables,month)
    operation.finish('scope', detail=f'Mois {month} ; {len(tables)} table(s)')
    total_rows = sum(len(table['rows']) for table in tables.values())
    operation.start('write', total=total_rows)
    written = 0
    with atomic_destination(Path(path),replace_existing=False) as temporary:
        if Path(path).suffix.lower() == ".sql":
            with temporary.open("w",encoding="utf-8",newline="\n") as stream:
                stream.write("-- Archive mensuelle SME : importer depuis Gestion des mois et archives, ne pas exécuter directement.\nSET NAMES utf8mb4;\n")
                for name,table in tables.items():
                    stream.write(table["ddl"]+";\n")
                    columns = ",".join(quote(c) for c in table["columns"])
                    for row in table["rows"]:
                        stream.write(f"INSERT INTO {quote(name)} ({columns}) VALUES ("+
                            ",".join(_literal(sql_value(row[c])) for c in table["columns"])+");\n")
                        written += 1
                        if written % 250 == 0 or written == total_rows:
                            operation.update('write', current=written, total=total_rows,
                                detail=f'{written:,}/{total_rows:,} ligne(s) ; {table_label(name)}')
        else:
            payload = dict(format=FORMAT,month=month,tables=tables,sha256=dataset_token(tables),created_at=datetime.now(timezone.utc).isoformat())
            with zipfile.ZipFile(temporary,"w",zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("month.json",json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        operation.finish('write', detail=f'{total_rows:,} ligne(s) écrite(s)')
        operation.start('publish', detail='Fermeture et enregistrement atomique du fichier')
    operation.finish('publish')
    return Path(path)


def read_database(settings):
    """Instantané cohérent des tables métier seulement ; aucun compte/config exporté."""
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    if has_import_transaction(settings): raise RuntimeError("Lecture d'archive interdite pendant une écriture en cours.")
    with MySQLConnection(settings).connect(read_only=True) as connection:
        connection.rollback()
        try:
            with connection.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                return _read_connection(connection)
        finally:
            connection.rollback()


def _read_connection(connection, *, lock=False):
    with operation_progress((('schema', 'Lecture du schéma métier'),
                             ('tables', 'Lecture des tables métier'),
                             ('verify', 'Contrôle des données et des dépendances'))) as operation:
        return _read_connection_with_progress(connection, lock=lock, operation=operation)


def _read_connection_with_progress(connection, *, lock, operation):
    operation.start('schema')
    with connection.cursor() as cur:
        if lock:
            cur.execute("SELECT @@SESSION.sql_mode")
            modes = str(cur.fetchone()[0]).upper().split(",")
            if not {"STRICT_ALL_TABLES","STRICT_TRANS_TABLES"}.intersection(modes):
                raise ValueError("La restauration mensuelle exige un mode SQL strict pour refuser les valeurs invalides.")
        cur.execute("SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION")
        columns = {}
        for table,column in cur.fetchall(): columns.setdefault(table,[]).append(column)
        allowed = {name for name,cols in columns.items() if name not in EXCLUDED and
            (name in ("sme_periode","agent","etl_erreur") or name.startswith("ref_") or name in DATED
             or "sme_periode_id" in cols or any(c in cols for c in MONTH_COLUMNS))}
        if "agent_identity_alias" in columns:
            allowed.add("agent_identity_alias")  # lecture de contrôle, jamais exporté ni restauré
        operation.finish('schema', detail=f'{len(allowed)} table(s) à lire')
        operation.start('tables', total=len(allowed))
        tables = {}
        for index, name in enumerate(sorted(allowed), 1):
            operation.update('tables', current=index - 1, total=len(allowed), detail=f'{table_label(name)} : lecture des lignes')
            quote(name)
            cur.execute(f"SHOW CREATE TABLE {quote(name)}")
            found = cur.fetchone()
            if not found: raise ValueError("Table métier inaccessible.")
            _,table = schema_from_ddl(found[1])
            selected = ",".join(quote(c) for c in table["columns"])
            cur.execute(f"SELECT {selected} FROM {quote(name)}" + (" FOR UPDATE" if lock else ""))
            table["rows"] = [dict(zip(table["columns"],map(portable,row))) for row in cur.fetchall()]
            tables[name] = table
            operation.update('tables', current=index, total=len(allowed), detail=f'{index}/{len(allowed)} table(s) ; {len(table["rows"]):,} ligne(s) lue(s)')
        operation.finish('tables')
        operation.start('verify')
        cur.execute("SELECT TABLE_NAME,REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL")
        owned = scope_tables(tables)
        for child,parent in cur.fetchall():
            if parent in owned and child not in allowed:
                raise ValueError(f"La table {child} dépend des données mensuelles sans périmètre pris en charge.")
        cur.execute("SELECT EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()")
        if any(row[0] in allowed for row in cur.fetchall()):
            raise ValueError("Des déclencheurs SQL métier pourraient modifier d’autres mois : restauration mensuelle spécialisée requise.")
    validate_dataset(tables)
    operation.finish('verify')
    return tables


@dataclass(frozen=True)
class MonthTransferPlan:
    month: str
    source: dict
    source_token: str
    target_token: str
    owner: tuple
    conflicts: tuple[str,...]
    existing_counts: tuple[tuple[str,int],...]


def _key(table,row):
    return tuple(row[c] for c in table["primary"])


def _target_rows(tables, month):
    if month not in months_in(tables):
        return {name:{**table,"rows":[]} for name,table in tables.items() if name in scope_tables(tables) or name == "sme_periode"}
    return extract_month(tables,month)


def build_plan(source, live, month, owner):
    with operation_progress((('scope', 'Isolation des données du mois source et cible'),
                             ('compare', 'Comparaison des schémas et des identités'),
                             ('dependencies', 'Contrôle des références des autres mois'),
                             ('fingerprint', 'Calcul des empreintes de l’aperçu'))) as operation:
        return _build_plan_with_progress(source, live, month, owner, operation)


def _build_plan_with_progress(source, live, month, owner, operation):
    operation.start('scope')
    month = normalize_import_mois(month)
    selected = extract_month(source, month)
    target = _target_rows(live,month)
    conflicts = []
    owned = scope_tables(selected)
    missing_scopes = scope_tables(live)-owned
    if missing_scopes:
        populated = [name for name in missing_scopes if target.get(name,{}).get("rows")]
        if populated: conflicts.append("Tables mensuelles contenant des données à remplacer mais absentes de la source : " + ", ".join(sorted(populated)))
    operation.finish('scope')
    operation.start('compare', total=len(selected))
    for table_index, (name,table) in enumerate(selected.items()):
        operation.update('compare', current=table_index, total=len(selected), detail=table_label(name))
        if name not in live:
            conflicts.append(f"Table absente de la cible : {name}."); continue
        current = live[name]
        if (table["columns"] != current["columns"] or table["primary"] != current["primary"] or table["types"] != current["types"] or table["auto"] != current["auto"]
            or table["unique"] != current["unique"] or json.dumps(table["foreign"]) != json.dumps(current["foreign"])):
            conflicts.append(f"Schéma incompatible : {name}."); continue
        if name in owned:
            if table["primary"] != [table["auto"]]:
                conflicts.append(f"La table mensuelle {name} ne possède pas une clé technique remappable.")
            continue
        if name == "sme_periode": continue
        lookup = {_key(current,row):row for row in current["rows"]}
        for row_index, row in enumerate(table["rows"], 1):
            if row_index % 100 == 0:
                operation.update('compare', current=table_index + (row_index - 1) / max(1, len(table['rows'])), total=len(selected),
                    detail=f'{table_label(name)} : {row_index - 1:,}/{len(table["rows"]):,} ligne(s) comparée(s)')
            key = _key(table,row)
            existing = lookup.get(key)
            differences = [c for c in table["columns"] if c not in TECHNICAL and existing and existing[c] != row[c]]
            if existing and differences:
                label = row.get("noms") or row.get("libelle") or row.get("designation") or ""
                fields = {"noms":"nom complet", "date_naissance":"date de naissance", "matricule_militaire":"matricule militaire", "matricule_solde":"matricule solde"}
                changed = ", ".join(fields.get(c,c.replace("_"," ")) for c in differences)
                identifier = ", ".join(map(str,key))
                conflicts.append(f"{table_label(name)} — identifiant {identifier} {label} : données partagées différentes ({changed}) ; aucun écrasement autorisé.")
                for column in differences[:5]:
                    field = fields.get(column,column.replace("_"," "))
                    conflicts.append(f"  {field} : source « {str(row[column])[:160]} » ; base actuelle « {str(existing[column])[:160]} ».")
            for cols in table["unique"]:
                def values(r):
                    result = []
                    for spec in cols:
                        c,_,length = spec.partition(":")
                        value = str(r[c]).strip().casefold() if r[c] is not None else None
                        result.append(value[:int(length)] if value is not None and length else value)
                    return tuple(result)
                unique_key = values(row)
                if None in unique_key: continue
                if any(_key(current,r) != key and values(r) == unique_key for r in current["rows"]):
                    conflicts.append(f"{name} {key} : clé unique {', '.join(cols)} déjà utilisée par un autre identifiant.")
            if name == "agent":
                from sme_gend.services.personnel_identity_assessment import identity_value
                aliases = live.get("agent_identity_alias",{}).get("rows",[])
                if any(str(alias.get("alias_legacy_id")) == str(row.get("legacy_access_id")) for alias in aliases):
                    conflicts.append(f"Agent {row.get('noms') or key} : cet ancien identifiant a été rapproché d'une autre fiche. La restauration ne recrée pas une identité fusionnée.")
                for other in current["rows"]:
                    if _key(current,other) == key: continue
                    for field in ("legacy_access_id","matricule_militaire","matricule_solde"):
                        normalize = (lambda v:identity_value(field,v)) if field.startswith("matricule") else (lambda v:str(v or "").strip().casefold())
                        value = normalize(row.get(field))
                        if value and value == normalize(other.get(field)):
                            conflicts.append(f"Agent {row.get('noms') or key} : {field} déjà porté par la fiche {other.get('id')}. Rapprochement manuel requis.")
    operation.finish('compare', detail=f'{len(selected)} table(s) comparée(s)')
    operation.start('dependencies', total=len(live))
    # Toute dépendance externe à supprimer, non incluse dans le mois, interdit le remplacement.
    for index, (name,table) in enumerate(live.items(), 1):
        for cols,parent,pcols in table["foreign"]:
            removed = target.get(parent,{}).get("rows",[]) if parent in scope_tables(live) else []
            removed_keys = {tuple(r[c] for c in pcols) for r in removed}
            kept = [r for r in table["rows"] if r not in target.get(name,{}).get("rows",[])]
            if any(tuple(r[c] for c in cols) in removed_keys for r in kept):
                conflicts.append(f"{name} : une ligne hors du mois référence des données à remplacer ({parent}).")
        operation.update('dependencies', current=index, total=len(live), detail=table_label(name))
    counts = tuple((name,len(table["rows"])) for name,table in sorted(target.items()) if name in scope_tables(live))
    try: _ordered_tables(selected)
    except ValueError as error: conflicts.append(str(error))
    operation.finish('dependencies')
    operation.start('fingerprint')
    result = MonthTransferPlan(month,selected,dataset_token(selected),dataset_token(live),owner,tuple(dict.fromkeys(conflicts)),counts)
    operation.finish('fingerprint', detail=f'{len(result.conflicts)} conflit(s) détecté(s)')
    return result


def preview_month(settings, source, month):
    from sme_gend.repositories.mysql_connection import mysql_database_key
    _authorize(write=True)
    with operation_progress((('read', 'Lecture de la base cible'),
                             ('compare', 'Comparaison des identités et des données du mois'))) as operation:
        operation.start('read')
        live = read_database(settings)
        operation.finish('read')
        operation.start('compare')
        result = build_plan(source,live,month,mysql_database_key(settings))
        operation.finish('compare', detail=f'{len(result.conflicts)} conflit(s) détecté(s)')
        return result


def _authorize(*,write):
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    from sme_gend.services.workflow_permissions import session_can_perform_action
    if not session_has_permission("system.archives"):
        raise PermissionError("Consultation des archives non autorisée.")
    if write and (not session_has_admin_role() or not session_can_perform_action("workflow.import_archive")):
        raise PermissionError("La restauration mensuelle exige un compte administrateur autorisé à importer.")


def export_month(settings,month,path):
    _authorize(write=False)
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("system.backup"): raise PermissionError("Export de sauvegarde non autorisé.")
    with operation_progress((('read', 'Lecture cohérente de la base'),
                             ('write', 'Création et enregistrement de l’archive du mois'))) as operation:
        operation.start('read')
        tables = read_database(settings)
        operation.finish('read')
        operation.start('write')
        result = write_month(path,tables,normalize_import_mois(month))
        operation.finish('write')
        return result


def preview_export(settings,month):
    _authorize(write=False)
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("system.backup"): raise PermissionError("Export de sauvegarde non autorisé.")
    with operation_progress((('read', 'Lecture cohérente des données métier'),
                             ('scope', 'Isolation et contrôle du mois sélectionné'))) as operation:
        operation.start('read')
        tables = read_database(settings)
        operation.finish('read')
        operation.start('scope')
        result = extract_month(tables,month)
        operation.finish('scope', detail=f'{sum(len(table["rows"]) for table in result.values()):,} ligne(s) dans le mois')
        return result


def _ordered_tables(tables):
    pending, ordered = set(tables), []
    while pending:
        ready = sorted(name for name in pending if not ({fk[1] for fk in tables[name]["foreign"]}-{name}) & pending)
        if not ready: raise ValueError("Cycle de dépendances entre tables : opération refusée.")
        ordered.extend(ready); pending.difference_update(ready)
    return ordered


def apply_month(settings, plan):
    """Sauvegarde, nouveau contrôle sous verrou commun, puis DML transactionnel."""
    with operation_progress((('plan', 'Vérification du contexte et de la source'),
                             ('safety', 'Sauvegarde de sécurité'),
                             ('verify', 'Nouveau contrôle sous verrou'),
                             ('delete', 'Retrait des anciennes lignes du mois'),
                             ('restore', 'Restauration des lignes et des références'),
                             ('commit', 'Journalisation et validation de la transaction'))) as operation:
        return _apply_month_with_progress(settings, plan, operation)


def _apply_month_with_progress(settings, plan, operation):
    operation.start('plan')
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.shared_reference_write import mark_shared_data_write
    from sme_gend.services.backup_restore_service import ensure_recent_backup
    from sme_gend.services.business_audit import record_business_action
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    _authorize(write=True)
    if not isinstance(plan,MonthTransferPlan) or plan.owner != mysql_database_key(settings):
        raise ValueError("Le contexte de restauration a changé. Recommencez l'aperçu.")
    if plan.conflicts or dataset_token(plan.source) != plan.source_token:
        raise ValueError("Conflits ou archive modifiée : restauration refusée.")
    operation.finish('plan')
    operation.start('safety')
    backup = ensure_recent_backup(settings,max_age_days=0.0)
    if backup is None: raise RuntimeError("Restauration annulée : sauvegarde préalable impossible.")
    operation.finish('safety')
    operation.start('verify', detail='Lecture et comparaison de la cible dans la transaction')
    _authorize(write=True)
    with period_state_write(settings,reason="month_restore",mois=plan.month) as change:
        with MySQLConnection(settings).connect() as connection:
            live = _read_connection(connection,lock=True)
            current = build_plan(plan.source,live,plan.month,plan.owner)
            if current.target_token != plan.target_token or current.conflicts:
                raise ReferenceConflictError("Les données ont changé depuis l'aperçu. Aucune donnée du mois n'a été remplacée ; relancez le contrôle.")
            target = _target_rows(live,plan.month)
            ordered = _ordered_tables(plan.source)
            owned = scope_tables(plan.source)
            operation.finish('verify')
            deleted = 0
            delete_total = sum(len(table.get('rows', [])) for name, table in target.items() if name in owned)
            operation.start('delete', total=delete_total)
            maps = {}
            with connection.cursor() as cur:
                for name in reversed(_ordered_tables(live)):
                    if name not in owned: continue
                    table = live[name]
                    predicate = " AND ".join(f"{quote(c)}=%s" for c in table["primary"])
                    for row in target.get(name,{}).get("rows",[]):
                        cur.execute(f"DELETE FROM {quote(name)} WHERE {predicate}",_key(table,row))
                        deleted += 1
                        if deleted % 100 == 0 or deleted == delete_total:
                            operation.update('delete', current=deleted, total=delete_total, detail=f'{deleted:,}/{delete_total:,} ligne(s) ; {table_label(name)}')
                operation.finish('delete')
                operation.start('restore', total=len(ordered))
                for table_index, name in enumerate(ordered):
                    table = plan.source[name]
                    maps[name] = {}
                    existing = {_key(live[name],r):r for r in live[name]["rows"]}
                    rows = list(table["rows"])
                    # Les référentiels hiérarchiques sont triés par dépendance de ligne.
                    while rows:
                        progress = False
                        for original in list(rows):
                            row = dict(original)
                            if any(parent == name and row[cols[0]] is not None and (row[cols[0]],) not in maps[name]
                                   for cols,parent,pcols in table["foreign"]): continue
                            for cols,parent,pcols in table["foreign"]:
                                key = tuple(row[c] for c in cols)
                                if any(v is None for v in key): continue
                                if len(cols) == 1 and pcols == plan.source[parent]["primary"]:
                                    mapped = maps[parent].get(key)
                                    if mapped is None: raise ValueError("Dépendance non préparée ; restauration annulée.")
                                    row[cols[0]] = mapped[0]
                            key = _key(table,original)
                            if name == "sme_periode":
                                found = [r for r in live[name]["rows"] if normalize_import_mois(r["annee_mois"]) == plan.month]
                                if found:
                                    maps[name][key] = _key(table,found[0]); rows.remove(original); progress=True; continue
                                row["statut"] = "Fermé"
                            elif name not in owned and key in existing:
                                maps[name][key] = key; rows.remove(original); progress=True; continue
                            columns = [c for c in table["columns"] if not (name in owned or name == "sme_periode") or c != table["auto"]]
                            if name == "carriere_situation":
                                row["est_actuelle"] = int(plan.month == change.before.active_month)
                                from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
                                row["date_debut"] = str(first_day_of_month(plan.month))
                                row["date_fin"] = None if row["est_actuelle"] else str(last_day_of_month(plan.month))
                            cur.execute(f"INSERT INTO {quote(name)} ("+",".join(quote(c) for c in columns)+") VALUES ("+",".join(["%s"]*len(columns))+")",
                                        tuple(sql_value(row[c]) for c in columns))
                            maps[name][key] = (int(cur.lastrowid),) if (name in owned or name == "sme_periode") else key
                            rows.remove(original); progress=True
                            done = len(table['rows']) - len(rows)
                            if done % 100 == 0:
                                operation.update('restore', current=table_index + done / max(1, len(table['rows'])), total=len(ordered),
                                    detail=f'{table_label(name)} : {done:,}/{len(table["rows"]):,} ligne(s) traitée(s)')
                        if not progress: raise ValueError("Dépendance circulaire entre lignes : restauration annulée.")
                    operation.update('restore', current=table_index + 1, total=len(ordered),
                        detail=f'{table_label(name)} : {len(table["rows"]):,} ligne(s) traitée(s)')
            operation.finish('restore')
            operation.start('commit', detail='Enregistrement de l’audit et attente de la confirmation MySQL')
            mark_shared_data_write(True)
            record_business_action(settings,action="sme_month_restore",module="workflow",entity_id=plan.month,
                mois=plan.month,before=dict(counts=plan.existing_counts),
                after=dict(source_sha256=plan.source_token,backup=str(backup),counts={n:len(t['rows']) for n,t in plan.source.items()}))
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    invalidate_liste_cache(settings)
    from sme_gend.services.operation_commit_progress import finish_after_commit
    finish_after_commit(settings, operation, 'commit', detail='Mois restauré et transaction confirmée')
    return dict(month=plan.month,backup=str(backup),counts={n:len(t["rows"]) for n,t in plan.source.items()})
