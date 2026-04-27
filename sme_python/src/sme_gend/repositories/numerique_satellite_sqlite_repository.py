"""Tables détail situation numérique (SQLite, clé = Id Access / id_acces)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.numerique_satellite import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    StageDetail,
)
from sme_gend.repositories.sqlite_common import apply_sqlite_schema


class SqliteNumeriqueSatelliteRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    def load_missions(self) -> dict[str, MissionDetail]:
        out: dict[str, MissionDetail] = {}
        q = "SELECT id, nature, lieu_mission, refnds, date_depart FROM tab_mission"
        with self._connect() as conn:
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
        q = "SELECT id, date_constat, pathologie, obs FROM tab_malade"
        with self._connect() as conn:
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
        q = "SELECT id, dat_desertion FROM tab_deserteur"
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            for row in cur.fetchall():
                sid = _norm_id(row[0])
                if not sid or sid in out:
                    continue
                out[sid] = DeserteurDetail(dat_desertion=_str(row[1]))
        return out

    def load_stages_loc(self) -> dict[str, StageDetail]:
        return self._load_stage_table("tab_stage_local")

    def load_stages_ex(self) -> dict[str, StageDetail]:
        return self._load_stage_table("tab_stage_ex")

    def _load_stage_table(self, table: str) -> dict[str, StageDetail]:
        out: dict[str, StageDetail] = {}
        q = f"SELECT id, nature_stage, lieu_stage, date_debut_fin FROM {table}"
        with self._connect() as conn:
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
