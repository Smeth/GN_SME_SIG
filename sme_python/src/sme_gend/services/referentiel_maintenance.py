"""Maintenance référentiels : migration nœuds org, RSA, layout, tables legacy."""

from __future__ import annotations

from sme_gend.services.automatic_maintenance import automatic_write

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.structure_subdivision_repository import StructureSubdivisionRepository
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.mysql_ref_etl import (
    RefEtlCache,
    _cf,
    _ensure_org_subdiv_u,
    _org_subdiv_codes,
    _upsert_subdiv_node,
    ensure_ref_subdiv_arme,
    load_ref_cache_from_db,
    load_subdiv_flat_rows,
)

_ROOT_ID = 1
_ORG_U_CODE = "_ORG"
_RESERVED_ROOT_CODES = frozenset({"ROOT", _ORG_U_CODE})


@dataclass(slots=True)
class ReferentielMaintenanceReport:
    org_nodes_migrated: int = 0
    carriere_subdiv_labels_normalized: int = 0
    rsa_backfilled: int = 0
    rsa_merged: int = 0
    rsa_deleted: int = 0
    rsa_deactivated: int = 0
    rsa_propagated: int = 0
    layout_sections: int = 0
    layout_subdivisions: int = 0
    tab_tables_dropped: list[str] = field(default_factory=list)
    dossier_projected: int = 0
    fonction_structure_links: int = 0
    fk_resync: dict[str, Any] = field(default_factory=dict)
    carriere_reconciled: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_nodes_migrated": self.org_nodes_migrated,
            "carriere_subdiv_labels_normalized": self.carriere_subdiv_labels_normalized,
            "rsa_backfilled": self.rsa_backfilled,
            "rsa_merged": self.rsa_merged,
            "rsa_deleted": self.rsa_deleted,
            "rsa_deactivated": self.rsa_deactivated,
            "rsa_propagated": self.rsa_propagated,
            "layout_sections": self.layout_sections,
            "layout_subdivisions": self.layout_subdivisions,
            "tab_tables_dropped": self.tab_tables_dropped,
            "dossier_projected": self.dossier_projected,
            "fonction_structure_links": self.fonction_structure_links,
            "fk_resync": self.fk_resync,
            "carriere_reconciled": self.carriere_reconciled,
            "warnings": self.warnings,
        }


def _is_leaf(cur: Any, node_id: int) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM ref_subdivision_noeud WHERE parent_id = %s",
        (int(node_id),),
    )
    return int((cur.fetchone() or (0,))[0]) == 0


def _node_depth(cur: Any, node_id: int) -> int:
    depth = 0
    cur_id: int | None = int(node_id)
    while cur_id is not None and cur_id != _ROOT_ID:
        depth += 1
        cur.execute("SELECT parent_id FROM ref_subdivision_noeud WHERE id = %s", (cur_id,))
        row = cur.fetchone()
        if not row:
            break
        cur_id = int(row[0]) if row[0] is not None else None
    return depth


def _repoint_rsa_noeud_feuille(cur: Any, old_leaf_id: int, new_leaf_id: int) -> None:
    """
    Réaffecte les RSA de ``old_leaf_id`` vers ``new_leaf_id``.

    Fusionne les doublons (structure_id, noeud_feuille_id) en conservant la ligne existante.
    """
    old_i = int(old_leaf_id)
    new_i = int(new_leaf_id)
    if old_i == new_i:
        return
    cur.execute(
        "SELECT id, structure_id FROM ref_subdivision_arme WHERE noeud_feuille_id = %s",
        (old_i,),
    )
    rows = cur.fetchall()
    for rsa_id, structure_id in rows:
        rsa_id_i = int(rsa_id)
        sid_i = int(structure_id)
        cur.execute(
            """
            SELECT id FROM ref_subdivision_arme
            WHERE structure_id = %s AND noeud_feuille_id = %s AND id <> %s
            LIMIT 1
            """,
            (sid_i, new_i, rsa_id_i),
        )
        dup = cur.fetchone()
        if dup:
            keep_id = int(dup[0])
            cur.execute(
                """
                UPDATE carriere_situation SET subdivision_arme_id = %s
                WHERE subdivision_arme_id = %s
                """,
                (keep_id, rsa_id_i),
            )
            cur.execute("DELETE FROM ref_subdivision_arme WHERE id = %s", (rsa_id_i,))
        else:
            cur.execute(
                "UPDATE ref_subdivision_arme SET noeud_feuille_id = %s WHERE id = %s",
                (new_i, rsa_id_i),
            )


