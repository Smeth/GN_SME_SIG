"""Export MySQL → fichier Access bundle complet (miroir de l'import Access → MySQL)."""

from __future__ import annotations

import logging
from contextlib import closing
from sme_gend.services.access_identity_map import write_identity_map
from sme_gend.services.access_agent_profile import write_profiles
from sme_gend.services.safe_paths import atomic_destination
import shutil
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from sme_gend.config.liste_generale_scope import (
    carriere_scope_for_explicit_export_mois,
    carriere_scope_for_settings,
    settings_for_chosen_export_mois,
)
from sme_gend.config.settings import Settings, resolve_access_template_mdb
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.access_connection import AccessConnection
from sme_gend.repositories.gain_perte_access_repository import AccessGainPerteRepository
from sme_gend.repositories.liste_generale_access_repository import AccessListeGeneraleRepository
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.mysql_import_period import normalize_import_mois

_log = logging.getLogger(__name__)


class _AccessGradeCodes:
    """Resolve personnel values to exact reference codes, never guess a grade."""

    def __init__(self, grades=()):
        from sme_gend.services.grade_label_format import normalize_grade_key
        self._normalize = normalize_grade_key
        self._codes = {}
        self._labels = {}
        for code, label, *_ in grades:
            code = str(code or "").strip()
            if not code:
                continue
            self._codes.setdefault(code.casefold(), set()).add(code)
            key = normalize_grade_key(str(label or ""))
            if key:
                self._labels.setdefault(key, set()).add(code)

    def resolve(self, raw):
        value = str(raw or "").strip()
        if not value:
            return None
        codes = self._codes.get(value.casefold()) or self._labels.get(self._normalize(value), set())
        if len(codes) != 1:
            reason = "ambigu" if codes else "absent du référentiel"
            raise ValueError(f"Export Access annulé : grade « {value} » {reason}. Corrigez sa correspondance avec un code de grade.")
        return next(iter(codes))


def _fetch_grade_codes(mcur):
    mcur.execute("SELECT code, libelle FROM ref_grade ORDER BY pref_ord, code")
    return _AccessGradeCodes(mcur.fetchall())

_ACCESS_TABLES_CLEAR_ORDER = (
    "TabMission",
    "TabMalade",
    "TabDeserteur",
    "TabStageLocal",
    "TabStageEx",
    "TabStageInterne",
    "TabGainPerte",
    "TabConge",
    "ListeGenerale",
    "TabSme",
    "TabSecun",
    "TabSecdeux",
    "TabSectrois",
    "TabStructure",
    "TabGrade",
)

_CONGE_TYPE_TO_ACCESS = {
    "ADMIN": "Congé administratif",
    "EXPECTATIVE": "Congé d'expectative",
    "AUTRE": "Autre congé",
}


@dataclass
class ExportListeRow:
    agent_id: int
    row: ListeGeneraleRow
    access_id: str = ""


@dataclass
class AccessExportResult:
    sme_mois: str
    output_path: Path
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    new_legacy_ids: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sme_mois": self.sme_mois,
            "output_path": str(self.output_path),
            "counts": dict(self.counts),
            "warnings": list(self.warnings),
            "new_legacy_ids": self.new_legacy_ids,
        }


def export_bundle_filename(mois: str, *, stamp: str | None = None) -> str:
    ym = normalize_import_mois(mois)
    ts = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"sme_bundle_{ym}_{ts}.mdb"


def mouvement_to_gain_perte_row(row: tuple[Any, ...]) -> GainPerteRow:
    """Convertit une ligne ``mouvement_effectif`` (+ legacy_access_id) en ``GainPerteRow``."""
    leg = str(row[0] or "").strip()
    grade = _str_or_none(row[1])
    nom = _str_or_none(row[2])
    cat = _str_or_none(row[3])
    typ = _str_or_none(row[4])
    cdsme = _str_or_none(row[5])
    motif = _str_or_none(row[6])
    observ = _str_or_none(row[7])
    objet = cat or typ
    obs = motif or observ
    return GainPerteRow(
        id=leg,
        grade=grade,
        nom=nom,
        objet=objet,
        cdsme=cdsme,
        observ=obs,
    )


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _to_access_date(v: Any) -> datetime | date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return v


