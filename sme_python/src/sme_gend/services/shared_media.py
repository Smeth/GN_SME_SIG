"""Chemin réseau commun et migration non destructive des photos existantes."""
from pathlib import Path, PureWindowsPath
from threading import RLock
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import ctypes
import logging
import os
import shutil
import time

KEY_SHARED_MEDIA = "shared_personnel_media"
_cached = (0.0, None, None)
_cache_epoch = 0
_cache_lock = RLock()


def _validated_unc(value: str) -> str:
    from sme_gend.services.safe_paths import relative_parts
    value = value.replace("/", "\\")
    if value.casefold().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    if not value.startswith("\\\\") or value.startswith(("\\\\.\\", "\\\\?\\")):
        raise ValueError("Chemin UNC attendu : \\\\serveur\\partage\\dossier.")
    pieces = value[2:].rstrip("\\").split("\\")
    if len(pieces) < 2:
        raise ValueError("Le chemin réseau doit préciser le serveur et le partage.")
    relative_parts("/".join(pieces))
    return "\\\\" + "\\".join(pieces)


def canonical_network_path(value: str) -> str:
    value = os.path.expandvars(value.strip())
    if not value:
        raise ValueError("Indiquez un dossier explicite pour les médias.")
    if value.startswith(("\\\\", "//")):
        return _validated_unc(value)
    windows = PureWindowsPath(value)
    if (windows.drive and not windows.root) or (windows.root and not windows.drive):
        raise ValueError("Chemin ambigu : utilisez un chemin absolu ou UNC.")
    if os.name == "nt":
        from ctypes import wintypes
        fn = ctypes.WinDLL("mpr").WNetGetUniversalNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.DWORD
        size = wintypes.DWORD(65536)
        buffer = ctypes.create_string_buffer(size.value)
        result = fn(value, 1, buffer, ctypes.byref(size))
        if result == 234:
            buffer = ctypes.create_string_buffer(size.value)
            result = fn(value, 1, buffer, ctypes.byref(size))
        if result == 0:
            unc = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPWSTR)).contents.value
            return _validated_unc(unc or "")
        if windows.drive:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(value[:2] + "\\")
            if drive_type not in (2, 3, 5, 6):
                raise OSError("Lecteur réseau non résolu ou indisponible. Indiquez le chemin UNC \\\\serveur\\partage.")
    return str(Path(value).expanduser().resolve())


def invalidate_shared_media_cache() -> None:
    """Refresh now, retaining the last known UNC if MySQL is temporarily down."""
    global _cached, _cache_epoch
    with _cache_lock:
        _cache_epoch += 1
        _cached = (float('-inf'), _cached[1], _cached[2])


def shared_media_root() -> Path | None:
    global _cached
    from sme_gend.config import get_settings
    from sme_gend.repositories.mysql_connection import mysql_database_key
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings = get_settings()
    key = mysql_database_key(settings)
    now = time.monotonic()
    with _cache_lock:
        previous = _cached[2] if _cached[1] == key else None
        if _cached[1] == key and now - _cached[0] < 5:
            return previous
        epoch = _cache_epoch
    try:
        payload = AppConfigRepository(settings).get_json(KEY_SHARED_MEDIA) or {}
        raw = str(payload.get("unc_path", "")).strip()
        path = Path(_validated_unc(raw)) if raw else None
    except Exception:
        logging.getLogger(__name__).warning("Configuration des médias partagés inaccessible", exc_info=True)
        path = previous
    with _cache_lock:
        if epoch == _cache_epoch:
            _cached = (now, key, path)
    return path


@contextmanager
def _root_file_locks(roots):
    from sme_gend.services.media_storage_coordination import locked_media_stream
    from sme_gend.services.safe_paths import contained_path
    # Stable order also protects against loss of the SQL lock during file I/O.
    unique = {str(root.resolve()).casefold():root for root in roots}
    with ExitStack() as stack:
        for key in sorted(unique):
            stack.enter_context(locked_media_stream(contained_path(unique[key], '.media-locks/root.lock')))
        yield


def _central_path(payload):
    if payload is None:
        return None
    raw = payload.get('unc_path')
    if not isinstance(raw,str) or not raw.strip():
        raise ValueError('Chemin partagé central invalide ; aucune écriture de média effectuée.')
    return _validated_unc(raw.strip())


