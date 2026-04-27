"""Import de la liste générale Access → SQLite."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.repositories.liste_generale_access_repository import (
    AccessListeGeneraleRepository,
)
from sme_gend.repositories.liste_generale_sqlite_repository import (
    SqliteListeGeneraleRepository,
)
from sme_gend.services.audit_log import append_audit_event


def import_liste_generale_access_to_sqlite(
    access_settings: Settings,
    sqlite_path: Path | None = None,
    *,
    record_audit: bool = True,
) -> tuple[int, int]:
    """
    Lit ``ListeGenerale`` via Access et upsert dans SQLite (clé ``id_acces``).

    ``access_settings`` doit avoir ``use_sqlite_liste_generale=False`` pour la lecture Access.
    Si ``sqlite_path`` est None, utilise ``access_settings.sqlite_liste_path``.
    Retourne (insertions, mises à jour).
    """
    dest = sqlite_path or access_settings.sqlite_liste_path
    if dest is None:
        raise ValueError("Chemin SQLite liste générale non défini.")
    access = AccessListeGeneraleRepository(access_settings)
    rows = access.fetch_all()
    sqlite_settings = replace(
        access_settings,
        sqlite_liste_path=Path(dest).expanduser().resolve(),
        use_sqlite_liste_generale=True,
    )
    sqlite_repo = SqliteListeGeneraleRepository(sqlite_settings)
    ins, upd = sqlite_repo.bulk_import_from_access_rows(rows)
    dest_resolved = Path(dest).expanduser().resolve()
    if record_audit:
        append_audit_event(
            action="liste_generale_import_sqlite",
            access_database=str(access_settings.database_path.resolve()),
            sqlite_path=str(dest_resolved),
            rows_read=len(rows),
            inserted=ins,
            updated=upd,
        )
    return ins, upd
