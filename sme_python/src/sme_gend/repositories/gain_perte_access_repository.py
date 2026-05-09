"""Repository Access pour ``TabGainPerte``."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.access_connection import AccessConnection


def _row(r: object) -> GainPerteRow:
    def g(i: int):  # type: ignore[return]
        try:
            return r[i]  # type: ignore[index]
        except (IndexError, TypeError):
            return None

    def s(v: object) -> str | None:
        if v is None:
            return None
        x = str(v).strip()
        return x or None

    return GainPerteRow(
        id=str(g(0) or "").strip(),
        grade=s(g(1)),
        nom=s(g(2)),
        objet=s(g(3)),
        cdsme=s(g(4)),
        observ=s(g(5)),
    )


class AccessGainPerteRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_all(self) -> list[GainPerteRow]:
        q = "SELECT ID, Grade, nom, Objet, Cdsme, Observ FROM TabGainPerte"
        try:
            with self._conn.connect() as conn:
                cur = conn.cursor()
                cur.execute(q)
                return [_row(r) for r in cur.fetchall()]
        except Exception:
            return []

    def fetch_for_cdsme(self, cdsme: str) -> list[GainPerteRow]:
        q = "SELECT ID, Grade, nom, Objet, Cdsme, Observ FROM TabGainPerte WHERE Cdsme = ?"
        try:
            with self._conn.connect() as conn:
                cur = conn.cursor()
                cur.execute(q, (cdsme,))
                return [_row(r) for r in cur.fetchall()]
        except Exception:
            return []
