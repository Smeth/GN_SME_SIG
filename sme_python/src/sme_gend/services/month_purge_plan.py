"""Plan partagé par l'aperçu, le réimport et le vidage mensuel.

Les situations mensuelles servent de preuves : un événement durable encore
attesté ailleurs est conservé et sa provenance réattribuée avant suppression.
"""
from dataclasses import dataclass
from datetime import timedelta
from sme_gend.services.dossier_grouping import normalized_label_sql


@dataclass(frozen=True)
class PurgeItem:
    table: str
    predicate: str
    params: tuple
    support: str | None = None


def legacy_diploma_date_matches_sql(text, confirmed):
    """Compare text to a known DATE without casting legacy date/place strings.

    This predicate is deliberately conservative for two-digit years: a possible
    historical attestation is retained, without assigning a century to its text.
    Identifiers are internal constants; all external values remain parameters.
    """
    day = f"CONCAT('(',DAY({confirmed}),'|',LPAD(DAY({confirmed}),2,'0'),')')"
    month = f"CONCAT('(',MONTH({confirmed}),'|',LPAD(MONTH({confirmed}),2,'0'),')')"
    return (f"REGEXP_LIKE(COALESCE({text},''),CONCAT('(^|[^0-9])(',YEAR({confirmed}),"
            f"'[-/.]',{month},'[-/.]',{day},'|',{day},'[-/.]',{month},'[-/.](',"
            f"YEAR({confirmed}),'|',DATE_FORMAT({confirmed},'%%y'),'))([^0-9]|$)'))")


def month_purge_plan(pid, ym, *, imported_only=False, target_period_ids=None):
    from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
    start, end = first_day_of_month(ym), last_day_of_month(ym) + timedelta(days=1)
    origin = f"Import archive {ym}".replace('-', '')
    lot = "d.import_lot_id IN (SELECT id FROM import_lot WHERE sme_periode_id = %s)"
    imported = "(d.import_lot_id IS NOT NULL OR COALESCE(d.obs, '') LIKE 'Import archive %%')"
    plans = []
    for table, col, field in (
        ("agent_diplome", "date_obtention", "diplome_libelle"),
        ("agent_distinction", "date_attribution", None),
        ("agent_passation_grade", "date_passation", "grade_libelle"),
        ("agent_affectation", "date_affectation", "structure_code"),
    ):
        source = "source" if table == "agent_distinction" else "obs"
        has_lot = table != "agent_passation_grade"
        parts, params = [], []
        if has_lot:
            parts.append(lot); params.append(pid)
        parts.append(f"REPLACE(SUBSTRING_INDEX(COALESCE(d.{source},''),CHAR(10),1),'-','') = %s"); params.append(origin)
        if table == "agent_affectation":
            parts.append("TRIM(REPLACE(d.sme_periode_mois, '-', '')) = %s"); params.append(ym.replace('-', ''))
        if not imported_only:
            guard = "d.import_lot_id IS NULL AND " if has_lot else ""
            if table == "agent_affectation":
                guard += "d.sme_periode_mois IS NULL AND "
            parts.append(f"({guard}COALESCE(d.{source}, '') NOT LIKE 'Import archive %%' AND d.{col} >= %s AND d.{col} < %s)")
            params.extend((start, end))
        predicate = "(" + " OR ".join(parts) + ")"
        if imported_only and table == "agent_affectation":
            predicate += " AND " + imported
        support = None
        if field:
            own = "libelle_libre" if table == "agent_diplome" else field
            match = f"{normalized_label_sql(f'c.{field}')} = {normalized_label_sql(f'd.{own}')}"
            if table == "agent_diplome":
                match += (" AND (d.date_obtention IS NULL OR c.date_obtention IS NULL OR "
                          + legacy_diploma_date_matches_sql('c.date_obtention', 'd.date_obtention') + ")")
            elif table == "agent_passation_grade":
                match += " AND (d.date_passation IS NULL OR c.date_grade IS NULL OR DATE(c.date_grade) = d.date_passation)"
            else:
                # Ne pas confondre un retour dans une unité avec une affectation antérieure.
                match += " AND p.annee_mois >= DATE_FORMAT(d.date_affectation, '%%Y-%%m')"
                match += (" AND NOT EXISTS (SELECT 1 FROM carriere_situation gap "
                          "JOIN sme_periode gp ON gp.id=gap.sme_periode_id "
                          "WHERE gap.agent_id=d.agent_id "
                          "AND gp.annee_mois >= DATE_FORMAT(d.date_affectation, '%%Y-%%m') "
                          "AND gp.annee_mois < p.annee_mois AND COALESCE(gap.structure_code, '') <> '' "
                          f"AND {normalized_label_sql('gap.structure_code')} <> {normalized_label_sql('d.structure_code')})")
            support = f"SELECT MIN(p.annee_mois) FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=d.agent_id AND c.sme_periode_id <> %s AND {match}"
            if target_period_ids:
                ids = ','.join(str(int(value)) for value in target_period_ids)
                support += f" AND c.sme_periode_id NOT IN ({ids})"
            auto = imported if has_lot else "COALESCE(d.obs, '') LIKE 'Import archive %%'"
            predicate += f" AND NOT ({auto} AND ({support}) IS NOT NULL)"
            params.append(pid)
        plans.append(PurgeItem(table, predicate, tuple(params), support))
    for table in ("agent_mission", "agent_malade", "agent_desertion", "parcours_stage", "mouvement_effectif", "agent_conge"):
        pred = "d.sme_periode_id = %s"
        if imported_only:
            if table == "agent_conge":
                pred += " AND d.source_code IN ('import_access', 'import_padmin')"
            elif table == "mouvement_effectif":
                pred += " AND d.import_lot_id IS NOT NULL"
            else:
                pred += " AND COALESCE(d.source_ligne_legacy, '') NOT LIKE '%%:saisie:%%' AND (d.import_lot_id IS NOT NULL OR d.source_ligne_legacy IS NOT NULL)"
        plans.append(PurgeItem(table, pred, (pid,)))
    # Les situations saisies manuellement résistent au retrait des seuls imports.
    plans.extend((PurgeItem("agent_position_event", "TRIM(REPLACE(d.mois_sme, '-', '')) = %s" + (" AND 1=0" if imported_only else ""), (ym.replace('-', ''),)),
                  PurgeItem("carriere_situation", "d.sme_periode_id = %s" + (
                      " AND (d.import_lot_id IS NOT NULL OR COALESCE(d.obs, '') LIKE 'Import archive %%')"
                      if imported_only else ""), (pid,))))
    return plans


