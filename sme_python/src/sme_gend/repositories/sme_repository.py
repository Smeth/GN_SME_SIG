"""Lecture / écriture `TabSme` (période mensuelle SME)."""

from __future__ import annotations

from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.access_connection import AccessConnection


def _pick(
    d: dict[str, Any],
    row: tuple[Any, ...],
    keys: tuple[str, ...],
    fallback_index: int,
) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    if fallback_index < len(row):
        return row[fallback_index]
    return None


class SmeRepository:
    """Accès à la table TabSme (colonnes attendues : Codsme, mois, Statut)."""

    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def table_exists(self) -> bool:
        try:
            with self._conn.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM TabSme")
                cur.fetchone()
            return True
        except Exception:
            return False

    def fetch_all(self) -> list[SmeRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT Codsme, mois, Statut FROM TabSme ORDER BY mois")
                rows_raw = cur.fetchall()
                return [SmeRow.from_sequence(r) for r in rows_raw]
            except Exception:
                cur.execute("SELECT * FROM TabSme")
                desc = [str(c[0] or "").lower() for c in (cur.description or [])]
                rows_raw = cur.fetchall()

        out: list[SmeRow] = []
        for r in rows_raw:
            d = {desc[i]: r[i] for i in range(min(len(desc), len(r)))}
            cod = _pick(d, r, ("codsme", "id", "idsme", "code"), 0)
            mois = _pick(d, r, ("mois", "periode", "date_mois"), 1)
            st = _pick(d, r, ("statut", "status", "etat"), 2)
            out.append(SmeRow.from_sequence((cod, mois, st)))
        return out

    def insert(self, codsme: str, mois: str, statut: str) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO TabSme (Codsme, mois, Statut) VALUES (?, ?, ?)",
                (codsme, mois, statut),
            )

    def update(self, codsme: str, mois: str, statut: str) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE TabSme SET mois = ?, Statut = ? WHERE Codsme = ?",
                (mois, statut, codsme),
            )

    def delete(self, codsme: str) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TabSme WHERE Codsme = ?", (codsme,))
