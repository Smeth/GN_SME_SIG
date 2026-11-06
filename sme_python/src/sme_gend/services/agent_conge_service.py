"""Registre congés — types, sync depuis position admin, import Access optionnel."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sme_gend.services.numerique_padmin import PADMIN_CONGE_ADMIN, PADMIN_CONGE_EXP

CONGE_TYPE_ADMIN = "ADMIN"
CONGE_TYPE_EXPECTATIVE = "EXPECTATIVE"
CONGE_TYPE_AUTRE = "AUTRE"

CONGE_TYPE_LABELS: dict[str, str] = {
    CONGE_TYPE_ADMIN: "Congé administratif",
    CONGE_TYPE_EXPECTATIVE: "Congé d'expectative",
    CONGE_TYPE_AUTRE: "Autre congé",
}

_PADMIN_TO_CONGE: dict[str, str] = {
    PADMIN_CONGE_ADMIN.casefold(): CONGE_TYPE_ADMIN,
    PADMIN_CONGE_EXP.casefold(): CONGE_TYPE_EXPECTATIVE,
}


def padmin_to_conge_type(position_admin: str | None) -> str | None:
    p = (position_admin or "").strip().casefold()
    if not p:
        return None
    if p in _PADMIN_TO_CONGE:
        return _PADMIN_TO_CONGE[p]
    if "congé" in p or "conge" in p:
        return CONGE_TYPE_AUTRE
    return None


def quota_days_between(debut: date | None, fin: date | None) -> float | None:
    if debut is None or fin is None:
        return None
    if fin < debut:
        return None
    return float((fin - debut).days + 1)


def _str_or_empty(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _agent_id(cur: Any, legacy: str) -> int | None:
    leg = _norm_id(legacy)
    if not leg:
        return None
    cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _access_table_exists(pull: Any, table: str) -> bool:
    rows, err = pull(f"SELECT TOP 1 * FROM [{table}]")
    return err is None and rows is not None


def import_conges_from_access(
    cur: Any,
    pull: Any,
    *,
    sme_periode_id: int | None,
    import_lot_id: int | None,
    replace_all: bool,
) -> tuple[int, str | None]:
    """
    Tente ``TabConge`` ; si absente, retourne 0 (sync via position admin ensuite).
    Colonnes attendues (souples) : ID, TypeConge, DateDebut, DateFin, Quota, Obs.
    """
    if not _access_table_exists(pull, "TabConge"):
        return 0, "TabConge absente — congés déduits de la position admin à l'import."

    # replace_all (import du mois courant) ne doit effacer QUE la période importée,
    # jamais toute la table — un DELETE FROM agent_conge inconditionnel effaçait
    # l'historique de tous les mois à chaque resynchronisation du mois courant.
    if sme_periode_id is not None:
        cur.execute("DELETE FROM agent_conge WHERE sme_periode_id = %s AND source_code IN ('import_access', 'import_padmin')", (sme_periode_id,))

    from sme_gend.services.access_date_parse import parse_access_date

    rows, err = pull(
        "SELECT ID, TypeConge, DateDebut, DateFin, Quota, Obs FROM TabConge"
    )
    if err:
        return 0, f"TabConge : {err}"

    n = 0
    for r in rows:
        leg = _norm_id(r[0])
        aid = _agent_id(cur, leg)
        if not aid:
            continue
        type_raw = (_str_or_empty(r[1]) or "").casefold()
        if "expect" in type_raw or "exp" in type_raw:
            tc = CONGE_TYPE_EXPECTATIVE
        elif "admin" in type_raw:
            tc = CONGE_TYPE_ADMIN
        else:
            tc = CONGE_TYPE_AUTRE
        d0 = parse_access_date(r[2])
        d1 = parse_access_date(r[3])
        if d0 is None:
            continue
        quota = None
        if r[4] is not None:
            try:
                quota = float(r[4])
            except (TypeError, ValueError):
                quota = quota_days_between(d0, d1)
        else:
            quota = quota_days_between(d0, d1)
        upsert_conge_row(
            cur,
            agent_id=aid,
            sme_periode_id=sme_periode_id,
            type_code=tc,
            date_debut=d0,
            date_fin=d1,
            quota_jours=quota,
            observation=_str_or_empty(r[5]),
            source_code="import_access",
            legacy=leg,
            import_lot_id=import_lot_id,
        )
        n += 1
    return n, None


def sync_conges_from_carriere_period(
    cur: Any,
    *,
    sme_periode_id: int,
    import_lot_id: int | None,
) -> int:
    """Crée / met à jour des congés pour les agents en position CongéAdmin / CongéExp."""
    from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month

    cur.execute(
        "SELECT annee_mois FROM sme_periode WHERE id = %s",
        (sme_periode_id,),
    )
    per_row = cur.fetchone()
    ym = str(per_row[0]) if per_row else ""
    default_debut = first_day_of_month(ym) if ym else None
    default_fin = last_day_of_month(ym) if ym else None

    cur.execute(
        """
        SELECT a.id, a.legacy_access_id, c.position_admin_libelle,
               c.date_debut, c.date_fin
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE c.sme_periode_id = %s
        """,
        (sme_periode_id,),
    )
    n = 0
    for aid, leg, padmin, d0, d1 in cur.fetchall():
        tc = padmin_to_conge_type(padmin)
        if not tc:
            continue
        if isinstance(d0, datetime):
            d0 = d0.date()
        if isinstance(d1, datetime):
            d1 = d1.date()
        if d0 is None and default_debut is not None:
            d0 = default_debut
        if d1 is None and default_fin is not None:
            d1 = default_fin
        if d0 is None:
            continue
        legacy = f"{leg or aid}:padmin:{sme_periode_id}"
        upsert_conge_row(
            cur,
            agent_id=int(aid),
            sme_periode_id=sme_periode_id,
            type_code=tc,
            date_debut=d0,
            date_fin=d1,
            quota_jours=quota_days_between(d0, d1),
            observation=f"Déduit de la position admin « {padmin} »",
            source_code="import_padmin",
            legacy=legacy,
            import_lot_id=import_lot_id,
        )
        n += 1
    return n


def upsert_conge_row(
    cur: Any,
    *,
    agent_id: int,
    sme_periode_id: int | None,
    type_code: str,
    date_debut: date,
    date_fin: date | None,
    quota_jours: float | None,
    observation: str | None,
    source_code: str,
    legacy: str,
    import_lot_id: int | None,
) -> None:
    leg = (legacy or "").strip() or f"{agent_id}:{date_debut}:{type_code}"
    cur.execute(
        """
        INSERT INTO agent_conge (
          agent_id, sme_periode_id, type_conge_code, date_debut, date_fin,
          quota_jours, observation, source_code, source_ligne_legacy, import_lot_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          type_conge_code = VALUES(type_conge_code),
          date_fin = COALESCE(VALUES(date_fin), date_fin),
          quota_jours = COALESCE(VALUES(quota_jours), quota_jours),
          observation = COALESCE(VALUES(observation), observation),
          source_code = VALUES(source_code),
          import_lot_id = COALESCE(VALUES(import_lot_id), import_lot_id)
        """,
        (
            agent_id,
            sme_periode_id,
            type_code,
            date_debut,
            date_fin,
            quota_jours,
            observation,
            source_code,
            leg,
            import_lot_id,
        ),
    )

