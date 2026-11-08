"""Service — génération du document 'Soit transmis' pour les limites d'âge."""

from __future__ import annotations

from sme_gend.services.export_progress import tracked_export, export_phase, export_rows

import copy
import json
from datetime import date
from pathlib import Path
from sme_gend.services.safe_paths import atomic_destination
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.models.liste_generale import ListeGeneraleRow

from sme_gend.config.settings import Settings
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import _grade_key, _int_to_fr


def soit_transmis_config_path() -> Path:
    from sme_gend.config.settings import project_root
    return project_root() / "data" / "soit_transmis_config.json"


# ---------------------------------------------------------------------------
# Ordre hiérarchique des grades (du plus élevé au plus bas)
# Accepte indifféremment les codes courts et les libellés complets normalisés.
# ---------------------------------------------------------------------------

_GRADE_ORDER: dict[str, int] = {
    # Officiers généraux
    "GENERAL DE DIVISION":     1,  "GDV": 1,
    "GENERAL DE BRIGADE":      2,  "GDB": 2,
    # Officiers supérieurs — Colonel-Major est au-dessus de Colonel
    "COLONEL-MAJOR":           3,  "CLM": 3,
    "COLONEL":                 4,  "COL": 4,
    "LIEUTENANT-COLONEL":      5,  "LTC": 5,
    # Officiers subalternes
    "COMMANDANT":              6,  "CDT": 6,
    "CAPITAINE":               7,  "CNE": 7,
    "LIEUTENANT":              8,  "LTN": 8,
    "SOUS-LIEUTENANT":         9,  "SLT": 9,
    "ASPIRANT":               10,  "ASP": 10,
    # Sous-officiers supérieurs
    "MAJOR":                  11,  "MAJ": 11,
    "ADJUDANT-CHEF":          12,  "ADC": 12,
    "ADJUDANT":               13,  "ADJ": 13,
    # Sous-officiers subalternes
    "MARECHAL DES LOGIS CHEF": 14,  "MARECHAL DES LOGIS-CHEF": 14,  "MDC": 14,
    "SERGENT-CHEF":            14,
    "MARECHAL DES LOGIS":      15,  "MDL": 15,
    "SERGENT":                 15,
    "ADJUDANT-MAJOR":          11,
    # Militaires du rang
    "BRIGADIER-CHEF":         16,  "BDC": 16,
    "BRIGADIER":              17,  "BRI": 17,
    "GENDARME":               18,  "GND": 18,
    "GENDARME ADJOINT":       19,  "GAD": 19,
}


def _grade_rank(grade_str: str) -> int:
    """Retourne le rang hiérarchique d'un grade (petit = plus élevé). 99 si inconnu."""
    key = _grade_key(grade_str)
    if key in _GRADE_ORDER:
        return _GRADE_ORDER[key]
    # Correspondance partielle : privilégier le motif le plus long (ex. MDC avant MDL)
    best_len = -1
    best_rank = 99
    for pat, rank in _GRADE_ORDER.items():
        if pat in key or key in pat:
            if len(pat) > best_len:
                best_len = len(pat)
                best_rank = rank
    return best_rank


def _person_row_key(person) -> tuple:
    """Clé unique de ``person`` — ``id`` (clé primaire), pas matricules/nom qui
    peuvent être vides ou partagés (homonymes). Une collision ici faisait
    disparaître une personne du lot (dict ``by_key`` écrasé) dans
    ``sort_limite_age_rows``, dupliquant l'autre à la place dans les documents
    générés (impression groupée, export ZIP, Soit transmis)."""
    return (person.id,)


def _resolve_grade_code(
    grade_str: str | None,
    grade_code_map: dict[str, str] | None,
) -> str:
    """Retourne le code court TabGrade (MDC, MDL…) pour le tri, depuis libellé ou code."""
    g = (grade_str or "").strip()
    if not g:
        return ""
    if grade_code_map and g in grade_code_map:
        return grade_code_map[g]
    key = _grade_key(g)
    if key in _GRADE_ORDER:
        return g
    return g


