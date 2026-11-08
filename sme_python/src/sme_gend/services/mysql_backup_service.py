"""Consistent closure backups, published only after a complete dump."""
from __future__ import annotations
import math
import logging
import os
import shutil
import subprocess
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction, schema_metadata_lock
from sme_gend.services.safe_paths import atomic_destination, file_key
from sme_gend.services.mysql_private_options import private_mysql_options

_log = logging.getLogger(__name__)

def _find_mysqldump() -> str | None:
    explicit = (os.environ.get("SME_MYSQLDUMP_PATH") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("mysqldump")
    if found:
        return found
    for root in (
        Path(r"C:\Program Files\MySQL"),
        Path(r"C:\Program Files\MariaDB"),
        Path(r"C:\xampp\mysql\bin"),
    ):
        if not root.is_dir():
            continue
        if root.name == "bin" and (root / "mysqldump.exe").is_file():
            return str(root / "mysqldump.exe")
        for exe in root.glob("**/mysqldump.exe"):
            return str(exe)
    return None



def _identifier(value):
    return "`" + str(value).replace("`","``") + "`"


def _literal(value):
    if value is None: return "NULL"
    if isinstance(value,bool): return "1" if value else "0"
    if isinstance(value,(int,float,Decimal)):
        if isinstance(value,float) and not math.isfinite(value) or isinstance(value,Decimal) and not value.is_finite():
            raise ValueError("Valeur numérique SQL non représentable.")
        return str(value)
    if isinstance(value,(bytes,bytearray,memoryview)):
        return "0x"+bytes(value).hex() if value else "''"
    if isinstance(value,(date,datetime,time)):
        return "'" + str(value) + "'"
    text = str(value)
    return "_utf8mb4 0x"+text.encode("utf-8").hex() if text else "''"


def _python_sql_dump(settings: Settings, dest: Path, *, selected_tables: list[str] | None = None,
                     snapshot_reader=None) -> dict[str, int]:
    if has_import_transaction(settings):
        raise RuntimeError("La sauvegarde doit précéder la transaction d'écriture.")
    with schema_metadata_lock(settings):
        return _python_sql_dump_locked(settings,dest,selected_tables=selected_tables,snapshot_reader=snapshot_reader)


def _python_sql_dump_locked(settings: Settings, dest: Path, *, selected_tables=None,snapshot_reader=None):
    """One InnoDB snapshot; stream rows rather than retaining the whole dump."""
    if has_import_transaction(settings):
        raise RuntimeError("La sauvegarde doit précéder la transaction d'écriture.")
    from pymysql.cursors import SSCursor
    from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
    with MySQLConnection(settings).connect(read_only=True) as conn:
        if conn.server_status & SERVER_STATUS_IN_TRANS:
            raise RuntimeError("Transaction préexistante : sauvegarde annulée.")
        with conn.cursor() as cur:
            cur.execute("SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME")
            tables = cur.fetchall()
            all_tables = tuple(str(row[0]) for row in tables)
            from sme_gend.services.database_maintenance import INTERNAL_TABLES
            tables = [row for row in tables if row[0] not in INTERNAL_TABLES]
            if selected_tables:
                wanted = {name.strip() for name in selected_tables if name.strip()}
                missing = wanted - {str(table[0]) for table in tables}
                if missing: raise ValueError("Table demandée absente : sauvegarde annulée.")
                tables = [table for table in tables if table[0] in wanted]
            if any(str(kind).upper() != "BASE TABLE" or str(engine).upper() != "INNODB" for _,engine,kind in tables):
                raise RuntimeError("Le repli interne nécessite uniquement des tables InnoDB ; utilisez le client natif pour les autres objets.")
            for table,column in (("ROUTINES","ROUTINE_SCHEMA"),("TRIGGERS","TRIGGER_SCHEMA"),("EVENTS","EVENT_SCHEMA")):
                cur.execute(f"SELECT COUNT(*) FROM information_schema.{table} WHERE {column}=DATABASE()")
                if cur.fetchone()[0]:
                    raise RuntimeError("Le client natif est requis pour conserver routines, déclencheurs ou événements.")
            cur.execute("SELECT @@SESSION.sql_mode")
            original_mode = cur.fetchone()[0]
        # Metadata reads can open a read transaction. This connection was
        # checked above and belongs exclusively to the dump.
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO'")
        counts = {}
        failed = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            if snapshot_reader is not None:
                snapshot_reader(conn, all_tables)
            with Path(dest).open("w",encoding="utf-8",newline="\n") as file:
                file.write("-- SME closure backup (consistent InnoDB snapshot)\nSET NAMES utf8mb4;\nSET SESSION SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';\nSET FOREIGN_KEY_CHECKS=0;\n")
                for table,engine,kind in tables:
                    quoted = _identifier(table)
                    counts[table] = 0
                    with conn.cursor() as cur:
                        cur.execute(f"SHOW CREATE TABLE {quoted}")
                        ddl = cur.fetchone()
                    if not ddl:
                        raise RuntimeError("Schéma incomplet pendant la sauvegarde.")
                    file.write(f"DROP TABLE IF EXISTS {quoted};\n{ddl[1]};\n")
                    with conn.cursor(SSCursor) as rows:
                        rows.execute(f"SELECT * FROM {quoted}")
                        columns = ",".join(_identifier(column[0]) for column in rows.description)
                        for row in rows:
                            counts[table] += 1
                            file.write(f"INSERT INTO {quoted} ({columns}) VALUES ("+",".join(_literal(value) for value in row)+");\n")
                file.write("SET FOREIGN_KEY_CHECKS=1;\n-- SME dump complete\n")
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if getattr(conn, 'open', None) is False:
                    raise RuntimeError('Connexion MySQL perdue pendant la sauvegarde.')
                conn.rollback()  # Read snapshot only; never validate a caller's write.
                with conn.cursor() as cur:
                    cur.execute("SET SESSION sql_mode=%s",(original_mode,))
            except BaseException:
                if not failed:
                    raise
                _log.warning('Nettoyage de la connexion de sauvegarde impossible ; erreur initiale conservée.')
    return counts


def _check_native_snapshot(settings):
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_TYPE='BASE TABLE' AND (ENGINE IS NULL OR ENGINE<>'InnoDB')")
            if cur.fetchone()[0]:
                raise RuntimeError("Une sauvegarde transactionnelle exige des tables InnoDB.")


def backup_mysql_database(settings: Settings, dest_dir: Path, slug: str) -> tuple[Path | None,list[str]]:
    if has_import_transaction(settings):
        return None,["La sauvegarde doit être créée avant toute transaction d'écriture."]
    try:
        with schema_metadata_lock(settings):
            return _backup_mysql_database_locked(settings,dest_dir,slug)
    except Exception:
        return None,["Sauvegarde non créée : une migration ou une sauvegarde utilise le schéma, ou la connexion est indisponible."]


def _backup_mysql_database_locked(settings: Settings, dest_dir: Path, slug: str):
    warnings = []
    if has_import_transaction(settings):
        return None,["La sauvegarde doit être créée avant toute transaction d'écriture."]
    try:
        slug = file_key(slug)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True,exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = dest_dir / f"mysql_smegend_{slug}_{stamp}_{uuid4().hex[:12]}.sql"
        exe = _find_mysqldump()
        with atomic_destination(dest,replace_existing=False) as temporary:
            native_ok = False
            if exe:
                try:
                    _check_native_snapshot(settings)
                    with private_mysql_options(settings) as (options,env):
                        command = [exe,f"--defaults-file={options}","--single-transaction","--quick",
                            "--routines","--events","--hex-blob","--no-tablespaces",
                            "--set-gtid-purged=OFF","--column-statistics=0",
                            f"--ignore-table={settings.mysql_database}._sme_maintenance_state","--",settings.mysql_database]
                        command.insert(-2, f"--ignore-table={settings.mysql_database}._sme_active_writer")
                        with temporary.open("wb") as output:
                            result = subprocess.run(command,stdout=output,stderr=subprocess.PIPE,check=False,
                                timeout=300,env=env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                        native_ok = result.returncode == 0 and temporary.stat().st_size > 0
                        if not native_ok:
                            warnings.append(f"Le client de sauvegarde MySQL a échoué (code {result.returncode}). Repli interne tenté.")
                except subprocess.TimeoutExpired:
                    warnings.append("Délai de sauvegarde MySQL dépassé (300 s). Repli interne tenté.")
                except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
                    warnings.append("Exécution sécurisée du client MySQL impossible. Repli interne tenté.")
            else:
                warnings.append("Client mysqldump introuvable : sauvegarde interne transactionnelle.")
            if not native_ok:
                try:
                    _python_sql_dump(settings,temporary)
                except Exception:
                    warnings.append("Sauvegarde interne échouée : vérifiez la connexion, les droits de lecture et les objets SQL pris en charge.")
                    raise
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("La sauvegarde produite est vide.")
        return dest,warnings
    except Exception:
        if not warnings:
            warnings.append("Sauvegarde non créée : vérifiez le nom, le dossier, les droits et l'espace disponible.")
        return None,warnings
