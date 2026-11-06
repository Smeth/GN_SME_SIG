"""Import bundle SME depuis Excel (.xlsx) : LISTE GENERALE + GAIN PERTE."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from datetime import date, datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.gain_perte_repository import cdsme_from_mois_ym
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.mysql_import_period import (
    is_historical_import,
    last_day_of_month,
    normalize_import_mois,
    prepare_import_context,
)
from sme_gend.services.mysql_ref_etl import ensure_ref_etl_cache


def _norm_name(s: str | None) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFD", str(s).strip().upper())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _cell_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    return s or None


def _cell_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return None


def _sheet_by_name(wb, *candidates: str):
    names = {n.strip().upper(): n for n in wb.sheetnames}
    for c in candidates:
        key = c.strip().upper()
        if key in names:
            return wb[names[key]]
    return None


def parse_liste_generale_xlsx(path: Path) -> list[ListeGeneraleRow]:
    """Lit la feuille LISTE GENERALE (en-tetes ligne 3, donnees a partir de 4)."""
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = _sheet_by_name(wb, "LISTE GENERALE", "ListeGenerale", "LISTE_GENERALE")
        if ws is None:
            raise ValueError("Feuille « LISTE GENERALE » introuvable.")
        rows_out: list[ListeGeneraleRow] = []
        for raw in ws.iter_rows(min_row=4, values_only=True):
            if not raw or not any(v is not None and str(v).strip() for v in raw):
                continue
            noms = _cell_str(raw[1] if len(raw) > 1 else None)
            if not noms:
                continue
            rows_out.append(
                ListeGeneraleRow(
                    id="",
                    noms=noms,
                    date_naissance=_cell_dt(raw[2] if len(raw) > 2 else None),
                    lieu_naissance=_cell_str(raw[3] if len(raw) > 3 else None),
                    grade=_cell_str(raw[4] if len(raw) > 4 else None),
                    date_grade=_cell_dt(raw[5] if len(raw) > 5 else None),
                    date_service=_cell_str(raw[6] if len(raw) > 6 else None),
                    ndecision=_cell_str(raw[7] if len(raw) > 7 else None),
                    matricule_militaire=_cell_str(raw[8] if len(raw) > 8 else None),
                    matricule_solde=_cell_str(raw[9] if len(raw) > 9 else None),
                    position_admin=_cell_str(raw[10] if len(raw) > 10 else None),
                    fonction=_cell_str(raw[11] if len(raw) > 11 else None),
                    subdivision_arme=_cell_str(raw[12] if len(raw) > 12 else None),
                    diplome=_cell_str(raw[13] if len(raw) > 13 else None),
                    date_obtention=_cell_str(raw[14] if len(raw) > 14 else None),
                    structure=_cell_str(raw[15] if len(raw) > 15 else None),
                    obs=_cell_str(raw[16] if len(raw) > 16 else None),
                    ordre_tri=int(raw[0]) if raw[0] is not None and str(raw[0]).isdigit() else None,
                    presseance=None,
                )
            )
        return rows_out
    finally:
        wb.close()


def _parse_gain_perte_section(rows: list[tuple[Any, ...]]) -> list[dict[str, str | None]]:
    """Extrait lignes apres en-tete N / GRADE / NOM / STRUCTURES / OBS."""
    out: list[dict[str, str | None]] = []
    in_section = False
    for raw in rows:
        cells = list(raw) + [None] * 5
        c0 = _cell_str(cells[0])
        c1 = _cell_str(cells[1])
        c2 = _cell_str(cells[2])
        if c0 and c0.upper().startswith("N") and c1 and "GRADE" in c1.upper():
            in_section = True
            continue
        if not in_section:
            continue
        if c0 and c0.upper().startswith("B-"):
            break
        if not c2 or not c2.strip() or c2.strip() == " ":
            continue
        if c0 and not re.match(r"^\d+$", c0):
            continue
        out.append(
            {
                "grade": c1,
                "nom": c2,
                "structure": _cell_str(cells[3]),
                "obs": _cell_str(cells[4]),
            }
        )
    return out


def parse_gain_perte_xlsx(path: Path) -> tuple[list[dict], list[dict]]:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = _sheet_by_name(wb, "GAIN PERTE", "GAINS PERTES", "GAIN_PERTE")
        if ws is None:
            return [], []
        all_rows = list(ws.iter_rows(values_only=True))
        text = "\n".join(_cell_str(r[0]) or "" for r in all_rows[:15]).upper()
        gains_block: list[tuple[Any, ...]] = []
        pertes_block: list[tuple[Any, ...]] = []
        mode = None
        for raw in all_rows:
            c0 = (_cell_str(raw[0]) or "").upper()
            if "A-" in c0 and "GAIN" in c0:
                mode = "gain"
                continue
            if "B-" in c0 and "PERTE" in c0:
                mode = "perte"
                continue
            if mode == "gain":
                gains_block.append(raw)
            elif mode == "perte":
                pertes_block.append(raw)
        gains = _parse_gain_perte_section(gains_block)
        pertes = _parse_gain_perte_section(pertes_block)
        return gains, pertes
    finally:
        wb.close()


def _resolve_legacy_ids(settings: Settings, rows: list[ListeGeneraleRow]) -> tuple[list[ListeGeneraleRow], int]:
    """Remplit ``id`` (legacy_access_id) via matricule ou nom."""
    resolved: list[ListeGeneraleRow] = []
    skipped = 0
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            for r in rows:
                leg: str | None = None
                mat = (r.matricule_militaire or "").strip()
                if mat:
                    cur.execute(
                        """
                        SELECT legacy_access_id FROM agent
                        WHERE REPLACE(REPLACE(TRIM(IFNULL(matricule_militaire,'')), '-', ''), '.', '')
                          = REPLACE(REPLACE(%s, '-', ''), '.', '')
                        LIMIT 1
                        """,
                        (mat,),
                    )
                    row = cur.fetchone()
                    if row:
                        leg = str(row[0] or "").strip() or None
                if not leg and r.noms:
                    cur.execute(
                        """
                        SELECT legacy_access_id FROM agent
                        WHERE UPPER(TRIM(noms)) = %s LIMIT 1
                        """,
                        (r.noms.strip().upper(),),
                    )
                    row = cur.fetchone()
                    if row:
                        leg = str(row[0] or "").strip() or None
                if not leg and r.noms:
                    nn = _norm_name(r.noms)
                    cur.execute("SELECT legacy_access_id, noms FROM agent")
                    for lid, noms in cur.fetchall():
                        if _norm_name(noms) == nn:
                            leg = str(lid or "").strip()
                            break
                if not leg:
                    skipped += 1
                    continue
                resolved.append(replace(r, id=leg))
    return resolved, skipped


def _resolve_agent_by_nom(settings: Settings, nom: str | None, grade: str | None) -> str | None:
    n = (nom or "").strip()
    if not n:
        return None
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT legacy_access_id, noms FROM agent WHERE UPPER(TRIM(noms)) = %s LIMIT 1",
                (n.upper(),),
            )
            row = cur.fetchone()
            if row:
                return str(row[0] or "").strip() or None
            target = _norm_name(n)
            cur.execute("SELECT legacy_access_id, noms FROM agent")
            best: str | None = None
            for lid, noms in cur.fetchall():
                if _norm_name(noms) == target:
                    return str(lid or "").strip() or None
                # typo proche : BILAO vs BILOA
                if target and _norm_name(noms).replace("AO", "OA") == target.replace("AO", "OA"):
                    best = str(lid or "").strip() or None
            if best:
                return best
            # recherche partielle (nom de famille)
            parts = [p for p in re.split(r"\s+", target) if len(p) > 2]
            if parts:
                fam = parts[0]
                cur.execute(
                    """
                    SELECT legacy_access_id, noms FROM agent
                    WHERE UPPER(noms) LIKE %s
                    """,
                    (f"%{fam}%",),
                )
                cands = cur.fetchall()
                if len(cands) == 1:
                    return str(cands[0][0] or "").strip() or None
                if grade and cands:
                    g = grade.strip().upper()[:3]
                    for lid, noms in cands:
                        if g and g in (str(noms or "").upper()):
                            return str(lid or "").strip() or None
    return None


def _insert_mouvements_for_month(
    settings: Settings,
    *,
    sme_periode_id: int,
    import_lot_id: int | None,
    mois: str,
    movements: list[GainPerteRow],
    replace_period: bool,
) -> int:
    from sme_gend.services.mysql_access_numerique_import import _clamp_mouvement_payload

    ym = normalize_import_mois(mois)
    cdsme = cdsme_from_mois_ym(ym) or ""
    ref_date = last_day_of_month(ym)
    n = 0
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            if replace_period:
                cur.execute(
                    "DELETE FROM mouvement_effectif WHERE sme_periode_id = %s AND import_lot_id IS NOT NULL",
                    (sme_periode_id,),
                )
            cache = ensure_ref_etl_cache(cur)
            for row in movements:
                leg = str(row.id or "").strip()
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                ag = cur.fetchone()
                if not ag:
                    continue
                agent_id = int(ag[0])
                leg_s, grade_s, nom_s, type_mv, cat, motif, observ_s, cdsme_s = _clamp_mouvement_payload(
                    objet=row.objet,
                    observ=row.observ,
                    grade=row.grade,
                    nom=row.nom,
                    cdsme=cdsme or row.cdsme,
                    leg=leg,
                )
                cur.execute(
                    """
                    INSERT INTO mouvement_effectif (
                      sme_periode_id, agent_id, identifiant_ligne, grade_snapshot, nom_snapshot,
                      type_mouvement, categorie_mouvement, motif, cdsme_derive, observ,
                      date_reference, import_lot_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        sme_periode_id,
                        agent_id,
                        leg_s,
                        grade_s,
                        nom_s,
                        type_mv,
                        cat,
                        motif,
                        cdsme_s or cdsme,
                        observ_s,
                        ref_date,
                        import_lot_id,
                    ),
                )
                n += 1
            _ = cache
    return n


