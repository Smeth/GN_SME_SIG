"""Liste générale unifiée : agent + carriere_situation (est_actuelle = 1)."""

from __future__ import annotations

from sme_gend.repositories.write_guard import atomic_repository_write

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_identity import new_internal_personnel_key
from sme_gend.models.personnel_sexe import normalize_sexe
from sme_gend.services.access_agent_profile import profile_field_supplied
from sme_gend.services.membership_scope import membership_projection_enabled

from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.config.liste_generale_scope import (
    carriere_scope_for_settings,
    carriere_write_scope_for_settings,
    carriere_scope_sql,
    resolve_carriere_scope_mois,
    resolve_liste_view_mois,
)
from sme_gend.services.mysql_import_period import (
    first_day_of_month,
    last_day_of_month,
    segment_dates_for_sme_mois,
)
from sme_gend.services.liste_generale_import_stats import ListeGeneraleImportStats
from sme_gend.services.mysql_ref_etl import (
    RefEtlCache,
    load_ref_cache_from_db,
    load_subdiv_flat_rows,
    prepare_carriere_write_fields,
)

# Colonnes / CAST hétérogènes (ex. codes structure) peuvent être en utf8mb4_bin :
# harmoniser avant LIKE pour éviter l'erreur 1267 avec la collation de session.
_MYSQL_LIKE_COLLATE = "utf8mb4_unicode_ci"


def _sql_for_like_match(expr: str) -> str:
    return f"CONVERT({expr} USING utf8mb4) COLLATE {_MYSQL_LIKE_COLLATE}"


def _carriere_join_sql(view_mois: str | None, *, settings=None, cursor=None) -> tuple[str, tuple]:
    """Jointure carrière : un segment par agent (le plus récent si doublons)."""
    membership = settings is not None and membership_projection_enabled(settings, cursor=cursor)
    filt, params = carriere_scope_sql(view_mois, alias="c_pick", membership=membership)
    return (
        f"""INNER JOIN carriere_situation c ON c.agent_id = a.id AND c.id = (
            SELECT MAX(c_pick.id) FROM carriere_situation c_pick
            WHERE c_pick.agent_id = a.id AND {filt}
        )""",
        params,
    )


def _select_liste(view_mois: str | None, *, settings=None, cursor=None) -> tuple[str, tuple]:
    join, params = _carriere_join_sql(view_mois, settings=settings, cursor=cursor)
    return (
        f"""
SELECT
  c.id AS carriere_id,
  a.legacy_access_id,
  a.noms,
  a.date_naissance,
  a.lieu_naissance,
  a.sexe,
  a.telephone,
  a.adresse_residence,
  IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle) AS grade_libelle,
  c.date_grade,
  c.date_service,
  c.ndecision,
  a.matricule_militaire,
  a.matricule_solde,
  IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle) AS position_admin_libelle,
  IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle) AS fonction_libelle,
  IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle) AS subdivision_arme_libelle,
  IF(display_diploma.id IS NULL, c.diplome_libelle, display_diploma.libelle_libre),
  IF(display_diploma.id IS NULL, c.date_obtention, display_diploma.date_obtention),
  IF(c.structure_id IS NOT NULL, rs.code, c.structure_code) AS structure_code,
  c.obs,
  c.ordre_tri,
  c.presseance
FROM agent a
{join}
LEFT JOIN sme_periode diploma_period ON diploma_period.id = c.sme_periode_id
LEFT JOIN agent_diplome display_diploma ON display_diploma.agent_id = a.id AND display_diploma.id = (
    SELECT d.id FROM agent_diplome d FORCE INDEX (idx_agent_diplome_agent)
    WHERE d.agent_id = a.id AND d.categorie = 'MILITAIRE'
      AND (d.date_obtention IS NULL OR d.date_obtention <= LEAST(
          COALESCE(c.date_fin, '9999-12-31'),
          LAST_DAY(CASE WHEN LEFT(REPLACE(diploma_period.annee_mois,'-',''),4) BETWEEN '1900' AND '2999'
            THEN CONCAT(LEFT(REPLACE(diploma_period.annee_mois,'-',''),4),'-',RIGHT(REPLACE(diploma_period.annee_mois,'-',''),2),'-01')
            ELSE CONCAT(RIGHT(REPLACE(diploma_period.annee_mois,'-',''),4),'-',LEFT(REPLACE(diploma_period.annee_mois,'-',''),2),'-01') END)))
    ORDER BY d.date_obtention DESC, d.id DESC LIMIT 1
)
LEFT JOIN ref_structure rs ON rs.id = c.structure_id
LEFT JOIN ref_grade rg ON rg.id = c.grade_id
LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
""",
        params,
    )


def _padmin_label_sql() -> str:
    return "COALESCE(rpa.libelle, c.position_admin_libelle, '')"


def _norm_padmin_sql(column: str | None = None) -> str:
    col = column or _padmin_label_sql()
    return (
        f"LOWER(REPLACE(REPLACE(REPLACE(TRIM({col}), 'é', 'e'), 'è', 'e'), '  ', ' '))"
    )


def _padmin_where_clause(mode: str) -> tuple[str, tuple]:
    """Presence modes, with an explicit-only option for Liste des présents."""
    from sme_gend.padmin_present import _non_present_exact_codes

    label = ("COALESCE(IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle), '')"
             if mode == 'present_strict' else None)
    norm = _norm_padmin_sql(label)
    # Collapse pasted tabs/newlines/NBSP before trimming their replacement
    # spaces, just like Python's strip/split normalization.
    norm = f"TRIM(REGEXP_REPLACE({norm}, '[[:space:]]+', ' '))"
    if mode == 'present_strict':
        from sme_gend.services.position_codes import LABELS, normalized_label

        labels = ', '.join("'" + normalized_label(value).replace("'", "''") + "'"
                           for value in LABELS['present'])
        return f"({norm} COLLATE utf8mb4_unicode_ci IN ({labels}))", ()
    stage = f"({norm} IN ('stageinterne', 'stage interne'))"
    codes = ', '.join("'" + code.replace("'", "''") + "'" for code in sorted(_non_present_exact_codes()))
    excluded = (f"({norm} REGEXP '(^|[^a-z])(non[ -]*present|pas[ -]+present|absent)([^a-z]|$)' "
                f"OR {norm} IN ({codes}) OR {norm} LIKE '%%mission%%')")
    text = f"({norm} REGEXP '(^|[^a-z])present([^a-z]|$)')"
    reference_flag = ("(rpa.id IS NOT NULL AND rpa.compte_comme_present = 1) OR "
                      "(c.position_admin_id IS NULL AND EXISTS (SELECT 1 FROM ref_position_admin rp "
                      "WHERE rp.compte_comme_present=1 AND "
                      "TRIM(REGEXP_REPLACE(rp.libelle,'[[:space:]]+',' ')) COLLATE utf8mb4_unicode_ci = "
                      "TRIM(REGEXP_REPLACE(c.position_admin_libelle,'[[:space:]]+',' ')) COLLATE utf8mb4_unicode_ci))")
    present = (f"({norm} = '' OR (NOT {stage} AND NOT {excluded} "
               f"AND ({text} OR ({reference_flag}))))")
    if mode == "present":
        return present, ()
    if mode == "stage_interne":
        return stage, ()
    return f"({present} OR {stage})", ()


def _carriere_scope_where(view_mois: str | None, *, settings=None, cursor=None) -> tuple[str, tuple]:
    membership = settings is not None and membership_projection_enabled(settings, cursor=cursor)
    return carriere_scope_sql(view_mois, alias="", membership=membership)


def _parse_date(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return None


def _to_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _row_to_liste(row: tuple) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=str(row[0]),
        access_join_id=str(row[1] or "").strip() or None,
        noms=row[2],
        date_naissance=_parse_date(row[3]),
        lieu_naissance=row[4],
        sexe=normalize_sexe(row[5]),
        telephone=_to_str(row[6]),
        adresse_residence=_to_str(row[7]),
        grade=row[8],
        date_grade=_parse_date(row[9]),
        date_service=row[10],
        ndecision=row[11],
        matricule_militaire=row[12],
        matricule_solde=row[13],
        position_admin=row[14],
        fonction=row[15],
        subdivision_arme=row[16],
        diplome=row[17],
        date_obtention=row[18],
        structure=row[19],
        obs=row[20],
        ordre_tri=row[21],
        presseance=row[22],
    )


