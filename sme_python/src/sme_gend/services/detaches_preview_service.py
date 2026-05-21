"""Aperçu HTML — module détachés (récap par sigle, détail, comparaison fichier officiel)."""

from __future__ import annotations

import html

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_excel_export import count_official_detache_lignes
from sme_gend.services.detaches_month_compare import nouveaux_detaches_vs_mois_precedent
from sme_gend.services.detaches_service import (
    DetachesService,
    designation_bloc_excel,
    detache_ecart_remarque,
    is_padmin_detache_row,
    is_structure_detache_row,
    recap_par_sigle,
    sigle_ou_subdivision,
)


def _build_sec_lookup(settings: Settings) -> dict[str, str]:
    """code (CodeU / CodeD / CodeT) → désignation dans la hiérarchie TabSec*.
    Priorité : code_t > code_d > code_u en cas de collision (peu probable)."""
    from sme_gend.repositories.subdivision_repository import SubdivisionRepository
    lookup: dict[str, str] = {}
    try:
        for r in SubdivisionRepository(settings).fetch_flat_hierarchy():
            if r.code_u and r.design_u:
                lookup.setdefault(r.code_u.strip(), r.design_u.strip())
            if r.code_d and r.design_d:
                lookup.setdefault(r.code_d.strip(), r.design_d.strip())
            if r.code_t and r.design_t:
                lookup[r.code_t.strip()] = r.design_t.strip()   # priorité niveau 3
    except Exception:
        pass
    return lookup


_CSS = """
body { margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif; font-size: 12px; background: #fff; }
h2 { font-size: 14pt; margin: 0 0 10px 0; color: #1a237e; }
h3 { font-size: 12pt; margin: 16px 0 8px 0; color: #283593; }
.meta { color: #555; margin-bottom: 12px; font-size: 11px; }
.warn { background: #fff3e0; border-left: 4px solid #ff9800; padding: 8px 12px; margin: 0 0 12px 0; }
table.grid { border-collapse: collapse; width: 100%; }
table.grid th, table.grid td { border: 1px solid #808080; padding: 4px 6px; text-align: left; }
table.grid th { background: #d9e2f3; font-weight: bold; text-align: center; }
table.grid td.num { text-align: right; }
table.grid tr:nth-child(even) td { background: #fafafa; }
"""


