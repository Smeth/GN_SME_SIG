"""Référentiel des positions administratives (SQLite)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.repositories.sqlite_common import BaseSqliteRepository


class SqlitePadminRepository(BaseSqliteRepository):
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqlitePadminRepository")
        super().__init__(Path(settings.sqlite_liste_path).expanduser().resolve())

    def fetch_all(self) -> list[PadminRefRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT libelle, compte_comme_present, ordre_tri FROM tab_padmin "
                "ORDER BY ordre_tri, libelle"
            )
            return [PadminRefRow.from_sequence(r) for r in cur.fetchall()]

    def insert(self, libelle: str, compte_comme_present: bool, ordre_tri: int | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tab_padmin (libelle, compte_comme_present, ordre_tri) VALUES (?, ?, ?)",
                (libelle.strip(), int(compte_comme_present), ordre_tri),
            )
            conn.commit()

    def update(
        self,
        old_libelle: str,
        new_libelle: str,
        compte_comme_present: bool,
        ordre_tri: int | None,
    ) -> None:
        """Met à jour la position admin et renomme dans liste_generale si le libellé change."""
        old = old_libelle.strip()
        new = new_libelle.strip()
        with self._connect() as conn:
            if old != new:
                # Renommer dans la liste générale pour préserver la cohérence
                conn.execute(
                    "UPDATE liste_generale SET padmin = ?, updated_at = datetime('now') "
                    "WHERE padmin = ?",
                    (new, old),
                )
            conn.execute(
                "UPDATE tab_padmin SET libelle = ?, compte_comme_present = ?, ordre_tri = ? "
                "WHERE libelle = ?",
                (new, int(compte_comme_present), ordre_tri, old),
            )
            conn.commit()

    def delete(self, libelle: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tab_padmin WHERE libelle = ?", (libelle.strip(),))
            conn.commit()
