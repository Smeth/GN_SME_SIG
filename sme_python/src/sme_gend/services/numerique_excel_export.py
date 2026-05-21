"""Export classeur situation numérique : gabarit Excel rempli depuis Access (liste + matrices)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow, SubdivisionRepository
from sme_gend.repositories.numerique_satellite_repository import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    NumeriqueSatelliteRepository,
    StageDetail,
)
from sme_gend.services.numerique_padmin import (
    PADMIN_DESERTEUR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)
from sme_gend.config.sme_context import load_sme_context
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
SATELLITE_DATA_START = 4

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

SHEET_GAINS_PERTES = "Gains et pertes"


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
    Réécrit la colonne CD (lignes 5–14) en **valeurs entières** issues du gabarit (snapshot).

    Sans cela, le classeur ouvert avec ``data_only=False`` peut garder des **formules** en CD
    dont le recalcul Excel ne coïncide pas avec les valeurs lues en ``data_only=True`` pour
    l’aperçu / le calcul de CF — d’où une ambiguïté « mois figé ». Le fichier exporté reflète
    exactement la base utilisée pour CE − CD.
    """
    vals = cd_vals if len(cd_vals) >= 10 else [*cd_vals, *([None] * (10 - len(cd_vals)))]
    for i in range(9):
        row = _RECAP_ROW_FIRST + i
        v = vals[i]
        ws.cell(row=row, column=_RECAP_COL_CD, value=(int(v) if v is not None else None))
    vtot = vals[9]
    ws.cell(
        row=_RECAP_ROW_TOTAL,
        column=_RECAP_COL_CD,
        value=(int(vtot) if vtot is not None else None),
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
        ws.cell(row=row, column=_RECAP_COL_CE, value=int(cur))
        prev = vals[idx]
        if prev is not None:
            ws.cell(row=row, column=_RECAP_COL_CF, value=int(cur) - int(prev))
        else:
            ws.cell(row=row, column=_RECAP_COL_CF, value=None)

    row_tot = _RECAP_ROW_TOTAL
    cur_sum = sum(matrix_row_total(matrix, key, grades) for key, _ in RECAP_INDISPO_KEYS)
    ws.cell(row=row_tot, column=_RECAP_COL_CE, value=int(cur_sum))
    prev_sum = vals[9]
    if prev_sum is not None:
        ws.cell(row=row_tot, column=_RECAP_COL_CF, value=int(cur_sum) - int(prev_sum))
    else:
        ws.cell(row=row_tot, column=_RECAP_COL_CF, value=None)


def _write_recap_row4_headers(ws: Worksheet, mois_actif: str | None) -> None:
    """Libellés dynamiques CD4 / CE4 / CF4 (ligne 4) lorsque le mois SME est connu — ne modifie pas CD5–CD14."""
    if not mois_actif:
        return
    hdr = recap_column_headers_from_mois_actif(mois_actif)
    if hdr is None:
        return
    mois_fige, mois_courant = hdr
    ws.cell(row=4, column=_RECAP_COL_CD, value=mois_fige)
    ws.cell(row=4, column=_RECAP_COL_CE, value=mois_courant)
    cur_cf = ws.cell(row=4, column=_RECAP_COL_CF).value
    if cur_cf is None or (isinstance(cur_cf, str) and not str(cur_cf).strip()):
        ws.cell(row=4, column=_RECAP_COL_CF, value="Variation")


def fill_gains_pertes_sheet(
    wb,
    settings: Settings,
    mois_actif: str | None,
    *,
    gain_perte_settings: Settings | None = None,
) -> None:
    """Feuille synthèse TabGainPerte (même mois que le contexte SME), alignée sur l’aperçu HTML."""
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository

    gp_settings = gain_perte_settings or settings
    name = SHEET_GAINS_PERTES
    if name in wb.sheetnames:
        idx = wb.sheetnames.index(name)
        wb.remove(wb[name])
        ws = wb.create_sheet(name, idx)
    else:
        ws = wb.create_sheet(name)

    ma = (mois_actif or "").strip()
    c = canonical_sme_mois_yyyy_mm(ma) if ma else None
    title = (
        f"TabGainPerte — mois SME {c}" if c else "TabGainPerte — définir le mois actif pour filtrer"
    )
    ws.cell(row=1, column=1, value=title)

    headers = ("ID", "Grade", "Nom", "Objet", "Observation", "Cdsme")
    for c_i, h in enumerate(headers, start=1):
        ws.cell(row=2, column=c_i, value=h)

    rows_gp: list = []
    if ma:
        try:
            rows_gp = GainPerteRepository(gp_settings).fetch_for_month(ma)
        except Exception:
            rows_gp = []

    for i, r in enumerate(rows_gp, start=3):
        ws.cell(row=i, column=1, value=str(r.id or ""))
        ws.cell(row=i, column=2, value=r.grade or "")
        ws.cell(row=i, column=3, value=r.nom or "")
        ws.cell(row=i, column=4, value=r.objet_display)
        ws.cell(row=i, column=5, value=r.observ_display)
        ws.cell(row=i, column=6, value=r.cdsme or "")


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
    ws["A3"] = title
    v33 = ws.cell(33, 1).value
    if v33 is None or (isinstance(v33, str) and not str(v33).strip()):
        ws["A33"] = title


def _fill_liste_generale(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    *,
    subdivision_flat: Sequence[SubdivisionFlatRow] | None = None,
) -> None:
    """
    Remplit la feuille « LISTE GENERALE » à partir de ``ListeGenerale`` (ordre identique au SELECT Access).

    Colonne A : numéro d’ordre 1…n (ligne de liste), pas l’Id Access — aligné sur une colonne « N° » maquette.
    Colonnes B–Q : noms, dates/lieux, grade, Padmin, structure, etc., dans le même ordre que le repository.
    """
    max_old = ws.max_row
    n = len(rows)
    for i, row in enumerate(rows):
        r = LISTE_DATA_START + i
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.noms)
        ws.cell(row=r, column=3, value=row.date_naissance)
        ws.cell(row=r, column=4, value=row.lieu_naissance)
        ws.cell(row=r, column=5, value=row.grade)
        ws.cell(row=r, column=6, value=row.date_grade)
        ws.cell(row=r, column=7, value=row.date_service)
        ws.cell(row=r, column=8, value=row.ndecision)
        ws.cell(row=r, column=9, value=row.matricule_militaire)
        ws.cell(row=r, column=10, value=row.matricule_solde)
        ws.cell(row=r, column=11, value=row.position_admin)
        ws.cell(row=r, column=12, value=row.fonction)
        sub_cell = (
            format_subdivision_arme_display(row.subdivision_arme, subdivision_flat)
            if subdivision_flat is not None
            else row.subdivision_arme
        )
        ws.cell(row=r, column=13, value=sub_cell)
        ws.cell(row=r, column=14, value=row.diplome)
        ws.cell(row=r, column=15, value=row.date_obtention)
        ws.cell(row=r, column=16, value=row.structure)
        ws.cell(row=r, column=17, value=row.obs)
    start_clear = LISTE_DATA_START + n
    for r in range(start_clear, max_old + 1):
        for c in range(1, 18):
            ws.cell(r, c).value = None


