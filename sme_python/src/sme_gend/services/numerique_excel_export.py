"""Export classeur situation numérique : gabarit Excel rempli depuis Access (liste + matrices)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.services.sme_export_revision import ExportRevision

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow, SubdivisionRepository
from sme_gend.repositories.numerique_satellite_repository import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    NumeriqueSatelliteRepository,
    StageDetail,
)
from sme_gend.services.numerique_padmin import (
    PADMIN_CONGE_ADMIN,
    PADMIN_DESERTEUR,
    PADMIN_INST_MR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_PRISON_MA,
    PADMIN_PUNIS,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.services.numerique_stats import (
    INDISPO_CATEGORY_SPECS,
    RECAP_INDISPO_KEYS,
    RUBRIQUE_KEYS_ROW8_19,
    build_indispo_matrix,
    build_numerique_matrix,
    extract_structure_codes_from_indispo_formula,
    matrix_row_total,
)
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display
from sme_gend.services.excel_style_helpers import (
    apply_data_block_styles,
    apply_row_styles,
    build_grade_abbrev_map,
    clear_cell_value,
    clear_row_values,
    grade_to_abbrev,
    is_section_title,
    snapshot_row_styles,
    unmerge_from_row,
    writable_cell,
    write_cell_value,
)

# Bloc « effectif détaillé des présents par grade » (ligne 6, colonnes BI→BZ).
_BH_PRESENTS_ROW = 6
_BH_GRADE_COL_START = 61  # BI

LISTE_HEADER_ROW = 3
LISTE_DATA_START = 4
NUM_GRADE_ROW = 7
NUM_FIRST_DATA_START = 8
NUM_FIRST_DATA_END = 19
NUM_SECOND_HEADER_ROW = 37
NUM_SECOND_DATA_START = 38
NUM_SECOND_DATA_END = 47
NUM_SECOND_TOTAL_ROW = 48

INDISPO_SHEET = "INDISPO PAR STRUCTURE bis"
INDISPO_DATA_START = 4
INDISPO_DATA_END = 44
INDISPO_TOTAL_ROW = 45
INDISPO_CAT_COL_START = 2
INDISPO_CAT_COL_END = 9
INDISPO_TOTAL_COL = 10

# Feuilles satellites (option A : une ligne par Padmin même sans détail Tab*).
SHEET_MISSION = "MISSION"
SHEET_MALADE = "MALADE"
SHEET_DESERTEUR = "DESERTEUR"
SHEET_STAGE_LOC = "STAGE-LOC"
SHEET_STAGE_EXT = "STAGE-EXT"
SHEET_GAIN_PERTE = "GAIN PERTE"
# Gabarit historique : « CONGé ADMIN » (é minuscule) — ne pas confondre avec É majuscule.
SHEET_CONGE_ADMIN = "CONGé ADMIN"
SHEET_DETENTION = "DETENTION-PRISON"
SHEET_NON_REJOINT = "NON-REJOINT"
SHEET_DETACHE = "DETACHE"
SHEET_DETACHE_PAR = "DETACHE PAR STRUCTURE"
SHEET_LISTE_PRESENTS = "LISTE DES PRÉSENTS"
SATELLITE_DATA_START = 4

INDISPO_MONTH_TITLE_COL = 11


def _normalize_sheet_key(name: str) -> str:
    """Clé de comparaison insensible à la casse et aux accents."""
    import unicodedata

    s = unicodedata.normalize("NFKD", (name or "").strip().casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _workbook_sheet(wb, *candidates: str) -> Worksheet | None:
    """Retourne la feuille dont le nom correspond (accents / casse tolérés)."""
    wanted = {_normalize_sheet_key(c) for c in candidates if c}
    for actual in wb.sheetnames:
        if _normalize_sheet_key(actual) in wanted:
            return wb[actual]
    return None

# Ordre des grades (feuille NUMERIQUE ligne 7) si le gabarit est absent.
DEFAULT_NUMERIQUE_GRADES: tuple[str, ...] = (
    "GDB",
    "CLM",
    "COL",
    "LCL",
    "CDT",
    "CNE",
    "LTN",
    "SLT",
    "ASP",
    "ADM",
    "ADC",
    "ADJ",
    "MDC",
    "SCH",
    "MDL",
    "SGT",
    "SDT",
    "CIV",
)

_MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

# Libellés colonnes récap (ligne 4) — alignés sur l’aperçu HTML (capitalisation française).
_MONTHS_FR_CAP = (
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
)

# Colonnes récap « variation des indisponibles » (feuille NUMERIQUE).
_RECAP_COL_CD = 82
_RECAP_COL_CE = 83
_RECAP_COL_CF = 84
_RECAP_ROW_FIRST = 5
_RECAP_ROW_TOTAL = 14

_GAIN_PERTE_HEADERS = ("N°", "GRADE", "NOM (S) ET PRENOM (S)", "STRUCTURES", "OBS")


def _month_index_vowel_start(month_idx_0: int) -> bool:
    """Mois commençant par une voyelle (élision : d'avril, d'août, d'octobre)."""
    lo = _MONTHS_FR[month_idx_0]
    return lo[0].lower() in "aeéèêiuoàâäôùû"


def _fr_title_mois_de(year: int, month_idx_0: int) -> str:
    """Titre principal type « Mois d'avril 2026 » / « Mois de mars 2026 »."""
    name = _MONTHS_FR[month_idx_0]
    part = f"d'{name}'" if _month_index_vowel_start(month_idx_0) else f"de {name}"
    return f"Mois {part} {year}"


def _month_title(when: datetime | None = None) -> str:
    d = when or datetime.now()
    return _fr_title_mois_de(d.year, d.month - 1)


def _ym_canon_to_cap_label(ym_canon: str) -> str:
    y, m = int(ym_canon[:4]), int(ym_canon[5:7])
    return f"{_MONTHS_FR_CAP[m - 1]} {y}"


def _prev_ym_canon(ym_canon: str) -> str:
    y, m = int(ym_canon[:4]), int(ym_canon[5:7])
    m -= 1
    if m < 1:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def recap_column_headers_from_mois_actif(mois: str) -> tuple[str, str] | None:
    """En-têtes colonnes « mois figé » / « mois courant » (ligne 4 CD / CE), comme l’aperçu HTML."""
    c = canonical_sme_mois_yyyy_mm((mois or "").strip())
    if not c:
        return None
    prev_c = _prev_ym_canon(c)
    return _ym_canon_to_cap_label(prev_c), _ym_canon_to_cap_label(c)


def month_title_from_mois_actif(mois: str | None) -> str | None:
    """Titre A3 : « Mois d'avril 2026 » d’après le mois SME (même mois que l’en-tête CE du récap)."""
    c = canonical_sme_mois_yyyy_mm((mois or "").strip())
    if not c:
        return None
    y, m = int(c[:4]), int(c[5:7])
    return _fr_title_mois_de(y, m - 1)


def month_title_de_lowercase(mois: str | None) -> str | None:
    """Libellé « Mois de février 2026 » — feuilles GAIN PERTE / INDISPO."""
    c = canonical_sme_mois_yyyy_mm((mois or "").strip())
    if not c:
        return None
    y, m = int(c[:4]), int(c[5:7])
    return f"Mois de {_MONTHS_FR[m - 1]} {y}"


def _read_recap_cd_column_from_ws(ws: Worksheet) -> list[int | None]:
    """Valeurs colonne CD (mois figé), lignes 5–14 — attendu : feuille ouverte en data_only=True."""
    out: list[int | None] = [None] * 10
    for i, r in enumerate(range(_RECAP_ROW_FIRST, _RECAP_ROW_TOTAL + 1)):
        v = ws.cell(row=r, column=_RECAP_COL_CD).value
        if v is None:
            continue
        try:
            out[i] = int(v)
        except (TypeError, ValueError):
            out[i] = None
    return out


def read_recap_cd_values_from_template_path(path: Path) -> list[int | None]:
    """Lit la colonne CD (valeurs calculées) depuis le gabarit — obligatoire data_only pour formules."""
    if not path.is_file():
        return [None] * 10
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if "NUMERIQUE" not in wb.sheetnames:
            return [None] * 10
        return _read_recap_cd_column_from_ws(wb["NUMERIQUE"])
    finally:
        wb.close()


def _materialize_recap_cd_baseline(ws: Worksheet, cd_vals: list[int | None]) -> None:
    """
    Réécrit la colonne CD (lignes 5–14) en valeurs entières (baseline mois M-1 depuis MySQL).

    Remplace d’éventuelles formules Excel en CD pour que le fichier exporté reflète
    exactement CE − CD, comme l’aperçu HTML.
    """
    vals = cd_vals if len(cd_vals) >= 10 else [*cd_vals, *([None] * (10 - len(cd_vals)))]
    for i in range(9):
        row = _RECAP_ROW_FIRST + i
        v = vals[i]
        write_cell_value(ws, row, _RECAP_COL_CD, (int(v) if v is not None else None))
    vtot = vals[9]
    write_cell_value(
        ws,
        _RECAP_ROW_TOTAL,
        _RECAP_COL_CD,
        int(vtot) if vtot is not None else None,
    )


def _write_recap_variation_ce_cf(
    ws: Worksheet,
    matrix: dict[str, dict[str, int]],
    grades: list[str],
    cd_vals: list[int | None],
) -> None:
    """Écrit totaux courants (CE) et variations CE−CD (CF), aligné sur l’aperçu HTML."""
    vals = cd_vals if len(cd_vals) >= 10 else [*cd_vals, *([None] * (10 - len(cd_vals)))]
    for idx, (key, _lib) in enumerate(RECAP_INDISPO_KEYS):
        row = _RECAP_ROW_FIRST + idx
        cur = matrix_row_total(matrix, key, grades)
        write_cell_value(ws, row, _RECAP_COL_CE, int(cur))
        prev = vals[idx]
        if prev is not None:
            write_cell_value(ws, row, _RECAP_COL_CF, int(cur) - int(prev))
        else:
            write_cell_value(ws, row, _RECAP_COL_CF, None)

    row_tot = _RECAP_ROW_TOTAL
    cur_sum = sum(matrix_row_total(matrix, key, grades) for key, _ in RECAP_INDISPO_KEYS)
    write_cell_value(ws, row_tot, _RECAP_COL_CE, int(cur_sum))
    prev_sum = vals[9]
    if prev_sum is not None:
        write_cell_value(ws, row_tot, _RECAP_COL_CF, int(cur_sum) - int(prev_sum))
    else:
        write_cell_value(ws, row_tot, _RECAP_COL_CF, None)


def _write_recap_row4_headers(ws: Worksheet, mois_actif: str | None) -> None:
    """Libellés dynamiques CD4 / CE4 / CF4 (ligne 4) lorsque le mois SME est connu — ne modifie pas CD5–CD14."""
    if not mois_actif:
        return
    hdr = recap_column_headers_from_mois_actif(mois_actif)
    if hdr is None:
        return
    mois_fige, mois_courant = hdr
    write_cell_value(ws, 4, _RECAP_COL_CD, mois_fige)
    write_cell_value(ws, 4, _RECAP_COL_CE, mois_courant)
    cur_cf = ws.cell(row=4, column=_RECAP_COL_CF).value
    if cur_cf is None or (isinstance(cur_cf, str) and not str(cur_cf).strip()):
        write_cell_value(ws, 4, _RECAP_COL_CF, "Variation")


def fill_gain_perte_template_sheet(
    ws: Worksheet,
    rows_gp: list,
    *,
    month_label: str | None = None,
    structure_by_nom: dict[str, str] | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    """Remplit la feuille gabarit « GAIN PERTE » (sections A- GAINS / B- PERTES) avec bordures."""
    style_section = snapshot_row_styles(ws, 4, 1, 5)
    style_header = snapshot_row_styles(ws, 6, 1, 5)
    style_data = snapshot_row_styles(ws, 7, 1, 5)
    gmap = grade_abbrev_map or {}

    if month_label:
        write_cell_value(ws, 2, 1, month_label)

    gains = sorted(
        [r for r in rows_gp if getattr(r, "is_gain", False)],
        key=lambda r: ((r.nom or "").casefold(), (r.grade or "").casefold()),
    )
    pertes = sorted(
        [r for r in rows_gp if getattr(r, "is_perte", False)],
        key=lambda r: ((r.nom or "").casefold(), (r.grade or "").casefold()),
    )
    lookup = structure_by_nom or {}

    if ws.max_row > 3:
        ws.delete_rows(4, ws.max_row - 3)

    row = 4
    write_cell_value(ws, row, 1, "A- GAINS")
    apply_row_styles(ws, row, style_section, col_start=1)
    row += 2
    for c_i, h in enumerate(_GAIN_PERTE_HEADERS, start=1):
        write_cell_value(ws, row, c_i, h)
    apply_row_styles(ws, row, style_header, col_start=1)
    row += 1
    for i, r in enumerate(gains, start=1):
        nom = (r.nom or "").strip()
        write_cell_value(ws, row, 1, i)
        write_cell_value(ws, row, 2, grade_to_abbrev((r.grade or "").strip(), gmap))
        write_cell_value(ws, row, 3, nom)
        write_cell_value(ws, row, 4, lookup.get(nom.casefold(), "") or (getattr(r, "structure_code", None) or ""))
        write_cell_value(ws, row, 5, r.observ_display)
        apply_row_styles(ws, row, style_data, col_start=1)
        row += 1

    row += 1
    write_cell_value(ws, row, 1, "B- PERTES")
    apply_row_styles(ws, row, style_section, col_start=1)
    row += 2
    for c_i, h in enumerate(_GAIN_PERTE_HEADERS, start=1):
        write_cell_value(ws, row, c_i, h)
    apply_row_styles(ws, row, style_header, col_start=1)
    row += 1
    for i, r in enumerate(pertes, start=1):
        nom = (r.nom or "").strip()
        write_cell_value(ws, row, 1, i)
        write_cell_value(ws, row, 2, grade_to_abbrev((r.grade or "").strip(), gmap))
        write_cell_value(ws, row, 3, nom)
        write_cell_value(ws, row, 4, lookup.get(nom.casefold(), "") or (getattr(r, "structure_code", None) or ""))
        write_cell_value(ws, row, 5, r.observ_display)
        apply_row_styles(ws, row, style_data, col_start=1)
        row += 1


def fill_gains_pertes_sheet(
    wb,
    settings: Settings,
    mois_actif: str | None,
    *,
    gain_perte_settings: Settings | None = None,
    rows: list[ListeGeneraleRow] | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    """Feuille « GAIN PERTE » du gabarit — mouvements du mois SME consulté."""
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository

    gp_settings = gain_perte_settings or settings
    ws = _workbook_sheet(wb, SHEET_GAIN_PERTE)
    if ws is None:
        return
    ma = (mois_actif or "").strip()
    month_label = month_title_de_lowercase(ma) if ma else None

    rows_gp: list = []
    if ma:
        try:
            rows_gp = GainPerteRepository(gp_settings).fetch_for_month(ma)
        except Exception as exc:
            raise RuntimeError(
                f"Export annulé : lecture des gains et pertes impossible pour {ma}. "
                "Vérifiez la connexion MySQL puis relancez l'export."
            ) from exc
        try:
            from sme_gend.services.gain_perte_coherence import load_unsignaled_pertes

            rows_gp = list(rows_gp) + load_unsignaled_pertes(gp_settings, ma, rows_gp)
        except Exception as exc:
            raise RuntimeError(
                f"Export annulé : vérification des pertes non signalées impossible pour {ma}. "
                "Vérifiez les données des deux mois comparés puis relancez l'export."
            ) from exc

    structure_by_nom: dict[str, str] = {}
    if rows:
        for r in rows:
            nom = (r.noms or "").strip()
            if nom:
                structure_by_nom[nom.casefold()] = (r.structure or "").strip()

    fill_gain_perte_template_sheet(
        ws,
        rows_gp,
        month_label=month_label,
        structure_by_nom=structure_by_nom,
        grade_abbrev_map=grade_abbrev_map,
    )


def read_numerique_grades_from_template(template_path: Path | None) -> list[str]:
    """Codes grade (ligne 7) depuis le gabarit, ou liste par défaut."""
    if not template_path or not Path(template_path).is_file():
        return list(DEFAULT_NUMERIQUE_GRADES)
    wb = load_workbook(Path(template_path), read_only=True, data_only=True)
    try:
        if "NUMERIQUE" not in wb.sheetnames:
            return list(DEFAULT_NUMERIQUE_GRADES)
        ws = wb["NUMERIQUE"]
        grades, _ = _parse_grade_columns(ws)
        return grades if grades else list(DEFAULT_NUMERIQUE_GRADES)
    finally:
        wb.close()


def collect_indispo_structure_meta(
    ws: Worksheet,
) -> tuple[list[str], list[list[str]]]:
    """
    Pour la feuille INDISPO : libellés (col. A) et codes structure par ligne,
    dans l'ordre des lignes données (hors TOTAL).
    """
    labels: list[str] = []
    row_codes: list[list[str]] = []
    for r in range(INDISPO_DATA_START, INDISPO_DATA_END + 1):
        label = ws.cell(row=r, column=1).value
        lab = (str(label).strip() if label is not None else "") or ""
        if not lab or lab.upper() == "TOTAL":
            continue
        f = ws.cell(row=r, column=2).value
        codes = extract_structure_codes_from_indispo_formula(f if isinstance(f, str) else None)
        if not codes:
            codes = [lab]
        labels.append(lab)
        row_codes.append(codes)
    return labels, row_codes


def _parse_grade_columns(ws: Worksheet, row: int = NUM_GRADE_ROW) -> tuple[list[str], list[int]]:
    """Codes grade et indices colonnes (hors TOTAL)."""
    grades: list[str] = []
    cols: list[int] = []
    c = 2
    while c <= ws.max_column:
        v = ws.cell(row, c).value
        if v is None:
            break
        if isinstance(v, str):
            s = v.strip()
            if s.upper() == "TOTAL" or s == "":
                break
            grades.append(s)
            cols.append(c)
        c += 1
    return grades, cols


def _write_month_titles(ws: Worksheet, title: str) -> None:
    """Titre période : A3 toujours ; A33 seulement si la cellule est vide (évite doublon inutile sur gabarit)."""
    write_cell_value(ws, 3, 1, title)
    v33 = ws.cell(33, 1).value
    if v33 is None or (isinstance(v33, str) and not str(v33).strip()):
        write_cell_value(ws, 33, 1, title)


def _fill_liste_generale(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    subdivision_flat: Sequence[SubdivisionFlatRow] | None = None,
    month_label: str | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    """
    Remplit la feuille « LISTE GENERALE » à partir de ``ListeGenerale`` (ordre identique au SELECT Access).

    Colonne A : numéro d’ordre 1…n (ligne de liste), pas l’Id Access — aligné sur une colonne « N° » maquette.
    Colonnes B–Q : noms, dates/lieux, grade, Padmin, structure, etc., dans le même ordre que le repository.
    """
    gmap = grade_abbrev_map or {}
    if month_label:
        write_cell_value(ws, 2, 1, month_label)
    max_old = ws.max_row
    n = len(rows)
    for i, row in enumerate(rows):
        r = LISTE_DATA_START + i
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, row.noms)
        write_cell_value(ws, r, 3, row.date_naissance)
        write_cell_value(ws, r, 4, row.lieu_naissance)
        write_cell_value(ws, r, 5, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 6, row.date_grade)
        write_cell_value(ws, r, 7, row.date_service)
        write_cell_value(ws, r, 8, row.ndecision)
        write_cell_value(ws, r, 9, row.matricule_militaire)
        write_cell_value(ws, r, 10, row.matricule_solde)
        write_cell_value(ws, r, 11, row.position_admin)
        write_cell_value(ws, r, 12, row.fonction)
        sub_cell = (
            format_subdivision_arme_display(row.subdivision_arme, subdivision_flat)
            if subdivision_flat is not None
            else row.subdivision_arme
        )
        write_cell_value(ws, r, 13, sub_cell)
        write_cell_value(ws, r, 14, row.diplome)
        write_cell_value(ws, r, 15, row.date_obtention)
        write_cell_value(ws, r, 16, row.structure)
        write_cell_value(ws, r, 17, row.obs)
    apply_data_block_styles(
        ws,
        template_row=LISTE_DATA_START,
        data_start=LISTE_DATA_START,
        n_rows=n,
        col_start=1,
        col_end=17,
    )
    for r in range(LISTE_DATA_START + n, max_old + 1):
        clear_row_values(ws, r, 1, 17)
    _apply_a4_landscape_page_setup(ws, n_data_rows=n)


def _apply_a4_landscape_page_setup(ws: Worksheet, *, n_data_rows: int, last_col: str = "Q") -> None:
    """Impression A4 paysage — liste générale (aligné gabarit historique)."""
    from openpyxl.worksheet.properties import PageSetupProperties

    last_row = max(LISTE_DATA_START + n_data_rows, LISTE_HEADER_ROW + 1)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.print_area = f"A1:{last_col}{last_row}"
    if ws.sheet_properties.pageSetUpPr is None:
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    else:
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


def _apply_a4_portrait_page_setup(ws: Worksheet, *, n_data_rows: int, last_col: str = "Q") -> None:
    """Impression A4 portrait — feuilles annexes (ex. liste des présents)."""
    from openpyxl.worksheet.properties import PageSetupProperties

    last_row = max(LISTE_DATA_START + n_data_rows, LISTE_HEADER_ROW + 1)
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.print_area = f"A1:{last_col}{last_row}"
    if ws.sheet_properties.pageSetUpPr is None:
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    else:
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


def _padmin_matches(row: ListeGeneraleRow, code: str) -> bool:
    """Comme le tableau de bord : égalité insensible à la casse."""
    a = (row.position_admin or "").strip()
    if not a:
        return False
    return a == code or a.casefold() == code.casefold()


def _sort_liste_rows(
    rows: list[ListeGeneraleRow],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
) -> list[ListeGeneraleRow]:
    if grades:
        from sme_gend.models.grade_ref import GradeRefRow
        from sme_gend.services.personnel_list_order import sort_liste_rows_like_ui

        return sort_liste_rows_like_ui(rows, grades, settings)  # type: ignore[arg-type]
    return sorted(
        rows,
        key=lambda r: (
            r.ordre_tri if r.ordre_tri is not None else 10**9,
            r.presseance if r.presseance is not None else 10**9,
            (r.noms or "").casefold(),
        ),
    )


def _parse_satellite_grade_columns(ws: Worksheet) -> list[tuple[str, int]]:
    """
    Ligne 1 = codes grade, ligne 2 = effectifs (0) — comme le gabarit MALADE / DESERTEUR / STAGE.
    MISSION n’a pas ce bloc : renvoie [].
    """
    out: list[tuple[str, int]] = []
    for c in range(2, min(ws.max_column, 45) + 1):
        r1 = ws.cell(1, c).value
        r2 = ws.cell(2, c).value
        if not isinstance(r1, str):
            continue
        s = r1.strip()
        if not s or len(s) > 10:
            continue
        u = s.upper()
        if "LISTE" in u:
            continue
        if isinstance(r2, (int, float)):
            out.append((s, c))
    return out


def _apply_satellite_grade_counts(
    ws: Worksheet,
    filtered: list[ListeGeneraleRow],
    grade_cols: list[tuple[str, int]],
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    if not grade_cols:
        return
    gmap = grade_abbrev_map or {}
    counts: dict[int, int] = {col: 0 for _, col in grade_cols}
    for row in filtered:
        g = grade_to_abbrev(row.grade, gmap)
        if not g:
            continue
        gf = g.casefold()
        for gbanner, col in grade_cols:
            if gbanner.casefold() == gf:
                counts[col] = counts.get(col, 0) + 1
                break
    for _, col in grade_cols:
        write_cell_value(ws, 2, col, int(counts.get(col, 0)))


def _clear_satellite_block(
    ws: Worksheet,
    *,
    data_start: int,
    n_filled: int,
    clear_cols: int,
    max_row_before: int,
) -> None:
    start_clear = data_start + n_filled
    for r in range(start_clear, max_row_before + 1):
        for c in range(1, clear_cols + 1):
            clear_cell_value(ws, r, c)


def fill_mission_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    missions: dict[str, MissionDetail],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, PADMIN_MISSION)],
        grades=grades,
        settings=settings,
    )
    max_row_before = ws.max_row
    data_cols = 8
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = missions.get(row.satellite_join_key())
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if d is not None:
            write_cell_value(ws, r, 5, d.nature)
            write_cell_value(ws, r, 6, d.lieu_mission)
            write_cell_value(ws, r, 7, d.refnds)
            write_cell_value(ws, r, 8, d.date_depart)
        else:
            for c in range(5, data_cols + 1):
                write_cell_value(ws, r, c, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=data_cols,
        max_row_before=max_row_before,
    )


def fill_malade_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    malades: dict[str, MaladeDetail],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}
    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, PADMIN_MALADE)],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = malades.get(row.satellite_join_key())
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if d is not None:
            write_cell_value(ws, r, 5, d.date_constat)
            write_cell_value(ws, r, 6, d.pathologie)
            write_cell_value(ws, r, 7, d.obs)
        else:
            for c in range(5, 8):
                write_cell_value(ws, r, c, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def fill_deserteur_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    deserteurs: dict[str, DeserteurDetail],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}
    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, PADMIN_DESERTEUR)],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    data_cols = 5
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = deserteurs.get(row.satellite_join_key())
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if d is not None:
            write_cell_value(ws, r, 5, d.dat_desertion)
        else:
            write_cell_value(ws, r, 5, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def fill_stage_loc_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    stages: dict[str, StageDetail],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}
    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, PADMIN_STAGE_LOCAL)],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = stages.get(row.satellite_join_key())
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if d is not None:
            write_cell_value(ws, r, 5, d.nature_stage)
            write_cell_value(ws, r, 6, d.lieu_stage)
            write_cell_value(ws, r, 7, d.date_debut_fin)
        else:
            for c in range(5, 8):
                write_cell_value(ws, r, c, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def fill_stage_ext_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    stages: dict[str, StageDetail],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}
    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, PADMIN_STAGE_EX)],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = stages.get(row.satellite_join_key())
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if d is not None:
            write_cell_value(ws, r, 5, d.nature_stage)
            write_cell_value(ws, r, 6, d.lieu_stage)
            write_cell_value(ws, r, 7, d.date_debut_fin)
        else:
            for c in range(5, 8):
                write_cell_value(ws, r, c, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def _padmin_one_of(row: ListeGeneraleRow, codes: tuple[str, ...]) -> bool:
    a = (row.position_admin or "").strip().casefold()
    return a in {c.casefold() for c in codes}


def fill_simple_grade_satellite_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    padmin_code: str,
    *,
    data_cols: int,
    extra_values: Callable[[ListeGeneraleRow], Sequence] | None = None,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    """Feuille satellite avec compteurs par grade (ligne 1–2) et colonnes N°… structure."""
    gmap = grade_abbrev_map or {}
    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, padmin_code)],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        if extra_values is not None:
            for c_idx, val in enumerate(extra_values(row), start=5):
                if c_idx <= data_cols:
                    write_cell_value(ws, r, c_idx, val)
        else:
            for c in range(5, data_cols + 1):
                write_cell_value(ws, r, c, None)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def fill_conge_admin_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    fill_simple_grade_satellite_sheet(
        ws,
        rows,
        PADMIN_CONGE_ADMIN,
        data_cols=5,
        extra_values=lambda r: ((r.obs or "").strip() or None,),
        grades=grades,
        settings=settings,
        grade_abbrev_map=grade_abbrev_map,
    )


