"""Construction des sorties SME à partir du mois consulté, sans cellules résiduelles."""
from collections import Counter
from dataclasses import replace
from pathlib import Path
from openpyxl import Workbook
from sme_gend.services.safe_paths import atomic_destination
from sme_gend.services.export_progress import tracked_export, export_phase

from sme_gend.services.export_workbook_tables import table_sheet, grade_summary, export_details, personnel_cells, detail_cells
from sme_gend.services.excel_style_helpers import build_grade_abbrev_map, grade_to_abbrev
from sme_gend.services.numerique_padmin import is_present_strict, row_predicate_for_rubrique
from sme_gend.services.sme_dpf_row_format import LISTE_GNLE_HEADERS, row_to_dpf_liste_cells

LIST_HEADERS = ("N°", "Nom(s) et prénom(s)", "Date de naissance", "Lieu de naissance", "Grade",
                "Date de grade", "Date d'entrée en service", "N° décision", "Matricule militaire",
                "Matricule solde", "Position administrative", "Fonction", "Subdivision d'arme",
                "Diplôme", "Date d'obtention", "Structure", "Observations")
DETAIL_HEADERS = ("N°", "Grade", "Nom(s) et prénom(s)", "Structure", "Nature / intitulé",
                  "Lieu", "Référence", "Début / période", "Fin", "Observations")


def list_cells(row, number, gmap):
    return [number, row.noms, row.date_naissance, row.lieu_naissance, grade_to_abbrev(row.grade, gmap),
            row.date_grade, row.date_service, row.ndecision, row.matricule_militaire, row.matricule_solde,
            row.position_admin, row.fonction, row.subdivision_arme, row.diplome, row.date_obtention,
            row.structure, row.obs]


def detail_sheet(wb, name, title, subset, details, gmap, mois):
    return table_sheet(wb, name, title, DETAIL_HEADERS,
                       [personnel_cells(r, i, gmap) + detail_cells(r, details) for i, r in enumerate(subset, 1)],
                       mois=mois, widths=[7, 10, 35, 16, 28, 24, 24, 24, 15, 32])


def _movements(settings, mois, rows, *, dpf=False):
    from sme_gend.services.monthly_movements import export_month_movements
    movements = export_month_movements(settings, mois)
    if dpf:
        keys = {r.satellite_join_key() for r in rows}
        names = {(r.noms or "").casefold() for r in rows}
        from sme_gend.services.sme_dpf_scope import DPF_STRUCTURE_CODES
        movements = [m for m in movements if str(m.id) in keys
                     or (getattr(m, "structure_code", "") or "").upper() in DPF_STRUCTURE_CODES
                     or (m.nom or "").casefold() in names]
    return movements


def _movement_sheet(wb, name, movements, rows, gmap, mois):
    structures = {(r.noms or "").casefold(): r.structure for r in rows}
    output = [[i, m.objet_display, grade_to_abbrev(m.grade, gmap), m.nom,
               getattr(m, "structure_code", None) or structures.get((m.nom or "").casefold()),
               m.observ_display] for i, m in enumerate(movements, 1)]
    return table_sheet(wb, name, "VARIATION DES EFFECTIFS", ("N°", "Mouvement", "Grade", "Nom(s) et prénom(s)", "Structure", "Motif / observations"),
                       output, mois=mois, widths=[7, 14, 10, 38, 24, 48])


