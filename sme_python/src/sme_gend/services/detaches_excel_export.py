"""Export Excel — feuilles « SME_Detaches » et génération complète « DETACHE »."""

from __future__ import annotations

from pathlib import Path
from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

from sme_gend.services.excel_style_helpers import write_literal_cell_value, writable_cell
from openpyxl import Workbook, load_workbook

from sme_gend.config.settings import Settings
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.detaches_service import (
    DetachesService,
    designation_bloc_excel,
    detache_ecart_remarque,
    filter_detaches_rows,
    sigle_ou_subdivision,
)
from sme_gend.services.detaches_sheet_writer import fill_detache_sheet

SHEET_SME = "SME_Detaches"


def _sme_detaches_headers() -> tuple[str, ...]:
    return (
        "N°",
        "Nom(s) et prénom(s)",
        "Grade",
        "Sigle / subdivision arme",
        "Désignation bloc (titres Excel / config)",
        "Structure",
        "Réf. note (décision)",
        "Date grade",
        "Matricule militaire",
        "Matricule solde",
        "Position administrative",
        "Fonction",
        "Observations",
        "Contrôle liste détachés",
    )


def _write_sme_detaches_sheet(ws, rows, cfg: DetachesFileConfig, grades=None) -> None:
    headers = _sme_detaches_headers()
    for col, h in enumerate(headers, start=1):
        write_literal_cell_value(ws, 1, col, h)
    for i, r in enumerate(export_rows(rows, label=ws.title), start=1):
        sig = sigle_ou_subdivision(r)
        write_literal_cell_value(ws, i + 1, 1, i)
        write_literal_cell_value(ws, i + 1, 2, r.noms)
        write_literal_cell_value(ws, i + 1, 3, format_grade_display(r.grade, grades))
        write_literal_cell_value(ws, i + 1, 4, sig)
        write_literal_cell_value(ws, i + 1, 5, designation_bloc_excel(cfg, sig))
        write_literal_cell_value(ws, i + 1, 6, r.structure)
        write_literal_cell_value(ws, i + 1, 7, r.ndecision)
        dg = r.date_grade
        write_literal_cell_value(ws, i + 1, 8, dg.strftime("%d/%m/%Y") if dg else None)
        write_literal_cell_value(ws, i + 1, 9, r.matricule_militaire)
        write_literal_cell_value(ws, i + 1, 10, r.matricule_solde)
        write_literal_cell_value(ws, i + 1, 11, r.position_admin)
        write_literal_cell_value(ws, i + 1, 12, r.fonction)
        write_literal_cell_value(ws, i + 1, 13, r.obs)
        write_literal_cell_value(ws, i + 1, 14, detache_ecart_remarque(r))
    from sme_gend.services.malade_punis_export import _autosize
    _autosize(ws, headers, 2, max(1, len(rows) + 1))


