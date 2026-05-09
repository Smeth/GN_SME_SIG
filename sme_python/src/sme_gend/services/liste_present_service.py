"""Liste des présents — mise en page configurable + export fidèle à la maquette Excel."""

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.subdivision_repository import SubdivisionRepository
from sme_gend.services.excel_template_exporter import (
    export_liste_fidele,
    export_simple_fallback,
)
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.padmin_present import padmin_counts_as_present
from sme_gend.services.resultat_format import (
    RESULTAT_DOCUMENT_TITLE,
    RESULTAT_HEADER_HTML,
    RESULTAT_HEADERS,
    row_to_resultat_cells,
)
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

_SANS_STRUCTURE_KEY = "(Sans structure)"
_SANS_SUBDIVISION_KEY = "(Sans subdivision)"


@dataclass
class PresentsAnomalies:
    """Présents avec données manquantes ou hors configuration de mise en page."""

    sans_subdivision: list[ListeGeneraleRow] = dc_field(default_factory=list)
    sans_structure: list[ListeGeneraleRow] = dc_field(default_factory=list)
    orphan_by_struct: dict[str, list[ListeGeneraleRow]] = dc_field(default_factory=dict)

    @property
    def has_issues(self) -> bool:
        return bool(self.sans_subdivision or self.sans_structure or self.orphan_by_struct)

    @property
    def total_orphans(self) -> int:
        return sum(len(v) for v in self.orphan_by_struct.values())


def _row_matches(row: ListeGeneraleRow, needle: str) -> bool:
    """Même logique que la liste générale (recherche texte)."""
    parts = [
        row.id,
        row.noms or "",
        row.grade or "",
        row.position_admin or "",
        row.structure or "",
        row.subdivision_arme or "",
        row.fonction or "",
        row.matricule_militaire or "",
        row.matricule_solde or "",
        row.obs or "",
        row.diplome or "",
    ]
    blob = " ".join(parts).lower()
    return needle in blob


def _default_subdivision_key_order(keys: list[str]) -> list[str]:
    """Tri historique : alphabétique, « (Sans subdivision) » en dernier."""
    return sorted(keys, key=lambda s: (s == _SANS_SUBDIVISION_KEY, s))


def _ordered_subdivision_keys(
    present_keys: list[str],
    subdivision_order: list[str] | None,
) -> list[str]:
    """Applique la liste explicite puis complète avec le tri par défaut pour les libellés restants."""
    if not subdivision_order:
        return _default_subdivision_key_order(present_keys)
    by_key = set(present_keys)
    # Casse Access / saisie JSON : associer une clé normalisée à la valeur réelle des effectifs
    fold_to_actual: dict[str, str] = {}
    for k in present_keys:
        f = k.casefold()
        if f not in fold_to_actual:
            fold_to_actual[f] = k
    seen: set[str] = set()
    out: list[str] = []
    for label in subdivision_order:
        lbl = (label or "").strip()
        if not lbl:
            continue
        actual: str | None = None
        if lbl in by_key:
            actual = lbl
        else:
            cand = fold_to_actual.get(lbl.casefold())
            if cand is not None:
                actual = cand
        if actual is None or actual in seen:
            continue
        out.append(actual)
        seen.add(actual)
    remaining = [k for k in present_keys if k not in seen]
    out.extend(_default_subdivision_key_order(remaining))
    return out