def fill_non_rejoint_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    fill_simple_grade_satellite_sheet(
        ws,
        rows,
        PADMIN_INST_MR,
        data_cols=5,
        extra_values=lambda r: ((r.obs or "").strip() or None,),
        grades=grades,
        settings=settings,
        grade_abbrev_map=grade_abbrev_map,
    )


def fill_detention_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    grades: list | None = None,
    settings: Settings | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    gmap = grade_abbrev_map or {}

    def _extra(row: ListeGeneraleRow) -> tuple:
        obs = (row.obs or "").strip()
        lieu = (row.structure or "").strip() or None
        return (lieu, obs or None, obs or None)

    max_row_before = ws.max_row
    filt = _sort_liste_rows(
        [r for r in rows if _padmin_one_of(r, (PADMIN_PUNIS, PADMIN_PRISON_MA))],
        grades=grades,
        settings=settings,
    )
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols, grade_abbrev_map=gmap)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        write_cell_value(ws, r, 1, i + 1)
        write_cell_value(ws, r, 2, grade_to_abbrev(row.grade, gmap))
        write_cell_value(ws, r, 3, row.noms)
        write_cell_value(ws, r, 4, row.structure)
        lieu, motif, peine = _extra(row)
        write_cell_value(ws, r, 5, lieu)
        write_cell_value(ws, r, 6, motif)
        write_cell_value(ws, r, 7, peine)
    apply_data_block_styles(
        ws,
        template_row=SATELLITE_DATA_START,
        data_start=SATELLITE_DATA_START,
        n_rows=len(filt),
        col_start=1,
        col_end=data_cols,
    )
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


