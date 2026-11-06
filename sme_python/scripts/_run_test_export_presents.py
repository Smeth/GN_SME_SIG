"""Export test liste des présents + comparaison structure avec la maquette mai."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openpyxl import load_workbook

from sme_gend.config import get_settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService


def _classify_row(val) -> str:
    if val is None or str(val).strip() == "":
        return "blank"
    s = str(val).strip()
    if s.isdigit():
        return "data"
    return "rubric"


def _extract_rubrics(path: Path, sheet: str = "Liste") -> list[tuple[int, str, str]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.active
    out: list[tuple[int, str, str]] = []
    for i, row in enumerate(ws.iter_rows(min_row=4, max_col=1, values_only=True), start=4):
        val = row[0]
        kind = _classify_row(val)
        if kind == "rubric":
            text = str(val).strip()
            # sec1 vs sec2 heuristique : longueur / majuscules (approximation)
            style = "sec1" if text == text.upper() and len(text) > 25 else "sec2"
            out.append((i, style, text))
    wb.close()
    return out


def main() -> int:
    settings = get_settings()
    ctx = load_sme_context()
    mois = (ctx.mois_actif or "").strip()
    print(f"MySQL: {settings.mysql_host}/{settings.mysql_database}")
    print(f"Mois actif: {mois or '(non défini)'}")
    print(f"Template: {settings.template_liste_present_path}")

    layout_cfg = LayoutConfigService(settings)
    svc = ListePresentService(settings, layout_cfg)
    layout = layout_cfg.load()
    print(f"Sections layout: {len(layout.sections)}")

    dest = ROOT / "data" / "_test_liste_presents_export.xlsx"
    svc.export_excel(dest)
    print(f"Export OK: {dest}")

    sections = svc.build_sections_data(layout, include_orphans=True)
    total = sum(len(p) for _, _, _, blocks in sections for _, p in blocks)
    print(f"Sections: {len(sections)} | Présents: {total}")
    for i, (t1, t2, code, blocks) in enumerate(sections[:10]):
        n = sum(len(p) for _, p in blocks)
        print(f"  [{i + 1}] {t1[:55]} / {t2[:35]} ({code}) -> {n} pers., {len(blocks)} rubriques")
    if len(sections) > 10:
        print(f"  ... +{len(sections) - 10} sections")

    ref = ROOT / "liste_des_presents_mai.xlsx"
    if not ref.is_file():
        print(f"Maquette référence absente: {ref}")
        return 0

    ref_r = _extract_rubrics(ref)
    gen_r = _extract_rubrics(dest)
    print(f"\nRubriques maquette mai: {len(ref_r)} | export généré: {len(gen_r)}")

    ref_sec1 = [t for _, k, t in ref_r if k == "sec1"]
    gen_sec1 = [t for _, k, t in gen_r if k == "sec1"]
    print(f"Sec1 maquette: {len(ref_sec1)} | export: {len(gen_sec1)}")

    missing_sec1 = [t for t in ref_sec1 if t not in gen_sec1]
    extra_sec1 = [t for t in gen_sec1 if t not in ref_sec1]
    if missing_sec1:
        print("\nSec1 dans maquette mais absentes de l'export (max 10):")
        for t in missing_sec1[:10]:
            print(f"  - {t}")
    if extra_sec1:
        print("\nSec1 dans l'export mais absentes de la maquette (max 10):")
        for t in extra_sec1[:10]:
            print(f"  + {t}")

    # Comparaison séquence sec2 (premiers 30)
    ref_sec2 = [t for _, k, t in ref_r if k == "sec2"][:30]
    gen_sec2 = [t for _, k, t in gen_r if k == "sec2"][:30]
    print("\nPremières rubriques sec2 — maquette:")
    for t in ref_sec2[:15]:
        print(f"  · {t}")
    print("Premières rubriques sec2 — export:")
    for t in gen_sec2[:15]:
        print(f"  · {t}")

    match = sum(1 for a, b in zip(ref_sec2, gen_sec2) if a == b)
    print(f"\nAlignement sec2 (30 premières): {match}/30 identiques")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