def migrate_misplaced_org_nodes(cur: Any, cache: RefEtlCache) -> int:
    """
    Déplace sous ``_ORG`` les feuilles créées par erreur directement sous la racine
    (parent_id=1, sans enfant, liées à ref_subdivision_arme ou code OI*).
    """
    org_u = _ensure_org_subdiv_u(cur, cache)
    cur.execute(
        """
        SELECT n.id, n.code, n.libelle
        FROM ref_subdivision_noeud n
        WHERE n.parent_id = %s
          AND UPPER(TRIM(n.code)) NOT IN ('ROOT', %s)
          AND NOT EXISTS (
            SELECT 1 FROM ref_subdivision_noeud c WHERE c.parent_id = n.id
          )
          AND (
            UPPER(TRIM(n.code)) LIKE 'OI%%'
            OR EXISTS (
              SELECT 1 FROM ref_subdivision_arme rsa WHERE rsa.noeud_feuille_id = n.id
            )
          )
        ORDER BY n.id
        """,
        (_ROOT_ID, _ORG_U_CODE),
    )
    rows = cur.fetchall()
    migrated = 0
    for old_id, code, libelle in rows:
        old_id_i = int(old_id)
        if _node_depth(cur, old_id_i) >= 3:
            continue
        lb = str(libelle or code or "").strip()
        if not lb:
            continue
        d_code, t_code = _org_subdiv_codes(lb)
        did = _upsert_subdiv_node(
            cur, parent_id=org_u, code=d_code, libelle=lb, pri_out=None
        )
        tid = _upsert_subdiv_node(
            cur, parent_id=did, code=t_code, libelle=lb, pri_out=None
        )
        _repoint_rsa_noeud_feuille(cur, old_id_i, tid)
        cur.execute("DELETE FROM ref_subdivision_noeud WHERE id = %s", (old_id_i,))
        cache.leaf_node_by_design_t[_cf(lb)] = tid
        migrated += 1
    return migrated


def _norm_subdiv_label(s: str | None) -> str:
    """Normalise un libellé SubArme pour rapprochement (casse, accents, espaces)."""
    raw = unicodedata.normalize("NFKD", (s or "").strip())
    folded = "".join(c for c in raw if not unicodedata.combining(c))
    folded = folded.casefold().replace("_", " ")
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _labels_equivalent(a: str, b: str) -> bool:
    na, nb = _norm_subdiv_label(a), _norm_subdiv_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) <= 4 or len(nb) <= 4:
        return na == nb
    return SequenceMatcher(None, na, nb).ratio() >= 0.82


def _find_canonical_label(rsa_label: str, canonicals: list[str]) -> str | None:
    for canon in canonicals:
        if _labels_equivalent(rsa_label, canon):
            return canon
    return None


def _pick_canonical_from_cluster(cluster: list[tuple[str, int]]) -> str:
    """Libellé retenu = le plus fréquent en carrière (à égalité, le plus long)."""
    best_label = cluster[0][0]
    best_cnt = -1
    for lab, cnt in cluster:
        if cnt > best_cnt or (cnt == best_cnt and len(lab) > len(best_label)):
            best_cnt = cnt
            best_label = lab
    return best_label


