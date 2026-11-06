"""Optimistic movement snapshots checked under a short InnoDB row lock."""
from __future__ import annotations

import re
from sme_gend.models.gain_perte import GainPerteRow


# Raw values, not the trimmed/COALESCE display fields. The same expression is
# used at load and at save; no persistent schema/version column is needed.
MOVEMENT_REVISION_SQL = """SHA2(CAST(JSON_ARRAY(
    m.id,m.sme_periode_id,m.agent_id,m.identifiant_ligne,m.grade_snapshot,
    m.nom_snapshot,m.type_mouvement,m.categorie_mouvement,m.motif,
    m.cdsme_derive,m.observ,m.date_reference,m.import_lot_id,
    a.legacy_access_id,p.annee_mois
) AS CHAR),256)"""


class GainPerteConflictError(ValueError):
    """The displayed movement no longer matches the stored movement."""


def require_movement_token(expected: GainPerteRow | None, movement_id: int) -> str:
    token = getattr(expected, "write_token", None)
    if (type(movement_id) is not int or movement_id <= 0 or
            not isinstance(expected, GainPerteRow) or expected.movement_id != movement_id or
            not isinstance(token, tuple) or len(token) != 2 or
            type(token[0]) is not int or token[0] != movement_id or
            not isinstance(token[1], str) or re.fullmatch(r"[0-9a-f]{64}", token[1]) is None):
        raise ValueError("Version du mouvement indisponible. Actualisez les données avant de recommencer.")
    return token[1]


def check_movement_target(cursor, movement_id: int, revision: str) -> None:
    # Current locking read, including in an outer REPEATABLE READ transaction.
    # Shared parent locks allow unrelated movements in the same month/agent;
    # only this movement receives an exclusive lock until the real commit.
    cursor.execute(
        f"SELECT {MOVEMENT_REVISION_SQL} FROM mouvement_effectif m "
        "INNER JOIN agent a ON a.id=m.agent_id "
        "LEFT JOIN sme_periode p ON p.id=m.sme_periode_id "
        "WHERE m.id=%s FOR UPDATE OF m FOR SHARE OF a,p", (movement_id,),
    )
    current = cursor.fetchone()
    if current is None or str(current[0]) != revision:
        raise GainPerteConflictError(
            "Ce mouvement a été modifié ou supprimé depuis son chargement. "
            "Votre opération n'a pas été enregistrée. Actualisez les données avant de recommencer."
        )


def require_period_open(cursor, mois_ym: str) -> int:
    """Lock and validate the target period inside the same write transaction."""
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    from sme_gend.services.sme_editing_policy import is_statut_ferme

    ym = canonical_sme_mois_yyyy_mm((mois_ym or "").strip())
    if not ym:
        raise ValueError("Mois SME absent ou invalide ; écriture refusée.")
    cursor.execute(
        "SELECT id, statut FROM sme_periode WHERE annee_mois=%s ORDER BY id LIMIT 1 FOR SHARE",
        (ym,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"La période SME {ym} n’existe pas ; écriture refusée.")
    if is_statut_ferme(str(row[1] or "")):
        raise ValueError(
            f"La période SME {ym} est clôturée. Réouvrez-la explicitement avant toute correction."
        )
    return int(row[0])


def require_movement_period_open(cursor, movement_id: int) -> str:
    """Verrouille le mois source d’un mouvement et refuse les anciennes périodes closes."""
    from sme_gend.models.gain_perte import cdsme_to_mois_ym
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    cursor.execute(
        "SELECT p.annee_mois, m.cdsme_derive "
        "FROM mouvement_effectif m LEFT JOIN sme_periode p ON p.id=m.sme_periode_id "
        "WHERE m.id=%s FOR SHARE OF m,p",
        (movement_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise GainPerteConflictError(
            "Ce mouvement a été supprimé depuis son chargement. Actualisez les données."
        )
    ym = canonical_sme_mois_yyyy_mm(str(row[0] or "")) or cdsme_to_mois_ym(row[1])
    if not ym:
        raise ValueError("Le mois SME du mouvement est absent ou invalide ; écriture refusée.")
    require_period_open(cursor, ym)
    return ym


def require_consistent_movement_month(row: GainPerteRow) -> tuple[str, str]:
    """Require the SME period and Access Cdsme to identify the same month."""
    from sme_gend.models.gain_perte import cdsme_to_mois_ym, periode_ym_for_mouvement
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    ym = canonical_sme_mois_yyyy_mm(periode_ym_for_mouvement(row) or "")
    cdsme = str(row.cdsme or "").strip()
    cdsme_ym = canonical_sme_mois_yyyy_mm(cdsme_to_mois_ym(cdsme) or "")
    if not ym or not cdsme_ym:
        raise ValueError("Mois SME ou Cdsme absent/invalide ; mouvement non enregistré.")
    if ym != cdsme_ym:
        raise ValueError(
            f"Incohérence de mois : la période SME indique {ym}, mais le Cdsme indique {cdsme_ym}. "
            "Corrigez le mois dans la fiche avant d’enregistrer."
        )
    return ym, f"{ym[5:7]}{ym[:4]}"
