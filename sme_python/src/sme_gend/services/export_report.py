"""Messages après export MySQL → Access."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def format_export_result_html(result: dict[str, Any]) -> str:
    """Texte HTML pour QMessageBox après ``export_mysql_bundle_to_access``."""
    mois = html.escape(str(result.get("sme_mois") or ""))
    out = html.escape(str(result.get("output_path") or ""))
    counts: dict[str, int] = {}
    raw_counts = result.get("counts")
    if isinstance(raw_counts, dict):
        counts = {str(k): int(v or 0) for k, v in raw_counts.items()}

    lg = counts.get("ListeGenerale", 0)
    sme = counts.get("TabSme", 0)
    gp = counts.get("TabGainPerte", 0)
    mission = counts.get("TabMission", 0)
    malade = counts.get("TabMalade", 0)
    stage_loc = counts.get("TabStageLocal", 0)
    stage_ex = counts.get("TabStageEx", 0)
    stage_interne = counts.get("TabStageInterne", 0)
    conge = counts.get("TabConge", 0)
    new_ids = int(result.get("new_legacy_ids") or 0)

    msg = (
        f"<b>Export Access terminé</b> — mois <b>{mois}</b>.<br>"
        f"Fichier : <code>{out}</code><br><br>"
        f"<b>ListeGenerale</b> : {lg} ligne(s)<br>"
        f"<b>TabSme</b> : {sme} · <b>TabGainPerte</b> : {gp}<br>"
        f"Satellites : missions {mission}, malades {malade}, "
        f"stages loc. {stage_loc}, stages ext. {stage_ex}, stages internes {stage_interne}"
    )
    if conge:
        msg += f", congés {conge}"
    msg += "."

    if new_ids:
        msg += (
            f"<br><br><b>{new_ids}</b> nouvel(aux) Id Access enregistré(s) dans MySQL "
            f"(legacy_access_id) pour les prochains exports."
        )

    warnings = result.get("warnings") or []
    if warnings:
        msg += "<br><br><span style='color:#e65100;'><b>Avertissements</b> :<ul>"
        for w in warnings:
            msg += f"<li>{html.escape(str(w))}</li>"
        msg += "</ul></span>"

    msg += (
        "<br><br>Ouvrez ce fichier dans l'<b>ancien SME Access</b> pour impressions "
        "et exports legacy. Ne modifiez pas ce .mdb en parallèle de MySQL."
    )
    return msg


def default_export_directory_hint() -> str:
    from sme_gend.services.sme_month_closure import sme_archives_dir

    return str(sme_archives_dir().resolve())