class ListePresentService:
    """Présents ; ordre des sections piloté par `liste_present_layout.json`."""

    def __init__(self, settings: Settings, layout_config: LayoutConfigService) -> None:
        self._settings = settings
        self._layout_config = layout_config
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)

    def _structure_index(self) -> dict[str, StructureRow]:
        return {s.code: s for s in self._structures.fetch_all()}

    def _presents_by_structure(self, search_filter: str | None = None) -> dict[str, list[ListeGeneraleRow]]:
        presents = [r for r in self._liste.fetch_all() if padmin_counts_as_present(r.position_admin)]
        needle = (search_filter or "").strip().lower()
        if needle:
            presents = [r for r in presents if _row_matches(r, needle)]
        by_struct: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for row in presents:
            key = (row.structure or "").strip() or _SANS_STRUCTURE_KEY
            by_struct[key].append(row)
        return dict(by_struct)

    def _blocks_for_rows(
        self,
        rows: list[ListeGeneraleRow],
        group_by_subdivision: bool,
        subdivision_order: list[str] | None = None,
    ) -> list[tuple[str, list[ListeGeneraleRow]]]:
        if not group_by_subdivision:
            rows = sorted(rows, key=lambda x: (x.ordre_tri or 0, x.noms or ""))
            return [("(Effectif)", rows)]

        by_sub: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for r in rows:
            sub_key = (r.subdivision_arme or "").strip() or "(Sans subdivision)"
            by_sub[sub_key].append(r)
        sub_keys = _ordered_subdivision_keys(list(by_sub.keys()), subdivision_order)
        blocks: list[tuple[str, list[ListeGeneraleRow]]] = []
        for sub in sub_keys:
            lst = by_sub[sub]
            lst.sort(key=lambda x: (x.ordre_tri or 0, x.noms or ""))
            blocks.append((sub, lst))
        return blocks

    def collect_anomalies(self, layout: ListeLayoutConfig | None = None) -> PresentsAnomalies:
        """Détecte les présents avec données manquantes ou hors configuration de mise en page."""
        if layout is None:
            layout = self._layout_config.load()
        layout_codes = {sec.structure_code.strip() for sec in layout.sections}
        by_struct = self._presents_by_structure()

        sans_subdivision: list[ListeGeneraleRow] = []
        sans_structure: list[ListeGeneraleRow] = []
        orphan_by_struct: dict[str, list[ListeGeneraleRow]] = {}

        for code, rows in by_struct.items():
            is_sans_struct = code == _SANS_STRUCTURE_KEY
            is_orphan = not is_sans_struct and code not in layout_codes
            for r in rows:
                if not (r.subdivision_arme or "").strip():
                    sans_subdivision.append(r)
                if is_sans_struct:
                    sans_structure.append(r)
                elif is_orphan:
                    orphan_by_struct.setdefault(code, []).append(r)

        return PresentsAnomalies(sans_subdivision, sans_structure, orphan_by_struct)

    def build_sections_data(
        self,
        layout: ListeLayoutConfig | None = None,
        *,
        search_filter: str | None = None,
        include_orphans: bool = False,
    ) -> list[
        tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]
    ]:
        """
        Sections ordonnées selon la config (titres, subdivisions).
        Retourne : (title_main, title_sub, structure_code, blocks).
        Données : table Access ListeGenerale, filtre présents / stage interne (Padmin) ;
        `search_filter` restreint l’aperçu / export aux lignes correspondantes.
        Si `include_orphans` est True, les personnes dont la structure est absente de la
        configuration apparaissent à la fin dans des sections marquées ⚠.
        """
        if layout is None:
            layout = self._layout_config.load()

        by_struct = self._presents_by_structure(search_filter)
        st_idx = self._structure_index()
        out: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]] = []
        covered_codes: set[str] = set()

        for sec in sorted(layout.sections, key=lambda s: s.order):
            code = sec.structure_code.strip()
            covered_codes.add(code)
            if code not in by_struct:
                continue
            rows = by_struct[code]
            if code in st_idx:
                st = st_idx[code]
                title_main = (sec.title_main_override or st.designation or code).strip().upper()
                title_sub = (sec.title_sub_override or st.code or code).strip()
            else:
                title_main = (sec.title_main_override or code).strip().upper()
                title_sub = (sec.title_sub_override or code).strip()

            blocks = self._blocks_for_rows(
                rows,
                sec.group_by_subdivision,
                sec.subdivision_order,
            )
            out.append((title_main, title_sub, code, blocks))

        if include_orphans:
            for code in sorted(by_struct.keys()):
                if code in covered_codes:
                    continue
                rows = by_struct[code]
                if code == _SANS_STRUCTURE_KEY:
                    title_main = "⚠ STRUCTURE NON RENSEIGNÉE"
                else:
                    title_main = f"⚠ HORS CONFIGURATION : {code}"
                blocks = self._blocks_for_rows(rows, group_by_subdivision=True, subdivision_order=None)
                out.append((title_main, code, code, blocks))

        return out

    def build_preview_html(self, search_filter: str | None = None) -> str:
        """Aperçu HTML aligné sur la même donnée que l’export (ordre config)."""
        layout = self._layout_config.load()
        sections = self.build_sections_data(layout, search_filter=search_filter, include_orphans=True)
        anomalies = self.collect_anomalies(layout)
        flat = SubdivisionRepository(self._settings).fetch_flat_hierarchy()
        ncols = len(RESULTAT_HEADERS)

        css = """
        body { margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif; font-size: 11px; background: #fff; }
        table.maquette { border-collapse: collapse; width: 100%; table-layout: fixed; }
        .title { font-size: 16pt; font-weight: bold; text-align: center; padding: 8px; border: 1px solid #000; background: #fff; }
        .hdr th { background: #d9e2f3; font-weight: bold; border: 1px solid #000; padding: 4px 3px; text-align: center; vertical-align: middle; }
        .sec1 td { background: #e8e8e8; font-weight: bold; text-align: center; }
        .sec1-orphan td { background: #f8d7da; font-weight: bold; text-align: center; color: #842029; }
        .sec2 td { background: #f2f2f2; font-weight: bold; text-align: center; font-size: 11px; }
        .subdiv td { background: #e6f0ff; font-style: italic; text-align: left; padding-left: 8px; }
        .subdiv-warn td { background: #fff3cd; font-style: italic; text-align: left; padding-left: 8px; color: #856404; }
        .data td { border: 1px solid #808080; padding: 2px 4px; vertical-align: top; }
        .data tr:nth-child(even) td { background: #fafafa; }
        .anomaly-box td { background: #fff3cd; border: 2px solid #f0ad4e; padding: 8px 12px; font-size: 11px; }
        """

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset=’utf-8’><style>",
            css,
            "</style></head><body>",
            "<table class=’maquette’>",
            f"<tr><td class=’title’ colspan=’{ncols}’>{html.escape(RESULTAT_DOCUMENT_TITLE)}</td></tr>",
            f"<tr><td colspan=’{ncols}’ style=’height:8px;border:none;’></td></tr>",
        ]

        if anomalies.has_issues:
            parts.append(f"<tr class=’anomaly-box’><td colspan=’{ncols}’>")
            parts.append("<b>&#9888; Anomalies détectées</b><br>")
            if anomalies.sans_structure:
                n = len(anomalies.sans_structure)
                noms = ", ".join(r.noms or "?" for r in anomalies.sans_structure[:5])
                if n > 5:
                    noms += f" … (+{n - 5})"
                parts.append(
                    f"&bull; <b>{n} personne(s) sans structure renseignée</b>"
                    f" : {html.escape(noms)}<br>"
                )
            if anomalies.orphan_by_struct:
                total = anomalies.total_orphans
                codes = ", ".join(sorted(anomalies.orphan_by_struct.keys()))
                parts.append(
                    f"&bull; <b>{total} personne(s) dans des structures hors configuration</b>"
                    f" ({html.escape(codes)}) — affichées en fin de liste (fond rouge)<br>"
                )
            if anomalies.sans_subdivision:
                n = len(anomalies.sans_subdivision)
                parts.append(
                    f"&bull; <b>{n} personne(s) sans subdivision d’arme</b>"
                    f" — apparaissent sous « (Sans subdivision) » (fond jaune)<br>"
                )
            parts.append("</td></tr>")
            parts.append(f"<tr><td colspan=’{ncols}’ style=’height:4px;border:none;’></td></tr>")

        parts += ["<tr class=’hdr’>"]
        for label, cspan in RESULTAT_HEADER_HTML:
            parts.append(f"<th colspan=’{cspan}’>{html.escape(label)}</th>")
        parts.append("</tr>")

        n_global = 0
        for title_main, title_sub, _code, blocks in sections:
            is_orphan_sec = title_main.startswith("⚠")
            sec1_cls = "sec1-orphan" if is_orphan_sec else "sec1"
            parts.append(
                f"<tr class=’{sec1_cls}’><td colspan=’{ncols}’>{html.escape(title_main)}</td></tr>"
            )
            parts.append(
                f"<tr class=’sec2’><td colspan=’{ncols}’>{html.escape(title_sub)}</td></tr>"
            )
            for sub_label, personnes in blocks:
                is_nosub = sub_label == _SANS_SUBDIVISION_KEY
                sub_disp = format_subdivision_arme_display(sub_label, flat)
                subdiv_cls = "subdiv-warn" if is_nosub else "subdiv"
                parts.append(
                    f"<tr class=’{subdiv_cls}’><td colspan=’{ncols}’>{html.escape(sub_disp)}</td></tr>"
                )
                n_interne = 0
                for r in personnes:
                    n_global += 1
                    n_interne += 1
                    parts.append("<tr class=’data’>")
                    sub_cell = format_subdivision_arme_display(r.subdivision_arme, flat)
                    for cell in row_to_resultat_cells(
                        r,
                        n_global,
                        n_interne,
                        subdivision_arme_display=sub_cell,
                    ):
                        parts.append(f"<td>{html.escape(cell)}</td>")
                    parts.append("</tr>")

        parts.append("</table></body></html>")
        return "".join(parts)

    def export_excel(self, destination: Path, search_filter: str | None = None) -> None:
        """Export : fidèle à la maquette si `resultat.xlsx` + `liste_present_layout.json` disponibles."""
        dest = Path(destination)
        layout = self._layout_config.load()
        sections = self.build_sections_data(layout, search_filter=search_filter)
        tpl = self._settings.template_liste_present_path
        flat = SubdivisionRepository(self._settings).fetch_flat_hierarchy()
        sections_disp = [
            (
                title_main,
                title_sub,
                code,
                [
                    (format_subdivision_arme_display(raw_sub, flat), people)
                    for raw_sub, people in blocks
                ],
            )
            for title_main, title_sub, code, blocks in sections
        ]

        def _sub_row(pr: ListeGeneraleRow) -> str:
            return format_subdivision_arme_display(pr.subdivision_arme, flat)

        if tpl is not None and tpl.is_file():
            export_liste_fidele(
                tpl,
                layout,
                sections_disp,
                dest,
                subdivision_display_for_row=_sub_row,
            )
        else:
            export_simple_fallback(
                dest,
                sections_disp,
                subdivision_display_for_row=_sub_row,
            )
