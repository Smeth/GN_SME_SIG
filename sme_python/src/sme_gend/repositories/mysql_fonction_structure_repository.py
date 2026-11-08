"""Liaisons strictes structure ↔ fonction (ref_fonction_structure)."""

from __future__ import annotations

from sme_gend.repositories.reference_collection_guard import load_collection, require_collection, check_collection

from sme_gend.services.shared_reference_write import shared_reference_write, mark_reference_write

from sme_gend.config.settings import Settings
from sme_gend.config.liste_generale_scope import carriere_scope_for_settings, carriere_write_scope_for_settings
from sme_gend.repositories.mysql_bundle_repositories import BaseMySQLRepository


class MySQLFonctionStructureRepository(BaseMySQLRepository):
    def fetch_linked_labels_by_structure(self):
        result={}
        with self._connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT s.code,f.libelle FROM ref_fonction_structure l '
                    'JOIN ref_structure s ON s.id=l.structure_id JOIN ref_fonction f ON f.id=l.fonction_id '
                    'ORDER BY s.code,COALESCE(l.ordre_tri,9999),f.libelle')
                for code,label in cursor.fetchall():result.setdefault(str(code).strip().casefold(),[]).append(str(label).strip())
        return result

    def load_links_structure_snapshot(self,code):
        return load_collection(self._settings,"links_structure",code)

    def load_links_fonction_snapshot(self,fonction_id):
        return load_collection(self._settings,"links_fonction",fonction_id)

    def load_links_all_snapshot(self):
        return load_collection(self._settings,"links_all")

    def fetch_libelles_for_structure(self, structure_code: str) -> list[str]:
        code = (structure_code or "").strip()
        if not code:
            return []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rf.libelle
                    FROM ref_fonction_structure rfs
                    INNER JOIN ref_structure rs ON rs.id = rfs.structure_id
                    INNER JOIN ref_fonction rf ON rf.id = rfs.fonction_id
                    WHERE rs.code = %s
                    ORDER BY COALESCE(rfs.ordre_tri, 9999), rf.libelle
                    """,
                    (code,),
                )
                return [str(r[0]).strip() for r in cur.fetchall() if r and str(r[0] or "").strip()]

    def fetch_observed_libelles_for_structure(self, structure_code: str) -> list[str]:
        """Fonctions déjà portées par cette structure en carrière, sans rattachement saisi."""
        code = (structure_code or "").strip()
        if not code:
            return []
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT TRIM(COALESCE(rf.libelle, c.fonction_libelle)) AS lib
                    FROM carriere_situation c
                    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                    WHERE UPPER(TRIM(COALESCE(rs.code, c.structure_code))) = UPPER(TRIM(%s))
                      AND TRIM(COALESCE(rf.libelle, c.fonction_libelle, '')) <> ''
                    ORDER BY lib
                    """,
                    (code,),
                )
                return [str(r[0]).strip() for r in cur.fetchall() if r and str(r[0] or "").strip()]

    def fetch_observed_usage(self) -> list[tuple[str, str, int]]:
        """(code structure, libellé, nombre de fiches) du mois consulté."""
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                scope, scope_p = carriere_scope_for_settings(self._settings, cursor=cur)
                cur.execute(
                    f"""
                    SELECT TRIM(COALESCE(rs.code, c.structure_code)) AS code,
                           TRIM(COALESCE(rf.libelle, c.fonction_libelle)) AS lib,
                           COUNT(*) AS n
                    FROM carriere_situation c
                    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                    WHERE {scope} AND TRIM(COALESCE(rs.code, c.structure_code, '')) <> ''
                      AND TRIM(COALESCE(rf.libelle, c.fonction_libelle, '')) <> ''
                    GROUP BY TRIM(COALESCE(rs.code, c.structure_code)),
                             TRIM(COALESCE(rf.libelle, c.fonction_libelle))
                    """, scope_p
                )
                out: list[tuple[str, str, int]] = []
                for code, lib, n in cur.fetchall():
                    sc = str(code or "").strip()
                    lb = str(lib or "").strip()
                    if sc and lb:
                        out.append((sc, lb, int(n or 0)))
                return out

    def fetch_structure_codes_for_fonction(self, fonction_id: int) -> list[str]:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rs.code
                    FROM ref_fonction_structure rfs
                    INNER JOIN ref_structure rs ON rs.id = rfs.structure_id
                    WHERE rfs.fonction_id = %s
                    ORDER BY rs.ordre_tri, rs.code
                    """,
                    (int(fonction_id),),
                )
                return [str(r[0]).strip() for r in cur.fetchall() if r and str(r[0] or "").strip()]

    def fetch_choice_counts_by_structure(self) -> dict[str, int]:
        """Nombre de fonctions proposées par structure (clé = code en minuscules).

        Même règle que ``fetch_libelles_for_structure`` : le rattachement
        référentiel prime ; sinon les fonctions déjà vues en carrière.
        Deux requêtes, quel que soit le nombre de structures.
        """
        linked: dict[str, int] = {}
        observed: dict[str, int] = {}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rs.code, COUNT(*)
                    FROM ref_fonction_structure rfs
                    INNER JOIN ref_structure rs ON rs.id = rfs.structure_id
                    INNER JOIN ref_fonction rf ON rf.id = rfs.fonction_id
                    GROUP BY rs.code
                    """
                )
                for code, n in cur.fetchall():
                    key = str(code or "").strip().casefold()
                    if key:
                        linked[key] = int(n or 0)
                cur.execute(
                    """
                    SELECT UPPER(TRIM(COALESCE(rs.code, c.structure_code))) AS code_u,
                           COUNT(DISTINCT TRIM(COALESCE(rf.libelle, c.fonction_libelle))) AS n
                    FROM carriere_situation c
                    LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                    LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                    WHERE TRIM(COALESCE(rf.libelle, c.fonction_libelle, '')) <> ''
                    GROUP BY code_u
                    """
                )
                for code_u, n in cur.fetchall():
                    key = str(code_u or "").strip().casefold()
                    if key:
                        observed[key] = int(n or 0)
        out: dict[str, int] = {}
        for key in set(linked) | set(observed):
            n_linked = linked.get(key, 0)
            out[key] = n_linked if n_linked else observed.get(key, 0)
        return out

    def fetch_links_summary(self) -> dict[int, list[str]]:
        """fonction_id → codes structures rattachées."""
        out: dict[int, list[str]] = {}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rfs.fonction_id, rs.code
                    FROM ref_fonction_structure rfs
                    INNER JOIN ref_structure rs ON rs.id = rfs.structure_id
                    ORDER BY rfs.fonction_id, rs.code
                    """
                )
                for fid, code in cur.fetchall():
                    out.setdefault(int(fid), []).append(str(code).strip())
        return out

    def set_links_for_structure(self, structure_code: str, fonction_libelles: list[str], *, expected=None) -> None:
        """Remplace les fonctions rattachées à une structure (sans toucher les autres structures)."""
        code = (structure_code or "").strip()
        if not code:
            return
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(code):
            return
        libs = sorted({(x or "").strip() for x in fonction_libelles if (x or "").strip()})
        require_collection(expected,"links_structure",code)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_collection(cur,expected)
                    cur.execute("SELECT id FROM ref_structure WHERE code = %s LIMIT 1", (code,))
                    srow = cur.fetchone()
                    if not srow:
                        raise ValueError(f"Structure inconnue : « {code} ».")
                    sid = int(srow[0])
                    cur.execute("DELETE FROM ref_fonction_structure WHERE structure_id = %s", (sid,))
                    mark_reference_write(cur.rowcount > 0)
                    for i, lib in enumerate(libs):
                        cur.execute(
                            "SELECT id FROM ref_fonction WHERE libelle = %s LIMIT 1",
                            (lib,),
                        )
                        frow = cur.fetchone()
                        if not frow:
                            raise ValueError("Fonction inconnue. Actualisez les données.")
                        cur.execute(
                            """
                            INSERT INTO ref_fonction_structure (structure_id, fonction_id, ordre_tri)
                            VALUES (%s, %s, %s)
                            """,
                            (sid, int(frow[0]), i),
                        )
                        mark_reference_write(cur.rowcount > 0)
                conn.commit()

    def set_links_for_fonction(self, fonction_id: int, structure_codes: list[str], *, expected=None) -> None:
        codes = sorted({(c or "").strip() for c in structure_codes if (c or "").strip()})
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        codes = [c for c in codes if not structure_fonctions_non_liees(c)]
        fid = int(fonction_id)
        require_collection(expected,"links_fonction",fid)
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_collection(cur,expected)
                    cur.execute("DELETE FROM ref_fonction_structure WHERE fonction_id = %s", (fid,))
                    mark_reference_write(cur.rowcount > 0)
                    for i, code in enumerate(codes):
                        cur.execute("SELECT id FROM ref_structure WHERE code = %s LIMIT 1", (code,))
                        row = cur.fetchone()
                        if not row:
                            raise ValueError(f"Structure inconnue : « {code} ».")
                        cur.execute(
                            """
                            INSERT INTO ref_fonction_structure (structure_id, fonction_id, ordre_tri)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE ordre_tri = VALUES(ordre_tri)
                            """,
                            (int(row[0]), fid, i),
                        )
                        mark_reference_write(cur.rowcount > 0)
                conn.commit()

    def merge_links_for_fonction(self,fonction_id,structure_codes):
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees
        codes=sorted({code.strip() for code in structure_codes if code and code.strip() and not structure_fonctions_non_liees(code)})
        with shared_reference_write(self._settings):
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM ref_fonction WHERE id=%s FOR UPDATE",(int(fonction_id),))
                    if cursor.fetchone() is None:raise ValueError("Fonction inconnue. Actualisez les données.")
                    for code in codes:
                        cursor.execute("SELECT id FROM ref_structure WHERE code=%s FOR SHARE",(code,))
                        row=cursor.fetchone()
                        if row is None:raise ValueError("Structure inconnue. Actualisez les données.")
                        cursor.execute("INSERT IGNORE INTO ref_fonction_structure(structure_id,fonction_id) VALUES(%s,%s)",(int(row[0]),int(fonction_id)))
                        mark_reference_write(cursor.rowcount>0)

    def ensure_link(self, structure_code: str, fonction_libelle: str) -> None:
        """Crée le lien structure ↔ fonction s'il manque (saisie validée)."""
        code = (structure_code or "").strip()
        lib = (fonction_libelle or "").strip()
        if not code or not lib:
            return
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(code):
            return
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM ref_structure WHERE code = %s LIMIT 1", (code,))
                    srow = cur.fetchone()
                    if not srow:
                        return
                    cur.execute(
                        "SELECT id FROM ref_fonction WHERE libelle = %s LIMIT 1",
                        (lib,),
                    )
                    frow = cur.fetchone()
                    if not frow:
                        return
                    cur.execute(
                        """
                        INSERT IGNORE INTO ref_fonction_structure (structure_id, fonction_id)
                        VALUES (%s, %s)
                        """,
                        (int(srow[0]), int(frow[0])),
                    )
                    mark_reference_write(cur.rowcount > 0)
                conn.commit()

    def backfill_from_carriere(self) -> int:
        """Rattache (structure, fonction) observés en carrière active."""
        inserted = 0
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    scope, scope_p = carriere_write_scope_for_settings(self._settings, cursor=cur)
                    cur.execute(
                        f"""
                        SELECT DISTINCT
                          COALESCE(rs.code, TRIM(c.structure_code)) AS sc,
                          COALESCE(rf.libelle, TRIM(c.fonction_libelle)) AS fl
                        FROM carriere_situation c
                        LEFT JOIN ref_structure rs ON rs.id = c.structure_id
                        LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
                        WHERE {scope}
                          AND TRIM(COALESCE(c.fonction_libelle, '')) <> ''
                          AND TRIM(COALESCE(c.structure_code, COALESCE(rs.code, ''))) <> ''
                        """, scope_p
                    )
                    pairs = [
                        (str(a).strip(), str(b).strip())
                        for a, b in cur.fetchall()
                        if a and b
                    ]
                    for sc, fl in pairs:
                        from sme_gend.services.fonction_structure_policy import (
                            structure_fonctions_non_liees,
                        )

                        if structure_fonctions_non_liees(sc):
                            continue
                        cur.execute("SELECT id FROM ref_structure WHERE code = %s LIMIT 1", (sc,))
                        srow = cur.fetchone()
                        if not srow:
                            continue
                        cur.execute("SELECT id FROM ref_fonction WHERE libelle = %s LIMIT 1", (fl,))
                        frow = cur.fetchone()
                        if not frow:
                            cur.execute(
                                "INSERT INTO ref_fonction (libelle) VALUES (%s) ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)",
                                (fl,),
                            )
                            mark_reference_write(cur.rowcount > 0)
                            cur.execute("SELECT id FROM ref_fonction WHERE libelle = %s LIMIT 1", (fl,))
                            frow = cur.fetchone()
                        if not frow:
                            continue
                        cur.execute(
                            """
                            INSERT IGNORE INTO ref_fonction_structure (structure_id, fonction_id)
                            VALUES (%s, %s)
                            """,
                            (int(srow[0]), int(frow[0])),
                        )
                        mark_reference_write(cur.rowcount > 0)
                        if cur.rowcount:
                            inserted += 1
                conn.commit()
            return inserted
