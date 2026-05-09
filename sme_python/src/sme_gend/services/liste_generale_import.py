"""Import de la liste générale Access → SQLite (avec génération d'audit mensuel par personne)."""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_access_repository import (
    AccessListeGeneraleRepository,
)
from sme_gend.repositories.liste_generale_sqlite_repository import (
    SqliteListeGeneraleRepository,
)
from sme_gend.services.audit_log import (
    append_audit_event,
    append_personnel_audit,
    diff_personnel_rows,
)
from sme_gend.services.personnel_dossier_history import (
    PARCOURS_FIELD_NAMES,
    PROFIL_FIELD_NAMES,
)

_TRACKED_FIELDS = PARCOURS_FIELD_NAMES | PROFIL_FIELD_NAMES


def _monthly_audit_ts(mois: str) -> str:
    """Horodatage ISO UTC du dernier jour du mois (ex. '2026-04' → '2026-04-30T23:59:59+00:00')."""
    y, m = int(mois[:4]), int(mois[5:7])
    last_day = calendar.monthrange(y, m)[1]
    return datetime(y, m, last_day, 23, 59, 59, tzinfo=timezone.utc).isoformat()


def _write_import_audit_entries(
    old_by_mat: dict[str, ListeGeneraleRow],
    new_rows: list[ListeGeneraleRow],
    mois: str,
) -> int:
    """
    Compare l'état SQLite avant import (old_by_mat) avec les données Access fraîches (new_rows).
    Pour chaque personne trouvée dans les deux bases (clé : matricule_militaire), écrit une
    entrée d'audit par personne avec tous les champs modifiés (parcours + profil).
    Retourne le nombre d'entrées créées.
    """
    try:
        ts = _monthly_audit_ts(mois)
    except (ValueError, IndexError):
        return 0

    motif = f"Import mensuel {mois} (Access → SQLite)"
    count = 0

    for nr in new_rows:
        mat = (nr.matricule_militaire or "").strip()
        if not mat:
            continue
        old_r = old_by_mat.get(mat)
        if old_r is None:
            continue  # nouvelle recrue — pas d'historique à générer
        all_changes = diff_personnel_rows(old_r, nr)
        # Filtre : uniquement les champs métier (parcours carrière + profil identité)
        changes = {k: v for k, v in all_changes.items() if k in _TRACKED_FIELDS}
        if not changes:
            continue
        append_personnel_audit(
            action="update",
            personnel_id=nr.id,
            changes=changes,
            sme_mois_context=mois,
            edited_by="Import mensuel",
            edit_motif=motif,
            ts_utc=ts,
        )
        count += 1

    return count


def import_liste_generale_access_to_sqlite(
    access_settings: Settings,
    sqlite_path: Path | None = None,
    *,
    record_audit: bool = True,
    mois: str | None = None,
) -> tuple[int, int]:
    """
    Lit ``ListeGenerale`` via Access et upsert dans SQLite (clé ``id_acces``).

    Si ``mois`` est fourni (format YYYY-MM), génère automatiquement les entrées d'audit
    parcours/profil par personne en comparant l'état SQLite AVANT l'import avec les
    données Access. Ces entrées apparaissent dans la fiche dossier (Parcours / Traçabilité).

    ``access_settings`` doit avoir ``use_sqlite_liste_generale=False`` pour la lecture Access.
    Si ``sqlite_path`` est None, utilise ``access_settings.sqlite_liste_path``.
    Retourne (insertions, mises à jour).
    """
    dest = sqlite_path or access_settings.sqlite_liste_path
    if dest is None:
        raise ValueError("Chemin SQLite liste générale non défini.")
    access = AccessListeGeneraleRepository(access_settings)
    new_rows = access.fetch_all()
    dest_resolved = Path(dest).expanduser().resolve()
    sqlite_settings = replace(
        access_settings,
        sqlite_liste_path=dest_resolved,
        use_sqlite_liste_generale=True,
    )
    sqlite_repo = SqliteListeGeneraleRepository(sqlite_settings)

    # Snapshot avant écrasement — pour la génération d'audit mensuel
    old_by_mat: dict[str, ListeGeneraleRow] = {}
    if mois:
        try:
            for r in sqlite_repo.fetch_all():
                mat = (r.matricule_militaire or "").strip()
                if mat:
                    old_by_mat[mat] = r
        except Exception:
            pass

    ins, upd = sqlite_repo.bulk_import_from_access_rows(new_rows)

    # Génération automatique des entrées d'audit par personne
    audit_count = 0
    if mois and old_by_mat:
        audit_count = _write_import_audit_entries(old_by_mat, new_rows, mois)

    if record_audit:
        append_audit_event(
            action="liste_generale_import_sqlite",
            access_database=str(access_settings.database_path.resolve()),
            sqlite_path=str(dest_resolved),
            rows_read=len(new_rows),
            inserted=ins,
            updated=upd,
            audit_entries_created=audit_count if mois else None,
        )
    return ins, upd
