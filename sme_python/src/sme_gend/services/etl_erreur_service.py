"""Journalisation des erreurs ETL MySQL (table ``etl_erreur``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection


@dataclass(frozen=True, slots=True)
class EtlErreurRow:
    id: int
    import_lot_id: int | None
    table_cible: str | None
    cle_legacy: str | None
    message: str | None
    cree_le: str | None

    @classmethod
    def from_sequence(cls, row: tuple[Any, ...]) -> EtlErreurRow:
        return cls(
            id=int(row[0]),
            import_lot_id=int(row[1]) if row[1] is not None else None,
            table_cible=str(row[2] or "").strip() or None,
            cle_legacy=str(row[3] or "").strip() or None,
            message=str(row[4] or "").strip() or None,
            cree_le=str(row[5]) if row[5] is not None else None,
        )


def log_etl_erreur(
    cur: Any,
    *,
    import_lot_id: int | None,
    table_cible: str,
    cle_legacy: str | None,
    message: str,
) -> None:
    """Insère une ligne dans ``etl_erreur`` (lot import optionnel)."""
    msg = (message or "").strip()
    if not msg:
        return
    cur.execute(
        """
        INSERT INTO etl_erreur (import_lot_id, table_cible, cle_legacy, message)
        VALUES (%s, %s, %s, %s)
        """,
        (
            int(import_lot_id) if import_lot_id else None,
            (table_cible or "").strip()[:128] or None,
            (cle_legacy or "").strip()[:512] or None,
            msg[:4000],
        ),
    )


def log_etl_erreur_settings(
    settings: Settings,
    *,
    import_lot_id: int | None,
    table_cible: str,
    cle_legacy: str | None,
    message: str,
) -> None:
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            log_etl_erreur(
                cur,
                import_lot_id=import_lot_id,
                table_cible=table_cible,
                cle_legacy=cle_legacy,
                message=message,
            )
        conn.commit()


def count_etl_erreurs(settings: Settings, *, import_lot_id: int | None = None) -> int:
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            if import_lot_id is not None:
                cur.execute(
                    "SELECT COUNT(*) FROM etl_erreur WHERE import_lot_id = %s",
                    (int(import_lot_id),),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM etl_erreur")
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0


def fetch_etl_erreurs(
    settings: Settings,
    *,
    limit: int = 500,
    import_lot_id: int | None = None,
) -> list[EtlErreurRow]:
    """Dernières lignes du journal ETL (ordre anti-chronologique)."""
    lim = max(1, min(int(limit), 5000))
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            if import_lot_id is not None:
                cur.execute(
                    """
                    SELECT id, import_lot_id, table_cible, cle_legacy, message, cree_le
                    FROM etl_erreur
                    WHERE import_lot_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (int(import_lot_id), lim),
                )
            else:
                cur.execute(
                    """
                    SELECT id, import_lot_id, table_cible, cle_legacy, message, cree_le
                    FROM etl_erreur
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (lim,),
                )
            return [EtlErreurRow.from_sequence(r) for r in cur.fetchall()]