def import_excel_bundle_for_month(
    settings: Settings,
    path: Path,
    *,
    mois: str,
    progress: Callable[[str], None] | None = None,
    liste_generale_only: bool = False,
) -> dict[str, Any]:
    """Importe LISTE GENERALE (+ GAIN PERTE sauf si ``liste_generale_only``)."""
    from sme_gend.config.sme_context import load_sme_context

    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    ym = normalize_import_mois(mois)
    ctx = load_sme_context()
    historical = is_historical_import(ym, ctx.mois_actif)

    if progress:
        progress(f"Lecture Excel {path.name}…")
    lg_raw = parse_liste_generale_xlsx(path)
    gains_raw: list[dict[str, str | None]] = []
    pertes_raw: list[dict[str, str | None]] = []
    if not liste_generale_only:
        gains_raw, pertes_raw = parse_gain_perte_xlsx(path)

    if progress:
        progress(f"Résolution identifiants ({len(lg_raw)} lignes liste)…")
    lg_rows, lg_skipped = _resolve_legacy_ids(settings, lg_raw)

    periode_id, lot_id, ym, _ = prepare_import_context(settings, ym, path)

    if progress:
        progress(f"Import carriere {ym} ({len(lg_rows)} agents)…")

    view_st = replace(settings, mysql_view_mois=ym)
    repo = MySQLAgentRepository(view_st)
    ins, upd, _lg_stats = repo.bulk_import_from_access_rows(
        lg_rows,
        sme_periode_id=periode_id,
        import_lot_id=lot_id,
        mois=ym,
        historical=historical,
    )
    imported_ids = [str(r.id or "").strip() for r in lg_rows if str(r.id or "").strip()]

    gp_rows: list[GainPerteRow] = []
    gp_unresolved: list[str] = []
    for g in gains_raw:
        leg = _resolve_agent_by_nom(settings, g.get("nom"), g.get("grade"))
        if not leg:
            gp_unresolved.append(f"GAIN: {g.get('nom')}")
            continue
        gp_rows.append(
            GainPerteRow(
                id=leg,
                grade=g.get("grade"),
                nom=g.get("nom"),
                objet="GAIN",
                cdsme=cdsme_from_mois_ym(ym),
                observ=g.get("obs") or "AJOUT",
            )
        )
    for p in pertes_raw:
        leg = _resolve_agent_by_nom(settings, p.get("nom"), p.get("grade"))
        if not leg:
            gp_unresolved.append(f"PERTE: {p.get('nom')}")
            continue
        gp_rows.append(
            GainPerteRow(
                id=leg,
                grade=p.get("grade"),
                nom=p.get("nom"),
                objet="PERTE",
                cdsme=cdsme_from_mois_ym(ym),
                observ=p.get("obs") or "PERTE",
            )
        )

    n_mv = 0
    if gp_rows:
        if progress:
            progress(f"Mouvements gains/pertes ({len(gp_rows)} lignes)…")
        n_mv = _insert_mouvements_for_month(
            settings,
            sme_periode_id=periode_id,
            import_lot_id=lot_id,
            mois=ym,
            movements=gp_rows,
            replace_period=True,
        )

    from sme_gend.services.agent_dossier_import_enrichment import enrich_dossier_after_import
    from sme_gend.services.carriere_reconciliation import reconcile_after_import
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    enrich_dossier_after_import(settings, ym, imported_ids, import_lot_id=lot_id)
    reconcile_after_import(settings, mois=ym, import_lot_id=lot_id)
    invalidate_liste_cache(settings)

    append_audit_event(
        action="mysql_bundle_import_excel",
        source_file=str(path),
        sme_mois=ym,
        liste_rows=len(lg_raw),
        liste_resolved=len(lg_rows),
        liste_skipped=lg_skipped,
        inserted=ins,
        updated=upd,
        mouvement_effectif=n_mv,
        gain_perte_unresolved=gp_unresolved,
    )

    return {
        "sme_mois": ym,
        "liste_read": len(lg_raw),
        "liste_resolved": len(lg_rows),
        "liste_skipped": lg_skipped,
        "liste_inserted": ins,
        "liste_updated": upd,
        "gains_read": len(gains_raw),
        "pertes_read": len(pertes_raw),
        "mouvement_effectif": n_mv,
        "gain_perte_unresolved": gp_unresolved,
        "historical": historical,
    }
