"""Contrôles automatiques avant clôture mensuelle SME."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.repositories.gain_perte_repository import GainPerteRepository
from sme_gend.padmin_present import padmin_counts_as_present
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.app_health_service import check_mysql_connectivity, find_latest_backup_zip
from sme_gend.services.sme_editing_policy import evaluate_editing_state, is_statut_ferme
from sme_gend.services.sme_month_closure import (
    canonical_sme_mois_yyyy_mm,
    extract_yyyy_mm,
    find_archive_for_month,
    mois_labels_equivalent,
    _validated_closure_months,
)
from sme_gend.services.sme_workspace_service import SmeWorkspaceService
from sme_gend.services.workflow_task_service import WorkflowTaskService


@dataclass(frozen=True, slots=True)
class PreClosureCheck:
    check_id: str
    level: str  # ok | warn | block
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class PreClosureReport:
    mois_cloture: str
    mois_suivant: str
    checks: tuple[PreClosureCheck, ...]

    @property
    def blocking(self) -> tuple[PreClosureCheck, ...]:
        return tuple(c for c in self.checks if c.level == "block")

    @property
    def can_proceed(self) -> bool:
        return not self.blocking


def run_pre_closure_checks(
    settings: Settings,
    mois_cloture: str,
    mois_suivant: str,
) -> PreClosureReport:
    ym_c = extract_yyyy_mm(mois_cloture) or mois_cloture.strip()
    ym_s = extract_yyyy_mm(mois_suivant) or mois_suivant.strip()
    checks: list[PreClosureCheck] = []
    try:
        if not mois_suivant.strip():
            raise ValueError("Indiquez le mois à activer après la clôture.")
        ym_c, ym_s = _validated_closure_months(mois_cloture, mois_suivant)
    except ValueError as exc:
        return PreClosureReport(ym_c, ym_s, (PreClosureCheck("format", "block", "Périodes invalides", str(exc)),))
    scoped_settings = replace(settings, mysql_view_mois=ym_c)
    ctx = load_sme_context()
    mois_actif = (ctx.mois_actif or "").strip()
    ws = SmeWorkspaceService(settings)

    if not ym_c or not ym_s:
        checks.append(
            PreClosureCheck(
                "format",
                "block",
                "Mois invalides",
                "Indiquez des mois au format YYYY-MM.",
            )
        )
        return PreClosureReport(ym_c, ym_s, tuple(checks))

    if ym_c == ym_s:
        checks.append(
            PreClosureCheck(
                "same_month",
                "block",
                "Mois identiques",
                "Le mois clôturé et le mois suivant doivent être différents.",
            )
        )

    active_db = ws.get_active_mois()
    if active_db and canonical_sme_mois_yyyy_mm(active_db) != ym_c:
        checks.append(
            PreClosureCheck(
                "active_mismatch",
                "warn",
                "Mois actif différent",
                f"Mois actif en base : « {active_db} » — vous clôturez « {ym_c} ».",
            )
        )

    # view_mois=ym_c : évalue le verrouillage du mois réellement clôturé, pas du
    # mois actif de la session locale (sme_context.json) — qui peut différer si
    # l'opérateur clôture un mois autre que celui affiché par défaut.
    edit_st = evaluate_editing_state(settings, view_mois=ym_c)
    if edit_st.locked and mois_labels_equivalent(edit_st.mois_actif, ym_c):
        checks.append(
            PreClosureCheck(
                "locked",
                "block",
                "Période déjà figée",
                edit_st.detail or "sme_periode indique une période fermée.",
            )
        )

    repo = SmeRepository(settings)
    tabsme_ok = False
    if repo.table_exists():
        for r in repo.fetch_all():
            if mois_labels_equivalent(r.mois, ym_c):
                tabsme_ok = True
                if is_statut_ferme(r.statut or ""):
                    checks.append(
                        PreClosureCheck(
                            "already_closed",
                            "warn",
                            "Déjà fermé dans sme_periode",
                            f"Statut actuel : « {r.statut} ».",
                        )
                    )
                break
    if not tabsme_ok:
        checks.append(
            PreClosureCheck(
                "tabsme_missing",
                "warn",
                "Ligne sme_periode absente",
                f"Aucune ligne sme_periode pour « {ym_c} » (mise à jour limitée à la clôture).",
            )
        )

    try:
        liste = ListeGeneraleRepository(scoped_settings)
        rows = liste.fetch_all()
        if not rows:
            checks.append(
                PreClosureCheck(
                    "empty_liste",
                    "warn",
                    "Liste générale vide",
                    "Aucun agent en base pour le mois courant.",
                )
            )
        elif not any(
            padmin_counts_as_present(r.position_admin, "present", settings=settings)
            for r in rows
        ):
            checks.append(
                PreClosureCheck(
                    "no_present",
                    "warn",
                    "Aucun présent détecté",
                    "Vérifiez les positions administratives (ref_position_admin).",
                )
            )
    except Exception as e:
        checks.append(
            PreClosureCheck(
                "liste_error",
                "block",
                "Liste générale inaccessible",
                str(e),
            )
        )

    try:
        gp = GainPerteRepository(settings).fetch_for_month(ym_c)
        if not gp:
            checks.append(
                PreClosureCheck(
                    "gain_perte_empty",
                    "warn",
                    "Gains & pertes vides",
                    f"Aucune ligne pour le mois « {ym_c} ».",
                )
            )
        try:
            from sme_gend.services.gain_perte_coherence import load_unsignaled_pertes

            missing = load_unsignaled_pertes(scoped_settings, ym_c, gp)
        except Exception as exc:
            missing = []
            checks.append(PreClosureCheck("perte_coherence_error", "block",
                "Contrôle des sorties indisponible", str(exc)))
        if missing:
            names = ", ".join((r.nom or r.id or "?").strip() for r in missing[:8])
            extra = f" (+{len(missing) - 8})" if len(missing) > 8 else ""
            checks.append(
                PreClosureCheck(
                    "perte_non_signalee",
                    "warn",
                    "Sorties sans perte",
                    f"{len(missing)} agent(s) présents le mois précédent et absents "
                    f"de la liste générale, sans perte enregistrée : {names}{extra}. "
                    "Ils doivent figurer en Gains & Pertes avec le motif « Non signalé », "
                    "ou avec le motif réel (décès, retraite, erreur…).",
                )
            )
    except Exception as exc:
        checks.append(PreClosureCheck("gain_perte_error", "block",
            "Mouvements inaccessibles", str(exc)))

    mysql_ok, mysql_msg = check_mysql_connectivity(settings)
    if not mysql_ok:
        checks.append(
            PreClosureCheck(
                "mysql",
                "block",
                "MySQL inaccessible",
                f"{mysql_msg}\n\n"
                "La clôture met à jour sme_periode en base : vérifiez Administration → Paramètres "
                "(hôte, port, base, identifiants) et que le service MySQL est démarré.",
            )
        )

    backup = find_latest_backup_zip()
    if backup is None:
        checks.append(
            PreClosureCheck(
                "backup",
                "warn",
                "Aucune sauvegarde ZIP détectée",
                "Créez une sauvegarde (Administration → Sauvegarde → Sauvegarde rapide).",
            )
        )
    else:
        age_days = (Path(backup).stat().st_mtime)
        import time

        days = (time.time() - age_days) / 86400
        if days > 14:
            checks.append(
                PreClosureCheck(
                    "backup_old",
                    "warn",
                    "Sauvegarde ancienne",
                    f"Dernière archive : {backup.name} ({int(days)} j).",
                )
            )

    if find_archive_for_month(ym_c):
        checks.append(
            PreClosureCheck(
                "archive_exists",
                "warn",
                "Archive déjà présente",
                f"Un fichier sme_bundle existe déjà pour « {ym_c} ».",
            )
        )

    if mois_actif and canonical_sme_mois_yyyy_mm(mois_actif) != ym_c:
        checks.append(
            PreClosureCheck(
                "ctx_active",
                "warn",
                "Mois actif UI",
                f"Mois actif affiché : « {mois_actif} » (clôture : « {ym_c} »).",
            )
        )

    if not checks:
        checks.append(
            PreClosureCheck(
                "ok",
                "ok",
                "Contrôles passés",
                "Aucun blocage détecté.",
            )
        )

    return PreClosureReport(ym_c, ym_s, tuple(checks))


def finalize_pre_closure_report(settings: Settings, report: PreClosureReport) -> None:
    """Crée les tâches workflow à partir du rapport (après affichage UI)."""
    WorkflowTaskService(settings).sync_from_pre_closure(report)
