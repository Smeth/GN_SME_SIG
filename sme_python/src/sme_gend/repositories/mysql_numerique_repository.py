"""Situation numérique v2 — lectures agent_mission / malade / desertion / parcours_stage."""

from __future__ import annotations

from sme_gend.models.personnel_identity import stage_source_access_id

from sme_gend.repositories.monthly_write_guard import UNSET, revision_sql, token_from_row, checked_target

from sme_gend.repositories.write_guard import atomic_repository_write, history_read_sql

from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import (
    AgentCongeRow,
    AgentDesertionRow,
    AgentMaladeRow,
    AgentMissionRow,
    AgentMouvementRow,
    AgentStageRow,
)
from sme_gend.services.agent_conge_service import CONGE_TYPE_LABELS
from sme_gend.models.numerique_satellite import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    StageDetail,
)
from sme_gend.repositories.mysql_common import BaseMySQLRepository


def _norm_id(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _date_str(v: object) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()  # type: ignore[union-attr]
    return _str(v)


SOURCE_SAISIE_MARK = ":saisie:"


def _is_saisie_manuelle(legacy: object) -> bool:
    """Vrai si la ligne vient d'une saisie dans l'application (et non de l'archive)."""
    return SOURCE_SAISIE_MARK in (_str(legacy) or "")


def _check_malade_dates(date_debut: date, date_fin: date | None) -> None:
    if date_fin is not None and date_fin < date_debut:
        raise ValueError("La date de fin est antérieure à la date de début.")


class MySQLNumeriqueRepository(BaseMySQLRepository):
    """Événements par agent ; plusieurs lignes possibles par agent et par type."""

    def _agent_id(self, cur, legacy_or_join_key: str) -> int | None:
        leg = _norm_id(legacy_or_join_key)
        if not leg:
            return None
        cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
        row = cur.fetchone()
        return int(row[0]) if row else None

    def list_missions_for_agent(self, legacy_id: str) -> list[AgentMissionRow]:
        aid = None
        rows_out: list[AgentMissionRow] = []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return []
                cur.execute(
                    history_read_sql(self._settings, """
                          SELECT m.date_debut, m.date_fin, m.nature_libelle, m.lieu,
                              m.reference_dossier, p.annee_mois, m.id, m.observation
                    FROM agent_mission m
                    LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
                    WHERE m.agent_id = %s
                    ORDER BY m.date_debut DESC, m.id DESC
                    """, 'm'),
                    (aid,),
                )
                for r in cur.fetchall():
                    rows_out.append(
                        AgentMissionRow(
                            _date_str(r[0]),
                            _date_str(r[1]),
                            _str(r[2]),
                            _str(r[3]),
                            _str(r[4]),
                            _str(r[5]),
                            int(r[6]),
                            _str(r[7]),
                        )
                    )
        return rows_out

    def list_stages_for_agent(self, legacy_id: str) -> list[AgentStageRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return []
                cur.execute(
                    history_read_sql(self._settings, """
                          SELECT ps.type_stage_code, ps.date_debut, ps.date_fin,
                              ps.nature_libelle, ps.lieu, ps.observation, p.annee_mois,
                              ps.id, ps.reference_dossier, ps.session
                    FROM parcours_stage ps
                    LEFT JOIN sme_periode p ON p.id = ps.sme_periode_id
                    WHERE ps.agent_id = %s
                    ORDER BY ps.date_debut DESC, ps.id DESC
                    """, 'ps'),
                    (aid,),
                )
                return [
                    AgentStageRow(
                        str(r[0] or ""),
                        _date_str(r[1]),
                        _date_str(r[2]),
                        _str(r[3]),
                        _str(r[4]),
                        _str(r[5]),
                        _str(r[6]),
                        int(r[7]),
                        reference_dossier=_str(r[8]),
                        session=_str(r[9]),
                    )
                    for r in cur.fetchall()
                ]

    def list_malades_for_agent(self, legacy_id: str) -> list[AgentMaladeRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return []
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT m.id, m.date_debut, m.date_fin, m.pathologie, m.observation,
                           p.annee_mois, m.source_ligne_legacy
                    FROM agent_malade m
                    LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
                    WHERE m.agent_id = %s
                    ORDER BY m.date_debut DESC, m.id DESC
                    """, 'm'),
                    (aid,),
                )
                return [
                    AgentMaladeRow(
                        _date_str(r[1]),
                        _date_str(r[2]),
                        _str(r[3]),
                        _str(r[4]),
                        _str(r[5]),
                        malade_id=int(r[0]) if r[0] is not None else None,
                        saisie_manuelle=_is_saisie_manuelle(r[6]),
                    )
                    for r in cur.fetchall()
                ]

    def list_desertions_for_agent(self, legacy_id: str) -> list[AgentDesertionRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return []
                cur.execute(
                    history_read_sql(self._settings, """
                    SELECT d.date_desertion, p.annee_mois, d.id, d.observation, d.reference_dossier
                    FROM agent_desertion d
                    LEFT JOIN sme_periode p ON p.id = d.sme_periode_id
                    WHERE d.agent_id = %s
                    ORDER BY d.date_desertion DESC, d.id DESC
                    """, 'd'),
                    (aid,),
                )
                return [
                    AgentDesertionRow(
                        _date_str(r[0]), _str(r[1]), int(r[2]), _str(r[3]), reference_dossier=_str(r[4])
                    )
                    for r in cur.fetchall()
                ]

    def list_mouvements_for_agent(self, legacy_id: str) -> list[AgentMouvementRow]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return []
                cur.execute(
                    """
                    SELECT p.annee_mois, m.categorie_mouvement, m.motif,
                           m.grade_snapshot, m.nom_snapshot, m.date_reference
                    FROM mouvement_effectif m
                    INNER JOIN sme_periode p ON p.id = m.sme_periode_id
                    WHERE m.agent_id = %s
                    ORDER BY m.date_reference DESC, m.id DESC
                    """,
                    (aid,),
                )
                return [
                    AgentMouvementRow(
                        _str(r[0]),
                        _str(r[1]),
                        _str(r[2]),
                        _str(r[3]),
                        _str(r[4]),
                        _date_str(r[5]),
                    )
                    for r in cur.fetchall()
                ]

    def list_conges_for_agent(self, legacy_id: str) -> list[AgentCongeRow]:
        from sme_gend.services.import_stability import is_missing_table_error

        try:
            with self._connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    aid = self._agent_id(cur, legacy_id)
                    if not aid:
                        return []
                    cur.execute(
                        history_read_sql(self._settings, """
                           SELECT c.type_conge_code, c.date_debut, c.date_fin, c.quota_jours,
                               c.observation, p.annee_mois, c.source_code, c.id
                        FROM agent_conge c
                        LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                        WHERE c.agent_id = %s
                        ORDER BY c.date_debut DESC, c.id DESC
                        """, 'c'),
                        (aid,),
                    )
                    out: list[AgentCongeRow] = []
                    for r in cur.fetchall():
                        tc = str(r[0] or "")
                        q = r[3]
                        q_s = f"{q:.1f}" if q is not None else None
                        out.append(
                            AgentCongeRow(
                                type_conge=tc,
                                type_label=CONGE_TYPE_LABELS.get(tc, tc),
                                date_debut=_date_str(r[1]),
                                date_fin=_date_str(r[2]),
                                quota_jours=q_s,
                                observation=_str(r[4]),
                                periode_sme=_str(r[5]),
                                source_code=_str(r[6]),
                                id=int(r[7]),
                            )
                        )
                    return out
        except Exception as e:
            if is_missing_table_error(e):
                return []
            raise

    def load_latest_conges_for_period(self, *, by_type: bool = False) -> dict:
        from sme_gend.repositories.temporal_reads import load_details
        return load_details(self,'conge',by_type=by_type)

    def load_position_events_for_period(self) -> dict:
        from sme_gend.repositories.temporal_reads import load_positions
        return load_positions(self)

    def _export_mois(self) -> str:
        from sme_gend.config.liste_generale_scope import resolve_export_mois

        return (resolve_export_mois(self._settings) or "").strip()

    def _periode_filter_sql(self, *, alias: str = "m") -> tuple[str, tuple]:
        ym = self._export_mois()
        col = (alias or "m").strip() or "m"
        if ym:
            return (
                f" AND ({col}.sme_periode_id = "
                f"(SELECT id FROM sme_periode WHERE annee_mois=%s LIMIT 1))",
                (ym,),
            )
        return "", ()

    def _scoped_latest_subquery(
        self,
        table: str,
        date_expr: str,
        *,
        extra_where: str = "",
    ) -> str:
        return f"""
        SELECT agent_id, MAX({date_expr}) AS dmax
        FROM {table}
        WHERE sme_periode_id = (SELECT id FROM sme_periode WHERE annee_mois=%s LIMIT 1)
        {extra_where}
        GROUP BY agent_id
        """

    def load_missions(self, *, legacy_id: str | None = None) -> dict[str, MissionDetail]:
        from sme_gend.repositories.temporal_reads import load_details
        return load_details(self,'mission',legacy_id=legacy_id)

    def load_malades(self, *, legacy_id: str | None = None) -> dict[str, MaladeDetail]:
        from sme_gend.repositories.temporal_reads import load_details
        return load_details(self,'malade',legacy_id=legacy_id)

    def load_deserteurs(self) -> dict[str, DeserteurDetail]:
        from sme_gend.repositories.temporal_reads import load_details
        return load_details(self,'desertion')

    def load_stages_loc(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._load_stages_merged("LOCAL", **({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_stages_ex(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._load_stages_merged("EXTERIEUR", **({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_stages_internal(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._load_stages_merged("INTERNE", **({} if legacy_id is None else {'legacy_id': legacy_id}))

    def _load_stages_merged(self, type_code: str, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        from sme_gend.repositories.temporal_reads import load_details
        return load_details(self,'stage',type_code=type_code,legacy_id=legacy_id)

    def _load_stages(self, type_code: str) -> dict[str, StageDetail]:
        """Lecture stricte du mois SME courant (tests / usages internes)."""
        from sme_gend.config.liste_generale_scope import resolve_export_mois

        ym = (resolve_export_mois(self._settings) or "").strip() or None
        return self._load_stages_for_periode(type_code, ym)

    def _load_stages_for_periode(
        self,
        type_code: str,
        periode_ym: str | None,
        *, legacy_id: str | None = None,
    ) -> dict[str, StageDetail]:
        out: dict[str, StageDetail] = {}
        if periode_ym:
            q = """
            SELECT COALESCE(ps.source_ligne_legacy, a.legacy_access_id),
                   ps.nature_libelle, ps.lieu, ps.observation,
                   ps.date_debut, ps.date_fin,
                   a.legacy_access_id, ps.source_ligne_legacy, ps.id, ps.sme_periode_id, __WRITE_REVISION__
            FROM parcours_stage ps
            INNER JOIN agent a ON a.id = ps.agent_id
            INNER JOIN sme_periode p ON p.id = ps.sme_periode_id AND p.annee_mois = %s
            WHERE ps.type_stage_code = %s
            ORDER BY COALESCE(ps.date_debut, '1900-01-01') DESC, ps.id DESC
            """
            params: tuple = (periode_ym, type_code)
        else:
            q = """
            SELECT COALESCE(ps.source_ligne_legacy, a.legacy_access_id),
                   ps.nature_libelle, ps.lieu, ps.observation,
                   ps.date_debut, ps.date_fin,
                   a.legacy_access_id, ps.source_ligne_legacy, ps.id, ps.sme_periode_id, __WRITE_REVISION__
            FROM parcours_stage ps
            INNER JOIN agent a ON a.id = ps.agent_id
            INNER JOIN (
              SELECT agent_id, MAX(COALESCE(date_debut, '1900-01-01')) AS dmax
              FROM parcours_stage WHERE type_stage_code = %s
              GROUP BY agent_id
            ) last ON last.agent_id = ps.agent_id
              AND COALESCE(ps.date_debut, '1900-01-01') = last.dmax
            WHERE ps.type_stage_code = %s
            ORDER BY COALESCE(ps.date_debut, '1900-01-01') DESC, ps.id DESC
            """
            params = (type_code, type_code)
        if legacy_id is not None:
            head, order = q.rsplit('ORDER BY', 1)
            q = head + ' AND (a.legacy_access_id=%s OR ps.source_ligne_legacy=%s) ORDER BY' + order
            params += (legacy_id, legacy_id)
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(q.replace("__WRITE_REVISION__", revision_sql("parcours_stage", "ps") + ", ps.reference_dossier, ps.session"), params)
                for row in cur.fetchall():
                    deb, fin = row[4], row[5]
                    ddf = ""
                    if deb and fin:
                        ddf = f"{_date_str(deb)} — {_date_str(fin)}"
                    elif deb:
                        ddf = _date_str(deb) or ""
                    elif fin:
                        ddf = _date_str(fin) or ""
                    detail = StageDetail(
                        nature_stage=_str(row[1]),
                        lieu_stage=_str(row[2]),
                        date_debut_fin=ddf or _str(row[3]),
                        write_token=token_from_row(row, 8),
                        reference_dossier=_str(row[11]) if len(row) > 11 else None,
                        session=_str(row[12]) if len(row) > 12 else None,
                    )
                    keys = {_norm_id(row[0])}
                    if len(row) > 6:
                        keys.add(_norm_id(row[6]))
                    if len(row) > 7:
                        keys.add(_norm_id(row[7]))
                    for sid in keys:
                        if not sid:
                            continue
                        self._merge_stage_detail(out, sid, detail)
        return out

    @staticmethod
    def _merge_stage_detail(
        out: dict[str, StageDetail],
        sid: str,
        detail: StageDetail,
    ) -> None:
        prev = out.get(sid)
        if prev is None:
            out[sid] = detail
            return
        if MySQLNumeriqueRepository._stage_detail_richer(detail, prev):
            out[sid] = detail

    @staticmethod
    def _stage_detail_richer(new: StageDetail, old: StageDetail) -> bool:
        def score(d: StageDetail) -> int:
            return int(bool((d.nature_stage or "").strip())) * 4 + int(
                bool((d.lieu_stage or "").strip())
            ) * 2 + int(bool((d.date_debut_fin or "").strip()))

        return score(new) > score(old)

    @atomic_repository_write(legacy_arg="legacy_id")
    def add_malade_row(
        self,
        legacy_id: str,
        *,
        date_debut: date,
        date_fin: date | None,
        pathologie: str | None,
        observation: str | None,
    ) -> None:
        """Ajoute une fiche maladie saisie dans l'application pour cet agent.

        ``sme_periode_id`` reste NULL : la fiche n'appartient à aucun mois d'archive
        et survit donc au réimport d'une période, qui n'efface que les lignes
        ``WHERE sme_periode_id = %s``.
        """
        leg = _norm_id(legacy_id)
        if not leg:
            raise ValueError("Identifiant agent requis.")
        _check_malade_dates(date_debut, date_fin)
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, leg)
                if not aid:
                    raise ValueError(f"Agent inconnu : {leg}")
                cur.execute(
                    """
                    INSERT INTO agent_malade (
                      agent_id, sme_periode_id, date_debut, date_fin, pathologie, observation,
                      source_ligne_legacy
                    ) VALUES (%s, NULL, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid,
                        date_debut,
                        date_fin,
                        _str(pathologie),
                        _str(observation),
                        f"{leg}{SOURCE_SAISIE_MARK}{date_debut}",
                    ),
                )

    @atomic_repository_write(event_table="agent_malade", id_arg="malade_id")
    def update_malade_row(
        self,
        malade_id: int,
        *,
        date_debut: date,
        date_fin: date | None,
        pathologie: str | None,
        observation: str | None,
    ) -> None:
        """Met à jour la fiche maladie désignée par son ``id`` (importée ou saisie)."""
        _check_malade_dates(date_debut, date_fin)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent_malade WHERE id = %s", (int(malade_id),))
                if not cur.fetchone():
                    raise ValueError("Fiche maladie introuvable — a-t-elle été supprimée ?")
                cur.execute(
                    """
                    UPDATE agent_malade
                    SET date_debut = %s, date_fin = %s, pathologie = %s, observation = %s
                    WHERE id = %s
                    """,
                    (
                        date_debut,
                        date_fin,
                        _str(pathologie),
                        _str(observation),
                        int(malade_id),
                    ),
                )

    @atomic_repository_write(event_table="agent_malade", id_arg="malade_id")
    def delete_malade_row(self, malade_id: int) -> None:
        """Supprime une seule fiche maladie, désignée par son ``id``."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_malade WHERE id = %s", (int(malade_id),))

    @atomic_repository_write(legacy_arg="person_id")
    def upsert_malade(self, person_id: str, detail: MaladeDetail, *, expected=UNSET) -> None:
        """Crée/remplace la fiche maladie *du mois de vue courant* pour cet agent.

        Le DELETE et l'INSERT sont tous les deux limités à ``sme_periode_id``
        (mois de vue courant, ou NULL si aucun mois n'est configuré) — la même
        portée que ``load_malades()``. Sans ce filtre, sauvegarder une fiche pour
        le mois affiché supprimait aussi les fiches historiques d'autres mois
        pour ce même agent (perte de données silencieuse).
        """
        kid = _norm_id(person_id)
        if not kid:
            raise ValueError("Identifiant agent vide.")
        from sme_gend.services.access_date_parse import parse_access_date

        d0 = parse_access_date(detail.date_constat)
        if d0 is None:
            raise ValueError("La date de constat est obligatoire et doit être valide.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (kid, kid))
                ag = cur.fetchone()
                if not ag:
                    raise ValueError(f"Agent inconnu : {kid}")
                aid = int(ag[0])
                pid = self._periode_id(cur)
                target = checked_target(cur, "agent_malade", aid, pid, kid, expected)
                if target is not UNSET and target is not None:
                    cur.execute("UPDATE agent_malade SET date_debut=%s,pathologie=%s,observation=%s WHERE id=%s AND agent_id=%s",
                                (d0, detail.pathologie, detail.obs, target, aid))
                    return
                if pid is not None:
                    cur.execute(
                        "DELETE FROM agent_malade WHERE agent_id = %s AND source_ligne_legacy = %s"
                        " AND sme_periode_id = %s",
                        (aid, kid, pid),
                    )
                else:
                    cur.execute(
                        "DELETE FROM agent_malade WHERE agent_id = %s AND source_ligne_legacy = %s"
                        " AND sme_periode_id IS NULL",
                        (aid, kid),
                    )
                cur.execute(
                    """
                    INSERT INTO agent_malade (
                      agent_id, sme_periode_id, date_debut, pathologie, observation, source_ligne_legacy
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (aid, pid, d0, detail.pathologie, detail.obs, kid),
                )

    def _periode_id(self, cur) -> int | None:
        from sme_gend.services.temporal_events import PERIOD_OVERRIDE
        override=PERIOD_OVERRIDE.get()
        if override is not None and override[0] is self:
            return override[1]
        from sme_gend.config.liste_generale_scope import resolve_export_mois

        ym = (resolve_export_mois(self._settings) or "").strip()
        if not ym:
            return None
        from sme_gend.services.month_period_scope import month_period_ids
        matching = month_period_ids(cur, ym)
        if len(matching) != 1:
            raise ValueError("La période demandée n'existe pas. Rechargez les périodes avant d'enregistrer.")
        return matching[0]

    @atomic_repository_write(legacy_arg="person_id")
    def upsert_mission(self, person_id: str, detail: MissionDetail, *, expected=UNSET) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        kid = _norm_id(person_id)
        if not kid:
            raise ValueError("Identifiant agent vide.")
        d0 = parse_access_date(detail.date_depart)
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, kid)
                if not aid:
                    raise ValueError(f"Agent inconnu : {kid}")
                pid = self._periode_id(cur)
                target = checked_target(cur, "agent_mission", aid, pid, kid, expected)
                if target is not UNSET and target is not None:
                    cur.execute("UPDATE agent_mission SET date_debut=%s,nature_libelle=%s,lieu=%s,reference_dossier=%s WHERE id=%s AND agent_id=%s",
                                (d0, detail.nature, detail.lieu_mission, detail.refnds, target, aid))
                    return
                cur.execute(
                    "DELETE FROM agent_mission WHERE agent_id = %s AND source_ligne_legacy = %s "
                    "AND sme_periode_id <=> %s",
                    (aid, kid, pid),
                )
                cur.execute(
                    """
                    INSERT INTO agent_mission (
                      agent_id, sme_periode_id, date_debut, nature_libelle, lieu,
                      reference_dossier, source_ligne_legacy
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid,
                        pid,
                        d0,
                        detail.nature,
                        detail.lieu_mission,
                        detail.refnds,
                        kid,
                    ),
                )

    @atomic_repository_write(legacy_arg="legacy_id")
    def add_mission_for_agent(
        self,
        legacy_id: str,
        *,
        date_debut: str | None,
        date_fin: str | None,
        nature: str | None,
        lieu: str | None,
        reference_dossier: str | None,
        observation: str | None,
    ) -> int:
        from sme_gend.services.access_date_parse import parse_access_date

        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    INSERT INTO agent_mission (
                      agent_id, sme_periode_id, date_debut, date_fin, nature_libelle,
                      lieu, reference_dossier, observation
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid, self._periode_id(cur), parse_access_date(date_debut),
                        parse_access_date(date_fin), nature, lieu, reference_dossier,
                        observation,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def update_mission_for_agent(
        self,
        legacy_id: str,
        mission_id: int,
        *,
        date_debut: str | None,
        date_fin: str | None,
        nature: str | None,
        lieu: str | None,
        reference_dossier: str | None,
        observation: str | None,
    ) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    UPDATE agent_mission
                    SET date_debut=%s, date_fin=%s, nature_libelle=%s, lieu=%s,
                        reference_dossier=%s, observation=%s
                    WHERE id=%s AND agent_id=%s
                    """,
                    (
                        parse_access_date(date_debut), parse_access_date(date_fin),
                        nature, lieu, reference_dossier, observation,
                        int(mission_id), aid,
                    ),
                )
                self._raise_if_event_missing(cur, "agent_mission", mission_id, aid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def delete_mission_for_agent(self, legacy_id: str, mission_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if aid:
                    cur.execute(
                        "DELETE FROM agent_mission WHERE id=%s AND agent_id=%s",
                        (int(mission_id), aid),
                    )
                    self._raise_if_event_missing(cur, "agent_mission", mission_id, aid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def add_desertion_for_agent(
        self, legacy_id: str, *, date_desertion: str, observation: str | None, reference_dossier: str | None = None
    ) -> int:
        from sme_gend.services.access_date_parse import parse_access_date

        event_date = parse_access_date(date_desertion)
        if event_date is None:
            raise ValueError("La date de désertion est requise.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    INSERT INTO agent_desertion (
                      agent_id, sme_periode_id, date_desertion, observation, reference_dossier
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (aid, self._periode_id(cur), event_date, observation, reference_dossier),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def update_desertion_for_agent(
        self,
        legacy_id: str,
        desertion_id: int,
        *,
        date_desertion: str,
        observation: str | None,
        reference_dossier: str | None = None,
    ) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        event_date = parse_access_date(date_desertion)
        if event_date is None:
            raise ValueError("La date de désertion est requise.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    UPDATE agent_desertion SET date_desertion=%s, observation=%s, reference_dossier=%s
                    WHERE id=%s AND agent_id=%s
                    """,
                    (event_date, observation, reference_dossier, int(desertion_id), aid),
                )
                self._raise_if_event_missing(cur, "agent_desertion", desertion_id, aid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def delete_desertion_for_agent(self, legacy_id: str, desertion_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if aid:
                    cur.execute(
                        "DELETE FROM agent_desertion WHERE id=%s AND agent_id=%s",
                        (int(desertion_id), aid),
                    )
                    self._raise_if_event_missing(cur, "agent_desertion", desertion_id, aid)

    @atomic_repository_write(legacy_arg="person_id")
    def upsert_desertion(self, person_id: str, detail: DeserteurDetail, *, expected=UNSET) -> None:
        """Crée/remplace la fiche désertion du mois de vue courant (comme ``upsert_malade``)."""
        from sme_gend.services.access_date_parse import parse_access_date

        kid = _norm_id(person_id)
        if not kid:
            raise ValueError("Identifiant agent vide.")
        d0 = parse_access_date(detail.dat_desertion)
        if d0 is None:
            raise ValueError("La date de désertion est obligatoire et doit être valide.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, kid)
                if not aid:
                    raise ValueError(f"Agent inconnu : {kid}")
                pid = self._periode_id(cur)
                target = checked_target(cur, "agent_desertion", aid, pid, kid, expected)
                if target is not UNSET and target is not None:
                    cur.execute("UPDATE agent_desertion SET date_desertion=%s,reference_dossier=%s WHERE id=%s AND agent_id=%s",
                                (d0, detail.reference_dossier, target, aid))
                    return
                if pid is not None:
                    cur.execute(
                        "DELETE FROM agent_desertion WHERE agent_id = %s AND source_ligne_legacy = %s"
                        " AND sme_periode_id = %s",
                        (aid, kid, pid),
                    )
                else:
                    cur.execute(
                        "DELETE FROM agent_desertion WHERE agent_id = %s AND source_ligne_legacy = %s"
                        " AND sme_periode_id IS NULL",
                        (aid, kid),
                    )
                cur.execute(
                    """
                    INSERT INTO agent_desertion (
                      agent_id, sme_periode_id, date_desertion, source_ligne_legacy, reference_dossier
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (aid, pid, d0, kid, detail.reference_dossier),
                )

    @atomic_repository_write(legacy_arg="person_id")
    def delete_desertion(self, person_id: str, *, expected=UNSET) -> None:
        """Supprime la fiche désertion du mois de vue courant pour cet agent."""
        kid = _norm_id(person_id)
        if not kid:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                pid = self._periode_id(cur)
                if expected is not UNSET:
                    aid = self._agent_id(cur, kid)
                    target = checked_target(cur, "agent_desertion", aid, pid, kid, expected)
                    if target is not None:
                        cur.execute("DELETE FROM agent_desertion WHERE id=%s AND agent_id=%s", (target, aid))
                    return
                if pid is not None:
                    cur.execute(
                        """
                        DELETE d FROM agent_desertion d
                        INNER JOIN agent a ON a.id = d.agent_id
                        WHERE (a.legacy_access_id = %s OR d.source_ligne_legacy = %s)
                          AND d.sme_periode_id = %s
                        """,
                        (kid, kid, pid),
                    )
                else:
                    cur.execute(
                        """
                        DELETE d FROM agent_desertion d
                        INNER JOIN agent a ON a.id = d.agent_id
                        WHERE (a.legacy_access_id = %s OR d.source_ligne_legacy = %s)
                          AND d.sme_periode_id IS NULL
                        """,
                        (kid, kid),
                    )

    @staticmethod
    def _raise_if_event_missing(cur, table: str, event_id: int, agent_id: int) -> None:
        if cur.rowcount:
            return
        cur.execute(
            f"SELECT 1 FROM {table} WHERE id=%s AND agent_id=%s",
            (int(event_id), int(agent_id)),
        )
        if cur.fetchone() is None:
            raise ValueError("Événement introuvable pour cet agent.")

    @atomic_repository_write(legacy_arg="person_id")
    def upsert_stage(
        self,
        person_id: str,
        detail: StageDetail,
        *,
        type_code: str,
        date_debut: str | None = None,
        date_fin: str | None = None,
        expected=UNSET,
    ) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        kid = _norm_id(person_id)
        if not kid:
            raise ValueError("Identifiant agent vide.")
        tc = (type_code or "").strip().upper()
        if tc not in ("LOCAL", "EXTERIEUR", "INTERNE"):
            raise ValueError(f"type_stage_code invalide : {type_code!r}")
        d0 = parse_access_date(date_debut)
        d1 = parse_access_date(date_fin)
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, kid)
                if not aid:
                    raise ValueError(f"Agent inconnu : {kid}")
                pid = self._periode_id(cur)
                target = checked_target(cur, "parcours_stage", aid, pid, kid, expected, type_code=tc)
                if target is not UNSET and target is not None:
                    cur.execute("UPDATE parcours_stage SET date_debut=%s,date_fin=%s,nature_libelle=%s,lieu=%s,observation=%s,reference_dossier=%s,session=%s WHERE id=%s AND agent_id=%s",
                                (d0, d1, detail.nature_stage, detail.lieu_stage, detail.date_debut_fin, detail.reference_dossier, detail.session, target, aid))
                    return
                cur.execute(
                    """
                    INSERT INTO ref_type_stage (code, libelle) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE libelle = VALUES(libelle)
                    """,
                    (tc, {"LOCAL": "Stage local", "EXTERIEUR": "Stage extérieur", "INTERNE": "Stage interne"}[tc]),
                )
                if pid is not None:
                    cur.execute(
                        """
                        DELETE FROM parcours_stage
                        WHERE agent_id = %s AND type_stage_code = %s
                          AND (source_ligne_legacy = %s OR source_ligne_legacy IS NULL)
                          AND sme_periode_id = %s
                        """,
                        (aid, tc, kid, pid),
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM parcours_stage
                        WHERE agent_id = %s AND type_stage_code = %s
                          AND (source_ligne_legacy = %s OR source_ligne_legacy IS NULL)
                          AND sme_periode_id IS NULL
                        """,
                        (aid, tc, kid),
                    )
                cur.execute(
                    """
                    INSERT INTO parcours_stage (
                      agent_id, sme_periode_id, type_stage_code, date_debut, date_fin,
                      lieu, nature_libelle, source_ligne_legacy, observation, reference_dossier, session
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid,
                        pid,
                        tc,
                        d0,
                        d1,
                        detail.lieu_stage,
                        detail.nature_stage,
                        stage_source_access_id(kid),
                        detail.date_debut_fin,
                        detail.reference_dossier,
                        detail.session,
                    ),
                )

    def _delete_stage_for_period(self, cur, aid: int, kid: str, tc: str, pid: int | None) -> None:
        if pid is not None:
            cur.execute(
                """
                DELETE FROM parcours_stage
                WHERE agent_id = %s AND type_stage_code = %s
                  AND (source_ligne_legacy = %s OR source_ligne_legacy IS NULL)
                  AND sme_periode_id = %s
                """,
                (aid, tc, kid, pid),
            )
        else:
            cur.execute(
                """
                DELETE FROM parcours_stage
                WHERE agent_id = %s AND type_stage_code = %s
                  AND (source_ligne_legacy = %s OR source_ligne_legacy IS NULL)
                  AND sme_periode_id IS NULL
                """,
                (aid, tc, kid),
            )

    @atomic_repository_write(legacy_arg="person_id")
    def clear_stage_for_period(self, person_id: str, type_code: str) -> None:
        """Retire la fiche stage du mois SME consulté (sans toucher aux autres mois)."""
        kid = _norm_id(person_id)
        if not kid:
            return
        tc = (type_code or "").strip().upper()
        if tc not in ("LOCAL", "EXTERIEUR", "INTERNE"):
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, kid)
                if not aid:
                    return
                pid = self._periode_id(cur)
                self._delete_stage_for_period(cur, aid, kid, tc, pid)

    @atomic_repository_write(legacy_arg="person_id")
    def clear_other_stages_for_period(self, person_id: str, *, keep_type: str, expected=UNSET) -> None:
        if expected is not UNSET and self.list_stages_for_agent(person_id) != expected:
            raise ValueError("Les stages de cet agent ont changé depuis l'ouverture du formulaire. Rechargez les données.")
        keep = (keep_type or "").strip().upper()
        for tc in ("LOCAL", "EXTERIEUR", "INTERNE"):
            if tc != keep:
                self.clear_stage_for_period(person_id, tc)

    @atomic_repository_write(legacy_arg="legacy_id")
    def add_stage_for_agent(
        self,
        legacy_id: str,
        *,
        type_code: str,
        date_debut: str | None,
        date_fin: str | None,
        nature: str | None,
        lieu: str | None,
        observation: str | None,
        reference_dossier: str | None = None,
        session: str | None = None,
    ) -> int:
        from sme_gend.services.access_date_parse import parse_access_date

        tc = (type_code or "").strip().upper()
        if tc not in ("LOCAL", "EXTERIEUR", "INTERNE"):
            raise ValueError(f"type_stage_code invalide : {type_code!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    INSERT INTO ref_type_stage (code, libelle) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE libelle = VALUES(libelle)
                    """,
                    (tc, {"LOCAL": "Stage local", "EXTERIEUR": "Stage extérieur", "INTERNE": "Stage interne"}[tc]),
                )
                cur.execute(
                    """
                    INSERT INTO parcours_stage (
                      agent_id, type_stage_code, date_debut, date_fin, nature_libelle,
                      lieu, observation, reference_dossier, session
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aid,
                        tc,
                        parse_access_date(date_debut),
                        parse_access_date(date_fin),
                        nature,
                        lieu,
                        observation,
                        reference_dossier,
                        session,
                    ),
                )
                return int(cur.lastrowid)

    @atomic_repository_write(legacy_arg="legacy_id")
    def update_stage_for_agent(
        self,
        legacy_id: str,
        stage_id: int,
        *,
        type_code: str,
        date_debut: str | None,
        date_fin: str | None,
        nature: str | None,
        lieu: str | None,
        observation: str | None,
        reference_dossier: str | None = None,
        session: str | None = None,
    ) -> None:
        from sme_gend.services.access_date_parse import parse_access_date

        tc = (type_code or "").strip().upper()
        if tc not in ("LOCAL", "EXTERIEUR", "INTERNE"):
            raise ValueError(f"type_stage_code invalide : {type_code!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    raise ValueError(f"Agent inconnu : {_norm_id(legacy_id)}")
                cur.execute(
                    """
                    INSERT INTO ref_type_stage (code, libelle) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE libelle = VALUES(libelle)
                    """,
                    (tc, {"LOCAL": "Stage local", "EXTERIEUR": "Stage extérieur", "INTERNE": "Stage interne"}[tc]),
                )
                cur.execute(
                    """
                    UPDATE parcours_stage
                    SET type_stage_code = %s, date_debut = %s, date_fin = %s,
                        nature_libelle = %s, lieu = %s, observation = %s, reference_dossier = %s, session = %s
                    WHERE id = %s AND agent_id = %s
                    """,
                    (
                        tc,
                        parse_access_date(date_debut),
                        parse_access_date(date_fin),
                        nature,
                        lieu,
                        observation,
                        reference_dossier,
                        session,
                        int(stage_id),
                        aid,
                    ),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        "SELECT 1 FROM parcours_stage WHERE id = %s AND agent_id = %s",
                        (int(stage_id), aid),
                    )
                    if cur.fetchone() is None:
                        raise ValueError("Stage introuvable pour cet agent.")

    @atomic_repository_write(legacy_arg="legacy_id")
    def delete_stage_for_agent(self, legacy_id: str, stage_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                aid = self._agent_id(cur, legacy_id)
                if not aid:
                    return
                cur.execute(
                    "DELETE FROM parcours_stage WHERE id = %s AND agent_id = %s",
                    (int(stage_id), aid),
                )

    @atomic_repository_write(legacy_arg="person_id")
    def delete_malade(self, person_id: str, *, expected=UNSET) -> None:
        """Supprime la fiche maladie **du mois de vue courant** pour cet agent.

        Même portée que ``load_malades()`` et ``upsert_malade()`` : sans filtre sur
        ``sme_periode_id``, l'écran « Malades / punitions » effaçait tout l'historique
        maladie de l'agent (autres mois importés et fiches saisies dans la fiche
        personnel compris).
        """
        kid = _norm_id(person_id)
        if not kid:
            return
        like_saisie = f"%{SOURCE_SAISIE_MARK}%"
        with self._connect() as conn:
            with conn.cursor() as cur:
                pid = self._periode_id(cur)
                if expected is not UNSET:
                    aid = self._agent_id(cur, kid)
                    target = checked_target(cur, "agent_malade", aid, pid, kid, expected)
                    if target is not None:
                        cur.execute("DELETE FROM agent_malade WHERE id=%s AND agent_id=%s "
                                    "AND (source_ligne_legacy IS NULL OR source_ligne_legacy NOT LIKE %s)",
                                    (target, aid, like_saisie))
                        if not cur.rowcount:
                            raise ValueError("Cette maladie a été saisie dans le dossier personnel. Modifiez-la depuis ce dossier.")
                    return
                if pid is not None:
                    cur.execute(
                        """
                        DELETE m FROM agent_malade m
                        INNER JOIN agent a ON a.id = m.agent_id
                        WHERE (a.legacy_access_id = %s OR m.source_ligne_legacy = %s)
                          AND m.sme_periode_id = %s
                          AND (m.source_ligne_legacy IS NULL
                               OR m.source_ligne_legacy NOT LIKE %s)
                        """,
                        (kid, kid, pid, like_saisie),
                    )
                else:
                    cur.execute(
                        """
                        DELETE m FROM agent_malade m
                        INNER JOIN agent a ON a.id = m.agent_id
                        WHERE (a.legacy_access_id = %s OR m.source_ligne_legacy = %s)
                          AND m.sme_periode_id IS NULL
                          AND (m.source_ligne_legacy IS NULL
                               OR m.source_ligne_legacy NOT LIKE %s)
                        """,
                        (kid, kid, like_saisie),
                    )


from sme_gend.services.temporal_events import install_temporal_writes
install_temporal_writes(MySQLNumeriqueRepository, {
 'agent_mission': ('upsert_mission','add_mission_for_agent','update_mission_for_agent','delete_mission_for_agent'),
 'agent_malade': ('upsert_malade','add_malade_row','update_malade_row','delete_malade','delete_malade_row'),
 'agent_desertion': ('upsert_desertion','add_desertion_for_agent','update_desertion_for_agent','delete_desertion_for_agent','delete_desertion'),
 'parcours_stage': ('upsert_stage','add_stage_for_agent','update_stage_for_agent','delete_stage_for_agent','clear_stage_for_period'),
})
