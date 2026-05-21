"""Liste des présents — mise en page configurable + export fidèle à la maquette Excel."""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Callable
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


def _call_progress(cb: Callable[[int], None] | None, value: int) -> None:
    if cb is not None:
        cb(min(100, max(0, int(value))))


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


# ─── Structures de données pré-calculées (cache d'un refresh) ────────────────

@dataclass
class _FetchedData:
    """Résultat d'une seule lecture DB partagée pour toutes les méthodes du refresh."""
    rows_all: list[ListeGeneraleRow]                    # toutes les lignes
    presents: list[ListeGeneraleRow]                    # filtrées par padmin+mode
    by_struct: dict[str, list[ListeGeneraleRow]]         # groupées par structure code
    st_idx: dict[str, StructureRow]                      # index référentiel structures
    flat: dict[str, str]                                  # hiérarchie subdivisions


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

    # ─── Lecture DB unique (optimisée) ────────────────────────────────────────

    def _fetch_all_data(
        self,
        *,
        search_filter: str | None = None,
        presences_mode: str = "present",
        on_progress: Callable[[int], None] | None = None,
    ) -> _FetchedData:
        """
        **Lecture unique** : une seule requête ListeGenerale + une seule requête TabStructure
        + une seule requête SubdivisionRepository. Toutes les méthodes du refresh partagent
        ce résultat pour éviter 3–4 aller-retours ODBC.

        ``on_progress`` reçoit des pourcentages **locaux** 0–100 (phase lecture seule).
        """
        _call_progress(on_progress, 5)
        rows_all = self._liste.fetch_all()
        _call_progress(on_progress, 22)
        presents = [r for r in rows_all if padmin_counts_as_present(r.position_admin, presences_mode)]
        _call_progress(on_progress, 38)
        needle = (search_filter or "").strip().lower()
        filtered = [r for r in presents if _row_matches(r, needle)] if needle else presents
        _call_progress(on_progress, 48)

        by_struct: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for row in filtered:
            key = (row.structure or "").strip() or _SANS_STRUCTURE_KEY
            by_struct[key].append(row)
        _call_progress(on_progress, 58)

        st_idx = {s.code: s for s in self._structures.fetch_all()}
        _call_progress(on_progress, 78)
        flat = SubdivisionRepository(self._settings).fetch_flat_hierarchy()
        _call_progress(on_progress, 100)
        return _FetchedData(
            rows_all=rows_all,
            presents=presents,
            by_struct=dict(by_struct),
            st_idx=st_idx,
            flat=flat,
        )

    # ─── Helpers sections / subdivisions ──────────────────────────────────────

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

    # ─── API publique ──────────────────────────────────────────────────────────

    def collect_anomalies(
        self,
        layout: ListeLayoutConfig | None = None,
        *,
        fetched: _FetchedData | None = None,
        presences_mode: str = "present",
    ) -> PresentsAnomalies:
        """Détecte les présents avec données manquantes ou hors config de mise en page."""
        if layout is None:
            layout = self._layout_config.load()
        layout_codes = {sec.structure_code.strip() for sec in layout.sections}

        if fetched is None:
            fetched = self._fetch_all_data(presences_mode=presences_mode)

        # Pour les anomalies on travaille sur TOUS les présents (sans filtre texte)
        unfiltered_by_struct: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for r in fetched.presents:
            key = (r.structure or "").strip() or _SANS_STRUCTURE_KEY
            unfiltered_by_struct[key].append(r)

        sans_subdivision: list[ListeGeneraleRow] = []
        sans_structure: list[ListeGeneraleRow] = []
        orphan_by_struct: dict[str, list[ListeGeneraleRow]] = {}

        for code, rows in unfiltered_by_struct.items():
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
        presences_mode: str = "present",
        fetched: _FetchedData | None = None,
    ) -> list[tuple[str, str, str, list[tuple[str, list[ListeGeneraleRow]]]]]:
        """
        Sections ordonnées selon la config (titres, subdivisions).
        Retourne : (title_main, title_sub, structure_code, blocks).
        """
        if layout is None:
            layout = self._layout_config.load()
        if fetched is None:
            fetched = self._fetch_all_data(
                search_filter=search_filter, presences_mode=presences_mode
            )

        by_struct = fetched.by_struct
        st_idx = fetched.st_idx
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

    def build_preview_html(
        self,
        search_filter: str | None = None,
        presences_mode: str = "present",
        on_progress: Callable[[int], None] | None = None,
    ) -> str:
        """Aperçu HTML aligné sur la même donnée que l'export.
        Une seule lecture DB pour sections + anomalies + hiérarchie subdivisions.

        ``on_progress`` : pourcentage global 0–100 pour feedback UI (optionnel).
        """
        def pct(p: int) -> None:
            _call_progress(on_progress, p)

        pct(2)
        layout = self._layout_config.load()
        pct(5)

        def fetch_pct(local: int) -> None:
            """Mappe la phase lecture (0–100 locale) vers la plage 5–72 % globale."""
            pct(5 + (local * 67 // 100))

        fetched = self._fetch_all_data(
            search_filter=search_filter,
            presences_mode=presences_mode,
            on_progress=fetch_pct if on_progress else None,
        )
        pct(74)

        sections = self.build_sections_data(
            layout,
            search_filter=search_filter,
            include_orphans=True,
            presences_mode=presences_mode,
            fetched=fetched,
        )
        pct(80)
        anomalies = self.collect_anomalies(layout, fetched=fetched, presences_mode=presences_mode)
        flat = fetched.flat  # déjà chargé, pas de 3e requête
        ncols = len(RESULTAT_HEADERS)
        pct(82)

        # ── Badge de mode ─────────────────────────────────────────────────────
        _mode_labels = {
            "present": "Présents uniquement",
            "stage_interne": "Stage interne uniquement",
            "les_deux": "Présents + Stage interne",
        }
        mode_label = _mode_labels.get(presences_mode, presences_mode)

        css = """
        body { margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif;
               font-size: 11px; background: #fff; }
        table.maquette { border-collapse: collapse; width: 100%; table-layout: fixed; }
        .title { font-size: 16pt; font-weight: bold; text-align: center;
                 padding: 8px; border: 1px solid #000; background: #fff; }
        .mode-badge { font-size: 9pt; font-weight: normal; float: right;
                      background: #dbeafe; color: #1e3a5f; padding: 2px 8px;
                      border-radius: 4px; border: 1px solid #93c5fd; }
        .hdr th { background: #d9e2f3; font-weight: bold; border: 1px solid #000;
                  padding: 4px 3px; text-align: center; vertical-align: middle; }
        .sec1 td  { background: #bdd7ee; font-weight: bold; border: 1px solid #000;
                    padding: 4px 6px; }
        .sec1-orphan td { background: #f8d7da; font-weight: bold; border: 1px solid #842029;
                          padding: 4px 6px; color: #842029; }
        .sec2 td  { background: #dce6f1; font-weight: bold; text-align: center;
                    border: 1px solid #000; font-size: 11px; padding: 2px 4px; }
        .subdiv td { background: #e6f0ff; font-style: italic;
                     border: 1px solid #b0c4de; padding: 2px 8px; }
        .subdiv-warn td { background: #fff3cd; font-style: italic;
                          border: 1px solid #f0ad4e; padding: 2px 8px; color: #856404; }
        .data-even td { background: #ffffff; border: 1px solid #9e9e9e;
                        padding: 2px 4px; vertical-align: top; }
        .data-odd  td { background: #f5f8ff; border: 1px solid #9e9e9e;
                        padding: 2px 4px; vertical-align: top; }
        .anomaly-box td { background: #fff3cd; border: 2px solid #f0ad4e;
                          padding: 8px 12px; font-size: 11px; }
        .total-row td { background: #d9e2f3; font-weight: bold; text-align: center;
                        border: 2px solid #000; padding: 8px; font-size: 12pt; }
        .no-data td { text-align: center; padding: 40px; color: #888;
                      font-style: italic; font-size: 13px; }
        @media print {
            .anomaly-box td { background: #ffe !important; }
            .sec1 td, .sec1-orphan td { background: #ccc !important; }
            .sec2 td { background: #eee !important; }
        }
        """

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            css,
            "</style></head><body>",
            "<table class='maquette'>",
            (
                f"<tr><td class='title' colspan='{ncols}'>"
                f"{html.escape(RESULTAT_DOCUMENT_TITLE)}"
                f"<span class='mode-badge'>{html.escape(mode_label)}</span>"
                f"</td></tr>"
            ),
            f"<tr><td colspan='{ncols}' style='height:8px;border:none;'></td></tr>",
        ]

        if anomalies.has_issues:
            parts.append(f"<tr class='anomaly-box'><td colspan='{ncols}'>")
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
                total_orphans = anomalies.total_orphans
                codes = ", ".join(sorted(anomalies.orphan_by_struct.keys()))
                parts.append(
                    f"&bull; <b>{total_orphans} personne(s) dans des structures hors configuration</b>"
                    f" ({html.escape(codes)}) — affichées en fin de liste (fond rouge)<br>"
                )
            if anomalies.sans_subdivision:
                n = len(anomalies.sans_subdivision)
                parts.append(
                    f"&bull; <b>{n} personne(s) sans subdivision d'arme</b>"
                    f" — apparaissent sous « (Sans subdivision) » (fond jaune)<br>"
                )
            parts.append("</td></tr>")
            parts.append(f"<tr><td colspan='{ncols}' style='height:4px;border:none;'></td></tr>")

        parts += ["<tr class='hdr'>"]
        for label, cspan in RESULTAT_HEADER_HTML:
            parts.append(f"<th colspan='{cspan}'>{html.escape(label)}</th>")
        parts.append("</tr>")

        n_global = 0
        n_sections = len(sections)
        for sec_idx, (title_main, title_sub, _code, blocks) in enumerate(sections):
            n_section = sum(len(people) for _, people in blocks)
            is_orphan_sec = title_main.startswith("⚠")
            sec1_cls = "sec1-orphan" if is_orphan_sec else "sec1"
            count_badge = (
                f"<span style='float:right;font-size:9pt;font-weight:normal;"
                f"background:rgba(0,0,0,.10);padding:1px 7px;border-radius:3px;'>"
                f"{n_section}&nbsp;présent(s)</span>"
            )
            parts.append(
                f"<tr class='{sec1_cls}'><td colspan='{ncols}'>"
                f"{html.escape(title_main)}{count_badge}</td></tr>"
            )
            parts.append(
                f"<tr class='sec2'><td colspan='{ncols}'>{html.escape(title_sub)}</td></tr>"
            )
            for sub_label, personnes in blocks:
                is_nosub = sub_label == _SANS_SUBDIVISION_KEY
                sub_disp = format_subdivision_arme_display(sub_label, flat)
                subdiv_cls = "subdiv-warn" if is_nosub else "subdiv"
                n_sub = len(personnes)
                sub_badge = (
                    f"<span style='float:right;font-size:9pt;opacity:.65;'>"
                    f"{n_sub}&nbsp;pers.</span>"
                )
                parts.append(
                    f"<tr class='{subdiv_cls}'><td colspan='{ncols}'>"
                    f"{html.escape(sub_disp)}{sub_badge}</td></tr>"
                )
                n_interne = 0
                for r in personnes:
                    n_global += 1
                    n_interne += 1
                    row_cls = "data-odd" if n_global % 2 else "data-even"
                    parts.append(f"<tr class='{row_cls}'>")
                    sub_cell = format_subdivision_arme_display(r.subdivision_arme, flat)
                    for cell in row_to_resultat_cells(
                        r,
                        n_global,
                        n_interne,
                        subdivision_arme_display=sub_cell,
                    ):
                        parts.append(f"<td>{html.escape(cell)}</td>")
                    parts.append("</tr>")

            if on_progress is not None and n_sections > 0:
                pct(82 + (sec_idx + 1) * 17 // n_sections)

        if n_global == 0:
            parts.append(
                f"<tr class='no-data'><td colspan='{ncols}'>"
                "Aucun présent dans les données actuelles.<br>"
                "<small>Vérifiez les positions administratives (colonne Padmin) "
                "et la configuration de mise en page.</small>"
                "</td></tr>"
            )
        else:
            parts.append(
                f"<tr class='total-row'><td colspan='{ncols}'>"
                f"TOTAL GÉNÉRAL — {n_global} présent(s)"
                f"</td></tr>"
            )

        parts.append("</table></body></html>")
        pct(100)
        return "".join(parts)

    def export_excel(
        self,
        destination: Path,
        search_filter: str | None = None,
        presences_mode: str = "present",
    ) -> None:
        """Export : fidèle à la maquette si `resultat.xlsx` + `liste_present_layout.json` disponibles."""
        dest = Path(destination)
        layout = self._layout_config.load()
        fetched = self._fetch_all_data(
            search_filter=search_filter,
            presences_mode=presences_mode,
        )
        sections = self.build_sections_data(
            layout,
            search_filter=search_filter,
            presences_mode=presences_mode,
            fetched=fetched,
        )
        tpl = self._settings.template_liste_present_path
        flat = fetched.flat
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
