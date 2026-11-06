"""Référentiels et satellites — MySQL (lecture ``ref_*`` / ``sme_periode``, écriture ref + miroir tab)."""

from __future__ import annotations

from sme_gend.services.shared_reference_write import (
    shared_reference_write, mark_reference_write, shared_data_write, mark_shared_data_write,
)

import logging
from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow, periode_ym_for_mouvement
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.models.sme_row import SmeRow
from sme_gend.models.structure import StructureRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.config.liste_generale_scope import (
    carriere_scope_for_settings, carriere_write_scope_for_settings, resolve_liste_view_mois,
)
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.repositories.reference_write_guard import (
    ReferenceConflictError, reference_revision_sql, require_reference_token, check_reference_target, insert_reference,
)
from sme_gend.repositories.gain_perte_write_guard import (
    MOVEMENT_REVISION_SQL, GainPerteConflictError, require_movement_token,
    check_movement_target, require_period_open, require_movement_period_open,
    require_consistent_movement_month,
)
from sme_gend.services.mysql_ref_etl import cdsme_to_annee_mois, load_subdiv_flat_rows

_log = logging.getLogger(__name__)


def _norm_id(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _movement_reference_date(row: GainPerteRow) -> date | None:
    """Date enregistrée : saisie explicite, sinon dernier jour du mois SME."""
    from datetime import date as date_cls

    from sme_gend.services.mysql_import_period import last_day_of_month

    raw = (row.date_reference or "").strip()
    if raw:
        try:
            return date_cls.fromisoformat(raw[:10])
        except ValueError:
            pass
    ym = periode_ym_for_mouvement(row) or ""
    if ym:
        return last_day_of_month(ym)
    return None


def _gain_row_from_mouvement(r: tuple) -> GainPerteRow:
    dr = r[7] if len(r) > 7 else None
    date_str: str | None = None
    if dr is not None:
        if hasattr(dr, "isoformat"):
            date_str = dr.isoformat()[:10]
        else:
            date_str = str(dr).strip()[:10] or None
    mid: int | None = None
    if len(r) > 8 and r[8] is not None:
        try:
            mid = int(r[8])
        except (TypeError, ValueError):
            mid = None
    return GainPerteRow(
        id=str(r[0] or "").strip(),
        grade=_str(r[1]),
        nom=_str(r[2]),
        objet=_str(r[3]),
        cdsme=_str(r[4]),
        observ=_str(r[5]),
        mois_ym=_str(r[6]),
        date_reference=date_str,
        movement_id=mid,
        write_token=(mid, str(r[9])) if mid is not None and len(r) > 9 and r[9] is not None else None,
    )


class MySQLGradeRepository(BaseMySQLRepository):
    def fetch_all(self) -> list[GradeRefRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT code, libelle, pref_ord, effectif_g, {reference_revision_sql('grade')} FROM ref_grade r "
                    "ORDER BY pref_ord, code"
                )
                return [GradeRefRow.from_sequence(r) for r in cur.fetchall()]

    def insert(
        self, code: str, libelle: str, pref_ord: int | None, effectif_g: int | None
    ) -> None:
        from sme_gend.services.reference_policy import grade_values
        c, lb, pref_ord, effectif_g = grade_values(code, libelle, pref_ord, effectif_g)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    insert_reference(cur, "grade",
                        """
                        INSERT INTO ref_grade (code, libelle, pref_ord, effectif_g)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (c, lb, pref_ord, effectif_g),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="grade_create",module="reference",entity_id=c,before=None,after=dict(code=c,libelle=lb,pref_ord=pref_ord,effectif_g=effectif_g))

    def update(
        self, code: str, libelle: str, pref_ord: int | None, effectif_g: int | None,
        *, expected: GradeRefRow | None = None,
    ) -> None:
        from sme_gend.services.reference_policy import grade_values
        c, lb, pref_ord, effectif_g = grade_values(code, libelle, pref_ord, effectif_g)
        revision = require_reference_token("grade", c, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "grade", c, revision)
                    cur.execute(
                        """
                        UPDATE ref_grade SET libelle=%s, pref_ord=%s, effectif_g=%s WHERE code=%s
                        """,
                        (lb, pref_ord, effectif_g, c),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="grade_update",module="reference",entity_id=c,before=expected,after=dict(code=c,libelle=lb,pref_ord=pref_ord,effectif_g=effectif_g))

    def delete(self, code: str, *, expected: GradeRefRow | None = None) -> None:
        c = code.strip()
        revision = require_reference_token("grade", c, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "grade", c, revision)
                    cur.execute("DELETE FROM ref_grade WHERE code = %s", (c,))
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="grade_delete",module="reference",entity_id=c,before=expected,after=None)
                        from sme_gend.services.grade_media import remove_association
                        remove_association(self._settings, c)


class MySQLStructureRepository(BaseMySQLRepository):
    def fetch_all(self) -> list[StructureRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT code, designation, ordre_tri, famille_unite, {reference_revision_sql('structure')} FROM ref_structure r "
                    "ORDER BY ordre_tri, code"
                )
                return [StructureRow.from_sequence(r) for r in cur.fetchall()]

    def fetch_by_code(self, code: str) -> StructureRow | None:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT code, designation, ordre_tri, famille_unite, {reference_revision_sql('structure')} FROM ref_structure r WHERE code = %s",
                    (code.strip(),),
                )
                r = cur.fetchone()
                return StructureRow.from_sequence(r) if r else None

    def insert(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
    ) -> None:
        c = code.strip()
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    insert_reference(cur, "structure",
                        """
                        INSERT INTO ref_structure (code, designation, ordre_tri, famille_unite)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (c, designation.strip(), ordre_tri, famille_unite),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="structure_create",module="reference",entity_id=c,before=None,after=dict(code=c,designation=designation,ordre_tri=ordre_tri,famille_unite=famille_unite))

    def update(
        self,
        code: str,
        designation: str,
        ordre_tri: int | None,
        *,
        famille_unite: str | None = None,
        expected: StructureRow | None = None,
    ) -> None:
        c = code.strip()
        revision = require_reference_token("structure", c, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "structure", c, revision)
                    cur.execute(
                        """
                        UPDATE ref_structure
                        SET designation=%s, ordre_tri=%s, famille_unite=%s
                        WHERE code=%s
                        """,
                        (designation.strip(), ordre_tri, famille_unite, c),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="structure_update",module="reference",entity_id=c,before=expected,after=dict(code=c,designation=designation,ordre_tri=ordre_tri,famille_unite=famille_unite))

    def delete(self, code: str, *, expected: StructureRow | None = None) -> None:
        c = code.strip()
        revision = require_reference_token("structure", c, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "structure", c, revision)
                    cur.execute("DELETE FROM ref_structure WHERE code = %s", (c,))
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="structure_delete",module="reference",entity_id=c,before=expected,after=None)

    def rename_code(self, old_code: str, new_code: str, *, expected: StructureRow | None = None) -> None:
        o, n = old_code.strip(), new_code.strip()
        revision = require_reference_token("structure", o, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "structure", o, revision)
                    cur.execute(
                        "UPDATE ref_structure SET code = %s WHERE code = %s", (n, o)
                    )
                    mark_reference_write(cur.rowcount > 0)


