"""Upsert satellites (missions, malades) — fusion / dédoublonnage à la réimport."""

from __future__ import annotations

from typing import Any


def upsert_agent_mission(
    cur: Any,
    *,
    agent_id: int,
    sme_periode_id: int | None,
    date_debut: Any,
    date_fin: Any,
    nature: str | None,
    lieu: str | None,
    reference: str | None,
    legacy: str,
    import_lot_id: int | None,
) -> None:
    leg = (legacy or "").strip() or f"{agent_id}:{date_debut}:{nature or ''}"
    cur.execute(
        """
        INSERT INTO agent_mission (
          agent_id, sme_periode_id, date_debut, date_fin, nature_libelle, lieu,
          reference_dossier, source_ligne_legacy, import_lot_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          date_debut = COALESCE(VALUES(date_debut), date_debut),
          date_fin = COALESCE(VALUES(date_fin), date_fin),
          nature_libelle = COALESCE(VALUES(nature_libelle), nature_libelle),
          lieu = COALESCE(VALUES(lieu), lieu),
          reference_dossier = COALESCE(VALUES(reference_dossier), reference_dossier),
          import_lot_id = COALESCE(VALUES(import_lot_id), import_lot_id)
        """,
        (
            agent_id,
            sme_periode_id,
            date_debut,
            date_fin,
            nature,
            lieu,
            reference,
            leg,
            import_lot_id,
        ),
    )


def upsert_agent_malade(
    cur: Any,
    *,
    agent_id: int,
    sme_periode_id: int | None,
    date_debut: Any,
    date_fin: Any,
    pathologie: str | None,
    observation: str | None,
    legacy: str,
    import_lot_id: int | None,
) -> None:
    leg = (legacy or "").strip() or f"{agent_id}:{date_debut}:{pathologie or ''}"
    cur.execute(
        """
        INSERT INTO agent_malade (
          agent_id, sme_periode_id, date_debut, date_fin, pathologie, observation,
          source_ligne_legacy, import_lot_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          date_fin = COALESCE(VALUES(date_fin), date_fin),
          pathologie = COALESCE(VALUES(pathologie), pathologie),
          observation = COALESCE(VALUES(observation), observation),
          import_lot_id = COALESCE(VALUES(import_lot_id), import_lot_id)
        """,
        (
            agent_id,
            sme_periode_id,
            date_debut,
            date_fin,
            pathologie,
            observation,
            leg,
            import_lot_id,
        ),
    )