def _stage_date_debut_fin(deb: Any, fin: Any, obs: Any) -> str | None:
    def _fmt(d: Any) -> str:
        if d is None:
            return ""
        if hasattr(d, "strftime"):
            return d.strftime("%d/%m/%Y")
        return str(d).strip()

    d0, d1 = _fmt(deb), _fmt(fin)
    if d0 and d1:
        return f"{d0} — {d1}"
    if d0:
        return d0
    if d1:
        return d1
    return _str_or_none(obs)


def _access_table_exists(cur: Any, table: str) -> bool:
    try:
        cur.execute(f"SELECT TOP 1 * FROM [{table}]")
        cur.fetchone()
        return True
    except Exception:
        return False


def _clear_access_tables(cur: Any) -> list[str]:
    tables = {}
    for row in cur.tables():
        name, kind = (row.table_name, row.table_type) if hasattr(row, "table_name") else (row[2], row[3])
        tables[str(name).casefold()] = str(kind).upper()
    skipped = []
    for table in _ACCESS_TABLES_CLEAR_ORDER:
        kind = tables.get(table.casefold())
        if kind is None:
            skipped.append(table)
            continue
        if kind != "TABLE":
            raise ValueError(f"Le modèle Access doit utiliser une table locale : {table}. Export annulé.")
        try:
            cur.execute(f"DELETE FROM [{table}]")
        except Exception as exc:
            raise RuntimeError(f"Impossible de vider la table Access {table}. Export annulé pour préserver les données.") from exc
    return skipped


def _fetch_liste_rows(
    cur: Any,
    settings: Settings,
    *,
    export_mois: str | None = None,
) -> list[ExportListeRow]:
    if export_mois:
        filt, params = carriere_scope_for_explicit_export_mois(export_mois, alias="c")
    else:
        filt, params = carriere_scope_for_settings(settings, alias="c")
    sql = f"""
SELECT
  a.id,
  a.legacy_access_id,
  a.noms,
  a.date_naissance,
  a.lieu_naissance,
  a.sexe,
  IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle),
  c.date_grade,
  c.date_service,
  c.ndecision,
  a.matricule_militaire,
  a.matricule_solde,
  IF(c.position_admin_id IS NOT NULL, rpa.libelle, c.position_admin_libelle),
  IF(c.fonction_id IS NOT NULL, rf.libelle, c.fonction_libelle),
  IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle, c.subdivision_arme_libelle),
  c.diplome_libelle,
  c.date_obtention,
  IF(c.structure_id IS NOT NULL, rs.code, c.structure_code),
  c.obs,
  c.ordre_tri,
  c.presseance,
  c.id,
  a.telephone,
  a.adresse_residence
FROM agent a
INNER JOIN carriere_situation c ON c.agent_id = a.id AND {filt}
LEFT JOIN ref_structure rs ON rs.id = c.structure_id
LEFT JOIN ref_grade rg ON rg.id = c.grade_id
LEFT JOIN ref_position_admin rpa ON rpa.id = c.position_admin_id
LEFT JOIN ref_fonction rf ON rf.id = c.fonction_id
LEFT JOIN ref_subdivision_arme rsa ON rsa.id = c.subdivision_arme_id
ORDER BY c.ordre_tri, a.noms
"""
    cur.execute(sql, params)
    out: list[ExportListeRow] = []
    for row in cur.fetchall():
        agent_id = int(row[0])
        legacy = _norm_id(row[1])
        liste = ListeGeneraleRow(
            id=str(row[21]),
            access_join_id=legacy or None,
            noms=row[2],
            date_naissance=row[3],
            lieu_naissance=row[4],
            sexe=row[5],
            telephone=row[22],
            adresse_residence=row[23],
            grade=row[6],
            date_grade=row[7],
            date_service=row[8],
            ndecision=row[9],
            matricule_militaire=row[10],
            matricule_solde=row[11],
            position_admin=row[12],
            fonction=row[13],
            subdivision_arme=row[14],
            diplome=row[15],
            date_obtention=row[16],
            structure=row[17],
            obs=row[18],
            ordre_tri=row[19],
            presseance=row[20],
        )
        out.append(ExportListeRow(agent_id=agent_id, row=liste, access_id=legacy))
    return out