def _fmt_carriere_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(val).strip()
    return s or None


def _carriere_snapshot_from_history_row(r: tuple) -> CarriereSnapshotRow:
    return CarriereSnapshotRow(
        annee_mois=str(r[0] or ""),
        date_debut=_fmt_carriere_date(r[1]),
        date_fin=_fmt_carriere_date(r[2]),
        grade=str(r[3] or "").strip() or None,
        date_grade=_fmt_carriere_date(r[4]),
        structure=str(r[5] or "").strip() or None,
        position_admin=str(r[6] or "").strip() or None,
        fonction=str(r[7] or "").strip() or None,
        subdivision_arme=str(r[8] or "").strip() or None,
        date_service=_fmt_carriere_date(r[9]),
        ndecision=str(r[10] or "").strip() or None,
        diplome=str(r[11] or "").strip() or None,
        date_obtention=_fmt_carriere_date(r[12]),
        est_actuelle=bool(r[13]),
    )


_CARRIERE_HISTORY_FIELDS = """
    p.annee_mois, c.date_debut, c.date_fin,
    c.grade_libelle, c.date_grade, c.structure_code,
    c.position_admin_libelle, c.fonction_libelle,
    c.subdivision_arme_libelle, c.date_service, c.ndecision,
    c.diplome_libelle, c.date_obtention, c.est_actuelle
"""


