"""Organisation de la liste des présents, sans chargement du personnel."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace
from html import escape
from urllib.parse import quote

from sme_gend.config.liste_generale_scope import resolve_export_mois
from sme_gend.config.settings import Settings
from sme_gend.models.liste_layout import ListeLayoutConfig
from sme_gend.models.structure import StructureRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.repositories.mysql_connection import mysql_database_key, mysql_read_snapshot
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.subdivision_repository import SubdivisionRepository
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_populate import NO_SUBDIVISION_STRUCTURES
from sme_gend.services.liste_present_rg_template import is_rg_compagnie
from sme_gend.services.search_text import normalize_search_text
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display


@dataclass(frozen=True)
class PresentsOutlineSnapshot:
    settings: Settings
    database: tuple
    month: str | None
    layout: ListeLayoutConfig
    structures: list[StructureRow]
    flat: list[SubdivisionFlatRow]
    subdivisions: dict[str, list[str]]


def capture_outline(service, *, on_progress=None) -> PresentsOutlineSnapshot:
    """Read only the layout and its references in one database snapshot."""
    def progress(value):
        if on_progress is not None:
            on_progress(value)

    settings = service._settings
    month = resolve_export_mois(settings)
    pinned = replace(settings, mysql_view_mois=month)
    layout_service = copy(service._layout_config)
    if isinstance(layout_service, LayoutConfigService):
        layout_service.rebind_settings(pinned)
    progress(0)
    with mysql_read_snapshot(pinned):
        layout = layout_service.load(read_only=True)
        progress(20)
        structures = StructureRepository(pinned).fetch_all()
        progress(40)
        flat = SubdivisionRepository(pinned).fetch_flat_hierarchy()
        progress(60)
        subdivisions = {}
        sections = sorted(layout.sections, key=lambda section: section.order)
        for index, section in enumerate(sections):
            # Match populate_liste_sections: explicit template order takes
            # precedence; otherwise resolve labels from the RSA references.
            if not section.group_by_subdivision or section.structure_code.strip() in NO_SUBDIVISION_STRUCTURES:
                labels = []
            elif section.subdivision_order:
                labels = [label for label in section.subdivision_order if (label or '').strip()]
            else:
                labels = layout_service.resolve_subdivision_order_labels(section) or []
            subdivisions[section.id] = list(labels)
            progress(60 + (index + 1) * 35 // max(1, len(sections)))
        snapshot = PresentsOutlineSnapshot(
            pinned, mysql_database_key(pinned), month, deepcopy(layout),
            deepcopy(structures), deepcopy(flat), deepcopy(subdivisions))
    progress(100)
    return snapshot


def render_outline(snapshot: PresentsOutlineSnapshot, search_filter=None) -> str:
    """Render editable rubric links, without personnel rows or counts."""
    structures = {row.code: row for row in snapshot.structures}
    needle = normalize_search_text((search_filter or '').strip())
    displayed_labels = {}
    def display_label(raw):
        if raw not in displayed_labels:
            displayed_labels[raw] = format_subdivision_arme_display(raw, snapshot.flat)
        return displayed_labels[raw]
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>",
        "body{font-family:Calibri,'Segoe UI',Arial,sans-serif;font-size:11pt;color:#17233b;margin:12px;}",
        "table{border-collapse:collapse;width:100%;}td{padding:7px 10px;border:1px solid #ced5df;}",
        ".structure td{background:#bdd7ee;font-weight:bold;font-size:12pt;}",
        ".subdivision td{background:#edf3fa;padding-left:28px;}",
        ".secondary td{background:#dce6f1;}.warning td{background:#fff3cd;color:#8a4700;}",
        "a{color:#17233b;text-decoration:none;}.note{color:#596577;}h2{font-size:14pt;margin:0 0 8px;}",
        "</style></head><body><h2>Organisation de la liste des présents</h2>",
    ]
    if snapshot.month:
        parts.append(f"<p class='note'>Mois SME : {escape(snapshot.month)}</p>")
    parts.append("<table width='100%'>")
    shown = 0
    for section in sorted(snapshot.layout.sections, key=lambda item: item.order):
        code = section.structure_code.strip()
        structure = structures.get(code)
        title = (section.title_main_override or (structure.designation if structure else code) or code).strip().upper()
        subtitle = (section.title_sub_override or (structure.code if structure else code) or code).strip()
        labels = snapshot.subdivisions.get(section.id, [])
        section_matches = not needle or any(needle in normalize_search_text(value) for value in (code, title, subtitle))
        visible = [(index, label) for index, label in enumerate(labels)
                   if section_matches or needle in normalize_search_text(label)
                   or needle in normalize_search_text(display_label(label))]
        if not section_matches and not visible:
            continue
        shown += 1
        section_url = 'section:' + quote(section.id, safe='')
        parts.append(f"<tr class='structure'><td><a href='{escape(section_url, quote=True)}'>{escape(title)}</a></td></tr>")
        if subtitle and subtitle.casefold() != title.casefold():
            parts.append(f"<tr class='secondary'><td><a href='{escape(section_url, quote=True)}'>{escape(subtitle)}</a></td></tr>")
        if structure is None:
            parts.append("<tr class='warning'><td>Structure absente du référentiel.</td></tr>")
        elif not section.group_by_subdivision or code in NO_SUBDIVISION_STRUCTURES:
            parts.append("<tr><td class='note'>Sans regroupement par subdivision</td></tr>")
        elif not labels:
            parts.append("<tr><td class='note'>Aucune subdivision configurée</td></tr>")
        for index, label in visible:
            url = 'subdivision:' + quote(section.id, safe='') + ':' + str(index)
            displayed = display_label(label)
            parts.append(f"<tr class='subdivision'><td><a href='{escape(url, quote=True)}'>{escape(displayed)}</a></td></tr>")
            if code.startswith('RG-') and is_rg_compagnie(label):
                parts.append(f"<tr class='secondary'><td><a href='{escape(url, quote=True)}'>COMMANDEMENT</a></td></tr>")
    parts.append("</table>")
    if not shown:
        parts.append("<p class='note'>Aucune rubrique correspondante.</p>")
    parts.append("</body></html>")
    return ''.join(parts)
