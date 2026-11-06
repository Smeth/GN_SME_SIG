"""Signal de modification partagé entre les postes, validé avec les données."""
from datetime import datetime, timezone
from uuid import uuid4

KEY_DATA_REVISION = "shared_data_revision"


def publish_data_change(settings, *, reason: str, mois: str | None = None) -> str:
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    token = uuid4().hex
    AppConfigRepository(settings).set_json(KEY_DATA_REVISION, {
        "token": token, "reason": reason, "mois": mois,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    })
    return token


def read_data_revision(settings) -> str:
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    repository = AppConfigRepository(settings)
    if not repository.table_exists('app_config'):
        raise RuntimeError('Configuration partagée absente : synchronisation impossible.')
    return str((repository.get_json(KEY_DATA_REVISION) or {}).get("token", ""))
