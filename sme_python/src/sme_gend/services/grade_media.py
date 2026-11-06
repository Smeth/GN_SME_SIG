"""Immutable image objects and transactional grade-code associations."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic

KEY = 'grade_insignia_by_code'
_lock = RLock()
_cache = {}
_epoch = 0


def invalidate_grade_media_cache():
    global _cache, _epoch
    with _lock:
        _epoch += 1
        _cache = {}


def media_index():
    global _cache
    now = monotonic()
    try:
        from sme_gend.config import get_settings
        from sme_gend.repositories.mysql_connection import mysql_database_key
        from sme_gend.repositories.app_config_repository import AppConfigRepository
        settings = get_settings()
        key = mysql_database_key(settings)
    except Exception:
        return {}
    with _lock:
        expires, cached = _cache.get(key, (0, {}))
        if now < expires:
            return cached
        epoch = _epoch
    try:
        value = AppConfigRepository(settings).get_json(KEY) or {}
    except Exception:
        value = {}
    with _lock:
        if epoch == _epoch:
            _cache[key] = (now + 5, value)
    return value


@contextmanager
def staged_insignia(code, source):
    """Unreferenced objects are harmless if SQL fails; existing images never change."""
    if source is None:
        yield None
        return
    from PySide6.QtGui import QImage, QImageReader
    from sme_gend.services.shared_media import shared_media_write_target
    from sme_gend.services.personnel_media import personnel_media_root, media_variant_lock
    from sme_gend.services.safe_paths import contained_path, atomic_destination
    source = Path(source)
    if source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("L'insigne ne doit pas dépasser 20 Mo.")
    size = QImageReader(str(source)).size()
    if size.width() * size.height() > 25000000:
        raise ValueError("L'image dépasse 25 millions de pixels.")
    data = source.read_bytes()
    image = QImage.fromData(data)
    if image.isNull() or image.width() * image.height() > 25000000:
        raise ValueError("Image d'insigne invalide ou trop grande (25 millions de pixels maximum).")
    ext = source.suffix.casefold()
    if ext not in ('.png', '.jpg', '.jpeg', '.svg', '.webp'):
        raise ValueError("Format d'image d'insigne non pris en charge.")
    key = 'code_' + sha256(code.casefold().encode()).hexdigest() + '_' + sha256(data).hexdigest()
    relative = f'grades/{key}{ext}'
    with shared_media_write_target(personnel_media_root()) as target:
        path = contained_path(target.root, relative)
        with media_variant_lock(target.root, 'grades', key):
            target.check()
            path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_destination(path, before_publish=target.check) as temporary:
                temporary.write_bytes(data)
            target.check()
            yield {'file': relative, 'label': ''}


def associate(settings, code, label, entry):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.shared_reference_write import mark_reference_write
    from sme_gend.services.business_audit import record_business_action
    repo = AppConfigRepository(settings)
    values = repo.get_json(KEY) or {}
    key = code.casefold()
    before = values.get(key)
    after = dict(entry or before or {})
    if not after:
        return
    after['label'] = label
    if before == after:
        return
    values[key] = after
    repo.set_json(KEY, values)
    mark_reference_write(True)
    record_business_action(settings, action='grade_insignia_change', module='reference',
                           entity_id=code, before=before, after=after)
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    after_mysql_commit(invalidate_grade_media_cache)


def remove_association(settings, code):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.business_audit import record_business_action
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    repo = AppConfigRepository(settings)
    values = repo.get_json(KEY) or {}
    before = values.pop(code.casefold(), None)
    if before is not None:
        repo.set_json(KEY, values)
        record_business_action(settings, action='grade_insignia_change', module='reference',
                               entity_id=code, before=before, after=None)
        after_mysql_commit(invalidate_grade_media_cache)