def _cluster_canonical_labels(
    labels_with_counts: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Fusionne les libellés équivalents ; retourne (canon, effectif total)."""
    clusters: list[list[tuple[str, int]]] = []
    for lab, cnt in labels_with_counts:
        placed = False
        for cluster in clusters:
            if _labels_equivalent(lab, cluster[0][0]):
                cluster.append((lab, cnt))
                placed = True
                break
        if not placed:
            clusters.append([(lab, cnt)])
    out: list[tuple[str, int]] = []
    for cluster in clusters:
        canon = _pick_canonical_from_cluster(cluster)
        total = sum(c for _l, c in cluster)
        out.append((canon, total))
    return out


def normalize_subdiv_libelles_in_carriere(cur: Any) -> tuple[int, list[str]]:
    """
    Unifie subdivision_arme_libelle en carrière (toutes périodes) par structure.

    Corrige les variantes d'orthographe issues des imports d'archives successifs.
    """
    updated = 0
    warnings: list[str] = []
    cur.execute(
        """
        SELECT c.structure_id, TRIM(c.subdivision_arme_libelle), COUNT(*)
        FROM carriere_situation c
        WHERE c.structure_id IS NOT NULL
          AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
        GROUP BY c.structure_id, TRIM(c.subdivision_arme_libelle)
        """
    )
    by_struct: dict[int, list[tuple[str, int]]] = {}
    for sid, lib, cnt in cur.fetchall():
        by_struct.setdefault(int(sid), []).append((str(lib).strip(), int(cnt)))

    for sid, rows in by_struct.items():
        merged = _cluster_canonical_labels(rows)
        for canon, _total in merged:
            for alt, _cnt in rows:
                if alt == canon or not _labels_equivalent(alt, canon):
                    continue
                cur.execute(
                    """
                    UPDATE carriere_situation
                    SET subdivision_arme_libelle = %s
                    WHERE structure_id = %s
                      AND TRIM(subdivision_arme_libelle) = %s
                    """,
                    (canon, sid, alt),
                )
                n = int(cur.rowcount or 0)
                if n:
                    updated += n
                    if len(warnings) < 80:
                        warnings.append(
                            f"Structure {sid} : « {alt} » → « {canon} » ({n} segment(s))."
                        )
    return updated, warnings


def _rsa_usage_count(cur: Any, rsa_id: int, *, mois: str | None) -> int:
    params: tuple = (int(rsa_id),)
    mois_clause = ""
    if mois:
        mois_clause = """
          AND EXISTS (
            SELECT 1 FROM sme_periode p
            WHERE p.id = c.sme_periode_id AND p.annee_mois = %s
          )
        """
        params = (int(rsa_id), mois.strip())
    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE c.subdivision_arme_id = %s
        {mois_clause}
        """,
        params,
    )
    return int((cur.fetchone() or (0,))[0])


def _rsa_total_usage(cur: Any, rsa_id: int) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM carriere_situation WHERE subdivision_arme_id = %s",
        (int(rsa_id),),
    )
    return int((cur.fetchone() or (0,))[0])


def _merge_rsa_into(cur: Any, dup_id: int, keeper_id: int) -> None:
    cur.execute(
        """
        UPDATE carriere_situation SET subdivision_arme_id = %s
        WHERE subdivision_arme_id = %s
        """,
        (int(keeper_id), int(dup_id)),
    )
    cur.execute(
        """
        UPDATE structure_organigram_node SET rsa_id = %s
        WHERE rsa_id = %s
        """,
        (int(keeper_id), int(dup_id)),
    )
    cur.execute("DELETE FROM ref_subdivision_arme WHERE id = %s", (int(dup_id),))


