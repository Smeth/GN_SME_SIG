"""Organigramme structures / subdivisions — lecture, réordonnancement, CRUD sécurisé."""

from __future__ import annotations

from sme_gend.repositories.reference_collection_guard import load_collection, require_collection, check_collection

from sme_gend.repositories.reference_write_guard import require_reference_token, check_reference_target

from sme_gend.services.shared_reference_write import shared_reference_write, mark_reference_write

from sme_gend.config.liste_generale_scope import carriere_scope_for_settings, carriere_write_scope_for_settings
from sme_gend.config.settings import Settings
from sme_gend.models.organigram import (
    ReferentielUsage,
    StructureOrganigramNode,
    SubdivisionOrganigramNode,
)
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.services.mysql_ref_etl import (
    ensure_ref_etl_cache,
    ensure_ref_subdiv_arme,
    load_subdiv_flat_rows,
)


class MySQLOrganigramRepository(BaseMySQLRepository):
    def load_order_structures_snapshot(self):
        return load_collection(self._settings,"order_structures")

    def load_order_subdivisions_snapshot(self,structure_id):
        return load_collection(self._settings,"order_subdivisions",structure_id)

    def _scope(self, *, cursor=None) -> tuple[str, tuple]:
        return carriere_scope_for_settings(self._settings, cursor=cursor)

    def _write_scope(self, *, cursor=None) -> tuple[str, tuple]:
        return carriere_write_scope_for_settings(self._settings, cursor=cursor)

    def fetch_organigram(self) -> list[StructureOrganigramNode]:
        """
        Charge l'organigramme complet en 4 requêtes fixes (indépendant du nombre
        de structures), au lieu d'une requête par structure puis une requête par
        libellé orphelin trouvé — ce triple N+1 pouvait représenter plusieurs
        centaines d'allers-retours MySQL avec quelques dizaines de structures.
        Le regroupement Python ci-dessous reproduit exactement la correspondance
        structure_id = X OU code texte = X de la version précédente.
        """
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                scope, scope_p = self._scope(cursor=cur)
                cur.execute(
                    f"""
                    SELECT rs.id, rs.code, rs.designation, rs.ordre_tri,
                           (
                             SELECT COUNT(*) FROM carriere_situation c
                             WHERE {scope}
                               AND (c.structure_id = rs.id
                                    OR UPPER(TRIM(c.structure_code)) = UPPER(TRIM(rs.code)))
                           ) AS n_agents
                    FROM ref_structure rs
                    ORDER BY rs.ordre_tri IS NULL, rs.ordre_tri, rs.code
                    """,
                    scope_p,
                )
                structure_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT id, structure_id, libelle, ordre_tri
                    FROM ref_subdivision_arme
                    WHERE actif = 1
                    ORDER BY structure_id, ordre_tri IS NULL, ordre_tri, libelle
                    """
                )
                rsa_rows = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT c.subdivision_arme_id, COUNT(*)
                    FROM carriere_situation c
                    WHERE {scope} AND c.subdivision_arme_id IS NOT NULL
                    GROUP BY c.subdivision_arme_id
                    """,
                    scope_p,
                )
                rsa_counts = {int(rid): int(n) for rid, n in cur.fetchall()}

                cur.execute(
                    f"""
                    SELECT c.structure_id, UPPER(TRIM(c.structure_code)),
                           TRIM(c.subdivision_arme_libelle), COUNT(*)
                    FROM carriere_situation c
                    WHERE {scope}
                      AND c.subdivision_arme_libelle IS NOT NULL
                      AND TRIM(c.subdivision_arme_libelle) <> ''
                    GROUP BY c.structure_id, UPPER(TRIM(c.structure_code)),
                             TRIM(c.subdivision_arme_libelle)
                    """,
                    scope_p,
                )
                orphan_rows = cur.fetchall()

        orphan_items: list[tuple[str, int]] = []
        orphans_by_sid: dict[int, list[int]] = {}
        orphans_by_code: dict[str, list[int]] = {}
        for o_sid, o_code_u, o_lib, o_n in orphan_rows:
            lbl = str(o_lib or "").strip()
            if not lbl:
                continue
            idx = len(orphan_items)
            orphan_items.append((lbl, int(o_n)))
            if o_sid is not None:
                orphans_by_sid.setdefault(int(o_sid), []).append(idx)
            if o_code_u is not None and str(o_code_u).strip():
                orphans_by_code.setdefault(str(o_code_u), []).append(idx)

        rsa_by_structure: dict[int, list[SubdivisionOrganigramNode]] = {}
        ref_labels_by_structure: dict[int, set[str]] = {}
        for rsa_id, struct_id, lib, o_tri in rsa_rows:
            lbl = str(lib or "").strip()
            if not lbl or struct_id is None:
                continue
            sid_i = int(struct_id)
            rsa_by_structure.setdefault(sid_i, []).append(
                SubdivisionOrganigramNode(
                    rsa_id=int(rsa_id),
                    libelle=lbl,
                    ordre_tri=int(o_tri) if o_tri is not None else None,
                    agent_count=rsa_counts.get(int(rsa_id), 0),
                    from_referentiel=True,
                )
            )
            ref_labels_by_structure.setdefault(sid_i, set()).add(lbl.casefold())

        structures: list[StructureOrganigramNode] = []
        for sid, code, des, ordre, n_ag in structure_rows:
            sid_i = int(sid)
            code_s = str(code or "").strip()
            code_u = code_s.upper()
            ref_subs = list(rsa_by_structure.get(sid_i, []))
            ref_labels = ref_labels_by_structure.get(sid_i, set())

            orphan_for_structure: dict[str, int] = {}
            seen_idx: set[int] = set()
            for idx in orphans_by_sid.get(sid_i, []) + orphans_by_code.get(code_u, []):
                if idx in seen_idx:
                    continue
                seen_idx.add(idx)
                lbl, o_n = orphan_items[idx]
                if lbl.casefold() in ref_labels:
                    continue
                orphan_for_structure[lbl] = orphan_for_structure.get(lbl, 0) + o_n

            for lbl in sorted(orphan_for_structure):
                ref_subs.append(
                    SubdivisionOrganigramNode(
                        rsa_id=None,
                        libelle=lbl,
                        ordre_tri=None,
                        agent_count=orphan_for_structure[lbl],
                        from_referentiel=False,
                    )
                )

            structures.append(
                StructureOrganigramNode(
                    structure_id=sid_i,
                    code=code_s,
                    designation=str(des or "").strip(),
                    ordre_tri=int(ordre) if ordre is not None else None,
                    agent_count=int(n_ag or 0),
                    subdivisions=ref_subs,
                )
            )
        return structures

    def reorder_structures(self, ordered_codes: list[str], *, expected=None) -> None:
        codes = [(c or "").strip() for c in ordered_codes if (c or "").strip()]
        require_collection(expected,"order_structures","")
        if len(codes)!=len(set(codes)) or not set(codes)<={str(row[1]).strip() for row in expected.owner}:
            raise ValueError("Ordre des structures incohérent avec la liste chargée.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_collection(cur,expected)
                    for i, code in enumerate(codes):
                        cur.execute(
                            "UPDATE ref_structure SET ordre_tri = %s WHERE code = %s",
                            (i + 1, code),
                        )
                        mark_reference_write(cur.rowcount > 0)

    def reorder_subdivisions(self, structure_id: int, ordered_rsa_ids: list[int], *, expected=None) -> None:
        require_collection(expected,"order_subdivisions",structure_id)
        if len(ordered_rsa_ids)!=len(set(map(int,ordered_rsa_ids))) or not set(map(int,ordered_rsa_ids))<={int(row[0]) for row in expected.choices}:
            raise ValueError("Ordre des subdivisions incohérent avec la liste chargée.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    check_collection(cur,expected)
                    for i, rsa_id in enumerate(ordered_rsa_ids):
                        cur.execute(
                            """
                            UPDATE ref_subdivision_arme
                            SET ordre_tri = %s
                            WHERE id = %s AND structure_id = %s
                            """,
                            (i + 1, int(rsa_id), int(structure_id)),
                        )
                        mark_reference_write(cur.rowcount > 0)

    def move_subdivision_to_structure(
        self,
        rsa_id: int,
        to_structure_id: int,
        to_structure_code: str,
    ) -> None:
        """Change la structure parente d'une subdivision référentiel (+ carrière du scope)."""
        to_sid = int(to_structure_id)
        to_code = (to_structure_code or "").strip()
        if not to_code:
            raise ValueError("Code structure cible requis.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    scope, scope_p = self._write_scope(cursor=cur)
                    cur.execute(
                        """
                        SELECT COALESCE(MAX(ordre_tri), 0) FROM ref_subdivision_arme
                        WHERE structure_id = %s
                        """,
                        (to_sid,),
                    )
                    nxt = int((cur.fetchone() or (0,))[0]) + 1
                    cur.execute(
                        """
                        UPDATE ref_subdivision_arme
                        SET structure_id = %s, ordre_tri = %s
                        WHERE id = %s
                        """,
                        (to_sid, nxt, int(rsa_id)),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    cur.execute(
                        f"""
                        UPDATE carriere_situation c
                        SET c.structure_id = %s, c.structure_code = %s
                        WHERE {scope} AND c.subdivision_arme_id = %s
                        """,
                        (to_sid, to_code) + scope_p + (int(rsa_id),),
                    )
                    mark_reference_write(cur.rowcount > 0)

    def attach_subdivision_leaf(self, structure_id, leaf_id, libelle):
        """Attach an exact leaf ID, avoiding ambiguous labels in other branches."""
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT id FROM ref_subdivision_arme WHERE structure_id=%s '
                                'AND noeud_feuille_id=%s FOR UPDATE', (structure_id, leaf_id))
                    if cur.fetchone():
                        raise ValueError('Ce niveau T est déjà rattaché à cette structure.')
                    cur.execute('SELECT COALESCE(MAX(ordre_tri),0)+1 FROM ref_subdivision_arme '
                                'WHERE structure_id=%s', (structure_id,))
                    order = int(cur.fetchone()[0])
                    cur.execute('INSERT INTO ref_subdivision_arme '
                                '(structure_id,noeud_feuille_id,libelle,ordre_tri,actif) VALUES(%s,%s,%s,%s,1)',
                                (structure_id, leaf_id, libelle, order))
                    rsa_id = int(cur.lastrowid)
                    mark_reference_write(True)
                    scope, scope_p = self._write_scope(cursor=cur)
                    cur.execute(f'UPDATE carriere_situation c SET c.subdivision_arme_id=%s '
                                f'WHERE {scope} AND TRIM(c.subdivision_arme_libelle)=%s '
                                'AND (c.structure_id=%s OR UPPER(TRIM(c.structure_code))='
                                '(SELECT UPPER(TRIM(code)) FROM ref_structure WHERE id=%s))',
                                (rsa_id,) + scope_p + (libelle, structure_id, structure_id))
                    mark_reference_write(cur.rowcount > 0)
                    return rsa_id

    def attach_subdivision_from_data(
        self, structure_id: int, libelle: str, *, hierarchy_label: str | None = None
    ) -> int:
        """Crée ref_subdivision_arme pour un libellé présent seulement dans les données."""
        lib = (libelle or "").strip()
        if not lib:
            raise ValueError("Libellé subdivision requis.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cache = ensure_ref_etl_cache(cur)
                    flat = load_subdiv_flat_rows(cur)
                    pick = (hierarchy_label or lib).strip()
                    rsa_id = ensure_ref_subdiv_arme(
                        cur,
                        cache,
                        structure_id=int(structure_id),
                        subdivision_libelle=pick,
                        flat_rows=flat,
                        auto_create=True,
                    )
                    if rsa_id is None:
                        raise ValueError(
                            f"Impossible de rattacher « {lib} » — choisissez une entrée "
                            "de la hiérarchie U→D→T ou vérifiez le libellé."
                        )
                    cur.execute(
                        "SELECT COALESCE(MAX(ordre_tri), 0) FROM ref_subdivision_arme WHERE structure_id = %s",
                        (int(structure_id),),
                    )
                    nxt = int((cur.fetchone() or (0,))[0]) + 1
                    cur.execute(
                        "UPDATE ref_subdivision_arme SET libelle = %s, ordre_tri = %s, actif = 1 WHERE id = %s",
                        (lib, nxt, int(rsa_id)),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    scope, scope_p = self._write_scope(cursor=cur)
                    cur.execute(
                        f"""
                        UPDATE carriere_situation c
                        SET c.subdivision_arme_id = %s
                        WHERE {scope}
                          AND TRIM(c.subdivision_arme_libelle) = %s
                          AND (c.structure_id = %s
                               OR UPPER(TRIM(c.structure_code)) = (
                                   SELECT UPPER(TRIM(code)) FROM ref_structure WHERE id = %s LIMIT 1
                               ))
                        """,
                        (int(rsa_id),) + scope_p + (lib, int(structure_id), int(structure_id)),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    return int(rsa_id)

    def add_subdivision(
        self,
        structure_id: int,
        libelle: str,
        *,
        hierarchy_label: str | None = None,
    ) -> int:
        return self.attach_subdivision_from_data(
            structure_id, libelle, hierarchy_label=hierarchy_label
        )

    def rename_subdivision(self, rsa_id: int, new_libelle: str, structure_code: str) -> int:
        new_l = (new_libelle or "").strip()
        old_l = ""
        sc = (structure_code or "").strip()
        if not new_l or not sc:
            raise ValueError("Libellé et code structure requis.")
        with shared_reference_write(self._settings):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT libelle FROM ref_subdivision_arme WHERE id = %s",
                        (int(rsa_id),),
                    )
                    row = cur.fetchone()
                    if row:
                        old_l = str(row[0] or "").strip()
                    cur.execute(
                        "UPDATE ref_subdivision_arme SET libelle = %s WHERE id = %s",
                        (new_l, int(rsa_id)),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    scope, scope_p = self._write_scope(cursor=cur)
                    cur.execute(
                        f"""
                        UPDATE carriere_situation c
                        SET c.subdivision_arme_libelle = %s, c.subdivision_arme_id = %s
                        WHERE {scope}
                          AND TRIM(c.subdivision_arme_libelle) = %s
                          AND (c.structure_id = (SELECT id FROM ref_structure WHERE code = %s LIMIT 1)
                               OR UPPER(TRIM(c.structure_code)) = UPPER(%s))
                        """,
                        (new_l, int(rsa_id)) + scope_p + (old_l, sc, sc),
                    )
                    mark_reference_write(cur.rowcount > 0)
                    return int(cur.rowcount or 0)

    def delete_subdivision(self, rsa_id: int) -> None:
        with shared_reference_write(self._settings):
            usage = self.count_subdiv_usage(int(rsa_id))
            if usage.total > 0:
                raise ValueError(
                    f"Subdivision utilisée par {usage.total} fiche(s) — "
                    "réaffectez le personnel avant suppression."
                )
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM ref_subdivision_arme WHERE id = %s", (int(rsa_id),))
                    mark_reference_write(cur.rowcount > 0)

    def grade_usage_counts(self):
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT g.code, COUNT(DISTINCT c.agent_id) FROM ref_grade g "
                            "LEFT JOIN carriere_situation c ON c.grade_id=g.id OR "
                            "(c.grade_id IS NULL AND (TRIM(c.grade_libelle)=g.code OR TRIM(c.grade_libelle)=g.libelle)) "
                            "GROUP BY g.id, g.code")
                return {str(code): int(count) for code, count in cur.fetchall()}

    def count_grade_usage(self, code: str) -> ReferentielUsage:
        c = (code or "").strip()
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                scope, scope_p = self._scope(cursor=cur)
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM carriere_situation c
                    INNER JOIN ref_grade g ON g.id = c.grade_id
                    WHERE {scope} AND g.code = %s
                    """,
                    scope_p + (c,),
                )
                by_fk = int((cur.fetchone() or (0,))[0])
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM carriere_situation c
                    WHERE {scope}
                      AND (UPPER(TRIM(c.grade_libelle)) = UPPER(%s)
                           OR UPPER(TRIM(c.grade_libelle)) = (
                               SELECT UPPER(TRIM(libelle)) FROM ref_grade WHERE code = %s LIMIT 1
                           ))
                    """,
                    scope_p + (c, c),
                )
                by_text = int((cur.fetchone() or (0,))[0])
                return ReferentielUsage(by_fk=by_fk, by_text=max(0, by_text - by_fk))

    def count_structure_usage(self, code: str) -> ReferentielUsage:
        c = (code or "").strip()
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                scope, scope_p = self._scope(cursor=cur)
                cur.execute("SELECT id FROM ref_structure WHERE code = %s", (c,))
                row = cur.fetchone()
                sid = int(row[0]) if row else None
                by_fk = 0
                if sid is not None:
                    cur.execute(
                        f"SELECT COUNT(*) FROM carriere_situation c WHERE {scope} AND c.structure_id = %s",
                        scope_p + (sid,),
                    )
                    by_fk = int((cur.fetchone() or (0,))[0])
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM carriere_situation c
                    WHERE {scope} AND UPPER(TRIM(c.structure_code)) = UPPER(%s)
                    """,
                    scope_p + (c,),
                )
                by_text = int((cur.fetchone() or (0,))[0])
                return ReferentielUsage(by_fk=by_fk, by_text=max(0, by_text - by_fk))

    def count_subdiv_usage(self, rsa_id: int) -> ReferentielUsage:
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rsa.libelle, rs.code
                    FROM ref_subdivision_arme rsa
                    INNER JOIN ref_structure rs ON rs.id = rsa.structure_id
                    WHERE rsa.id = %s
                    """,
                    (int(rsa_id),),
                )
                row = cur.fetchone()
                if not row:
                    return ReferentielUsage()
                lib = str(row[0] or "").strip()
                sc = str(row[1] or "").strip()
                scope, scope_p = self._scope(cursor=cur)
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM carriere_situation c
                    WHERE {scope} AND c.subdivision_arme_id = %s
                    """,
                    scope_p + (int(rsa_id),),
                )
                by_fk = int((cur.fetchone() or (0,))[0])
                by_text = 0
                if lib and sc:
                    cur.execute(
                        f"""
                        SELECT COUNT(*) FROM carriere_situation c
                        WHERE {scope}
                          AND TRIM(c.subdivision_arme_libelle) = %s
                          AND (c.structure_id = (SELECT id FROM ref_structure WHERE code = %s LIMIT 1)
                               OR UPPER(TRIM(c.structure_code)) = UPPER(%s))
                        """,
                        scope_p + (lib, sc, sc),
                    )
                    by_text = max(0, int((cur.fetchone() or (0,))[0]) - by_fk)
                return ReferentielUsage(by_fk=by_fk, by_text=by_text)

    def safe_delete_structure(self, code: str, *, expected=None) -> None:
        revision = require_reference_token("structure", code.strip(), expected)
        with shared_reference_write(self._settings):
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    check_reference_target(cursor, "structure", code.strip(), revision)
            usage = self.count_structure_usage(code)
            if usage.total > 0:
                raise ValueError(
                    f"Structure « {code} » : {usage.total} agent(s) rattaché(s) — "
                    "modifiez leur affectation avant suppression."
                )
            c = (code or "").strip()
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM ref_structure WHERE code = %s", (c,))
                    row = cur.fetchone()
                    if not row:
                        return
                    sid = int(row[0])
                    cur.execute(
                        "SELECT COUNT(*) FROM ref_subdivision_arme WHERE structure_id = %s",
                        (sid,),
                    )
                    if int((cur.fetchone() or (0,))[0]) > 0:
                        cur.execute(
                            "DELETE FROM ref_subdivision_arme WHERE structure_id = %s",
                            (sid,),
                        )
                        mark_reference_write(cur.rowcount > 0)
                    cur.execute("DELETE FROM ref_structure WHERE id = %s", (sid,))
                    mark_reference_write(cur.rowcount > 0)

    def safe_delete_grade(self, code: str, *, expected=None) -> None:
        revision = require_reference_token("grade", code.strip(), expected)
        with shared_reference_write(self._settings):
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    check_reference_target(cursor, "grade", code.strip(), revision)
            usage = self.grade_usage_counts().get(code.strip(), 0)
            if usage > 0:
                raise ValueError(
                    f"Grade « {code} » : {usage} agent(s), archives comprises — "
                    "modifiez leur grade avant suppression."
                )
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
                                "AND table_name='agent_passation_grade'")
                    if cur.fetchone()[0]:
                        cur.execute("SELECT COUNT(*) FROM agent_passation_grade p JOIN ref_grade g ON g.id=p.grade_id WHERE g.code=%s", (code.strip(),))
                        if cur.fetchone()[0]:
                            raise ValueError("Grade utilisé dans l'historique des passations. Suppression refusée.")
                    cur.execute("DELETE FROM ref_grade WHERE code = %s", (code.strip(),))
                    mark_reference_write(cur.rowcount > 0)
                    if cur.rowcount > 0:
                        from sme_gend.services.business_audit import record_business_action
                        record_business_action(self._settings, action="grade_delete", module="reference",
                                               entity_id=code, before=expected, after=None)
                        from sme_gend.services.grade_media import remove_association
                        remove_association(self._settings, code)