def fill_detache_par_structure_sheet(
    ws: Worksheet,
    cfg,
    detaches: Sequence[ListeGeneraleRow],
    *,
    settings: Settings | None = None,
) -> None:
    """Récapitulatif des détachés par structure d'emploi — format tableau gabarit."""
    from collections import defaultdict

    from sme_gend.services.detaches_sheet_writer import _norm_sigle_key, _structure_sort_key

    style_header = snapshot_row_styles(ws, 3, 1, 5)
    style_section = snapshot_row_styles(ws, 4, 1, 5)
    style_data = snapshot_row_styles(ws, 5, 1, 5)

    by_sigle: dict[str, list[ListeGeneraleRow]] = defaultdict(list)
    for r in detaches:
        by_sigle[_norm_sigle_key(r)].append(r)

    unmerge_from_row(ws, 4)
    max_clear = ws.max_row
    for r in range(4, max_clear + 1):
        clear_row_values(ws, r, 1, 5)

    row = 4
    n_global = 0
    last_section: str | None = None
    opt = cfg.options
    ordered = sorted(cfg.structures, key=lambda e: _structure_sort_key(e, opt.sigle_presidence))

    def _write_section(title: str, sigle: str) -> None:
        nonlocal row
        write_cell_value(ws, row, 1, title)
        write_cell_value(ws, row, 5, sigle)
        apply_row_styles(ws, row, style_section, col_start=1)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        row += 1

    def _write_data(num: int, label: str, count: int, sigle: str) -> None:
        nonlocal row
        write_cell_value(ws, row, 1, num)
        write_cell_value(ws, row, 2, label)
        write_cell_value(ws, row, 3, count)
        write_cell_value(ws, row, 5, sigle)
        apply_row_styles(ws, row, style_data, col_start=1)
        row += 1

    for ent in ordered:
        count = len(by_sigle.get(ent.sigle, []))
        if count <= 0:
            continue
        titres = list(ent.titres_lignes) or [ent.sigle]
        section_title = titres[0] if len(titres) > 1 and is_section_title(titres[0]) else None
        label = titres[-1] if titres else ent.sigle

        if section_title and section_title != last_section:
            _write_section(section_title, ent.sigle)
            last_section = section_title
            n_global = 0

        n_global += 1
        _write_data(n_global, label, count, ent.sigle)

    unknown = [
        sig
        for sig, lst in by_sigle.items()
        if sig not in {s.sigle for s in cfg.structures} and lst
    ]
    if unknown:
        _write_section(opt.libelle_bloc_non_reference, "")
        n_global = 0
        for sig in sorted(unknown):
            count = len(by_sigle[sig])
            n_global += 1
            _write_data(n_global, sig, count, sig)


