"""Sonde de schéma Access : détecte tables/colonnes absentes avant import.

Ne bloque jamais l'import — les colonnes en trop dans le ``.mdb`` (ajoutées
manuellement) sont de toute façon ignorées sans risque par les requêtes
``SELECT`` nommées du pipeline ; seules les colonnes/tables *manquantes*
méritent un avertissement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.repositories.access_connection import AccessConnection

# Tables Access lues par le pipeline d'import et colonnes qu'il attend.
# TabConge est volontairement absente : gérée comme optionnelle ailleurs
# (agent_conge_service._access_table_exists).
EXPECTED_ACCESS_SCHEMA: dict[str, tuple[str, ...]] = {
    "ListeGenerale": (
        "Id", "noms", "DateN", "LieuN", "Grade", "DateGrade", "DateService",
        "Ndecision", "Matmilitaire", "Matsolde", "Padmin", "Fonction", "SubArme",
        "Diplome", "DateObtention", "Structure", "Obs", "OderTri", "presseance",
    ),
    "TabStructure": ("code", "designation", "OderTri"),
    "TabGrade": ("CodeG", "Grade", "PrefOrd", "EffectifG"),
    "TabSecun": ("CodeU", "SecuDesign"),
    "TabSecdeux": ("CodeU", "CodeD", "SecdDesign", "PriOutD"),
    "TabSectrois": ("CodeD", "CodeT", "SectDesign", "PriOutT"),
    "TabSme": ("Codsme", "mois", "Statut"),
    "TabMission": ("ID", "Nature", "LieuMission", "Refnds", "DateDepart"),
    "TabMalade": ("ID", "DateConstat", "Pathologie", "Obs"),
    "TabDeserteur": ("ID", "DatDesertion"),
    "TabGainPerte": ("ID", "Grade", "nom", "Objet", "Cdsme", "Observ"),
}


def probe_access_schema(access_conn: "AccessConnection") -> list[str]:
    """Retourne les avertissements « table absente » / « colonne manquante »."""
    try:
        conn = access_conn.connect()
    except Exception as e:
        return [f"Sonde schéma Access impossible : {e}"]
    warnings: list[str] = []
    try:
        cur = conn.cursor()
        for table, expected_cols in EXPECTED_ACCESS_SCHEMA.items():
            try:
                cur.execute(f"SELECT TOP 1 * FROM [{table}]")
                actual_cols = {str(d[0]).casefold() for d in (cur.description or [])}
            except Exception:
                warnings.append(f"Table Access absente ou illisible : {table}")
                continue
            missing = [c for c in expected_cols if c.casefold() not in actual_cols]
            if missing:
                warnings.append(
                    f"{table} : colonne(s) attendue(s) manquante(s) — {', '.join(missing)}"
                )
    finally:
        conn.close()
    return warnings
