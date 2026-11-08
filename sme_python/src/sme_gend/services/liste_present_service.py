"""Liste des présents — mise en page configurable + export fidèle à la maquette Excel."""

from __future__ import annotations
from sme_gend.services.export_progress import tracked_export, HTML_STEPS, export_phase, export_rows

import html
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field as dc_field, replace
from copy import deepcopy, copy
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.models.structure import StructureRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.subdivision_repository import SubdivisionRepository
from sme_gend.repositories.mysql_connection import mysql_read_snapshot, mysql_database_key
from sme_gend.services.excel_template_exporter import (
    export_liste_fidele,
    export_simple_fallback,
)
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.padmin_present import is_explicit_present_position
from sme_gend.services.resultat_format import (
    RESULTAT_DOCUMENT_TITLE,
    RESULTAT_HEADER_HTML,
    RESULTAT_HEADERS,
    row_to_resultat_cells,
)
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display
from sme_gend.ui.theme import PREVIEW_PAPER_TEXT

_SANS_STRUCTURE_KEY = "(Sans structure)"
_SANS_SUBDIVISION_KEY = "(Sans subdivision)"

_PREVIEW_TEXT = PREVIEW_PAPER_TEXT
_PREVIEW_BASE_RULES = (
    f"body, table, td, th, p, span, div, li, b, strong {{ color: {_PREVIEW_TEXT}; }}"
)
# Divisions (sec1/sec2) : bandeaux plus hauts ; subdivisions : ligne compacte.
_PREVIEW_MAQUETTE_ROW_CSS = f"""
        .sec1 td  {{ background: #bdd7ee; font-weight: bold; border: 1px solid #000;
                    padding: 7px 8px; font-size: 12pt; line-height: 1.3;
                    color: {_PREVIEW_TEXT}; }}
        .sec1-orphan td {{ background: #f8d7da; font-weight: bold; border: 1px solid #842029;
                          padding: 7px 8px; font-size: 12pt; line-height: 1.3; color: #842029; }}
        .sec2 td  {{ background: #dce6f1; font-weight: bold; text-align: center;
                    border: 1px solid #000; font-size: 11px; padding: 4px 6px;
                    line-height: 1.25; color: {_PREVIEW_TEXT}; }}
        .subdiv td, .subdiv-warn td {{
                     background: #dce6f1; font-weight: bold; text-align: center;
                     border: 1px solid #000; font-size: 11px; padding: 4px 6px;
                     line-height: 1.25; color: {_PREVIEW_TEXT}; }}
        .subdiv-warn td {{ background: #fff3cd; color: #856404; }}
"""


def _call_progress(cb: Callable[[int], None] | None, value: int) -> None:
    if cb is not None:
        cb(min(100, max(0, int(value))))


@dataclass
class PresentsAnomalies:
    """Présents avec données manquantes ou hors configuration de mise en page."""

    sans_subdivision: list[ListeGeneraleRow] = dc_field(default_factory=list)
    sans_structure: list[ListeGeneraleRow] = dc_field(default_factory=list)
    orphan_by_struct: dict[str, list[ListeGeneraleRow]] = dc_field(default_factory=dict)
    invalid_layout_structures: list[str] = dc_field(default_factory=list)
    positions_vides: list[ListeGeneraleRow] = dc_field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.positions_vides
            or self.sans_subdivision
            or self.sans_structure
            or self.orphan_by_struct
            or self.invalid_layout_structures
        )

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
    flat: list[SubdivisionFlatRow]                        # référentiel subdivisions


def _row_matches(row: ListeGeneraleRow, needle: str) -> bool:
    from sme_gend.ui.personnel_list_filters import free_text_matches
    return free_text_matches(row, needle)


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


@dataclass(frozen=True, repr=False)
class PresentsExportSnapshot:
    settings: object
    database: tuple
    search: str
    presence: str
    layout: object
    sections: list
    flat: list
    grades: list
    month: str | None
    anomalies: PresentsAnomalies | None = None


