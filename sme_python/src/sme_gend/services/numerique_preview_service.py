"""Aperçu HTML — situation numérique (mêmes agrégats que l'export Excel)."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, HTML_STEPS, export_phase, export_rows

import html
from pathlib import Path

from openpyxl import load_workbook

from sme_gend.config.liste_generale_scope import (
    ExportMoisContext,
    build_export_mois_context,
    resolve_export_mois,
    settings_for_numerique_data,
)
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.numerique_excel_export import (
    INDISPO_SHEET,
    SHEET_DESERTEUR,
    SHEET_MALADE,
    SHEET_STAGE_EXT,
    SHEET_STAGE_LOC,
    collect_indispo_structure_meta,
    read_numerique_grades_from_template,
    read_satellite_grade_columns_from_template,
    recap_column_headers_from_mois_actif,
    satellite_grade_count_pairs,
    sorted_rows_for_padmin,
)
from sme_gend.services.numerique_padmin import (
    PADMIN_DESERTEUR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)
from sme_gend.services.numerique_recap_baseline import resolve_recap_cd_baseline
from sme_gend.services.numerique_stats import (
    INDISPO_CATEGORY_SPECS,
    NUMERIQUE_CATEGORY_SLICES,
    RUBRIQUE_KEYS_ROW8_19,
    RECAP_INDISPO_KEYS,
    build_indispo_matrix,
    build_numerique_matrix,
    category_indisponibles,
    matrix_row_total,
    sum_by_grade_category,
)

# Libellés affichés (lignes matrice NUMERIQUE).
RUBRIQUE_LABELS_FR: dict[str, str] = {
    "inscrits": "Inscrits",
    "presents": "Présents",
    "detache": "Détaché",
    "deserteur": "Déserteur",
    "conge_admin": "Congé administratif",
    "conge_exp": "Congé d'expectative",
    "mission": "Mission",
    "malade": "Malade",
    "inst_mr": "Instance de MR",
    "stage_local": "Stage local",
    "stage_ex": "Stage étranger",
    "prison_ma": "Prison M-A",
}

INDISPO_COL_LABELS_FR: tuple[str, ...] = (
    "Mission",
    "Malade",
    "Congé admin",
    "Stage local",
    "Stage ext.",
    "Déserteur",
    "Instance MR",
    "Détention",
)

_CSS = """
body { margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif; font-size: 12px; background: #fff; }
h2 { font-size: 14pt; margin: 0 0 10px 0; color: #1a237e; }
.meta { color: #555; margin-bottom: 12px; font-size: 11px; }
table.grid { border-collapse: collapse; width: 100%; }
table.grid th, table.grid td { border: 1px solid #808080; padding: 4px 6px; text-align: center; }
table.grid th { background: #d9e2f3; font-weight: bold; }
table.grid td.row-hdr { background: #e8e8e8; font-weight: bold; text-align: left; }
table.grid tr:nth-child(even) td:not(.row-hdr) { background: #fafafa; }
.tot { font-weight: bold; background: #e3f2fd !important; }
h3 { font-size: 12pt; margin: 18px 0 8px 0; color: #283593; border-bottom: 1px solid #c5cae9; padding-bottom: 4px; }
.section-note { color: #666; font-size: 11px; margin: 0 0 8px 0; }
"""


class NumeriquePreviewService:
    """Aperçus HTML alignés sur les calculs d'export (MySQL)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gain_perte_settings = settings
        self._repo = ListeGeneraleRepository(settings)

    @property
    def liste_settings(self) -> Settings:
        """Source ListeGenerale / satellites — snapshot du mois SME consulté."""
        return self._data_settings()

    @property
    def gain_perte_settings(self) -> Settings:
        """Source mouvement_effectif (gains & pertes) : toujours la base d'exploitation (saisie gains/pertes)."""
        return self._gain_perte_settings

    def rebind_settings(
        self,
        settings: Settings,
        *,
        gain_perte_settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._gain_perte_settings = (
            gain_perte_settings if gain_perte_settings is not None else settings
        )

    def _data_settings(self) -> Settings:
        return settings_for_numerique_data(self._settings)

    def _liste_rows(self) -> list[ListeGeneraleRow]:
        from sme_gend.services.liste_generale_cache import cached_numerique_liste_rows

        return cached_numerique_liste_rows(self._settings)

    def _sat_repo(self) -> NumeriqueSatelliteRepository:
        return NumeriqueSatelliteRepository(self._data_settings())

    def _grades(self) -> list:
        from sme_gend.repositories.grade_repository import GradeRepository

        return GradeRepository(self._settings).fetch_all()

    def _export_mois_context(self) -> ExportMoisContext:
        return build_export_mois_context(self._settings)

    def _build_html_gain_perte_section(self) -> str:
        """Section HTML résumant les gains & pertes du mois exporté (mouvement_effectif)."""
        from sme_gend.repositories.gain_perte_repository import GainPerteRepository

        exp_ctx = self._export_mois_context()
        export_mois = (exp_ctx.export_mois or "").strip()
        if not export_mois:
            return ""

        try:
            repo = GainPerteRepository(self._gain_perte_settings)
            gp_rows = repo.fetch_for_month(export_mois)
            from sme_gend.services.gain_perte_coherence import load_unsignaled_pertes

            gp_rows = list(gp_rows) + load_unsignaled_pertes(
                self._gain_perte_settings, export_mois, gp_rows
            )
        except Exception:
            return (
                f"<h3>7. Gains &amp; Pertes — {html.escape(export_mois)}</h3>"
                "<p class='section-note'>Lecture des Gains &amp; Pertes impossible. "
                "Vérifiez la connexion puis actualisez.</p>"
            )

        if not gp_rows:
            return (
                f"<h3>7. Gains &amp; Pertes — {html.escape(export_mois)}</h3>"
                "<p class='section-note'>Aucune ligne mouvement_effectif (gains & pertes) pour ce mois dans la <b>base d'exploitation</b> "
                "(les archives mensuelles ne contiennent en général pas cette table ; "
                "le filtre utilise le code mois Cdsme, ex. 052026).</p>"
            )

        n_gains = sum(1 for r in gp_rows if r.is_gain)
        n_pertes = sum(1 for r in gp_rows if r.is_perte)
        n_deces = sum(1 for r in gp_rows if r.is_deces)

        parts: list[str] = [
            f"<h3>7. Gains &amp; Pertes — {html.escape(export_mois)}</h3>",
            f"<p class='section-note'>{len(gp_rows)} enregistrement(s) — "
            f"<b>{n_gains}</b> gain(s), <b>{n_pertes}</b> perte(s) dont <b>{n_deces}</b> décès.</p>",
            "<table class='grid'><thead><tr>"
            "<th>ID</th><th>Grade</th><th>Nom</th><th>Objet</th><th>Observation</th>"
            "</tr></thead><tbody>",
        ]
        for r in gp_rows:
            style = " style='color:#c62828;font-weight:600;'" if r.is_deces else ""
            parts.append(
                f"<tr{style}>"
                f"<td>{html.escape(r.id or '')}</td>"
                f"<td>{html.escape(r.grade or '')}</td>"
                f"<td>{html.escape(r.nom or '')}</td>"
                f"<td>{html.escape(r.objet_display)}</td>"
                f"<td>{html.escape(r.observ_display)}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        return "".join(parts)

    @tracked_export(steps=HTML_STEPS)
    def build_html_matrice(self) -> str:
        rows = self._liste_rows()
        tpl = self._settings.template_numerique_path
        from sme_gend.services.excel_style_helpers import build_grade_abbrev_map

        grade_rows = self._grades()
        grade_abbrev_map = build_grade_abbrev_map(grade_rows)
        grades = read_numerique_grades_from_template(tpl)
        matrix = build_numerique_matrix(rows, grades, grade_abbrev_map=grade_abbrev_map)

        cat_labels = [name for name, _sl in NUMERIQUE_CATEGORY_SLICES] + ["Total"]
        cat_insc = sum_by_grade_category(matrix["inscrits"], grades)
        cat_pres = sum_by_grade_category(matrix["presents"], grades)
        cat_ind = category_indisponibles(matrix["inscrits"], matrix["presents"], grades)

        _ym_recap = (resolve_export_mois(self._settings) or "").strip()
        cd_vals = resolve_recap_cd_baseline(self._settings, _ym_recap or None, grades)
        recap_rows_html: list[str] = []
        for idx, ((key, lib), cd_cell) in enumerate(zip(RECAP_INDISPO_KEYS, cd_vals[:9], strict=True)):
            cur = matrix_row_total(matrix, key, grades)
            prev = cd_cell
            var = None if prev is None else cur - prev
            recap_rows_html.append(
                "<tr>"
                f"<td class='row-hdr'>{html.escape(lib)}</td>"
                f"<td>{'' if prev is None else prev}</td>"
                f"<td class='tot'>{cur}</td>"
                f"<td>{'' if var is None else var}</td>"
                "</tr>"
            )
        cd_total = cd_vals[9] if len(cd_vals) > 9 else None
        cur_sum = sum(matrix_row_total(matrix, key, grades) for key, _ in RECAP_INDISPO_KEYS)
        prev_sum = cd_total
        var_sum = None if prev_sum is None else cur_sum - prev_sum
        recap_rows_html.append(
            "<tr>"
            "<td class='row-hdr'>TOTAL</td>"
            f"<td class='tot'>{'' if prev_sum is None else prev_sum}</td>"
            f"<td class='tot'>{cur_sum}</td>"
            f"<td class='tot'>{'' if var_sum is None else var_sum}</td>"
            "</tr>"
        )

        title = "Synthèse des effectifs — feuille NUMERIQUE (aperçu complet)"
        exp_ctx = self._export_mois_context()
        export_lbl = html.escape(exp_ctx.label_short())
        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            _CSS,
            "</style></head><body>",
            f"<h2>{html.escape(title)}</h2>",
            f"<p class='meta'>{exp_ctx.banner_html()} — {len(rows)} ligne(s) liste générale. "
            "« Présents » = Padmin contenant « présent » ou libellé « stage interne ». "
            "Colonne <b>mois figé</b> : totaux du <b>mois SME précédent</b> "
            f"({export_lbl}).</p>",
            "<h3>1. Matrice principale (rubrique × grade)</h3>",
            "<table class='grid'><thead><tr><th>Rubrique</th>",
        ]
        for g in grades:
            parts.append(f"<th>{html.escape(g)}</th>")
        parts.append("<th>TOTAL</th></tr></thead><tbody>")

        for key in RUBRIQUE_KEYS_ROW8_19:
            label = RUBRIQUE_LABELS_FR.get(key, key)
            counts = matrix[key]
            row_sum = sum(counts.get(g, 0) for g in grades)
            parts.append("<tr><td class='row-hdr'>")
            parts.append(html.escape(label))
            parts.append("</td>")
            for g in grades:
                v = int(counts.get(g, 0))
                parts.append(f"<td>{v}</td>")
            parts.append(f"<td class='tot'>{row_sum}</td></tr>")

        parts.append("</tbody></table>")

        parts.append("<h3>2. État général des effectifs par catégorie</h3>")
        parts.append(
            "<p class='section-note'>Équivalent colonnes V–AC (Inscrits / Présents / Indisponibles par catégorie).</p>"
        )
        parts.append("<table class='grid'><thead><tr><th>Catégorie</th>")
        for lab in cat_labels:
            parts.append(f"<th>{html.escape(lab)}</th>")
        parts.append("</tr></thead><tbody>")
        parts.append("<tr><td class='row-hdr'>Inscrits</td>")
        for v in cat_insc:
            parts.append(f"<td>{int(v)}</td>")
        parts.append("</tr>")
        parts.append("<tr><td class='row-hdr'>Présents</td>")
        for v in cat_pres:
            parts.append(f"<td>{int(v)}</td>")
        parts.append("</tr>")
        parts.append("<tr><td class='row-hdr'>Indisponibles</td>")
        for v in cat_ind:
            parts.append(f"<td>{int(v)}</td>")
        parts.append("</tr></tbody></table>")

        parts.append("<h3>3. Situation ventilée par grade (inscrits / présents / indisponibles)</h3>")
        parts.append("<p class='section-note'>Équivalent zone AD–AS : trois lignes de synthèse par grade.</p>")
        parts.append("<table class='grid'><thead><tr><th>Rubrique</th>")
        for g in grades:
            parts.append(f"<th>{html.escape(g)}</th>")
        parts.append("<th>TOTAL</th></tr></thead><tbody>")
        ins = matrix["inscrits"]
        pr = matrix["presents"]
        for label, rowmap in (
            ("Inscrits", ins),
            ("Présents", pr),
            ("Indisponibles", {g: int(ins.get(g, 0)) - int(pr.get(g, 0)) for g in grades}),
        ):
            parts.append(f"<tr><td class='row-hdr'>{html.escape(label)}</td>")
            sm = 0
            for g in grades:
                v = int(rowmap.get(g, 0))
                sm += v
                parts.append(f"<td>{v}</td>")
            parts.append(f"<td class='tot'>{sm}</td></tr>")
        parts.append("</tbody></table>")

        parts.append("<h3>4. État des présents par catégorie</h3>")
        parts.append("<p class='section-note'>Équivalent zone AY–BG (agrégats des présents par tranche).</p>")
        parts.append("<table class='grid'><thead><tr><th/>")
        for lab in cat_labels:
            parts.append(f"<th>{html.escape(lab)}</th>")
        parts.append("</tr></thead><tbody><tr>")
        parts.append("<td class='row-hdr'>Présents</td>")
        for v in cat_pres:
            parts.append(f"<td>{int(v)}</td>")
        parts.append("</tr></tbody></table>")

        parts.append("<h3>5. Effectif détaillé des présents par grade</h3>")
        parts.append("<p class='section-note'>Équivalent zone BH–BZ (une ligne, effectifs « Présents » par grade).</p>")
        parts.append("<table class='grid'><thead><tr><th>Grade</th>")
        for g in grades:
            parts.append(f"<th>{html.escape(g)}</th>")
        parts.append("</tr></thead><tbody><tr><td class='row-hdr'>Présents</td>")
        for g in grades:
            parts.append(f"<td>{int(matrix['presents'].get(g, 0))}</td>")
        parts.append("</tr></tbody></table>")

        exp_ctx = self._export_mois_context()
        _ym = (exp_ctx.export_mois or "").strip()
        if _ym:
            hdr = recap_column_headers_from_mois_actif(_ym)
            if hdr:
                mois_fige, mois_courant = hdr
            else:
                mois_courant = _ym
                mois_fige = "Mois précédent"
        else:
            mois_courant = "Mois courant"
            mois_fige = "Mois précédent (MySQL)"

        parts.append("<h3>6. Récapitulatif de la variation des indisponibles</h3>")
        parts.append(
            "<p class='section-note'>Équivalent zone CB–CF : "
            "« mois courant » = totaux calculés sur la liste générale du mois actif ; "
            "« mois figé » = mêmes totaux recalculés pour le <b>mois SME précédent</b> en base "
            "(les données de ce mois doivent être renseignées). Variation = courant − figé. Identique à l'export Excel.</p>"
        )
        parts.append("<table class='grid'><thead><tr><th>Position administrative</th>")
        parts.append(f"<th>{html.escape(mois_fige)}</th>")
        parts.append(f"<th>{html.escape(mois_courant)}</th>")
        parts.append("<th>Variation</th></tr></thead><tbody>")
        parts.extend(recap_rows_html)
        parts.append("</tbody></table>")

        gp_section = self._build_html_gain_perte_section()
        if gp_section:
            parts.append(gp_section)

        parts.append("</body></html>")
        return "".join(parts)

    @tracked_export(steps=HTML_STEPS)
    def build_html_indispo(self) -> str:
        rows = self._liste_rows()
        tpl = self._settings.template_numerique_path
        if tpl is None or not tpl.is_file():
            return (
                "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
                + _CSS
                + "</style></head><body>"
                "<h2>Indisponibilités par structure</h2>"
                "<p class='meta'><b>Gabarit Excel requis</b> pour définir les lignes structures "
                "(feuille « INDISPO PAR STRUCTURE bis »). Indiquez le chemin dans "
                "Fichier → Configuration MySQL et chemins…</p></body></html>"
            )

        wb = load_workbook(tpl, data_only=False, keep_vba=False)
        try:
            if INDISPO_SHEET not in wb.sheetnames:
                return (
                    "<!DOCTYPE html><html><body><p>Feuille « "
                    + html.escape(INDISPO_SHEET)
                    + " » absente du gabarit.</p></body></html>"
                )
            ws = wb[INDISPO_SHEET]
            labels, row_codes = collect_indispo_structure_meta(ws)
        finally:
            wb.close()

        mat = build_indispo_matrix(rows, row_structure_codes=row_codes)

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            _CSS,
            "</style></head><body>",
            "<h2>Indisponibilités par structure</h2>",
            f"<p class='meta'>{self._export_mois_context().banner_html()} — "
            f"{len(rows)} ligne(s) liste générale — même logique que l'export Excel.</p>",
            "<table class='grid'><thead><tr><th>Structure</th>",
        ]
        for lab in INDISPO_COL_LABELS_FR:
            parts.append(f"<th>{html.escape(lab)}</th>")
        parts.append("<th>TOTAL</th></tr></thead><tbody>")

        for lab, line in zip(labels, mat, strict=True):
            parts.append("<tr><td class='row-hdr'>")
            parts.append(html.escape(lab))
            parts.append("</td>")
            for v in line:
                parts.append(f"<td>{int(v)}</td>")
            parts.append(f"<td class='tot'>{int(sum(line))}</td></tr>")

        col_totals = [sum(mat[r][c] for r in range(len(mat))) for c in range(len(INDISPO_CATEGORY_SPECS))]
        parts.append("<tr><td class='row-hdr'>TOTAL</td>")
        for v in col_totals:
            parts.append(f"<td class='tot'>{int(v)}</td>")
        parts.append(f"<td class='tot'>{int(sum(col_totals))}</td></tr>")

        parts.append("</tbody></table></body></html>")
        return "".join(parts)

    @tracked_export(steps=HTML_STEPS)
    def build_html_satellite_etats(self) -> str:
        """Aperçu des feuilles MISSION, MALADE, DESERTEUR, STAGE-LOC, STAGE-EXT (comme l’export Excel)."""
        rows = self._liste_rows()
        tpl = self._settings.template_numerique_path
        grades = self._grades()

        sat = self._sat_repo()
        exp_ctx = self._export_mois_context()
        missions = sat.load_missions()
        malades = sat.load_malades()
        deserteurs = sat.load_deserteurs()
        stages_loc = sat.load_stages_loc()
        stages_ex = sat.load_stages_ex()

        def td_val(v: object | None) -> str:
            if v is None:
                return "<td></td>"
            s = str(v).strip()
            if not s:
                return "<td></td>"
            return f"<td>{html.escape(s)}</td>"

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            _CSS,
            "</style></head><body>",
            "<h2>Listes détaillées — missions, malades, déserteurs et stages (aperçu)</h2>",
            f"<p class='meta'>{exp_ctx.banner_html()} — {len(rows)} ligne(s) dans <b>ListeGenerale</b> — même filtre "
            "<b>Padmin</b> et jointure sur <b>agent_mission</b>, <b>agent_malade</b>, "
            "<b>agent_desertion</b>, <b>parcours_stage</b> (comme l’export Excel). "
            "Sans ligne satellite : identité et structure affichées, détails vides.</p>",
        ]
        if tpl is None or not Path(tpl).is_file():
            parts.append(
                "<p class='meta'><b>Note :</b> indiquez le gabarit « numerique.xlsx » "
                "(Fichier → Chemins…) pour aligner les bandeaux par grade sur les colonnes Excel.</p>"
            )

        # --- MISSION ---
        mission_rows = sorted_rows_for_padmin(rows, PADMIN_MISSION, grades=grades)
        parts.append("<h3>MISSION</h3>")
        parts.append(f"<p class='section-note'>{len(mission_rows)} personnel(s) — position « Mission ».</p>")
        parts.append("<table class='grid'><thead><tr>")
        for h in (
            "N°",
            "Grade",
            "Nom",
            "Structure",
            "Nature mission",
            "Lieu",
            "Réf. NDS",
            "Date départ",
        ):
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(export_rows(mission_rows)):
            d = missions.get(row.satellite_join_key())
            parts.append("<tr>")
            parts.append(f"<td>{i + 1}</td>")
            parts.append(td_val(row.grade))
            parts.append(td_val(row.noms))
            parts.append(td_val(row.structure))
            parts.append(td_val(d.nature if d else None))
            parts.append(td_val(d.lieu_mission if d else None))
            parts.append(td_val(d.refnds if d else None))
            parts.append(td_val(d.date_depart if d else None))
            parts.append("</tr>")
        parts.append("</tbody></table>")

        def _grade_banner_html(sheet: str, filt: list[ListeGeneraleRow]) -> None:
            gc = read_satellite_grade_columns_from_template(tpl, sheet)
            from sme_gend.services.excel_style_helpers import build_grade_abbrev_map
            pairs = satellite_grade_count_pairs(gc, filt, grade_abbrev_map=build_grade_abbrev_map(self._grades()))
            if not pairs:
                return
            parts.append("<p class='section-note'>Effectifs par grade (bandeau gabarit, ligne 2) :</p>")
            parts.append("<table class='grid'><thead><tr>")
            for g, _ in pairs:
                parts.append(f"<th>{html.escape(g)}</th>")
            parts.append("</tr></thead><tbody><tr>")
            for _, n in pairs:
                parts.append(f"<td>{int(n)}</td>")
            parts.append("</tr></tbody></table>")

        # --- MALADE ---
        malade_rows = sorted_rows_for_padmin(rows, PADMIN_MALADE, grades=grades)
        parts.append("<h3>MALADE</h3>")
        parts.append(f"<p class='section-note'>{len(malade_rows)} personnel(s) — position « Malade ».</p>")
        _grade_banner_html(SHEET_MALADE, malade_rows)
        parts.append("<table class='grid'><thead><tr>")
        for h in ("N°", "Grade", "Nom", "Structure", "Date constat", "Pathologie", "Obs."):
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(export_rows(malade_rows)):
            d = malades.get(row.satellite_join_key())
            parts.append("<tr>")
            parts.append(f"<td>{i + 1}</td>")
            parts.append(td_val(row.grade))
            parts.append(td_val(row.noms))
            parts.append(td_val(row.structure))
            parts.append(td_val(d.date_constat if d else None))
            parts.append(td_val(d.pathologie if d else None))
            parts.append(td_val(d.obs if d else None))
            parts.append("</tr>")
        parts.append("</tbody></table>")

        # --- DESERTEUR ---
        desert_rows = sorted_rows_for_padmin(rows, PADMIN_DESERTEUR, grades=grades)
        parts.append("<h3>DESERTEUR</h3>")
        parts.append(f"<p class='section-note'>{len(desert_rows)} personnel(s) — position « Déserteur ».</p>")
        _grade_banner_html(SHEET_DESERTEUR, desert_rows)
        parts.append("<table class='grid'><thead><tr>")
        for h in ("N°", "Grade", "Nom", "Structure", "Date désertion"):
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(export_rows(desert_rows)):
            d = deserteurs.get(row.satellite_join_key())
            parts.append("<tr>")
            parts.append(f"<td>{i + 1}</td>")
            parts.append(td_val(row.grade))
            parts.append(td_val(row.noms))
            parts.append(td_val(row.structure))
            parts.append(td_val(d.dat_desertion if d else None))
            parts.append("</tr>")
        parts.append("</tbody></table>")

        # --- STAGE-LOC ---
        sl_rows = sorted_rows_for_padmin(rows, PADMIN_STAGE_LOCAL, grades=grades)
        parts.append("<h3>STAGE-LOC</h3>")
        parts.append(f"<p class='section-note'>{len(sl_rows)} personnel(s) — position « StageLocal ».</p>")
        _grade_banner_html(SHEET_STAGE_LOC, sl_rows)
        parts.append("<table class='grid'><thead><tr>")
        for h in ("N°", "Grade", "Nom", "Structure", "Nature stage", "Lieu", "Dates début / fin"):
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(export_rows(sl_rows)):
            d = stages_loc.get(row.satellite_join_key())
            parts.append("<tr>")
            parts.append(f"<td>{i + 1}</td>")
            parts.append(td_val(row.grade))
            parts.append(td_val(row.noms))
            parts.append(td_val(row.structure))
            parts.append(td_val(d.nature_stage if d else None))
            parts.append(td_val(d.lieu_stage if d else None))
            parts.append(td_val(d.date_debut_fin if d else None))
            parts.append("</tr>")
        parts.append("</tbody></table>")

        # --- STAGE-EXT ---
        se_rows = sorted_rows_for_padmin(rows, PADMIN_STAGE_EX, grades=grades)
        parts.append("<h3>STAGE-EXT</h3>")
        parts.append(f"<p class='section-note'>{len(se_rows)} personnel(s) — position « StageEx ».</p>")
        _grade_banner_html(SHEET_STAGE_EXT, se_rows)
        parts.append("<table class='grid'><thead><tr>")
        for h in ("N°", "Grade", "Nom", "Structure", "Nature stage", "Lieu", "Dates début / fin"):
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr></thead><tbody>")
        for i, row in enumerate(export_rows(se_rows)):
            d = stages_ex.get(row.satellite_join_key())
            parts.append("<tr>")
            parts.append(f"<td>{i + 1}</td>")
            parts.append(td_val(row.grade))
            parts.append(td_val(row.noms))
            parts.append(td_val(row.structure))
            parts.append(td_val(d.nature_stage if d else None))
            parts.append(td_val(d.lieu_stage if d else None))
            parts.append(td_val(d.date_debut_fin if d else None))
            parts.append("</tr>")
        parts.append("</tbody></table>")

        parts.append("</body></html>")
        return "".join(parts)