class DetachesPreviewService:
    """HTML — périmètre détachés (Padmin « Détaché » et/ou Structure = DETACHE), sigle = subdivision arme."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._svc = DetachesService(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._svc = DetachesService(settings)

    def build_html(self) -> str:
        rows = self._svc.fetch_detaches()
        tpl = self._settings.template_detaches_path
        n_off = count_official_detache_lignes(tpl) if tpl else None
        n_acc = len(rows)
        anomalies = [r for r in rows if is_structure_detache_row(r) and not is_padmin_detache_row(r)]
        recap = recap_par_sigle(rows)
        cfg = DetachesConfigService(self._settings).load()

        # Résolution codes → désignations TabSec*
        lookup = _build_sec_lookup(self._settings)
        has_lookup = bool(lookup)

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            _CSS,
            "</style></head><body>",
            "<h2>Détachés — liste issue de la base configurée</h2>",
            "<p class='meta'><b>Périmètre</b> : personnel avec <b>position administrative « Détaché »</b> "
            "(comme la situation numérique) <b>ou</b> colonne <b>Structure</b> = <code>DETACHE</code> "
            "(casse ignorée). "
            "Les lignes avec <code>Structure = DETACHE</code> mais une <b>autre Padmin</b> sont signalées "
            "(liste en fin de tableau, colonne Contrôle). "
            "Le <b>sigle</b> correspond à la colonne <b>subdivision arme</b> ; les <b>titres de bloc</b> "
            "affichés dans Excel sont ceux définis dans <b>Référentiels → Config. fichier DETACHES</b> "
            "(une ligne par sigle, colonnes titre 1 à 3)."
            + (" La désignation du lieu de détachement est résolue depuis <b>TabSecun / TabSecdeux / TabSectrois</b>." if has_lookup else
               " <i>(Désignations non disponibles — référentiel TabSec* vide ou non chargé.)</i>")
            + "</p>",
        ]

        if anomalies:
            parts.append(
                "<div class='warn'><b>À vérifier — Structure DETACHE sans Padmin « Détaché »</b> : "
                f"{len(anomalies)} personne(s). Leur <b>position administrative</b> actuelle est indiquée "
                "dans le détail ; corrigez la base ou validez le cas.</div>"
            )

        if n_off is not None and n_off != n_acc:
            parts.append(
                "<div class='warn'><b>Écart de volumétrie</b> : "
                f"{n_acc} ligne(s) au périmètre liste détachés en base, "
                f"{n_off} ligne(s) de données détectée(s) sur la feuille <code>DETACHE</code> "
                f"du fichier « officiel » ({html.escape(str(tpl))}). "
                "Vérifiez la mise à jour du fichier Excel ou les filtres de comptage.</div>"
            )
        elif n_off is not None:
            parts.append(
                f"<p class='meta'>Cohérence indicative : même ordre de grandeur que le fichier officiel "
                f"({n_off} ligne(s) détectées sur <code>DETACHE</code>).</p>"
            )

        # ── Récapitulatif par sigle ────────────────────────────────────────────
        parts.append("<h3>Récapitulatif par sigle / subdivision arme</h3>")
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th>Sigle (SubArme)</th>"
            "<th>Désignation bloc (titres Excel / config)</th>"
            + ("<th>Désignation (TabSec*)</th>" if has_lookup else "")
            + "<th>Effectif</th></tr></thead><tbody>"
        )
        for sig, n in recap:
            des_cfg = designation_bloc_excel(cfg, sig)
            desig = lookup.get(sig.strip(), "—") if has_lookup else None
            parts.append("<tr>")
            parts.append(f"<td>{html.escape(sig)}</td>")
            parts.append(f"<td>{html.escape(des_cfg or '—')}</td>")
            if has_lookup:
                parts.append(f"<td>{html.escape(desig or '—')}</td>")
            parts.append(f'<td class="num">{n}</td>')
            parts.append("</tr>")
        parts.append(
            "<tr><td><b>Total</b></td><td></td>"
            + ("<td></td>" if has_lookup else "")
            + f'<td class="num"><b>{n_acc}</b></td></tr></tbody></table>'
        )

        # ── Détail ────────────────────────────────────────────────────────────
        parts.append("<h3>Détail</h3>")
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th>N°</th><th>Nom</th><th>Grade</th>"
            "<th>Sigle (SubArme)</th><th>Désignation bloc (config)</th>"
            + ("<th>Unité de détachement</th>" if has_lookup else "")
            + "<th>Structure d'origine</th>"
            + ("<th>Désignation structure</th>" if has_lookup else "")
            + "<th>Position administrative</th>"
            + "<th>Contrôle</th>"
            + "<th>Réf. note</th></tr></thead><tbody>"
        )
        for i, r in enumerate(rows, 1):
            sigle = sigle_ou_subdivision(r)
            des_bl = designation_bloc_excel(cfg, sigle)
            parts.append("<tr>")
            parts.append(f"<td class='num'>{i}</td>")
            parts.append(f"<td>{html.escape(r.noms or '')}</td>")
            parts.append(f"<td>{html.escape(r.grade or '')}</td>")
            parts.append(f"<td>{html.escape(sigle)}</td>")
            parts.append(f"<td>{html.escape(des_bl or '—')}</td>")
            if has_lookup:
                desig_det = lookup.get(sigle.strip(), "—")
                parts.append(f"<td>{html.escape(desig_det)}</td>")
            parts.append(f"<td>{html.escape(r.structure or '')}</td>")
            if has_lookup:
                desig_orig = lookup.get((r.structure or "").strip(), "—")
                parts.append(f"<td>{html.escape(desig_orig)}</td>")
            parts.append(f"<td>{html.escape(r.position_admin or '')}</td>")
            parts.append(f"<td>{html.escape(detache_ecart_remarque(r))}</td>")
            parts.append(f"<td>{html.escape(r.ndecision or '')}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")

        ctx = load_sme_context()
        cmp = nouveaux_detaches_vs_mois_precedent(self._settings, ctx.mois_actif)
        parts.append("<h3>Écarts avec le mois précédent (fichier archive)</h3>")
        if not cmp.comparable:
            parts.append(f"<div class='warn'>{html.escape(cmp.message)}</div>")
        else:
            parts.append(f"<p class='meta'>{html.escape(cmp.message)}</p>")
            if cmp.cles_dupliquees_prev or cmp.cles_dupliquees_cur:
                parts.append(
                    "<div class='warn'>Doublons de clé (Id / jointure) : plusieurs lignes partagent la même clé — "
                    "les totaux « lignes » du module peuvent différer des cartes tableau de bord sans pour autant "
                    "indiquer de nouvelles fiches distinctes.</div>"
                )

            if cmp.nouveaux:
                parts.append("<h4>Nouveaux au périmètre détachés (absents de l’archive du mois précédent)</h4>")
                parts.append(
                    "<table class='grid'><thead><tr>"
                    "<th>Nom</th><th>Grade</th><th>Sigle</th><th>Désignation bloc (config)</th>"
                    "<th>Structure</th><th>Position admin.</th>"
                    "</tr></thead><tbody>"
                )
                for r in sorted(cmp.nouveaux, key=lambda x: ((x.noms or "").lower(),)):
                    sl = sigle_ou_subdivision(r)
                    parts.append("<tr>")
                    parts.append(f"<td>{html.escape(r.noms or '')}</td>")
                    parts.append(f"<td>{html.escape(r.grade or '')}</td>")
                    parts.append(f"<td>{html.escape(sl)}</td>")
                    parts.append(f"<td>{html.escape(designation_bloc_excel(cfg, sl) or '—')}</td>")
                    parts.append(f"<td>{html.escape(r.structure or '')}</td>")
                    parts.append(f"<td>{html.escape(r.position_admin or '')}</td>")
                    parts.append("</tr>")
                parts.append("</tbody></table>")

            if cmp.partis:
                parts.append("<h4>Ne sont plus au périmètre détachés (présents dans l’archive du mois précédent)</h4>")
                parts.append(
                    "<table class='grid'><thead><tr>"
                    "<th>Nom</th><th>Grade</th><th>Sigle</th><th>Désignation bloc (config)</th>"
                    "<th>Structure</th><th>Position admin.</th>"
                    "</tr></thead><tbody>"
                )
                for r in sorted(cmp.partis, key=lambda x: ((x.noms or "").lower(),)):
                    sl = sigle_ou_subdivision(r)
                    parts.append("<tr>")
                    parts.append(f"<td>{html.escape(r.noms or '')}</td>")
                    parts.append(f"<td>{html.escape(r.grade or '')}</td>")
                    parts.append(f"<td>{html.escape(sl)}</td>")
                    parts.append(f"<td>{html.escape(designation_bloc_excel(cfg, sl) or '—')}</td>")
                    parts.append(f"<td>{html.escape(r.structure or '')}</td>")
                    parts.append(f"<td>{html.escape(r.position_admin or '')}</td>")
                    parts.append("</tr>")
                parts.append("</tbody></table>")

            if not cmp.nouveaux and not cmp.partis:
                if cmp.n_prev_lignes != cmp.n_cur_lignes:
                    parts.append(
                        "<div class='warn'>Nombre de lignes différent entre archive et mois actuel, "
                        "mais aucune entrée ni sortie de fiche détectée au niveau des clés uniques : "
                        "vérifiez les doublons d’identifiant sur la liste générale, ou que l’archive "
                        "correspond bien au mois attendu.</div>"
                    )
                else:
                    parts.append(
                        "<p class='meta'><i>Aucun écart de fiche : mêmes personnes (clés) au périmètre détachés "
                        "que dans l’archive du mois précédent.</i></p>"
                    )

        parts.append("</body></html>")
        return "".join(parts)