def _write_structures(cur: Any, mcur: Any) -> int:
    mcur.execute("SELECT code, designation, ordre_tri FROM ref_structure ORDER BY ordre_tri, code")
    n = 0
    for code, designation, ordre_tri in mcur.fetchall():
        c = _norm_id(code)
        if not c:
            continue
        cur.execute(
            "INSERT INTO TabStructure (code, designation, OderTri) VALUES (?, ?, ?)",
            (c, designation, ordre_tri),
        )
        n += 1
    return n


def _write_grades(cur: Any, mcur: Any) -> int:
    mcur.execute(
        "SELECT code, libelle, pref_ord, effectif_g FROM ref_grade ORDER BY pref_ord, code"
    )
    n = 0
    for code, libelle, pref_ord, effectif_g in mcur.fetchall():
        c = _norm_id(code)
        if not c:
            continue
        cur.execute(
            "INSERT INTO TabGrade (CodeG, Grade, PrefOrd, EffectifG) VALUES (?, ?, ?, ?)",
            (c, libelle, pref_ord, effectif_g),
        )
        n += 1
    return n


def _write_subdivisions(cur: Any, mcur: Any) -> dict[str, int]:
    counts = {"TabSecun": 0, "TabSecdeux": 0, "TabSectrois": 0}
    mcur.execute(
        "SELECT code, libelle FROM ref_subdivision_noeud WHERE parent_id = 1 ORDER BY code"
    )
    for code_u, design_u in mcur.fetchall():
        cu = _norm_id(code_u)
        if not cu:
            continue
        cur.execute(
            "INSERT INTO TabSecun (CodeU, SecuDesign) VALUES (?, ?)",
            (cu, design_u),
        )
        counts["TabSecun"] += 1

    mcur.execute(
        """
        SELECT u.code, d.code, d.libelle, d.pri_out
        FROM ref_subdivision_noeud d
        INNER JOIN ref_subdivision_noeud u ON d.parent_id = u.id
        WHERE u.parent_id = 1
        ORDER BY u.code, d.code
        """
    )
    for code_u, code_d, design_d, pri_out in mcur.fetchall():
        cu, cd = _norm_id(code_u), _norm_id(code_d)
        if not cu or not cd:
            continue
        cur.execute(
            "INSERT INTO TabSecdeux (CodeU, CodeD, SecdDesign, PriOutD) VALUES (?, ?, ?, ?)",
            (cu, cd, design_d, pri_out),
        )
        counts["TabSecdeux"] += 1

    mcur.execute(
        """
        SELECT d.code, t.code, t.libelle, t.pri_out
        FROM ref_subdivision_noeud t
        INNER JOIN ref_subdivision_noeud d ON t.parent_id = d.id
        INNER JOIN ref_subdivision_noeud u ON d.parent_id = u.id
        WHERE u.parent_id = 1
        ORDER BY d.code, t.code
        """
    )
    for code_d, code_t, design_t, pri_out in mcur.fetchall():
        cd, ct = _norm_id(code_d), _norm_id(code_t)
        if not cd or not ct:
            continue
        cur.execute(
            "INSERT INTO TabSectrois (CodeD, CodeT, SectDesign, PriOutT) VALUES (?, ?, ?, ?)",
            (cd, ct, design_t, pri_out),
        )
        counts["TabSectrois"] += 1
    return counts


def _write_sme_periods(cur: Any, mcur: Any) -> int:
    mcur.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(codsme), ''), annee_mois), annee_mois, statut
        FROM sme_periode ORDER BY annee_mois
        """
    )
    n = 0
    for codsme, mois, statut in mcur.fetchall():
        sr = SmeRow.from_sequence((codsme, mois, statut))
        if not sr.codsme and not sr.mois:
            continue
        cur.execute(
            "INSERT INTO TabSme (Codsme, mois, Statut) VALUES (?, ?, ?)",
            (sr.codsme or sr.mois, sr.mois, sr.statut or "Ouvert"),
        )
        n += 1
    return n


def _write_liste_generale(
    cur: Any,
    rows: list[ExportListeRow],
    *,
    access_repo: AccessListeGeneraleRepository,
    grade_codes: _AccessGradeCodes | None = None,
) -> tuple[int, int, dict[int, str]]:
    """Retourne (écrits, nouveaux legacy, map agent_id → access_id)."""
    written = 0
    new_legacy = 0
    id_map: dict[int, str] = {}
    grade_codes = grade_codes or _AccessGradeCodes()
    # Resolve the whole list before writing any personnel row.
    resolved_grades = {id(item): grade_codes.resolve(item.row.grade) for item in rows}
    insert_sql = """
