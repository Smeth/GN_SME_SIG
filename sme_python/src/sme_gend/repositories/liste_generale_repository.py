"""Accès lecture à `ListeGenerale`."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.access_connection import AccessConnection
from sme_gend.padmin_present import padmin_counts_as_present

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
        """Personnes comptées présentes : « présent » dans Padmin ou stage interne (voir padmin_counts_as_present)."""
        rows = self.fetch_all()
        return [r for r in rows if padmin_counts_as_present(r.position_admin)]

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

    def update_structure_column(self, old_code: str, new_code: str) -> int:
        """Met à jour le champ Structure pour toutes les lignes concernées."""
        o = (old_code or "").strip()
        n = (new_code or "").strip()
        if not o or not n:
            return 0
        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ListeGenerale SET Structure = ? WHERE Structure = ?",
                (n, o),
            )
            return int(cur.rowcount or 0)

    def rename_subarme_for_structure(
        self, structure_code: str, old_label: str, new_label: str
    ) -> int:
        """
        Renomme les SubArme pour une structure donnée.
        `old_label` / `new_label` : libellés tels qu'en base ; « (Sans subdivision) » = vide / NULL.
        """
        sc = (structure_code or "").strip()
        if not sc:
            return 0
        old_l = (old_label or "").strip()
        new_l = (new_label or "").strip()
        old_empty = old_l in ("", "(Sans subdivision)")
        new_empty = new_l in ("", "(Sans subdivision)")
        new_val = None if new_empty else new_l

        with self._conn_factory.connect() as conn:
            cur = conn.cursor()
            if old_empty:
                cur.execute(
                    """
                    UPDATE ListeGenerale SET SubArme = ?
                    WHERE Structure = ? AND (SubArme IS NULL OR SubArme = '')
                    """,
                    (new_val, sc),
                )
            else:
                cur.execute(
                    """
                    UPDATE ListeGenerale SET SubArme = ?
                    WHERE Structure = ? AND SubArme = ?
                    """,
                    (new_val, sc, old_l),
                )
                return int(cur.rowcount or 0)
