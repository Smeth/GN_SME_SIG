"""Aperçu HTML — module détachés (récap, détail, comparaison mois précédent)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import html
from dataclasses import dataclass, field

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.config.liste_generale_scope import list_available_export_mois
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_month_compare import (
    compare_detaches_entre_mois,
    default_compare_months,
)
from sme_gend.services.detaches_service import (
    DetachesService,
    designation_bloc_excel,
    detache_ecart_remarque,
    filter_detaches_display,
    filter_detaches_rows,
    is_padmin_detache_row,
    is_structure_detache_row,
    recap_par_padmin,
    recap_par_sigle,
    recap_par_structure,
    sigle_ou_subdivision,
)


def _build_sec_lookup(settings: Settings) -> dict[str, str]:
    from sme_gend.repositories.subdivision_repository import SubdivisionRepository

    lookup: dict[str, str] = {}
    for r in SubdivisionRepository(settings).fetch_flat_hierarchy():
        if r.code_u and r.design_u:
            lookup.setdefault(r.code_u.strip(), r.design_u.strip())
        if r.code_d and r.design_d:
            lookup.setdefault(r.code_d.strip(), r.design_d.strip())
        if r.code_t and r.design_t:
            lookup[r.code_t.strip()] = r.design_t.strip()
    return lookup


_CSS = """
body { margin: 10px; font-family: Calibri, "Segoe UI", Arial, sans-serif; font-size: 12px; }
h2 { font-size: 13pt; margin: 0 0 8px 0; color: #1a237e; }
h3 { font-size: 11pt; margin: 12px 0 6px 0; color: #283593; }
.meta { color: #64748b; margin-bottom: 8px; font-size: 11px; }
.warn { background: #fff3e0; border-left: 4px solid #ff9800; padding: 6px 10px; margin: 0 0 8px 0; font-size: 11px; }
table.grid { border-collapse: collapse; width: 100%; }
table.grid th, table.grid td { border: 1px solid #808080; padding: 4px 6px; text-align: left; }
table.grid th { background: #d9e2f3; font-weight: bold; text-align: center; }
table.grid td.num { text-align: right; }
table.grid tr:nth-child(even) td { background: #fafafa; }
.empty { color: #64748b; font-style: italic; padding: 24px; text-align: center; }
"""


@dataclass(slots=True)
class DetachesPreviewMeta:
    total_scope: int
    total_display: int
    n_anomalies: int
    sigles: list[tuple[str, int]]
    structures: list[tuple[str, int]]
    padmins: list[tuple[str, int]]
    status_line: str
    compare_mois_ref: str
    compare_mois_cible: str
    available_months: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DetachesPreviewBundle:
    meta: DetachesPreviewMeta
    html_recap: str
    html_detail: str
    html_anomalies: str
    html_compare: str
    rows: list = field(default_factory=list, repr=False)
    filters: tuple = field(default=(), repr=False)
    source_settings: object = field(default=None, repr=False)
    configuration: object = field(default=None, repr=False)


def detaches_filter_key(sans_note_only=False, avec_note_only=False, search="", sigle="", structure="", padmin=""):
    from sme_gend.services.search_text import normalize_search_text
    return (bool(sans_note_only), bool(avec_note_only), normalize_search_text(search),
            str(sigle or "__all__"), str(structure or "__all__"), str(padmin or "__all__"))


class DetachesPreviewService:
    """HTML compact — périmètre détachés (Padmin « Détaché » et/ou Structure = DETACHE)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._svc = DetachesService(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._svc = DetachesService(settings)

    def build_html(
        self,
        *,
        sans_note_only: bool = False,
        avec_note_only: bool = False,
        search: str = "",
        sigle: str = "",
    ) -> str:
        """Compatibilité : renvoie l’onglet détail."""
        return self.build_bundle(
            sans_note_only=sans_note_only,
            avec_note_only=avec_note_only,
            search=search,
            sigle=sigle,
        ).html_detail

    def build_bundle(
        self,
        *,
        sans_note_only: bool = False,
        avec_note_only: bool = False,
        search: str = "",
        sigle: str = "",
        structure: str = "",
        padmin: str = "",
        compare_mois_ref: str = "",
        compare_mois_cible: str = "",
    ) -> DetachesPreviewBundle:
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        source_month = resolve_export_mois(self._settings)
        scope_rows = filter_detaches_rows(
            self._svc.fetch_detaches(),
            sans_note_only=sans_note_only,
            avec_note_only=avec_note_only,
        )
        disp_kw = dict(
            search=search,
            sigle=sigle,
            structure=structure,
            padmin=padmin,
        )
        rows = filter_detaches_display(scope_rows, **disp_kw)
        n_scope = len(scope_rows)
        n_disp = len(rows)
        anomalies_all = [
            r for r in scope_rows if is_structure_detache_row(r) and not is_padmin_detache_row(r)
        ]
        anomalies = filter_detaches_display(anomalies_all, **disp_kw)
        recap_all = recap_par_sigle(scope_rows)
        recap = recap_par_sigle(rows)
        structures_all = recap_par_structure(scope_rows)
        padmins_all = recap_par_padmin(scope_rows)
        cfg = DetachesConfigService(self._settings).load()
        lookup = _build_sec_lookup(self._settings)
        has_lookup = bool(lookup)

        warnings: list[str] = []
        if anomalies_all:
            warnings.append(
                f"<b>{len(anomalies_all)} fiche(s)</b> avec Structure = DETACHE "
                "sans Padmin « Détaché » — voir l’onglet <b>Contrôle Structure</b>."
            )

        def_ref, def_cible = default_compare_months(self._settings)
        cmp_ref = (compare_mois_ref or def_ref or "").strip()
        cmp_cible = (compare_mois_cible or def_cible or "").strip()
        months = list_available_export_mois(self._settings)

        filter_bits: list[str] = []
        if sans_note_only:
            filter_bits.append("sans réf. note")
        elif avec_note_only:
            filter_bits.append("avec réf. note")
        if (sigle or "").strip() and sigle != "__all__":
            filter_bits.append(f"sigle {sigle.strip()}")
        if (structure or "").strip() and structure != "__all__":
            filter_bits.append(f"structure {structure.strip()}")
        if (padmin or "").strip() and padmin != "__all__":
            filter_bits.append(f"Padmin {padmin.strip()}")
        if (search or "").strip():
            filter_bits.append("recherche active")
        filt = f" · Filtres : {', '.join(filter_bits)}" if filter_bits else ""

        status = f"{n_disp} affiché(s) / {n_scope} au périmètre{filt}"
        meta = DetachesPreviewMeta(
            total_scope=n_scope,
            total_display=n_disp,
            n_anomalies=len(anomalies_all),
            sigles=recap_all,
            structures=structures_all,
            padmins=padmins_all,
            status_line=status,
            compare_mois_ref=cmp_ref,
            compare_mois_cible=cmp_cible,
            available_months=months,
            warnings=warnings,
        )

        wrap = lambda body: (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            + _CSS
            + "</style></head><body>"
            + body
            + "</body></html>"
        )

        html_recap = wrap(self._html_recap(recap, n_disp, has_lookup, lookup, cfg))
        html_detail = wrap(self._html_detail(rows, n_disp, has_lookup, lookup, cfg))
        html_anomalies = wrap(
            self._html_anomalies(anomalies, len(anomalies_all), has_lookup, lookup, cfg)
        )
        html_compare = wrap(
            self._html_compare(cfg, cmp_ref, cmp_cible, **disp_kw)
        )

        return DetachesPreviewBundle(
            meta=meta,
            html_recap=html_recap,
            html_detail=html_detail,
            html_anomalies=html_anomalies,
            html_compare=html_compare,
            rows=deepcopy(rows),
            filters=detaches_filter_key(sans_note_only, avec_note_only, **disp_kw),
            source_settings=replace(self._settings, mysql_view_mois=source_month),
            configuration=deepcopy(cfg),
        )

    def _html_recap(
        self,
        recap: list[tuple[str, int]],
        n_total: int,
        has_lookup: bool,
        lookup: dict[str, str],
        cfg,
    ) -> str:
        if not recap:
            return "<p class='empty'>Aucun détaché pour les filtres choisis.</p>"
        parts = ["<h2>Récapitulatif par sigle</h2>"]
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th>Sigle</th><th>Titres Excel (config)</th>"
            + ("<th>TabSec*</th>" if has_lookup else "")
            + "<th>Effectif</th></tr></thead><tbody>"
        )
        for sig, n in recap:
            des_cfg = designation_bloc_excel(cfg, sig)
            parts.append("<tr>")
            parts.append(f"<td>{html.escape(sig)}</td>")
            parts.append(f"<td>{html.escape(des_cfg or '—')}</td>")
            if has_lookup:
                parts.append(f"<td>{html.escape(lookup.get(sig.strip(), '—'))}</td>")
            parts.append(f'<td class="num">{n}</td></tr>')
        parts.append(
            "<tr><td><b>Total</b></td><td></td>"
            + ("<td></td>" if has_lookup else "")
            + f'<td class="num"><b>{n_total}</b></td></tr></tbody></table>'
        )
        return "".join(parts)

    def _html_detail(
        self,
        rows: list[ListeGeneraleRow],
        n_total: int,
        has_lookup: bool,
        lookup: dict[str, str],
        cfg,
    ) -> str:
        parts = [f"<h2>Liste ({n_total})</h2>"]
        if not rows:
            parts.append("<p class='empty'>Aucune ligne. Élargissez les filtres ou actualisez.</p>")
            return "".join(parts)
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th>N°</th><th>Nom</th><th>Grade</th><th>Sigle</th><th>Bloc Excel</th>"
            + ("<th>Unité dét.</th>" if has_lookup else "")
            + "<th>Structure orig.</th>"
            + ("<th>Désign. struct.</th>" if has_lookup else "")
            + "<th>Padmin</th><th>Contrôle</th><th>Réf. note</th>"
            "</tr></thead><tbody>"
        )
        for i, r in enumerate(rows, 1):
            sigle = sigle_ou_subdivision(r)
            des_bl = designation_bloc_excel(cfg, sigle)
            ctrl = detache_ecart_remarque(r)
            parts.append("<tr>")
            parts.append(f"<td class='num'>{i}</td>")
            parts.append(f"<td>{html.escape(r.noms or '')}</td>")
            parts.append(f"<td>{html.escape(r.grade or '')}</td>")
            parts.append(f"<td>{html.escape(sigle)}</td>")
            parts.append(f"<td>{html.escape(des_bl or '—')}</td>")
            if has_lookup:
                parts.append(f"<td>{html.escape(lookup.get(sigle.strip(), '—'))}</td>")
            parts.append(f"<td>{html.escape(r.structure or '')}</td>")
            if has_lookup:
                parts.append(f"<td>{html.escape(lookup.get((r.structure or '').strip(), '—'))}</td>")
            parts.append(f"<td>{html.escape(r.position_admin or '')}</td>")
            if ctrl:
                parts.append(f"<td>{html.escape(ctrl)}</td>")
            else:
                parts.append("<td></td>")
            parts.append(f"<td>{html.escape(r.ndecision or '')}</td></tr>")
        parts.append("</tbody></table>")
        return "".join(parts)

    def _html_anomalies(
        self,
        rows: list[ListeGeneraleRow],
        n_total_scope: int,
        has_lookup: bool,
        lookup: dict[str, str],
        cfg,
    ) -> str:
        parts = [
            "<h2>Structure DETACHE sans Padmin « Détaché »</h2>",
            "<p class='meta'>Ces fiches sont comptées dans le périmètre détachés "
            "grâce à la colonne Structure, mais la position administrative n’est pas « Détaché ». "
            "Vérifiez la cohérence avec la situation numérique.</p>",
        ]
        if n_total_scope != len(rows):
            parts.append(
                f"<p class='meta'>{len(rows)} affichée(s) / {n_total_scope} au périmètre "
                "(filtres actifs sur la barre d’outils).</p>"
            )
        if not rows:
            parts.append("<p class='empty'>Aucune fiche dans cette catégorie pour les filtres choisis.</p>")
            return "".join(parts)
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th>N°</th><th>Nom</th><th>Grade</th><th>Sigle</th><th>Bloc Excel</th>"
            + ("<th>Unité dét.</th>" if has_lookup else "")
            + "<th>Structure</th><th>Padmin</th><th>Remarque</th><th>Réf. note</th>"
            "</tr></thead><tbody>"
        )
        for i, r in enumerate(rows, 1):
            sigle = sigle_ou_subdivision(r)
            parts.append("<tr>")
            parts.append(f"<td class='num'>{i}</td>")
            parts.append(f"<td>{html.escape(r.noms or '')}</td>")
            parts.append(f"<td>{html.escape(r.grade or '')}</td>")
            parts.append(f"<td>{html.escape(sigle)}</td>")
            parts.append(f"<td>{html.escape(designation_bloc_excel(cfg, sigle) or '—')}</td>")
            if has_lookup:
                parts.append(f"<td>{html.escape(lookup.get(sigle.strip(), '—'))}</td>")
            parts.append(f"<td>{html.escape(r.structure or '')}</td>")
            pa = (r.position_admin or "").strip() or "(vide)"
            parts.append(f"<td>{html.escape(r.position_admin or '')}</td>")
            parts.append(
                f"<td>{html.escape(f'Structure DETACHE, Padmin « {pa} »')}</td>"
            )
            parts.append(f"<td>{html.escape(r.ndecision or '')}</td></tr>")
        parts.append("</tbody></table>")
        return "".join(parts)

    def _html_compare(
        self,
        cfg,
        mois_ref: str,
        mois_cible: str,
        *,
        search: str = "",
        sigle: str = "",
        structure: str = "",
        padmin: str = "",
    ) -> str:
        cmp = compare_detaches_entre_mois(self._settings, mois_ref, mois_cible)
        parts = ["<h2>Comparaison de deux mois</h2>"]
        if not cmp.comparable:
            parts.append(f"<div class='warn'>{html.escape(cmp.message)}</div>")
            return "".join(parts)
        parts.append(f"<p class='meta'>{html.escape(cmp.message)}</p>")
        parts.append(
            "<table class='grid'><thead><tr>"
            "<th></th>"
            f"<th>Mois {html.escape(cmp.mois_ref)}</th>"
            f"<th>Mois {html.escape(cmp.mois_cible)}</th>"
            "<th>Écart</th></tr></thead><tbody>"
            f"<tr><td>Nombre de détachés (lignes)</td><td class='num'>{cmp.n_prev_lignes}</td>"
            f"<td class='num'>{cmp.n_cur_lignes}</td>"
            f"<td class='num'>{cmp.n_cur_lignes - cmp.n_prev_lignes:+d}</td></tr>"
            f"<tr><td>Personnes distinctes</td><td class='num'>{cmp.n_prev_cles}</td>"
            f"<td class='num'>{cmp.n_cur_cles}</td>"
            f"<td class='num'>{cmp.n_cur_cles - cmp.n_prev_cles:+d}</td></tr>"
            f"<tr><td>Devenus détachés</td><td class='num'>—</td>"
            f"<td class='num'>{len(cmp.nouveaux)}</td><td></td></tr>"
            f"<tr><td>Ne sont plus détachés</td><td class='num'>{len(cmp.partis)}</td>"
            f"<td class='num'>—</td><td></td></tr>"
            f"<tr><td>Toujours détachés — informations modifiées</td><td colspan='2' class='num'>"
            f"{len(cmp.modifies)}</td><td></td></tr>"
            "</tbody></table>"
        )
        if cmp.cles_dupliquees_prev or cmp.cles_dupliquees_cur:
            parts.append(
                "<div class='warn'>Plusieurs lignes pour la même personne : les totaux "
                "« lignes » peuvent différer du nombre de personnes.</div>"
            )

        disp_kw = dict(search=search, sigle=sigle, structure=structure, padmin=padmin)
        nouveaux = filter_detaches_display(cmp.nouveaux, **disp_kw)
        partis = filter_detaches_display(cmp.partis, **disp_kw)
        modifies = [
            pair
            for pair in cmp.modifies
            if filter_detaches_display([pair[1]], **disp_kw)
        ]

        def _table(title: str, items: list) -> None:
            if not items:
                return
            parts.append(f"<h3>{html.escape(title)} ({len(items)})</h3>")
            parts.append(
                "<table class='grid'><thead><tr>"
                "<th>N°</th><th>Nom</th><th>Grade</th><th>Sigle</th><th>Bloc</th>"
                "<th>Structure</th><th>Position admin.</th></tr></thead><tbody>"
            )
            for i, r in enumerate(
                sorted(items, key=lambda x: ((x.noms or "").lower(),)), 1
            ):
                sl = sigle_ou_subdivision(r)
                parts.append("<tr>")
                parts.append(f"<td class='num'>{i}</td>")
                parts.append(f"<td>{html.escape(r.noms or '')}</td>")
                parts.append(f"<td>{html.escape(r.grade or '')}</td>")
                parts.append(f"<td>{html.escape(sl)}</td>")
                parts.append(f"<td>{html.escape(designation_bloc_excel(cfg, sl) or '—')}</td>")
                parts.append(f"<td>{html.escape(r.structure or '')}</td>")
                parts.append(f"<td>{html.escape(r.position_admin or '')}</td></tr>")
            parts.append("</tbody></table>")

        _table(
            f"Devenus détachés (présents en {cmp.mois_cible}, absents en {cmp.mois_ref})",
            nouveaux,
        )
        _table(
            f"Ne sont plus détachés (présents en {cmp.mois_ref}, absents en {cmp.mois_cible})",
            partis,
        )
        if modifies:
            parts.append(
                f"<h3>Toujours détachés — informations modifiées ({len(modifies)})</h3>"
            )
            parts.append(
                "<p class='meta'>Même personne comptée comme détachée aux deux mois, "
                "mais sigle, structure ou position administrative a changé.</p>"
            )
            parts.append(
                "<table class='grid'><thead><tr>"
                "<th>N°</th><th>Nom</th><th>Grade</th>"
                f"<th>Sigle ({html.escape(cmp.mois_ref)})</th>"
                f"<th>Sigle ({html.escape(cmp.mois_cible)})</th>"
                f"<th>Structure ({html.escape(cmp.mois_ref)})</th>"
                f"<th>Structure ({html.escape(cmp.mois_cible)})</th>"
                f"<th>Position admin. ({html.escape(cmp.mois_ref)})</th>"
                f"<th>Position admin. ({html.escape(cmp.mois_cible)})</th>"
                "</tr></thead><tbody>"
            )
            for i, (prev_r, cur_r) in enumerate(
                sorted(modifies, key=lambda p: ((p[1].noms or "").lower(),)), 1
            ):
                parts.append("<tr>")
                parts.append(f"<td class='num'>{i}</td>")
                parts.append(f"<td>{html.escape(cur_r.noms or '')}</td>")
                parts.append(f"<td>{html.escape(cur_r.grade or '')}</td>")
                parts.append(f"<td>{html.escape(sigle_ou_subdivision(prev_r))}</td>")
                parts.append(f"<td>{html.escape(sigle_ou_subdivision(cur_r))}</td>")
                parts.append(f"<td>{html.escape(prev_r.structure or '')}</td>")
                parts.append(f"<td>{html.escape(cur_r.structure or '')}</td>")
                parts.append(f"<td>{html.escape(prev_r.position_admin or '')}</td>")
                parts.append(f"<td>{html.escape(cur_r.position_admin or '')}</td></tr>")
            parts.append("</tbody></table>")

        if not nouveaux and not partis and not modifies:
            if cmp.n_prev_lignes != cmp.n_cur_lignes:
                parts.append(
                    "<div class='warn'>Le nombre de lignes diffère sans personne listée "
                    "ci-dessus (identifiants en double possibles).</div>"
                )
            else:
                parts.append(
                    "<p class='meta'>Aucune personne concernée entre ces deux mois.</p>"
                )
        return "".join(parts)