def sort_persons_by_grade(
    persons: list,
    *,
    grade_code_map: dict[str, str] | None = None,
    grades: list | None = None,
) -> list:
    """
    Trie par hiérarchie de grade (TabGrade PrefOrd si disponible, sinon table interne),
    puis par nom.
    """
    from sme_gend.services.personnel_list_order import (
        _grade_pref_maps,
        _grade_sort_key,
        _norm,
    )

    by_code, by_lib = _grade_pref_maps(grades) if grades else ({}, {})

    def _sort_key(p) -> tuple:
        raw = (p.grade or "").strip()
        code = _resolve_grade_code(raw, grade_code_map)
        if grades and (by_code or by_lib):
            po, gl = _grade_sort_key(code, by_code, by_lib)
            if po >= 999_999:
                po, gl = _grade_sort_key(raw, by_code, by_lib)
        else:
            po = _grade_rank(code or raw)
            gl = _grade_key(raw)
        return (po, gl, _norm(p.noms), p.matricule_militaire or "")

    return sorted(persons, key=_sort_key)


def sort_limite_age_rows(
    rows: list[tuple],
    *,
    grade_code_map: dict[str, str] | None = None,
    grades: list | None = None,
) -> list[tuple]:
    """Trie des couples (ListeGeneraleRow, LimiteDateResult) comme sort_persons_by_grade."""
    if not rows:
        return rows
    by_key = {_person_row_key(p): (p, r) for p, r in rows}
    ordered = sort_persons_by_grade(
        [p for p, _ in rows],
        grade_code_map=grade_code_map,
        grades=grades,
    )
    return [by_key[_person_row_key(p)] for p in ordered]


# ---------------------------------------------------------------------------
# Helpers de remplissage
# ---------------------------------------------------------------------------

def _first_run_set(para, text: str) -> None:
    """Écrase le premier run d'un paragraphe et vide les suivants."""
    runs = para.runs
    if not runs:
        para.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r.text = ""


def _find_para_containing(cell, substring: str):
    """Retourne le premier paragraphe d'une cellule contenant substring."""
    for p in cell.paragraphs:
        if substring in p.text:
            return p
    return None


def _fill_p19(p19, count: int) -> None:
    """
    Remplace la partie variable (compte en lettres + chiffres) de P19.
    Runs 0-2 sont gardés intacts (intro, 'pour attributions' gras, guillemet fermant).
    """
    count_words = _int_to_fr(count)
    runs = p19.runs
    # Vider runs 3 et suivants
    for i in range(3, len(runs)):
        runs[i].text = ""
    # Reconstruire à partir de run3
    tail = (
        f"{count_words} ({count}) dossiers de limite d’âge "
        "et de service de personnels évoluant dans différentes "
        "structures de la gendarmerie nationale."
    )
    if len(runs) > 3:
        runs[3].text = tail
    else:
        p19.add_run(tail)


def _fill_persons_table(
    table,
    persons: list[ListeGeneraleRow],
    grade_code_map: dict[str, str] | None = None,
    grades: list | None = None,
) -> None:
    """
    Vide les lignes de données (index ≥ 1) de la table et les remplace
    par les personnes fournies.
    grade_code_map : {libelle_complet → code_court} pour l'affichage dans la colonne GRADE.
    grades : référentiel TabGrade (repli si un libellé manque dans la map).
    """
    from docx.oxml.ns import qn

    from sme_gend.services.grade_label_format import format_grade_display

    tbl = table._tbl
    tr_elems = tbl.findall(qn("w:tr"))
    template_tr = copy.deepcopy(tr_elems[1]) if len(tr_elems) > 1 else None

    for tr in tr_elems[1:]:
        tbl.remove(tr)

    gmap = grade_code_map or {}

    for idx, person in enumerate(export_rows(persons, label='Personnels du soit-transmis')):
        if template_tr is None:
            continue
        new_tr = copy.deepcopy(template_tr)
        tbl.append(new_tr)
        row = table.rows[idx + 1]

        def _set(ci: int, text: str) -> None:
            cell = row.cells[ci]
            p = cell.paragraphs[0]
            if p.runs:
                p.runs[0].text = text
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.add_run(text)

        grade_full = (person.grade or "").strip()
        grade_disp = gmap.get(grade_full) or format_grade_display(grade_full, grades)

        _set(0, str(idx + 1))
        _set(1, grade_disp)
        _set(2, (person.noms or "").strip())


# ---------------------------------------------------------------------------
# Service principal
# ---------------------------------------------------------------------------

