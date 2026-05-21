"""Export Excel — feuilles « SME_Detaches » et génération complète « DETACHE »."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from sme_gend.config.settings import Settings
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_service import (
    DetachesService,
    designation_bloc_excel,
    detache_ecart_remarque,
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


def _write_sme_detaches_sheet(ws, rows, cfg: DetachesFileConfig) -> None:
    headers = _sme_detaches_headers()
    for col, h in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=h)
    for i, r in enumerate(rows, start=1):
        sig = sigle_ou_subdivision(r)
        ws.cell(row=i + 1, column=1, value=i)
        ws.cell(row=i + 1, column=2, value=r.noms)
        ws.cell(row=i + 1, column=3, value=r.grade)
        ws.cell(row=i + 1, column=4, value=sig)
        ws.cell(row=i + 1, column=5, value=designation_bloc_excel(cfg, sig))
        ws.cell(row=i + 1, column=6, value=r.structure)
        ws.cell(row=i + 1, column=7, value=r.ndecision)
        dg = r.date_grade
        ws.cell(row=i + 1, column=8, value=dg.strftime("%d/%m/%Y") if dg else None)
        ws.cell(row=i + 1, column=9, value=r.matricule_militaire)
        ws.cell(row=i + 1, column=10, value=r.matricule_solde)
        ws.cell(row=i + 1, column=11, value=r.position_admin)
        ws.cell(row=i + 1, column=12, value=r.fonction)
        ws.cell(row=i + 1, column=13, value=r.obs)
        ws.cell(row=i + 1, column=14, value=detache_ecart_remarque(r))


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


def export_detaches_workbook(
    settings: Settings,
    destination: Path,
    *,
    use_template: bool = True,
) -> None:
    """
    Crée ou met à jour un classeur : si `use_template` et gabarit `DETACHES.xlsx` présent,
    copie toutes les feuilles existantes et ajoute/remplace la feuille « SME_Detaches ».
    Sinon, classeur minimal avec uniquement « SME_Detaches ».
    """
    svc = DetachesService(settings)
    rows = svc.fetch_detaches()
    tpl = settings.template_detaches_path

    cfg = DetachesConfigService(settings).load()

    if use_template and tpl is not None and tpl.is_file():
        wb = load_workbook(tpl, data_only=False, keep_vba=False)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    if SHEET_SME in wb.sheetnames:
        wb.remove(wb[SHEET_SME])
    ws = wb.create_sheet(SHEET_SME)
    _write_sme_detaches_sheet(ws, rows, cfg)

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    wb.close()


def export_detaches_workbook_full(
    settings: Settings,
    destination: Path,
    cfg: DetachesFileConfig,
    *,
    use_template: bool = True,
) -> tuple[int, int]:
    """
    Exporte un classeur : régénère la feuille « DETACHE » selon la configuration (titres, ordre, sigles)
    et ajoute / remplace « SME_Detaches ». Retourne (effectif personnes sur DETACHE, nombre de blocs).
    """
    svc = DetachesService(settings)
    rows = svc.fetch_detaches()
    grades = GradeRepository(settings).fetch_all()
    tpl = settings.template_detaches_path

    if use_template and tpl is not None and tpl.is_file():
        wb = load_workbook(tpl, data_only=False, keep_vba=False)
        if "DETACHE" not in wb.sheetnames:
            wb.create_sheet("DETACHE")
        ws_d = wb["DETACHE"]
        n_pers, n_blocs = fill_detache_sheet(ws_d, cfg, rows, grades)
    else:
        wb = Workbook()
        wb.remove(wb.active)
        ws_d = wb.create_sheet("DETACHE")
        n_pers, n_blocs = fill_detache_sheet(ws_d, cfg, rows, grades)

    if SHEET_SME in wb.sheetnames:
        wb.remove(wb[SHEET_SME])
    ws_s = wb.create_sheet(SHEET_SME)
    _write_sme_detaches_sheet(ws_s, rows, cfg)

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    wb.close()
    return n_pers, n_blocs