class MySQLSmeRepository(BaseMySQLRepository):
    def table_exists(self) -> bool:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name = 'sme_periode' LIMIT 1
                    """
                )
                return cur.fetchone() is not None

    def fetch_all(self) -> list[SmeRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(TRIM(codsme), ''), annee_mois), annee_mois, statut
                    FROM sme_periode ORDER BY annee_mois
                    """
                )
                return [SmeRow.from_sequence(r) for r in cur.fetchall()]

    def insert(self, codsme: str, mois: str, statut: str) -> None:
        from sme_gend.services.mysql_import_period import ensure_sme_periode
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        ym = canonical_sme_mois_yyyy_mm(mois) or mois.strip()
        from sme_gend.services.active_month_guard import period_state_write
        with period_state_write(self._settings,reason="period_status"):
            with self._connect() as conn:
                ensure_sme_periode(
                    conn, ym, codsme=codsme.strip() or None, statut=statut.strip() or None
                )

    def update(self, codsme: str, mois: str, statut: str) -> None:
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        ym = canonical_sme_mois_yyyy_mm(mois) or mois.strip()
        key = (codsme or "").strip()
        from sme_gend.services.active_month_guard import period_state_write
        with period_state_write(self._settings,reason="period_status"):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE sme_periode SET statut=%s, codsme=COALESCE(NULLIF(%s,''), codsme)
                        WHERE codsme=%s OR annee_mois=%s
                        """,
                        (statut, key, key, ym),
                    )

    def delete(self, codsme: str) -> None:
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        key = (codsme or "").strip()
        ym = canonical_sme_mois_yyyy_mm(key)
        from sme_gend.services.active_month_guard import period_state_write
        with period_state_write(self._settings,reason="period_status"):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    if ym:
                        cur.execute("DELETE FROM sme_periode WHERE annee_mois=%s", (ym,))


class MySQLGainPerteRepository(BaseMySQLRepository):
    _SELECT_MVT = f"""
        SELECT a.legacy_access_id, m.grade_snapshot, m.nom_snapshot,
               COALESCE(m.categorie_mouvement, m.type_mouvement),
               m.cdsme_derive,
               COALESCE(NULLIF(TRIM(m.observ), ''), m.motif),
               p.annee_mois, m.date_reference, m.id, {MOVEMENT_REVISION_SQL}
        FROM mouvement_effectif m
        INNER JOIN agent a ON a.id = m.agent_id
        LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
    """

    def fetch_all(self) -> list[GainPerteRow]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._SELECT_MVT + " ORDER BY m.date_reference, m.id")
                return [_gain_row_from_mouvement(r) for r in cur.fetchall()]

    def fetch_for_cdsme(self, cdsme: str) -> list[GainPerteRow]:
        ym = cdsme_to_annee_mois(cdsme)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ym:
                    cur.execute(
                        self._SELECT_MVT
                        + """
                        WHERE p.annee_mois = %s
                           OR (p.id IS NULL AND m.cdsme_derive = %s)
                        ORDER BY m.id
                        """,
                        (ym, cdsme.strip()),
                    )
                else:
                    cur.execute(
                        self._SELECT_MVT + " WHERE m.cdsme_derive = %s ORDER BY m.id",
                        (cdsme.strip(),),
                    )
                return [_gain_row_from_mouvement(r) for r in cur.fetchall()]

    def fetch_distinct_months(self) -> list[str]:
        """Mois disponibles : périodes SME + mouvements (Cdsme inclus)."""
        return self.fetch_month_catalog()

    def fetch_month_catalog(self) -> list[str]:
        """Catalogue YYYY-MM : ``sme_periode`` + mouvements effectif."""
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT DISTINCT ym FROM (
                            SELECT TRIM(p.annee_mois) AS ym
                            FROM sme_periode p
                            WHERE p.annee_mois IS NOT NULL
                              AND TRIM(p.annee_mois) <> ''
                            UNION
                            SELECT TRIM(p.annee_mois) AS ym
                            FROM mouvement_effectif m
                            INNER JOIN sme_periode p ON p.id = m.sme_periode_id
                            WHERE p.annee_mois IS NOT NULL
                              AND TRIM(p.annee_mois) <> ''
                            UNION
                            SELECT CONCAT(
                                SUBSTRING(m.cdsme_derive, 3, 4), '-',
                                LPAD(SUBSTRING(m.cdsme_derive, 1, 2), 2, '0')
                            ) AS ym
                            FROM mouvement_effectif m
                            WHERE m.cdsme_derive REGEXP '^[0-9]{6}$'
                        ) AS catalog
                        WHERE ym REGEXP '^[0-9]{4}-[0-9]{2}$'
                        ORDER BY ym DESC
                    """
                )
                return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def _resolve_agent_and_periode(
        self, cur, row: GainPerteRow
    ) -> tuple[int | None, int | None]:
        leg = str(row.id or "").strip()
        cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
        ag = cur.fetchone()
        if not ag:
            return None, None
        agent_id = int(ag[0])
        ym = periode_ym_for_mouvement(row)
        pid = None
        if ym:
            cur.execute("SELECT id FROM sme_periode WHERE annee_mois = %s", (ym,))
            pr = cur.fetchone()
            if pr:
                pid = int(pr[0])
        return agent_id, pid

    def insert_row(self, row: GainPerteRow) -> None:
        ym_target, cdsme_target = require_consistent_movement_month(row)
        with shared_data_write(self._settings, reason="gain_perte", mois=ym_target):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    agent_id, pid = self._resolve_agent_and_periode(cur, row)
                    if agent_id is None or pid is None:
                        ym_hint = periode_ym_for_mouvement(row) or "?"
                        raise ValueError(
                            "Agent ou période SME introuvable pour ce mouvement "
                            f"(mois SME « {ym_hint} » absent de sme_periode ?)."
                        )
                    require_period_open(cur, ym_target)
                    ref_date = _movement_reference_date(row)
                    from sme_gend.services.mysql_access_numerique_import import _clamp_mouvement_payload

                    leg_s, grade_s, nom_s, type_mv, cat, motif, observ_s, cdsme_s = _clamp_mouvement_payload(
                        objet=row.objet,
                        observ=row.observ,
                        grade=row.grade,
                        nom=row.nom,
                        cdsme=cdsme_target,
                        leg=str(row.id or "").strip(),
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
                            pid,
                            agent_id,
                            leg_s,
                            grade_s,
                            nom_s,
                            type_mv,
                            cat,
                            motif,
                            cdsme_s,
                            observ_s,
                            ref_date,
                        ),
                    )
                    mark_shared_data_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="gain_perte_create",module="gain_perte",entity_id=cur.lastrowid,before=None,after=row)

    def update_row(
        self,
        row: GainPerteRow,
        *,
        where_id: str,
        where_cdsme: str | None,
        movement_id: int | None = None,
    ) -> None:
        """Modifie une ligne ``mouvement_effectif``, identifiée par ``movement_id``
        (clé primaire) uniquement.

        ``where_id``/``where_cdsme`` (agent + mois) ne suffisent pas à cibler une
        ligne unique : un agent peut avoir plusieurs mouvements le même mois (ex.
        perte puis réintégration). Un ancien chemin de repli matchait sur ces deux
        seuls critères quand ``movement_id`` était absent — il modifiait alors TOUS
        les mouvements de cet agent pour ce mois au lieu d'un seul.
        """
        if movement_id is None:
            raise ValueError(
                "movement_id requis pour modifier un mouvement (identification univoque)."
            )
        revision = require_movement_token(row, movement_id)
        ym_target, cdsme_target = require_consistent_movement_month(row)
        from sme_gend.services.mysql_access_numerique_import import _clamp_mouvement_payload

        leg_s, grade_s, nom_s, type_mv, cat, motif, observ_s, cdsme_s = _clamp_mouvement_payload(
            objet=row.objet,
            observ=row.observ,
            grade=row.grade,
            nom=row.nom,
            cdsme=cdsme_target,
            leg=str(row.id or "").strip(),
        )
        with shared_data_write(self._settings, reason="gain_perte", mois=ym_target):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_movement_target(cur, movement_id, revision)
                    require_movement_period_open(cur, movement_id)
                    agent_id, pid = self._resolve_agent_and_periode(cur, row)
                    if agent_id is None or pid is None:
                        ym_hint = periode_ym_for_mouvement(row) or "?"
                        raise ValueError(
                            f"Période SME introuvable pour le mois « {ym_hint} » "
                            "(créez le mois dans le pilotage SME si besoin)."
                        )
                    require_period_open(cur, ym_target)
                    ref_date = _movement_reference_date(row)
                    cur.execute(
                        """
                        UPDATE mouvement_effectif m
                        SET m.sme_periode_id=%s, m.agent_id=%s, m.identifiant_ligne=%s, m.grade_snapshot=%s,
                            m.nom_snapshot=%s, m.type_mouvement=%s, m.categorie_mouvement=%s,
                            m.cdsme_derive=%s, m.motif=%s, m.observ=%s, m.date_reference=%s
                        WHERE m.id=%s
                        """,
                        (
                            pid,
                            agent_id,
                            leg_s,
                            grade_s,
                            nom_s,
                            type_mv,
                            cat,
                            cdsme_s,
                            motif,
                            observ_s,
                            ref_date,
                            movement_id,
                        ),
                    )
                    mark_shared_data_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="gain_perte_update",module="gain_perte",entity_id=movement_id,before=None,after=row)

    def delete_row(
        self,
        row_id: str,
        cdsme: str | None,
        *,
        movement_id: int | None = None,
        expected: GainPerteRow | None = None,
    ) -> None:
        """Supprime une ligne ``mouvement_effectif``, identifiée par ``movement_id``
        (clé primaire) uniquement — voir ``update_row`` pour la raison."""
        if movement_id is None:
            raise ValueError(
                "movement_id requis pour supprimer un mouvement (identification univoque)."
            )
        revision = require_movement_token(expected, movement_id)
        with shared_data_write(self._settings, reason="gain_perte", mois=cdsme_to_annee_mois(cdsme or "")):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_movement_target(cur, movement_id, revision)
                    require_movement_period_open(cur, movement_id)
                    cur.execute("DELETE FROM mouvement_effectif WHERE id=%s", (movement_id,))
                    mark_shared_data_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="gain_perte_delete",module="gain_perte",entity_id=movement_id,before=expected,after=None)

    def delete_rows(self, rows: list[GainPerteRow]) -> None:
        """Suppression atomique avec vérification optimiste de chaque ligne."""
        if not rows:
            return
        tokens: dict[int, str] = {}
        snapshots: dict[int, GainPerteRow] = {}
        for row in rows:
            movement_id = row.movement_id
            if movement_id is None:
                raise ValueError("movement_id requis pour supprimer un mouvement.")
            token = require_movement_token(row, movement_id)
            if movement_id in tokens:
                raise ValueError("La sélection contient deux fois le même mouvement.")
            tokens[movement_id] = token
            snapshots[movement_id] = row
        months = {periode_ym_for_mouvement(row) for row in rows}
        month = next(iter(months)) if len(months) == 1 else None
        with shared_data_write(self._settings, reason="gain_perte", mois=month):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for movement_id in sorted(tokens):
                        check_movement_target(cur, movement_id, tokens[movement_id])
                        require_movement_period_open(cur, movement_id)
                    deleted = 0
                    from sme_gend.services.business_audit import record_business_action
                    for movement_id in sorted(tokens):
                        cur.execute("DELETE FROM mouvement_effectif WHERE id=%s", (movement_id,))
                        if cur.rowcount != 1:
                            raise GainPerteConflictError(
                                "Un mouvement sélectionné a changé ou a déjà été supprimé. "
                                "Aucune suppression de la sélection n’a été validée."
                            )
                        deleted += 1
                        record_business_action(
                            self._settings, action="gain_perte_delete", module="gain_perte",
                            entity_id=movement_id, before=snapshots[movement_id], after=None,
                        )
                    mark_shared_data_write(deleted > 0)


