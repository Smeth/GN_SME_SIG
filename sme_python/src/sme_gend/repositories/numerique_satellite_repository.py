"""Lecture des tables détail situation numérique (TabMission, TabMalade, …) — clé ID = ListeGenerale.Id."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection


@dataclass(frozen=True, slots=True)
class MissionDetail:
    nature: str | None
    lieu_mission: str | None
    refnds: str | None
    date_depart: str | None


@dataclass(frozen=True, slots=True)
class MaladeDetail:
    date_constat: str | None
    pathologie: str | None
    obs: str | None


@dataclass(frozen=True, slots=True)
class DeserteurDetail:
    dat_desertion: str | None


@dataclass(frozen=True, slots=True)
class StageDetail:
    nature_stage: str | None
    lieu_stage: str | None
    date_debut_fin: str | None


class NumeriqueSatelliteRepository:
    """Données satellites jointes sur Id (VARCHAR) — une entrée par ID (première ligne si doublons)."""

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


def _norm_id(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
