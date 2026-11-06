"""Référentiels ref_medaille et ref_decoration."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.distinction_ref import DistinctionRefRow
from sme_gend.repositories.mysql_common import BaseMySQLRepository

_MED_COLS = (
    "id, code, libelle_court, libelle_long, categorie, ordre_preseance, actif"
)
_DEC_COLS = _MED_COLS


class MySQLDistinctionRepository(BaseMySQLRepository):
    def table_ready(self) -> bool:
        try:
            with self._connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name IN ('ref_medaille', 'ref_decoration', 'agent_distinction')
                        LIMIT 1
                        """
                    )
                    return cur.fetchone() is not None
        except Exception:
            return False

    def list_medailles(self, *, actif_only: bool = False) -> list[DistinctionRefRow]:
        sql = f"SELECT {_MED_COLS} FROM ref_medaille"
        if actif_only:
            sql += " WHERE actif = 1"
        sql += " ORDER BY ordre_preseance IS NULL, ordre_preseance, libelle_court, code"
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [DistinctionRefRow.from_sequence(r) for r in cur.fetchall()]

    def list_decorations(self, *, actif_only: bool = False) -> list[DistinctionRefRow]:
        sql = f"SELECT {_DEC_COLS} FROM ref_decoration"
        if actif_only:
            sql += " WHERE actif = 1"
        sql += " ORDER BY ordre_preseance IS NULL, ordre_preseance, libelle_court, code"
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [DistinctionRefRow.from_sequence(r) for r in cur.fetchall()]

    def insert_medaille(
        self,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> int:
        c = (code or "").strip()
        if not c:
            raise ValueError("Le code médaille est obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ref_medaille (
                      code, libelle_court, libelle_long, categorie, ordre_preseance, actif
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        c,
                        (libelle_court or "").strip() or None,
                        (libelle_long or "").strip() or None,
                        (categorie or "").strip() or None,
                        ordre_preseance,
                        1 if actif else 0,
                    ),
                )
                return int(cur.lastrowid)

    def update_medaille(
        self,
        ref_id: int,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> None:
        c = (code or "").strip()
        if not c:
            raise ValueError("Le code médaille est obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ref_medaille SET
                      code=%s, libelle_court=%s, libelle_long=%s,
                      categorie=%s, ordre_preseance=%s, actif=%s
                    WHERE id=%s
                    """,
                    (
                        c,
                        (libelle_court or "").strip() or None,
                        (libelle_long or "").strip() or None,
                        (categorie or "").strip() or None,
                        ordre_preseance,
                        1 if actif else 0,
                        int(ref_id),
                    ),
                )

    def delete_medaille(self, ref_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ref_medaille WHERE id = %s", (int(ref_id),))

    def insert_decoration(
        self,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> int:
        c = (code or "").strip()
        if not c:
            raise ValueError("Le code décoration est obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ref_decoration (
                      code, libelle_court, libelle_long, categorie, ordre_preseance, actif
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        c,
                        (libelle_court or "").strip() or None,
                        (libelle_long or "").strip() or None,
                        (categorie or "").strip() or None,
                        ordre_preseance,
                        1 if actif else 0,
                    ),
                )
                return int(cur.lastrowid)

    def update_decoration(
        self,
        ref_id: int,
        *,
        code: str,
        libelle_court: str | None,
        libelle_long: str | None = None,
        categorie: str | None = None,
        ordre_preseance: int | None = None,
        actif: bool = True,
    ) -> None:
        c = (code or "").strip()
        if not c:
            raise ValueError("Le code décoration est obligatoire.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ref_decoration SET
                      code=%s, libelle_court=%s, libelle_long=%s,
                      categorie=%s, ordre_preseance=%s, actif=%s
                    WHERE id=%s
                    """,
                    (
                        c,
                        (libelle_court or "").strip() or None,
                        (libelle_long or "").strip() or None,
                        (categorie or "").strip() or None,
                        ordre_preseance,
                        1 if actif else 0,
                        int(ref_id),
                    ),
                )

    def delete_decoration(self, ref_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ref_decoration WHERE id = %s", (int(ref_id),))

    def count_attributions(self) -> int:
        if not self.table_ready():
            return 0
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM agent_distinction")
                row = cur.fetchone()
                return int(row[0] or 0) if row else 0

    def seed_defaults_if_empty(self) -> None:
        """Nomenclature minimale si les tables sont vides (saisie dossier possible)."""
        med_seed = [
            ("MM", "Médaille militaire", "Médaille militaire", "Médaille", 100),
            ("CSM", "Croix du combattant", "Croix du combattant volontaire", "Médaille", 110),
            ("ONM", "Médaille de la Gendarmerie", "Médaille de la Gendarmerie nationale", "Médaille", 120),
        ]
        dec_seed = [
            ("LH", "Légion d'honneur", "Ordre national de la Légion d'honneur", "Ordre national", 10),
            ("ONM_ORD", "Ordre national du Mérite", "Ordre national du Mérite", "Ordre national", 20),
            ("MDN", "Médaille de la Défense", "Médaille de la Défense nationale", "Distinction", 30),
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ref_medaille")
                n_med = int((cur.fetchone() or (0,))[0])
                if n_med == 0:
                    for code, court, long_, cat, ordre in med_seed:
                        cur.execute(
                            """
                            INSERT INTO ref_medaille (
                              code, libelle_court, libelle_long, categorie, ordre_preseance, actif
                            ) VALUES (%s, %s, %s, %s, %s, 1)
                            """,
                            (code, court, long_, cat, ordre),
                        )
                cur.execute("SELECT COUNT(*) FROM ref_decoration")
                n_dec = int((cur.fetchone() or (0,))[0])
                if n_dec == 0:
                    for code, court, long_, cat, ordre in dec_seed:
                        cur.execute(
                            """
                            INSERT INTO ref_decoration (
                              code, libelle_court, libelle_long, categorie, ordre_preseance, actif
                            ) VALUES (%s, %s, %s, %s, %s, 1)
                            """,
                            (code, court, long_, cat, ordre),
                        )
