"""Ordre d'affichage / export par défaut — liste générale."""

from __future__ import annotations

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow

# En tête : libellés ListeGenerale.Fonction dans cet ordre exact (1→10), puis le reste
# uniquement par grade (PrefOrd TabGrade : plus petit = plus gradé, cf. ORDER BY PrefOrd).
# Au sein d'une même fonction prioritaire : même règle de grade.
FONCTION_PRIORITY_ORDER: tuple[str, ...] = (
    "COMGEND",
    "C2 CEMGEND",
    "INSPECTEUR IT",
    "DIRECTEUR DOE",
    "DIRECTEUR DPF",
    "DIRECTEUR DSF",
    "DIRECTEUR DIRTRANS",
    "DIRECTEUR DTRJD",
    "DIRECTEUR DIRMAT",
    "DIRECTEUR DDT",
)


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _norm_fonction(s: str | None) -> str:
    """Espaces internes normalisés pour coller aux saisies Access (« C2  CEMGEND » = « C2 CEMGEND »)."""
    return " ".join((s or "").strip().split()).casefold()


# Anciens libellés Access → même rang que les entrées actuelles (indices 2 et 3).
_LEGACY_FONCTION_PRIORITY_INDEX: dict[str, int] = {
    _norm_fonction("DIRECTEUR IT"): 2,
    _norm_fonction("DIRECTEUR DEO"): 3,
}


def _fonction_priority_index(fonction: str | None) -> int | None:
    """Indice dans FONCTION_PRIORITY_ORDER si la fonction correspond, sinon None."""
    f = _norm_fonction(fonction)
    if not f:
        return None
    for i, label in enumerate(FONCTION_PRIORITY_ORDER):
        if f == _norm_fonction(label):
            return i
    return _LEGACY_FONCTION_PRIORITY_INDEX.get(f)


def _grade_pref_maps(grades: list[GradeRefRow]) -> tuple[dict[str, int], dict[str, int]]:
    """Code grade et libellé -> PrefOrd (plus petit = plus gradé dans TabGrade)."""
    by_code: dict[str, int] = {}
    by_lib: dict[str, int] = {}
    for g in grades:
        po = g.pref_ord
        if po is None:
            continue
        cc = _norm(g.code)
        lb = _norm(g.libelle)
        if cc:
            by_code[cc] = po
        if lb:
            by_lib[lb] = po
    return by_code, by_lib


def _grade_sort_key(
    grade_field: str | None,
    by_code: dict[str, int],
    by_lib: dict[str, int],
) -> tuple[int, str]:
    """Clé de tri grade : PrefOrd croissant, puis libellé pour stabilité."""
    g = (grade_field or "").strip()
    if not g:
        return (999_999, "")
    gf = g.casefold()
    po = by_code.get(gf)
    if po is None:
        po = by_lib.get(gf)
    if po is None:
        return (999_999, gf)
    return (po, gf)


def sort_liste_generale_default_order(
    rows: list[ListeGeneraleRow],
    grades: list[GradeRefRow],
) -> list[ListeGeneraleRow]:
    """
    Règle métier liste générale :
    1) Fonctions listées dans FONCTION_PRIORITY_ORDER en tête (dans cet ordre) ;
       même fonction : du plus gradé au moins gradé (PrefOrd TabGrade).
    2) Toutes les autres lignes : du plus gradé au moins gradé, puis nom.
    """
    by_code, by_lib = _grade_pref_maps(grades)

    def key(r: ListeGeneraleRow) -> tuple:
        pi = _fonction_priority_index(r.fonction)
        gk = _grade_sort_key(r.grade, by_code, by_lib)
        nom = _norm(r.noms)
        rid = r.id or ""
        if pi is not None:
            return (0, pi, gk[0], gk[1], nom, rid)
        return (1, gk[0], gk[1], nom, rid)

    return sorted(rows, key=key)
