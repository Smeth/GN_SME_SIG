"""Inspect/apply explicit schema migrations; never changes SQL on --plan."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser = argparse.ArgumentParser(description="Migration explicite du schéma SME")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-database")
    parser.add_argument("--safety-backup", type=Path)
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--username", help="Compte applicatif administrateur ; mot de passe demandé sans l'afficher")
    args = parser.parse_args()
    from sme_gend.config import get_settings
    from sme_gend.services.schema_migrations import schema_migration_status, apply_schema_migrations
    settings = get_settings()
    if args.plan:
        result = schema_migration_status(settings)
    else:
        if not args.expect_database or not args.safety_backup:
            parser.error("--apply exige --expect-database et --safety-backup")
        from sme_gend.config.auth_config import load_auth_config
        from sme_gend.services.auth_service import AuthService
        from sme_gend.services.auth_context import set_app_auth
        auth = AuthService()
        auth.replace_config(load_auth_config(strict=True))
        set_app_auth(auth)
        try:
            if auth.has_users():
                import getpass
                auth.mark_enforced()
                username = args.username or input("Compte administrateur : ").strip()
                if auth.authenticate(username, getpass.getpass("Mot de passe applicatif : ")) is None:
                    raise PermissionError(auth.last_login_error or "Connexion administrateur refusée.")
            elif auth.is_enabled():
                raise PermissionError("Initialisez un compte administrateur dans l'application avant cette migration.")
            result = apply_schema_migrations(settings, expected_database=args.expect_database,
                safety_backup=args.safety_backup, maintenance_confirmed=args.maintenance_confirmed,
                allow_resume=args.resume)
        finally:
            try:
                if auth.current_session() is not None:
                    auth.logout()
            except Exception:
                if "result" in locals() and isinstance(result, dict):
                    result.setdefault("warnings", []).append("La migration est terminée ; la fermeture de session reste à vérifier.")
            finally:
                set_app_auth(None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