class MySQLAgentRepository(BaseMySQLRepository):
    """Accès liste générale via le modèle unifié agent / carriere_situation."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    @property
    def _scope_mois(self) -> str | None:
        return resolve_carriere_scope_mois(self._settings)

    @property
    def _view_mois(self) -> str | None:
        return resolve_liste_view_mois(self._settings)

    def list_carriere_history_for_agent(self, legacy_access_id: str) -> list[CarriereSnapshotRow]:
        """Toutes les situations carrière par période SME (ordre chronologique)."""
        leg = str(legacy_access_id or "").strip()
        if not leg:
            return []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                if not row:
                    return []
                aid = int(row[0])
                cur.execute(
                    f"""
                    SELECT {_CARRIERE_HISTORY_FIELDS}
                    FROM carriere_situation c
                    INNER JOIN sme_periode p ON p.id = c.sme_periode_id
                    WHERE c.agent_id = %s
                    ORDER BY p.annee_mois ASC, c.id ASC
                    """,
                    (aid,),
                )
                return [_carriere_snapshot_from_history_row(r) for r in cur.fetchall()]

    def resolve_agent_id_from_row(self, row: ListeGeneraleRow) -> int:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid, _ = self._resolve_agent_id(cur, row)
                return int(aid)

    def fetch_latest_carriere_mois(self, agent_id: int) -> str | None:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.annee_mois
                    FROM carriere_situation c
                    LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                    WHERE c.agent_id = %s
                    ORDER BY COALESCE(p.annee_mois, '0000-00') DESC, c.id DESC
                    LIMIT 1
                    """,
                    (int(agent_id),),
                )
                row = cur.fetchone()
                return str(row[0]).strip() if row and row[0] else None

    def fetch_latest_liste_row_for_agent(self, agent_id: int) -> ListeGeneraleRow | None:
        """Dernière situation carrière connue (hors filtre est_actuelle)."""
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id
                    FROM carriere_situation c
                    LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                    WHERE c.agent_id = %s
                    ORDER BY COALESCE(p.annee_mois, '0000-00') DESC, c.id DESC
                    LIMIT 1
                    """,
                    (int(agent_id),),
                )
                pick = cur.fetchone()
                if not pick:
                    cur.execute(
                        """
                        SELECT
                          CAST(a.id AS CHAR) AS carriere_id,
                          a.legacy_access_id, a.noms, a.date_naissance, a.lieu_naissance,
                          a.sexe, a.telephone, a.adresse_residence,
                          NULL, NULL, NULL, NULL,
                          a.matricule_militaire, a.matricule_solde,
                          NULL, NULL, NULL, NULL, NULL,
                          NULL, NULL, NULL, NULL
                        FROM agent a WHERE a.id = %s
                        """,
                        (int(agent_id),),
                    )
                    r = cur.fetchone()
                    if not r:
                        return None
                    return _row_to_liste(r)
                cid = int(pick[0])
                cur.execute(
                    f"""
                    SELECT
                      c.id AS carriere_id,
                      a.legacy_access_id,
                      a.noms,
                      a.date_naissance,
                      a.lieu_naissance,
                      a.sexe,
                      a.telephone,
                      a.adresse_residence,
                      IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle),
                      c.date_grade,
                      c.date_service,
                      c.ndecision,
                      a.matricule_militaire,
                      a.matricule_solde,
                      IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle),
                      IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle),
                      IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle),
                      c.diplome_libelle,
                      c.date_obtention,
                      IF(c.structure_id IS NOT NULL, rs.code, c.structure_code),
                      c.obs,
                      c.ordre_tri,
                      c.presseance
                    FROM carriere_situation c
                    INNER JOIN agent a ON a.id = c.agent_id
                    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                    LEFT JOIN ref_grade rg ON rg.id = c.grade_id
                    LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
                    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                    LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
                    WHERE c.id = %s
                    """,
                    (cid,),
                )
                r = cur.fetchone()
                return _row_to_liste(r) if r else None

    def load_carriere_history_bulk(
        self,
    ) -> dict[str, tuple[str | None, list[CarriereSnapshotRow]]]:
        """Historique carrière de tous les agents — une requête (alertes, backfill)."""
        out: dict[str, tuple[str | None, list[CarriereSnapshotRow]]] = {}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT a.legacy_access_id, a.noms, {_CARRIERE_HISTORY_FIELDS}
                    FROM carriere_situation c
                    INNER JOIN agent a ON a.id = c.agent_id
                    INNER JOIN sme_periode p ON p.id = c.sme_periode_id
                    WHERE a.legacy_access_id IS NOT NULL
                      AND TRIM(a.legacy_access_id) <> ''
                    ORDER BY a.legacy_access_id, p.annee_mois ASC, c.id ASC
                    """
                )
                for r in cur.fetchall():
                    leg = str(r[0] or "").strip()
                    if not leg:
                        continue
                    noms = str(r[1] or "").strip() or None
                    snap = _carriere_snapshot_from_history_row(r[2:])
                    if leg not in out:
                        out[leg] = (noms, [snap])
                    else:
                        prev_noms, snaps = out[leg]
                        if noms and not prev_noms:
                            prev_noms = noms
                        snaps.append(snap)
                        out[leg] = (prev_noms, snaps)
        return out

    def fetch_agent_id_by_legacy(self, legacy_access_id: str) -> int | None:
        leg = (legacy_access_id or "").strip()
        if not leg:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                return int(row[0]) if row else None

    def fetch_career_id_for_agent_periode(
        self, agent_id: int, periode_id: int
    ) -> int | None:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM carriere_situation
                    WHERE agent_id = %s AND sme_periode_id = %s
                    LIMIT 1
                    """,
                    (int(agent_id), int(periode_id)),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None

    def fetch_agent_id_for_career(self, career_id: str) -> int | None:
        try:
            pk = int((career_id or "").strip())
        except ValueError:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_id FROM carriere_situation WHERE id = %s",
                    (pk,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None

    def career_segment_exists(self, career_id: str) -> bool:
        try:
            pk = int((career_id or "").strip())
        except ValueError:
            return False
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM carriere_situation WHERE id = %s", (pk,))
                return cur.fetchone() is not None

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        try:
            pk = int(row_id)
        except ValueError:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                sql, params = _select_liste(self._scope_mois, settings=self._settings, cursor=cur)
                cur.execute(f"{sql} WHERE c.id = %s", params + (pk,))
                r = cur.fetchone()
                return _row_to_liste(r) if r else None

    def fetch_scoped_by_legacy(self, legacy_id: str) -> ListeGeneraleRow | None:
        """Résout une identité agent dans le même périmètre que la liste générale."""
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                from sme_gend.services.agent_identity_alias import canonical_legacy_id
                legacy_id = canonical_legacy_id(cur, legacy_id)
                sql, params = _select_liste(self._scope_mois, settings=self._settings, cursor=cur)
                cur.execute(f"{sql} WHERE a.legacy_access_id = %s", params + (legacy_id.strip(),))
                row = cur.fetchone()
                return _row_to_liste(row) if row else None

    def fetch_all(self) -> list[ListeGeneraleRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                sql, params = _select_liste(self._scope_mois, settings=self._settings, cursor=cur)
                cur.execute(f"{sql} ORDER BY c.ordre_tri, c.id", params)
                return [_row_to_liste(r) for r in cur.fetchall()]

    def search_current_personnel(
        self, term: str, *, limit: int | None = None
    ) -> list[ListeGeneraleRow]:
        """Recherche bornée exécutée en base, sans charger tout l'effectif."""
        needle = f"%{term.strip().upper()}%"
        columns = (
            "a.noms",
            "IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle)",
            "IF(c.structure_id IS NOT NULL, rs.code, c.structure_code)",
            "rs.designation",
            "a.matricule_militaire",
            "a.matricule_solde",
            "IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle)",
            "IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle)",
            "a.legacy_access_id",
            "CAST(c.id AS CHAR)",
        )
        concat = f"UPPER(CONCAT_WS(' ', {', '.join(columns)}))"
        where = (f"{_sql_for_like_match(concat)} LIKE %s OR EXISTS (SELECT 1 FROM agent_identity_alias ax "
                 "WHERE ax.canonical_agent_id=a.id AND ax.alias_legacy_id LIKE %s)")
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                sql, scope_params = _select_liste(self._scope_mois, settings=self._settings, cursor=cur)
                query = f"{sql} WHERE ({where}) ORDER BY c.ordre_tri, c.id"
                params = scope_params + (needle, needle)
                if limit is not None:
                    query += " LIMIT %s"
                    params += (max(1, int(limit)),)
                cur.execute(query, params)
                return [_row_to_liste(row) for row in cur.fetchall()]

    def search_historical_personnel(
        self, term: str, *, limit: int | None = None
    ) -> list[tuple[int, ListeGeneraleRow, str | None, tuple | None]]:
        """Charge les résultats historiques et leurs résumés en deux requêtes."""
        needle = f"%{term.strip().upper()}%"
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                current_scope, current_params = carriere_scope_sql(None, alias='current_c',
                    membership=membership_projection_enabled(self._settings, cursor=cur))
                query = f"""
                    SELECT a.id
                    FROM agent a
                    WHERE NOT EXISTS (
                      SELECT 1 FROM carriere_situation current_c
                      WHERE current_c.agent_id = a.id AND {current_scope}
                    )
                      AND (
                        {_sql_for_like_match("UPPER(COALESCE(a.noms, ''))")} LIKE %s
                        OR {_sql_for_like_match("UPPER(COALESCE(a.matricule_militaire, ''))")} LIKE %s
                        OR {_sql_for_like_match("UPPER(COALESCE(a.matricule_solde, ''))")} LIKE %s
                        OR {_sql_for_like_match("UPPER(COALESCE(a.legacy_access_id, ''))")} LIKE %s
                        OR EXISTS (SELECT 1 FROM agent_identity_alias ax WHERE ax.canonical_agent_id=a.id AND ax.alias_legacy_id LIKE %s)
                        OR EXISTS (
                          SELECT 1 FROM carriere_situation hc
                          LEFT JOIN ref_grade hg ON hg.id=hc.grade_id
                          LEFT JOIN ref_structure hs ON hs.id=hc.structure_id
                          LEFT JOIN ref_fonction hf ON hf.id=hc.fonction_id
                          LEFT JOIN ref_position_admin hp ON hp.id=hc.position_admin_id
                          WHERE hc.agent_id=a.id AND hc.id=(
                            SELECT last_c.id FROM carriere_situation last_c
                            LEFT JOIN sme_periode last_p ON last_p.id=last_c.sme_periode_id
                            WHERE last_c.agent_id=a.id
                            ORDER BY COALESCE(last_p.annee_mois,'0000-00') DESC,last_c.id DESC LIMIT 1
                          ) AND {_sql_for_like_match("UPPER(CONCAT_WS(' ', IF(hc.grade_id IS NOT NULL,hg.libelle,hc.grade_libelle), IF(hc.structure_id IS NOT NULL,hs.code,hc.structure_code), hs.designation, IF(hc.fonction_id IS NOT NULL,hf.libelle,hc.fonction_libelle), IF(hc.position_admin_id IS NOT NULL,hp.libelle,hc.position_admin_libelle)))")} LIKE %s
                        )
                      )
                    ORDER BY a.noms, a.id
                    """
                params = current_params + (needle,) * 6
                if limit is not None:
                    query += " LIMIT %s"
                    params += (max(1, int(limit)),)
                cur.execute(query, params)
                agent_ids = [int(row[0]) for row in cur.fetchall()]
                if not agent_ids:
                    return []

                placeholders = ", ".join(["%s"] * len(agent_ids))
                cur.execute(
                    f"""
                    SELECT
                      a.id,
                      COALESCE(c.id, a.id) AS carriere_id,
                      a.legacy_access_id,
                      a.noms,
                      a.date_naissance,
                      a.lieu_naissance,
                      a.sexe,
                      a.telephone,
                      a.adresse_residence,
                      IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle),
                      c.date_grade,
                      c.date_service,
                      c.ndecision,
                      a.matricule_militaire,
                      a.matricule_solde,
                      IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle),
                      IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle),
                      IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle),
                      c.diplome_libelle,
                      c.date_obtention,
                      IF(c.structure_id IS NOT NULL, rs.code, c.structure_code),
                      c.obs,
                      c.ordre_tri,
                      c.presseance,
                      p.annee_mois,
                      COALESCE(m.categorie_mouvement, m.type_mouvement),
                      COALESCE(m.motif, ''),
                      COALESCE(m.observ, ''),
                      mp.annee_mois
                    FROM agent a
                    LEFT JOIN carriere_situation c ON c.id = (
                      SELECT c_pick.id
                      FROM carriere_situation c_pick
                      LEFT JOIN sme_periode p_pick ON p_pick.id = c_pick.sme_periode_id
                      WHERE c_pick.agent_id = a.id
                      ORDER BY COALESCE(p_pick.annee_mois, '0000-00') DESC, c_pick.id DESC
                      LIMIT 1
                    )
                    LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                    LEFT JOIN ref_grade rg ON rg.id = c.grade_id
                    LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
                    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                    LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
                    LEFT JOIN mouvement_effectif m ON m.id = (
                      SELECT m_pick.id
                      FROM mouvement_effectif m_pick
                      LEFT JOIN sme_periode mp_pick ON mp_pick.id = m_pick.sme_periode_id
                      WHERE m_pick.agent_id = a.id
                        AND UPPER(COALESCE(
                          m_pick.categorie_mouvement, m_pick.type_mouvement, ''
                        )) = 'PERTE'
                      ORDER BY COALESCE(mp_pick.annee_mois, '0000-00') DESC, m_pick.id DESC
                      LIMIT 1
                    )
                    LEFT JOIN sme_periode mp ON mp.id = m.sme_periode_id
                    WHERE a.id IN ({placeholders})
                    ORDER BY a.noms
                    """,
                    tuple(agent_ids),
                )
                return [
                    (
                        int(row[0]),
                        _row_to_liste(row[1:24]),
                        str(row[24]).strip() if row[24] else None,
                        (row[25], row[26], row[27], row[28])
                        if row[25] is not None
                        else None,
                    )
                    for row in cur.fetchall()
                ]

    def fetch_all_at_mois(self, mois_ym: str) -> list[ListeGeneraleRow]:
        """Liste générale figée sur un mois SME (snapshot carrière, sans repli est_actuelle)."""
        from sme_gend.services.mysql_import_period import normalize_import_mois
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        raw = (mois_ym or "").strip()
        ym = canonical_sme_mois_yyyy_mm(raw) or normalize_import_mois(raw) if raw else ""
        if not ym:
            return self.fetch_all()
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql, params = _select_liste(ym, settings=self._settings, cursor=cur)
                cur.execute(f"{sql} ORDER BY c.ordre_tri, c.id", params)
                return [_row_to_liste(r) for r in cur.fetchall()]

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        return self.fetch_for_presence_mode("present")

    def fetch_for_presence_mode(self, mode: str = "present", *, mois_ym: str | None = None) -> list[ListeGeneraleRow]:
        """Charge uniquement les lignes utiles à la liste des présents (filtre SQL si possible)."""
        mode = (mode or "present").strip() or "present"
        where_sql, _ = _padmin_where_clause(mode)
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql, params = _select_liste(mois_ym if mois_ym is not None else self._scope_mois,
                                            settings=self._settings, cursor=cur)
                cur.execute(
                    f"{sql} WHERE {where_sql} "
                    "ORDER BY c.ordre_tri, c.presseance, a.noms, c.id",
                    params,
                )
                return [_row_to_liste(r) for r in cur.fetchall()]

    def fetch_distinct_padmin(self) -> list[str]:
        scope, scope_p = _carriere_scope_where(self._scope_mois, settings=self._settings)
        q = f"""
        SELECT DISTINCT position_admin_libelle FROM carriere_situation
        WHERE {scope} AND position_admin_libelle IS NOT NULL
          AND TRIM(position_admin_libelle) <> ''
        ORDER BY position_admin_libelle
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(q, scope_p)
                return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme(self) -> list[str]:
        scope, scope_p = _carriere_scope_where(self._scope_mois, settings=self._settings)
        q = f"""
        SELECT DISTINCT subdivision_arme_libelle FROM carriere_situation
        WHERE {scope} AND subdivision_arme_libelle IS NOT NULL
          AND TRIM(subdivision_arme_libelle) <> ''
        ORDER BY subdivision_arme_libelle
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(q, scope_p)
                return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme_for_structure(self, structure_code: str) -> list[str]:
        scope, scope_p = _carriere_scope_where(self._scope_mois, settings=self._settings)
        q = f"""
        SELECT DISTINCT subdivision_arme_libelle FROM carriere_situation
        WHERE {scope} AND structure_code = %s
          AND subdivision_arme_libelle IS NOT NULL AND TRIM(subdivision_arme_libelle) <> ''
        ORDER BY subdivision_arme_libelle
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(q, scope_p + ((structure_code or "").strip(),))
                return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def _resolve_agent_id(self, cur, row: ListeGeneraleRow) -> tuple[int, str | None]:
        cid = int(row.id) if row.id else None
        if cid:
            cur.execute(
                "SELECT agent_id, legacy_access_id FROM carriere_situation c "
                "JOIN agent a ON a.id = c.agent_id WHERE c.id = %s",
                (cid,),
            )
            ex = cur.fetchone()
            if ex:
                return int(ex[0]), ex[1]
        leg = (row.access_join_id or row.id or "").strip()
        if leg:
            cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
            ex2 = cur.fetchone()
            if ex2:
                return int(ex2[0]), leg
        return 0, leg or None

    @atomic_repository_write(row_arg="row")
    def update_row(self, row: ListeGeneraleRow, *, expected: ListeGeneraleRow | None = None) -> None:
        if expected is not None:
            from sme_gend.repositories.mysql_connection import mysql_import_transaction
            if expected.id != row.id:
                raise ValueError("Identifiant de fiche incohérent.")
            with mysql_import_transaction(self._settings):
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""SELECT a.id FROM agent a JOIN carriere_situation c
                            ON c.agent_id=a.id WHERE c.id=%s FOR UPDATE OF a""", (int(row.id),))
                        if cur.fetchone() is None:
                            raise ValueError("Fiche supprimée depuis son ouverture. Actualisez la liste.")
                if self.fetch_by_id(row.id) != expected:
                    raise ValueError("Cette fiche a été modifiée sur un autre poste. Actualisez avant d'enregistrer.")
                self._write_row(row)
            return
        self._write_row(row)

    def _write_row(self, row: ListeGeneraleRow) -> None:
        pk = int(row.id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cache = load_ref_cache_from_db(cur)
                flat = load_subdiv_flat_rows(cur)
                fields = prepare_carriere_write_fields(
                    cur, cache, row, flat_rows=flat, auto_create=True
                )
                cur.execute(
                    """
                    UPDATE agent a
                    INNER JOIN carriere_situation c ON c.agent_id = a.id AND c.id = %s
                    SET a.noms = %s, a.date_naissance = %s, a.lieu_naissance = %s, a.sexe = %s,
                        a.telephone = %s, a.adresse_residence = %s,
                        a.matricule_militaire = %s, a.matricule_solde = %s,
                        c.grade_libelle = %s, c.date_grade = %s, c.date_service = %s,
                        c.ndecision = %s, c.position_admin_libelle = %s,
                        c.fonction_libelle = %s, c.subdivision_arme_libelle = %s,
                        c.diplome_libelle = %s, c.date_obtention = %s,
                        c.structure_code = %s, c.obs = %s, c.ordre_tri = %s, c.presseance = %s,
                        c.structure_id = %s, c.grade_id = %s, c.position_admin_id = %s,
                        c.fonction_id = %s, c.subdivision_arme_id = %s
                    """,
                    (
                        pk,
                        row.noms,
                        row.date_naissance.date() if isinstance(row.date_naissance, datetime) else row.date_naissance,
                        row.lieu_naissance,
                        normalize_sexe(row.sexe),
                        (row.telephone or "").strip() or None,
                        (row.adresse_residence or "").strip() or None,
                        row.matricule_militaire,
                        row.matricule_solde,
                        fields["grade_libelle"],
                        row.date_grade.date() if isinstance(row.date_grade, datetime) else row.date_grade,
                        row.date_service,
                        row.ndecision,
                        fields["position_admin_libelle"],
                        fields["fonction_libelle"],
                        fields["subdivision_arme_libelle"],
                        row.diplome,
                        row.date_obtention,
                        fields["structure_code"],
                        row.obs,
                        row.ordre_tri,
                        row.presseance,
                        fields["structure_id"],
                        fields["grade_id"],
                        fields["position_admin_id"],
                        fields["fonction_id"],
                        fields["subdivision_arme_id"],
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"Aucune ligne mise à jour pour carriere id = {pk!r}")

    @atomic_repository_write()
    def insert_row(self, row: ListeGeneraleRow) -> str:
        from sme_gend.config.liste_generale_scope import resolve_mutation_view_mois
        from sme_gend.services.mysql_import_period import ensure_sme_periode

        ym = resolve_mutation_view_mois(self._settings)
        date_debut, date_fin = segment_dates_for_sme_mois(ym, open_segment=True)
        with self._connect() as conn:
            with conn.cursor() as cur:
                periode_id = ensure_sme_periode(conn, ym)
                cache = load_ref_cache_from_db(cur)
                flat = load_subdiv_flat_rows(cur)
                fields = prepare_carriere_write_fields(
                    cur, cache, row, flat_rows=flat, auto_create=True
                )
                cur.execute(
                    """
                    INSERT INTO agent (legacy_access_id, noms, date_naissance, lieu_naissance, sexe,
                        telephone, adresse_residence, matricule_militaire, matricule_solde)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        (row.access_join_id or "").strip() or new_internal_personnel_key(),
                        row.noms,
                        row.date_naissance.date() if isinstance(row.date_naissance, datetime) else row.date_naissance,
                        row.lieu_naissance,
                        normalize_sexe(row.sexe),
                        (row.telephone or "").strip() or None,
                        (row.adresse_residence or "").strip() or None,
                        row.matricule_militaire,
                        row.matricule_solde,
                    ),
                )
                aid = cur.lastrowid
                cur.execute(
                    """
                    INSERT INTO carriere_situation (
                        agent_id, sme_periode_id, est_actuelle, grade_libelle, date_grade, date_service,
                        ndecision, position_admin_libelle, fonction_libelle,
                        subdivision_arme_libelle, diplome_libelle, date_obtention,
                        structure_code, obs, ordre_tri, presseance,
                        structure_id, grade_id, position_admin_id, fonction_id, subdivision_arme_id,
                        date_debut, date_fin
                    ) VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid,
                        periode_id,
                        fields["grade_libelle"],
                        row.date_grade.date() if isinstance(row.date_grade, datetime) else row.date_grade,
                        row.date_service,
                        row.ndecision,
                        fields["position_admin_libelle"],
                        fields["fonction_libelle"],
                        fields["subdivision_arme_libelle"],
                        row.diplome,
                        row.date_obtention,
                        fields["structure_code"],
                        row.obs,
                        row.ordre_tri,
                        row.presseance,
                        fields["structure_id"],
                        fields["grade_id"],
                        fields["position_admin_id"],
                        fields["fonction_id"],
                        fields["subdivision_arme_id"],
                        date_debut,
                        date_fin,
                    ),
                )
                return str(int(cur.lastrowid))

    def _reopen_career_from_snapshot(
        self,
        cur,
        *,
        career_pk: int,
        agent_id: int,
        periode_id: int,
        row: ListeGeneraleRow,
        fields: dict,
        date_debut: date,
        date_fin: date | None,
    ) -> str:
        cur.execute(
            """
            UPDATE carriere_situation
            SET est_actuelle = 1,
                sme_periode_id = %s,
                grade_libelle = %s,
                date_grade = %s,
                date_service = %s,
                ndecision = %s,
                position_admin_libelle = %s,
                fonction_libelle = %s,
                subdivision_arme_libelle = %s,
                diplome_libelle = %s,
                date_obtention = %s,
                structure_code = %s,
                obs = %s,
                ordre_tri = %s,
                presseance = %s,
                structure_id = %s,
                grade_id = %s,
                position_admin_id = %s,
                fonction_id = %s,
                subdivision_arme_id = %s,
                date_debut = %s,
                date_fin = %s
            WHERE id = %s
            """,
            (
                periode_id,
                fields["grade_libelle"],
                row.date_grade.date()
                if isinstance(row.date_grade, datetime)
                else row.date_grade,
                row.date_service,
                row.ndecision,
                fields["position_admin_libelle"],
                fields["fonction_libelle"],
                fields["subdivision_arme_libelle"],
                row.diplome,
                row.date_obtention,
                fields["structure_code"],
                row.obs,
                row.ordre_tri,
                row.presseance,
                fields["structure_id"],
                fields["grade_id"],
                fields["position_admin_id"],
                fields["fonction_id"],
                fields["subdivision_arme_id"],
                date_debut,
                date_fin,
                career_pk,
            ),
        )
        cur.execute(
            """
            UPDATE carriere_situation SET est_actuelle = 0
            WHERE agent_id = %s AND id <> %s
            """,
            (agent_id, career_pk),
        )
        return str(career_pk)

    @atomic_repository_write()
    def restore_deleted_row(self, row: ListeGeneraleRow, *, deleted_career_id: str) -> str:
        """Réouvre ou recrée un segment carrière à partir d'un snapshot d'audit."""
        from sme_gend.config.liste_generale_scope import resolve_mutation_view_mois
        from sme_gend.services.mysql_import_period import ensure_sme_periode

        old_pk = int((deleted_career_id or row.id or "0").strip() or "0")
        if old_pk <= 0:
            raise ValueError("Identifiant carrière supprimé inconnu.")
        ym = resolve_mutation_view_mois(self._settings)
        date_debut, date_fin = segment_dates_for_sme_mois(ym, open_segment=True)
        with self._connect() as conn:
            with conn.cursor() as cur:
                periode_id = ensure_sme_periode(conn, ym)
                cache = load_ref_cache_from_db(cur)
                flat = load_subdiv_flat_rows(cur)
                fields = prepare_carriere_write_fields(
                    cur, cache, row, flat_rows=flat, auto_create=True
                )
                cur.execute(
                    "SELECT agent_id FROM carriere_situation WHERE id = %s",
                    (old_pk,),
                )
                existing_seg = cur.fetchone()
                if existing_seg:
                    agent_id = int(existing_seg[0])
                    return self._reopen_career_from_snapshot(
                        cur,
                        career_pk=old_pk,
                        agent_id=agent_id,
                        periode_id=periode_id,
                        row=row,
                        fields=fields,
                        date_debut=date_debut,
                        date_fin=date_fin,
                    )
                probe = ListeGeneraleRow(
                    id=str(old_pk),
                    noms=row.noms,
                    date_naissance=row.date_naissance,
                    lieu_naissance=row.lieu_naissance,
                    grade=row.grade,
                    date_grade=row.date_grade,
                    date_service=row.date_service,
                    ndecision=row.ndecision,
                    matricule_militaire=row.matricule_militaire,
                    matricule_solde=row.matricule_solde,
                    position_admin=row.position_admin,
                    fonction=row.fonction,
                    subdivision_arme=row.subdivision_arme,
                    diplome=row.diplome,
                    date_obtention=row.date_obtention,
                    structure=row.structure,
                    obs=row.obs,
                    ordre_tri=row.ordre_tri,
                    presseance=row.presseance,
                    access_join_id=row.access_join_id,
                    sexe=row.sexe,
                    telephone=row.telephone,
                    adresse_residence=row.adresse_residence,
                )
                agent_id, legacy = self._resolve_agent_id(cur, probe)
                if agent_id:
                    cur.execute(
                        """
                        SELECT id FROM carriere_situation
                        WHERE agent_id = %s AND sme_periode_id = %s
                        LIMIT 1
                        """,
                        (agent_id, periode_id),
                    )
                    periode_seg = cur.fetchone()
                    if periode_seg:
                        career_pk = int(periode_seg[0])
                        return self._reopen_career_from_snapshot(
                            cur,
                            career_pk=career_pk,
                            agent_id=agent_id,
                            periode_id=periode_id,
                            row=row,
                            fields=fields,
                            date_debut=date_debut,
                            date_fin=date_fin,
                        )
                if agent_id:
                    cur.execute(
                        """
                        UPDATE agent SET noms = %s, date_naissance = %s, lieu_naissance = %s, sexe = %s,
                            telephone = %s, adresse_residence = %s,
                            matricule_militaire = %s, matricule_solde = %s
                        WHERE id = %s
                        """,
                        (
                            row.noms,
                            row.date_naissance.date()
                            if isinstance(row.date_naissance, datetime)
                            else row.date_naissance,
                            row.lieu_naissance,
                            normalize_sexe(row.sexe),
                            (row.telephone or "").strip() or None,
                            (row.adresse_residence or "").strip() or None,
                            row.matricule_militaire,
                            row.matricule_solde,
                            agent_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO agent (legacy_access_id, noms, date_naissance, lieu_naissance, sexe,
                            telephone, adresse_residence, matricule_militaire, matricule_solde)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            legacy or (row.access_join_id or "").strip() or new_internal_personnel_key(),
                            row.noms,
                            row.date_naissance.date()
                            if isinstance(row.date_naissance, datetime)
                            else row.date_naissance,
                            row.lieu_naissance,
                            normalize_sexe(row.sexe),
                            (row.telephone or "").strip() or None,
                            (row.adresse_residence or "").strip() or None,
                            row.matricule_militaire,
                            row.matricule_solde,
                        ),
                    )
                    agent_id = int(cur.lastrowid)
                cur.execute(
                    """
                    INSERT INTO carriere_situation (
                        agent_id, sme_periode_id, est_actuelle, grade_libelle, date_grade, date_service,
                        ndecision, position_admin_libelle, fonction_libelle,
                        subdivision_arme_libelle, diplome_libelle, date_obtention,
                        structure_code, obs, ordre_tri, presseance,
                        structure_id, grade_id, position_admin_id, fonction_id, subdivision_arme_id,
                        date_debut, date_fin
                    ) VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        agent_id,
                        periode_id,
                        fields["grade_libelle"],
                        row.date_grade.date()
                        if isinstance(row.date_grade, datetime)
                        else row.date_grade,
                        row.date_service,
                        row.ndecision,
                        fields["position_admin_libelle"],
                        fields["fonction_libelle"],
                        fields["subdivision_arme_libelle"],
                        row.diplome,
                        row.date_obtention,
                        fields["structure_code"],
                        row.obs,
                        row.ordre_tri,
                        row.presseance,
                        fields["structure_id"],
                        fields["grade_id"],
                        fields["position_admin_id"],
                        fields["fonction_id"],
                        fields["subdivision_arme_id"],
                        date_debut,
                        date_fin,
                    ),
                )
                return str(int(cur.lastrowid))

    @atomic_repository_write(carriere_arg="row_id")
    def delete_row(self, row_id: str) -> None:
        pk = int(row_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT agent_id FROM carriere_situation WHERE id = %s", (pk,))
                ex = cur.fetchone()
                if not ex:
                    raise ValueError(f"Aucune carrière id = {pk!r}")
                cur.execute("DELETE FROM carriere_situation WHERE id = %s", (pk,))
                cur.execute(
                    "DELETE FROM agent WHERE id = %s AND NOT EXISTS "
                    "(SELECT 1 FROM carriere_situation c WHERE c.agent_id = %s)",
                    (ex[0], ex[0]),
                )

    @atomic_repository_write(row_arg="row")
    def delete_row_with_perte(
        self,
        row: ListeGeneraleRow,
        *,
        mois_ym: str,
        motif: str,
    ) -> None:
        """Enregistre la perte et supprime la carrière dans une transaction."""
        from sme_gend.repositories.gain_perte_repository import cdsme_from_mois_ym
        from sme_gend.services.mysql_access_numerique_import import (
            _clamp_mouvement_payload,
        )
        from sme_gend.models.gain_perte import observ_is_deces
        from sme_gend.services.mysql_import_period import (
            ensure_sme_periode,
            first_day_of_month,
            last_day_of_month,
            normalize_import_mois,
        )

        ym = normalize_import_mois(mois_ym)
        loss_reason = (motif or "").strip()
        if not loss_reason:
            raise ValueError("Le motif de perte est obligatoire.")
        ref_date = (
            first_day_of_month(ym) if observ_is_deces(loss_reason) else last_day_of_month(ym)
        )
        pk = int(row.id)
        cdsme = cdsme_from_mois_ym(ym)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.id, a.legacy_access_id
                    FROM carriere_situation c
                    INNER JOIN agent a ON a.id = c.agent_id
                    WHERE c.id = %s
                    """,
                    (pk,),
                )
                agent = cur.fetchone()
                if not agent:
                    raise ValueError(f"Aucune carrière id = {pk!r}")
                agent_id, legacy_id = int(agent[0]), str(agent[1] or "").strip()
                if not legacy_id:
                    raise ValueError("Identifiant personnel manquant pour enregistrer la perte.")
                periode_id = ensure_sme_periode(conn, ym)
                (
                    identifiant_ligne,
                    grade_snapshot,
                    nom_snapshot,
                    type_mouvement,
                    categorie_mouvement,
                    motif_code,
                    observation,
                    cdsme_derive,
                ) = _clamp_mouvement_payload(
                    objet="PERTE",
                    observ=loss_reason,
                    grade=row.grade,
                    nom=row.noms,
                    cdsme=cdsme,
                    leg=legacy_id,
                )
                cur.execute(
                    """
                    INSERT INTO mouvement_effectif (
                      sme_periode_id, agent_id, identifiant_ligne, grade_snapshot, nom_snapshot,
                      type_mouvement, categorie_mouvement, motif, cdsme_derive, observ,
                      date_reference
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        periode_id,
                        agent_id,
                        identifiant_ligne,
                        grade_snapshot,
                        nom_snapshot,
                        type_mouvement,
                        categorie_mouvement,
                        motif_code,
                        cdsme_derive,
                        observation,
                        ref_date,
                    ),
                )
                date_debut_seg = first_day_of_month(ym)
                cur.execute(
                    """
                    UPDATE carriere_situation
                    SET est_actuelle = 0,
                        sme_periode_id = COALESCE(sme_periode_id, %s),
                        date_debut = COALESCE(date_debut, %s),
                        date_fin = %s
                    WHERE id = %s
                    """,
                    (periode_id, date_debut_seg, ref_date, pk),
                )
                cur.execute(
                    "UPDATE carriere_situation SET est_actuelle = 0 WHERE agent_id = %s",
                    (agent_id,),
                )

    @atomic_repository_write()
    def backfill_structure_id_for_code(self, structure_code: str) -> int:
        """Renseigne structure_id pour les carrières du scope courant."""
        sc = (structure_code or "").strip()
        if not sc:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                scope, scope_p = carriere_write_scope_for_settings(self._settings, cursor=cur)
                cur.execute(
                    f"""
                    UPDATE carriere_situation c
                    INNER JOIN ref_structure rs ON UPPER(TRIM(rs.code)) = UPPER(%s)
                    SET c.structure_id = rs.id
                    WHERE {scope}
                      AND (c.structure_id IS NULL OR c.structure_id <> rs.id)
                      AND (UPPER(TRIM(c.structure_code)) = UPPER(%s) OR c.structure_id = rs.id)
                    """,
                    (sc,) + scope_p + (sc,),
                )
                return int(cur.rowcount or 0)

    @atomic_repository_write()
    def update_structure_column(self, old_code: str, new_code: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                scope, scope_p = carriere_write_scope_for_settings(self._settings, cursor=cur)
                cur.execute(
                    f"""
                    UPDATE carriere_situation c SET c.structure_code = %s
                    WHERE {scope} AND c.structure_code = %s
                    """,
                    ((new_code or "").strip(),) + scope_p + ((old_code or "").strip(),),
                )
                n = int(cur.rowcount or 0)
        self.backfill_structure_id_for_code((new_code or "").strip())
        return n

    @atomic_repository_write()
    def rename_subarme_for_structure(
        self, structure_code: str, old_label: str, new_label: str, *, mois_ym: str | None = None
    ) -> int:
        sc = (structure_code or "").strip()
        old_l = (old_label or "").strip()
        new_l = (new_label or "").strip()
        new_val = None if new_l in ("", "(Sans subdivision)") else new_l
        if mois_ym is not None and not mois_ym.strip():
            raise ValueError('Choisissez un mois SME avant de renommer une subdivision.')
        with self._connect() as conn:
            with conn.cursor() as cur:
                if mois_ym is None:
                    scope, scope_p = carriere_write_scope_for_settings(self._settings, cursor=cur)
                else:
                    join, scope_p = _carriere_join_sql(mois_ym, settings=self._settings, cursor=cur)
                    scope = ('c.id IN (SELECT scoped.id FROM (SELECT DISTINCT c.id '
                             f'FROM agent a {join}) AS scoped)')
                cur.execute("SELECT rsa.id FROM ref_subdivision_arme rsa JOIN ref_structure rs ON rs.id=rsa.structure_id "
                            "WHERE BINARY rsa.libelle=BINARY %s AND BINARY rs.code=BINARY %s ORDER BY rsa.id LIMIT 2", (new_val, sc))
                matches = cur.fetchall()
                target_id = matches[0][0] if len(matches) == 1 and new_val is not None else None
                # Match the same resolved labels as the list, including FK-backed rows.
                visible_structure = "IF(c.structure_id IS NOT NULL, rs.code, c.structure_code)"
                visible_subdivision = "IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle)"
                update = """UPDATE carriere_situation c
                    LEFT JOIN ref_structure rs ON rs.id=c.structure_id
                    LEFT JOIN ref_subdivision_arme rsa ON rsa.id=c.subdivision_arme_id
                    SET c.subdivision_arme_libelle=%s, c.subdivision_arme_id=%s"""
                if old_l in ("", "(Sans subdivision)"):
                    cur.execute(
                        f"{update} WHERE {scope} AND BINARY {visible_structure}=BINARY %s "
                        f"AND ({visible_subdivision} IS NULL OR TRIM({visible_subdivision})='')",
                        (new_val, target_id) + scope_p + (sc,),
                    )
                else:
                    cur.execute(
                        f"{update} WHERE {scope} AND BINARY {visible_structure}=BINARY %s "
                        f"AND BINARY TRIM({visible_subdivision})=BINARY %s",
                        (new_val, target_id) + scope_p + (sc, old_l),
                    )
                return int(cur.rowcount or 0)

    def bulk_import_from_access_rows(
        self,
        rows: list[ListeGeneraleRow],
        *,
        sme_periode_id: int,
        import_lot_id: int,
        mois: str,
        historical: bool = False,
        ref_cache: RefEtlCache | None = None,
        prep_stats: ListeGeneraleImportStats | None = None,
        detail_progress: Callable[[str], None] | None = None,
    ) -> tuple[int, int, ListeGeneraleImportStats]:
        """Upsert agent + snapshot carrière pour la période ``mois`` (``YYYY-MM``)."""
        from sme_gend.repositories.mysql_connection import has_import_transaction, mysql_import_transaction
        if not has_import_transaction(self._settings):
            # The monthly observation and its optional membership overlay commit
            # together, including when this repository is called by an Excel import.
            with mysql_import_transaction(self._settings):
                return self.bulk_import_from_access_rows(rows, sme_periode_id=sme_periode_id,
                    import_lot_id=import_lot_id, mois=mois, historical=historical,
                    ref_cache=ref_cache, prep_stats=prep_stats, detail_progress=detail_progress)
        inserted = 0
        updated = 0
        stats = prep_stats or ListeGeneraleImportStats(rows_read=len(rows))
        stats.inserted = 0
        stats.updated = 0
        stats.matricule_merged = 0
        stats.ids_ignored = 0

        matricule_counts: dict[str, int] = {}
        for r in rows:
            mat = (r.matricule_militaire or "").strip()
            if mat:
                matricule_counts[mat] = matricule_counts.get(mat, 0) + 1
        stats.matricule_duplicates_kept = sum(
            max(0, c - 1) for c in matricule_counts.values()
        )

        debut = first_day_of_month(mois)
        fin = last_day_of_month(mois)
        date_fin_val = fin if historical else None
        carriere_vals = (
            "grade_libelle=%s, date_grade=%s, date_service=%s, ndecision=%s, "
            "position_admin_libelle=%s, fonction_libelle=%s, subdivision_arme_libelle=%s, "
            "diplome_libelle=%s, date_obtention=%s, structure_code=%s, obs=%s, "
            "ordre_tri=%s, presseance=%s, structure_id=%s, grade_id=%s, "
            "position_admin_id=%s, fonction_id=%s, subdivision_arme_id=%s, "
            "import_lot_id=%s, date_debut=%s, date_fin=%s"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Recharger les libellés une seule fois évite les SELECT de
                # référentiel exécutés pour chaque agent.
                cache = load_ref_cache_from_db(cur)
                if ref_cache is not None:
                    for cache_name in (
                        "structure_by_code",
                        "structure_code_by_id",
                        "grade_by_code",
                        "grade_by_libelle",
                        "grade_libelle_by_id",
                        "padmin_by_libelle",
                        "padmin_libelle_by_id",
                        "fonction_by_libelle",
                        "fonction_libelle_by_id",
                        "leaf_node_by_design_t",
                        "leaf_node_by_flat_key",
                        "subdiv_arme_by_struct_leaf",
                        "subdiv_arme_libelle_by_id",
                        "periode_by_ym",
                        "misc_node_ids",
                    ):
                        getattr(cache, cache_name).update(getattr(ref_cache, cache_name))
                flat_rows = load_subdiv_flat_rows(cur)
                cur.execute("SELECT id, legacy_access_id, matricule_militaire FROM agent")
                agent_records: dict[int, tuple[str, str]] = {}
                agent_by_legacy: dict[str, int] = {}
                agent_ids_by_matricule: dict[str, list[int]] = {}

                def track_agent(agent_id: int, legacy_id: str, matricule: str) -> None:
                    previous = agent_records.get(agent_id)
                    if previous is not None:
                        old_legacy, old_mat = previous
                        old_legacy_key = old_legacy.casefold()
                        if old_legacy and agent_by_legacy.get(old_legacy_key) == agent_id:
                            del agent_by_legacy[old_legacy_key]
                        old_hits = agent_ids_by_matricule.get(old_mat.casefold())
                        if old_hits and agent_id in old_hits:
                            old_hits.remove(agent_id)
                            if not old_hits:
                                del agent_ids_by_matricule[old_mat.casefold()]
                    agent_records[agent_id] = (legacy_id, matricule)
                    if legacy_id:
                        agent_by_legacy[legacy_id.casefold()] = agent_id
                    if matricule:
                        agent_ids_by_matricule.setdefault(matricule.casefold(), []).append(agent_id)

                for agent_id, legacy_id, matricule in cur.fetchall():
                    track_agent(
                        int(agent_id),
                        str(legacy_id or "").strip(),
                        str(matricule or "").strip(),
                    )

                cur.execute(
                    "SELECT id, agent_id FROM carriere_situation WHERE sme_periode_id = %s",
                    (sme_periode_id,),
                )
                career_id_by_agent = {
                    int(agent_id): int(career_id)
                    for career_id, agent_id in cur.fetchall()
                }
                from sme_gend.services.agent_identity_alias import load_identity_aliases
                identity_aliases = load_identity_aliases(cur)
                for old_key, (canonical_id, _) in identity_aliases.items():
                    agent_by_legacy[old_key] = canonical_id
                if identity_aliases:
                    targets = sorted({value[0] for value in identity_aliases.values()})
                    marks = ",".join(["%s"] * len(targets))
                    cur.execute("SELECT id,noms,date_naissance,matricule_militaire,matricule_solde FROM agent WHERE id IN (" + marks + ")", tuple(targets))
                    canonical_profiles = {int(row[0]): row[1:] for row in cur.fetchall()}
                    seen_alias_targets = {}
                    for incoming in rows:
                        key = str(incoming.id or "").strip().casefold()
                        owner = agent_by_legacy.get(key)
                        if owner in targets:
                            previous = seen_alias_targets.setdefault(owner, key)
                            if previous != key:
                                raise ValueError("Plusieurs identifiants rapprochés figurent dans le même import. Contrôlez les doublons avant de réimporter.")
                        if key in identity_aliases:
                            expected = canonical_profiles[identity_aliases[key][0]]
                            birth = incoming.date_naissance.date() if isinstance(incoming.date_naissance, datetime) else incoming.date_naissance
                            from sme_gend.services.personnel_identity_assessment import identity_value
                            for field, actual, kept in zip(("noms", "date_naissance", "matricule_militaire", "matricule_solde"),
                                                          (incoming.noms, birth, incoming.matricule_militaire, incoming.matricule_solde), expected):
                                a, b = identity_value(field, actual), identity_value(field, kept)
                                if a and b and a != b:
                                    raise ValueError("L'ancien identifiant rapproché " + str(incoming.id) + " porte une identité différente de la fiche conservée. Import annulé ; vérifiez le fichier source.")
                imported_agent_ids: set[int] = set()
                report_every = max(1, len(rows) // 100)
                for row_index, r in enumerate(rows, start=1):
                    leg = str(r.id or "").strip()
                    if not leg:
                        stats.ids_ignored += 1
                        if detail_progress and (
                            row_index % report_every == 0 or row_index == len(rows)
                        ):
                            detail_progress(
                                f"Liste générale — {row_index}/{len(rows)} agents traités"
                            )
                        continue
                    agent_id = agent_by_legacy.get(leg.casefold())
                    # Réimports / Id Access vide : rattacher via matricule à l'agent
                    # déjà connu (Id Access réel prioritaire, sinon ancien GEN-).
                    if agent_id is None and leg.startswith("GEN-"):
                        mat = (r.matricule_militaire or "").strip()
                        # Ignorer les matricules factices (/, -, …) : trop d'agents
                        # partagent ces valeurs → mauvaise fusion.
                        mat_ok = bool(mat) and mat not in {"/", "-", ".", "?", "0", "N/A", "NA"}
                        if mat_ok:
                            mat_hits = sorted(
                                agent_ids_by_matricule.get(mat.casefold(), []),
                                key=lambda hit: (
                                    not bool(agent_records[hit][0])
                                    or agent_records[hit][0].startswith("GEN-"),
                                    hit,
                                ),
                            )[:2]
                            # Fusion seulement si un seul agent cible crédible
                            # (évite d'écraser deux personnes au même matricule).
                            if len(mat_hits) == 1:
                                aid_reuse = mat_hits[0]
                                old_leg = agent_records[aid_reuse][0]
                                if old_leg.startswith("GEN-") and old_leg != leg:
                                    clash = agent_by_legacy.get(leg.casefold())
                                    if clash is not None:
                                        aid_reuse = clash
                                    else:
                                        cur.execute(
                                            "UPDATE agent SET legacy_access_id = %s WHERE id = %s",
                                            (leg, aid_reuse),
                                        )
                                        track_agent(aid_reuse, leg, agent_records[aid_reuse][1])
                                agent_id = aid_reuse
                                stats.matricule_merged += 1
                            elif len(mat_hits) > 1:
                                # Plusieurs agents : préférer l'Id Access réel unique
                                reals = [
                                    hit for hit in mat_hits
                                    if agent_records[hit][0]
                                    and not agent_records[hit][0].startswith("GEN-")
                                ]
                                if len(reals) == 1:
                                    agent_id = reals[0]
                                    stats.matricule_merged += 1
                    dn = r.date_naissance.date() if isinstance(r.date_naissance, datetime) else r.date_naissance
                    dg = r.date_grade.date() if isinstance(r.date_grade, datetime) else r.date_grade
                    # Toujours 0 à l'upsert : refresh_est_actuelle_flags en fin de bundle.
                    est = 0
                    if agent_id is not None:
                        aid = agent_id
                        if not historical and leg.casefold() not in identity_aliases:
                            cur.execute(
                                """
                                UPDATE agent SET noms=%s, date_naissance=%s, lieu_naissance=%s,
                                    sexe=CASE WHEN %s THEN %s ELSE sexe END,
                                    telephone=CASE WHEN %s THEN %s ELSE telephone END,
                                    adresse_residence=CASE WHEN %s THEN %s ELSE adresse_residence END,
                                    matricule_militaire=%s, matricule_solde=%s WHERE id=%s
                                """,
                                (
                                    r.noms,
                                    dn,
                                    r.lieu_naissance,
                                    profile_field_supplied(r, "sexe"), normalize_sexe(r.sexe),
                                    profile_field_supplied(r, "telephone"), (r.telephone or "").strip() or None,
                                    profile_field_supplied(r, "adresse_residence"), (r.adresse_residence or "").strip() or None,
                                    r.matricule_militaire,
                                    r.matricule_solde,
                                    aid,
                                ),
                            )
                            track_agent(
                                aid,
                                agent_records.get(aid, (leg, ""))[0],
                                (r.matricule_militaire or "").strip(),
                            )
                        car_ex_id = career_id_by_agent.get(aid)
                        fields = prepare_carriere_write_fields(
                            cur, cache, r, flat_rows=flat_rows, auto_create=True
                        )
                        params = (
                            fields["grade_libelle"],
                            dg,
                            r.date_service,
                            r.ndecision,
                            fields["position_admin_libelle"],
                            fields["fonction_libelle"],
                            fields["subdivision_arme_libelle"],
                            r.diplome,
                            r.date_obtention,
                            fields["structure_code"],
                            r.obs,
                            r.ordre_tri,
                            r.presseance,
                            fields["structure_id"],
                            fields["grade_id"],
                            fields["position_admin_id"],
                            fields["fonction_id"],
                            fields["subdivision_arme_id"],
                            import_lot_id,
                            debut,
                            date_fin_val,
                        )
                        if car_ex_id is not None:
                            cur.execute(
                                f"UPDATE carriere_situation SET {carriere_vals} WHERE id=%s",
                                params + (car_ex_id,),
                            )
                        else:
                            cur.execute(
                                """
                                INSERT INTO carriere_situation (
                                  agent_id, est_actuelle, sme_periode_id, import_lot_id,
                                  grade_libelle, date_grade, date_service, ndecision,
                                  position_admin_libelle, fonction_libelle,
                                  subdivision_arme_libelle, diplome_libelle, date_obtention,
                                  structure_code, obs, ordre_tri, presseance,
                                  structure_id, grade_id, position_admin_id, fonction_id,
                                  subdivision_arme_id, date_debut, date_fin
                                ) VALUES (
                                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                  %s,%s,%s,%s,%s,%s,%s
                                )
                                """,
                                (
                                    aid,
                                    est,
                                    sme_periode_id,
                                    import_lot_id,
                                    fields["grade_libelle"],
                                    dg,
                                    r.date_service,
                                    r.ndecision,
                                    fields["position_admin_libelle"],
                                    fields["fonction_libelle"],
                                    fields["subdivision_arme_libelle"],
                                    r.diplome,
                                    r.date_obtention,
                                    fields["structure_code"],
                                    r.obs,
                                    r.ordre_tri,
                                    r.presseance,
                                    fields["structure_id"],
                                    fields["grade_id"],
                                    fields["position_admin_id"],
                                    fields["fonction_id"],
                                    fields["subdivision_arme_id"],
                                    debut,
                                    date_fin_val,
                                ),
                            )
                            career_id_by_agent[aid] = int(cur.lastrowid)
                        updated += 1
                        imported_agent_ids.add(aid)
                    else:
                        cur.execute(
                            """
                            INSERT INTO agent (legacy_access_id, noms, date_naissance, lieu_naissance, sexe,
                              telephone, adresse_residence, matricule_militaire, matricule_solde)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            """,
                            (
                                leg,
                                r.noms,
                                dn,
                                r.lieu_naissance,
                                normalize_sexe(r.sexe),
                                (r.telephone or "").strip() or None,
                                (r.adresse_residence or "").strip() or None,
                                r.matricule_militaire,
                                r.matricule_solde,
                            ),
                        )
                        aid = cur.lastrowid
                        track_agent(aid, leg, (r.matricule_militaire or "").strip())
                        fields = prepare_carriere_write_fields(
                            cur, cache, r, flat_rows=flat_rows, auto_create=True
                        )
                        cur.execute(
                            """
                            INSERT INTO carriere_situation (
                              agent_id, est_actuelle, sme_periode_id, import_lot_id,
                              grade_libelle, date_grade, date_service, ndecision,
                              position_admin_libelle, fonction_libelle,
                              subdivision_arme_libelle, diplome_libelle, date_obtention,
                              structure_code, obs, ordre_tri, presseance,
                              structure_id, grade_id, position_admin_id, fonction_id,
                              subdivision_arme_id, date_debut, date_fin
                            ) VALUES (
                              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              %s,%s,%s,%s,%s,%s,%s
                            )
                            """,
                            (
                                aid,
                                est,
                                sme_periode_id,
                                import_lot_id,
                                fields["grade_libelle"],
                                dg,
                                r.date_service,
                                r.ndecision,
                                fields["position_admin_libelle"],
                                fields["fonction_libelle"],
                                fields["subdivision_arme_libelle"],
                                r.diplome,
                                r.date_obtention,
                                fields["structure_code"],
                                r.obs,
                                r.ordre_tri,
                                r.presseance,
                                fields["structure_id"],
                                fields["grade_id"],
                                fields["position_admin_id"],
                                fields["fonction_id"],
                                fields["subdivision_arme_id"],
                                debut,
                                date_fin_val,
                            ),
                        )
                        career_id_by_agent[int(aid)] = int(cur.lastrowid)
                        inserted += 1
                        imported_agent_ids.add(int(aid))
                    if detail_progress and (
                        row_index % report_every == 0 or row_index == len(rows)
                    ):
                        detail_progress(
                            f"Liste générale — {row_index}/{len(rows)} agents traités"
                        )
                # Sync strict du mois : miroir Access — retire les carrières
                # d'agents non présents dans ce lot (y compris Id Access réels).
                if imported_agent_ids:
                    placeholders = ", ".join(["%s"] * len(imported_agent_ids))
                    cur.execute(
                        f"""
                        DELETE FROM carriere_situation
                        WHERE sme_periode_id = %s
                          AND agent_id NOT IN ({placeholders})
                        """,
                        (sme_periode_id, *sorted(imported_agent_ids)),
                    )
                elif not rows:
                    # Une liste Access valide mais vide est aussi un miroir :
                    # ne pas laisser les situations issues d'une reprise manuelle.
                    cur.execute("DELETE FROM carriere_situation WHERE sme_periode_id=%s", (sme_periode_id,))
                from sme_gend.services.mysql_import_period import dedupe_carriere_for_periode

                dedupe_carriere_for_periode(conn, sme_periode_id)
                if membership_projection_enabled(self._settings, cursor=cur):
                    from sme_gend.services.membership_projection import rebuild_imported_membership
                    rebuild_imported_membership(self._settings, mois, [sme_periode_id])
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT agent_id)
                    FROM carriere_situation
                    WHERE sme_periode_id = %s
                    """,
                    (sme_periode_id,),
                )
                row = cur.fetchone()
                stats.agents_distinct = int(row[0] or 0) if row else 0
        stats.inserted = inserted
        stats.updated = updated
        return inserted, updated, stats

    def link_satellite_agent_ids(self) -> None:
        """No-op : satellites dans agent_mission / agent_malade / parcours_stage."""
        return
