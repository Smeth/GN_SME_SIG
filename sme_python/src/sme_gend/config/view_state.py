"""État UI partagé — persistance MySQL (app_config).

Filtres / recherche de la liste générale : session courante uniquement (mémoire),
pas de sauvegarde ici (multi-poste, pas de confusion entre utilisateurs).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sme_gend.config.user_paths import user_application_data_dir

_log = logging.getLogger(__name__)


def view_state_file() -> Path:
    """Chemin legacy — migration one-shot vers MySQL uniquement."""
    return user_application_data_dir() / "view_state.json"


def load_all_view_state() -> dict[str, Any]:
    try:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_VIEW_STATE
        from sme_gend.services.shared_config_write import load_shared_dict

        raw = load_shared_dict(get_settings(), KEY_VIEW_STATE)
        if isinstance(raw, dict):
            return raw
    except Exception as exc:
        _log.warning("view_state MySQL : %s", exc)
    return {}


def save_all_view_state(data: dict[str, Any]) -> None:
    from sme_gend.config import get_settings
    from sme_gend.services.app_config_store import KEY_VIEW_STATE
    from sme_gend.services.shared_config_write import save_shared_model

    save_shared_model(get_settings(),KEY_VIEW_STATE,data)


def get_view_state(view_id: str, mois: str | None = None) -> dict[str, Any]:
    all_data = load_all_view_state()
    key = view_id if not mois else f"{view_id}::{mois}"
    block = all_data.get(key)
    return dict(block) if isinstance(block, dict) else {}


def set_view_state(view_id: str, state: dict[str, Any], mois: str | None = None) -> None:
    key = view_id if not mois else f"{view_id}::{mois}"
    def change(all_data):
        all_data[key]=state
        return all_data
    _mutate_view_state(change)


def clear_view_state_for_view(view_id: str) -> int:
    """Supprime toutes les entrées ``view_id`` et ``view_id::*`` (nettoyage legacy)."""
    prefix = f"{view_id}::"
    removed=[]
    def change(all_data):
        removed.extend(k for k in list(all_data) if k==view_id or k.startswith(prefix))
        for key in removed:all_data.pop(key,None)
        return all_data
    _mutate_view_state(change)
    return len(removed)


def _mutate_view_state(change):
    from sme_gend.config import get_settings
    from sme_gend.services.app_config_store import KEY_VIEW_STATE
    from sme_gend.services.shared_config_write import mutate_shared_dict
    return mutate_shared_dict(get_settings(),KEY_VIEW_STATE,change)
