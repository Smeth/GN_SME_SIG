"""Export PDF du dossier agent — fiche individuelle à pagination adaptative."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, HTML_STEPS, export_phase, export_rows

import base64
import html
from datetime import datetime
from pathlib import Path
from sme_gend.services.safe_paths import atomic_destination

from sme_gend.config.liste_generale_scope import build_export_mois_context
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sexe import sexe_display_long
from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
from sme_gend.services.agent_dossier_service import AgentDossierService
from sme_gend.services.grade_label_format import grade_combo_label
from sme_gend.services.matricule_format import format_matricule_solde_display
from sme_gend.services.personnel_media import grade_insignia_path, person_photo_path
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_label_format import format_structure_display
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

_CLR_INK = "#111111"
_CLR_MUTED = "#555555"
_CLR_LINE = "#000000"
_CLR_HEAD = "#1a365d"
_CLR_HEAD_BG = "#e8eef4"
_CLR_LABEL_BG = "#f0f2f5"
_PAGE_TOTAL = 3


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _dash(val) -> str:
    if val is None:
        return "—"
    s = str(val).strip()
    return _esc(s) if s else "—"


def _fmt_date(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%d/%m/%Y")
        except Exception:
            pass
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


def _split_nom_prenoms(noms: str | None) -> tuple[str, str, str]:
    raw = " ".join((noms or "").strip().split())
    if not raw:
        return "—", "—", "—"
    parts = raw.split(" ", 1)
    nom = _esc(parts[0])
    if len(parts) == 1:
        return nom, "—", nom
    prenoms = _esc(parts[1])
    return nom, prenoms, _esc(raw)


def _img_data_uri(path: Path | None, *, max_bytes: int = 800_000) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
        from PySide6.QtGui import QImage
        import logging
        photo = QImage.fromData(raw)
        if photo.isNull():
            logging.getLogger(__name__).warning("Photo non incorporée : format non décodable.")
            return None
        photo = photo.scaled(600, 800, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        format_name = "PNG" if photo.hasAlphaChannel() else "JPEG"
        while True:
            encoded = QByteArray()
            buffer = QBuffer(encoded)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            saved = photo.save(buffer, format_name, 85)
            buffer.close()
            if not saved:
                return None
            raw = bytes(encoded)
            if len(raw) <= max_bytes:
                return f"data:image/{format_name.lower()};base64," + base64.b64encode(raw).decode("ascii")
            if photo.width() <= 32 or photo.height() <= 32:
                logging.getLogger(__name__).warning("Photo non incorporée : limite de taille trop faible.")
                return None
            photo = photo.scaled(max(1, int(photo.width() * .75)), max(1, int(photo.height() * .75)),
                                 Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    ext = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(ext, "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _media_person_key(row: ListeGeneraleRow) -> str:
    return str(row.satellite_join_key() or row.id or "").strip()


def _css() -> str:
    return (
        "body{margin:0;padding:0;font-family:'Segoe UI',Arial,Helvetica,sans-serif;"
        "font-size:9pt;color:" + _CLR_INK + ";background:#fff;line-height:1.3;}"
        "table{border-collapse:collapse;border-spacing:0;}"
        ".page{page-break-after:always;}"
        ".page-last{page-break-after:auto;}"
    )


def _page_shell(inner: str, *, last: bool = False) -> str:
    cls = "page-last" if last else "page"
    return (
        f"<div class='{cls}'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'><tr><td>"
        f"{inner}"
        f"</td></tr></table></div>"
    )


def _doc_header(
    *,
    dossier_id: str,
    matricule: str,
    etablie_le: str,
    mois_sme: str,
    page_num: int,
    noms_complet: str,
) -> str:
    periode = f"Période SME : {_esc(mois_sme)}" if mois_sme else ""
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:2px solid {_CLR_HEAD};margin-bottom:8px;'>"
        f"<tr><td style='padding:10px 12px;text-align:center;background-color:{_CLR_HEAD_BG};'>"
        f"<div style='font-size:10pt;font-weight:bold;letter-spacing:0.5px;'>GENDARMERIE NATIONALE</div>"
        f"<div style='font-size:13pt;font-weight:bold;margin-top:4px;text-decoration:underline;'>"
        f"FICHE INDIVIDUELLE</div>"
        f"<div style='font-size:8.5pt;color:{_CLR_MUTED};margin-top:3px;font-style:italic;'>"
        f"Document administratif — synthèse du parcours (données SME)</div>"
        f"</td></tr></table>"
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid {_CLR_LINE};margin-bottom:6px;font-size:8.5pt;'>"
        f"<tr style='background-color:{_CLR_LABEL_BG};'>"
        f"<td width='25%' style='padding:5px 8px;border:1px solid {_CLR_LINE};'>"
        f"<b>Agent :</b> {_esc(noms_complet)}</td>"
        f"<td width='25%' style='padding:5px 8px;border:1px solid {_CLR_LINE};'>"
        f"<b>N° dossier :</b> {_esc(dossier_id)}</td>"
        f"<td width='25%' style='padding:5px 8px;border:1px solid {_CLR_LINE};'>"
        f"<b>Matricule :</b> {_esc(matricule)}</td>"
        f"<td width='25%' style='padding:5px 8px;border:1px solid {_CLR_LINE};'>"
        f"<b>Établie le :</b> {_esc(etablie_le)}</td>"
        f"</tr>"
        f"<tr>"
        f"<td colspan='2' style='padding:4px 8px;border:1px solid {_CLR_LINE};color:{_CLR_MUTED};'>"
        f"{periode or '&nbsp;'}</td>"
        f"<td colspan='2' style='padding:4px 8px;border:1px solid {_CLR_LINE};text-align:right;"
        f"font-size:8pt;color:{_CLR_MUTED};'>"
        f"Confidentiel — usage interne &nbsp;|&nbsp; Page {page_num} / {_PAGE_TOTAL}"
        f"</td></tr></table>"
    )


def _section_title(num: int, title: str) -> str:
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='margin-top:10px;margin-bottom:5px;'>"
        f"<tr><td style='background-color:{_CLR_HEAD};color:#ffffff;font-weight:bold;"
        f"font-size:9.5pt;padding:5px 10px;'>{num}. {_esc(title.upper())}</td></tr></table>"
    )


def _form_grid(rows: list[tuple[str, str]], *, label_width: str = "36%") -> str:
    body = ""
    for label, val in export_rows(rows):
        body += (
            f"<tr>"
            f"<td width='{label_width}' style='padding:5px 8px;border:1px solid {_CLR_LINE};"
            f"background-color:{_CLR_LABEL_BG};font-weight:bold;vertical-align:top;'>"
            f"{_esc(label)}</td>"
            f"<td style='padding:5px 8px;border:1px solid {_CLR_LINE};vertical-align:top;'>{val}</td>"
            f"</tr>"
        )
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid {_CLR_LINE};'>{body}</table>"
    )


def _media_cell(
    uri: str | None,
    *,
    box_w: int,
    box_h: int,
    img_w: int,
    img_h: int,
    caption: str,
) -> str:
    if uri:
        inner = (
            f"<img src='{uri}' width='{img_w}' height='{img_h}' "
            f"style='max-width:{img_w}px;max-height:{img_h}px;display:block;margin:0 auto;' alt=''/>"
        )
    else:
        inner = (
            f"<div style='font-size:8pt;font-weight:bold;color:{_CLR_MUTED};'>"
            f"{_esc(caption.upper())}</div>"
            f"<div style='font-size:7.5pt;color:#888;margin-top:6px;'>Non renseignée</div>"
        )
    return (
        f"<td width='{box_w}' valign='middle' align='center' "
        f"style='border:1px solid {_CLR_LINE};background-color:#fafafa;padding:6px;'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'><tr>"
        f"<td align='center' valign='middle' style='height:{box_h}px;'>{inner}</td>"
        f"</tr></table>"
        f"<div style='font-size:7pt;color:{_CLR_MUTED};margin-top:4px;'>{_esc(caption)}</div>"
        f"</td>"
    )


def _side_by_side_media_form(
    media_html: str,
    form_html: str,
    *,
    media_width: int,
) -> str:
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0'>"
        f"<tr>{media_html}"
        f"<td valign='top' style='padding-left:8px;'>{form_html}</td></tr></table>"
    )


def _fonction_absente(value: str | None) -> bool:
    """Vrai si la fonction n'est pas renseignée (vide, ou le libellé « vide » / « dive »)."""
    from sme_gend.services.fonction_structure_policy import fold_label

    folded = fold_label(value)
    return not folded or folded in {"vide", "dive"}