def _copy_worksheet_into_workbook(src_ws: Worksheet, dest_wb, sheet_name: str) -> Worksheet:
    """Copie fidèle d'une feuille (styles, fusions, largeurs) vers un classeur cible."""
    from copy import copy

    if sheet_name in dest_wb.sheetnames:
        idx = dest_wb.sheetnames.index(sheet_name)
        dest_wb.remove(dest_wb[sheet_name])
        dest_ws = dest_wb.create_sheet(sheet_name, idx)
    else:
        dest_ws = dest_wb.create_sheet(sheet_name)

    for row in src_ws.iter_rows():
        for cell in row:
            write_cell_value(dest_ws, cell.row, cell.column, cell.value)
            dest = writable_cell(dest_ws, cell.row, cell.column)
            if cell.has_style:
                dest.font = copy(cell.font)
                dest.border = copy(cell.border)
                dest.fill = copy(cell.fill)
                dest.number_format = cell.number_format
                dest.protection = copy(cell.protection)
                dest.alignment = copy(cell.alignment)

    for col_letter, dim in src_ws.column_dimensions.items():
        dest_ws.column_dimensions[col_letter].width = dim.width

    for row_num, dim in src_ws.row_dimensions.items():
        if dim.height is not None:
            dest_ws.row_dimensions[row_num].height = dim.height

    for merge in src_ws.merged_cells.ranges:
        dest_ws.merge_cells(str(merge))

    dest_ws.page_setup.orientation = src_ws.page_setup.orientation
    dest_ws.page_setup.paperSize = src_ws.page_setup.paperSize
    dest_ws.page_setup.fitToWidth = src_ws.page_setup.fitToWidth
    dest_ws.page_setup.fitToHeight = src_ws.page_setup.fitToHeight
    if src_ws.print_area:
        dest_ws.print_area = src_ws.print_area

    return dest_ws