class MySQLSubdivisionRepository(BaseMySQLRepository):
    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                return load_subdiv_flat_rows(cur)


class MySQLFonctionsRepository(BaseMySQLRepository):
    def fetch_all(self) -> list:
        from sme_gend.models.fonction_ref import FonctionRefRow

        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id, libelle, {reference_revision_sql('fonction')} FROM ref_fonction r ORDER BY libelle")
                rows = [FonctionRefRow.from_sequence(r) for r in cur.fetchall()]
                if rows:
                    return rows
        return []

    def fetch_distinct(self) -> list[str]:
        return [r.libelle for r in self.fetch_all() if r.libelle]

    def insert(self, libelle: str) -> int:
        lb = libelle.strip()
        if not lb:
            raise ValueError("Le libellé de fonction est obligatoire.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM ref_fonction WHERE libelle = %s LIMIT 1",
                        (lb,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        raise ReferenceConflictError(f"La fonction « {lb} » existe déjà.")
                    try:
                        cur.execute("INSERT INTO ref_fonction (libelle) VALUES (%s)", (lb,))
                        mark_reference_write(cur.rowcount > 0)
                        if cur.rowcount > 0:
                            from sme_gend.services.business_audit import record_business_action
                            record_business_action(self._settings,action="fonction_create",module="reference",entity_id=cur.lastrowid,before=None,after=dict(libelle=lb))
                    except Exception as exc:
                        err = str(exc).lower()
                        if "duplicate" in err:
                            raise ReferenceConflictError(f"La fonction « {lb} » existe déjà.") from exc
                        raise
                    new_id = int(cur.lastrowid or 0)
            if not new_id:
                raise ValueError("Impossible de créer la fonction.")
            return new_id

    def fetch_usage_counts(self) -> dict[int, int]:
        """
        Nombre de fiches carrière (situation actuelle) par fonction.

        Deux agrégations indexées au lieu d'un LEFT JOIN avec OR (timeout MySQL).
        """
        out: dict[int, int] = {}
        from sme_gend.config.liste_generale_scope import carriere_scope_for_settings
        scope, scope_p = carriere_scope_for_settings(self._settings)
        libelle_to_id: dict[str, int] = {}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, libelle FROM ref_fonction")
                for fid, lib in cur.fetchall():
                    lb = str(lib or "").strip()
                    if lb:
                        libelle_to_id[lb.casefold()] = int(fid)

                cur.execute(
                    f"""
                    SELECT fonction_id, COUNT(*)
                    FROM carriere_situation c
                    WHERE {scope}
                      AND fonction_id IS NOT NULL
                    GROUP BY fonction_id
                    """, scope_p
                )
                for fid, n in cur.fetchall():
                    out[int(fid)] = int(n or 0)

                cur.execute(
                    f"""
                    SELECT TRIM(fonction_libelle), COUNT(*)
                    FROM carriere_situation c
                    WHERE {scope}
                      AND fonction_id IS NULL
                      AND fonction_libelle IS NOT NULL
                      AND TRIM(fonction_libelle) <> ''
                    GROUP BY TRIM(fonction_libelle)
                    """, scope_p
                )
                for lib, n in cur.fetchall():
                    key = str(lib or "").strip().casefold()
                    fid = libelle_to_id.get(key)
                    if fid is not None:
                        out[fid] = out.get(fid, 0) + int(n or 0)
        return out

    def rename_impact(self, libelle, fonction_id):
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE fonction_libelle=%s OR fonction_id=%s',(libelle,fonction_id))
                career=int(cur.fetchone()[0])
                cur.execute('SELECT COUNT(*) FROM agent_affectation WHERE fonction_libelle=%s',(libelle,))
                assignments=int(cur.fetchone()[0])
                return career, assignments

    def update(self, fonction_id: int, old_libelle: str, new_libelle: str, *, expected: FonctionRefRow | None = None) -> None:
        old = old_libelle.strip()
        new = new_libelle.strip()
        if not new:
            raise ValueError("Le libellé de fonction est obligatoire.")
        revision = require_reference_token("fonction", fonction_id, expected)
        if old != expected.libelle:
            raise ValueError("Ancien libellé incohérent avec la version chargée. Actualisez les données.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "fonction", fonction_id, revision)
                    cur.execute(
                        "UPDATE ref_fonction SET libelle=%s WHERE id=%s",
                        (new, int(fonction_id)),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="fonction_update",module="reference",entity_id=fonction_id,before=expected,after=dict(libelle=new))
                    if old != new:
                        cur.execute(
                            """
                            UPDATE carriere_situation SET fonction_libelle=%s
                            WHERE fonction_libelle=%s
                            """,
                            (new, old),
                        )
                        mark_reference_write(cur.rowcount > 0)
                        cur.execute(
                            """
                            UPDATE agent_affectation SET fonction_libelle=%s
                            WHERE fonction_libelle=%s
                            """,
                            (new, old),
                        )
                        mark_reference_write(cur.rowcount > 0)
                        cur.execute(
                            """
                            UPDATE carriere_situation SET fonction_id=%s
                            WHERE fonction_libelle=%s
                            """,
                            (int(fonction_id), new),
                        )
                        mark_reference_write(cur.rowcount > 0)

    def delete(self, fonction_id: int, *, expected: FonctionRefRow | None = None) -> None:
        revision = require_reference_token("fonction", fonction_id, expected)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_reference_target(cur, "fonction", fonction_id, revision)
                    cur.execute("SELECT libelle FROM ref_fonction WHERE id=%s", (int(fonction_id),))
                    row = cur.fetchone()
                    if not row:
                        return
                    lib = str(row[0] or "").strip()
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM carriere_situation
                        WHERE fonction_libelle=%s OR fonction_id=%s
                        """,
                        (lib, int(fonction_id)),
                    )
                    n = int((cur.fetchone() or [0])[0])
                    cur.execute('SELECT COUNT(*) FROM agent_affectation WHERE fonction_libelle=%s',(lib,))
                    n += int((cur.fetchone() or [0])[0])
                    if n > 0:
                        raise ValueError(
                            f"Impossible de supprimer « {lib} » : {n} ligne(s) carrière l'utilisent."
                        )
                    cur.execute(
                        "DELETE FROM ref_fonction_structure WHERE fonction_id=%s",
                        (int(fonction_id),),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    cur.execute("DELETE FROM ref_fonction WHERE id=%s", (int(fonction_id),))
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings,action="fonction_delete",module="reference",entity_id=fonction_id,before=expected,after=None)

    def count_usage(self, libelle: str, fonction_id: int | None = None) -> int:
        lb = libelle.strip()
        if not lb and fonction_id is None:
            return 0
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                if fonction_id is not None:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM carriere_situation
                        WHERE fonction_id=%s OR fonction_libelle=%s
                        """,
                        (int(fonction_id), lb),
                    )
                else:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM carriere_situation
                        WHERE fonction_libelle=%s
                        """,
                        (lb,),
                    )
                return int((cur.fetchone() or [0])[0])


class MySQLStructureSubdivisionRepository(BaseMySQLRepository):
    def fetch_distinct_pairs(self) -> list[tuple[str, str]]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT rs.code, rsa.libelle
                    FROM ref_subdivision_arme rsa
                    INNER JOIN ref_structure rs ON rs.id = rsa.structure_id
                    WHERE rsa.libelle IS NOT NULL AND TRIM(rsa.libelle) <> ''
                    ORDER BY rs.code, rsa.libelle
                    """
                )
                return [
                    (str(r[0] or "").strip(), str(r[1] or "").strip()) for r in cur.fetchall()
                ]

    def fetch_subdiv_labels_for_structure(self, structure_code: str) -> list[str]:
        sc = (structure_code or "").strip()
        if not sc:
            return []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                # Pas de SELECT DISTINCT + ORDER BY ordre_tri (MySQL 3065 / ONLY_FULL_GROUP_BY).
                cur.execute(
                    """
                    SELECT rsa.libelle
                    FROM ref_subdivision_arme rsa
                    INNER JOIN ref_structure rs ON rs.id = rsa.structure_id
                    WHERE rs.code = %s
                      AND rsa.actif = 1
                      AND TRIM(COALESCE(rsa.libelle, '')) <> ''
                    ORDER BY rsa.ordre_tri IS NULL, rsa.ordre_tri, rsa.libelle
                    """,
                    (sc,),
                )
                seen: set[str] = set()
                out: list[str] = []
                for (raw,) in cur.fetchall():
                    lbl = str(raw or "").strip()
                    if not lbl:
                        continue
                    key = lbl.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(lbl)
                return out

    def fetch_rsa_order_for_structure(self, structure_code: str) -> tuple[list[str], list[int]]:
        sc = (structure_code or "").strip()
        if not sc:
            return [], []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rsa.id, rsa.libelle
                    FROM ref_subdivision_arme rsa
                    INNER JOIN ref_structure rs ON rs.id = rsa.structure_id
                    WHERE rs.code = %s
                      AND rsa.actif = 1
                      AND TRIM(COALESCE(rsa.libelle, '')) <> ''
                    ORDER BY rsa.ordre_tri IS NULL, rsa.ordre_tri, rsa.libelle
                    """,
                    (sc,),
                )
                labels: list[str] = []
                ids: list[int] = []
                for rid, lib in cur.fetchall():
                    lbl = str(lib or "").strip()
                    if not lbl:
                        continue
                    labels.append(lbl)
                    ids.append(int(rid))
                return labels, ids

    def fetch_distinct_subdiv_arme_labels(self) -> list[str]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT libelle FROM ref_subdivision_arme
                    WHERE actif = 1 AND TRIM(COALESCE(libelle, '')) <> ''
                    ORDER BY libelle
                    """
                )
                return [str(r[0] or "").strip() for r in cur.fetchall() if r[0]]


from sme_gend.repositories.mysql_padmin_repository import MySQLPadminRepository