def _padmin_matches(row: ListeGeneraleRow, code: str) -> bool:
    """Comme le tableau de bord : égalité insensible à la casse."""
    a = (row.position_admin or "").strip()
    if not a:
        return False
    return a == code or a.casefold() == code.casefold()


def _sort_liste_rows(rows: list[ListeGeneraleRow]) -> list[ListeGeneraleRow]:
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
) -> None:
    if not grade_cols:
        return
    counts: dict[int, int] = {col: 0 for _, col in grade_cols}
    for row in filtered:
        g = (row.grade or "").strip()
        if not g:
            continue
        gf = g.casefold()
        for gbanner, col in grade_cols:
            if gbanner.casefold() == gf:
                counts[col] = counts.get(col, 0) + 1
                break
    for _, col in grade_cols:
        ws.cell(row=2, column=col, value=int(counts.get(col, 0)))


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
            ws.cell(r, c).value = None


def fill_mission_sheet(
    ws: Worksheet,
    rows: list[ListeGeneraleRow],
    missions: dict[str, MissionDetail],
) -> None:
    filt = _sort_liste_rows([r for r in rows if _padmin_matches(r, PADMIN_MISSION)])
    max_row_before = ws.max_row
    data_cols = 8
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = missions.get(row.satellite_join_key())
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.grade)
        ws.cell(row=r, column=3, value=row.noms)
        ws.cell(row=r, column=4, value=row.structure)
        if d is not None:
            ws.cell(row=r, column=5, value=d.nature)
            ws.cell(row=r, column=6, value=d.lieu_mission)
            ws.cell(row=r, column=7, value=d.refnds)
            ws.cell(row=r, column=8, value=d.date_depart)
        else:
            for c in range(5, data_cols + 1):
                ws.cell(row=r, column=c, value=None)
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
) -> None:
    max_row_before = ws.max_row
    filt = _sort_liste_rows([r for r in rows if _padmin_matches(r, PADMIN_MALADE)])
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = malades.get(row.satellite_join_key())
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.grade)
        ws.cell(row=r, column=3, value=row.noms)
        ws.cell(row=r, column=4, value=row.structure)
        if d is not None:
            ws.cell(row=r, column=5, value=d.date_constat)
            ws.cell(row=r, column=6, value=d.pathologie)
            ws.cell(row=r, column=7, value=d.obs)
        else:
            for c in range(5, 8):
                ws.cell(row=r, column=c, value=None)
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
) -> None:
    max_row_before = ws.max_row
    filt = _sort_liste_rows([r for r in rows if _padmin_matches(r, PADMIN_DESERTEUR)])
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols)
    data_cols = 5
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = deserteurs.get(row.satellite_join_key())
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.grade)
        ws.cell(row=r, column=3, value=row.noms)
        ws.cell(row=r, column=4, value=row.structure)
        if d is not None:
            ws.cell(row=r, column=5, value=d.dat_desertion)
        else:
            ws.cell(row=r, column=5, value=None)
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
) -> None:
    max_row_before = ws.max_row
    filt = _sort_liste_rows([r for r in rows if _padmin_matches(r, PADMIN_STAGE_LOCAL)])
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = stages.get(row.satellite_join_key())
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.grade)
        ws.cell(row=r, column=3, value=row.noms)
        ws.cell(row=r, column=4, value=row.structure)
        if d is not None:
            ws.cell(row=r, column=5, value=d.nature_stage)
            ws.cell(row=r, column=6, value=d.lieu_stage)
            ws.cell(row=r, column=7, value=d.date_debut_fin)
        else:
            for c in range(5, 8):
                ws.cell(row=r, column=c, value=None)
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
) -> None:
    max_row_before = ws.max_row
    filt = _sort_liste_rows([r for r in rows if _padmin_matches(r, PADMIN_STAGE_EX)])
    grade_cols = _parse_satellite_grade_columns(ws)
    _apply_satellite_grade_counts(ws, filt, grade_cols)
    data_cols = 7
    max_gc = max((c for _, c in grade_cols), default=data_cols)
    for i, row in enumerate(filt):
        r = SATELLITE_DATA_START + i
        d = stages.get(row.satellite_join_key())
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=row.grade)
        ws.cell(row=r, column=3, value=row.noms)
        ws.cell(row=r, column=4, value=row.structure)
        if d is not None:
            ws.cell(row=r, column=5, value=d.nature_stage)
            ws.cell(row=r, column=6, value=d.lieu_stage)
            ws.cell(row=r, column=7, value=d.date_debut_fin)
        else:
            for c in range(5, 8):
                ws.cell(row=r, column=c, value=None)
    _clear_satellite_block(
        ws,
        data_start=SATELLITE_DATA_START,
        n_filled=len(filt),
        clear_cols=max(data_cols, max_gc),
        max_row_before=max_row_before,
    )


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
        ws.cell(row=row, column=col, value=v)
        s += v
    if total_col is not None:
        ws.cell(row=row, column=total_col, value=s)


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
        ws.cell(
            row=_BH_PRESENTS_ROW,
            column=_BH_GRADE_COL_START + i,
            value=int(counts.get(g, 0)),
        )


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
) -> None:
    title = month_title or month_title_from_mois_actif(mois_actif) or _month_title()
    _write_month_titles(ws, title)

    grades, grade_cols = _parse_grade_columns(ws)
    if not grades:
        raise ValueError("Gabarit NUMERIQUE : aucune colonne grade (ligne 7).")

    total_col = _find_total_col(ws)
    matrix = build_numerique_matrix(rows, grades)

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
            ws.cell(row=srow, column=col, value=t)
        ws.cell(
            row=srow,
            column=total_col,
            value=sum(int(ws.cell(row=srow, column=c).value or 0) for c in grade_cols),
        )

    _write_bh_presents_par_grade(ws, grades, matrix)

    snap = recap_cd_snapshot if recap_cd_snapshot is not None else [None] * 10
    _materialize_recap_cd_baseline(ws, snap)
    _write_recap_variation_ce_cf(ws, matrix, grades, snap)
    _write_recap_row4_headers(ws, mois_actif)


