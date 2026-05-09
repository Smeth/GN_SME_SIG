"""Import Access → SQLite : référentiels, TabSme, tables satellites (+ liste via import dédié)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection
from sme_gend.repositories.sme_access_repository import AccessSmeRepository
from sme_gend.repositories.sqlite_common import apply_sqlite_schema
from sme_gend.services.audit_log import append_audit_event


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def copy_referentials_and_satellites(
    access_settings: Settings,
    sqlite_path: Path,
) -> dict[str, Any]:
    """
    Remplace le contenu des tables référentielles et satellites dans ``sqlite_path``.
    Ne modifie pas ``liste_generale``.
    Retourne ``{"counts": {table: n}, "warnings": [str, ...]}``.
    """
    apply_sqlite_schema(sqlite_path)
    counts: dict[str, int] = {}
    warnings: list[str] = []

    ac = AccessConnection(access_settings)

    def pull(query: str) -> tuple[list[tuple[Any, ...]], str | None]:
        try:
            with ac.connect() as conn:
                cur = conn.cursor()
                cur.execute(query)
                return list(cur.fetchall()), None
        except Exception as e:
            return [], str(e)

    with sqlite3.connect(str(sqlite_path)) as sq:
        scur = sq.cursor()

        # Principe : DELETE seulement si la lecture Access a réussi — données SQLite
        # existantes sont préservées en cas d'indisponibilité Access.

        rows, err = pull("SELECT ID, Nature, LieuMission, Refnds, DateDepart FROM TabMission")
        if err:
            warnings.append(f"TabMission: {err}")
        else:
            scur.execute("DELETE FROM tab_mission")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_mission (id, nature, lieu_mission, refnds, date_depart) "
                    "VALUES (?,?,?,?,?)",
                    (_norm_id(r[0]), _str_or_empty(r[1]), _str_or_empty(r[2]), _str_or_empty(r[3]), _str_or_empty(r[4])),
                )
            counts["tab_mission"] = len(rows)

        rows, err = pull("SELECT ID, DateConstat, Pathologie, Obs FROM TabMalade")
        if err:
            warnings.append(f"TabMalade: {err}")
        else:
            scur.execute("DELETE FROM tab_malade")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_malade (id, date_constat, pathologie, obs) VALUES (?,?,?,?)",
                    (_norm_id(r[0]), _str_or_empty(r[1]), _str_or_empty(r[2]), _str_or_empty(r[3])),
                )
            counts["tab_malade"] = len(rows)

        rows, err = pull("SELECT ID, DatDesertion FROM TabDeserteur")
        if err:
            warnings.append(f"TabDeserteur: {err}")
        else:
            scur.execute("DELETE FROM tab_deserteur")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_deserteur (id, dat_desertion) VALUES (?,?)",
                    (_norm_id(r[0]), _str_or_empty(r[1])),
                )
            counts["tab_deserteur"] = len(rows)

        for acc_tbl, loc_tbl in (
            ("TabStageLocal", "tab_stage_local"),
            ("TabStageEx", "tab_stage_ex"),
        ):
            rows, err = pull(
                f"SELECT ID, Naturestage, LieuStage, DateDebutFin FROM [{acc_tbl}]"
            )
            if err:
                warnings.append(f"{acc_tbl}: {err}")
            else:
                scur.execute(f"DELETE FROM {loc_tbl}")
                for r in rows:
                    scur.execute(
                        f"INSERT INTO {loc_tbl} (id, nature_stage, lieu_stage, date_debut_fin) "
                        "VALUES (?,?,?,?)",
                        (
                            _norm_id(r[0]),
                            _str_or_empty(r[1]),
                            _str_or_empty(r[2]),
                            _str_or_empty(r[3]),
                        ),
                    )
                counts[loc_tbl] = len(rows)

        rows, err = pull("SELECT CodeD, CodeT, SectDesign, PriOutT FROM TabSectrois")
        if err:
            warnings.append(f"TabSectrois: {err}")
        else:
            scur.execute("DELETE FROM tab_sectrois")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_sectrois (code_d, code_t, sect_design, pri_out_t) "
                    "VALUES (?,?,?,?)",
                    (_norm_id(r[0]), _norm_id(r[1]), _str_or_empty(r[2]), _float_or_none(r[3])),
                )
            counts["tab_sectrois"] = len(rows)

        rows, err = pull("SELECT CodeU, CodeD, SecdDesign, PriOutD FROM TabSecdeux")
        if err:
            warnings.append(f"TabSecdeux: {err}")
        else:
            scur.execute("DELETE FROM tab_secdeux")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_secdeux (code_u, code_d, secd_design, pri_out_d) "
                    "VALUES (?,?,?,?)",
                    (_norm_id(r[0]), _norm_id(r[1]), _str_or_empty(r[2]), _float_or_none(r[3])),
                )
            counts["tab_secdeux"] = len(rows)

        rows, err = pull("SELECT CodeU, SecuDesign FROM TabSecun")
        if err:
            warnings.append(f"TabSecun: {err}")
        else:
            scur.execute("DELETE FROM tab_secun")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_secun (code_u, secu_design) VALUES (?,?)",
                    (_norm_id(r[0]), _str_or_empty(r[1])),
                )
            counts["tab_secun"] = len(rows)

        try:
            sme_rows = AccessSmeRepository(access_settings).fetch_all()
            scur.execute("DELETE FROM tab_sme")
            for r in sme_rows:
                scur.execute(
                    "INSERT INTO tab_sme (codsme, mois, statut) VALUES (?,?,?)",
                    (r.codsme, r.mois, r.statut),
                )
            counts["tab_sme"] = len(sme_rows)
        except Exception as e:
            warnings.append(f"TabSme: {e}")

        rows, err = pull("SELECT CodeG, Grade, PrefOrd, EffectifG FROM TabGrade")
        if err:
            warnings.append(f"TabGrade: {err}")
        else:
            scur.execute("DELETE FROM tab_grade")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_grade (code_g, grade, pref_ord, effectif_g) VALUES (?,?,?,?)",
                    (
                        _norm_id(r[0]),
                        _str_or_empty(r[1]),
                        _int_or_none(r[2]),
                        _int_or_none(r[3]),
                    ),
                )
            counts["tab_grade"] = len(rows)

        rows, err = pull("SELECT code, designation, OderTri FROM TabStructure")
        if err:
            warnings.append(f"TabStructure: {err}")
        else:
            scur.execute("DELETE FROM tab_structure")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_structure (code, designation, ordre_tri) VALUES (?,?,?)",
                    (_norm_id(r[0]), _str_or_empty(r[1]), _int_or_none(r[2])),
                )
            counts["tab_structure"] = len(rows)

        rows, err = pull("SELECT ID, Grade, nom, Objet, Cdsme, Observ FROM TabGainPerte")
        if err:
            warnings.append(f"TabGainPerte: {err}")
        else:
            scur.execute("DELETE FROM tab_gain_perte")
            for r in rows:
                scur.execute(
                    "INSERT INTO tab_gain_perte (id, grade, nom, objet, cdsme, observ) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        _norm_id(r[0]),
                        _str_or_empty(r[1]),
                        _str_or_empty(r[2]),
                        _str_or_empty(r[3]),
                        _str_or_empty(r[4]),
                        _str_or_empty(r[5]),
                    ),
                )
            counts["tab_gain_perte"] = len(rows)

        sq.commit()

    return {"counts": counts, "warnings": warnings}


def _str_or_empty(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def import_full_sqlite_bundle_from_access(
    access_settings: Settings,
    sqlite_path: Path | None = None,
    *,
    progress: Callable[[str], None] | None = None,
    mois: str | None = None,
) -> dict[str, Any]:
    """
    Référentiels + satellites + liste générale (upsert).
    ``access_settings.use_sqlite_liste_generale`` doit être False pour la lecture Access.
    Si ``mois`` n'est pas fourni, il est lu depuis le contexte SME (mois_actif).
    """
    from sme_gend.services.liste_generale_import import import_liste_generale_access_to_sqlite

    # Récupère le mois depuis le contexte si non fourni explicitement
    if mois is None:
        try:
            from sme_gend.config.sme_context import load_sme_context as _lsc
            mois = (_lsc().mois_actif or "").strip() or None
        except Exception:
            pass

    dest = sqlite_path or access_settings.sqlite_liste_path
    if dest is None:
        raise ValueError("Chemin SQLite non défini.")
    dest_resolved = Path(dest).expanduser().resolve()

    if progress:
        progress("Référentiels et satellites…")
    ref = copy_referentials_and_satellites(access_settings, dest_resolved)

    if progress:
        progress("Liste générale…")
    ins, upd = import_liste_generale_access_to_sqlite(
        access_settings, dest_resolved, record_audit=False, mois=mois
    )

    append_audit_event(
        action="sqlite_bundle_import_access",
        access_database=str(access_settings.database_path.resolve()),
        sqlite_path=str(dest_resolved),
        liste_inserted=ins,
        liste_updated=upd,
        ref_counts=ref.get("counts"),
        ref_warnings=ref.get("warnings"),
    )

    return {
        "sqlite_path": str(dest_resolved),
        "access_database": str(access_settings.database_path.resolve()),
        "referentials": ref,
        "liste_inserted": ins,
        "liste_updated": upd,
    }
