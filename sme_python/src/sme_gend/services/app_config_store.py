"""Stockage centralisé des JSON métier dans MySQL (multi-poste)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from sme_gend.config.settings import (
    Settings,
    detaches_config_path,
    liste_layout_config_path,
    project_root,
)
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.repositories.mysql_connection import (
    mysql_database_key,
    mysql_initialization_lock,
)
from sme_gend.config.sme_context import peek_editeur_defaut
from sme_gend.services.auth_context import resolve_audit_actor

_log = logging.getLogger(__name__)

_ensure_config_lock = threading.Lock()
_ensure_config_done: set[tuple[str, int, str]] = set()

KEY_LISTE_LAYOUT = "liste_present_layout"
KEY_FONCTION_PRIORITY = "fonction_priority_order"
KEY_DETACHES = "detaches_config"
KEY_LIMITE_AGE = "limite_age_config"
KEY_SOIT_TRANSMIS = "soit_transmis_config"
KEY_SME_WORKSPACE = "sme_workspace"
KEY_AUTH = "auth_config"
KEY_VIEW_STATE = "ui_view_state"
KEY_RETRAITE_FILTRES = "limite_age_retraite_filtres"

_PREF_UI = "ui_prefs"


def _repo(settings: Settings) -> AppConfigRepository:
    return AppConfigRepository(settings)


def ensure_central_config_ready(settings: Settings) -> None:
    """Crée les tables si besoin et importe les JSON locaux une seule fois par processus.

    ``load_sme_context()`` appelle cette fonction à chaque chargement, y compris
    depuis des workers d'arrière-plan (``run_in_background``) en parallèle du thread
    UI. Un verrou réel (et non un simple ``set`` de tokens, qui laissait passer deux
    threads arrivés à quelques microsecondes d'écart) est nécessaire : sans lui, deux
    threads pouvaient lancer la migration one-shot (``run_migration_v2``) en même
    temps et provoquer un deadlock MySQL (1213) sur des ``INSERT ... ON DUPLICATE KEY
    UPDATE`` concurrents visant la même clé ``app_config`` inexistante.
    """
    from sme_gend.repositories.mysql_connection import has_import_transaction
    if has_import_transaction(settings):
        return  # Aucune DDL ne doit valider implicitement une écriture métier.
    token = mysql_database_key(settings)
    if token in _ensure_config_done:
        return
    with _ensure_config_lock:
        if token in _ensure_config_done:
            return
        with mysql_initialization_lock(settings):
            repo = _repo(settings)
            try:
                repo.ensure_tables()
            except Exception as exc:
                _log.warning("Tables app_config non créées (%s)", exc)
                return
            migrate_json_files_to_database(settings)
            from sme_gend.services.config_migration_v2 import run_migration_v2

            run_migration_v2(settings)
            _ensure_config_done.add(token)


def migrate_json_files_to_database(settings: Settings) -> None:
    """Importe les fichiers ``data/*.json`` vers MySQL s'ils n'y sont pas encore."""
    repo = _repo(settings)
    actor = peek_editeur_defaut() or "import_initial"
    pairs: list[tuple[str, Path]] = [
        (KEY_LISTE_LAYOUT, liste_layout_config_path()),
        (KEY_DETACHES, detaches_config_path()),
        (KEY_LIMITE_AGE, project_root() / "data" / "limite_age_config.json"),
        (KEY_SOIT_TRANSMIS, project_root() / "data" / "soit_transmis_config.json"),
    ]
    for key, path in pairs:
        if repo.has_key(key):
            continue
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Import config %s ignoré : %s", path, exc)
            continue
        if isinstance(raw, dict):
            repo.set_json(key, raw, updated_by=actor)
            _log.info("Config %s importée depuis %s", key, path)

    if not repo.has_key(KEY_AUTH):
        from sme_gend.config.auth_config import AuthConfig, auth_config_file

        local_auth = auth_config_file()
        if local_auth.is_file():
            try:
                raw = json.loads(local_auth.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = AuthConfig.from_dict(raw)
                    repo.set_json(KEY_AUTH, cfg.to_dict(), updated_by=actor)
                    _log.info("Config %s importée depuis %s", KEY_AUTH, local_auth)
            except Exception as exc:
                _log.warning("Import auth_config ignoré : %s", exc)


def load_config_dict(settings: Settings, key: str, *, strict: bool = False) -> dict[str, Any] | None:
    try:
        return _repo(settings).get_json(key)
    except Exception as exc:
        if strict:
            raise
        _log.warning("Lecture config MySQL %s : %s", key, exc)
        return None


def save_config_dict(
    settings: Settings,
    key: str,
    payload: dict[str, Any],
    *,
    updated_by: str | None = None,
    expected_revision: int | None = None,
) -> None:
    from sme_gend.services.shared_config_write import GUARDED_KEYS,VersionedConfigDict,save_shared_model
    if key in GUARDED_KEYS:
        if not isinstance(payload,VersionedConfigDict):
            raise ValueError("Version de configuration indisponible. Utilisez la configuration chargée avant d'enregistrer.")
        save_shared_model(settings,key,payload,updated_by=updated_by)
        return
    actor = (updated_by or "").strip() or resolve_audit_actor()
    if expected_revision is None:
        _repo(settings).set_json(key, payload, updated_by=actor)
    else:
        _repo(settings).set_json_if_revision(
            key, payload, expected_revision=expected_revision, updated_by=actor,
        )


def load_config_model(
    settings: Settings,
    key: str,
    *,
    default_factory,
) -> Any:
    """Charge depuis MySQL ; initialise la clé avec le défaut si absente."""
    from sme_gend.services.shared_config_write import MODEL_KEYS,load_shared_model
    if key in MODEL_KEYS:
        return load_shared_model(settings,key,default_factory)
    raw = load_config_dict(settings, key)
    if raw is not None:
        return default_factory(raw)
    cfg = default_factory({})
    if hasattr(cfg, "to_dict"):
        save_config_dict(settings, key, cfg.to_dict())
    return cfg


def save_config_model(
    settings: Settings,
    key: str,
    cfg: Any,
    *,
    updated_by: str | None = None,
) -> None:
    from sme_gend.services.shared_config_write import MODEL_KEYS,save_shared_model
    if key in MODEL_KEYS:
        save_shared_model(settings,key,cfg,updated_by=updated_by)
    elif hasattr(cfg, "to_dict"):
        save_config_dict(settings, key, cfg.to_dict(), updated_by=updated_by)
    elif isinstance(cfg, dict):
        save_config_dict(settings, key, cfg, updated_by=updated_by)