def fill_indispo_sheet(ws: Worksheet, rows: list[ListeGeneraleRow]) -> None:
    _labels, row_codes = collect_indispo_structure_meta(ws)

    mat = build_indispo_matrix(rows, row_structure_codes=row_codes)

    for idx, line in enumerate(mat):
        r_out = INDISPO_DATA_START + idx
        for j, val in enumerate(line):
            ws.cell(
                row=r_out,
                column=INDISPO_CAT_COL_START + j,
                value=int(val),
            )
        ws.cell(row=r_out, column=INDISPO_TOTAL_COL, value=int(sum(line)))

    for c in range(INDISPO_CAT_COL_START, INDISPO_TOTAL_COL + 1):
        t = sum(
            int(ws.cell(row=r, column=c).value or 0)
            for r in range(INDISPO_DATA_START, INDISPO_DATA_END + 1)
        )
        ws.cell(row=INDISPO_TOTAL_ROW, column=c, value=t)


def export_numerique_workbook(
    settings: Settings,
    template_path: Path,
    destination: Path,
    *,
    month_title: str | None = None,
    mois_actif: str | None = None,
    gain_perte_settings: Settings | None = None,
) -> None:
    """
    Charge le gabarit, remplit LISTE GENERALE, NUMERIQUE, INDISPO et les feuilles
    satellites (MISSION, MALADE, …) depuis Access. Les formules Excel sont remplacées
    par les valeurs calculées sur les zones remplies.

    Le titre de période (A3/A33) et le récap variation (CE/CF) suivent ``mois_actif``
    du contexte SME si ``mois_actif`` / ``month_title`` ne sont pas fournis explicitement.
    La colonne CD (lignes 5–14) est réécrite avec les valeurs « mois figé » lues sur le gabarit
    (même base que le calcul de variation), pour éviter tout décalage avec des formules Excel.
    """
    tpl = Path(template_path)
    if not tpl.is_file():
        raise FileNotFoundError(f"Gabarit situation numérique introuvable :\n{tpl}")

    ctx = load_sme_context()
    eff_mois = (mois_actif if mois_actif is not None else ctx.mois_actif or "").strip() or None
    eff_title = month_title
    if eff_title is None and eff_mois:
        eff_title = month_title_from_mois_actif(eff_mois)
    if eff_title is None:
        eff_title = _month_title()

    recap_cd_snapshot = read_recap_cd_values_from_template_path(tpl)

    repo = ListeGeneraleRepository(settings)
    rows = repo.fetch_all()

    sat_repo = NumeriqueSatelliteRepository(settings)
    missions = sat_repo.load_missions()
    malades = sat_repo.load_malades()
    deserteurs = sat_repo.load_deserteurs()
    stages_loc = sat_repo.load_stages_loc()
    stages_ex = sat_repo.load_stages_ex()
    sub_flat = SubdivisionRepository(settings).fetch_flat_hierarchy()

    wb = load_workbook(tpl, data_only=False, keep_vba=False)
    try:
        if "LISTE GENERALE" in wb.sheetnames:
            _fill_liste_generale(wb["LISTE GENERALE"], rows, subdivision_flat=sub_flat)
        if "NUMERIQUE" in wb.sheetnames:
            fill_numerique_sheet(
                wb["NUMERIQUE"],
                rows,
                month_title=eff_title,
                mois_actif=eff_mois,
                recap_cd_snapshot=recap_cd_snapshot,
            )
        if INDISPO_SHEET in wb.sheetnames:
            fill_indispo_sheet(wb[INDISPO_SHEET], rows)

        if SHEET_MISSION in wb.sheetnames:
            fill_mission_sheet(wb[SHEET_MISSION], rows, missions)
        if SHEET_MALADE in wb.sheetnames:
            fill_malade_sheet(wb[SHEET_MALADE], rows, malades)
        if SHEET_DESERTEUR in wb.sheetnames:
            fill_deserteur_sheet(wb[SHEET_DESERTEUR], rows, deserteurs)
        if SHEET_STAGE_LOC in wb.sheetnames:
            fill_stage_loc_sheet(wb[SHEET_STAGE_LOC], rows, stages_loc)
        if SHEET_STAGE_EXT in wb.sheetnames:
            fill_stage_ext_sheet(wb[SHEET_STAGE_EXT], rows, stages_ex)

        fill_gains_pertes_sheet(
            wb, settings, eff_mois, gain_perte_settings=gain_perte_settings
        )

        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        wb.save(dest)
    finally:
        wb.close()


def workbook_sheet_names(path: Path) -> list[str]:
    wb = load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def sorted_rows_for_padmin(rows: list[ListeGeneraleRow], padmin_code: str) -> list[ListeGeneraleRow]:
    """Filtrage Padmin (insensible à la casse) + tri — aligné sur l’export Excel."""
    return _sort_liste_rows([r for r in rows if _padmin_matches(r, padmin_code)])


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
) -> list[tuple[str, int]]:
    """Effectifs par grade dans l’ordre des colonnes du gabarit (aperçu HTML)."""
    if not grade_cols:
        return []
    counts: dict[str, int] = {gb: 0 for gb, _ in grade_cols}
    for row in filtered:
        g = (row.grade or "").strip()
        if not g:
            continue
        gf = g.casefold()
        for gbanner, _ in grade_cols:
            if gbanner.casefold() == gf:
                counts[gbanner] = counts.get(gbanner, 0) + 1
                break
    return [(gb, counts.get(gb, 0)) for gb, _ in grade_cols]