def _remember_shared_root(settings, raw):
    global _cached, _cache_epoch
    from sme_gend.repositories.mysql_connection import mysql_database_key
    with _cache_lock:
        _cache_epoch += 1
        _cached = (time.monotonic(), mysql_database_key(settings), Path(raw) if raw else None)


@dataclass
class MediaWriteTarget:
    root: object
    settings: object = None
    gate: object = None
    shared: bool = False

    def check(self):
        if self.gate is not None:
            self.gate.check()


@contextmanager
def shared_media_write_target(requested_root, *, settings=None, gate=None):
    if gate is None and not str(requested_root).startswith(('\\','//')):
        yield MediaWriteTarget(requested_root)
        return
    from sme_gend.config import get_settings
    from sme_gend.config.user_paths import load_user_paths
    from sme_gend.repositories.mysql_connection import current_transaction_settings
    from sme_gend.services.media_storage_coordination import media_storage_gate
    settings = settings or current_transaction_settings() or get_settings()
    if gate is None:
        with media_storage_gate(settings) as acquired:
            with shared_media_write_target(requested_root,settings=settings,gate=acquired) as target:
                yield target
        return
    payload = gate.payload()
    central = _central_path(payload)
    overrides = load_user_paths()
    explicit_override = bool(os.environ.get('SME_PERSONNEL_MEDIA_PATH','').strip()
                             or overrides.personnel_media_local_override)
    raw = str(requested_root) if explicit_override or not central else central
    shared = raw.startswith(('\\','//'))
    root = Path(_validated_unc(raw) if shared else raw)
    if central and raw == central:
        _root_children(root)
    with _root_file_locks([root]):
        if gate.payload() != payload:
            raise RuntimeError('Le dossier partagé a changé ; réessayez la copie après actualisation.')
        _remember_shared_root(settings,central)
        yield MediaWriteTarget(root,settings,gate,shared)


def _root_children(root):
    if not root.is_dir():
        raise OSError('Dossier partagé source inaccessible ; aucune bascule effectuée.')
    return {child.name.casefold():child for child in root.iterdir()}


def _image_index(root):
    from sme_gend.services.safe_paths import contained_path
    children = _root_children(root)
    index = {}
    for folder in ('photos','grades'):
        if folder not in children:
            continue
        source = contained_path(root, children[folder].name)
        if not source.is_dir():
            raise OSError('Sous-dossier de médias inaccessible ; aucune bascule effectuée.')
        for image in source.iterdir():
            if image.suffix.lower() in {'.jpg','.jpeg','.png','.webp','.svg'} and image.is_file():
                checked = contained_path(root, f'{children[folder].name}/{image.name}')
                index.setdefault((folder,image.stem.casefold()),[]).append(checked)
    return index


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def _assert_shared_images_preserved(source_index, target):
    destination_index = _image_index(target) if target.is_dir() else {}
    expected = {}
    for identity,files in source_index.items():
        digests = {_file_digest(path) for path in files}
        target_files = destination_index.get(identity,[])
        target_digests = {_file_digest(path) for path in target_files}
        if len(digests) != 1 or (target_digests and target_digests != digests):
            raise ValueError('Des images différentes existent pour une même identité. '
                             'Aucune bascule effectuée ; conservez les deux dossiers et résolvez les conflits '
                             'ou choisissez une nouvelle destination vide.')
        expected[identity] = next(iter(digests))
    return expected


def _verify_copied_images(expected, target):
    if not expected:
        return
    index = _image_index(target)
    for identity,digest in expected.items():
        paths = index.get(identity,[])
        if not paths or any(_file_digest(path) != digest for path in paths):
            raise OSError('Vérification des images copiées échouée ; ancien dossier partagé conservé.')