def count_official_detache_lignes(template_path: Path | None) -> int | None:
    """
    Compte les lignes de données sur la feuille « DETACHE » (colonne A = entier = rang).
    Retourne None si fichier ou feuille absente.
    """
    if template_path is None or not template_path.is_file():
        return None
    wb = load_workbook(template_path, read_only=True, data_only=True)
    try:
        if "DETACHE" not in wb.sheetnames:
            return None
        ws = wb["DETACHE"]
        n = 0
        for r in range(1, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if isinstance(a, int) and a >= 1:
                n += 1
        return n
    finally:
        wb.close()


@tracked_export
def export_detaches_workbook(
    settings: Settings,
    destination: Path,
    *,
    use_template: bool = True,
    sans_note_only: bool = False,
    avec_note_only: bool = False,
    rows: list | None = None,
    configuration: DetachesFileConfig | None = None,
) -> None:
    """
    Crée ou met à jour un classeur : si `use_template` et gabarit `DETACHES.xlsx` présent,
    copie toutes les feuilles existantes et ajoute/remplace la feuille « SME_Detaches ».
    Sinon, classeur minimal avec uniquement « SME_Detaches ».
    """
    rows = filter_detaches_rows(
        DetachesService(settings).fetch_detaches() if rows is None else list(rows),
        sans_note_only=sans_note_only,
        avec_note_only=avec_note_only,
    )
    tpl = settings.template_detaches_path

    cfg = configuration if configuration is not None else DetachesConfigService(settings).load()

    if use_template and tpl is not None and tpl.is_file():
        wb = load_workbook(tpl, data_only=False, keep_vba=False)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    if SHEET_SME in wb.sheetnames:
        wb.remove(wb[SHEET_SME])
    ws = wb.create_sheet(SHEET_SME)
    try:
        grades_ref = GradeRepository(settings).fetch_all()
    except Exception:
        grades_ref = []
    export_phase("render", total=len(rows))
    _write_sme_detaches_sheet(ws, rows, cfg, grades_ref)

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    export_phase("write", total=1, detail="Enregistrement du classeur des détachés")
    wb.save(dest)
    wb.close()
    export_phase("finalize", detail="Historique du document")

    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.doc_gen_log import log_doc_generation

    log_doc_generation(
        doc_type="detaches", settings=settings,
        format="xlsx",
        count=len(rows),
        dest_path=str(dest),
        mois=resolve_export_mois(settings),
        meta={
            "mode": "sme_only",
            "filtre_sans_note": sans_note_only,
            "filtre_avec_note": avec_note_only,
        },
    )


@tracked_export
def export_detaches_workbook_full(
    settings: Settings,
    destination: Path,
    cfg: DetachesFileConfig,
    *,
    use_template: bool = True,
    sans_note_only: bool = False,
    avec_note_only: bool = False,
    rows: list | None = None,
) -> tuple[int, int]:
    """
    Exporte un classeur : régénère la feuille « DETACHE » selon la configuration (titres, ordre, sigles)
    et ajoute / remplace « SME_Detaches ». Retourne (effectif personnes sur DETACHE, nombre de blocs).
    """
    rows = filter_detaches_rows(
        DetachesService(settings).fetch_detaches() if rows is None else list(rows),
        sans_note_only=sans_note_only,
        avec_note_only=avec_note_only,
    )
    grades = GradeRepository(settings).fetch_all()
    tpl = settings.template_detaches_path
    export_phase("render", total=2*len(rows), detail="Remplissage de DETACHE et SME_Detaches")

    if use_template and tpl is not None and tpl.is_file():
        wb = load_workbook(tpl, data_only=False, keep_vba=False)
        if "DETACHE" not in wb.sheetnames:
            wb.create_sheet("DETACHE")
        ws_d = wb["DETACHE"]
        n_pers, n_blocs = fill_detache_sheet(ws_d, cfg, rows, grades, settings=settings)
    else:
        wb = Workbook()
        wb.remove(wb.active)
        ws_d = wb.create_sheet("DETACHE")
        n_pers, n_blocs = fill_detache_sheet(ws_d, cfg, rows, grades, settings=settings)

    if SHEET_SME in wb.sheetnames:
        wb.remove(wb[SHEET_SME])
    ws_s = wb.create_sheet(SHEET_SME)
    _write_sme_detaches_sheet(ws_s, rows, cfg, grades)

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    export_phase("write", total=1, detail="Enregistrement du classeur complet des détachés")
    wb.save(dest)
    wb.close()
    export_phase("finalize", detail="Historique du document")

    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.doc_gen_log import log_doc_generation

    log_doc_generation(
        doc_type="detaches", settings=settings,
        format="xlsx",
        count=n_pers,
        dest_path=str(dest),
        mois=resolve_export_mois(settings),
        meta={
            "mode": "full",
            "blocs": n_blocs,
            "sme_rows": len(rows),
            "filtre_sans_note": sans_note_only,
            "filtre_avec_note": avec_note_only,
        },
    )
    return n_pers, n_blocs
