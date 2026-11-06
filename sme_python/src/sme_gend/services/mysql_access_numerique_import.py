"""Import Access → tables agent-centrées (sans tab_mission / tab_malade / …)."""

from __future__ import annotations

from sme_gend.models.personnel_identity import stage_source_access_id

import re
from datetime import datetime
from typing import Any, Callable

from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str_or_empty(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _agent_id(cur: Any, legacy: str) -> int | None:
    leg = _norm_id(legacy)
    if not leg:
        return None
    cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
    row = cur.fetchone()
    return int(row[0]) if row else None


MOUVEMENT_MOTIF_MAX = 64
MOUVEMENT_OBSERV_MAX = 256
MOUVEMENT_TYPE_MAX = 64

_KNOWN_MOTIF_PREFIXES = (
    "DECES",
    "DÉCÈS",
    "AJOUT",
    "RETRAITE",
    "DEMISSION",
    "RADIATION",
    "MUTATION",
    "NOMINATION",
    "DISPONIBILITE",
    "LIMITE D'AGE",
    "LIMITE D AGE",
)


def _truncate_field(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s if len(s) <= max_len else s[:max_len]


def _motif_code_from_observ(observ: str | None, default: str) -> str:
    """Code court pour ``motif`` (VARCHAR 64) — le détail reste dans ``observ``."""
    obs = (observ or "").strip().upper()
    if not obs:
        return default
    obs_norm = obs.replace("É", "E").replace("È", "E")
    for code in _KNOWN_MOTIF_PREFIXES:
        c = code.replace("É", "E").replace("È", "E")
        if obs_norm.startswith(c) or c in obs_norm:
            return _truncate_field(c, MOUVEMENT_MOTIF_MAX) or default
    return _truncate_field(obs_norm, MOUVEMENT_MOTIF_MAX) or default


def _normalize_mouvement(objet: str | None, observ: str | None) -> tuple[str | None, str | None]:
    o = (objet or "").strip().upper()
    if o == "GAIN":
        return "GAIN", _motif_code_from_observ(observ, "AJOUT")
    if o == "PERTE":
        return "PERTE", _motif_code_from_observ(observ, "DECES")
    cat = _truncate_field(o or None, 16)
    motif = _motif_code_from_observ(observ, "")
    return cat, motif if motif else None


def _clamp_mouvement_payload(
    *,
    objet: str | None,
    observ: str | None,
    grade: str | None,
    nom: str | None,
    cdsme: str | None,
    leg: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None, str | None, str | None]:
    cat, motif = _normalize_mouvement(objet, observ)
    return (
        _truncate_field(leg, 256),
        _truncate_field(grade, 256),
        _truncate_field(nom, 512),
        _truncate_field(objet, MOUVEMENT_TYPE_MAX),
        cat,
        motif,
        _truncate_field(observ, MOUVEMENT_OBSERV_MAX),
        _truncate_field(cdsme, 32),
    )


def _clear_satellite_for_import(
    cur: Any,
    table: str,
    *,
    sme_periode_id: int | None,
    replace_all: bool,
) -> None:
    """Efface les lignes de la période importée — jamais toute la table.

    ``replace_all`` (import du mois courant) faisait auparavant un ``DELETE FROM
    {table}`` sans condition : chaque resynchronisation du mois courant effaçait
    l'historique complet de TOUS les mois pour cette table (perte de données
    silencieuse). ``replace_all`` est conservé dans la signature pour ne pas casser
    les appelants, mais n'a plus d'effet destructeur — la portée est toujours
    limitée à ``sme_periode_id``.
    """
    if sme_periode_id is not None:
        cur.execute(
            f"DELETE FROM {table} WHERE sme_periode_id = %s AND "
            "COALESCE(source_ligne_legacy, '') NOT LIKE '%%:saisie:%%' "
            "AND (import_lot_id IS NOT NULL OR source_ligne_legacy IS NOT NULL)",
            (sme_periode_id,),
        )


def import_missions_from_access(
    cur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    *,
    sme_periode_id: int | None,
    import_lot_id: int | None,
    replace_all: bool,
) -> int:
    _clear_satellite_for_import(cur, "agent_mission", sme_periode_id=sme_periode_id, replace_all=replace_all)
    from sme_gend.services.mysql_satellite_upsert import upsert_agent_mission

    rows, _ = pull("SELECT ID, Nature, LieuMission, Refnds, DateDepart FROM TabMission")
    n = 0
    for r in rows:
        leg = _norm_id(r[0])
        aid = _agent_id(cur, leg)
        if not aid:
            continue
        upsert_agent_mission(
            cur,
            agent_id=aid,
            sme_periode_id=sme_periode_id,
            date_debut=parse_access_date(r[4]),
            date_fin=None,
            nature=_str_or_empty(r[1]),
            lieu=_str_or_empty(r[2]),
            reference=_str_or_empty(r[3]),
            legacy=leg,
            import_lot_id=import_lot_id,
        )
        n += 1
    return n


def import_malades_from_access(
    cur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    *,
    sme_periode_id: int | None,
    import_lot_id: int | None,
    replace_all: bool,
) -> int:
    _clear_satellite_for_import(cur, "agent_malade", sme_periode_id=sme_periode_id, replace_all=replace_all)
    from sme_gend.services.mysql_satellite_upsert import upsert_agent_malade

    rows, _ = pull("SELECT ID, DateConstat, Pathologie, Obs FROM TabMalade")
    n = 0
    for r in rows:
        leg = _norm_id(r[0])
        aid = _agent_id(cur, leg)
        if not aid:
            continue
        d0 = parse_access_date(r[1])
        if d0 is None:
            continue
        upsert_agent_malade(
            cur,
            agent_id=aid,
            sme_periode_id=sme_periode_id,
            date_debut=d0,
            date_fin=None,
            pathologie=_str_or_empty(r[2]),
            observation=_str_or_empty(r[3]),
            legacy=leg,
            import_lot_id=import_lot_id,
        )
        n += 1
    return n


def import_desertions_from_access(
    cur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    *,
    sme_periode_id: int | None,
    import_lot_id: int | None,
    replace_all: bool,
) -> int:
    _clear_satellite_for_import(cur, "agent_desertion", sme_periode_id=sme_periode_id, replace_all=replace_all)
    rows, _ = pull("SELECT ID, DatDesertion FROM TabDeserteur")
    n = 0
    for r in rows:
        leg = _norm_id(r[0])
        aid = _agent_id(cur, leg)
        if not aid:
            continue
        dd = parse_access_date(r[1])
        if dd is None:
            continue
        cur.execute(
            """
            INSERT INTO agent_desertion (
              agent_id, sme_periode_id, date_desertion, source_ligne_legacy, import_lot_id
            ) VALUES (%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              date_desertion=VALUES(date_desertion),
              sme_periode_id=COALESCE(VALUES(sme_periode_id), sme_periode_id)
            """,
            (aid, sme_periode_id, dd, leg, import_lot_id),
        )
        n += 1
    return n


def _ensure_ref_type_stage_codes(cur: Any) -> None:
    """Codes attendus par l'import Access (FK parcours_stage) — réinsère si table vidée."""
    for code, lib in (
        ("LOCAL", "Stage local"),
        ("EXTERIEUR", "Stage extérieur"),
        ("INTERNE", "Stage interne"),
    ):
        cur.execute(
            """
            INSERT INTO ref_type_stage (code, libelle) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE libelle = VALUES(libelle)
            """,
            (code, lib),
        )


def _stage_dates(value: Any, debut: Any, fin: Any) -> tuple[Any, Any]:
    # Recognize only the exact date-pair format produced by our Access export.
    # Old archives also use this column for free text: retain their monthly
    # fallback and keep the original text in observation.
    match = re.fullmatch(r"\s*(\d{2}/\d{2}/\d{4})\s*\u2014\s*(\d{2}/\d{2}/\d{4})\s*", str(value or ""))
    if match is None:
        return debut, fin
    try:
        start, end = (datetime.strptime(part, "%d/%m/%Y").date() for part in match.groups())
    except ValueError as exc:
        raise ValueError("Dates de stage Access invalides.") from exc
    if end < start:
        raise ValueError("Dates de stage Access invalides : fin avant le debut.")
    return start, end


def import_stages_from_access(
    cur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    *,
    mois: str,
    sme_periode_id: int,
    import_lot_id: int | None,
) -> dict[str, int]:
    _ensure_ref_type_stage_codes(cur)
    debut = first_day_of_month(mois)
    fin = last_day_of_month(mois)
    counts: dict[str, int] = {}
    cur.execute("DELETE FROM parcours_stage WHERE sme_periode_id = %s AND "
                "COALESCE(source_ligne_legacy, '') NOT LIKE '%%:saisie:%%' "
                "AND (import_lot_id IS NOT NULL OR source_ligne_legacy IS NOT NULL)", (sme_periode_id,))
    for acc_tbl, type_code in (
        ("TabStageLocal", "LOCAL"),
        ("TabStageEx", "EXTERIEUR"),
        ("TabStageInterne", "INTERNE"),
    ):
        rows, _ = pull(f"SELECT ID, Naturestage, LieuStage, DateDebutFin FROM [{acc_tbl}]")
        n = 0
        for r in rows:
            leg = _norm_id(r[0])
            aid = _agent_id(cur, leg)
            if not aid:
                continue
            stage_debut, stage_fin = _stage_dates(r[3], debut, fin)
            cur.execute(
                """
                INSERT INTO parcours_stage (
                  agent_id, sme_periode_id, type_stage_code, date_debut, date_fin,
                  lieu, nature_libelle, observation, source_ligne_legacy, import_lot_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    aid,
                    sme_periode_id,
                    type_code,
                    stage_debut,
                    stage_fin,
                    _str_or_empty(r[2]),
                    _str_or_empty(r[1]),
                    _str_or_empty(r[3]),
                    stage_source_access_id(leg),
                    import_lot_id,
                ),
            )
            n += 1
        counts[f"parcours_stage_{type_code.lower()}"] = n
    return counts


def _movement_cdsme_import_status(cdsme: object, target_ym: str) -> str:
    """Classe le Cdsme source sans deviner le mois des lignes incomplètes."""
    raw = str(cdsme or "").strip()
    if not raw:
        return "missing"
    if len(raw) != 6 or not raw.isdigit():
        return "invalid"
    month, year = int(raw[:2]), int(raw[2:])
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        return "invalid"
    source_ym = f"{year:04d}-{month:02d}"
    return "target" if source_ym == target_ym else "other_month"


def _movement_cdsme_matches_import(cdsme: object, target_ym: str) -> bool:
    """True uniquement si le Cdsme valide correspond au mois importé."""
    return _movement_cdsme_import_status(cdsme, target_ym) == "target"


def import_mouvements_from_access(
    cur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    cache: Any,
    *,
    sme_periode_id: int,
    import_lot_id: int | None,
    mois_import: str,
    replace_all: bool,
    diagnostics: dict[str, int] | None = None,
) -> int:
    """Remplace les mouvements du mois importé à partir de TabGainPerte.

    Les mouvements dont le Cdsme indique un autre mois sont ignorés : un archive
    Access réutilisée comme base pour le mois suivant ne doit pas reporter les
    gains/pertes précédents. Le remplacement SQL reste borné au seul identifiant
    de période importé.
    """
    from sme_gend.models.gain_perte import observ_is_deces
    from sme_gend.services.mysql_import_period import (
        first_day_of_month,
        last_day_of_month,
        normalize_import_mois,
    )

    ym_import = normalize_import_mois(mois_import)
    rows, _ = pull("SELECT ID, Grade, nom, Objet, Cdsme, Observ FROM TabGainPerte")

    statuses = [_movement_cdsme_import_status(row[4], ym_import) for row in rows]
    missing = sum(status == "missing" for status in statuses)
    invalid = sum(status == "invalid" for status in statuses)
    other_month = sum(status == "other_month" for status in statuses)
    if diagnostics is not None:
        diagnostics.update({
            "missing_month": missing,
            "invalid_month": invalid,
            "other_month": other_month,
        })
    if missing or invalid:
        raise ValueError(
            "Import Gains & Pertes annulé : le mois SME ne peut pas être déterminé "
            f"pour {missing} ligne(s) sans Cdsme et {invalid} ligne(s) avec Cdsme invalide. "
            "Renseignez le Cdsme au format MMYYYY dans Access puis relancez l’import. "
            "Aucune ligne de mouvement n’a été remplacée."
        )

    prepared: list[tuple[int, int, str, str, str, str, str, str, str, str]] = []
    touched_pids: set[int] = set()
    for (leg, grade, nom, objet, cdsme, observ), status in zip(rows, statuses):
        if status == "other_month":
            continue
        leg_s = _norm_id(leg)
        aid = _agent_id(cur, leg_s)
        if not aid:
            continue
        leg_s, grade_s, nom_s, type_mv, cat, motif, observ_s, cdsme_s = _clamp_mouvement_payload(
            objet=_str_or_empty(objet),
            observ=_str_or_empty(observ),
            grade=_str_or_empty(grade),
            nom=_str_or_empty(nom),
            cdsme=_str_or_empty(cdsme),
            leg=leg_s,
        )
        # Les mouvements sont propres au mois. Le filtre sur le Cdsme source
        # ci-dessus empêche de réaffecter les mouvements des mois précédents.
        from sme_gend.repositories.gain_perte_repository import cdsme_from_mois_ym

        row_ym = ym_import
        pid = sme_periode_id
        derived = cdsme_from_mois_ym(ym_import)
        if derived:
            cdsme_s = derived
        ym_ref = row_ym or ym_import
        if observ_is_deces(observ_s):
            ref_date = first_day_of_month(ym_ref)
        else:
            ref_date = last_day_of_month(ym_ref)
        touched_pids.add(pid)
        prepared.append(
            (pid, aid, leg_s, grade_s, nom_s, type_mv, cat, motif, cdsme_s, observ_s, ref_date)
        )

    if replace_all:
        touched_pids.add(sme_periode_id)
        placeholders = ", ".join(["%s"] * len(touched_pids))
        cur.execute(
            f"DELETE FROM mouvement_effectif WHERE sme_periode_id IN ({placeholders}) AND import_lot_id IS NOT NULL",
            tuple(touched_pids),
        )
    else:
        cur.execute(
            "DELETE FROM mouvement_effectif WHERE sme_periode_id = %s AND import_lot_id IS NOT NULL",
            (sme_periode_id,),
        )

    for pid, aid, leg_s, grade_s, nom_s, type_mv, cat, motif, cdsme_s, observ_s, ref_date in prepared:
        cur.execute(
            """
            INSERT INTO mouvement_effectif (
              sme_periode_id, agent_id, identifiant_ligne, grade_snapshot, nom_snapshot,
              type_mouvement, categorie_mouvement, motif, cdsme_derive, observ,
              date_reference, import_lot_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                pid,
                aid,
                leg_s,
                grade_s,
                nom_s,
                type_mv,
                cat,
                motif,
                cdsme_s,
                observ_s,
                ref_date,
                import_lot_id,
            ),
        )
    return len(prepared)
