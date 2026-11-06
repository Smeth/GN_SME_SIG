"""Ordre d'affichage / export par défaut — liste générale."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.grade_label_format import format_grade_display, normalize_grade_key


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _norm_fonction(s: str | None) -> str:
    """Espaces internes normalisés (« C2  CEMGEND » = « C2 CEMGEND »)."""
    return " ".join((s or "").strip().split()).casefold()


def _fonction_priority_index(
    fonction: str | None,
    priority_index: dict[str, int],
) -> int | None:
    f = _norm_fonction(fonction)
    if not f:
        return None
    return priority_index.get(f)


def _grade_pref_maps(grades: list[GradeRefRow]) -> tuple[dict[str, int], dict[str, int]]:
    """Code grade et libellé -> PrefOrd (plus petit = plus gradé dans TabGrade)."""
    by_code: dict[str, int] = {}
    by_lib: dict[str, int] = {}
    for g in grades:
        po = g.pref_ord
        if po is None:
            continue
        cc = normalize_grade_key(g.code)
        lb = normalize_grade_key(g.libelle)
        if cc and (cc not in by_code or po < by_code[cc]):
            by_code[cc] = po
        if lb and (lb not in by_lib or po < by_lib[lb]):
            by_lib[lb] = po
    return by_code, by_lib


def _grade_sort_key(
    grade_field: str | None,
    by_code: dict[str, int],
    by_lib: dict[str, int],
) -> tuple[int, str]:
    """Clé de tri grade : PrefOrd croissant (préséance), puis libellé pour stabilité."""
    g = (grade_field or "").strip()
    if not g:
        return (999_999, "")
    gf = normalize_grade_key(g)
    po = by_code.get(gf)
    if po is None:
        po = by_lib.get(gf)
    if po is None:
        from sme_gend.services.soit_transmis_service import _grade_rank

        return (_grade_rank(g), gf)
    return (po, gf)


class GradeSortHelper:
    """Tri colonne Grade via PrefOrd TabGrade (préséance), pas l'ordre alphabétique."""

    def __init__(self, grades: list[GradeRefRow] | None = None) -> None:
        self._grades = tuple(grades or ())
        self._display_cache: dict[str | None, str] = {}
        self._sort_cache: dict = {}
        self._by_code, self._by_lib = _grade_pref_maps(grades or [])

    def display_label(self, grade: str | None) -> str:
        """Code du référentiel, sans changer le grade enregistré ni son tri."""
        if grade not in self._display_cache:
            self._display_cache[grade] = format_grade_display(grade, self._grades)
        return self._display_cache[grade]

    def sort_key(self, grade_field: str | None) -> tuple[int, str]:
        if grade_field not in self._sort_cache:
            self._sort_cache[grade_field] = _grade_sort_key(grade_field, self._by_code, self._by_lib)
        return self._sort_cache[grade_field]

    def compare_less(self, left: str | None, right: str | None) -> bool:
        return self.sort_key(left) < self.sort_key(right)


def sort_liste_generale_default_order(
    rows: list[ListeGeneraleRow],
    grades: list[GradeRefRow],
    *,
    priority_index: dict[str, int] | None = None,
) -> list[ListeGeneraleRow]:
    """
    Règle métier liste générale :
    1) Fonctions prioritaires (config MySQL / ref_fonction) en tête ;
       même fonction : du plus gradé au moins gradé (PrefOrd TabGrade).
    2) Toutes les autres lignes : du plus gradé au moins gradé, puis nom.
    """
    by_code, by_lib = _grade_pref_maps(grades)
    prio = priority_index or {}

    def key(r: ListeGeneraleRow) -> tuple:
        pi = _fonction_priority_index(r.fonction, prio)
        gk = _grade_sort_key(r.grade, by_code, by_lib)
        nom = _norm(r.noms)
        rid = r.id or ""
        if pi is not None:
            return (0, pi, gk[0], gk[1], nom, rid)
        return (1, gk[0], gk[1], nom, rid)

    return sorted(rows, key=key)


def sort_liste_generale_by_grade(
    rows: list[ListeGeneraleRow],
    grades: list[GradeRefRow],
) -> list[ListeGeneraleRow]:
    """Liste générale : du plus gradé au moins gradé (PrefOrd), puis nom."""
    if not rows:
        return []
    by_code, by_lib = _grade_pref_maps(grades)

    def key(r: ListeGeneraleRow) -> tuple:
        gk = _grade_sort_key(r.grade, by_code, by_lib)
        return (gk[0], gk[1], _norm(r.noms), r.id or "")

    return sorted(rows, key=key)


def sort_liste_generale_for_settings(
    rows: list[ListeGeneraleRow],
    grades: list[GradeRefRow],
    settings: Settings,
) -> list[ListeGeneraleRow]:
    """Tri avec fonctions prioritaires (édition SME, pas la liste générale)."""
    from sme_gend.services.fonction_priority_service import build_priority_index

    if not rows:
        return []
    prio = build_priority_index(settings)
    return sort_liste_generale_default_order(list(rows), grades, priority_index=prio)


def sort_liste_rows_like_ui(
    rows: list[ListeGeneraleRow],
    grades: list[GradeRefRow],
    settings: Settings | None = None,
) -> list[ListeGeneraleRow]:
    """Tri écran et impression de la liste générale : ordre des grades."""
    _ = settings
    return sort_liste_generale_by_grade(list(rows), grades)
