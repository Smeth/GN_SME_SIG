"""Harmonisation manuelle structures / SubArme — toutes périodes carrière."""

from __future__ import annotations

from dataclasses import dataclass
from sme_gend.repositories.hierarchy_merge_guard import maintenance_write,versioned_variants

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_common import BaseMySQLRepository
from sme_gend.services.referentiel_maintenance import _merge_rsa_into


@dataclass(slots=True)
class SubdivVariantRow:
    libelle: str
    rsa_id: int | None
    segment_count: int
    in_referentiel: bool


@dataclass(slots=True)
class StructureVariantRow:
    structure_id: int | None
    code: str
    designation: str | None
    segment_count: int
    source: str  # referentiel | carriere_seule


@dataclass(slots=True)
class SubdivMergePreview:
    carriere_rows: int
    rsa_merged: int


@dataclass(slots=True)
class StructureMergePreview:
    carriere_rows: int
    rsa_moved: int
    structures_removed: int


class MySQLCoherenceRepository(BaseMySQLRepository):
    def structure_id_for_code(self, code: str) -> int | None:
        c = (code or "").strip()
        if not c:
            return None
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ref_structure WHERE code = %s LIMIT 1", (c,))
                row = cur.fetchone()
                return int(row[0]) if row else None

    def _structure_match_sql(self, *, alias: str = "c") -> str:
        a = alias.strip() or "c"
        return (
            f"({a}.structure_id = %s OR "
            f"UPPER(TRIM({a}.structure_code)) = UPPER(%s))"
        )

    @versioned_variants('merge_subdiv')
    def list_subdiv_variants(self, structure_id: int, structure_code: str) -> list[SubdivVariantRow]:
        sid = int(structure_id)
        sc = (structure_code or "").strip()
        by_label: dict[str, SubdivVariantRow] = {}
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rsa.id, TRIM(rsa.libelle)
                    FROM ref_subdivision_arme rsa
                    WHERE rsa.structure_id = %s
                    ORDER BY rsa.ordre_tri IS NULL, rsa.ordre_tri, rsa.id
                    """,
                    (sid,),
                )
                for rsa_id, lib in cur.fetchall():
                    lb = str(lib or "").strip()
                    if not lb:
                        continue
                    by_label[lb] = SubdivVariantRow(
                        libelle=lb,
                        rsa_id=int(rsa_id),
                        segment_count=0,
                        in_referentiel=True,
                    )

                cur.execute(
                    f"""
                    SELECT TRIM(c.subdivision_arme_libelle), COUNT(*),
                           MAX(c.subdivision_arme_id)
                    FROM carriere_situation c
                    WHERE {self._structure_match_sql()}
                      AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
                    GROUP BY TRIM(c.subdivision_arme_libelle)
                    ORDER BY TRIM(c.subdivision_arme_libelle)
                    """,
                    (sid, sc),
                )
                for lib, cnt, rsa_hint in cur.fetchall():
                    lb = str(lib or "").strip()
                    if not lb:
                        continue
                    n = int(cnt)
                    hint = int(rsa_hint) if rsa_hint is not None else None
                    if lb in by_label:
                        by_label[lb].segment_count = n
                        if by_label[lb].rsa_id is None and hint:
                            by_label[lb].rsa_id = hint
                    else:
                        by_label[lb] = SubdivVariantRow(
                            libelle=lb,
                            rsa_id=hint,
                            segment_count=n,
                            in_referentiel=hint is not None,
                        )

                for row in by_label.values():
                    if row.segment_count == 0 and row.rsa_id is not None:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM carriere_situation
                            WHERE subdivision_arme_id = %s
                            """,
                            (int(row.rsa_id),),
                        )
                        row.segment_count = int((cur.fetchone() or (0,))[0])

        return sorted(by_label.values(), key=lambda r: (-r.segment_count, r.libelle.casefold()))

    def preview_subdiv_merge(self,structure_id,structure_code,*,keeper_rsa_id,target_libelle,merge_rsa_ids,merge_libelles):
        ids=sorted({int(x) for x in merge_rsa_ids if x}|({int(keeper_rsa_id)} if keeper_rsa_id else set()))
        labels=sorted({str(x).strip() for x in merge_libelles if str(x).strip()})
        clauses=[];params=[int(structure_id),structure_code.strip()]
        if ids:clauses.append('c.subdivision_arme_id IN ('+','.join(['%s']*len(ids))+')');params.extend(ids)
        if labels:clauses.append('TRIM(c.subdivision_arme_libelle) IN ('+','.join(['%s']*len(labels))+')');params.extend(labels)
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM carriere_situation c WHERE '+self._structure_match_sql()+' AND ('+(' OR '.join(clauses) or '1=0')+')',tuple(params))
                count=int(cur.fetchone()[0])
        return SubdivMergePreview(count,len(set(ids)-({int(keeper_rsa_id)} if keeper_rsa_id else set())))

    def apply_subdiv_merge(self,structure_id,structure_code,*,keeper_rsa_id,target_libelle,merge_rsa_ids,merge_libelles,expected=None):
        from sme_gend.services.mysql_ref_etl import ensure_ref_subdiv_arme,load_ref_cache_from_db,load_subdiv_flat_rows
        sid=int(structure_id);sc=(structure_code or '').strip();target=(target_libelle or '').strip()
        if not sc or not target or len(target)>512:raise ValueError('Structure et libelle cible valides requis.')
        merge_rsa=sorted({int(x) for x in merge_rsa_ids if x});labels=sorted({str(x).strip() for x in merge_libelles if str(x).strip()})
        with maintenance_write(self._settings,expected,'merge_subdiv',(sid,sc)) as (cur,snapshot):
            structures={int(row[0]):row for row in snapshot.parts[2]}
            if sid not in structures or structures[sid][1]!=sc:raise ValueError('Structure selectionnee incoherente. Actualisez les donnees.')
            rsa={int(row[0]):row for row in snapshot.parts[1]};keeper=int(keeper_rsa_id) if keeper_rsa_id else None
            requested=set(merge_rsa)|({keeper} if keeper else set())
            if any(rid not in rsa or rsa[rid][1]!=sid for rid in requested):raise ValueError('Subdivision hors de la structure selectionnee.')
            for row in snapshot.parts[3]:
                outside = row[1]!=sid if row[1] is not None else str(row[2] or '').strip().casefold()!=sc.casefold()
                if row[3] in requested and outside:raise ValueError('Une subdivision selectionnee est utilisee dans une autre structure ; corrigez ce rattachement avant fusion.')
            if any(row[5] in requested and row[1]!=sid for row in snapshot.parts[6]):raise ValueError('Un organigramme externe utilise ces subdivisions.')
            cur.execute('SELECT id FROM ref_subdivision_arme WHERE structure_id=%s AND TRIM(libelle)=%s',(sid,target))
            if any(int(row[0]) not in requested for row in cur.fetchall()):raise ValueError('Le libelle cible existe dans une subdivision non selectionnee.')
            if keeper is None:
                cache=load_ref_cache_from_db(cur)
                keeper=ensure_ref_subdiv_arme(cur,cache,structure_id=sid,subdivision_libelle=labels[0] if labels else target,
                    flat_rows=load_subdiv_flat_rows(cur),auto_create=True)
                if not keeper:raise ValueError('Impossible de creer la subdivision gardienne.')
            cur.execute('UPDATE ref_subdivision_arme SET libelle=%s,actif=1 WHERE id=%s',(target,keeper))
            for rid in merge_rsa:
                if rid!=keeper:_merge_rsa_into(cur,rid,keeper)
            # All references to the surviving ID must agree with its canonical
            # label, including rows whose old text is missing or different.
            cur.execute('UPDATE carriere_situation SET subdivision_arme_libelle=%s WHERE subdivision_arme_id=%s',(target,keeper))
            updated=int(cur.rowcount)
            for label in labels:
                cur.execute('UPDATE carriere_situation SET subdivision_arme_libelle=%s,subdivision_arme_id=%s WHERE '
                    +self._structure_match_sql(alias='carriere_situation')+' AND subdivision_arme_id IS NULL AND TRIM(subdivision_arme_libelle)=%s',
                    (target,keeper,sid,sc,label))
                updated+=int(cur.rowcount)
            return updated,len(set(merge_rsa)-{keeper})

    @versioned_variants('merge_structure')
    def list_structure_variants(self) -> list[StructureVariantRow]:
        rows: list[StructureVariantRow] = []
        seen_codes: set[str] = set()
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rs.id, rs.code, rs.designation,
                           COUNT(c.id) AS n
                    FROM ref_structure rs
                    LEFT JOIN carriere_situation c ON c.structure_id = rs.id
                    GROUP BY rs.id, rs.code, rs.designation
                    ORDER BY rs.ordre_tri IS NULL, rs.ordre_tri, rs.code
                    """
                )
                for sid, code, desig, n in cur.fetchall():
                    c = str(code or "").strip()
                    if not c:
                        continue
                    seen_codes.add(c.casefold())
                    rows.append(
                        StructureVariantRow(
                            structure_id=int(sid),
                            code=c,
                            designation=str(desig or "").strip() or None,
                            segment_count=int(n or 0),
                            source="referentiel",
                        )
                    )

                cur.execute(
                    """
                    SELECT TRIM(c.structure_code) AS code, COUNT(*) AS n
                    FROM carriere_situation c
                    WHERE TRIM(COALESCE(c.structure_code, '')) <> ''
                    GROUP BY UPPER(TRIM(c.structure_code)), TRIM(c.structure_code)
                    ORDER BY TRIM(c.structure_code)
                    """
                )
                for code, n in cur.fetchall():
                    c = str(code or "").strip()
                    if not c or c.casefold() in seen_codes:
                        continue
                    cur.execute(
                        "SELECT id FROM ref_structure WHERE UPPER(TRIM(code)) = UPPER(%s) LIMIT 1",
                        (c,),
                    )
                    ref = cur.fetchone()
                    if ref:
                        continue
                    rows.append(
                        StructureVariantRow(
                            structure_id=None,
                            code=c,
                            designation=None,
                            segment_count=int(n or 0),
                            source="carriere_seule",
                        )
                    )
        return rows

    def preview_structure_merge(self,*,keeper_structure_id,keeper_code,absorb_structure_ids,absorb_text_codes):
        ids=sorted({int(x) for x in absorb_structure_ids if int(x)!=int(keeper_structure_id)})
        texts=sorted({str(x).strip().upper() for x in absorb_text_codes if str(x).strip() and str(x).strip().casefold()!=keeper_code.strip().casefold()})
        clauses=[];params=[]
        if ids:clauses.append('c.structure_id IN ('+','.join(['%s']*len(ids))+')');params.extend(ids)
        if texts:clauses.append('(c.structure_id IS NULL AND UPPER(TRIM(c.structure_code)) IN ('+','.join(['%s']*len(texts))+'))');params.extend(texts)
        with self._connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM carriere_situation c WHERE '+(' OR '.join(clauses) or '1=0'),tuple(params));count=int(cur.fetchone()[0])
                moved=removed=0
                if ids:
                    placeholders=','.join(['%s']*len(ids))
                    cur.execute('SELECT COUNT(*) FROM ref_subdivision_arme WHERE structure_id IN ('+placeholders+')',tuple(ids));moved=int(cur.fetchone()[0])
                    cur.execute('SELECT COUNT(*) FROM ref_structure WHERE id IN ('+placeholders+')',tuple(ids));removed=int(cur.fetchone()[0])
        return StructureMergePreview(count,moved,removed)

    def apply_structure_merge(self,*,keeper_structure_id,target_code,target_designation,absorb_structure_ids,absorb_text_codes,expected=None):
        kid=int(keeper_structure_id);target=(target_code or '').strip()
        if not target or len(target)>64:raise ValueError('Code structure cible valide requis.')
        absorb=sorted({int(x) for x in absorb_structure_ids if int(x)!=kid})
        texts={str(x).strip() for x in absorb_text_codes if str(x).strip()}
        with maintenance_write(self._settings,expected,'merge_structure') as (cur,snapshot):
            structures={int(row[0]):row for row in snapshot.parts[2]}
            if kid not in structures or not set(absorb)<=structures.keys():raise ValueError('Structure selectionnee absente. Actualisez les donnees.')
            cur.execute('SELECT id FROM ref_structure WHERE UPPER(TRIM(code))=UPPER(%s) AND id<>%s',(target,kid))
            if any(int(row[0]) not in absorb for row in cur.fetchall()):raise ValueError('Le code cible appartient a une structure non selectionnee.')
            # Text-only variants must really be unlinked; never retarget an
            # unrelated referenced structure because its cached text is stale.
            codes={str(row[1]).casefold():sid for sid,row in structures.items()}
            if any(code.casefold() in codes and codes[code.casefold()] not in {kid,*absorb} for code in texts):
                raise ValueError('Une variante texte appartient a une structure non selectionnee.')
            updated=0;moved=0
            source_codes={str(structures[sid][1]) for sid in [kid,*absorb]}|texts
            selected={kid,*absorb}
            selected_rsa={row[0] for row in snapshot.parts[1] if row[1] in selected}
            aliases={code.casefold() for code in source_codes}|{target.casefold()}
            for row in snapshot.parts[3]:
                if row[3] in selected_rsa and (row[1] not in selected and (row[1] is not None or str(row[2] or '').strip().casefold() not in aliases)):
                    raise ValueError('Une subdivision selectionnee est rattachee a une carriere externe ; corrigez ce rattachement avant fusion.')
            if any(row[5] in selected_rsa and row[1] not in selected for row in snapshot.parts[6]):
                raise ValueError('Un organigramme externe utilise ces subdivisions.')
            for aid in absorb:
                cur.execute('SELECT id,noeud_feuille_id,libelle FROM ref_subdivision_arme WHERE structure_id=%s ORDER BY id',(aid,))
                for rid,leaf,label in cur.fetchall():
                    cur.execute('SELECT id,libelle FROM ref_subdivision_arme WHERE structure_id=%s AND noeud_feuille_id=%s',(kid,leaf))
                    existing=cur.fetchone()
                    if existing:
                        _merge_rsa_into(cur,int(rid),int(existing[0]))
                        cur.execute('UPDATE carriere_situation SET subdivision_arme_libelle=%s WHERE subdivision_arme_id=%s',(existing[1],existing[0]))
                    else:
                        cur.execute('SELECT id FROM ref_subdivision_arme WHERE structure_id=%s AND TRIM(libelle)=%s',(kid,str(label or '').strip()))
                        if cur.fetchone():raise ValueError('Deux feuilles differentes portent le meme libelle. Fusionnez les subdivisions explicitement avant les structures.')
                        cur.execute('UPDATE ref_subdivision_arme SET structure_id=%s WHERE id=%s',(kid,rid));moved+=1
                cur.execute('INSERT INTO ref_fonction_structure(structure_id,fonction_id,ordre_tri) '
                    'SELECT %s,fonction_id,ordre_tri FROM ref_fonction_structure WHERE structure_id=%s '
                    'ON DUPLICATE KEY UPDATE fonction_id=VALUES(fonction_id)',(kid,aid))
                cur.execute('DELETE FROM ref_fonction_structure WHERE structure_id=%s',(aid,))
                cur.execute('UPDATE structure_organigram_node SET structure_id=%s WHERE structure_id=%s',(kid,aid))
                cur.execute('UPDATE carriere_situation SET structure_id=%s,structure_code=%s WHERE structure_id=%s',(kid,target,aid));updated+=int(cur.rowcount)
                cur.execute('UPDATE agent_affectation SET structure_id=%s,structure_code=%s WHERE structure_id=%s',(kid,target,aid))
                cur.execute('DELETE FROM ref_structure WHERE id=%s',(aid,))
            # Promote selected legacy text references across every period.
            for code in sorted(source_codes):
                cur.execute('UPDATE carriere_situation SET structure_id=%s,structure_code=%s WHERE structure_id IS NULL AND UPPER(TRIM(structure_code))=UPPER(%s)',(kid,target,code));updated+=int(cur.rowcount)
                cur.execute('UPDATE agent_affectation SET structure_id=%s,structure_code=%s WHERE structure_id IS NULL AND UPPER(TRIM(structure_code))=UPPER(%s)',(kid,target,code))
            cur.execute('UPDATE carriere_situation SET structure_code=%s WHERE structure_id=%s',(target,kid));updated+=int(cur.rowcount)
            cur.execute('UPDATE agent_affectation SET structure_code=%s WHERE structure_id=%s',(target,kid))
            designation=(target_designation or '').strip() or structures[kid][2]
            cur.execute('UPDATE ref_structure SET code=%s,designation=%s WHERE id=%s',(target,designation,kid))
            return updated,moved,len(absorb)


def coherence_repository(settings: Settings) -> MySQLCoherenceRepository:
    return MySQLCoherenceRepository(settings)
