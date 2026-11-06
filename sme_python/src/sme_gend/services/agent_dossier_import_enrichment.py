"""Enrichissement automatique du dossier agent lors des imports Access."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.models.agent_dossier import DIPLOME_DEFAULT
from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.import_diploma_date import interpret_diploma_date, source_observation
from sme_gend.services.carriere_snapshot_diff import diff_carriere_snapshots
from sme_gend.services.mysql_import_period import first_day_of_month


@dataclass(slots=True)
class DossierEnrichmentCounts:
    diplomes: int = 0
    passations: int = 0
    affectations: int = 0
    date_issues: int = 0
    date_warnings: list[str] = field(default_factory=list)

    def total(self) -> int:
        return self.diplomes + self.passations + self.affectations


def _norm(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


def _import_obs(mois: str) -> str:
    return f"Import archive {mois}"


def _grade_date(snap: CarriereSnapshotRow, mois: str) -> date | None:
    return parse_access_date(snap.date_grade) or first_day_of_month(mois)


def _diplome_date(snap: CarriereSnapshotRow) -> date | None:
    return interpret_diploma_date(snap.date_obtention).value


def _structure_date(mois: str) -> date:
    return first_day_of_month(mois)


def enrich_agent_from_snapshots(
    settings: Settings,
    legacy_access_id: str,
    imported_mois: str,
    *,
    import_lot_id: int | None = None,
) -> DossierEnrichmentCounts:
    """
    Compare le snapshot du mois importé au précédent et alimente diplômes,
    passations de grade et affectations (idempotent).
    """
    leg = str(legacy_access_id or "").strip()
    ym = (imported_mois or "").strip()
    counts = DossierEnrichmentCounts()
    if not leg or not ym:
        return counts

    repo = MySQLAgentDossierRepository(settings)
    agent_id = repo.agent_id_for_legacy(leg)
    if agent_id is None:
        return counts

    snaps = MySQLAgentRepository(settings).list_carriere_history_for_agent(leg)
    if not snaps:
        return counts

    idx = next((i for i, s in enumerate(snaps) if s.annee_mois == ym), None)
    if idx is None:
        return counts

    after = snaps[idx]
    before = snaps[idx - 1] if idx > 0 else None
    obs = _import_obs(ym)

    if before is None:
        counts.diplomes += _maybe_add_diplome(repo, agent_id, after, obs, import_lot_id, date_quality=counts)
        counts.passations += _maybe_add_passation(repo, agent_id, after, ym, obs)
        counts.affectations += _maybe_add_affectation(
            repo, agent_id, after, ym, obs, import_lot_id
        )
        return counts

    changes = diff_carriere_snapshots(before, after)
    if ("diplome" in changes or "date_obtention" in changes) and _norm(after.diplome):
        counts.diplomes += _maybe_add_diplome(repo, agent_id, after, obs, import_lot_id, date_quality=counts)
    if "grade" in changes and _norm(after.grade):
        counts.passations += _maybe_add_passation(repo, agent_id, after, ym, obs)
    if "structure" in changes and _norm(after.structure):
        counts.affectations += _maybe_add_affectation(
            repo, agent_id, after, ym, obs, import_lot_id
        )
    elif ("fonction" in changes or "subdivision_arme" in changes) and _norm(after.structure):
        counts.affectations += _maybe_add_affectation(
            repo, agent_id, after, ym, obs, import_lot_id
        )
    return counts


def _maybe_add_diplome(
    repo: MySQLAgentDossierRepository,
    agent_id: int,
    snap: CarriereSnapshotRow,
    obs: str,
    import_lot_id: int | None,
    *, date_quality: DossierEnrichmentCounts | None = None,
) -> int:
    lib = _norm(snap.diplome)
    if not lib:
        return 0
    parsed = interpret_diploma_date(snap.date_obtention)
    if parsed.issue and date_quality is not None:
        date_quality.date_issues += 1
        if len(date_quality.date_warnings) < 12:
            date_quality.date_warnings.append(f"Dossier agent {agent_id} : diplôme conservé ; date d'obtention à vérifier ({parsed.issue}).")
    extra = {'lieu': parsed.place} if parsed.place else {}
    pid = repo.insert_diplome_if_absent(
        agent_id,
        libelle=lib,
        date_obtention=parsed.value,
        categorie=DIPLOME_DEFAULT,
        obs=source_observation(obs, parsed),
        import_lot_id=import_lot_id,
        **extra,
    )
    return 1 if pid else 0


def _maybe_add_passation(
    repo: MySQLAgentDossierRepository,
    agent_id: int,
    snap: CarriereSnapshotRow,
    mois: str,
    obs: str,
) -> int:
    grade = _norm(snap.grade)
    if not grade:
        return 0
    pid = repo.insert_passation_if_absent(
        agent_id,
        grade_libelle=grade,
        date_passation=_grade_date(snap, mois),
        ndecision=_norm(snap.ndecision),
        obs=obs,
    )
    return 1 if pid else 0


def _maybe_add_affectation(
    repo: MySQLAgentDossierRepository,
    agent_id: int,
    snap: CarriereSnapshotRow,
    mois: str,
    obs: str,
    import_lot_id: int | None,
) -> int:
    structure = _norm(snap.structure)
    if not structure:
        return 0
    pid = repo.insert_affectation_if_absent(
        agent_id,
        structure_code=structure,
        date_affectation=_structure_date(mois),
        fonction=_norm(snap.fonction),
        subdivision_arme=_norm(snap.subdivision_arme),
        sme_periode_mois=mois,
        obs=obs,
        import_lot_id=import_lot_id,
    )
    return 1 if pid else 0


def enrich_dossier_after_import(
    settings: Settings,
    imported_mois: str,
    legacy_ids: list[str],
    *,
    import_lot_id: int | None = None,
) -> dict:
    """Enrichit le dossier de chaque agent importé pour le mois donné."""
    totals = DossierEnrichmentCounts()
    for leg in legacy_ids:
        lid = str(leg or "").strip()
        if not lid:
            continue
        c = enrich_agent_from_snapshots(
            settings,
            lid,
            imported_mois,
            import_lot_id=import_lot_id,
        )
        totals.diplomes += c.diplomes
        totals.passations += c.passations
        totals.affectations += c.affectations
        totals.date_issues += c.date_issues
        totals.date_warnings.extend(c.date_warnings[:max(0, 12 - len(totals.date_warnings))])
    return {
        "dossier_diplomes": totals.diplomes,
        "dossier_passations": totals.passations,
        "dossier_affectations": totals.affectations,
        "dossier_total": totals.total(),
        "date_issues": totals.date_issues,
        "date_warnings": totals.date_warnings,
    }


def backfill_dossier_from_all_snapshots(settings: Settings) -> dict[str, int]:
    """
    Rejoue l'enrichissement pour tous les agents et toutes les paires de mois
    consécutifs déjà en base (utile après migration).
    """
    repo = MySQLAgentRepository(settings)
    dossier_repo = MySQLAgentDossierRepository(settings)
    totals = DossierEnrichmentCounts()
    bulk = repo.load_carriere_history_bulk()

    for leg, (_noms, snaps) in bulk.items():
        agent_id = dossier_repo.agent_id_for_legacy(leg)
        if agent_id is None or not snaps:
            continue
        for idx, after in enumerate(snaps):
            ym = after.annee_mois
            if not ym:
                continue
            obs = _import_obs(ym)
            before = snaps[idx - 1] if idx > 0 else None
            if before is None:
                totals.diplomes += _maybe_add_diplome(dossier_repo, agent_id, after, obs, None)
                totals.passations += _maybe_add_passation(dossier_repo, agent_id, after, ym, obs)
                totals.affectations += _maybe_add_affectation(
                    dossier_repo, agent_id, after, ym, obs, None
                )
                continue
            changes = diff_carriere_snapshots(before, after)
            if ("diplome" in changes or "date_obtention" in changes) and _norm(after.diplome):
                totals.diplomes += _maybe_add_diplome(dossier_repo, agent_id, after, obs, None)
            if "grade" in changes and _norm(after.grade):
                totals.passations += _maybe_add_passation(dossier_repo, agent_id, after, ym, obs)
            if "structure" in changes and _norm(after.structure):
                totals.affectations += _maybe_add_affectation(
                    dossier_repo, agent_id, after, ym, obs, None
                )
            elif ("fonction" in changes or "subdivision_arme" in changes) and _norm(after.structure):
                totals.affectations += _maybe_add_affectation(
                    dossier_repo, agent_id, after, ym, obs, None
                )

    return {
        "dossier_diplomes": totals.diplomes,
        "dossier_passations": totals.passations,
        "dossier_affectations": totals.affectations,
        "dossier_total": totals.total(),
    }