def _copy_media_sources(target, sources):
    from sme_gend.services.safe_paths import contained_path, atomic_destination
    from sme_gend.services.personnel_media import media_variant_lock
    for folder in ('photos','grades'):
        destination = contained_path(target,folder)
        destination.mkdir(parents=True,exist_ok=True)
        for source_root in sources:
            source = contained_path(source_root,folder)
            if source.is_dir() and source.resolve() != destination.resolve():
                for image in source.iterdir():
                    if image.is_file() and image.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.svg'}:
                        source_image = contained_path(source_root,f'{folder}/{image.name}')
                        final = contained_path(target,f'{folder}/{image.name}')
                        with media_variant_lock(target,folder,image.stem):
                            if any(final.with_suffix(ext).exists() for ext in ('.png','.jpg','.jpeg','.webp','.svg')):
                                continue
                            try:
                                with atomic_destination(final,replace_existing=False) as temporary:
                                    shutil.copy2(source_image,temporary)
                            except FileExistsError:
                                if not final.is_file():raise


class SharedMediaSwitchCommittedError(RuntimeError):
    committed = True


class SharedMediaSwitchOutcomeUnknownError(RuntimeError):
    committed = None


def configure_shared_media(settings, selected_path: str, previous_root: Path) -> str:
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("system.settings")
    from sme_gend.config.user_paths import user_application_data_dir
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, MySQLConnection
    from sme_gend.services.media_storage_coordination import media_storage_gate, decode_payload
    from sme_gend.services.data_sync import publish_data_change
    canonical = canonical_network_path(selected_path)
    target = Path(canonical)
    fallback = [Path(previous_root), user_application_data_dir()/'personnel_media']
    if not canonical.startswith(('\\','//')):
        _copy_media_sources(target,list(dict.fromkeys(fallback)))
        invalidate_shared_media_cache()
        return canonical
    committed = False
    try:
        with media_storage_gate(settings) as gate:
            payload = gate.payload()
            old_raw = _central_path(payload)
            old = Path(old_raw) if old_raw else None
            # Check existence before acquiring a lock that creates its parent.
            if old is not None:
                _root_children(old)
            with _root_file_locks([target]+([old] if old is not None else [])):
                if gate.payload() != payload:
                    raise RuntimeError('Configuration partagée modifiée pendant l’attente ; aucune bascule effectuée.')
                moving = old is not None and old_raw.casefold() != canonical.casefold()
                expected = _assert_shared_images_preserved(_image_index(old),target) if moving else {}
                sources = list(dict.fromkeys(([old] if old is not None else [])+fallback))
                _copy_media_sources(target,sources)
                _verify_copied_images(expected,target)
                gate.check()
                token = None
                try:
                    with mysql_import_transaction(settings,read_committed=True):
                        with MySQLConnection(settings).connect() as connection:
                            with connection.cursor() as cur:
                                cur.execute('SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE', (KEY_SHARED_MEDIA,))
                                row = cur.fetchone()
                        current = decode_payload(row[0]) if row else None
                        if current != payload:
                            raise RuntimeError('Configuration partagée modifiée ; ancien choix non écrasé.')
                        AppConfigRepository(settings).set_json(KEY_SHARED_MEDIA,{'unc_path':canonical})
                        token = publish_data_change(settings,reason='media_storage')
                        gate.check()
                except Exception as exc:
                    if token is not None:
                        # A lost COMMIT acknowledgement must not invite a second migration.
                        try:
                            actual = gate.payload()
                            revision = gate.payload('shared_data_revision') or {}
                        except Exception:
                            raise SharedMediaSwitchOutcomeUnknownError(
                                'Résultat de bascule incertain après interruption MySQL. '
                                'Conservez les deux dossiers et vérifiez le dossier actif '
                                'avant de relancer une migration.'
                            ) from exc
                        if actual == {'unc_path':canonical} and revision.get('token') == token:
                            committed = True
                            _remember_shared_root(settings,canonical)
                        else:
                            raise SharedMediaSwitchOutcomeUnknownError(
                                'Résultat de bascule incertain : la révision partagée a changé. '
                                'Conservez les deux dossiers et vérifiez le dossier actif '
                                'avant de relancer une migration.'
                            ) from exc
                    raise
                committed = True
                _remember_shared_root(settings,canonical)
        return canonical
    except Exception as exc:
        if committed:
            raise SharedMediaSwitchCommittedError(
                'Bascule déjà enregistrée dans MySQL, mais fin de coordination interrompue. '
                'Ne recommencez pas la migration ; actualisez la configuration des postes.'
            ) from exc
        raise
