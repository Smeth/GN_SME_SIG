"""Helpers Edition SME : alertes, écarts dossier, import CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.mysql_connection import MySQLConnection


# Colonnes optionnelles affichables (clé → en-tête).
OPTIONAL_COLUMN_DEFS: list[tuple[str, str, str]] = [
    ("date_naissance", "Date naiss.", "readonly"),
    ("lieu_naissance", "Lieu naiss.", "readonly"),
    ("date_grade", "Date grade", "readonly"),
    ("date_service", "Entrée service", "readonly"),
    ("ndecision", "N° décision", "readonly"),
    ("diplome", "Diplôme", "readonly"),
    ("date_obtention", "Date diplôme", "readonly"),
    ("telephone", "Téléphone", "text"),
    ("adresse_residence", "Adresse résidence", "text"),
    ("obs", "Observations", "text"),
    ("ordre_tri", "Ordre tri", "readonly"),
    ("presseance", "Préséance", "readonly"),
]

OPTIONAL_COLUMN_KEYS = {c[0] for c in OPTIONAL_COLUMN_DEFS}

# Import CSV : en-têtes reconnus → champ ListeGeneraleRow
CSV_FIELD_ALIASES: dict[str, str] = {
    "n°": "id",
    "id": "id",
    "nom et prénom": "noms",
    "noms": "noms",
    "nom": "noms",
    "grade": "grade",
    "structure": "structure",
    "subdivision": "subdivision_arme",
    "subdivision d'arme": "subdivision_arme",
    "subdivision_arme": "subdivision_arme",
    "position admin.": "position_admin",
    "position admin": "position_admin",
    "position_admin": "position_admin",
    "fonction": "fonction",
    "matricule mil.": "matricule_militaire",
    "matricule militaire": "matricule_militaire",
    "matricule_militaire": "matricule_militaire",
    "matricule solde": "matricule_solde",
    "matricule_solde": "matricule_solde",
    "observations": "obs",
    "obs": "obs",
    "diplôme": "diplome",
    "diplome": "diplome",
    "téléphone": "telephone",
    "telephone": "telephone",
    "tel": "telephone",
    "adresse": "adresse_residence",
    "adresse résidence": "adresse_residence",
    "adresse de residence": "adresse_residence",
    "adresse_residence": "adresse_residence",
}


@dataclass(slots=True)
class CsvImportResult:
    updated: int = 0
    skipped: int = 0
    errors: list[str] | None = None


class CareerAlertsScanWorker(QThread):
    finished_scan = Signal(object)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        from sme_gend.services.career_alerts_cache import scan_and_cache_career_alerts

        try:
            snap = scan_and_cache_career_alerts(self._settings)
        except Exception:
            from sme_gend.services.career_alerts_cache import CareerAlertsSnapshot

            snap = CareerAlertsSnapshot(ready=True)
        if not self.isInterruptionRequested():
            self.finished_scan.emit(snap)


def normalize_label_sql(expr: str) -> str:
    """Expression MySQL 8 rendant deux libellés comparables « métier ».

    La collation ``utf8mb4_unicode_ci`` est déjà insensible à la casse et aux
    accents : ce qui subsistait comme faux écart entre un libellé importé
    d'Access et une saisie manuelle, ce sont les espaces insécables et les
    espaces multiples internes.
    """
    no_nbsp = f"REPLACE(COALESCE({expr}, ''), CHAR(0xA0 USING utf8mb4), ' ')"
    return f"TRIM(REGEXP_REPLACE({no_nbsp}, '[[:space:]]+', ' '))"


# Règle de sélection identique à
# MySQLAgentDossierRepository.sync_carriere_diplome_from_dossier : le diplôme
# MILITAIRE prime, puis le plus récent (à défaut, tout diplôme du dossier).
DOSSIER_DIPLOME_RANK_SQL = (
    "PARTITION BY agent_id "
    "ORDER BY (categorie = 'MILITAIRE') DESC, date_obtention DESC, id DESC"
)

# Règle identique à sync_carriere_grade_from_passations : dernière passation.
DOSSIER_PASSATION_RANK_SQL = (
    "PARTITION BY agent_id "
    "ORDER BY COALESCE(date_passation, '1900-01-01') DESC, id DESC"
)


def fetch_dossier_gap_carriere_ids(settings: Settings) -> set[str]:
    """Carrières du mois consulté dont diplôme ou grade liste ≠ dossier.

    La sélection des valeurs du dossier applique la même règle que la projection
    ``sync_carriere_from_dossier`` et les libellés sont normalisés : un écart
    remonté ici est une vraie divergence de valeur, pas un artefact de
    formatage ni un effet de la priorité au diplôme militaire.
    """
    from sme_gend.config.liste_generale_scope import carriere_scope_for_settings

    scope_sql, scope_params = carriere_scope_for_settings(settings, alias="c")
    ids: set[str] = set()
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id FROM carriere_situation c
                JOIN agent a ON a.id = c.agent_id
                LEFT JOIN (
                  SELECT agent_id, libelle_libre,
                         ROW_NUMBER() OVER ({DOSSIER_DIPLOME_RANK_SQL}) rn
                  FROM agent_diplome
                ) d ON d.agent_id = a.id AND d.rn = 1
                WHERE {scope_sql}
                  AND {normalize_label_sql('c.diplome_libelle')} <>
                      {normalize_label_sql('d.libelle_libre')}
                """,
                scope_params,
            )
            ids.update(str(r[0]) for r in cur.fetchall())
            cur.execute(
                f"""
                SELECT c.id FROM carriere_situation c
                JOIN agent a ON a.id = c.agent_id
                LEFT JOIN (
                  SELECT agent_id, grade_libelle,
                         ROW_NUMBER() OVER ({DOSSIER_PASSATION_RANK_SQL}) rn
                  FROM agent_passation_grade
                ) pg ON pg.agent_id = a.id AND pg.rn = 1
                WHERE {scope_sql}
                  AND {normalize_label_sql('c.grade_libelle')} <>
                      {normalize_label_sql('pg.grade_libelle')}
                """,
                scope_params,
            )
            ids.update(str(r[0]) for r in cur.fetchall())
    return ids