def add_liste_present_sheet(
    wb,
    settings: Settings,
    *,
    month_label: str | None = None,
) -> None:
    """Ajoute la feuille « LISTE DES PRÉSENTS » (export fidèle maquette resultat.xlsx)."""
    import tempfile

    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_service import ListePresentService

    layout_cfg = LayoutConfigService(settings)
    svc = ListePresentService(settings, layout_cfg)
    layout = layout_cfg.load()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        svc.export_excel(tmp_path)
        src_wb = load_workbook(tmp_path, data_only=False, keep_vba=False)
        try:
            src_name = layout.output_sheet if layout.output_sheet in src_wb.sheetnames else src_wb.sheetnames[0]
            dest_ws = _copy_worksheet_into_workbook(src_wb[src_name], wb, SHEET_LISTE_PRESENTS)
            if month_label:
                write_cell_value(dest_ws, 2, 1, month_label)
            write_cell_value(dest_ws, 1, 1, "LISTE DES PRÉSENTS")
            _apply_a4_portrait_page_setup(dest_ws, n_data_rows=max(0, dest_ws.max_row - LISTE_DATA_START))
        finally:
            src_wb.close()
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_workbook_month_labels(wb, *, eff_mois: str | None) -> None:
    """Met à jour les libellés « Mois de … » / « Mois d'… » sur toutes les feuilles concernées."""
    import re

    month_de = month_title_de_lowercase(eff_mois)
    month_title = month_title_from_mois_actif(eff_mois)
    if not month_de and not month_title:
        return

    ws_gp = _workbook_sheet(wb, SHEET_GAIN_PERTE)
    if ws_gp is not None and month_de:
        write_cell_value(ws_gp, 2, 1, month_de)
    ws_ind = _workbook_sheet(wb, INDISPO_SHEET)
    if ws_ind is not None and month_de:
        write_cell_value(ws_ind, 2, INDISPO_MONTH_TITLE_COL, month_de)
    ws_num = wb["NUMERIQUE"] if "NUMERIQUE" in wb.sheetnames else None
    if ws_num is not None and month_title:
        write_cell_value(ws_num, 3, 1, month_title)
        _write_recap_row4_headers(ws_num, eff_mois)
    ws_liste = _workbook_sheet(wb, "LISTE GENERALE")
    if ws_liste is not None and month_de:
        write_cell_value(ws_liste, 2, 1, month_de)
    ws_lp = _workbook_sheet(wb, SHEET_LISTE_PRESENTS)
    if ws_lp is not None and month_de:
        write_cell_value(ws_lp, 2, 1, month_de)

    month_re = re.compile(
        r"^Mois\s+(?:de|d')(?P<name>[a-zàâäéèêëïîôùûüç]+)\s+(?P<year>\d{4})$",
        re.IGNORECASE,
    )
    if not month_de:
        return
    target_de = month_de.casefold()
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=8, max_col=min(ws.max_column or 1, 90)):
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                s = v.strip()
                if not s:
                    continue
                if month_re.match(s) and s.casefold() != target_de:
                    write_cell_value(ws, cell.row, cell.column, month_de)
                elif month_title and s.startswith("Mois ") and s != month_title:
                    low = s.casefold()
                    if low.startswith("mois de ") or low.startswith("mois d'"):
                        write_cell_value(ws, cell.row, cell.column, month_title)


