"""Messages et contrat de retour des imports Access → MySQL."""

from __future__ import annotations

import html
from typing import Any

from sme_gend.config.settings import Settings


def _carriere_reconciliation_summary(result: dict[str, Any]) -> dict[str, int | str | None] | None:
    raw = result.get("carriere_reconciliation")
    if raw is None:
        return None
    if hasattr(raw, "to_summary_dict"):
        return raw.to_summary_dict()
    if isinstance(raw, dict):
        return {
            "mois": raw.get("mois"),
            "fk_updated": int(raw.get("fk_updated") or 0),
            "ordre_tri_updated": int(raw.get("ordre_tri_updated") or 0),
            "unresolved_before": int(raw.get("unresolved_before") or 0),
            "unresolved_after": int(raw.get("unresolved_after") or 0),
            "etl_errors_logged": int(raw.get("etl_errors_logged") or 0),
        }
    return None


def format_carriere_reconciliation_html(summary: dict[str, int | str | None]) -> str:
    """Bloc HTML réconciliation FK (post-import automatique)."""
    fk = int(summary.get("fk_updated") or 0)
    ordre = int(summary.get("ordre_tri_updated") or 0)
    unresolved = int(summary.get("unresolved_after") or 0)
    etl = int(summary.get("etl_errors_logged") or 0)
    before = int(summary.get("unresolved_before") or 0)

    msg = (
        f"<br><b>Réconciliation carrière</b> (automatique) : "
        f"<b>{fk}</b> ligne(s) FK, <b>{ordre}</b> ordre(s) de tri aligné(s)."
    )
    if before and before != unresolved:
        msg += f" FK non résolues : {before} → <b>{unresolved}</b>."
    elif unresolved:
        msg += f" FK non résolues restantes : <b>{unresolved}</b>."
    else:
        msg += " Toutes les FK du mois importé sont résolues."

    if unresolved:
        msg += (
            f"<br><span style='color:#e65100;'>"
            f"<b>Attention</b> : {unresolved} référence(s) carrière sans FK "
            f"(grade, structure, padmin, fonction ou subdivision)."
        )
        if etl:
            msg += f" {etl} entrée(s) journalisées dans <code>etl_erreur</code>."
        msg += (
            " Utilisez <i>Maintenance → Réconcilier référentiels carrière</i> "
            "ou complétez les référentiels manquants."
            "</span>"
        )
    return msg


_PREVIEW_LABELS: dict[str, str] = {
    "ListeGenerale": "Personnel (ListeGenerale)",
    "TabMission": "Missions",
    "TabMalade": "Maladies",
    "TabDeserteur": "Désertions",
    "TabGainPerte": "Gains/pertes",
    "TabStageLocal": "Stages locaux",
    "TabStageEx": "Stages extérieurs",
    "TabStageInterne": "Stages internes",
    "TabConge": "Congés",
}


def format_import_preview_html(preview: dict[str, Any]) -> str:
    """Bloc HTML « aperçu » (décomptes + avertissements schéma) avant confirmation d'import."""
    counts = preview.get("counts") or {}
    warnings = list(preview.get("warnings") or [])

    parts = ["<b>Aperçu du fichier Access</b><br>"]
    rows = [
        f"{label} : <b>{counts[key]}</b>"
        for key, label in _PREVIEW_LABELS.items()
        if key in counts
    ]
    if rows:
        parts.append(" · ".join(rows) + "<br>")
    else:
        parts.append("<i>Aucun décompte disponible.</i><br>")

    if warnings:
        parts.append(
            "<br><span style='color:#e65100;'><b>Avertissements schéma</b><ul>"
        )
        for w in warnings:
            parts.append(f"<li>{html.escape(str(w))}</li>")
        parts.append("</ul></span>")

    return "".join(parts)


def format_liste_generale_import_stats_html(stats: dict[str, Any] | None) -> str:
    """Bloc décompte liste générale après import Access."""
    if not stats:
        return ""
    read = int(stats.get("rows_read") or 0)
    empty = int(stats.get("ids_empty_source") or 0)
    auto = int(stats.get("ids_auto_generated") or 0)
    ignored = int(stats.get("ids_ignored") or 0)
    merged = int(stats.get("matricule_merged") or 0)
    dup_kept = int(stats.get("matricule_duplicates_kept") or 0)
    distinct = int(stats.get("agents_distinct") or 0)
    ins = int(stats.get("liste_inserted") or stats.get("inserted") or 0)
    upd = int(stats.get("liste_updated") or stats.get("updated") or 0)
    dup_id = int(stats.get("duplicate_access_ids_resolved") or 0)

    lines = [
        "<b>Décompte liste générale</b>",
        f"Lignes lues (Access) : <b>{read}</b>",
        f"Id Access vides (auto-générés) : <b>{empty}</b>"
        + (f" → <code>GEN-…</code>" if auto else ""),
        f"Lignes ignorées (Id vide) : <b>{ignored}</b>",
        f"Fusionnées par matricule : <b>{merged}</b>"
        + (
            " <span style='color:#2e7d32;'>(désactivée — homonymes conservés)</span>"
            if merged == 0
            else ""
        ),
    ]
    if dup_kept:
        lines.append(
            f"Matricules en double conservés : <b>{dup_kept}</b> ligne(s) supplémentaire(s)"
        )
    if dup_id:
        lines.append(
            f"Id Access dupliqués dans le fichier (suffixe #n) : <b>{dup_id}</b>"
        )
    lines.append(f"Agents distincts en base (mois importé) : <b>{distinct}</b>")
    lines.append(
        f"Opérations import : <b>{ins}</b> création(s) agent, "
        f"<b>{upd}</b> mise(s) à jour carrière"
    )
    if read and distinct and read != distinct:
        delta = read - distinct
        lines.append(
            f"<span style='color:#1565c0;'>Écart lignes Access − agents distincts : "
            f"<b>{delta}</b> (Id dupliqués dans le fichier ou réimport sur agents existants).</span>"
        )
    return "<br>".join(lines) + "<br>"


