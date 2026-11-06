"""Reproducible release checks against synthetic profiles and a disposable MySQL.

No production settings are loaded. Logs and the disposable server stay in build/.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
UNIT_FILES = [
    "test_centralized_operations.py",
    "test_corrections_suite.py", "test_sessions_month_drafts_regressions.py",
    "test_sensitive_audit_search.py", "test_other_journal_filters.py",
    "test_personnel_dossier_open.py", "test_portable_template_safety.py",
    "test_shared_config_conflicts.py", "test_shared_media_and_sync.py",
    "test_shared_sync_regressions.py", "test_docx_exports_recipe.py",
    "test_parcours_pdf_export.py", "test_export_integrity_regressions.py",
    "test_numerique_export_workbook.py", "test_backup_restore.py",
    "test_restore_safety.py", "test_prepared_restore.py",
]
MYSQL_FILES = [
    "test_concurrent_clients_mysql.py",
    "test_centralized_operations_mysql.py",
    "test_journal_paging_mysql.py", "test_sessions_presence_journal_mysql.py",
    "test_sensitive_audit_mysql.py", "test_dossier_month_purge_mysql.py",
]


def source_fingerprint():
    digest = hashlib.sha256()
    for path in sorted((PROJECT / "src").rglob("*.py")):
        digest.update(path.relative_to(PROJECT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def stop_disposable_mysql(metadata):
    """MySQL on Windows can have a monitor child: request server shutdown."""
    import pymysql
    info = json.loads(Path(metadata).read_text(encoding='utf-8-sig'))
    data = Path(info['datadir']).resolve()
    port = int(info['port'])
    if port == 3306 or not data.is_relative_to((PROJECT / 'build').resolve()):
        raise RuntimeError('Arrêt refusé : instance non jetable.')
    connection = pymysql.connect(host='127.0.0.1', port=port, user='root', password='', connect_timeout=3)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT @@datadir')
            if Path(cursor.fetchone()[0]).resolve() != data:
                raise RuntimeError('Arrêt refusé : répertoire du serveur différent.')
            cursor.execute('SHUTDOWN')
    finally:
        connection.close()


@contextmanager
def disposable_mysql(root, executable):
    executable = executable.resolve(strict=True)
    server_root = root / "mysql"
    server_root.mkdir(mode=0o777)
    data = server_root / "data"
    log_path = server_root / "server.log"
    base = [str(executable), "--no-defaults", "--basedir=" + str(executable.parent.parent),
            "--datadir=" + str(data)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("wb") as log:
        subprocess.run(base + ["--initialize-insecure"], stdout=log, stderr=log,
                       check=True, timeout=90, creationflags=flags)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port == 3306:
            raise RuntimeError("Port de production interdit.")
        server = subprocess.Popen(base + ["--bind-address=127.0.0.1", "--port=" + str(port),
                                         "--mysqlx=OFF", "--skip-log-bin"],
                                  stdout=log, stderr=log, creationflags=flags)
        try:
            import pymysql
            deadline = time.monotonic() + 60
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Le serveur jetable s'est arrêté ; consulter " + str(log_path))
                try:
                    connection = pymysql.connect(host="127.0.0.1", port=port, user="root",
                                                 password="", connect_timeout=2)
                    break
                except pymysql.Error:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.5)
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT @@datadir")
                    if Path(cursor.fetchone()[0]).resolve() != data.resolve():
                        raise RuntimeError("Répertoire du serveur inattendu.")
            metadata = server_root / "metadata.json"
            metadata.write_text(json.dumps(dict(port=port, datadir=str(data.resolve()), pid=server.pid)), encoding="utf-8")
            yield metadata
        finally:
            # Only this Popen child is stopped; never a service or an existing server.
            metadata = server_root / 'metadata.json'
            if metadata.is_file():
                stop_disposable_mysql(metadata)
                server.wait(timeout=20)
            elif server.poll() is None:
                server.terminate()
                server.wait(timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysql-server", type=Path)
    parser.add_argument("--inherited-test-acls", action="store_true",
                        help="Synthetic temporary dirs only; does not validate Windows protections")
    parser.add_argument("--mysql-only", action="store_true")
    parser.add_argument("--mysql-test-file", action="append", choices=MYSQL_FILES,
                        help="Repeat to select native modules; defaults to the full release set")
    args = parser.parse_args()
    root = PROJECT / "build" / ("release_checks_" + uuid4().hex)
    root.mkdir(mode=0o777)
    print("Rapports :", root, flush=True)
    results = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
               "source_sha256": source_fingerprint(),
               "inherited_test_acls": args.inherited_test_acls, "runs": []}
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SME_", "SMEDB_", "IMPRESSION_MYSQL_TEST"))}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")

    def run(name, paths, metadata=None, extra=()):
        command = [sys.executable, "-u", str(PROJECT / "scripts/run_isolated_tests.py")]
        if args.inherited_test_acls:
            command.append("--inherited-test-acls")
        if metadata:
            command += ["--mysql-test-instance", str(metadata)]
        command += [*paths, *extra, "-q", "--tb=short"]
        output = root / (name + ".txt")
        print("Validation :", name, flush=True)
        with output.open("w", encoding="utf-8") as stream:
            try:
                code = subprocess.run(command, cwd=PROJECT, env=env, stdout=stream,
                                      stderr=subprocess.STDOUT, timeout=240).returncode
            except subprocess.TimeoutExpired:
                code = 124
        text = output.read_text(encoding="utf-8")
        print(text[-2500:], flush=True)
        row = dict(name=name, exit_code=code, output=str(output.relative_to(PROJECT)))
        profile_line = next((line for line in text.splitlines() if line.startswith("Profil de test : ")), "")
        if profile_line:
            xml = Path(profile_line.split(" : ", 1)[1]) / "results.xml"
            if xml.is_file() and xml.resolve().is_relative_to((PROJECT / "build").resolve()):
                suites = ET.parse(xml).getroot()
                row["junit_xml"] = str(xml.relative_to(PROJECT))
                row.update({key: sum(int(s.get(key, 0)) for s in suites.iter("testsuite"))
                            for key in ("tests", "failures", "errors", "skipped")})
        results["runs"].append(row)
        (root / "validation.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        return code

    if not args.mysql_only:
        run("unit", ["tests/" + name for name in UNIT_FILES if (PROJECT / "tests" / name).exists()])
    if args.mysql_server:
        with disposable_mysql(root, args.mysql_server) as metadata:
            run("mysql", ["tests/integration/" + name for name in (args.mysql_test_file or MYSQL_FILES)], metadata)
            run("restore", ["tests/integration/test_restore_mysql.py"], metadata,
                ["-k", "dump_restore_retains_multiline_unicode_and_binary or dml_failure_rolls_back_and_does_not_return_connection_to_pool or shipped_schema_initializes_only_configured_database or two_writers_cannot_overwrite_same_snapshot"])
        results["disposable_server_stopped"] = True
    results["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    results["sources_unchanged"] = results["source_sha256"] == source_fingerprint()
    results["ok"] = (bool(results["runs"]) and results["sources_unchanged"]
                     and all(row["exit_code"] == 0 for row in results["runs"]))
    (root / "validation.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