def dedupe_rsa_from_carriere(
    cur: Any,
    *,
    mois: str | None = None,
) -> tuple[int, int, int, list[str]]:
    """
    Aligne ref_subdivision_arme sur les libellés carrière (source de vérité).

    ``mois`` : YYYY-MM ; si ``None``, tous les segments carrière importés.

    - Fusionne les RSA doublons d'une même structure vers le libellé canonique.
    - Supprime les RSA jamais utilisés en carrière.
    - Désactive (actif=0) les RSA hors libellés canoniques encore référencés historiquement.
    """
    mois_s = (mois or "").strip()

    merged = 0
    deleted = 0
    deactivated = 0
    warnings: list[str] = []

    if mois_s:
        cur.execute(
            """
            SELECT c.structure_id, TRIM(c.subdivision_arme_libelle), COUNT(*)
            FROM carriere_situation c
            INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
            WHERE c.structure_id IS NOT NULL
              AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
            GROUP BY c.structure_id, TRIM(c.subdivision_arme_libelle)
            ORDER BY c.structure_id, TRIM(c.subdivision_arme_libelle)
            """,
            (mois_s,),
        )
    else:
        cur.execute(
            """
            SELECT c.structure_id, TRIM(c.subdivision_arme_libelle), COUNT(*)
            FROM carriere_situation c
            WHERE c.structure_id IS NOT NULL
              AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
            GROUP BY c.structure_id, TRIM(c.subdivision_arme_libelle)
            ORDER BY c.structure_id, TRIM(c.subdivision_arme_libelle)
            """
        )
    canon_by_struct: dict[int, list[tuple[str, int]]] = {}
    for sid, lib, cnt in cur.fetchall():
        canon_by_struct.setdefault(int(sid), []).append((str(lib).strip(), int(cnt)))
    for sid in list(canon_by_struct.keys()):
        canon_by_struct[sid] = _cluster_canonical_labels(canon_by_struct[sid])

    cur.execute(
        """
        SELECT rsa.id, rsa.structure_id, TRIM(rsa.libelle), rs.code
        FROM ref_subdivision_arme rsa
        INNER JOIN ref_structure rs ON rs.id = rsa.structure_id
        WHERE rsa.actif = 1
        ORDER BY rsa.structure_id, rsa.id
        """
    )
    by_struct: dict[int, list[tuple[int, str, str]]] = {}
    for rsa_id, sid, lib, scode in cur.fetchall():
        by_struct.setdefault(int(sid), []).append(
            (int(rsa_id), str(lib or "").strip(), str(scode or "").strip())
        )

    for sid, rsa_rows in by_struct.items():
        canonicals = [c[0] for c in canon_by_struct.get(sid, [])]
        groups: dict[str, list[tuple[int, str, int]]] = {}
        orphan_rows: list[tuple[int, str, int]] = []

        for rsa_id, lib, _scode in rsa_rows:
            if mois_s:
                usage_ref = _rsa_usage_count(cur, rsa_id, mois=mois_s)
            else:
                usage_ref = _rsa_total_usage(cur, rsa_id)
            match = _find_canonical_label(lib, canonicals) if canonicals else None
            if match:
                groups.setdefault(match, []).append((rsa_id, lib, usage_ref))
            else:
                orphan_rows.append((rsa_id, lib, usage_ref))

        # RSA sans correspondance aux libellés canoniques : supprimer seulement si jamais utilisés.
        for rsa_id, lib, _usage_mois in orphan_rows:
            total = _rsa_total_usage(cur, rsa_id)
            if total <= 0:
                cur.execute(
                    "UPDATE structure_organigram_node SET rsa_id = NULL WHERE rsa_id = %s",
                    (rsa_id,),
                )
                cur.execute("DELETE FROM ref_subdivision_arme WHERE id = %s", (rsa_id,))
                deleted += 1
            else:
                cur.execute(
                    "UPDATE ref_subdivision_arme SET actif = 0 WHERE id = %s",
                    (rsa_id,),
                )
                deactivated += 1
                scope = f"mois {mois_s}" if mois_s else "toutes periodes"
                warnings.append(
                    f"RSA {rsa_id} « {lib} » desactive (hors libelles {scope}, {total} fiche(s) historiques)."
                )

        for canon, members in groups.items():
            if len(members) <= 1:
                rid, _lib, _u = members[0]
                cur.execute(
                    "UPDATE ref_subdivision_arme SET libelle = %s, actif = 1 WHERE id = %s",
                    (canon, rid),
                )
                continue

            def _rank(item: tuple[int, str, int]) -> tuple:
                rid, lib, usage = item
                exact = 1 if _norm_subdiv_label(lib) == _norm_subdiv_label(canon) else 0
                return (usage, exact, -rid)

            members.sort(key=_rank, reverse=True)
            keeper_id, _keeper_lib, _ = members[0]
            cur.execute(
                "UPDATE ref_subdivision_arme SET libelle = %s, actif = 1 WHERE id = %s",
                (canon, keeper_id),
            )
            for dup_id, dup_lib, _ in members[1:]:
                _merge_rsa_into(cur, dup_id, keeper_id)
                merged += 1
                warnings.append(
                    f"Fusion RSA {dup_id} « {dup_lib} » -> {keeper_id} « {canon} » (structure_id={sid})."
                )

    return merged, deleted, deactivated, warnings


def backfill_rsa_from_carriere(cur: Any, cache: RefEtlCache, *, mois: str | None = None) -> int:
    """Crée les RSA manquants pour chaque couple (structure, SubArme) en carrière."""
    flat = load_subdiv_flat_rows(cur)
    params: tuple = ()
    mois_clause = ""
    if mois:
        mois_clause = """
          AND EXISTS (
            SELECT 1 FROM sme_periode p
            WHERE p.id = c.sme_periode_id AND p.annee_mois = %s
          )
        """
        params = (mois.strip(),)
    cur.execute(
        f"""
        SELECT DISTINCT c.structure_id, TRIM(c.subdivision_arme_libelle)
        FROM carriere_situation c
        WHERE c.structure_id IS NOT NULL
          AND c.subdivision_arme_libelle IS NOT NULL
          AND TRIM(c.subdivision_arme_libelle) <> ''
          {mois_clause}
        ORDER BY c.structure_id, TRIM(c.subdivision_arme_libelle)
        """,
        params,
    )
    created = 0
    for sid, lib in cur.fetchall():
        sid_i = int(sid)
        lib_s = str(lib or "").strip()
        if not lib_s:
            continue
        before = len(cache.subdiv_arme_by_struct_leaf)
        rid = ensure_ref_subdiv_arme(
            cur,
            cache,
            structure_id=sid_i,
            subdivision_libelle=lib_s,
            flat_rows=flat,
            auto_create=True,
        )
        if rid and len(cache.subdiv_arme_by_struct_leaf) > before:
            created += 1
    return created