def _write_matrix_row(
    ws: Worksheet,
    row: int,
    grade_cols: list[int],
    total_col: int | None,
    counts: dict[str, int],
    grades: list[str],
) -> None:
    s = 0
    for g, col in zip(grades, grade_cols, strict=True):
        v = int(counts.get(g, 0))
        write_cell_value(ws, row, col, v)
        s += v
    if total_col is not None:
        write_cell_value(ws, row, total_col, s)


def _write_bh_presents_par_grade(
    ws: Worksheet,
    grades: list[str],
    matrix: dict[str, dict[str, int]],
) -> None:
    """
    Remplace les formules erronées (=AE10…) par les effectifs « Présents » par grade (=B9…),
    conformément au titre du bloc BH (feuille NUMERIQUE).
    """
    counts = matrix["presents"]
    for i, g in enumerate(grades):
        if i >= 18:
            break
        write_cell_value(ws, _BH_PRESENTS_ROW, _BH_GRADE_COL_START + i, int(counts.get(g, 0)))


def _find_total_col(ws: Worksheet, grade_row: int = NUM_GRADE_ROW) -> int | None:
    c = 2
    while c <= ws.max_column:
        v = ws.cell(grade_row, c).value
        if isinstance(v, str) and v.strip().upper() == "TOTAL":
            return c
        c += 1
    return None