class SoitTransmisService:

    # ── Config I/O ────────────────────────────────────────────────────────

    def load(self, settings: Settings | None = None) -> SoitTransmisConfig:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_SOIT_TRANSMIS, load_config_model

        st = settings or get_settings()

        def _factory(d: dict) -> SoitTransmisConfig:
            return SoitTransmisConfig.from_dict(d) if d else SoitTransmisConfig()

        return load_config_model(st, KEY_SOIT_TRANSMIS, default_factory=_factory)

    def save(self, cfg: SoitTransmisConfig, settings: Settings | None = None) -> None:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_SOIT_TRANSMIS, save_config_model

        save_config_model(settings or get_settings(), KEY_SOIT_TRANSMIS, cfg)

    # ── Génération Word ───────────────────────────────────────────────────

    @tracked_export
    def generate_docx(
        self,
        persons: list[ListeGeneraleRow],
        cfg: SoitTransmisConfig,
        dest_path: Path,
        sign_date: date | None = None,
        grade_code_map: dict[str, str] | None = None,
        grades: list | None = None,
        settings: Settings | None = None,
    ) -> Path:
        from docx import Document
        from sme_gend.config.settings import project_root

        from sme_gend.config.settings import default_modele_path, resolve_modele_file

        template_path = resolve_modele_file("soit_transmis.docx") or default_modele_path(
            "soit_transmis.docx"
        )
        if not template_path.is_file():
            raise FileNotFoundError(f"Modèle introuvable : {template_path}")

        export_phase('render', total=len(persons), detail='Remplissage du modèle de soit-transmis')
        doc = Document(str(template_path))
        paras = doc.paragraphs

        ville_hdr = (cfg.ville or "").strip().upper() or "BRAZZAVILLE"

        # ── Trouver les paragraphes variables dans le corps ───────────────
        p07 = p08 = p10_body = p19 = None
        for p in paras:
            txt = p.text.strip()
            txt_up = txt.upper()
            if p07 is None and "Monsieur" in txt and "commissaire" in txt:
                p07 = p
            elif p08 is None and "directeur des services financiers" in txt:
                p08 = p
            elif p10_body is None and txt.startswith("-") and ville_hdr in txt_up:
                p10_body = p
            elif p19 is None and "honneur de vous transmettre" in txt:
                p19 = p

        # ── Remplir le destinataire ───────────────────────────────────────
        if p07 is not None:
            _first_run_set(p07, cfg.destinataire_titre)
        if p08 is not None:
            _first_run_set(p08, cfg.destinataire_role)

        # ── Remplir la ville (P10 corps) ──────────────────────────────────
        if p10_body is not None:
            _first_run_set(p10_body, f"-{cfg.ville.upper()}-")

        # ── Remplir P19 (compte) ──────────────────────────────────────────
        if p19 is not None:
            _fill_p19(p19, len(persons))

        # ── Remplir Table 0 (en-tête) ─────────────────────────────────────
        if doc.tables:
            t0 = doc.tables[0]
            # Cellule gauche [0,0] — référence N°
            p_ref = _find_para_containing(t0.cell(0, 0), "N°")
            if p_ref is not None:
                ref_num = cfg.reference_num.strip()
                ref_val = (
                    f"N°{ref_num}{cfg.reference_suffix}"
                    if ref_num
                    else f"N°_________{cfg.reference_suffix}"
                )
                _first_run_set(p_ref, ref_val)
            # Cellule droite [0,2] — lieu et date (date laissée vide : saisie manuelle sur le modèle)
            ville = (cfg.ville or "").strip() or "Brazzaville"
            p_date = (
                _find_para_containing(t0.cell(0, 2), ville)
                or _find_para_containing(t0.cell(0, 2), "Brazzaville")
            )
            if p_date is not None:
                date_str = sign_date.strftime("%d/%m/%Y") if sign_date else ""
                _first_run_set(p_date, f"{ville}, le {date_str}".rstrip())

        # ── Remplir Table 2 (liste des personnels — triée par grade puis nom) ─
        if len(doc.tables) >= 3:
            sorted_persons = sort_persons_by_grade(
                persons,
                grade_code_map=grade_code_map,
                grades=grades,
            )
            _fill_persons_table(doc.tables[2], sorted_persons, grade_code_map)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(dest_path, temporary_suffix=".docx") as staged:
            export_phase("write", total=1, detail="Écriture du document final")
            doc.save(str(staged))
            export_phase("finalize", detail="Confirmation du fichier et historique")

        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(
            settings=settings, doc_type="soit_transmis", format="docx",
            persons=[p.noms or "?" for p in list(persons)],
            dest_path=str(dest_path), count=len(persons),
        )
        return dest_path