def propagate_t_leaf_to_structures(
    cur: Any,
    cache: RefEtlCache,
    *,
    leaf_node_id: int,
    libelle: str | None = None,
    structure_ids: list[int] | None = None,
    only_structures_with_carriere: bool = True,
) -> int:
    """
    Crée ``ref_subdivision_arme`` pour un nœud T vers une ou plusieurs structures.

    Par défaut ne cible que les structures où la carrière porte déjà ce libellé SubArme.
    """
    leaf_id = int(leaf_node_id)
    if not _is_leaf(cur, leaf_id):
        raise ValueError("Seuls les nœuds feuille (niveau T) peuvent être propagés.")
    if _node_depth(cur, leaf_id) < 3:
        raise ValueError("Le nœud sélectionné n'est pas une feuille T valide.")

    cur.execute("SELECT libelle FROM ref_subdivision_noeud WHERE id = %s", (leaf_id,))
    row = cur.fetchone()
    lb = (libelle or (str(row[0]) if row else "") or "").strip()
    if not lb:
        raise ValueError("Libellé SubArme requis.")

    flat = load_subdiv_flat_rows(cur)
    cache.leaf_node_by_design_t[_cf(lb)] = leaf_id
    targets: set[int] = set(int(x) for x in (structure_ids or []))
    if not targets:
        if only_structures_with_carriere:
            cur.execute(
                """
                SELECT DISTINCT structure_id FROM carriere_situation
                WHERE structure_id IS NOT NULL
                  AND TRIM(subdivision_arme_libelle) = %s
                """,
                (lb,),
            )
            targets = {int(r[0]) for r in cur.fetchall()}
        else:
            cur.execute("SELECT id FROM ref_structure")
            targets = {int(r[0]) for r in cur.fetchall()}

    n = 0
    for sid in sorted(targets):
        rid = ensure_ref_subdiv_arme(
            cur,
            cache,
            structure_id=sid,
            subdivision_libelle=lb,
            flat_rows=flat,
            auto_create=False,
        )
        if rid:
            cur.execute(
                """
                UPDATE ref_subdivision_arme
                SET noeud_feuille_id = %s, libelle = %s, actif = 1
                WHERE id = %s
                """,
                (leaf_id, lb, int(rid)),
            )
            n += 1
    return n


def drop_tab_legacy_tables(settings: Settings, *, backup_dir, maintenance_confirmed=False, expected=None) -> list[str]:
    """Dedicated, backed-up DDL phase; never accept a business cursor."""
    from sme_gend.services.legacy_cleanup import run_legacy_cleanup
    return run_legacy_cleanup(settings,backup_dir=backup_dir,maintenance_confirmed=maintenance_confirmed,expected=expected)['tables_dropped']


@automatic_write
def rebuild_liste_present_layout(settings: Settings) -> ListeLayoutConfig:
    """Régénère le layout depuis ref_structure + ref_subdivision_arme."""
    from sme_gend.repositories.mysql_bundle_repositories import MySQLStructureSubdivisionRepository

    layout_service=LayoutConfigService(settings)
    previous_layout=layout_service.load()
    struct_repo = StructureRepository(settings)
    rsa_repo = MySQLStructureSubdivisionRepository(settings)
    structures = struct_repo.fetch_all()
    sections: list[SectionBlock] = []
    for i, st in enumerate(structures):
        labels, rsa_ids = rsa_repo.fetch_rsa_order_for_structure(st.code)
        sections.append(
            SectionBlock(
                id=f"auto-{st.code}-{i}",
                order=i,
                structure_code=st.code,
                title_main_override=None,
                title_sub_override=None,
                group_by_subdivision=True,
                subdivision_order=labels or None,
                subdivision_rsa_order=rsa_ids or None,
            )
        )
    cfg = ListeLayoutConfig(sections=sections,write_token=previous_layout.write_token)
    layout_service.save(cfg)
    return cfg


