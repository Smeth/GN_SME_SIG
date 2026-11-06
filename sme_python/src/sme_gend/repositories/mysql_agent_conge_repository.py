"""Écriture registre ``agent_conge``."""

from __future__ import annotations

from sme_gend.repositories.write_guard import atomic_repository_write

from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.services.agent_conge_service import quota_days_between, upsert_conge_row
from sme_gend.services.mysql_import_period import ensure_sme_periode, normalize_import_mois


class MySQLAgentCongeRepository(BaseMySQLRepository):
    @atomic_repository_write(legacy_arg="legacy_access_id")
    def upsert_manual(
        self,
        legacy_access_id: str,
        *,
        type_code: str,
        date_debut: date,
        date_fin: date | None,
        quota_jours: float | None,
        observation: str | None,
        sme_mois: str | None,
        insert_only: bool = False,
    ) -> None:
        leg = str(legacy_access_id or "").strip()
        if not leg:
            raise ValueError("Identifiant agent requis.")
        q = quota_jours
        if q is None:
            q = quota_days_between(date_debut, date_fin)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Agent inconnu : {leg}")
                aid = int(row[0])
                pid = None
                if sme_mois:
                    ym = normalize_import_mois(sme_mois)
                    pid = ensure_sme_periode(conn, ym)
                legacy = f"{leg}:saisie:{date_debut}:{type_code}"
                if insert_only:
                    cur.execute("SELECT id FROM agent_conge WHERE agent_id=%s "
                                "AND sme_periode_id <=> %s AND source_ligne_legacy=%s FOR UPDATE",
                                (aid, pid, legacy))
                    if cur.fetchone() is not None:
                        raise ValueError("Ce congé existe déjà. Rechargez le dossier puis utilisez Modifier.")
                upsert_conge_row(
                    cur,
                    agent_id=aid,
                    sme_periode_id=pid,
                    type_code=str(type_code or "AUTRE"),
                    date_debut=date_debut,
                    date_fin=date_fin,
                    quota_jours=q,
                    observation=observation,
                    source_code="saisie",
                    legacy=legacy,
                    import_lot_id=None,
                )

    @atomic_repository_write(legacy_arg="legacy_access_id")
    def update_manual(
        self,
        legacy_access_id: str,
        conge_id: int,
        *,
        type_code: str,
        date_debut: date,
        date_fin: date | None,
        quota_jours: float | None,
        observation: str | None,
        sme_mois: str | None,
    ) -> None:
        leg = str(legacy_access_id or "").strip()
        if not leg:
            raise ValueError("Identifiant agent requis.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Agent inconnu : {leg}")
                pid = None
                if sme_mois:
                    pid = ensure_sme_periode(conn, normalize_import_mois(sme_mois))
                cur.execute(
                    """
                    UPDATE agent_conge
                    SET sme_periode_id=%s, type_conge_code=%s, date_debut=%s,
                        date_fin=%s, quota_jours=%s, observation=%s, source_code='saisie'
                    WHERE id=%s AND agent_id=%s
                    """,
                    (
                        pid,
                        str(type_code or "AUTRE"),
                        date_debut,
                        date_fin,
                        quota_jours if quota_jours is not None else quota_days_between(date_debut, date_fin),
                        observation,
                        int(conge_id),
                        int(row[0]),
                    ),
                )
                self._raise_if_missing(cur, int(conge_id), int(row[0]))

    @atomic_repository_write(legacy_arg="legacy_access_id")
    def delete_for_agent(self, legacy_access_id: str, conge_id: int) -> None:
        leg = str(legacy_access_id or "").strip()
        if not leg:
            raise ValueError("Identifiant agent requis.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Agent inconnu : {leg}")
                aid = int(row[0])
                cur.execute(
                    "DELETE FROM agent_conge WHERE id=%s AND agent_id=%s",
                    (int(conge_id), aid),
                )
                self._raise_if_missing(cur, int(conge_id), aid)

    @staticmethod
    def _raise_if_missing(cur, conge_id: int, agent_id: int) -> None:
        if cur.rowcount:
            return
        cur.execute(
            "SELECT 1 FROM agent_conge WHERE id=%s AND agent_id=%s",
            (conge_id, agent_id),
        )
        if cur.fetchone() is None:
            raise ValueError("Congé introuvable pour cet agent.")


from sme_gend.services.temporal_events import install_temporal_writes
install_temporal_writes(MySQLAgentCongeRepository, {'agent_conge': ('upsert_manual','update_manual','delete_for_agent')})