def _data_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    col_widths: list[str] | None = None,
    blank_if_empty: bool = False,
) -> str:
    th_parts = []
    for i, h in enumerate(headers):
        w = ""
        if col_widths and i < len(col_widths):
            w = f" width='{col_widths[i]}'"
        th_parts.append(
            f"<th{w} style='border:1px solid {_CLR_LINE};padding:5px 6px;"
            f"background-color:{_CLR_LABEL_BG};font-weight:bold;text-align:center;"
            f"font-size:8.5pt;'>{_esc(h)}</th>"
        )
    th_row = "".join(th_parts)
    if not rows and blank_if_empty:
        blanks = "".join(
            f"<td style='border:1px solid {_CLR_LINE};padding:4px 6px;"
            f"font-size:8.5pt;'>&nbsp;</td>"
            for _ in headers
        )
        body = f"<tr>{blanks}</tr>"
    elif not rows:
        body = (
            f"<tr><td colspan='{len(headers)}' style='border:1px solid {_CLR_LINE};"
            f"padding:10px;text-align:center;font-style:italic;color:{_CLR_MUTED};'>"
            f"Aucun enregistrement</td></tr>"
        )
    else:
        body = ""
        for row in export_rows(rows):
            cells = row + [""] * (len(headers) - len(row))
            tds = "".join(
                f"<td style='border:1px solid {_CLR_LINE};padding:4px 6px;"
                f"font-size:8.5pt;vertical-align:top;'>{_esc(str(c)) if c else '—'}</td>"
                for c in cells[: len(headers)]
            )
            body += f"<tr>{tds}</tr>"
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid {_CLR_LINE};'>"
        f"<thead><tr>{th_row}</tr></thead><tbody>{body}</tbody></table>"
    )


