"""Liste des présents — mise en page configurable + export fidèle à la maquette Excel."""

from __future__ import annotations

import html
from collections import defaultdict
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.services.excel_template_exporter import (
    export_liste_fidele,
    export_simple_fallback,
)
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.resultat_format import RESULTAT_HEADERS, row_to_resultat_cells


def _is_present(padmin: str | None) -> bool:
    if not padmin:
        return False
    return "present" in padmin.strip().lower().replace("é", "e").replace("è", "e")


class ListePresentService:
    """Présents ; ordre des sections piloté par `liste_present_layout.json`."""

    def __init__(self, settings: Settings, layout_config: LayoutConfigService) -> None:
        self._settings = settings
        self._layout_config = layout_config
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)

    def _structure_index(self) -> dict[str, StructureRow]:
        return {s.code: s for s in self._structures.fetch_all()}

    def _presents_by_structure(self) -> dict[str, list[ListeGeneraleRow]]:
        presents = [r for r in self._liste.fetch_all() if _is_present(r.position_admin)]
        by_struct: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for row in presents:
            key = (row.structure or "").strip() or "(Sans structure)"
            by_struct[key].append(row)
        return dict(by_struct)

    def _blocks_for_rows(
        self,
        rows: list[ListeGeneraleRow],
        group_by_subdivision: bool,
    ) -> list[tuple[str, list[ListeGeneraleRow]]]:
        if not group_by_subdivision:
            rows = sorted(rows, key=lambda x: (x.ordre_tri or 0, x.noms or ""))
            return [("(Effectif)", rows)]

        by_sub: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for r in rows:
            sub_key = (r.subdivision_arme or "").strip() or "(Sans subdivision)"
            by_sub[sub_key].append(r)
        sub_keys = sorted(by_sub.keys(), key=lambda s: (s == "(Sans subdivision)", s))
        blocks: list[tuple[str, list[ListeGeneraleRow]]] = []
        for sub in sub_keys:
            lst = by_sub[sub]
            lst.sort(key=lambda x: (x.ordre_tri or 0, x.noms or ""))
            blocks.append((sub, lst))
        return blocks

    def build_sections_data(self, layout: ListeLayoutConfig | None = None) -> list[
        tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]
    ]:
        """
        Sections ordonnées selon la config (titres, subdivisions).
        Retourne : (title_main, title_sub, structure_code, blocks).
        """
        if layout is None:
            layout = self._layout_config.load()

        by_struct = self._presents_by_structure()
        st_idx = self._structure_index()
        out: list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]] = []

        for sec in sorted(layout.sections, key=lambda s: s.order):
            code = sec.structure_code.strip()
            if code not in by_struct:
                continue
            rows = by_struct[code]
            if code in st_idx:
                st = st_idx[code]
                title_main = (sec.title_main_override or st.designation or code).strip()
                title_main = title_main.upper()
                title_sub = (sec.title_sub_override or st.code or code).strip()
            else:
                title_main = (sec.title_main_override or code).strip().upper()
                title_sub = (sec.title_sub_override or code).strip()

            blocks = self._blocks_for_rows(rows, sec.group_by_subdivision)
            out.append((title_main, title_sub, code, blocks))

        return out

    def build_preview_html(self) -> str:
        """Aperçu HTML aligné sur la même donnée que l’export (ordre config)."""
        sections = self.build_sections_data()
        ncols = len(RESULTAT_HEADERS)

        css = """
        body { margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif; font-size: 11px; background: #fff; }
        table.maquette { border-collapse: collapse; width: 100%; table-layout: fixed; }
        .title { font-size: 16pt; font-weight: bold; text-align: center; padding: 8px; border: 1px solid #000; background: #fff; }
        .hdr th { background: #d9e2f3; font-weight: bold; border: 1px solid #000; padding: 4px 3px; text-align: center; vertical-align: middle; }
        .sec1 td { background: #e8e8e8; font-weight: bold; text-align: center; }
        .sec2 td { background: #f2f2f2; font-weight: bold; text-align: center; font-size: 11px; }
        .subdiv td { background: #e6f0ff; font-style: italic; text-align: left; padding-left: 8px; }
        .data td { border: 1px solid #808080; padding: 2px 4px; vertical-align: top; }
        .data tr:nth-child(even) td { background: #fafafa; }
        """

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            css,
            "</style></head><body>",
            "<table class='maquette'>",
            f"<tr><td class='title' colspan='{ncols}'>{html.escape('LISTE DES PRÉSENTS')}</td></tr>",
            f"<tr><td colspan='{ncols}' style='height:8px;border:none;'></td></tr>",
            "<tr class='hdr'>",
        ]
        for h in RESULTAT_HEADERS:
            parts.append(f"<th>{html.escape(h)}</th>")
        parts.append("</tr>")

        n_global = 0
        for title_main, title_sub, _code, blocks in sections:
            parts.append(
                f"<tr class='sec1'><td colspan='{ncols}'>{html.escape(title_main)}</td></tr>"
            )
            parts.append(
                f"<tr class='sec2'><td colspan='{ncols}'>{html.escape(title_sub)}</td></tr>"
            )
            for sub_label, personnes in blocks:
                sub_line = f"Subdivision d'arme : {sub_label}"
                parts.append(
                    f"<tr class='subdiv'><td colspan='{ncols}'>{html.escape(sub_line)}</td></tr>"
                )
                n_interne = 0
                for r in personnes:
                    n_global += 1
                    n_interne += 1
                    parts.append("<tr class='data'>")
                    for cell in row_to_resultat_cells(r, n_global, n_interne):
                        parts.append(f"<td>{html.escape(cell)}</td>")
                    parts.append("</tr>")

        parts.append("</table></body></html>")
        return "".join(parts)

    def export_excel(self, destination: Path) -> None:
        """Export : fidèle à la maquette si `resultat.xlsx` + `liste_present_layout.json` disponibles."""
        dest = Path(destination)
        layout = self._layout_config.load()
        sections = self.build_sections_data(layout)
        tpl = self._settings.template_liste_present_path

        if tpl is not None and tpl.is_file():
            export_liste_fidele(tpl, layout, sections, dest)
        else:
            export_simple_fallback(dest, sections)