@automatic_write
def run_referentiel_maintenance(
    settings: Settings,
    *,
    migrate_org: bool = True,
    backfill_rsa: bool = True,
    dedupe_rsa: bool = True,
    rebuild_layout: bool = True,
    drop_legacy: bool = False,
    resync_dossier: bool = True,
    resync_fk: bool = True,
    backfill_fonction_structure: bool = True,
    reconcile_carriere: bool = False,
    normalize_carriere_labels: bool = False,
    all_periods: bool = False,
    mois: str | None = None,
) -> ReferentielMaintenanceReport:
    """Pipeline complet post-migration / alignement référentiels."""
    from sme_gend.config.sme_context import load_sme_context

    report = ReferentielMaintenanceReport()
    mc = MySQLConnection(settings)
    mois_ctx = (mois or load_sme_context().mois_actif or "").strip()
    dedupe_mois: str | None = None if all_periods else (mois_ctx or None)
    backfill_mois: str | None = None if all_periods else ((mois or mois_ctx or None) or None)
    scope_mois: str | None = None if all_periods else mois

    with mc.connect() as conn:
        with conn.cursor() as cur:
            cache = load_ref_cache_from_db(cur)
            if migrate_org:
                report.org_nodes_migrated = migrate_misplaced_org_nodes(cur, cache)
            if normalize_carriere_labels or all_periods:
                n_norm, norm_warn = normalize_subdiv_libelles_in_carriere(cur)
                report.carriere_subdiv_labels_normalized = n_norm
                report.warnings.extend(norm_warn[:40])
            if dedupe_rsa and (all_periods or dedupe_mois):
                merged, deleted, deactivated, dedupe_warn = dedupe_rsa_from_carriere(
                    cur, mois=dedupe_mois
                )
                report.rsa_merged = merged
                report.rsa_deleted = deleted
                report.rsa_deactivated = deactivated
                report.warnings.extend(dedupe_warn[:50])
            if backfill_rsa:
                cache = load_ref_cache_from_db(cur)
                report.rsa_backfilled = backfill_rsa_from_carriere(
                    cur, cache, mois=backfill_mois
                )
        conn.commit()

    if rebuild_layout:
        cfg = rebuild_liste_present_layout(settings)
        report.layout_sections = len(cfg.sections)
        report.layout_subdivisions = sum(
            len(s.subdivision_order or []) for s in cfg.sections
        )

    if resync_fk:
        from sme_gend.services.sot_maintenance import resync_fk_referentiels

        report.fk_resync = resync_fk_referentiels(settings, mois=scope_mois)

    if reconcile_carriere or all_periods:
        from sme_gend.services.carriere_reconciliation import reconcile_carriere_referentiels

        rec = reconcile_carriere_referentiels(
            settings, mois=scope_mois, log_unresolved=False
        )
        report.carriere_reconciled = rec.to_summary_dict()

    if resync_dossier:
        from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch

        out = project_carriere_from_dossier_batch(
            settings,
            mois=scope_mois,
            only_est_actuelle=not bool(scope_mois),
        )
        report.dossier_projected = int(out.get("projected", 0))

    if backfill_fonction_structure:
        from sme_gend.services.referentiel_service import ReferentielService

        report.fonction_structure_links = ReferentielService(
            settings
        ).fonction_backfill_structure_links()

    return report


@automatic_write
def run_archives_referentiel_alignment(settings: Settings) -> ReferentielMaintenanceReport:
    """
    Alignement structures / subdivisions sur l'ensemble des archives importées.

    À lancer après une série d'imports (.mdb / Excel) lorsque des doublons
    ou fautes d'orthographe apparaissent entre mois.
    """
    report = run_referentiel_maintenance(
        settings,
        migrate_org=True,
        backfill_rsa=True,
        dedupe_rsa=True,
        rebuild_layout=True,
        resync_dossier=False,
        resync_fk=True,
        reconcile_carriere=True,
        normalize_carriere_labels=True,
        all_periods=True,
        mois=None,
    )
    from sme_gend.services.structure_organigram_service import StructureOrganigramService

    svc = StructureOrganigramService(settings)
    n_att = 0
    for node in svc.fetch_tree():
        n_att += svc.import_all_data_subdivisions(node.structure_id, node.code)
    if n_att:
        svc.sync_layout_from_database()
        report.warnings.insert(
            0, f"Subdivisions « données seules » rattachées au référentiel : {n_att}."
        )
    return report