INSERT INTO ListeGenerale (
    Id, noms, DateN, LieuN, Grade, DateGrade, DateService, Ndecision,
    Matmilitaire, Matsolde, Padmin, Fonction, SubArme,
    Diplome, DateObtention, Structure, Obs, OderTri, presseance
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
    ordered = sorted(rows, key=lambda item: not bool(item.access_id or item.row.access_join_id)
                     or str(item.access_id or item.row.access_join_id).startswith("GEN-"))
    used = set()
    for item in rows:
        key = item.access_id or item.row.access_join_id or ""
        if key and not key.startswith("GEN-"):
            try: used.add(int(key))
            except ValueError: pass  # Text-id templates retain their external ids.
    next_id = max(used | {0}) + 1
    if next_id > 2147483647:
        next_id = 1
    for item in ordered:
        r = item.row
        legacy = item.access_id or _norm_id(r.access_join_id)
        params = (
            r.noms,
            _to_access_date(r.date_naissance),
            r.lieu_naissance,
            resolved_grades[id(item)],
            _to_access_date(r.date_grade),
            r.date_service,
            r.ndecision,
            r.matricule_militaire,
            r.matricule_solde,
            r.position_admin,
            r.fonction,
            r.subdivision_arme,
            r.diplome,
            r.date_obtention,
            r.structure,
            r.obs,
            r.ordre_tri,
            r.presseance,
        )
        access_id = legacy
        if legacy and not legacy.startswith("GEN-"):
            try:
                id_param: str | int = int(legacy)
            except ValueError:
                id_param = legacy
            # An external id must never be silently reassigned on SQL failure.
            cur.execute(insert_sql, (id_param, *params))
            access_id = str(id_param)
        else:
            # Some official templates have a required LONG Id without AutoNumber.
            # Allocate only inside this new file, after reserving every external Id.
            while next_id in used and next_id <= 2147483647:
                next_id += 1
            if next_id > 2147483647:
                raise ValueError("Aucun identifiant numérique Access disponible.")
            cur.execute(insert_sql, (next_id, *params))
            access_id = str(next_id)
            used.add(next_id)
            next_id += 1
            new_legacy += 1
        item.access_id = access_id
        id_map[item.agent_id] = access_id
        written += 1
    return written, new_legacy, id_map


def _periode_id(mcur: Any, mois: str) -> int | None:
    mcur.execute("SELECT id FROM sme_periode WHERE annee_mois = %s LIMIT 1", (mois,))
    row = mcur.fetchone()
    return int(row[0]) if row else None


def _write_satellites(
    cur: Any,
    mcur: Any,
    *,
    mois: str,
    periode_id: int | None,
    id_map: dict[int, str],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    def _access_id(agent_id: int, legacy: str | None) -> str | None:
        aid = id_map.get(agent_id)
        if aid:
            return aid
        leg = _norm_id(legacy)
        return leg or None

    if periode_id is not None:
        mcur.execute(
            """
            SELECT m.agent_id, a.legacy_access_id, m.nature_libelle, m.lieu,
                   m.reference_dossier, m.date_debut
            FROM agent_mission m
            INNER JOIN agent a ON a.id = m.agent_id
            INNER JOIN (
              SELECT agent_id, MAX(COALESCE(date_debut, '1900-01-01')) AS dmax
              FROM agent_mission WHERE sme_periode_id = %s
              GROUP BY agent_id
            ) last ON last.agent_id = m.agent_id
              AND COALESCE(m.date_debut, '1900-01-01') = last.dmax
            WHERE m.sme_periode_id = %s
            """,
            (periode_id, periode_id),
        )
    else:
        mcur.execute(
            """
            SELECT m.agent_id, a.legacy_access_id, m.nature_libelle, m.lieu,
                   m.reference_dossier, m.date_debut
            FROM agent_mission m
            INNER JOIN agent a ON a.id = m.agent_id
            """
        )
    n = 0
    for agent_id, legacy, nature, lieu, ref, ddep in mcur.fetchall():
        kid = _access_id(int(agent_id), legacy)
        if not kid:
            continue
        cur.execute(
            "INSERT INTO TabMission (ID, Nature, LieuMission, Refnds, DateDepart) VALUES (?,?,?,?,?)",
            (kid, nature, lieu, ref, _to_access_date(ddep)),
        )
        n += 1
    counts["TabMission"] = n

    if periode_id is not None:
        mcur.execute(
            """
            SELECT m.agent_id, a.legacy_access_id, m.date_debut, m.pathologie, m.observation
            FROM agent_malade m
            INNER JOIN agent a ON a.id = m.agent_id
            INNER JOIN (
              SELECT agent_id, MAX(date_debut) AS dmax
              FROM agent_malade WHERE sme_periode_id = %s
              GROUP BY agent_id
            ) last ON last.agent_id = m.agent_id AND m.date_debut = last.dmax
            WHERE m.sme_periode_id = %s
            """,
            (periode_id, periode_id),
        )
    else:
        mcur.execute(
            """
            SELECT m.agent_id, a.legacy_access_id, m.date_debut, m.pathologie, m.observation
            FROM agent_malade m
            INNER JOIN agent a ON a.id = m.agent_id
            """
        )
    n = 0
    for agent_id, legacy, d0, patho, obs in mcur.fetchall():
        kid = _access_id(int(agent_id), legacy)
        if not kid or d0 is None:
            continue
        cur.execute(
            "INSERT INTO TabMalade (ID, DateConstat, Pathologie, Obs) VALUES (?,?,?,?)",
            (kid, _to_access_date(d0), patho, obs),
        )
        n += 1
    counts["TabMalade"] = n

    if periode_id is not None:
        mcur.execute(
            """
            SELECT d.agent_id, a.legacy_access_id, d.date_desertion
            FROM agent_desertion d
            INNER JOIN agent a ON a.id = d.agent_id
            WHERE d.sme_periode_id = %s
            """,
            (periode_id,),
        )
    else:
        mcur.execute(
            """
            SELECT d.agent_id, a.legacy_access_id, d.date_desertion
            FROM agent_desertion d
            INNER JOIN agent a ON a.id = d.agent_id
            """
        )
    n = 0
    for agent_id, legacy, dd in mcur.fetchall():
        kid = _access_id(int(agent_id), legacy)
        if not kid or dd is None:
            continue
        cur.execute(
            "INSERT INTO TabDeserteur (ID, DatDesertion) VALUES (?, ?)",
            (kid, _to_access_date(dd)),
        )
        n += 1
    counts["TabDeserteur"] = n

    for acc_tbl, type_code in (
        ("TabStageLocal", "LOCAL"),
        ("TabStageEx", "EXTERIEUR"),
        ("TabStageInterne", "INTERNE"),
    ):
        if periode_id is not None:
            mcur.execute(
                """
                SELECT ps.agent_id, a.legacy_access_id, ps.nature_libelle, ps.lieu,
                       ps.observation, ps.date_debut, ps.date_fin
                FROM parcours_stage ps
                INNER JOIN agent a ON a.id = ps.agent_id
                WHERE ps.sme_periode_id = %s AND ps.type_stage_code = %s
                """,
                (periode_id, type_code),
            )
        else:
            mcur.execute(
                """
                SELECT ps.agent_id, a.legacy_access_id, ps.nature_libelle, ps.lieu,
                       ps.observation, ps.date_debut, ps.date_fin
                FROM parcours_stage ps
                INNER JOIN agent a ON a.id = ps.agent_id
                WHERE ps.type_stage_code = %s
                """,
                (type_code,),
            )
        n = 0
        for agent_id, legacy, nature, lieu, obs, deb, fin in mcur.fetchall():
            kid = _access_id(int(agent_id), legacy)
            if not kid:
                continue
            ddf = _stage_date_debut_fin(deb, fin, obs)
            cur.execute(
                f"INSERT INTO [{acc_tbl}] (ID, Naturestage, LieuStage, DateDebutFin) VALUES (?,?,?,?)",
                (kid, nature, lieu, ddf),
            )
            n += 1
        counts[acc_tbl] = n

    return counts


def _write_gain_perte(cur: Any, mcur: Any, *, gp_repo: AccessGainPerteRepository, periode_id: int, id_map: dict[int, str], grade_codes: _AccessGradeCodes | None = None) -> int:
    grade_codes = grade_codes or _AccessGradeCodes()
    mcur.execute(
        """
        SELECT a.legacy_access_id, m.grade_snapshot, m.nom_snapshot,
               m.categorie_mouvement, m.type_mouvement, m.cdsme_derive,
               m.motif, m.observ, a.id
        FROM mouvement_effectif m
        INNER JOIN agent a ON a.id = m.agent_id
        WHERE m.sme_periode_id = %s
        ORDER BY m.date_reference, m.id
        """, (periode_id,)
    )
    n = 0
    for row in mcur.fetchall():
        gp = mouvement_to_gain_perte_row((id_map.get(int(row[8]), row[0]), *row[1:8]))
        if not gp.id:
            continue
        gp = replace(gp, grade=grade_codes.resolve(gp.grade))
        gp_repo.insert_row(gp, cursor=cur)
        n += 1
    return n


def _write_conges(cur: Any, mcur: Any, *, periode_id: int | None, id_map: dict[int, str]) -> int:
    if not _access_table_exists(cur, "TabConge"):
        return 0
    if periode_id is not None:
        mcur.execute(
            """
            SELECT c.agent_id, a.legacy_access_id, c.type_conge_code,
                   c.date_debut, c.date_fin, c.quota_jours, c.observation
            FROM agent_conge c
            INNER JOIN agent a ON a.id = c.agent_id
            WHERE c.sme_periode_id = %s
            """,
            (periode_id,),
        )
    else:
        mcur.execute(
            """
            SELECT c.agent_id, a.legacy_access_id, c.type_conge_code,
                   c.date_debut, c.date_fin, c.quota_jours, c.observation
            FROM agent_conge c
            INNER JOIN agent a ON a.id = c.agent_id
            """
        )
    n = 0
    for agent_id, legacy, type_code, d0, d1, quota, obs in mcur.fetchall():
        kid = id_map.get(int(agent_id)) or _norm_id(legacy)
        if not kid or d0 is None:
            continue
        tc = _CONGE_TYPE_TO_ACCESS.get(str(type_code or "").strip().upper(), str(type_code or ""))
        cur.execute(
            "INSERT INTO TabConge (ID, TypeConge, DateDebut, DateFin, Quota, Obs) VALUES (?,?,?,?,?,?)",
            (kid, tc, _to_access_date(d0), _to_access_date(d1), quota, obs),
        )
        n += 1
    return n


def _persist_new_legacy_ids(mcur: Any, assignments: list[tuple[int, str]]) -> int:
    n = 0
    for agent_id, legacy in assignments:
        leg = _norm_id(legacy)
        if not leg:
            continue
        mcur.execute(
            """
            UPDATE agent SET legacy_access_id = %s
            WHERE id = %s AND (legacy_access_id IS NULL OR TRIM(legacy_access_id) = '')
            """,
            (leg, agent_id),
        )
        n += int(mcur.rowcount or 0)
    return n


def export_mysql_bundle_to_access(
    mysql_settings: Settings,
    *,
    mois: str,
    export_dir: Path,
    template_mdb: Path | None = None,
    progress: Callable[[str], None] | None = None,
    persist_legacy_ids: bool = True,
) -> AccessExportResult:
    """
    Exporte un bundle Access complet pour le mois ``mois`` (``YYYY-MM``).

    Copie le modèle ``smegend.mdb``, vide les tables métier et les remplit depuis MySQL.
    """
    ym = normalize_import_mois(mois)
    data_settings = settings_for_chosen_export_mois(mysql_settings, ym)
    export_dir = Path(export_dir).expanduser().resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    tpl = Path(template_mdb).expanduser().resolve() if template_mdb else resolve_access_template_mdb()
    if not tpl.is_file():
        raise FileNotFoundError(
            f"Modèle Access introuvable : {tpl}. Placez smegend.mdb dans le dossier modeles/."
        )

    out_name = export_bundle_filename(ym)
    out_path = export_dir / out_name
    if out_path.exists():
        raise FileExistsError(f"Le fichier existe déjà : {out_path}")

    with atomic_destination(out_path, temporary_suffix=tpl.suffix, replace_existing=False) as working_path:
        if progress:
            progress(f"Copie du modèle Access…")
        shutil.copy2(tpl, working_path)

        access_settings = mysql_settings.__class__(
            database_path=working_path,
            mysql_host=data_settings.mysql_host,
            mysql_port=data_settings.mysql_port,
            mysql_database=data_settings.mysql_database,
            mysql_user=data_settings.mysql_user,
            mysql_password=data_settings.mysql_password,
            mysql_charset=data_settings.mysql_charset,
            mysql_view_mois=ym,
            template_liste_present_path=data_settings.template_liste_present_path,
            template_numerique_path=data_settings.template_numerique_path,
            template_detaches_path=data_settings.template_detaches_path,
            odbc_driver=data_settings.odbc_driver,
        )

        counts: dict[str, int] = {}
        warnings: list[str] = []
        new_legacy = 0

        ac = AccessConnection(access_settings)
        mc = MySQLConnection(data_settings)
        liste_repo = AccessListeGeneraleRepository(access_settings)
        gp_repo = AccessGainPerteRepository(access_settings)

        with mc.connect() as mconn:
            with mconn.cursor() as mcur:
                if progress:
                    progress(f"Lecture liste générale ({ym})…")
                liste_rows = _fetch_liste_rows(mcur, data_settings, export_mois=ym)
                grade_codes = _fetch_grade_codes(mcur)
                for item in liste_rows:
                    grade_codes.resolve(item.row.grade)
                periode_id = _periode_id(mcur, ym)
                if periode_id is None:
                    raise ValueError(f"La période {ym} n'existe pas ; export annulé.")

                with closing(ac.connect()) as aconn, aconn:
                    aconn.autocommit = False
                    with closing(aconn.cursor()) as acur:
                        skipped = _clear_access_tables(acur)
                        if skipped:
                            warnings.append(
                                "Tables Access absentes ou non vidées : " + ", ".join(skipped)
                            )

                        if progress:
                            progress("Écriture référentiels…")
                        counts["TabStructure"] = _write_structures(acur, mcur)
                        counts["TabGrade"] = _write_grades(acur, mcur)
                        counts.update(_write_subdivisions(acur, mcur))
                        counts["TabSme"] = _write_sme_periods(acur, mcur)

                        if progress:
                            progress(f"Écriture ListeGenerale ({len(liste_rows)} lignes)…")
                        lg_n, new_legacy, id_map = _write_liste_generale(
                            acur, liste_rows, access_repo=liste_repo, grade_codes=grade_codes
                        )
                        counts["ListeGenerale"] = lg_n
                        write_identity_map(acur, liste_rows, id_map)
                        write_profiles(acur, liste_rows, id_map)

                        if progress:
                            progress("Écriture satellites (missions, malades, stages…)…")
                        counts.update(
                            _write_satellites(
                                acur,
                                mcur,
                                mois=ym,
                                periode_id=periode_id,
                                id_map=id_map,
                            )
                        )

                        if progress:
                            progress("Écriture gains & pertes…")
                        counts["TabGainPerte"] = _write_gain_perte(acur, mcur, gp_repo=gp_repo, periode_id=periode_id, id_map=id_map, grade_codes=grade_codes)

                        if progress:
                            progress("Écriture congés (si TabConge présente)…")
                        counts["TabConge"] = _write_conges(
                            acur, mcur, periode_id=periode_id, id_map=id_map
                        )

                if persist_legacy_ids and new_legacy:
                    assignments = [
                        (item.agent_id, item.access_id)
                        for item in liste_rows
                        if item.access_id
                    ]
                    if progress:
                        progress("Enregistrement des nouveaux Id Access dans MySQL…")
                    new_legacy = _persist_new_legacy_ids(mcur, assignments)
                    mconn.commit()

    result = AccessExportResult(
        sme_mois=ym,
        output_path=out_path,
        counts=counts,
        warnings=warnings,
        new_legacy_ids=new_legacy,
    )

    append_audit_event(
        action="mysql_bundle_export_access",
        sme_mois=ym,
        output_path=str(out_path),
        mysql_database=data_settings.mysql_database,
        counts=counts,
        warnings=warnings,
        new_legacy_ids=new_legacy,
    )
    _log.info("Export Access bundle %s → %s", ym, out_path)
    return result