def _norm_header(h: str) -> str:
    return (h or "").strip().casefold()


def _parse_csv_row(headers: list[str], values: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for h, v in zip(headers, values):
        field = CSV_FIELD_ALIASES.get(_norm_header(h))
        if not field:
            continue
        t = (v or "").strip()
        out[field] = t or None
    return out


def import_csv_updates(
    personnel_service: Any,
    rows_by_id: dict[str, ListeGeneraleRow],
    path: Path,
    *,
    match_field: str = "id",
) -> CsvImportResult:
    """
    Met à jour les fiches existantes depuis un CSV (;).
    La colonne de correspondance doit être ``id`` ou ``matricule_militaire``.
    """
    from dataclasses import replace

    result = CsvImportResult(errors=[])
    if match_field not in ("id", "matricule_militaire"):
        result.errors.append(f"Colonne de correspondance invalide : {match_field}")
        return result

    # Matricules en doublon : plusieurs fiches partageraient la même clé et
    # l'une écraserait silencieusement l'autre dans `index`, si bien que
    # l'import ne mettrait à jour que l'une des deux personnes sans avertir
    # pour l'autre. On les marque "ambiguës" pour refuser le rapprochement
    # plutôt que de choisir arbitrairement.
    index: dict[str, ListeGeneraleRow] = {}
    ambiguous_keys: set[str] = set()
    for r in rows_by_id.values():
        key = (r.id if match_field == "id" else r.matricule_militaire) or ""
        key = str(key).strip()
        if not key:
            continue
        existing = index.get(key)
        if existing is not None and existing.id != r.id:
            ambiguous_keys.add(key)
        index[key] = r

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        try:
            headers = next(reader)
        except StopIteration:
            result.errors.append("Fichier CSV vide.")
            return result
        for line_no, values in enumerate(reader, start=2):
            if not any((c or "").strip() for c in values):
                continue
            parsed = _parse_csv_row(headers, values)
            key = (parsed.get(match_field) or "").strip()
            if not key:
                result.skipped += 1
                continue
            if key in ambiguous_keys:
                result.errors.append(
                    f"Ligne {line_no} ({key}) : {match_field} partagé par plusieurs fiches — "
                    "correspondance ambiguë, ligne ignorée."
                )
                result.skipped += 1
                continue
            before = index.get(key)
            if before is None:
                result.skipped += 1
                continue
            changes = {k: v for k, v in parsed.items() if k != match_field and k != "id"}
            if not changes:
                result.skipped += 1
                continue
            after = replace(before, **changes)
            try:
                personnel_service.update_personnel(before, after)
                index[key] = after
                rows_by_id[before.id] = after
                result.updated += 1
            except Exception as exc:
                result.errors.append(f"Ligne {line_no} ({key}): {exc}")
    return result
