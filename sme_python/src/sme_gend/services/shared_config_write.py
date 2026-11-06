"""Version shared JSON settings and publish changes in the same transaction."""

from copy import deepcopy
import json
import re
import weakref

from sme_gend.repositories.mysql_connection import (
    MySQLConnection,
    after_mysql_commit,
    has_import_transaction,
    mysql_database_key,
)
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import (
    has_reference_write_lock,
    mark_reference_write,
    shared_reference_write,
)

MODEL_KEYS = frozenset({
    "liste_present_layout", "fonction_priority_order", "detaches_config",
    "limite_age_config", "soit_transmis_config",
})
GUARDED_KEYS = MODEL_KEYS | {"postes_tete_exclus", "ui_view_state", "sme_workspace"}
CONFIG_WRITE_PERMISSIONS = {
    "liste_present_layout": "effectifs.layout",
    "fonction_priority_order": "referentiels.fonctions",
    "detaches_config": "detaches.config",
    "limite_age_config": "attestations.limite_age",
    "soit_transmis_config": "attestations.soit_transmis",
}


def may_write_config(key):
    from sme_gend.services.auth_guard import session_has_permission
    from sme_gend.services.action_permissions import may_sync_structure_layout
    return (key not in CONFIG_WRITE_PERMISSIONS
            or session_has_permission(CONFIG_WRITE_PERMISSIONS[key])
            or (key == 'liste_present_layout' and may_sync_structure_layout()))


def _check_key(key):
    if key not in GUARDED_KEYS:
        raise ValueError("Configuration hors du périmètre de ce contrôle.")


def decode_payload(value):
    value = json.loads(value) if isinstance(value, (str, bytes)) else value
    if not isinstance(value, dict):
        raise ValueError("Configuration partagée invalide ; aucune donnée n'a été remplacée.")
    return value


def load_shared_payload(settings, key):
    _check_key(key)
    try:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                # A targeted mutation may join an older REPEATABLE READ
                # transaction. Only the owning reference lock permits this
                # current read; ordinary UI loads remain nonlocking.
                suffix = " FOR SHARE" if has_reference_write_lock(settings) else ""
                cursor.execute("SELECT payload FROM app_config WHERE config_key=%s" + suffix, (key,))
                row = cursor.fetchone()
                return decode_payload(row[0]) if row is not None else None
    except Exception as exc:
        raise RuntimeError(
            "Impossible de charger la configuration partagée. Vérifiez la connexion "
            "et réessayez ; aucun défaut n'a été enregistré."
        ) from exc


def bind_config_version(cfg, key, raw, settings):
    cfg.write_token = fingerprint(raw)
    cfg._pending_write = None
    if key != "liste_present_layout":
        cfg._write_key = key
        cfg._write_database = mysql_database_key(settings)
    return cfg


def copy_config_version(source, target):
    for attr in ("write_token", "_pending_write", "_write_key", "_write_database"):
        if hasattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    return target


def save_shared_model(settings, key, cfg, *, updated_by=None, validate=None):
    _check_key(key)
    if not may_write_config(key):
        raise PermissionError("Votre rôle ne permet pas de modifier cette configuration partagée.")
    token = getattr(cfg, "write_token", None)
    if key == "liste_present_layout":
        # Preserve the previous layout API and its existing DTO copies.
        from sme_gend.models.liste_layout import ListeLayoutConfig
        bound = isinstance(cfg, ListeLayoutConfig)
    else:
        bound = (
            getattr(cfg, "_write_key", None) == key
            and getattr(cfg, "_write_database", None) == mysql_database_key(settings)
        )
    if not bound or not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
        if key == "liste_present_layout":
            raise ValueError("Version de mise en page indisponible. Rechargez les données avant d'enregistrer.")
        raise ValueError("Version de configuration indisponible. Rechargez les données avant d'enregistrer.")

    payload = cfg.to_dict()
    new_token = fingerprint(payload)
    previous = cfg._pending_write
    from sme_gend.services.auth_context import resolve_audit_actor
    actor = updated_by if updated_by is not None else resolve_audit_actor()
    try:
        with shared_reference_write(settings):
            with MySQLConnection(settings).connect() as connection:
                expected = token
                if previous is not None and previous[0]() is connection and previous[1] == token:
                    expected = previous[2]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE", (key,))
                    row = cursor.fetchone()
                    current = decode_payload(row[0]) if row is not None else None
                    if fingerprint(current) != expected:
                        raise ReferenceConflictError(
                            "La configuration partagée a été modifiée depuis son chargement. "
                            "Votre brouillon est conservé. Rechargez les données avant de reprendre."
                        )
                    if validate is not None:
                        validate(cursor)
                    if fingerprint(current) == new_token:
                        return
                    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                    if row is None:
                        cursor.execute(
                            "INSERT INTO app_config(config_key,payload,updated_by) VALUES(%s,%s,%s)",
                            (key, encoded, actor),
                        )
                    else:
                        cursor.execute(
                            "UPDATE app_config SET payload=%s,updated_by=%s WHERE config_key=%s",
                            (encoded, actor, key),
                        )
                    mark_reference_write(cursor.rowcount > 0)

                # Multiple saves in an outer transaction use its pending
                # version. The public token advances only at the real commit.
                pending = (weakref.ref(connection), token, new_token)
                cfg._pending_write = pending

                def committed():
                    if cfg._pending_write is pending:
                        cfg.write_token = new_token
                        cfg._pending_write = None

                after_mysql_commit(committed)
    except BaseException:
        cfg._pending_write = previous
        raise


def load_shared_model(settings, key, default_factory, *, initialize=True):
    raw = load_shared_payload(settings, key)
    cfg = bind_config_version(default_factory(raw if raw is not None else {}), key, raw, settings)
    may_initialize = may_write_config(key)
    if raw is None and initialize and may_initialize and not has_import_transaction(settings):
        try:
            save_shared_model(settings, key, cfg)
        except ReferenceConflictError:
            # A concurrent first load may have initialized or edited this
            # setting. Return its winner instead of overwriting with defaults.
            raw = load_shared_payload(settings, key)
            if raw is None:
                raise
            cfg = bind_config_version(default_factory(raw), key, raw, settings)
    return cfg


class VersionedConfigDict(dict):
    """Keep persistence metadata outside the serialized dictionary."""

    def __init__(self, payload=None, *, key=None, token=None, database=None):
        super().__init__(payload or {})
        self._write_key = key
        self.write_token = token
        self._pending_write = None
        self._write_database = database

    def to_dict(self):
        return dict(self)


def load_shared_dict(settings, key):
    raw = load_shared_payload(settings, key)
    return VersionedConfigDict(raw, key=key, token=fingerprint(raw), database=mysql_database_key(settings))


def mutate_shared_dict(settings, key, transform, *, updated_by=None):
    """Apply one targeted intent to the current payload under the write lock."""
    _check_key(key)
    with shared_reference_write(settings):
        cfg = load_shared_dict(settings, key)
        payload = transform(deepcopy(dict(cfg)))
        if not isinstance(payload, dict):
            raise ValueError("Résultat de configuration invalide.")
        if cfg.write_token == fingerprint(None) and not payload:
            return cfg
        cfg.clear()
        cfg.update(payload)
        save_shared_model(settings, key, cfg, updated_by=updated_by)
        return cfg
