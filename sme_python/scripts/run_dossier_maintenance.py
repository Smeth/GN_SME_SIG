#!/usr/bin/env python3
"""Maintenance dossier : schema, dedupe, resync leger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.services.automatic_maintenance import automatic_write

# Import services avant repositories (evite import circulaire).
from sme_gend.services.career_import_alerts import scan_all_career_alerts
from sme_gend.services.sot_status_service import fetch_sot_status_summary


def _mc():
    from sme_gend.repositories.mysql_connection import MySQLConnection

    return MySQLConnection
def ensure_ref_fonction_structure(settings) -> bool:
    from sme_gend.repositories.mysql_connection import has_import_transaction
    if has_import_transaction(settings):raise RuntimeError('Initialisation DDL interdite dans une transaction metier.')
    MySQLConnection = _mc()
    mc = MySQLConnection(settings)
    ddl = """
CREATE TABLE IF NOT EXISTS ref_fonction_structure (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL,
  fonction_id BIGINT NOT NULL,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_fonction_struct (structure_id, fonction_id),
  CONSTRAINT fk_rfs_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_rfs_fonction FOREIGN KEY (fonction_id) REFERENCES ref_fonction (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = 'ref_fonction_structure'
                """
            )
            if cur.fetchone()[0]:
                return False
            cur.execute(ddl)
        conn.commit()
    return True


@automatic_write
def backfill_fonction_structure(settings) -> int:
    from sme_gend.services.referentiel_service import ReferentielService

    return ReferentielService(settings).fonction_backfill_structure_links()


def dedupe_dossier_tables(settings, *, expected=None) -> dict[str, int]:
    from sme_gend.services.automatic_jobs import dedupe_dossier_tables as dedupe
    return dedupe(settings,expected=expected)


@automatic_write
def light_resync(settings, *, mois: str) -> None:
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.sot_maintenance import (
        project_carriere_from_dossier_batch,
        resync_fk_referentiels,
    )

    resync_fk_referentiels(settings, mois=mois)
    project_carriere_from_dossier_batch(settings, mois=mois, only_est_actuelle=True)
    invalidate_liste_cache(settings)


def main(argv=None) -> int:
    from sme_gend.services.automatic_cli import run_cli
    return run_cli('dossier',argv)


if __name__ == "__main__":
    raise SystemExit(main())
