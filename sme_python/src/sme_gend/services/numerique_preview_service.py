"""Aperçu HTML — situation numérique (mêmes agrégats que l'export Excel)."""

from __future__ import annotations

import html
from pathlib import Path

from openpyxl import load_workbook

from sme_gend.config.settings import Settings
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.numerique_excel_export import (
    INDISPO_SHEET,
    collect_indispo_structure_meta,
    read_numerique_grades_from_template,
)
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


def _read_recap_mois_fige_cd(template_path: Path | None) -> list[int | None]:
    """Valeurs colonne CD (mois figé dans le gabarit) pour le récap variation, lignes 5–14."""
    out: list[int | None] = [None] * 10
    if template_path is None or not template_path.is_file():
        return out
    wb = load_workbook(template_path, read_only=True, data_only=True)
    try:
        if "NUMERIQUE" not in wb.sheetnames:
            return out
        ws = wb["NUMERIQUE"]
        for i, r in enumerate(range(5, 15)):
            v = ws.cell(r, 82).value  # CD
            if v is None:
                continue
            try:
                out[i] = int(v)
            except (TypeError, ValueError):
                out[i] = None
        return out
    finally:
        wb.close()


class NumeriquePreviewService:
    """Aperçus HTML alignés sur les calculs d'export (Access)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)

    def build_html_matrice(self) -> str:
        rows = self._repo.fetch_all()
        tpl = self._settings.template_numerique_path
        grades = read_numerique_grades_from_template(tpl)
        matrix = build_numerique_matrix(rows, grades)

        cat_labels = [name for name, _sl in NUMERIQUE_CATEGORY_SLICES] + ["Total"]
        cat_insc = sum_by_grade_category(matrix["inscrits"], grades)
        cat_pres = sum_by_grade_category(matrix["presents"], grades)
        cat_ind = category_indisponibles(matrix["inscrits"], matrix["presents"], grades)

        cd_vals = _read_recap_mois_fige_cd(tpl)
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

        title = "Situation numérique — feuille NUMERIQUE (aperçu complet)"
        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            _CSS,
            "</style></head><body>",
            f"<h2>{html.escape(title)}</h2>",
            f"<p class='meta'>Effectifs : {len(rows)} ligne(s) — source <b>ListeGenerale</b> (Access). "
            "« Présents » = position administrative contenant « présent » (comme la liste des présents). "
            "Les tableaux de droite du gabarit sont recalculés ici comme les formules Excel (sauf la colonne "
            "<b>mois figé</b>, lue depuis le gabarit si disponible).</p>",
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

        mois_courant = "Mois courant (Σ T)"
        mois_fige = "Mois figé (CD gabarit)"
        if tpl and tpl.is_file():
            wb = None
            try:
                wb = load_workbook(tpl, read_only=True, data_only=True)
                if "NUMERIQUE" in wb.sheetnames:
                    ws_m = wb["NUMERIQUE"]
                    t4 = ws_m.cell(4, 83).value  # CE4
                    t3 = ws_m.cell(4, 82).value  # CD4
                    if isinstance(t4, str) and t4.strip():
                        mois_courant = str(t4).strip()
                    if isinstance(t3, str) and t3.strip():
                        mois_fige = str(t3).strip()
            except OSError:
                pass
            finally:
                if wb is not None:
                    wb.close()

        parts.append("<h3>6. Récapitulatif de la variation des indisponibles</h3>")
        parts.append(
            "<p class='section-note'>Équivalent zone CB–CF : « mois courant » = totaux colonne T par rubrique ; "
            "« mois figé » = valeurs lues en colonne CD du gabarit (historique saisi dans le fichier).</p>"
        )
        parts.append("<table class='grid'><thead><tr><th>Position administrative</th>")
        parts.append(f"<th>{html.escape(mois_fige)}</th>")
        parts.append(f"<th>{html.escape(mois_courant)}</th>")
        parts.append("<th>Variation</th></tr></thead><tbody>")
        parts.extend(recap_rows_html)
        parts.append("</tbody></table>")

        parts.append("</body></html>")
        return "".join(parts)

    def build_html_indispo(self) -> str:
        rows = self._repo.fetch_all()
        tpl = self._settings.template_numerique_path
        if tpl is None or not tpl.is_file():
            return (
                "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
                + _CSS
                + "</style></head><body>"
                "<h2>Indisponibles par structure</h2>"
                "<p class='meta'><b>Gabarit Excel requis</b> pour définir les lignes structures "
                "(feuille « INDISPO PAR STRUCTURE bis »). Indiquez le chemin dans "
                "Fichier → Chemins des fichiers de données…</p></body></html>"
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
            "<h2>Indisponibles par structure</h2>",
            f"<p class='meta'>{len(rows)} ligne(s) en base — même logique que l'export Excel.</p>",
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