def fill_numerique_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    month_title: str | None = None,
    mois_actif: str | None = None,
    recap_cd_snapshot: list[int | None] | None = None,
    grade_abbrev_map: dict[str, str] | None = None,
) -> None:
    title = month_title or month_title_from_mois_actif(mois_actif) or _month_title()
    _write_month_titles(ws, title)

    grades, grade_cols = _parse_grade_columns(ws)
    if not grades:
        raise ValueError("Gabarit NUMERIQUE : aucune colonne grade (ligne 7).")

    total_col = _find_total_col(ws)
    matrix = build_numerique_matrix(rows, grades, grade_abbrev_map=grade_abbrev_map)

    r = NUM_FIRST_DATA_START
    for key in RUBRIQUE_KEYS_ROW8_19:
        counts = matrix[key]
        _write_matrix_row(ws, r, grade_cols, total_col, counts, grades)
        r += 1
        if r > NUM_FIRST_DATA_END:
            break

    keys_second = RUBRIQUE_KEYS_ROW8_19[2:]
    r2 = NUM_SECOND_DATA_START
    for key in keys_second:
        counts = matrix[key]
        _write_matrix_row(ws, r2, grade_cols, total_col, counts, grades)
        r2 += 1

    if total_col:
        srow = NUM_SECOND_TOTAL_ROW
        for col in grade_cols:
            t = sum(
                int(ws.cell(row=rr, column=col).value or 0)
                for rr in range(NUM_SECOND_DATA_START, NUM_SECOND_DATA_END + 1)
            )
            write_cell_value(ws, srow, col, t)
        write_cell_value(
            ws,
            srow,
            total_col,
            sum(int(ws.cell(row=srow, column=c).value or 0) for c in grade_cols),
        )

    _write_bh_presents_par_grade(ws, grades, matrix)

    snap = recap_cd_snapshot if recap_cd_snapshot is not None else [None] * 10
    _materialize_recap_cd_baseline(ws, snap)
    _write_recap_variation_ce_cf(ws, matrix, grades, snap)
    _write_recap_row4_headers(ws, mois_actif)


def fill_indispo_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    month_label: str | None = None,
) -> None:
    if month_label:
        write_cell_value(ws, 2, INDISPO_MONTH_TITLE_COL, month_label)
    _labels, row_codes = collect_indispo_structure_meta(ws)

    mat = build_indispo_matrix(rows, row_structure_codes=row_codes)

    for idx, line in enumerate(mat):
        r_out = INDISPO_DATA_START + idx
        for j, val in enumerate(line):
            write_cell_value(ws, r_out, INDISPO_CAT_COL_START + j, int(val),
            )
        write_cell_value(ws, r_out, INDISPO_TOTAL_COL, int(sum(line)))

    for c in range(INDISPO_CAT_COL_START, INDISPO_TOTAL_COL + 1):
        t = sum(
            int(ws.cell(row=r, column=c).value or 0)
            for r in range(INDISPO_DATA_START, INDISPO_DATA_END + 1)
        )
        write_cell_value(ws, INDISPO_TOTAL_ROW, c, t)


def _capture_numerique_workbook_data(settings, mois, grade_order, movement_settings):
    """All report inputs share one fresh MySQL snapshot, never the UI cache."""
    from datetime import timezone
    from sme_gend.repositories.mysql_connection import mysql_read_snapshot
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    from sme_gend.repositories.grade_repository import GradeRepository
    from sme_gend.services.detaches_service import in_detaches_scope, _sort_key_detaches
    from sme_gend.services.numerique_recap_baseline import resolve_recap_cd_baseline
    from sme_gend.services.clean_sme_exports import _movements
    from sme_gend.services.detaches_config_service import DetachesConfigService
    from sme_gend.services.export_workbook_tables import export_details

    with mysql_read_snapshot(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), @@hostname, UTC_TIMESTAMP(6)")
            database, server, captured = cursor.fetchone()
            cursor.execute("SELECT table_name FROM information_schema.tables "
                           "WHERE table_schema=DATABASE() AND table_type='BASE TABLE' "
                           "AND engine <> 'InnoDB'")
            non_transactional = [str(item[0]) for item in cursor.fetchall()]
            if non_transactional:
                raise ValueError("Lecture SME cohérente impossible : tables non transactionnelles : "
                                 + ", ".join(non_transactional))
            cursor.execute("SELECT l.id, p.annee_mois, l.chemin_fichier, l.checksum, l.importe_le "
                           "FROM import_lot l LEFT JOIN sme_periode p ON p.id=l.sme_periode_id "
                           "WHERE p.annee_mois IN (%s,%s) ORDER BY l.id", (mois, _prev_ym_canon(mois)))
            imports = [{"id": item[0], "mois": item[1], "source_path": item[2],
                        "source_checksum": item[3],
                        "imported_at": item[4].isoformat() if hasattr(item[4], "isoformat") else item[4]}
                       for item in cursor.fetchall()]
        rows = ListeGeneraleRepository(settings).fetch_all_at_mois(mois)
        grades = GradeRepository(settings).fetch_all()
        previous = resolve_recap_cd_baseline(settings, mois, grade_order,
                                             grade_abbrev_map=build_grade_abbrev_map(grades))
        dataset = {
            "mois": mois, "rows": rows, "grades": grades,
            "details": export_details(NumeriqueSatelliteRepository(settings)),
            "detaches": sorted((row for row in rows if in_detaches_scope(row)), key=_sort_key_detaches),
            "previous_counts": previous, "movements": _movements(movement_settings, mois, rows),
            "detaches_config": DetachesConfigService(settings).load().to_dict(),
        }
    source = {
        "kind": "mysql_monthly_snapshot", "database": database, "server": server,
        "captured_at_utc": captured.replace(tzinfo=timezone.utc).isoformat(),
        "imports": imports,
        "history_policy": "Imported monthly observations retained; no dates inferred between archives.",
    }
    return dataset, source


