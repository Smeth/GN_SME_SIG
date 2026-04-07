"""Accès lecture à `ListeGenerale`."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.access_connection import AccessConnection

# Ordre des colonnes = ordre du SELECT (évite les ambiguïtés de noms Access).
SELECT_LISTE_GENERALE = """
SELECT
    Id,
    noms,
    DateN,
    LieuN,
    Grade,
    DateGrade,
    DateService,
    Ndecision,
    Matmilitaire,
    Matsolde,
    Padmin,
    Fonction,
    SubArme,
    Diplome,
    DateObtention,
    Structure,
    Obs,
    OderTri,
    presseance
FROM ListeGenerale
"""


class ListeGeneraleRepository:
    """Repository : lecture et mise à jour de la liste générale."""

    def __init__(self, settings: Settings) -> None:
        self._conn_factory = AccessConnection(settings)

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        """Une ligne par identifiant Id (tolère int / str)."""
        rid = (row_id or "").strip()
        if not rid:
            return None
        try:
            id_param: str | int = int(rid)
        except ValueError:
            id_param = rid
        q = f"{SELECT_LISTE_GENERALE.strip()} WHERE Id = ?"
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q, (id_param,))
            r = cur.fetchone()
            if not r:
                return None
            return ListeGeneraleRow.from_sequence(r)

    def fetch_all(self) -> list[ListeGeneraleRow]:
        """Toutes les lignes (tri par ordre métier si défini)."""
        with self._conn_factory.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"{SELECT_LISTE_GENERALE} ORDER BY OderTri, Id")
            return [ListeGeneraleRow.from_sequence(r) for r in cursor.fetchall()]

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        """Personnes dont la position administrative est « présent » (tolérance accents)."""
        rows = self.fetch_all()
        return [r for r in rows if _is_present(r.position_admin)]

    def fetch_distinct_padmin(self) -> list[str]:
        """Valeurs distinctes de position administrative (liste générale)."""
        q = """
        SELECT DISTINCT Padmin FROM ListeGenerale
        WHERE Padmin IS NOT NULL AND Padmin <> ''
        ORDER BY Padmin
        """
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme(self) -> list[str]:
        """Valeurs distinctes SubArme (effectifs)."""
        q = """
        SELECT DISTINCT SubArme FROM ListeGenerale
        WHERE SubArme IS NOT NULL AND SubArme <> ''
        ORDER BY SubArme
        """
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme_for_structure(self, structure_code: str) -> list[str]:
        """Subdivisions d'arme distinctes pour une structure (champ ListeGenerale.Structure)."""
        q = """
        SELECT DISTINCT SubArme
        FROM ListeGenerale
        WHERE Structure = ? AND SubArme IS NOT NULL AND SubArme <> ''
        ORDER BY SubArme
        """
        code = (structure_code or "").strip()
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(q, (code,))
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def update_row(self, row: ListeGeneraleRow) -> None:
        """Met à jour une ligne ListeGenerale (identifiant Id inchangé)."""
        sql = """
        UPDATE ListeGenerale SET
            noms = ?,
            DateN = ?,
            LieuN = ?,
            Grade = ?,
            DateGrade = ?,
            DateService = ?,
            Ndecision = ?,
            Matmilitaire = ?,
            Matsolde = ?,
            Padmin = ?,
            Fonction = ?,
            SubArme = ?,
            Diplome = ?,
            DateObtention = ?,
            Structure = ?,
            Obs = ?,
            OderTri = ?,
            presseance = ?
        WHERE Id = ?
        """
        id_param: str | int = row.id
        try:
            id_param = int(str(row.id).strip())
        except ValueError:
            id_param = row.id

        params = (
            row.noms,
            row.date_naissance,
            row.lieu_naissance,
            row.grade,
            row.date_grade,
            row.date_service,
            row.ndecision,
            row.matricule_militaire,
            row.matricule_solde,
            row.position_admin,
            row.fonction,
            row.subdivision_arme,
            row.diplome,
            row.date_obtention,
            row.structure,
            row.obs,
            row.ordre_tri,
            row.presseance,
            id_param,
        )
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            if cur.rowcount == 0:
                raise ValueError(f"Aucune ligne mise à jour pour Id = {row.id!r}")


def _is_present(padmin: str | None) -> bool:
    if not padmin:
        return False
    return "present" in padmin.strip().lower().replace("é", "e").replace("è", "e")
