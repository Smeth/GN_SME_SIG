"""Impression / PDF liste générale — modèle « SITUATION NOMINATIVE DES EFFECTIFS »."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, datetime

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.matricule_format import format_matricule_solde_display
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

# Colonnes et largeurs mesurées sur sme_python/liste_generale.pdf (A4 paysage).
# Ordre : N°, identité, carrière, matricule militaire puis solde, affectation, diplôme, structure.
PRINT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("idx", "N°", "3%"),
    ("noms", "Noms et prénoms", "13%"),
    ("naissance", "Date et lieu de naissance", "10%"),
    ("grade", "Grade", "4%"),
    ("date_grade", "Date de nomination au grade", "6%"),
    ("date_service", "Date d'entrée en service", "5%"),
    ("ndecision", "N° décision d'engagement", "8%"),
    ("matricule_militaire", "Matricule militaire", "6%"),
    ("matricule_solde", "Matricule solde", "6%"),
    ("position_admin", "Position administrative", "5%"),
    ("fonction", "Fonction", "7%"),
    ("subdivision_arme", "Subdivision d'arme", "6%"),
    ("diplome", "Dernier diplôme militaire", "5%"),
    ("date_obtention", "Date et lieu d'obtention", "7%"),
    ("structure", "Structure", "6%"),
    ("obs", "Obs", "3%"),
)

LISTE_GENERALE_OFFICIAL_TITLE = "SITUATION NOMINATIVE DES EFFECTIFS"

# Typographie du modèle liste_generale.pdf : Tahoma, titre 12 pt, tableau 7 pt.
OFFICIAL_FONT = "Tahoma"
OFFICIAL_TITLE_PT = 12.0
OFFICIAL_META_PT = 7.0
OFFICIAL_HDR_PT = 7.0
OFFICIAL_CELL_PT = 7.0
OFFICIAL_BODY_PT = 7.0

# Largeurs Excel (caractères) — même ordre que PRINT_COLUMNS.
OFFICIAL_EXCEL_COLS: list[tuple[str, str, int]] = [
    ("idx", "N°", 5),
    ("noms", "Noms et prénoms", 28),
    ("naissance", "Date et lieu de naissance", 22),
    ("grade", "Grade", 8),
    ("date_grade", "Date de nomination au grade", 14),
    ("date_service", "Date d'entrée en service", 14),
    ("ndecision", "N° décision d'engagement", 22),
    ("matricule_militaire", "Matricule militaire", 14),
    ("matricule_solde", "Matricule solde", 14),
    ("position_admin", "Position administrative", 14),
    ("fonction", "Fonction", 16),
    ("subdivision_arme", "Subdivision d'arme", 14),
    ("diplome", "Dernier diplôme militaire", 12),
    ("date_obtention", "Date et lieu d'obtention", 16),
    ("structure", "Structure", 12),
    ("obs", "Obs", 8),
]


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _fmt_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%d/%m/%Y")
    if isinstance(val, date):
        return val.strftime("%d/%m/%Y")
    s = str(val).strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        try:
            y, m, d = s[:10].split("-")
            return f"{d}/{m}/{y}"
        except ValueError:
            pass
    return s


def official_liste_cell_value(
    row: ListeGeneraleRow,
    key: str,
    *,
    flat: Sequence[SubdivisionFlatRow] | None,
    line_no: int,
    grades: Sequence[GradeRefRow] | None = None,
) -> str:
    if key == "idx":
        return str(line_no)
    if key == "noms":
        return (row.noms or "").strip()
    if key == "naissance":
        dn = _fmt_date(row.date_naissance)
        ln = (row.lieu_naissance or "").strip()
        if dn and ln:
            return f"{dn} {ln}"
        return dn or ln
    if key == "grade":
        return format_grade_display(row.grade, grades)
    if key == "date_grade":
        return _fmt_date(row.date_grade)
    if key == "date_service":
        return _fmt_date(row.date_service) or (row.date_service or "").strip()
    if key == "ndecision":
        return (row.ndecision or "").strip()
    if key == "matricule_solde":
        v = format_matricule_solde_display(row.matricule_solde, empty="")
        return v if v != "—" else ""
    if key == "matricule_militaire":
        return (row.matricule_militaire or "").strip()
    if key == "position_admin":
        return (row.position_admin or "").strip()
    if key == "fonction":
        return (row.fonction or "").strip()
    if key == "subdivision_arme":
        if flat:
            return format_subdivision_arme_display(row.subdivision_arme, flat) or (row.subdivision_arme or "")
        return (row.subdivision_arme or "").strip()
    if key == "diplome":
        return (row.diplome or "").strip()
    if key == "date_obtention":
        return _fmt_date(row.date_obtention) or (row.date_obtention or "").strip()
    if key == "structure":
        return (row.structure or "").strip()
    if key == "obs":
        return (row.obs or "").strip()
    return ""


def _css() -> str:
    return (
        "@page{size:A4 landscape;margin:8mm 6mm 8mm 6mm;}"
        f"body{{margin:0;padding:0;font-family:'{OFFICIAL_FONT}',Arial,sans-serif;"
        f"font-size:{OFFICIAL_BODY_PT}pt;color:#000;"
        "}"
        "table{border-collapse:collapse;width:100%;table-layout:fixed;}"
        ".title{text-align:center;font-weight:bold;"
        f"font-size:{OFFICIAL_TITLE_PT}pt;"
        "margin:0 0 6px 0;letter-spacing:0.3px;}"
        f".meta{{text-align:center;font-size:{OFFICIAL_META_PT}pt;color:#000;margin-bottom:4px;}}"
        "th,td{border:1px solid #000;padding:1px 2px;vertical-align:top;"
        "word-wrap:break-word;overflow:hidden;line-height:1.1;}"
        f"td{{font-size:{OFFICIAL_CELL_PT}pt;}}"
        f"td.hdr{{font-weight:bold;text-align:center;font-size:{OFFICIAL_HDR_PT}pt;}}"
        ".col-idx{text-align:center;}"
    )


def resolve_print_columns(keys: Sequence[str] | None) -> tuple[tuple[str, str, str], ...]:
    """Colonnes officielles, dans l'ordre du modèle, limitées à ``keys``."""
    if not keys:
        return PRINT_COLUMNS
    wanted = {key for key in keys if key}
    chosen = tuple(col for col in PRINT_COLUMNS if col[0] in wanted)
    return chosen or PRINT_COLUMNS