def export_numerique_workbook(
    settings: Settings,
    template_path: Path,
    destination: Path,
    *,
    month_title: str | None = None,
    mois_actif: str | None = None,
    export_mois: str | None = None,
    gain_perte_settings: Settings | None = None,
) -> ExportRevision:
    """Export the faithful model and retain a new verifiable revision.

    This does not validate/close the month or change any imported Access archive.
    """
    from dataclasses import replace
    from io import BytesIO
    from sme_gend.config.liste_generale_scope import resolve_export_mois, settings_for_chosen_export_mois
    from sme_gend.repositories.mysql_connection import mysql_database_key
    from sme_gend.models.detaches_file_config import DetachesFileConfig
    from sme_gend.services.numerique_model_export import build_from_model
    from sme_gend.services.doc_gen_log import log_doc_generation
    from sme_gend.services.auth_guard import session_can_export_documents
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.sme_export_revision import (
        revision_root, validate_export_destination, save_export_revision, retrieve_export_revision,
    )

    if not session_can_export_documents():
        raise PermissionError("Le droit d'exporter les effectifs est nécessaire.")
    tpl = Path(template_path)
    if not tpl.is_file():
        raise FileNotFoundError(f"Gabarit situation numérique introuvable :\n{tpl}")
    chosen = export_mois or mois_actif or resolve_export_mois(settings)
    eff_mois = canonical_sme_mois_yyyy_mm(chosen or "")
    if not eff_mois:
        raise ValueError("Choisissez le mois SME avant l'export.")
    data_settings = settings_for_chosen_export_mois(settings, eff_mois)
    movement_settings = replace(gain_perte_settings, mysql_view_mois=eff_mois) if gain_perte_settings else data_settings
    if mysql_database_key(movement_settings) != mysql_database_key(data_settings):
        raise ValueError("Le classeur SME doit lire les effectifs et les mouvements dans la même base.")
    root = revision_root(data_settings)
    dest = validate_export_destination(destination, template=tpl, root=root)
    # Read once. Editing the source template during capture cannot change the
    # model used to generate a revision or its recorded hash.
    template_bytes = tpl.read_bytes()
    model = load_workbook(BytesIO(template_bytes), read_only=True, data_only=True)
    try:
        grade_order = _parse_grade_columns(model["NUMERIQUE"])[0] if "NUMERIQUE" in model.sheetnames else []
    finally:
        model.close()
    dataset, source = _capture_numerique_workbook_data(data_settings, eff_mois, grade_order, movement_settings)
    actor = resolve_audit_actor()

    def render(model_path, workbook_path):
        values = dict(dataset)
        values["detaches_config"] = DetachesFileConfig.from_dict(values["detaches_config"])
        return build_from_model(model_path, workbook_path, **values)

    revision = save_export_revision(
        root=root, mois=eff_mois, template_bytes=template_bytes, dataset=dataset,
        source=source, actor=actor, renderer=render,
    )
    try:
        retrieve_export_revision(revision.folder, dest, expected=revision.journal_metadata())
    except Exception as exc:
        raise RuntimeError("La version SME a été conservée, mais la copie vers le fichier choisi a échoué. "
                           f"Version récupérable : {revision.workbook}") from exc
    try:
        log_doc_generation(settings=data_settings, doc_type="numerique", format="xlsx",
                           count=len(dataset["rows"]), dest_path=str(dest), mois=eff_mois,
                           meta=revision.journal_metadata())
    except Exception as exc:
        # Both durable outputs exist. A missing receipt must never be presented
        # as a failed generation encouraging the operator to create duplicates.
        from sme_gend.repositories.mysql_connection import CommittedMirrorError
        import logging
        logging.getLogger(__name__).warning("Export SME conservé ; journal à vérifier.", exc_info=True)
        warning = ("Historique confirmé dans MySQL ; sa copie locale reste à vérifier."
                   if isinstance(exc, CommittedMirrorError) else
                   "L'enregistrement dans l'historique central n'a pas pu être confirmé.")
        revision = replace(revision, journal_warning=warning + " Le fichier et sa version sont conservés ; "
                           "aucune nouvelle génération n'est nécessaire.")
    return revision


def workbook_sheet_names(path: Path) -> list[str]:
    wb = load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def sorted_rows_for_padmin(
    rows: list[ListeGeneraleRow],
    padmin_code: str,
    *,
    grades: list | None = None,
    settings: Settings | None = None,
) -> list[ListeGeneraleRow]:
    """Filtrage Padmin (insensible à la casse) + tri — aligné sur la liste générale."""
    return _sort_liste_rows(
        [r for r in rows if _padmin_matches(r, padmin_code)],
        grades=grades,
        settings=settings,
    )


def read_satellite_grade_columns_from_template(
    template_path: Path | None,
    sheet_name: str,
) -> list[tuple[str, int]]:
    """Codes grade (ligne 1 du gabarit) pour MALADE / DESERTEUR / STAGE ; [] si absent."""
    if not template_path or not Path(template_path).is_file():
        return []
    p = Path(template_path)
    wb = load_workbook(p, read_only=True, data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            return []
        return _parse_satellite_grade_columns(wb[sheet_name])
    finally:
        wb.close()


def satellite_grade_count_pairs(
    grade_cols: list[tuple[str, int]],
    filtered: list[ListeGeneraleRow],
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> list[tuple[str, int]]:
    """Effectifs par grade dans l’ordre des colonnes du gabarit (aperçu HTML)."""
    if not grade_cols:
        return []
    counts: dict[str, int] = {gb: 0 for gb, _ in grade_cols}
    for row in filtered:
        g = grade_to_abbrev(row.grade, grade_abbrev_map or {})
        if not g:
            continue
        gf = g.casefold()
        for gbanner, _ in grade_cols:
            if gbanner.casefold() == gf:
                counts[gbanner] = counts.get(gbanner, 0) + 1
                break
    return [(gb, counts.get(gb, 0)) for gb, _ in grade_cols]
