"""Tables détail situation numérique (Access)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.numerique_satellite import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    StageDetail,
)
from sme_gend.repositories.access_connection import AccessConnection


class AccessNumeriqueSatelliteRepository:
    def __init__(self, settings: Settings) -> None:
        self._factory = AccessConnection(settings)

    def load_missions(self) -> dict[str, MissionDetail]:
        out: dict[str, MissionDetail] = {}
        q = "SELECT ID, Nature, LieuMission, Refnds, DateDepart FROM TabMission"
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            for row in cur.fetchall():
                sid = _norm_id(row[0])
                if not sid or sid in out:
                    continue
                out[sid] = MissionDetail(
                    nature=_str(row[1]),
                    lieu_mission=_str(row[2]),
                    refnds=_str(row[3]),
                    date_depart=_str(row[4]),
                )
        return out

    def load_malades(self) -> dict[str, MaladeDetail]:
        out: dict[str, MaladeDetail] = {}
        q = "SELECT ID, DateConstat, Pathologie, Obs FROM TabMalade"
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            for row in cur.fetchall():
                sid = _norm_id(row[0])
                if not sid or sid in out:
                    continue
                out[sid] = MaladeDetail(
                    date_constat=_str(row[1]),
                    pathologie=_str(row[2]),
                    obs=_str(row[3]),
                )
        return out

    def load_deserteurs(self) -> dict[str, DeserteurDetail]:
        out: dict[str, DeserteurDetail] = {}
        q = "SELECT ID, DatDesertion FROM TabDeserteur"
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            for row in cur.fetchall():
                sid = _norm_id(row[0])
                if not sid or sid in out:
                    continue
                out[sid] = DeserteurDetail(dat_desertion=_str(row[1]))
        return out

    def load_stages_loc(self) -> dict[str, StageDetail]:
        return self._load_stage_table("TabStageLocal")

    def load_stages_ex(self) -> dict[str, StageDetail]:
        return self._load_stage_table("TabStageEx")

    def _load_stage_table(self, table: str) -> dict[str, StageDetail]:
        out: dict[str, StageDetail] = {}
        q = f"SELECT ID, Naturestage, LieuStage, DateDebutFin FROM [{table}]"
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            for row in cur.fetchall():
                sid = _norm_id(row[0])
                if not sid or sid in out:
                    continue
                out[sid] = StageDetail(
                    nature_stage=_str(row[1]),
                    lieu_stage=_str(row[2]),
                    date_debut_fin=_str(row[3]),
                )
        return out

    def upsert_malade(self, person_id: str, detail: MaladeDetail) -> None:
        kid = _norm_id(person_id)
        if not kid:
            raise ValueError("Identifiant TabMalade vide.")
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM TabMalade WHERE ID = ?", (kid,))
            n = int(cur.fetchone()[0])
            if n:
                cur.execute(
                    "UPDATE TabMalade SET DateConstat=?, Pathologie=?, Obs=? WHERE ID=?",
                    (detail.date_constat, detail.pathologie, detail.obs, kid),
                )
            else:
                cur.execute(
                    "INSERT INTO TabMalade (ID, DateConstat, Pathologie, Obs) VALUES (?,?,?,?)",
                    (kid, detail.date_constat, detail.pathologie, detail.obs),
                )

    def delete_malade(self, person_id: str) -> None:
        kid = _norm_id(person_id)
        if not kid:
            return
        with self._factory.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TabMalade WHERE ID = ?", (kid,))


def _norm_id(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
