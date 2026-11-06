"""Diplômes et passations de grade — persistance MySQL."""

from __future__ import annotations

from sme_gend.repositories.write_guard import atomic_repository_write, history_read_sql

from datetime import date
from typing import TYPE_CHECKING

from sme_gend.config.settings import Settings
from sme_gend.models.agent_dossier import (
    AgentAffectationRow,
    AgentDiplomeRow,
    AgentPassationGradeRow,
    DIPLOME_DEFAULT,
)
from sme_gend.models.distinction_ref import (
    DISTINCTION_KIND_DECORATION,
    DISTINCTION_KIND_MEDAILLE,
    AgentDistinctionRow,
)
from sme_gend.repositories.mysql_common import BaseMySQLRepository

if TYPE_CHECKING:
    from sme_gend.models.subdivision_flat import SubdivisionFlatRow
    from sme_gend.services.mysql_ref_etl import RefEtlCache


class MySQLAgentDossierRepository(BaseMySQLRepository):
    def _carriere_cutoff(self, carriere_id: str) -> date | None:
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.mysql_import_period import last_day_of_month
        from sme_gend.services.access_date_parse import parse_access_date
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT p.annee_mois,c.date_fin
                    FROM carriere_situation c LEFT JOIN sme_periode p ON p.id=c.sme_periode_id
                    WHERE c.id=%s""", (int(carriere_id),))
                row = cur.fetchone()
        if not row:
            raise ValueError("Situation de carrière introuvable.")
        month = canonical_sme_mois_yyyy_mm(str(row[0] or ''))
        end = parse_access_date(row[1])
        cutoff = last_day_of_month(month) if month else end
        if cutoff is None:
            raise ValueError("Période de carrière non reconnue ; vérifiez le mois avant de recalculer.")
        return min(cutoff, end) if end else cutoff

    def resolve_agent_id(self, *, carriere_id: str | None = None, legacy_id: str | None = None) -> int:
        cid = (carriere_id or "").strip()
        leg = (legacy_id or "").strip()
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                if cid:
                    cur.execute(
                        "SELECT agent_id FROM carriere_situation WHERE id = %s LIMIT 1",
                        (int(cid),),
                    )
                    row = cur.fetchone()
                    if row:
                        return int(row[0])
                if leg:
                    cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                    row = cur.fetchone()
                    if row:
                        return int(row[0])
        raise ValueError("Agent introuvable pour ce dossier.")

    def list_diplomes(self, agent_id: int) -> list[AgentDiplomeRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT id, agent_id,
                           COALESCE(categorie, 'CIVIL'), COALESCE(libelle_libre, ''),
                           date_obtention, lieu_obtention, mention, etablissement, obs,
                           ref_diplome_id
                    FROM agent_diplome
                    WHERE agent_id = %s
                    ORDER BY categorie, date_obtention IS NULL, date_obtention, libelle_libre
                    """, 'agent_diplome'),
                    (int(agent_id),),
                )
                return [AgentDiplomeRow.from_sequence(r) for r in cur.fetchall()]

    @atomic_repository_write(agent_arg="agent_id")
    def insert_diplome(
        self,
        agent_id: int,
        *,
        categorie: str,
        libelle: str,
        date_obtention: date | None = None,
        lieu: str | None = None,
        mention: str | None = None,
        etablissement: str | None = None,
        obs: str | None = None,
        import_lot_id: int | None = None,
    ) -> int:
        lib = (libelle or "").strip()
        if not lib:
            raise ValueError("Libellé du diplôme obligatoire.")
        cat = (categorie or DIPLOME_DEFAULT).strip().upper()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_diplome (
                        agent_id, categorie, libelle_libre, date_obtention,
                        lieu_obtention, mention, etablissement, obs, import_lot_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(agent_id),
                        cat,
                        lib,
                        date_obtention,
                        (lieu or "").strip() or None,
                        (mention or "").strip() or None,
                        (etablissement or "").strip() or None,
                        (obs or "").strip() or None,
                        int(import_lot_id) if import_lot_id else None,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(event_table="agent_diplome", id_arg="diplome_id")
    def update_diplome(
        self,
        diplome_id: int,
        *,
        categorie: str,
        libelle: str,
        date_obtention: date | None = None,
        lieu: str | None = None,
        mention: str | None = None,
        etablissement: str | None = None,
        obs: str | None = None,
    ) -> None:
        lib = (libelle or "").strip()
        if not lib:
            raise ValueError("Libellé du diplôme obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE agent_diplome SET
                      categorie=%s, libelle_libre=%s, date_obtention=%s,
                      lieu_obtention=%s, mention=%s, etablissement=%s, obs=%s
                    WHERE id=%s
                    """,
                    (
                        (categorie or DIPLOME_DEFAULT).strip().upper(),
                        lib,
                        date_obtention,
                        (lieu or "").strip() or None,
                        (mention or "").strip() or None,
                        (etablissement or "").strip() or None,
                        (obs or "").strip() or None,
                        int(diplome_id),
                    ),
                )

    @atomic_repository_write(event_table="agent_diplome", id_arg="diplome_id")
    def delete_diplome(self, diplome_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_diplome WHERE id = %s", (int(diplome_id),))

    def list_distinctions(self, agent_id: int) -> list[AgentDistinctionRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT d.id, d.agent_id,
                           CASE WHEN d.ref_medaille_id IS NOT NULL
                                THEN %s ELSE %s END,
                           d.ref_medaille_id, d.ref_decoration_id,
                           COALESCE(rm.libelle_court, rm.code, rd.libelle_court, rd.code, ''),
                           d.date_attribution, d.date_publication_journal,
                           d.numero_arrete, d.date_arrete, d.citation, d.source
                    FROM agent_distinction d
                    LEFT JOIN ref_medaille rm ON rm.id = d.ref_medaille_id
                    LEFT JOIN ref_decoration rd ON rd.id = d.ref_decoration_id
                    WHERE d.agent_id = %s
                    ORDER BY d.date_attribution IS NULL, d.date_attribution DESC, d.id DESC
                    """, 'd'),
                    (
                        DISTINCTION_KIND_MEDAILLE,
                        DISTINCTION_KIND_DECORATION,
                        int(agent_id),
                    ),
                )
                return [AgentDistinctionRow.from_sequence(r) for r in cur.fetchall()]

    @atomic_repository_write(agent_arg="agent_id")
    def insert_distinction(
        self,
        agent_id: int,
        *,
        kind: str,
        ref_medaille_id: int | None = None,
        ref_decoration_id: int | None = None,
        date_attribution: date | None = None,
        date_publication_journal: date | None = None,
        numero_arrete: str | None = None,
        date_arrete: date | None = None,
        citation: str | None = None,
        source: str | None = None,
    ) -> int:
        k = (kind or "").strip().lower()
        if k == DISTINCTION_KIND_MEDAILLE:
            if not ref_medaille_id:
                raise ValueError("Médaille obligatoire.")
            ref_decoration_id = None
        elif k == DISTINCTION_KIND_DECORATION:
            if not ref_decoration_id:
                raise ValueError("Décoration / ordre obligatoire.")
            ref_medaille_id = None
        else:
            raise ValueError("Type de distinction invalide.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_distinction (
                      agent_id, ref_medaille_id, ref_decoration_id,
                      date_attribution, date_publication_journal,
                      numero_arrete, date_arrete, citation, source
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(agent_id),
                        int(ref_medaille_id) if ref_medaille_id else None,
                        int(ref_decoration_id) if ref_decoration_id else None,
                        date_attribution,
                        date_publication_journal,
                        (numero_arrete or "").strip() or None,
                        date_arrete,
                        (citation or "").strip() or None,
                        (source or "").strip() or None,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(event_table="agent_distinction", id_arg="distinction_id")
    def update_distinction(
        self,
        distinction_id: int,
        *,
        kind: str,
        ref_medaille_id: int | None = None,
        ref_decoration_id: int | None = None,
        date_attribution: date | None = None,
        date_publication_journal: date | None = None,
        numero_arrete: str | None = None,
        date_arrete: date | None = None,
        citation: str | None = None,
        source: str | None = None,
    ) -> None:
        k = (kind or "").strip().lower()
        if k == DISTINCTION_KIND_MEDAILLE:
            if not ref_medaille_id:
                raise ValueError("Médaille obligatoire.")
            ref_decoration_id = None
        elif k == DISTINCTION_KIND_DECORATION:
            if not ref_decoration_id:
                raise ValueError("Décoration / ordre obligatoire.")
            ref_medaille_id = None
        else:
            raise ValueError("Type de distinction invalide.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE agent_distinction SET
                      ref_medaille_id=%s, ref_decoration_id=%s,
                      date_attribution=%s, date_publication_journal=%s,
                      numero_arrete=%s, date_arrete=%s, citation=%s, source=%s
                    WHERE id=%s
                    """,
                    (
                        int(ref_medaille_id) if ref_medaille_id else None,
                        int(ref_decoration_id) if ref_decoration_id else None,
                        date_attribution,
                        date_publication_journal,
                        (numero_arrete or "").strip() or None,
                        date_arrete,
                        (citation or "").strip() or None,
                        (source or "").strip() or None,
                        int(distinction_id),
                    ),
                )

    @atomic_repository_write(event_table="agent_distinction", id_arg="distinction_id")
    def delete_distinction(self, distinction_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_distinction WHERE id = %s", (int(distinction_id),))

    def list_passations(self, agent_id: int) -> list[AgentPassationGradeRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT id, agent_id, grade_libelle, date_passation,
                           ndecision, obs, grade_id
                    FROM agent_passation_grade
                    WHERE agent_id = %s
                    ORDER BY date_passation IS NULL, date_passation, id
                    """, 'agent_passation_grade'),
                    (int(agent_id),),
                )
                return [AgentPassationGradeRow.from_sequence(r) for r in cur.fetchall()]

    @atomic_repository_write(agent_arg="agent_id")
    def insert_passation(
        self,
        agent_id: int,
        *,
        grade_libelle: str,
        date_passation: date | None = None,
        ndecision: str | None = None,
        obs: str | None = None,
    ) -> int:
        gl = (grade_libelle or "").strip()
        if not gl:
            raise ValueError("Grade obligatoire pour une passation.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                gid = self._resolve_grade_id(cur, gl)
                cur.execute(
                    """
                    INSERT INTO agent_passation_grade (
                        agent_id, grade_libelle, date_passation, ndecision, obs, grade_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(agent_id),
                        gl,
                        date_passation,
                        (ndecision or "").strip() or None,
                        (obs or "").strip() or None,
                        gid,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(event_table="agent_passation_grade", id_arg="passation_id")
    def update_passation(
        self,
        passation_id: int,
        *,
        grade_libelle: str,
        date_passation: date | None = None,
        ndecision: str | None = None,
        obs: str | None = None,
    ) -> None:
        gl = (grade_libelle or "").strip()
        if not gl:
            raise ValueError("Grade obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                gid = self._resolve_grade_id(cur, gl)
                cur.execute(
                    """
                    UPDATE agent_passation_grade SET
                      grade_libelle=%s, date_passation=%s, ndecision=%s, obs=%s, grade_id=%s
                    WHERE id=%s
                    """,
                    (
                        gl,
                        date_passation,
                        (ndecision or "").strip() or None,
                        (obs or "").strip() or None,
                        gid,
                        int(passation_id),
                    ),
                )

    @atomic_repository_write(event_table="agent_passation_grade", id_arg="passation_id")
    def delete_passation(self, passation_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_passation_grade WHERE id = %s", (int(passation_id),))

    def list_affectations(self, agent_id: int) -> list[AgentAffectationRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT id, agent_id, structure_code, date_affectation,
                           fonction_libelle, subdivision_arme_libelle, sme_periode_mois, obs, structure_id
                    FROM agent_affectation
                    WHERE agent_id = %s
                    ORDER BY date_affectation, id
                    """, 'agent_affectation'),
                    (int(agent_id),),
                )
                return [AgentAffectationRow.from_sequence(r) for r in cur.fetchall()]

    @atomic_repository_write(agent_arg="agent_id")
    def insert_affectation(
        self,
        agent_id: int,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = None,
        import_lot_id: int | None = None,
        structure_id: int | None = None,
    ) -> int:
        sc = (structure_code or "").strip()
        if not sc:
            raise ValueError("Code structure obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                sid = structure_id
                if sid is None:
                    cur.execute(
                        "SELECT id FROM ref_structure WHERE code = %s LIMIT 1",
                        (sc,),
                    )
                    row = cur.fetchone()
                    sid = int(row[0]) if row else None
                cur.execute(
                    """
                    INSERT INTO agent_affectation (
                        agent_id, structure_code, structure_id, fonction_libelle,
                        subdivision_arme_libelle, date_affectation, sme_periode_mois,
                        obs, import_lot_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(agent_id),
                        sc,
                        sid,
                        (fonction or "").strip() or None,
                        (subdivision_arme or "").strip() or None,
                        date_affectation,
                        (sme_periode_mois or "").strip() or None,
                        (obs or "").strip() or None,
                        int(import_lot_id) if import_lot_id else None,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(event_table="agent_affectation", id_arg="affectation_id")
    def delete_affectation(self, affectation_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_affectation WHERE id = %s", (int(affectation_id),))

    @atomic_repository_write(event_table="agent_affectation", id_arg="affectation_id")
    def update_affectation(
        self,
        affectation_id: int,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = None,
    ) -> None:
        sc = (structure_code or "").strip()
        if not sc:
            raise ValueError("Code structure obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ref_structure WHERE code = %s LIMIT 1",
                    (sc,),
                )
                row = cur.fetchone()
                sid = int(row[0]) if row else None
                cur.execute(
                    """
                    UPDATE agent_affectation SET
                        structure_code = %s,
                        structure_id = %s,
                        fonction_libelle = %s,
                        subdivision_arme_libelle = %s,
                        date_affectation = %s,
                        sme_periode_mois = %s,
                        obs = %s
                    WHERE id = %s
                    """,
                    (
                        sc,
                        sid,
                        (fonction or "").strip() or None,
                        (subdivision_arme or "").strip() or None,
                        date_affectation,
                        (sme_periode_mois or "").strip() or None,
                        (obs or "").strip() or None,
                        int(affectation_id),
                    ),
                )

    def agent_id_for_legacy(self, legacy_access_id: str) -> int | None:
        leg = (legacy_access_id or "").strip()
        if not leg:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                return int(row[0]) if row else None

    @atomic_repository_write(agent_arg="agent_id")
    def record_list_saisie_diplome(
        self,
        agent_id: int,
        *,
        libelle: str,
        date_obtention: date | None = None,
        categorie: str = DIPLOME_DEFAULT,
        obs: str | None = "Saisie applicative",
        previous_date: date | None = None,
    ) -> int | None:
        """Upsert diplôme issu de la liste : met à jour si même libellé, sinon insère."""
        lib = (libelle or "").strip()
        if not lib:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM agent_diplome
                    WHERE agent_id = %s
                      AND UPPER(TRIM(libelle_libre)) = UPPER(TRIM(%s))
                      AND (%s IS NULL OR date_obtention <=> %s)
                    ORDER BY (categorie='MILITAIRE') DESC, date_obtention IS NULL, date_obtention DESC, id DESC
                    LIMIT 1
                    """,
                    (int(agent_id), lib, previous_date, previous_date),
                )
                row = cur.fetchone()
                if row:
                    did = int(row[0])
                    cur.execute('UPDATE agent_diplome SET libelle_libre=%s,date_obtention=%s WHERE id=%s AND agent_id=%s',
                                (lib,date_obtention,did,int(agent_id)))
                    return did
        return self.insert_diplome(
            agent_id,
            categorie=categorie,
            libelle=lib,
            date_obtention=date_obtention,
            obs=obs,
        )

    @atomic_repository_write(agent_arg="agent_id")
    def record_list_saisie_passation(
        self,
        agent_id: int,
        *,
        grade_libelle: str,
        date_passation: date | None = None,
        ndecision: str | None = None,
        obs: str | None = "Saisie applicative",
    ) -> int | None:
        """Ajoute une passation (historique) depuis la saisie liste."""
        gl = (grade_libelle or "").strip()
        if not gl:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id FROM agent_passation_grade
                    WHERE agent_id=%s AND UPPER(TRIM(grade_libelle))=UPPER(%s)
                    AND date_passation <=> %s AND COALESCE(TRIM(ndecision),'')=%s
                    ORDER BY id DESC LIMIT 1''',
                    (int(agent_id), gl, date_passation, (ndecision or '').strip()))
                existing = cur.fetchone()
                if existing:
                    return int(existing[0])
        return self.insert_passation(
            agent_id,
            grade_libelle=gl,
            date_passation=date_passation,
            ndecision=ndecision,
            obs=obs,
        )

    @atomic_repository_write(agent_arg="agent_id")
    def record_list_saisie_affectation(
        self,
        agent_id: int,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = "Saisie applicative",
    ) -> int | None:
        """Ajoute une affectation (historique) depuis la saisie liste."""
        sc = (structure_code or "").strip()
        if not sc:
            return None
        return self.insert_affectation(
            agent_id,
            structure_code=sc,
            date_affectation=date_affectation,
            fonction=fonction,
            subdivision_arme=subdivision_arme,
            sme_periode_mois=sme_periode_mois,
            obs=obs,
        )

    @atomic_repository_write(agent_arg="agent_id")
    def insert_diplome_if_absent(
        self,
        agent_id: int,
        *,
        libelle: str,
        date_obtention: date | None = None,
        categorie: str = DIPLOME_DEFAULT,
        obs: str | None = None,
        import_lot_id: int | None = None,
        lieu: str | None = None,
    ) -> int | None:
        lib = (libelle or "").strip()
        if not lib:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Compléter une date inconnue sans transformer une précision en
                # nouvelle obtention. Une ambiguïté reste visible et n'est pas fusionnée.
                from sme_gend.services.dossier_grouping import normalized_label_sql
                cur.execute(f"""SELECT id, date_obtention, obs, lieu_obtention FROM agent_diplome
                    WHERE agent_id=%s AND {normalized_label_sql('libelle_libre')}={normalized_label_sql('%s')}
                    AND COALESCE(categorie, 'MILITAIRE')=%s
                    AND (date_obtention IS NULL OR %s IS NULL OR date_obtention=%s) FOR UPDATE""",
                    (int(agent_id), lib, categorie, date_obtention, date_obtention))
                candidates = list(cur.fetchall())
                exact = [c for c in candidates if c[1] == date_obtention]
                chosen = exact if exact else candidates
                if len(chosen) == 1:
                    did, old_date, old_obs, old_lieu = chosen[0]
                    if (old_obs or '').startswith('Import archive '):
                        new_date = old_date or date_obtention
                        new_lieu = old_lieu or (lieu or '').strip() or None
                        merged_obs = old_obs or ''
                        for line in (obs or '').splitlines():
                            if line and not line.startswith('Import archive ') and line not in merged_obs:
                                merged_obs += '\n' + line
                        if (new_date, new_lieu, merged_obs) != (old_date, old_lieu, old_obs):
                            cur.execute("UPDATE agent_diplome SET date_obtention=%s, lieu_obtention=%s, obs=%s WHERE id=%s",
                                        (new_date, new_lieu, merged_obs, did))
                    return None
                if exact:
                    return None  # Existing duplicates are not multiplied by reimport.
                return self.insert_diplome(
                    agent_id,
                    categorie=categorie,
                    libelle=lib,
                    date_obtention=date_obtention,
                    lieu=lieu,
                    obs=obs,
                    import_lot_id=import_lot_id,
                )

    @atomic_repository_write(agent_arg="agent_id")
    def insert_passation_if_absent(
        self,
        agent_id: int,
        *,
        grade_libelle: str,
        date_passation: date | None = None,
        ndecision: str | None = None,
        obs: str | None = None,
    ) -> int | None:
        gl = (grade_libelle or "").strip()
        if not gl:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                if str(obs or '').startswith('Import archive '):
                    from sme_gend.services.grade_history_merge import matching_archive_passation
                    if matching_archive_passation(cur, agent_id, gl, date_passation, ndecision):
                        return None
                if self._passation_exists(cur, agent_id, gl, date_passation):
                    return None
        return self.insert_passation(
            agent_id,
            grade_libelle=gl,
            date_passation=date_passation,
            ndecision=ndecision,
            obs=obs,
        )

    @atomic_repository_write(agent_arg="agent_id")
    def insert_affectation_if_absent(
        self,
        agent_id: int,
        *,
        structure_code: str,
        date_affectation: date,
        fonction: str | None = None,
        subdivision_arme: str | None = None,
        sme_periode_mois: str | None = None,
        obs: str | None = None,
        import_lot_id: int | None = None,
    ) -> int | None:
        sc = (structure_code or "").strip()
        if not sc:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                if self._affectation_exists(cur, agent_id, sc, date_affectation):
                    return None
        return self.insert_affectation(
            agent_id,
            structure_code=sc,
            date_affectation=date_affectation,
            fonction=fonction,
            subdivision_arme=subdivision_arme,
            sme_periode_mois=sme_periode_mois,
            obs=obs,
            import_lot_id=import_lot_id,
        )

    @staticmethod
    def _diplome_exists(cur, agent_id: int, libelle: str, date_obt: date | None, categorie: str = DIPLOME_DEFAULT) -> bool:
        from sme_gend.services.dossier_grouping import normalized_label_sql
        cur.execute(
            f"""
            SELECT 1 FROM agent_diplome
            WHERE agent_id = %s
              AND {normalized_label_sql('libelle_libre')} = {normalized_label_sql('%s')}
              AND COALESCE(categorie, 'MILITAIRE') = %s
              AND (
                (date_obtention IS NULL AND %s IS NULL)
                OR date_obtention = %s
              )
            LIMIT 1
            """,
            (int(agent_id), libelle, categorie, date_obt, date_obt),
        )
        return cur.fetchone() is not None

    @staticmethod
    def _passation_exists(cur, agent_id: int, grade: str, d_pass: date | None) -> bool:
        cur.execute(
            """
            SELECT 1 FROM agent_passation_grade
            WHERE agent_id = %s
              AND UPPER(TRIM(grade_libelle)) = UPPER(TRIM(%s))
              AND (
                (date_passation IS NULL AND %s IS NULL)
                OR date_passation = %s
              )
            LIMIT 1
            """,
            (int(agent_id), grade, d_pass, d_pass),
        )
        return cur.fetchone() is not None

    @staticmethod
    def _affectation_exists(cur, agent_id: int, structure: str, d_aff: date) -> bool:
        cur.execute(
            """
            SELECT 1 FROM agent_affectation
            WHERE agent_id = %s
              AND UPPER(TRIM(structure_code)) = UPPER(TRIM(%s))
              AND date_affectation = %s
            LIMIT 1
            """,
            (int(agent_id), structure, d_aff),
        )
        return cur.fetchone() is not None

    def _resolve_grade_id(self, cur, libelle: str) -> int | None:
        g = (libelle or "").strip()
        if not g:
            return None
        cur.execute(
            """
            SELECT id FROM ref_grade
            WHERE code = %s OR UPPER(TRIM(libelle)) = UPPER(%s)
            LIMIT 1
            """,
            (g, g),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    @atomic_repository_write(agent_arg="agent_id")
    def sync_carriere_diplome_from_dossier(self, agent_id: int, carriere_id: str, *, cutoff: date | None = None) -> None:
        """Met à jour diplome_libelle / date_obtention sur la carrière depuis le dossier."""
        from sme_gend.models.agent_dossier import DIPLOME_MILITAIRE

        diplomes = self.list_diplomes(int(agent_id))
        cutoff = cutoff or self._carriere_cutoff(carriere_id)
        eligible = [d for d in diplomes if not cutoff or not d.date_obtention or d.date_obtention <= cutoff]
        if any(d.categorie == DIPLOME_MILITAIRE for d in diplomes) and not any(d.categorie == DIPLOME_MILITAIRE for d in eligible):
            return  # Conserver le snapshot si le dossier ne décrit que le futur.
        diplomes = eligible
        libelle: str | None = None
        date_obt: str | None = None
        if diplomes:
            militaire = [d for d in diplomes if d.categorie == DIPLOME_MILITAIRE]
            if militaire:
                best = max(militaire, key=lambda d: (
                    d.date_obtention.toordinal() if d.date_obtention else 0,
                    d.id or 0,
                ))
                libelle = (best.libelle or "").strip() or None
                if best.date_obtention:
                    date_obt = best.date_obtention.isoformat()

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE carriere_situation SET
                      diplome_libelle=%s, date_obtention=%s
                    WHERE id=%s
                    """,
                    (libelle, date_obt, int(carriere_id)),
                )

    @atomic_repository_write(agent_arg="agent_id")
    def sync_carriere_grade_from_passations(self, agent_id: int, carriere_id: str, *, cutoff: date | None = None) -> None:
        """Met à jour grade/date_grade sur la carrière depuis la dernière passation."""
        cutoff = cutoff or self._carriere_cutoff(carriere_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT grade_libelle, date_passation, ndecision
                    FROM agent_passation_grade
                    WHERE agent_id = %s
                      AND (%s IS NULL OR date_passation IS NULL OR date_passation <= %s)
                    ORDER BY COALESCE(date_passation, '1900-01-01') DESC, id DESC
                    LIMIT 1
                    """,
                    (int(agent_id), cutoff, cutoff),
                )
                row = cur.fetchone()
                if not row:
                    if cutoff:
                        cur.execute("SELECT 1 FROM agent_passation_grade WHERE agent_id=%s LIMIT 1", (int(agent_id),))
                        if cur.fetchone():
                            return
                    cur.execute(
                        """
                        UPDATE carriere_situation SET
                          grade_libelle=NULL, date_grade=NULL, ndecision=NULL, grade_id=NULL
                        WHERE id=%s
                        """,
                        (int(carriere_id),),
                    )
                    return
                gl, dp, nd = row[0], row[1], row[2]
                gid = self._resolve_grade_id(cur, str(gl or ""))
                cur.execute(
                    """
                    UPDATE carriere_situation SET
                      grade_libelle=%s, date_grade=%s, ndecision=%s, grade_id=%s
                    WHERE id=%s
                    """,
                    (gl, dp, nd, gid, int(carriere_id)),
                )

    @atomic_repository_write(agent_arg="agent_id")
    def sync_carriere_affectation_from_dossier(
        self,
        agent_id: int,
        carriere_id: str,
        *,
        ref_cache: "RefEtlCache | None" = None,
        flat_rows: "list[SubdivisionFlatRow] | None" = None,
        cutoff: date | None = None,
    ) -> None:
        """
        Projette structure / fonction / subdivision depuis la dernière affectation.

        ``ref_cache``/``flat_rows`` permettent à un appelant en lot (batch) de
        charger le référentiel une seule fois pour tous les agents au lieu
        d'une relecture complète (plusieurs requêtes bulk + une jointure à 3
        niveaux) à chaque agent — sans cela, un lot de quelques centaines
        d'agents multiplie ce coût par autant d'appels, ce qui peut à lui seul
        expliquer un import qui prend des dizaines de minutes voire plus.
        """
        from sme_gend.models.liste_generale import ListeGeneraleRow
        from sme_gend.services.mysql_ref_etl import (
            load_ref_cache_from_db,
            load_subdiv_flat_rows,
            prepare_carriere_write_fields,
        )

        affectations = self.list_affectations(int(agent_id))
        cutoff = cutoff or self._carriere_cutoff(carriere_id)
        eligible = [a for a in affectations if not cutoff or not a.date_affectation or a.date_affectation <= cutoff]
        if affectations and not eligible:
            return
        affectations = eligible
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not affectations:
                    cur.execute(
                        """
                        UPDATE carriere_situation SET
                          structure_code=NULL, structure_id=NULL,
                          fonction_libelle=NULL, fonction_id=NULL,
                          subdivision_arme_libelle=NULL, subdivision_arme_id=NULL
                        WHERE id=%s
                        """,
                        (int(carriere_id),),
                    )
                    return

                def _aff_key(a: AgentAffectationRow) -> tuple:
                    d = a.date_affectation
                    return (d.toordinal() if d else 0, a.id or 0)

                best = max(affectations, key=_aff_key)
                stub = ListeGeneraleRow(
                    id=str(carriere_id),
                    noms=None,
                    date_naissance=None,
                    lieu_naissance=None,
                    grade=None,
                    date_grade=None,
                    date_service=None,
                    ndecision=None,
                    matricule_militaire=None,
                    matricule_solde=None,
                    position_admin=None,
                    fonction=best.fonction,
                    subdivision_arme=best.subdivision_arme,
                    diplome=None,
                    date_obtention=None,
                    structure=best.structure_code,
                    obs=None,
                    ordre_tri=None,
                    presseance=None,
                )
                cache = ref_cache if ref_cache is not None else load_ref_cache_from_db(cur)
                flat = flat_rows if flat_rows is not None else load_subdiv_flat_rows(cur)
                fields = prepare_carriere_write_fields(
                    cur, cache, stub, flat_rows=flat, auto_create=True
                )
                cur.execute(
                    """
                    UPDATE carriere_situation SET
                      structure_code=%s, structure_id=%s,
                      fonction_libelle=%s, fonction_id=%s,
                      subdivision_arme_libelle=%s, subdivision_arme_id=%s
                    WHERE id=%s
                    """,
                    (
                        fields["structure_code"],
                        fields["structure_id"],
                        fields["fonction_libelle"],
                        fields["fonction_id"],
                        fields["subdivision_arme_libelle"],
                        fields["subdivision_arme_id"],
                        int(carriere_id),
                    ),
                )

    @atomic_repository_write(agent_arg="agent_id")
    def apply_dated_grade(self, agent_id, before, effective_date, *, carriere_id, protected_dates=(), component='grade'):
        """Recalculate existing months from the event date, including closed months."""
        from sme_gend.services.sme_editing_policy import is_statut_ferme
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.mysql_import_period import last_day_of_month
        from sme_gend.services.access_date_parse import parse_access_date
        target = effective_date.strftime('%Y-%m')
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT annee_mois, statut FROM sme_periode FOR SHARE')
                statuses = {}
                for raw_month, status in cur.fetchall():
                    month = canonical_sme_mois_yyyy_mm(raw_month)
                    if month not in statuses or is_statut_ferme(status or ''):
                        statuses[month] = status
                cur.execute('SELECT c.id, p.annee_mois,c.date_fin FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=%s',
                            (int(agent_id),))
                careers = cur.fetchall()
                columns = {
                    'grade': ('grade_libelle', 'date_grade', 'ndecision'),
                    'diplome': ('diplome_libelle', 'date_obtention'),
                    'affectation': ('structure_code', 'fonction_libelle', 'subdivision_arme_libelle'),
                }[component]
                projection_sql = f"SELECT id,{','.join(columns)} FROM carriere_situation WHERE agent_id=%s"
                cur.execute(projection_sql, (int(agent_id),))
                previous = {r[0]: dict(zip(columns, r[1:])) for r in cur.fetchall()}
                source_month = next((canonical_sme_mois_yyyy_mm(m) for cid, m, end in careers if str(cid) == str(carriere_id)), None)
                if source_month is None:
                    raise ValueError("Le mois de la fiche est introuvable ; aucune modification enregistrée.")
                if self._carriere_cutoff(carriere_id) < effective_date:
                    # The ordinary monthly writer has already written the requested grade.
                    # Restore its exact baseline when the event takes effect later.
                    if component == 'grade':
                        gid = self._resolve_grade_id(cur, before.grade or '')
                        cur.execute('UPDATE carriere_situation SET grade_libelle=%s, grade_id=%s, date_grade=%s, ndecision=%s WHERE id=%s AND agent_id=%s',
                                    (before.grade, gid, before.date_grade, before.ndecision, int(carriere_id), int(agent_id)))
                    elif component == 'diplome':
                        cur.execute('UPDATE carriere_situation SET diplome_libelle=%s,date_obtention=%s WHERE id=%s',
                                    (before.diplome,parse_access_date(before.date_obtention),int(carriere_id)))
        for cid, raw_month, end in careers:
            month = canonical_sme_mois_yyyy_mm(raw_month)
            if month and month >= target:
                cutoff = min(last_day_of_month(month),parse_access_date(end)) if parse_access_date(end) else last_day_of_month(month)
                if cutoff >= effective_date:
                    if component == 'grade': self.sync_carriere_grade_from_passations(agent_id,str(cid),cutoff=cutoff)
                    elif component == 'diplome': self.sync_carriere_diplome_from_dossier(agent_id,str(cid),cutoff=cutoff)
                    elif component == 'affectation': self.sync_carriere_affectation_from_dossier(agent_id,str(cid),cutoff=cutoff)
                elif str(cid)==str(carriere_id) and component!='grade':
                    self.sync_carriere_from_dossier(agent_id,str(cid),components=frozenset({component}))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(projection_sql, (int(agent_id),))
                current = {r[0]: dict(zip(columns, r[1:])) for r in cur.fetchall()}
        changes = []
        for cid, raw_month, end in careers:
            if previous.get(cid) != current.get(cid):
                month = canonical_sme_mois_yyyy_mm(raw_month)
                changes.append({'carriere_id': cid, 'month': month,
                                'closed': is_statut_ferme(statuses.get(month) or ''),
                                'before': previous.get(cid), 'after': current.get(cid)})
        if changes:
            from sme_gend.services.temporal_projection_audit import log_temporal_projection
            log_temporal_projection(agent_id, component, effective_date, changes)

    def assert_carriere_affectation(self, carriere_id, row, changed_fields):
        from sme_gend.services.mysql_ref_etl import (
            load_ref_cache_from_db, load_subdiv_flat_rows, prepare_carriere_write_fields,
        )
        columns = {'structure': 'structure_code', 'fonction': 'fonction_libelle',
                   'subdivision_arme': 'subdivision_arme_libelle'}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                expected = prepare_carriere_write_fields(
                    cur, load_ref_cache_from_db(cur), row,
                    flat_rows=load_subdiv_flat_rows(cur), auto_create=False,
                )
                cur.execute('SELECT structure_code, fonction_libelle, subdivision_arme_libelle FROM carriere_situation WHERE id=%s',
                            (int(carriere_id),))
                saved = cur.fetchone()
        normalize = lambda value: str(value or '').strip().casefold()
        for index, (field, column) in enumerate(columns.items()):
            if field in changed_fields and (not saved or normalize(saved[index]) != normalize(expected[column])):
                raise ValueError(
                    "L'affectation demandée est remplacée par une autre affectation de l'historique. "
                    "Vérifiez le mois et les dates dans la fiche complète, rubrique Affectations. "
                    "Aucune modification enregistrée."
                )

    @atomic_repository_write(agent_arg="agent_id")
    def sync_carriere_from_dossier(
        self,
        agent_id: int,
        carriere_id: str,
        *,
        ref_cache: "RefEtlCache | None" = None,
        flat_rows: "list[SubdivisionFlatRow] | None" = None,
        components: frozenset[str] | None = None,
    ) -> None:
        """Projection complète dossier → fiche carrière (diplôme, grade, affectation)."""
        cutoff = self._carriere_cutoff(carriere_id)
        components = components if components is not None else frozenset({'diplome', 'grade', 'affectation'})
        if 'diplome' in components:
            self.sync_carriere_diplome_from_dossier(agent_id, carriere_id, cutoff=cutoff)
        if 'grade' in components:
            self.sync_carriere_grade_from_passations(agent_id, carriere_id, cutoff=cutoff)
        if 'affectation' in components:
            self.sync_carriere_affectation_from_dossier(
                agent_id, carriere_id, ref_cache=ref_cache, flat_rows=flat_rows, cutoff=cutoff
            )