def format_import_result_html(
    result: dict[str, Any],
    *,
    mois_fallback: str = "",
    include_alerts: bool = True,
    settings: Settings | None = None,
) -> str:
    """Texte HTML pour QMessageBox après ``import_full_mysql_bundle_from_access``."""
    mois = str(result.get("sme_mois") or mois_fallback or "")
    hist = " (historique)" if result.get("historical") else ""
    ins = int(result.get("liste_inserted") or 0)
    upd = int(result.get("liste_updated") or 0)
    lg_stats = result.get("liste_generale_stats")
    cp = int(result.get("conge_padmin_sync") or 0)
    de = result.get("dossier_enrichment") or {}
    dd = int(de.get("dossier_diplomes") or 0)
    dp = int(de.get("dossier_passations") or 0)
    da = int(de.get("dossier_affectations") or 0)
    ref = result.get("referentials") or {}
    warns = list(ref.get("warnings") or [])
    warns.extend(de.get("date_warnings") or [])

    msg = (
        f"Période <b>{html.escape(mois)}</b>{hist}<br>"
        f"{format_liste_generale_import_stats_html(lg_stats)}"
        f"Missions, maladies, stages, congés et gains/pertes : période <b>{html.escape(mois)}</b>.<br>"
        f"Dossiers enrichis : <b>{dd}</b> diplôme(s), <b>{dp}</b> passation(s), "
        f"<b>{da}</b> affectation(s). Congés (position admin) : <b>{cp}</b>.<br><br>"
        "Consultez la <b>fiche dossier</b> → onglet « Dossier complet »."
    )

    recon = _carriere_reconciliation_summary(result)
    if de.get('date_issues'):
        msg += (f"<br><b>{int(de['date_issues'])} date(s) de diplôme à vérifier</b> : "
                "les diplômes ont été conservés ; les valeurs Access d'origine sont dans leurs observations. "
                "L'import n'est pas bloqué par ces anomalies.<br>")
    replacement = result.get("month_replacement")
    if isinstance(replacement, dict):
        removed = sum(
            int(value) for key, value in replacement.items()
            if key not in {"sme_mois", "sme_periode_id", "skipped", "agent_orphans_removed"}
            and isinstance(value, int)
        )
        msg += (
            f"<br><b>Mois remplacé</b> : {removed} ancienne(s) ligne(s) supprimée(s) "
            "avant reconstruction des données et historiques."
        )
    if recon:
        msg += format_carriere_reconciliation_html(recon)

    if include_alerts and settings and mois:
        try:
            from sme_gend.services.career_import_alerts import scan_all_career_alerts

            alerts = scan_all_career_alerts(settings, mois_focus=mois, limit=12)
            if alerts:
                aw = sum(1 for a in alerts if a.severity == "warning")
                msg += f"<br><br><b>Alertes qualité</b> : {len(alerts)}"
                if aw:
                    msg += f" (<b>{aw}</b> avertissement(s))"
                show_n = 8
                msg += "<ul>"
                for a in alerts[:show_n]:
                    who = html.escape(a.noms or a.agent_legacy_id or "agent inconnu")
                    msg += f"<li><b>{who}</b> — {html.escape(a.message)}</li>"
                msg += "</ul>"
                if len(alerts) > show_n:
                    msg += (
                        f"<i>… et {len(alerts) - show_n} autre(s) "
                        f"(voir les alertes carrière dans l’application).</i>"
                    )
        except Exception:
            pass

    if warns:
        msg += "<br><b>Avertissements import</b><ul>"
        for w in warns:
            msg += f"<li>{html.escape(str(w))}</li>"
        msg += "</ul>"
    if result.get("historical"):
        msg += (
            "<br><br><span style='color:#1565c0;'>"
            "<b>Consultation</b> : l'application affiche le mois importé "
            f"(<b>{html.escape(mois)}</b>) dans la liste générale et les exports. "
            "Le mois de travail opérationnel (barre latérale) reste inchangé."
            "</span>"
        )
    else:
        msg += (
            "<br><br><span style='color:#1565c0;'>"
            "<b>Consultation</b> : la liste générale affiche le mois importé "
            f"(<b>{html.escape(mois)}</b>) — situation « actuelle » si c'est le mois de travail."
            "</span>"
        )
    return msg
