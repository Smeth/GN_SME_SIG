"""Import de la liste générale Access → MySQL (avec génération d'audit mensuel par personne)."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_access_repository import AccessListeGeneraleRepository
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.services.audit_log import (
    append_audit_event,
    append_personnel_audit_batch,
    row_to_audit_dict,
    diff_personnel_rows,
)
from sme_gend.services.liste_generale_import_stats import ListeGeneraleImportStats
from sme_gend.services.liste_generale_row_prep import prepare_liste_generale_rows_for_import
from sme_gend.services.mysql_ref_etl import RefEtlCache


def _write_import_audit_entries(
    old_by_mat: dict[str, ListeGeneraleRow],
    new_rows: list[ListeGeneraleRow],
    mois: str,
    detail_progress: Callable[[str], None] | None = None,
) -> int:
    """
    Compare l'état MySQL avant import (old_by_mat) avec les données Access fraîches (new_rows).
    """
    count = 0
    motif = f"Import mensuel {mois} (Access → MySQL)"
    audit_entries: list[dict[str, Any]] = []
    report_every = max(1, len(new_rows) // 100)
    for index, r in enumerate(new_rows, start=1):
        mat = (r.matricule_militaire or "").strip()
        old = old_by_mat.get(mat) if mat else None
        if old is not None:
            changes = diff_personnel_rows(old, r)
            if changes:
                audit_entries.append(
                    {
                        "action": "update",
                        "personnel_id": old.satellite_join_key() or old.id or mat,
                        "changes": changes,
                        "snapshot_before": row_to_audit_dict(old),
                        "snapshot_after": row_to_audit_dict(r),
                        "sme_mois_context": mois,
                        "edit_motif": motif,
                    }
                )
                count += 1
        if detail_progress and (index % report_every == 0 or index == len(new_rows)):
            detail_progress(f"Audit carrière — {index}/{len(new_rows)} agents vérifiés")
    append_personnel_audit_batch(audit_entries)
    return count


def import_liste_generale_access_to_mysql(
    access_settings: Settings,
    mysql_settings: Settings | None = None,
    *,
    record_audit: bool = True,
    mois: str | None = None,
    sme_periode_id: int | None = None,
    import_lot_id: int | None = None,
    historical: bool = False,
    ref_cache: RefEtlCache | None = None,
    detail_progress: Callable[[str], None] | None = None,
) -> tuple[int, int, list[str], ListeGeneraleImportStats]:
    """Lit ListeGenerale via Access et upsert dans MySQL (clé legacy_access_id)."""
    from sme_gend.services.mysql_import_period import normalize_import_mois

    mysql_st = mysql_settings or access_settings
    access = AccessListeGeneraleRepository(access_settings)
    if detail_progress:
        detail_progress("Liste générale — lecture du fichier Access…")
    raw_rows = access.fetch_all()
    if not mois or sme_periode_id is None or import_lot_id is None:
        raise ValueError("Import MySQL : mois, sme_periode_id et import_lot_id sont requis.")
    ym = normalize_import_mois(mois)
    view_st = replace(mysql_st, mysql_view_mois=ym)
    mysql_repo = MySQLAgentRepository(view_st)

    if detail_progress:
        detail_progress(f"Liste générale — préparation de {len(raw_rows)} lignes…")
    prepared, ids_empty, ids_auto, dup_ids = prepare_liste_generale_rows_for_import(
        raw_rows,
        import_lot_id=import_lot_id,
    )
    stats = ListeGeneraleImportStats(
        rows_read=len(raw_rows),
        ids_empty_source=ids_empty,
        ids_auto_generated=ids_auto,
        ids_ignored=0,
        duplicate_access_ids_resolved=dup_ids,
    )

    old_by_mat: dict[str, ListeGeneraleRow] = {}
    if mois:
        if detail_progress:
            detail_progress("Liste générale — lecture de l'état MySQL avant import…")
        try:
            for r in mysql_repo.fetch_all():
                mat = (r.matricule_militaire or "").strip()
                if mat:
                    old_by_mat[mat] = r
        except Exception as exc:
            raise RuntimeError(
                "Impossible de lire l'état MySQL avant l'import ; l'import de la liste "
                "générale a été interrompu pour éviter un audit incomplet."
            ) from exc

    if detail_progress:
        detail_progress(f"Liste générale — import de {len(prepared)} agents…")
    ins, upd, stats = mysql_repo.bulk_import_from_access_rows(
        prepared,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        mois=ym,
        historical=historical,
        ref_cache=ref_cache,
        prep_stats=stats,
        detail_progress=detail_progress,
    )
    imported_ids = [str(r.id or "").strip() for r in prepared if str(r.id or "").strip()]
    audit_count = 0
    if mois and old_by_mat:
        audit_rows = prepared
        # Une ancienne identité rapprochée n'écrase pas le profil canonique.
        # L'audit doit alors comparer l'état réellement enregistré, pas les
        # anciennes coordonnées présentes dans le fichier source.
        from sme_gend.repositories.mysql_connection import MySQLConnection
        from sme_gend.services.agent_identity_alias import load_identity_aliases
        with MySQLConnection(view_st).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                aliases = load_identity_aliases(cursor)
        if any(str(row.id or "").strip().casefold() in aliases for row in prepared):
            audit_rows = mysql_repo.fetch_all()
        audit_count = _write_import_audit_entries(
            old_by_mat,
            audit_rows,
            mois,
            detail_progress=detail_progress,
        )

    if record_audit:
        append_audit_event(
            action="liste_generale_import_mysql",
            access_database=str(access_settings.database_path.resolve()),
            mysql_database=mysql_st.mysql_database,
            rows_read=stats.rows_read,
            inserted=ins,
            updated=upd,
            audit_entries_created=audit_count if mois else None,
            **stats.to_dict(),
        )
    return ins, upd, imported_ids, stats