@tracked_export
def build_numerique(settings, destination, *, mois, rows, grades, details, detaches, grade_order, previous_counts,
                    movement_settings=None):
    from sme_gend.services.numerique_stats import RECAP_INDISPO_KEYS
    from sme_gend.services.numerique_excel_export import _prev_ym_canon
    wb = Workbook()
    wb.remove(wb.active)
    export_phase('render', detail='Construction des tableaux du mois')
    gmap = build_grade_abbrev_map(grades)
    table_sheet(wb, "LISTE GENERALE", "LISTE GÉNÉRALE", LIST_HEADERS,
                [list_cells(r, i, gmap) for i, r in enumerate(rows, 1)], mois=mois,
                widths=[7, 36, 15, 23, 10, 15, 15, 25, 20, 18, 22, 25, 25, 22, 18, 18, 32])
    grade_summary(wb, "NUMERIQUE", rows, mois=mois, gmap=gmap, grade_order=grade_order)
    _movement_sheet(wb, "GAIN PERTE", _movements(movement_settings or settings, mois, rows), rows, gmap, mois)
    buckets = sorted({r.position_admin or "Non renseignée" for r in rows if not is_present_strict(r)
                      and (r.position_admin or "").casefold() != "détaché"})
    by_structure = {}
    for row in rows:
        if not is_present_strict(row) and (row.position_admin or "").casefold() != "détaché":
            by_structure.setdefault(row.structure or "Non renseignée", Counter())[row.position_admin or "Non renseignée"] += 1
    recap = [[structure, *(counts[b] for b in buckets), sum(counts.values())] for structure, counts in sorted(by_structure.items())]
    totals = [sum(r[i] for r in recap) for i in range(1, len(buckets) + 2)]
    recap.append(["TOTAL", *totals])
    table_sheet(wb, "INDISPO PAR STRUCTURE bis", "INDISPONIBLES PAR STRUCTURE (HORS DÉTACHÉS)",
                ["Structure", *buckets, "Total"], recap, mois=mois)
    for name, key, title in (
        ("MISSION", "mission", "PERSONNELS EN MISSION"), ("MALADE", "malade", "PERSONNELS MALADES"),
        ("DESERTEUR", "deserteur", "PERSONNELS DÉSERTEURS"), ("CONGé ADMIN", "conge_admin", "CONGÉS ADMINISTRATIFS"),
        ("CONGE EXP", "conge_exp", "CONGÉS D'EXPECTATIVE"), ("DETENTION-PRISON", "prison_ma", "DÉTENTION ET PUNITIONS"),
        ("NON-REJOINT", "inst_mr", "INSTANCE DE MISE EN ROUTE"), ("STAGE-LOC", "stage_local", "STAGES LOCAUX"),
        ("STAGE-EXT", "stage_ex", "STAGES À L'ÉTRANGER"),
    ):
        detail_sheet(wb, name, title, [r for r in rows if row_predicate_for_rubrique(key)(r)], details, gmap, mois)
    internal = [r for r in rows if (r.position_admin or "").casefold().replace(" ", "") == "stageinterne"]
    detail_sheet(wb, "STAGE-INTERNE", "STAGES INTERNES", internal, details, gmap, mois)
    detail_sheet(wb, "DETACHE", "PERSONNELS DÉTACHÉS", detaches, details, gmap, mois)
    counter = Counter(r.subdivision_arme or "Non renseignée" for r in detaches)
    table_sheet(wb, "DETACHE PAR STRUCTURE", "DÉTACHÉS PAR STRUCTURE D'EMPLOI", ("N°", "Structure d'emploi", "Effectif"),
                [[i, code, count] for i, (code, count) in enumerate(sorted(counter.items()), 1)] + [[None, "TOTAL", len(detaches)]], mois=mois)
    presents = [r for r in rows if is_present_strict(r)]
    table_sheet(wb, "LISTE DES PRÉSENTS", "LISTE DES PRÉSENTS", LIST_HEADERS,
                [list_cells(r, i, gmap) for i, r in enumerate(presents, 1)], mois=mois,
                widths=[7, 36, 15, 23, 10, 15, 15, 25, 20, 18, 22, 25, 25, 22, 18, 18, 32])
    previous = previous_counts or [None] * len(RECAP_INDISPO_KEYS)
    variance = []
    for i, (key, label) in enumerate(RECAP_INDISPO_KEYS):
        count = sum(row_predicate_for_rubrique(key)(r) for r in rows)
        before = previous[i] if i < len(previous) else None
        variance.append([label, before, count, count - before if before is not None else None])
    table_sheet(wb, "VARIATION INDISPONIBLES", "VARIATION DES INDISPONIBLES", ("Position", _prev_ym_canon(mois), mois, "Variation"), variance, mois=mois)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with atomic_destination(destination, temporary_suffix=".xlsx") as staged:
            export_phase('write', total=1, detail='Enregistrement du classeur SME')
            wb.save(staged)
        export_phase('finalize', detail='Classeur final installé')
        return tuple(wb.sheetnames)
    finally:
        wb.close()


@tracked_export
def build_dpf(settings, destination, data, grades, details):
    from sme_gend.services.sme_dpf_scope import filter_dpf_rows, is_dpf_stagiaire, is_dpf_control_row
    rows = filter_dpf_rows(data.liste_gnle)
    mois = data.mois
    gmap = build_grade_abbrev_map(grades)
    wb = Workbook()
    wb.remove(wb.active)
    export_phase('render', detail='Construction des feuilles SME DPF')
    grade_summary(wb, "I- SITUATION NUMERIQUE DES EFF", rows, mois=mois, gmap=gmap,
                  grade_order=[grade_to_abbrev(g.code, gmap) for g in grades], dpf=True)
    for name, title, subset in (
        ("LISTE GNLE", "DIRECTION DU PERSONNEL ET DE LA FORMATION — LISTE GÉNÉRALE", rows),
        ("III- LISTE DE PRESENTS", "LISTE DES PRÉSENTS — DPF ET CONTRÔLE SPÉCIAL", [r for r in rows if is_present_strict(r)]),
        ("V- CONTROLE SPECIAL DPF", "CONTRÔLE SPÉCIAL DPF — TOUTES POSITIONS", [r for r in rows if is_dpf_control_row(r)]),
    ):
        table_sheet(wb, name, title, LISTE_GNLE_HEADERS,
                    [row_to_dpf_liste_cells(r, i, grades) for i, r in enumerate(subset, 1)], mois=mois,
                    widths=[7, 36, 28, 10, 15, 15, 25, 18, 18, 22, 25, 24, 24, 25, 18, 32])
    detail_sheet(wb, "IV- INDISPONIBLES", "INDISPONIBLES — DPF ET CONTRÔLE SPÉCIAL",
                 [r for r in rows if not is_present_strict(r)], details, gmap, mois)
    detail_sheet(wb, "VI-STAGIAIRES", "STAGIAIRES — TYPES LOCAL, INTERNE ET ÉTRANGER",
                 [r for r in rows if is_dpf_stagiaire(r)], details, gmap, mois)
    _movement_sheet(wb, "VII- ETAT DE VARIATION DES EFF.", _movements(settings, mois, rows, dpf=True), rows, gmap, mois)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with atomic_destination(destination, temporary_suffix=".xlsx") as staged:
            export_phase('write', total=1, detail='Enregistrement du classeur SME DPF')
            wb.save(staged)
        export_phase('finalize', detail='Classeur DPF final installé')
    finally:
        wb.close()
