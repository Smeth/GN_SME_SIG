"""pytest sans données réelles ; MySQL optionnel limité au serveur jetable vérifié."""
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    project = Path(__file__).resolve().parents[1]
    arguments = sys.argv[1:]
    shard = None
    if '--test-shard' in arguments:
        index = arguments.index('--test-shard')
        shard = tuple(int(part) for part in arguments[index + 1].split('/'))
        if len(shard) != 2 or not 0 <= shard[0] < shard[1]:
            raise ValueError('Lot de test invalide : utiliser index/nombre, à partir de zéro.')
        del arguments[index:index + 2]
    emulate_acls = '--emulate-test-acls' in arguments
    native_dump = '--allow-native-mysqldump' in arguments
    for flag in ('--emulate-test-acls', '--allow-native-mysqldump'):
        if flag in arguments:
            arguments.remove(flag)
    if "--test-dependencies" in arguments:
        index = arguments.index("--test-dependencies")
        dependency_dir = Path(arguments[index + 1]).resolve(strict=True)
        if not dependency_dir.is_dir():
            raise ValueError("Le chemin des dépendances de test doit être un dossier.")
        sys.path.insert(0, str(dependency_dir))
        del arguments[index:index + 2]
    inherited_acls = "--inherited-test-acls" in arguments
    if inherited_acls:
        arguments.remove("--inherited-test-acls")
    metadata_path = None
    if "--mysql-test-instance" in arguments:
        index = arguments.index("--mysql-test-instance")
        metadata_path = Path(arguments[index + 1]).resolve()
        os.environ["IMPRESSION_MYSQL_TEST_METADATA"] = str(metadata_path)
        del arguments[index:index + 2]
    os.chdir(project)
    (project / "build").mkdir(exist_ok=True)
    root = (project / "build" / ("isolated_tests_" + uuid4().hex)).resolve()
    root.mkdir(mode=0o777)
    print("Profil de test :", root, flush=True)
    if inherited_acls:
        # Only disposable pytest directories use inherited ACLs. Application
        # protection functions remain unchanged and are not certified by this run.
        original_mkdir = os.mkdir
        def test_mkdir(path, mode=0o777, **kwargs):
            return original_mkdir(path, 0o777 if mode == 0o700 else mode, **kwargs)
        os.mkdir = test_mkdir
        print("ACL héritées pour les répertoires temporaires synthétiques ; protections Windows non validées.", flush=True)
    for key in tuple(os.environ):
        if key.startswith(("SME_", "SMEDB_")):
            del os.environ[key]
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP", "TMP"):
        os.environ[key] = str(root)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.dont_write_bytecode = True
    tempfile.tempdir = str(root)
    # pandas consulte platform.uname(), dont le repli Windows peut invoquer
    # « ver ». Mettre ces métadonnées OS en cache avant de bloquer les processus
    # démarrés par les tests ; aucune donnée applicative n'est consultée.
    import platform
    platform.uname()
    allowed_port = None
    metadata = os.environ.get("IMPRESSION_MYSQL_TEST_METADATA")
    if metadata:
        import json
        info = json.loads(Path(metadata).read_text(encoding="utf-8-sig"))
        data = Path(info["datadir"]).resolve()
        if not data.is_relative_to(project / "build") or int(info["port"]) == 3306:
            raise RuntimeError("Le serveur fourni n'est pas une instance de test isolée.")
        allowed_port = int(info["port"])
    if native_dump and allowed_port is None:
        raise RuntimeError('Le client natif exige une instance MySQL jetable vérifiée.')

    def permitted_dump(executable, command):
        import re
        import shlex
        expected = Path('C:/Program Files/MySQL/MySQL Server 8.0/bin/mysqldump.exe')
        if not native_dump:
            return False
        argv = shlex.split(command, posix=False) if isinstance(command, str) else command
        candidate = executable or (argv[0].strip('"') if argv else '')
        if Path(candidate).resolve() != expected.resolve():
            return False
        required = ['--no-defaults', '--host=127.0.0.1', f'--port={allowed_port}', '--user=root',
                    '--single-transaction', '--hex-blob', '--no-tablespaces', '--set-gtid-purged=OFF',
                    '--column-statistics=0']
        return (len(argv) == len(required) + 2 and argv[1:-1] == required
                and re.fullmatch('impression_test_[0-9a-f]{32}', argv[-1]) is not None)

    def writable(path):
        if isinstance(path, int):
            return True
        # Ne pas résoudre un UNC hostile : aucun accès réseau nécessaire ici.
        normalized = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(str(root))
        return normalized == base or normalized.startswith(base + os.sep)

    def guard(event, args):
        targets = []
        if event == "open":
            path, mode, flags = args
            if ((isinstance(mode, str) and any(c in mode for c in "wax+")) or
                    (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))):
                targets.append(path)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"):
            targets.append(args[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            targets.extend(args[:2])
        elif event == "socket.connect":
            address = args[1]
            if not (allowed_port and isinstance(address, tuple) and
                    address[:2] == ("127.0.0.1", allowed_port)):
                raise RuntimeError("ISOLATED_TEST_BLOCKED_NETWORK")
        elif event == 'subprocess.Popen' and permitted_dump(args[0], args[1]):
            return
        elif event in ("socket.sendto", "subprocess.Popen", "os.system"):
            raise RuntimeError("ISOLATED_TEST_BLOCKED_EXTERNAL_IO")
        for target in targets:
            if not writable(target):
                raise RuntimeError("ISOLATED_TEST_BLOCKED_FILE_WRITE")

    sys.addaudithook(guard)
    if emulate_acls:
        from sme_gend.services import mysql_private_options
        def synthetic_acl(path):
            if not Path(path).resolve().is_relative_to(root):
                raise RuntimeError('Émulation ACL interdite hors du profil synthétique.')
        mysql_private_options._restrict_folder = synthetic_acl
        print('ACL simulées dans le profil jetable uniquement ; protections Windows non validées.', flush=True)
    import pymysql
    import pyodbc
    original_connect = pymysql.connect
    def connect(**kwargs):
        if not (allowed_port and kwargs.get("host") == "127.0.0.1" and
                kwargs.get("port") == allowed_port):
            raise RuntimeError("ISOLATED_TEST_BLOCKED_MYSQL")
        return original_connect(**kwargs)
    pymysql.connect = connect
    def no_odbc(*args, **kwargs):
        raise RuntimeError("ISOLATED_TEST_BLOCKED_ODBC")
    pyodbc.connect = no_odbc
    from sme_gend.services import audit_log, sensitive_audit_log
    audit_log._audit_dir = lambda: root / "logs"
    sensitive_audit_log._local_log_path = lambda: root / "sensitive_audit.jsonl"
    import pytest
    sys.path[:0] = [str(project / "tests"), str(project / "tests/integration")]
    class CollectionReport:
        def pytest_collection_modifyitems(self, config, items):
            if shard is not None:
                kept = [item for index, item in enumerate(items) if index % shard[1] == shard[0]]
                discarded = [item for index, item in enumerate(items) if index % shard[1] != shard[0]]
                items[:] = kept
                config.hook.pytest_deselected(items=discarded)

        def pytest_collection_finish(self, session):
            import json
            (root / "collected.json").write_text(
                json.dumps([item.nodeid for item in session.items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    args = arguments or ["tests"]
    return pytest.main([*args, "-s", "-p", "no:cacheprovider", "--junitxml", str(root / "results.xml"), "--basetemp", str(root / "pytest"),
                        "--log-file", str(root / "pytest.log")], plugins=[CollectionReport()])


if __name__ == "__main__":
    raise SystemExit(main())