def _header_row(columns: Sequence[tuple[str, str, str]]) -> str:
    """Ligne d'en-têtes dans ``tbody`` (Qt répète ``thead`` à chaque page imprimée)."""
    use_width = len(columns) == len(PRINT_COLUMNS)
    parts = []
    for key, label, width in columns:
        cls = "hdr col-idx" if key == "idx" else "hdr"
        style = f" style='width:{width};'" if use_width else ""
        parts.append(f"<td class='{cls}'{style}>{_esc(label)}</td>")
    return "<tr class='hdr-row'>" + "".join(parts) + "</tr>"


def _data_row(
    row: ListeGeneraleRow,
    line_no: int,
    *,
    flat: Sequence[SubdivisionFlatRow] | None,
    columns: Sequence[tuple[str, str, str]],
    grades: Sequence[GradeRefRow] | None = None,
) -> str:
    cells = []
    for key, _, _w in columns:
        val = official_liste_cell_value(row, key, flat=flat, line_no=line_no, grades=grades)
        cls = "col-idx" if key == "idx" else ""
        cells.append(f"<td class='{cls}'>{_esc(val)}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def build_liste_generale_print_html(
    rows: Sequence[ListeGeneraleRow],
    *,
    mois_label: str | None = None,
    flat: Sequence[SubdivisionFlatRow] | None = None,
    grades: Sequence[GradeRefRow] | None = None,
    columns: Sequence[str] | None = None,
) -> str:
    """HTML A4 paysage pour QPrinter / aperçu avant impression."""
    cols = resolve_print_columns(columns)
    row_list = list(rows)
    body = "".join(
        _data_row(r, i + 1, flat=flat, columns=cols, grades=grades) for i, r in enumerate(row_list)
    )
    title_block = f"<p class='title'>{LISTE_GENERALE_OFFICIAL_TITLE}</p>"
    table = f"<table><tbody>{_header_row(cols)}{body}</tbody></table>"

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/><style>"
        + _css()
        + "</style></head><body>"
        + title_block
        + table
        + "</body></html>"
    )


def apply_liste_generale_printer_layout(printer) -> None:
    """A4 paysage — ``setPageLayout`` seul est ignoré par certains pilotes Windows."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize

    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        QPageLayout.Orientation.Landscape,
        QMarginsF(8, 10, 8, 10),
    )
    printer.setPageLayout(layout)
    if hasattr(printer, "setPageOrientation"):
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)


def sync_document_to_printer(doc, printer) -> None:
    """Aligne ``QTextDocument`` sur la zone imprimable paysage du ``QPrinter``."""
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QPageLayout

    apply_liste_generale_printer_layout(printer)
    layout = printer.pageLayout()
    rect = layout.paintRect(QPageLayout.Unit.Point)
    doc.setDocumentMargin(0)
    doc.setPageSize(QSizeF(rect.width(), rect.height()))


def html_to_print_document(html: str):
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setHtml(html)
    return doc
