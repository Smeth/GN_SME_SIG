"""Correspondance subdivisions template (present.xlsx / layout) ↔ libellés MDB (SubArme).

Port de ``rg_template.py`` (projet DATABASE) — règles décrites dans
``ALGORITHME_LISTE_PRESENTS.md`` § 3.5–3.6.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Alias explicites template → MDB (complétés par motifs RG dynamiques)
# ---------------------------------------------------------------------------

_STATIC_ALIASES: dict[tuple[str, str], str] = {
    # RG-BZV
    ("RG-BZV", "COMMANDEMENT RG-BZV"): "COMMANDEMENT",
    ("RG-BZV", "ETAT-MAJOR RG-BZV"): "ETAT-MAJOR",
    ("RG-BZV", "ONU"): "DETACHEMENT ONU",
    (
        "RG-BZV",
        "SECTION DE RECHERCHES JUDICIAIRE RG-BZV",
    ): "SECTION DE RECHERCHES JUDICIAIRES",
    ("RG-BZV", "BRIGADE TERRITORIALE DE MBAMOU"): "BRIGADE TERRITORIALE MBAMOU",
    (
        "RG-BZV",
        "PSIG COMPAGNIE DE GENDARMERIE TERRITORIALE BZV-1",
    ): "PELOTON DE SURVEILLANCE ET D'INTERVENTION DE LA GENDARMERIE BZV-1",
    (
        "RG-BZV",
        "PSIG COMPAGNIE DE GENDARMERIE TERRITORIALE BZV-2",
    ): "PELOTON DE SURVEILLANCE ET D'INTERVENTION DE LA GENDARMERIE BZV-2",
    (
        "RG-BZV",
        "PSIG COMPAGNIE DE GENDARMERIE TERRITORIALE DE KINTELE",
    ): "PELOTON DE SURVEILLANCE ET D'INTERVENTION DE LA GENDARMERIE DE KINTELE",
    (
        "RG-BZV",
        "DETACHEMENT DE GENDARMERIE DU PALAIS DE JUSTICE",
    ): "DETACHEMENT DE GENDARMERIE DU PALAIS DE JUSTICE",
    (
        "RG-BZV",
        "COUR DE COMPTES ET DE DISCIPLINE BUDGETAIRE",
    ): "DETACHEMENT COUR DE COMPTES ET DE DISCIPLINE BUDGETAIRE",
    # RG-PNR
    ("RG-PNR", "DET. CA-PNR"): "DETACHEMENT COUR D'APPEL DE POINTE-NOIRE",
    (
        "RG-PNR",
        "TRIBUNAL DE GRANDE INSTANCE DE POINTE NOIRE",
    ): "DETACHEMENT TRIBUNAL DE GRANDE INSTANCE DE POINTE NOIRE",
    ("RG-PNR", "ESSIG RG-PNR"): "ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE",
    ("RG-PNR", "ESSIG-PNR"): "ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE",
    # RG-POO
    ("RG-POO", "ESSIG RG-POO"): "ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE",
    # RG-LEK
    (
        "RG-LEK",
        "ESCADRON DE GENDARMERIE MOBILE RG-LEK",
    ): "ESCADRON DE GENDARMERIE MOBILE",
    # RG-CVO
    ("RG-CVO", "PSIG KELLE"): "PELOTON DE SURVEILLANCE ET D'INTERVENTION DE KELLE",
    # DPF / divers
    ("COMGEND", "Direction"): "Direction",
}

# Variantes orthographiques MDB acceptées pour un libellé template
_EXTRA_MDB: dict[tuple[str, str], list[str]] = {
    (
        "RG-BZV",
        "PSIG COMPAGNIE DE GENDARMERIE TERRITORIALE DE KINTELE",
    ): [
        "PELOTON DE SURVEILLENCE ET D'INTERVENTION DE LA GENDARMERIE DE KINTELE",
    ],
}

_RG_CODES = (
    "RG-BZV", "RG-POO", "RG-BZA", "RG-LEK", "RG-NRI", "RG-KLU", "RG-PNR",
    "RG-DJL", "RG-PLT", "RG-NKA", "RG-CUV", "RG-CVO", "RG-COB", "RG-SGH", "RG-LIK",
)

# Motifs communs à toutes les régions (template avec suffixe code → libellé MDB)
_RG_WILDCARD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("COMMANDEMENT RG-{code}", "COMMANDEMENT"),
    ("ETAT-MAJOR RG-{code}", "ETAT-MAJOR"),
    ("SECTION DE RECHERCHES JUDICIAIRE RG-{code}", "SECTION DE RECHERCHES JUDICIAIRES"),
    ("SECTION DE RECHERCHES JUDICIAIRES RG-{code}", "SECTION DE RECHERCHES JUDICIAIRES"),
    ("ESSIG RG-{code}", "ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE"),
)


def _build_aliases() -> dict[tuple[str, str], str]:
    out = dict(_STATIC_ALIASES)
    for code in _RG_CODES:
        for tpl_pat, mdb in _RG_WILDCARD_PATTERNS:
            out[(code, tpl_pat.format(code=code))] = mdb
    return out


ALIASES: dict[tuple[str, str], str] = _build_aliases()


def normalize(text: str | None) -> str:
    """Uppercase, sans accents, espaces compressés."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def strip_code_suffix(template_sub: str, code: str) -> str:
    t = normalize(template_sub)
    c = normalize(code)
    if not t or not c:
        return t
    if t.endswith(c):
        return t[: -len(c)].strip()
    t = t.replace(c, "").strip()
    short = c.replace("RG-", "")
    if short:
        t = t.replace(short, "").strip()
    return t


def is_brigade_recherche(label: str) -> bool:
    u = normalize(label)
    return "BRIGADE DE RECHERCHE" in u or "BRIGADE DE RECHERCHES" in u


def is_compagnie_territoriale(label: str) -> bool:
    u = normalize(label)
    return u.startswith("COMPAGNIE") and "TERRITORIALE" in u


def is_rg_compagnie(label: str) -> bool:
    """Compagnie territoriale RG (hors brigades de recherche)."""
    u = normalize(label)
    if not u.startswith("COMPAGNIE"):
        return False
    if is_brigade_recherche(u):
        return False
    return "TERRITORIALE" in u or "GENDARMERIE TERRITORIALE" in u


def template_sub_matches_mdb(code: str, template_sub: str, mdb_sub: str) -> bool:
    """True si le libellé MDB correspond à la rubrique template pour cette structure."""
    if not (template_sub or "").strip():
        return not (mdb_sub or "").strip()
    if not (mdb_sub or "").strip():
        return False

    t = normalize(template_sub)
    m = normalize(mdb_sub)
    c = (code or "").strip()

    if t == m:
        return True

    alias = ALIASES.get((c, template_sub.strip()))
    if alias is not None and normalize(alias) == m:
        return True

    for extra in _EXTRA_MDB.get((c, template_sub.strip()), []):
        if normalize(extra) == m:
            return True

    base = strip_code_suffix(template_sub, c)
    if base and base == m:
        return True

    if is_brigade_recherche(t) != is_brigade_recherche(m):
        return False
    if is_compagnie_territoriale(t) and is_brigade_recherche(m):
        return False
    if is_brigade_recherche(t) and is_compagnie_territoriale(m):
        return False

    if m in t or t in m:
        return True

    return False
