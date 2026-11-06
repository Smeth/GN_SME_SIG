"""Libellés de fonctions qualifiés par subdivision (CHEF DE … + nom/sigle)."""

from __future__ import annotations

import re

from sme_gend.services.fonction_structure_policy import fold_label
from sme_gend.services.fonction_subdivision_kind import subdivision_kind

# Types de subdivision pour lesquels on applique « préfixe + qualificatif ».
_CHEF_PREFIX_BY_KIND: dict[str, str] = {
    "DIVISION": "CHEF DE DIVISION",
    "SECTION": "CHEF DE SECTION",
    "SECRETARIAT": "CHEF DE SECRETARIAT",
    "POLE": "CHEF DE POLE",
    "SERVICE": "CHEF DE SERVICE",
    "DEPARTEMENT": "CHEF DE DEPARTEMENT",
}

# Mots de tête à retirer du libellé subdivision pour obtenir le qualificatif.
_KIND_STRIP_TOKENS: dict[str, tuple[str, ...]] = {
    "DIVISION": ("DIVISION", "DIVISIONS"),
    "SECTION": ("SECTION", "SECTIONS"),
    "SECRETARIAT": ("SECRETARIAT", "SECRETARIATS"),
    "POLE": ("POLE", "POLES"),
    "SERVICE": ("SERVICE", "SERVICES"),
    "DEPARTEMENT": ("DEPARTEMENT", "DEPARTEMENTS"),
}

_ARTICLE_PREFIXES = (
    "DU ",
    "DE LA ",
    "DE L ",
    "DE L'",
    "DE ",
    "D ",
    "D'",
    "LA ",
    "LE ",
    "LES ",
)


def subdivision_qualifier(subdivision: str | None, kind: str | None = None) -> str:
    """Nom ou sigle de l'unité (sans le mot DIVISION / SECTION / …)."""
    raw = " ".join((subdivision or "").split()).strip()
    if not raw:
        return ""
    kind = kind or subdivision_kind(subdivision) or ""
    folded = fold_label(raw)
    tokens = _KIND_STRIP_TOKENS.get(kind, ())
    for head in tokens:
        head_f = fold_label(head)
        head_words = head_f.split()
        raw_words = raw.split()
        if folded == head_f:
            return ""
        if len(raw_words) > len(head_words) and fold_label(
            " ".join(raw_words[: len(head_words)])
        ) == head_f:
            return _clean_qualifier(" ".join(raw_words[len(head_words) :]))
    # Sigle court ou libellé sans préfixe type (ex. « DGP », « PERS S/OFF »).
    if kind in _CHEF_PREFIX_BY_KIND:
        return _clean_qualifier(raw)
    return _clean_qualifier(raw)


def _clean_qualifier(text: str) -> str:
    t = " ".join(text.split()).strip().upper()
    t = t.replace("/", " / ")
    t = re.sub(r"\s+", " ", t)
    changed = True
    while changed and t:
        changed = False
        for prefix in _ARTICLE_PREFIXES:
            if t.startswith(prefix):
                t = t[len(prefix) :].strip()
                changed = True
                break
    return t


def qualified_poste_libelle(subdivision: str | None) -> str | None:
    """Libellé attendu pour le poste de tête de cette subdivision, ou None."""
    if not (subdivision or "").strip():
        return None
    kind = subdivision_kind(subdivision)
    if not kind:
        return None
    prefix = _CHEF_PREFIX_BY_KIND.get(kind)
    if not prefix:
        return None
    qual = subdivision_qualifier(subdivision, kind)
    if not qual:
        return prefix
    return f"{prefix} {qual}"


def fonction_matches_qualified_poste(
    fonction: str | None,
    subdivision: str | None,
) -> bool:
    """True si la fonction correspond au poste qualifié (ou alias historiques)."""
    expected = qualified_poste_libelle(subdivision)
    if not expected:
        return False
    fn = fold_label(fonction)
    if not fn:
        return False
    fn_alt = fold_label((fonction or "").replace("_", " ").replace("-", " "))
    exp = fold_label(expected)
    if fn == exp or fn.startswith(exp + " ") or fn_alt == exp or fn_alt.startswith(exp + " "):
        return True
    kind = subdivision_kind(subdivision)
    if kind == "SECTION":
        qual = fold_label(subdivision_qualifier(subdivision, kind))
        if fn in {fold_label("CDS"), fold_label("CHEF DE SECTION")}:
            return True
        if qual and fn in {fold_label(f"CDS {qual}"), fold_label(f"CHEF DE SECTION {qual}")}:
            return True
    if kind == "SECRETARIAT":
        qual = fold_label(subdivision_qualifier(subdivision, kind))
        if fn in {fold_label("CHEFSEC"), fold_label("CHEF DE SECRETARIAT")}:
            return True
        if qual and fn in {
            fold_label(f"CHEFSEC {qual}"),
            fold_label(f"CHEF SEC {qual}"),
            fold_label(f"CHEF-SEC {qual}"),
        }:
            return True
    if kind == "DIVISION":
        qual = fold_label(subdivision_qualifier(subdivision, kind))
        compact = qual.replace(" ", "_") if qual else ""
        if qual and fn in {
            fold_label(f"CHEF DIVISION {qual}"),
            fold_label(f"CHEF_DE_{compact}"),
            fold_label(f"CHEF_{compact}"),
            fold_label(compact.replace("_", " ")),
        }:
            return True
    # Ancien libellé générique (une subdivision = un siège).
    prefix = _CHEF_PREFIX_BY_KIND.get(kind or "")
    if prefix and fn == fold_label(prefix):
        return True
    return False


def alias_keys_for_qualified_poste(subdivision: str | None) -> set[str]:
    """Clés de repli pour détecter le titulaire (occupation, postes vacants)."""
    expected = qualified_poste_libelle(subdivision)
    if not expected:
        return set()
    keys = {fold_label(expected)}
    kind = subdivision_kind(subdivision)
    prefix = _CHEF_PREFIX_BY_KIND.get(kind or "")
    qual = subdivision_qualifier(subdivision, kind)
    if prefix:
        keys.add(fold_label(prefix))
    if kind == "SECTION" and qual:
        keys.update(
            {
                fold_label(f"CDS {qual}"),
                fold_label("CDS"),
                fold_label("CHEF DE SECTION"),
            }
        )
    if kind == "DIVISION" and qual:
        keys.update({fold_label("CHEF DE DIVISION")})
    if kind == "SECRETARIAT" and qual:
        keys.update(
            {
                fold_label(f"CHEFSEC {qual}"),
                fold_label("CHEFSEC"),
                fold_label("CHEF DE SECRETARIAT"),
            }
        )
    return {k for k in keys if k}