def _signatures_block(structure: str, etablie_le: str) -> str:
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-top:14px;'>"
        f"<tr><td colspan='2' style='padding:6px 0 10px;text-align:center;font-size:9pt;'>"
        f"<b>Fait à</b> {_dash(structure)} &nbsp;&nbsp;&nbsp; <b>Le</b> {_esc(etablie_le)}"
        f"</td></tr>"
        f"<tr>"
        f"<td width='50%' style='border:1px solid {_CLR_LINE};height:58px;vertical-align:bottom;"
        f"text-align:center;font-size:8.5pt;padding:6px;'>Signature de l'intéressé(e)</td>"
        f"<td width='50%' style='border:1px solid {_CLR_LINE};height:58px;vertical-align:bottom;"
        f"text-align:center;font-size:8.5pt;padding:6px;'>Cachet et signature de l'autorité</td>"
        f"</tr></table>"
    )


@tracked_export(steps=HTML_STEPS)
def build_dossier_html(row: ListeGeneraleRow, settings: Settings) -> str:
    """Fiche lisible : identité, situation et seuls historiques renseignés."""
    ctx = build_export_mois_context(settings)
    mois = (ctx.export_mois or "").strip()
    try:
        ref = ReferentielService(settings)
        grades, structures = ref.grades(), ref.structures()
    except Exception:
        grades, structures = [], []
    grade = grade_combo_label(row.grade or "", grades) or row.grade or "—"
    structure = format_structure_display(row.structure, structures) or row.structure or "—"
    header = (
        "<h3 style='text-align:center;margin:0;'>GENDARMERIE NATIONALE</h3>"
        "<h2 style='text-align:center;margin:5px 0 12px;'>FICHE INDIVIDUELLE</h2>"
        f"<p><b>{_esc(row.noms or '—')}</b> — Matricule : {_esc(row.matricule_militaire or '—')}<br/>"
        f"Situation SME : {_esc(mois)} · Éditée le {datetime.now().strftime('%d/%m/%Y')}</p>"
    )
    identity = [
        ("Nom(s) et prénom(s)", _dash(row.noms)),
        ("Date de naissance", _dash(_fmt_date(row.date_naissance))),
        ("Lieu de naissance", _dash(row.lieu_naissance)),
        ("Sexe", _dash(sexe_display_long(row.sexe))),
        ("Matricule solde", _dash(format_matricule_solde_display(row.matricule_solde))),
    ]
    for label, value in (("Téléphone", row.telephone), ("Adresse", row.adresse_residence)):
        if value:
            identity.append((label, _dash(value)))
    photo = _img_data_uri(person_photo_path(_media_person_key(row)))
    body = header + _section_title(1, "Identité")
    form = _form_grid(identity)
    body += _side_by_side_media_form(_media_cell(photo, box_w=100, box_h=112, img_w=90, img_h=108, caption="Photo"), form, media_width=100) if photo else form
    military = [("Grade", _dash(grade)), ("Structure / unité", _dash(structure)),
                ("Position administrative", _dash(row.position_admin)),
                ("Date d'entrée en service", _dash(_fmt_date(row.date_service))),
                ("Date de nomination au grade", _dash(_fmt_date(row.date_grade)))]
    if not _fonction_absente(row.fonction):
        military.append(("Fonction", _dash(row.fonction)))
    if row.subdivision_arme:
        military.append(("Subdivision d'arme", _dash(row.subdivision_arme)))
    if row.ndecision:
        military.append(("Référence de décision", _dash(row.ndecision)))
    body += _section_title(2, "Situation militaire") + _form_grid(military)
    svc, events = AgentDossierService(settings), AgentDossierEventsService(settings)
    leg = str(row.satellite_join_key() or "")
    section_number = 2
    def unique(values):
        return list(dict.fromkeys(tuple(str(v or "") for v in line) for line in values))
    def section(title, headers, lines, widths):
        nonlocal section_number
        lines = unique(lines)
        if not lines:
            return ""
        section_number += 1
        return _section_title(section_number, title) + _data_table(headers, [list(line) for line in lines], col_widths=widths)
    passations = [[p.grade_libelle, _fmt_date(p.date_passation), p.ndecision] for p in svc.list_passations(row)]
    body += section("Historique des grades", ["Grade", "Date de nomination", "Décision"], passations, ["28%", "28%", "44%"])
    from sme_gend.services.dossier_grouping import group_affectations, group_diplomas, group_stages, function_events
    raw_affectations = list(svc.list_affectations(row))
    affectations = [g.row for g in group_affectations(raw_affectations)]
    # Une répétition mensuelle de la seule unité actuelle sans fonction n'est pas
    # un mouvement de carrière. Conserver les véritables changements d'unité.
    meaningful = any(not _fonction_absente(a.fonction) for a in affectations) or len({a.structure_code for a in affectations}) > 1
    body += section("Historique des affectations", ["Structure", "Fonction", "Date d'affectation"],
                    [[a.structure_code, a.fonction, _fmt_date(a.date_affectation)] for a in affectations] if meaningful else [], ["28%", "45%", "27%"])
    body += section("Fonctions et subdivisions constatées", ["Structure", "Fonction", "Subdivision", "Date de constat"],
                    [[a.structure_code, a.fonction, a.subdivision_arme, _fmt_date(a.date_affectation)]
                     for a in function_events(raw_affectations)], ["22%", "32%", "24%", "22%"])
    activities = [["Mission", m.nature, m.lieu, _fmt_date(m.date_debut), _fmt_date(m.date_fin)] for m in events.missions(leg)]
    activities += [["Stage " + (g.row.type_stage or ""), g.row.nature, g.row.lieu, _fmt_date(g.row.date_debut), _fmt_date(g.row.date_fin)] for g in group_stages(events.stages(leg))]
    body += section("Missions et stages", ["Type", "Intitulé", "Lieu", "Début", "Fin"], activities, ["16%", "30%", "22%", "16%", "16%"])
    degrees = [["Militaire" if g.row.categorie == "MILITAIRE" else "Civil", g.row.libelle, _fmt_date(g.row.date_obtention)] for g in group_diplomas(svc.list_diplomes(row))]
    if not degrees and (row.diplome or "").strip():
        degrees.append(["Militaire", row.diplome, _fmt_date(row.date_obtention)])
    body += section("Diplômes et formations", ["Catégorie", "Diplôme", "Date d'obtention"], degrees, ["23%", "50%", "27%"])
    distinctions = svc.list_distinctions(row) if hasattr(svc, "list_distinctions") else []
    body += section("Médailles et décorations", ["Distinction", "Date", "Décision"],
                    [[d.libelle, _fmt_date(d.date_attribution), d.numero_arrete] for d in distinctions], ["45%", "25%", "30%"])
    from sme_gend.services.position_admin_events import list_position_events
    absences = [[position, _fmt_date(start), _fmt_date(end), " — ".join(str(v) for v in (lieu, reference, obs) if v)]
                for position, start, end, lieu, reference, obs, _month in list_position_events(settings, leg)]
    absences += [["Maladie", _fmt_date(m.date_debut), _fmt_date(m.date_fin), m.observation] for m in events.malades(leg)]
    absences += [[c.type_label, _fmt_date(c.date_debut), _fmt_date(c.date_fin), c.observation] for c in events.conges(leg)]
    absences += [["Désertion", _fmt_date(d.date_desertion), "", d.observation] for d in events.desertions(leg)]
    body += section("Positions administratives et absences", ["Nature", "Début", "Fin", "Observations"], absences, ["25%", "17%", "17%", "41%"])
    observation = (row.obs or "").strip()
    if observation and not observation.startswith(("Import archive", "Saisie applicative")):
        section_number += 1
        body += _section_title(section_number, "Observations") + f"<p>{_esc(observation)}</p>"
    return "<!DOCTYPE html><html><head><meta charset='utf-8'/><style>" + _css() + "</style></head><body>" + body + "</body></html>"


def export_parcours_pdf(
    row: ListeGeneraleRow,
    settings: Settings,
    dest: Path | str,
) -> Path:
    """Génère un PDF du dossier complet via QPrinter (HTML)."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    path = Path(dest).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setDefaultStyleSheet(_css())
    doc.setHtml(build_dossier_html(row, settings))

    with atomic_destination(path, temporary_suffix=".pdf") as staged:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(staged))
        printer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(16, 14, 16, 14),
            )
        )
        doc.print_(printer)
        if printer.printerState() == QPrinter.PrinterState.Error or staged.stat().st_size == 0:
            raise OSError("La génération du PDF a échoué ; le fichier précédent est conservé.")

    from sme_gend.services.doc_gen_log import log_doc_generation

    ctx = build_export_mois_context(settings)
    log_doc_generation(
        doc_type="dossier_pdf", settings=settings,
        format="pdf",
        persons=[row.noms or "?"],
        dest_path=str(path),
        count=1,
        mois=ctx.export_mois,
    )
    return path