def preserve_supported_events(cur, plan, pid, ym):
    """Détache des lots supprimés les événements attestés par un autre mois."""
    for item in plan:
        if not item.support:
            continue
        table = item.table
        has_lot = table != "agent_passation_grade"
        origin = "REPLACE(SUBSTRING_INDEX(COALESCE(d.obs,''),CHAR(10),1),'-','') = %s"
        args = [f"Import archive {ym}".replace('-', '')]
        if has_lot:
            origin += " OR d.import_lot_id IN (SELECT id FROM import_lot WHERE sme_periode_id = %s)"
            args.append(pid)
        if table == "agent_affectation":
            origin += " OR (TRIM(REPLACE(d.sme_periode_mois, '-', '')) = %s AND COALESCE(d.obs, '') LIKE 'Import archive %%')"
            args.append(ym.replace('-', ''))
        assignments = (f"d.obs = CONCAT('Import archive ', ({item.support}), "
                       "CASE WHEN LOCATE(CHAR(10),COALESCE(d.obs,''))>0 "
                       "THEN SUBSTRING(d.obs,LOCATE(CHAR(10),d.obs)) ELSE '' END)")
        params = [pid]
        if has_lot:
            assignments += ", d.import_lot_id = NULL"
        if table == "agent_affectation":
            assignments += f", d.sme_periode_mois = ({item.support})"
            params.append(pid)
        cur.execute(f"UPDATE {table} d SET {assignments} WHERE ({origin}) AND ({item.support}) IS NOT NULL", tuple(params + args + [pid]))
