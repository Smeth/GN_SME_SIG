"""Réinitialise le mot de passe du compte admin (défaut : SME-Admin2024)."""

from __future__ import annotations

import sys


def main() -> int:
    from sme_gend.config import get_settings
    from sme_gend.config.auth_config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME
    from sme_gend.services.auth_service import AuthService

    settings = get_settings()
    auth = AuthService()
    auth.reload_config()
    auth.change_password(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
    print(
        f"Mot de passe réinitialisé pour « {DEFAULT_ADMIN_USERNAME} » : "
        f"{DEFAULT_ADMIN_PASSWORD!r}"
    )
    print(f"(config enregistrée — MySQL {settings.mysql_host}/{settings.mysql_database})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