class ListePresentService:
    """Présents ; ordre des sections piloté par `liste_present_layout.json`."""

    def __init__(self, settings: Settings, layout_config: LayoutConfigService) -> None:
        self._settings = settings
        self._layout_config = layout_config
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)

    def validate_snapshot(self, snapshot, *, search_filter=None, presences_mode='present'):
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        if (snapshot.database != mysql_database_key(self._settings)
                or snapshot.month != resolve_export_mois(self._settings)
                or snapshot.search != (search_filter or '').strip()
                or snapshot.presence != presences_mode):
            raise ValueError("L’aperçu ne correspond plus à cette source, à ce mois ou à ces filtres. Actualisez-le.")
        self._validate_present_people(snapshot)

    @staticmethod
    def _require_present_mode(mode):
        if mode != 'present':
            raise ValueError('La Liste des présents accepte uniquement la position Présent. Actualisez l’aperçu.')

    @staticmethod
    def _validate_present_people(snapshot):
        ListePresentService._require_present_mode(snapshot.presence)
        if any(not is_explicit_present_position(row.position_admin)
               for _, _, _, blocks in snapshot.sections for _, people in blocks for row in people):
            raise ValueError('L’aperçu contient des positions autres que Présent. Actualisez-le avant de l’exporter.')

    def capture_snapshot(self, search_filter=None, presences_mode='present', *, on_progress=None):
        """Capture rows, layout and references in one read-only database instant."""
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        self._require_present_mode(presences_mode)
        settings = self._settings
        month = resolve_export_mois(settings)
        pinned = replace(settings, mysql_view_mois=month)
        reader = copy(self)
        reader._settings = pinned
        reader._layout_config = copy(self._layout_config)
        if isinstance(reader._layout_config, LayoutConfigService):
            reader._layout_config.rebind_settings(pinned)
        _call_progress(on_progress, 2)
        with mysql_read_snapshot(pinned):
            layout = reader._layout_config.load(read_only=True)
            _call_progress(on_progress, 5)
            def fetch_pct(local):
                _call_progress(on_progress, 5 + local*67//100)
            fetched = reader._fetch_all_data(search_filter=search_filter,
                presences_mode=presences_mode, on_progress=fetch_pct if on_progress else None)
            _call_progress(on_progress, 74)
            sections = reader.build_sections_data(layout, search_filter=search_filter,
                include_orphans=True, presences_mode=presences_mode, fetched=fetched)
            _call_progress(on_progress, 80)
            anomalies = reader.collect_anomalies(layout, fetched=fetched, presences_mode=presences_mode)
            grades = GradeRepository(pinned).fetch_all()
            snapshot = PresentsExportSnapshot(pinned, mysql_database_key(pinned),
                (search_filter or '').strip(), presences_mode, deepcopy(layout), deepcopy(sections),
                deepcopy(fetched.flat), deepcopy(grades), month, deepcopy(anomalies))
        return snapshot

    def _subdivisions_flat(self) -> list[SubdivisionFlatRow]:
        # Fresh hierarchy for each fetch; all render stages share the fetched snapshot.
        return self._subdivisions.fetch_flat_hierarchy()

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
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        self._require_present_mode(presences_mode)
        presents = self._liste.fetch_for_presence_mode('present_strict', mois_ym=resolve_export_mois(self._settings))
        presents = [row for row in presents if is_explicit_present_position(row.position_admin)]
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
        flat = self._subdivisions_flat()
        _call_progress(on_progress, 100)
        return _FetchedData(
            rows_all=presents,
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

        from sme_gend.services.liste_present_rubric import build_strict_subdiv_blocks

        return build_strict_subdiv_blocks(
            rows,
            subdivision_order,
            include_empty_rubrics=False,
        )

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
        db_codes = set(fetched.st_idx) if fetched is not None else self._layout_config.database_structure_codes()
        invalid_layout = sorted(layout_codes - db_codes)

        if fetched is None:
            fetched = self._fetch_all_data(presences_mode=presences_mode)

        # Anomalies are restricted to the same visible personnel as the output.
        unfiltered_by_struct: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
        for r in (r for group in fetched.by_struct.values() for r in group):
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

        return PresentsAnomalies(
            sans_subdivision,
            sans_structure,
            orphan_by_struct,
            invalid_layout,
            [r for group in fetched.by_struct.values() for r in group if not (r.position_admin or "").strip()],
        )

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

        Moteur : ``populate_liste`` (ALGORITHME_LISTE_PRESENTS.md) — matching RG,
        anti-doublons, CS-DPF fusionné, en-tête COMMANDEMENT sous compagnies RG.
        """
        self._require_present_mode(presences_mode)
        if layout is None:
            layout = self._layout_config.load()
        if fetched is None:
            fetched = self._fetch_all_data(
                search_filter=search_filter, presences_mode=presences_mode
            )

        needle = (search_filter or "").strip().lower()
        rows = (
            [r for r in fetched.presents if _row_matches(r, needle)]
            if needle
            else fetched.presents
        )
        rows = [row for row in rows if is_explicit_present_position(row.position_admin)]

        from sme_gend.services.liste_present_populate import populate_liste_sections

        sections, _missing, _assigned = populate_liste_sections(
            layout,
            rows,
            fetched.st_idx,
            include_orphans=include_orphans,
            resolve_subdivision_labels=self._layout_config.resolve_subdivision_order_labels,
        )
        return sections

    # Aperçu HTML complet jusqu'à ~10 000 lignes (effectifs nationaux).
    PREVIEW_MAX_DETAIL_ROWS = 10_000

    def _count_section_rows(
        self, sections: list[tuple[str, str, str, list[tuple[str, list]]]]
    ) -> int:
        return sum(
            len(people) for _, _, _, blocks in sections for _, people in blocks
        )

    def _build_preview_summary_html(
        self,
        *,
        sections: list[tuple[str, str, str, list[tuple[str, list]]]],
        mode_label: str,
        total_rows: int,
        ncols: int,
        anomalies: Any,
        month: str | None = None,
    ) -> str:
        import html

        css = f"""
        {_PREVIEW_BASE_RULES}
        body {{ margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif;
               font-size: 11px; background: #fff; color: {_PREVIEW_TEXT}; }}
        table.maquette {{ border-collapse: collapse; width: 100%; }}
        .title {{ font-size: 14pt; font-weight: bold; text-align: center; padding: 8px;
                 border: 1px solid #000; background: #fff8e1; color: {_PREVIEW_TEXT}; }}
        .hdr th {{ background: #d9e2f3; font-weight: bold; border: 1px solid #000; padding: 6px;
                   color: {_PREVIEW_TEXT}; }}
        .sum td {{ border: 1px solid #ccc; padding: 4px 8px; color: {_PREVIEW_TEXT}; }}
        .note {{ color: #5d4037; font-size: 10pt; padding: 10px; background: #fff3e0;
                border: 1px solid #ffcc80; margin-bottom: 12px; }}
        """
        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            css,
            "</style></head><body>",
            "<p class='note'><b>Aperçu synthétique</b> — ",
            f"{total_rows} présent(s) dépassent le seuil d'affichage détaillé "
            f"({self.PREVIEW_MAX_DETAIL_ROWS}). Les exports <b>PDF et Excel</b> contiennent la liste détaillée complète.</p>",
            self._presence_note(anomalies, month=month),
            "<table class='maquette'>",
            (
                f"<tr><td class='title' colspan='3'>"
                f"{html.escape(RESULTAT_DOCUMENT_TITLE)} — {html.escape(mode_label)}"
                f"</td></tr>"
            ),
            "<tr class='hdr'><th>Structure</th><th>Sous-titre</th><th>Présents</th></tr>",
        ]
        for title_main, title_sub, _code, blocks in sections:
            n_sec = sum(len(p) for _, p in blocks)
            parts.append(
                "<tr class='sum'>"
                f"<td>{html.escape(title_main)}</td>"
                f"<td>{html.escape(title_sub)}</td>"
                f"<td style='text-align:center'>{n_sec}</td></tr>"
            )
        parts.append(
            f"<tr class='sum'><td colspan='2'><b>Total</b></td>"
            f"<td style='text-align:center'><b>{total_rows}</b></td></tr>"
        )
        parts.append("</table></body></html>")
        return "".join(parts)

    @tracked_export(steps=HTML_STEPS)
    def build_preview_html(
        self,
        search_filter: str | None = None,
        presences_mode: str = "present",
        on_progress: Callable[[int], None] | None = None,
        *, on_snapshot=None,
    ) -> str:
        """Aperçu HTML aligné sur la même donnée que l'export.
        Une seule lecture DB pour sections + anomalies + hiérarchie subdivisions.

        ``on_progress`` : pourcentage global 0–100 pour feedback UI (optionnel).
        """
        snapshot = self.capture_snapshot(search_filter, presences_mode, on_progress=on_progress)
        if on_snapshot is not None:
            on_snapshot(snapshot)
        export_phase('render', detail='Construction de l’aperçu de la liste des présents')
        return self.render_snapshot_html(snapshot, on_progress=on_progress)

    @tracked_export(steps=HTML_STEPS)
    def render_snapshot_html(self, snapshot, *, detailed=False, on_progress=None, for_print=False):
        """Render the captured personnel without reading the database again."""
        self._validate_present_people(snapshot)
        sections, flat, grade_rows = snapshot.sections, snapshot.flat, snapshot.grades
        presences_mode = snapshot.presence
        anomalies = snapshot.anomalies or PresentsAnomalies()
        def pct(value):
            _call_progress(on_progress, value)
        ncols = len(RESULTAT_HEADERS)
        _mode_labels = {
            "present": "Présents uniquement",
            "stage_interne": "Stage interne uniquement",
            "les_deux": "Présents + Stage interne",
        }
        mode_label = _mode_labels.get(presences_mode, presences_mode)
        total_rows = self._count_section_rows(sections)
        export_phase('render', total=total_rows, detail='Lignes de la liste des présents')
        if not detailed and not for_print and total_rows > self.PREVIEW_MAX_DETAIL_ROWS:
            pct(100)
            return self._build_preview_summary_html(
                sections=sections,
                mode_label=mode_label,
                total_rows=total_rows,
                ncols=ncols,
                anomalies=anomalies,
                month=snapshot.month,
            )
        pct(82)

        css = f"""
        {_PREVIEW_BASE_RULES}
        body {{ margin: 12px; font-family: Calibri, "Segoe UI", Arial, sans-serif;
               font-size: 11px; background: #fff; color: {_PREVIEW_TEXT}; }}
        table.maquette {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
        .title {{ font-size: 16pt; font-weight: bold; text-align: center;
                 padding: 8px; border: 1px solid #000; background: #fff; color: {_PREVIEW_TEXT}; }}
        .mode-badge {{ font-size: 9pt; font-weight: normal; float: right;
                      background: #dbeafe; color: #1e3a5f; padding: 2px 8px;
                      border-radius: 4px; border: 1px solid #93c5fd; }}
        .hdr th {{ background: #d9e2f3; font-weight: bold; border: 1px solid #000;
                  padding: 4px 3px; text-align: center; vertical-align: middle;
                  color: {_PREVIEW_TEXT}; }}
        {_PREVIEW_MAQUETTE_ROW_CSS}
        .data-even td {{ background: #ffffff; border: 1px solid #9e9e9e;
                        padding: 2px 4px; vertical-align: top; color: {_PREVIEW_TEXT}; }}
        .data-odd  td {{ background: #f5f8ff; border: 1px solid #9e9e9e;
                        padding: 2px 4px; vertical-align: top; color: {_PREVIEW_TEXT}; }}
        .anomaly-box td {{ background: #fff3cd; border: 2px solid #f0ad4e;
                          padding: 8px 12px; font-size: 11px; color: {_PREVIEW_TEXT}; }}
        .total-row td {{ background: #d9e2f3; font-weight: bold; text-align: center;
                        border: 2px solid #000; padding: 8px; font-size: 12pt;
                        color: {_PREVIEW_TEXT}; }}
        .no-data td {{ text-align: center; padding: 40px; color: #888;
                      font-style: italic; font-size: 13px; }}
        @media print {{
            .anomaly-box td {{ background: #ffe !important; }}
            .sec1 td, .sec1-orphan td {{ background: #ccc !important; }}
            .sec2 td {{ background: #eee !important; }}
        }}
        """
        print_widths = (2, 3, 12, 7, 6, 4, 5, 5, 5, 7, 6, 5, 6, 6, 5, 5, 4, 7)
        if for_print:
            # Keep print CSS separate: Qt's rich-text parser can ignore rules
            # following the preview's unsupported @media block.
            css = _PREVIEW_BASE_RULES + """
            body { margin: 0; font-family: Tahoma, Arial, sans-serif; font-size: 7pt; }
            table.maquette { border-collapse: collapse; width: 100%; }
            .title { font-size: 12pt; font-weight: bold; text-align: center; margin: 3px 0 6px; }
            .note { font-size: 7pt; margin: 0 0 4px; }
            .hdr th { font-size: 7pt; padding: 2px; background: #d9e2f3;
                      font-weight: bold; border: 1px solid #000; text-align: center; }
            .data-even td { background: #ffffff; }
            .data-odd td { background: #f5f8ff; }
            .data-even td, .data-odd td { font-size: 7pt; padding: 2px;
                border: 1px solid #9e9e9e; vertical-align: top; }
            .sec1 td, .sec1-orphan td { background: #bdd7ee; font-weight: bold;
                font-size: 9pt; padding: 3px; border: 1px solid #000; }
            .sec1-orphan td { background: #f8d7da; }
            .sec2 td, .subdiv td, .subdiv-warn td { background: #dce6f1; font-weight: bold;
                text-align: center; border: 1px solid #000; font-size: 7pt; padding: 2px; }
            .subdiv-warn td { background: #fff3cd; }
            .total-row td { background: #d9e2f3; font-size: 9pt; font-weight: bold;
                text-align: center; border: 1px solid #000; padding: 4px; }
            .anomaly-box td { background: #fff3cd; font-size: 7pt; padding: 4px; border: 1px solid #000; }
            """

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
            css,
            "</style></head><body>",
            self._presence_note(anomalies, month=snapshot.month),
        ]
        if for_print:
            parts.append(f"<p class='title'>{html.escape(RESULTAT_DOCUMENT_TITLE)}</p>")
        parts.append("<table class='maquette'>")
        if not for_print:
            parts.extend((
                f"<tr><td class='title' colspan='{ncols}'>"
                f"{html.escape(RESULTAT_DOCUMENT_TITLE)}"
                f"</td></tr>",
                f"<tr><td colspan='{ncols}' style='height:8px;border:none;'></td></tr>"))

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
            if anomalies.invalid_layout_structures:
                codes = ", ".join(anomalies.invalid_layout_structures[:8])
                extra = (
                    f" … (+{len(anomalies.invalid_layout_structures) - 8})"
                    if len(anomalies.invalid_layout_structures) > 8
                    else ""
                )
                parts.append(
                    f"&bull; <b>{len(anomalies.invalid_layout_structures)} section(s) layout "
                    f"hors référentiel base</b> ({html.escape(codes)}{extra})"
                    f" — utilisez « Synchroniser avec la base » pour corriger<br>"
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
            if for_print:
                parts.append("</table><table class='maquette'>")

        if for_print:
            parts.append('<thead>')
        parts += ["<tr class='hdr'>"]
        header_column = 0
        for label, cspan in RESULTAT_HEADER_HTML:
            if for_print:
                label = {'N° interne': 'N° int.', 'Date de nomination au grade': 'Date grade',
                    "Date d'entrée en service": 'Date service', "N° decision d'engagement": 'Réf. eng.',
                    'Position administrative': 'Position admin.', "Subdivision d'arme": "Subdiv. d'arme", 'Structure': 'Struct.',
                    "Date et lieu d'obtention": 'Date / lieu diplôme'}.get(label,label)
            width = f" width='{sum(print_widths[header_column:header_column+cspan])}%'" if for_print else ''
            parts.append(f"<th colspan='{cspan}'{width}>{html.escape(label)}</th>")
            header_column += cspan
        parts.append("</tr>")
        if for_print:
            parts.append('</thead><tbody>')

        n_global = 0
        n_sections = len(sections)
        from sme_gend.services.liste_present_rubric import (
            SANS_SUBDIVISION_KEY,
            iter_section_rubrics,
        )

        for sec_idx, (title_main, title_sub, _code, blocks) in enumerate(sections):
            n_section = sum(len(people) for _, people in blocks)
            is_orphan_sec = title_main.startswith("⚠")
            sec1_cls = "sec1-orphan" if is_orphan_sec else "sec1"
            count_badge = (
                f"<span style='float:right;font-size:9pt;font-weight:normal;"
                f"background:rgba(0,0,0,.10);padding:1px 7px;border-radius:3px;'>"
                f"{n_section}&nbsp;présent(s)</span>"
            )
            if for_print:
                count_badge = f' — {n_section} présent(s)'
            rubrics = iter_section_rubrics(title_main, title_sub, blocks)
            for kind, payload in rubrics:
                if kind == "sec1":
                    parts.append(
                        f"<tr class='{sec1_cls}'><td colspan='{ncols}'>"
                        f"{html.escape(str(payload))}{count_badge}</td></tr>"
                    )
                elif kind == "sec2":
                    label = str(payload)
                    is_nosub = label == SANS_SUBDIVISION_KEY
                    subdiv_cls = "subdiv-warn" if is_nosub else "subdiv"
                    parts.append(
                        f"<tr class='{subdiv_cls}'><td colspan='{ncols}'>"
                        f"{html.escape(label)}</td></tr>"
                    )
                elif kind == "data":
                    n_interne = 0
                    for r in export_rows(payload, label='Liste des présents'):
                        n_global += 1
                        n_interne += 1
                        row_cls = "data-odd" if n_global % 2 else "data-even"
                        parts.append(f"<tr class='{row_cls}'>")
                        sub_cell = format_subdivision_arme_display(r.subdivision_arme, flat)
                        for column, cell in enumerate(row_to_resultat_cells(
                            r,
                            n_global,
                            n_interne,
                            subdivision_arme_display=sub_cell,
                            grades=grade_rows,
                        )):
                            width = f" width='{print_widths[column]}%'" if for_print else ''
                            parts.append(f"<td{width}>{html.escape(cell)}</td>")
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

        parts.append(('</tbody>' if for_print else '')+"</table></body></html>")
        pct(100)
        return "".join(parts)

    @staticmethod
    def _presence_note(anomalies, *, month=None):
        import html
        note = ('Personnel explicitement en position Présent ou Présent au corps pour le mois consulté. '
                'Les positions vides, À vérifier et toutes les autres positions sont exclues. '
                'Ce document ne constitue pas un pointage quotidien.')
        if month:
            note = f"Mois SME : {month}. " + note
        return "<p class='note'>" + html.escape(note) + "</p>"

    @tracked_export
    def export_excel(
        self,
        destination: Path,
        search_filter: str | None = None,
        presences_mode: str = "present",
        *, snapshot: PresentsExportSnapshot | None = None,
    ) -> None:
        """Export : fidèle à la maquette si `resultat.xlsx` + `liste_present_layout.json` disponibles."""
        dest = Path(destination)
        if snapshot is None:
            snapshot = self.capture_snapshot(search_filter, presences_mode)
        self.validate_snapshot(snapshot, search_filter=search_filter, presences_mode=presences_mode)
        layout, sections, flat, grade_rows = deepcopy((snapshot.layout, snapshot.sections, snapshot.flat, snapshot.grades))
        tpl = snapshot.settings.template_liste_present_path
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

        from sme_gend.services.safe_paths import atomic_destination
        from sme_gend.services.export_progress import export_phase
        export_phase('render', detail='Génération des rubriques et des lignes de présence')
        with atomic_destination(dest, temporary_suffix=".xlsx") as staged:
            if tpl is not None and tpl.is_file():
                export_liste_fidele(
                    tpl,
                    layout,
                    sections_disp,
                    staged,
                    subdivision_display_for_row=_sub_row,
                    grades=grade_rows,
                )
            else:
                export_simple_fallback(
                    staged,
                    sections_disp,
                    subdivision_display_for_row=_sub_row,
                    grades=grade_rows,
                )

            from openpyxl import load_workbook
            export_phase('write', detail='Ajout des contrôles de présence et enregistrement du classeur')
            workbook = load_workbook(staged)
            main_sheet = workbook[layout.output_sheet] if layout.output_sheet in workbook.sheetnames else workbook.worksheets[0]
            if snapshot.month:
                # Keep the model's title/styles; make its monthly scope visible
                # in print without overwriting its user-defined header text.
                header = main_sheet.oddHeader.center
                header.text = (header.text+'\n' if header.text else '') + f'Mois SME : {snapshot.month}'
            checks = workbook.create_sheet("Contrôles de présence")
            checks.append([(f'Mois SME : {snapshot.month}. ' if snapshot.month else '')+
                "Présence administrative du mois ; ce document ne constitue pas un pointage quotidien."])
            checks.append(['Uniquement Présent / Présent au corps ; positions vides, À vérifier et autres positions exclues.'])
            checks.append(["N° carrière", "Nom", "Structure", "Avertissement"])
            checks.column_dimensions['A'].width = 24
            checks.column_dimensions['B'].width = 42
            checks.column_dimensions['C'].width = 24
            checks.column_dimensions['D'].width = 58
            checks.freeze_panes = 'A4'
            workbook.save(staged)
            workbook.close()

        export_phase('finalize', detail='Historique de la liste des présents')
        from sme_gend.config.liste_generale_scope import resolve_export_mois
        from sme_gend.services.doc_gen_log import log_doc_generation

        total = sum(
            len(people)
            for _t1, _t2, _code, blocks in sections_disp
            for _sub, people in blocks
        )
        log_doc_generation(
            doc_type="liste_present", settings=snapshot.settings if snapshot is not None else self._settings,
            format="xlsx",
            count=total,
            persons=[f"{r.id} — {r.noms or ''}" for _, _, _, blocks in sections for _, people in blocks for r in people],
            dest_path=str(dest),
            mois=snapshot.month if snapshot is not None else resolve_export_mois(self._settings),
            meta={"sections": len(sections_disp), "presences_mode": presences_mode, "personnel_ids": [str(r.id) for _, _, _, blocks in sections for _, people in blocks for r in people], "positions_vides": sum(not (r.position_admin or "").strip() for _, _, _, blocks in sections for _, people in blocks for r in people)},
        )
