"""Limites d'âge et de service — source unique du logiciel.

Article 98 nouveau, loi n° 17-2023 du 27 mai 2023.
Les grades absents de cet article conservent le barème déjà utilisé par les
attestations, en attendant un texte de référence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.services.grade_label_format import normalize_grade_key

SOURCE_ARTICLE_98 = "article98"
SOURCE_LEGACY = "hors_article98"
STATUTORY_REFERENCE = "Article 98 nouveau — loi n° 17-2023 du 27 mai 2023"
STATUTORY_SOURCE_URL = "https://www.sgg.cg/JO/2023/congo-jo-2023-23.pdf#page=6"


@dataclass(frozen=True, slots=True)
class StatutoryLimit:
    code: str
    libelle: str
    limite_age: int
    limite_service: int
    source: str


# JO n° 23 du 8 juin 2023, pages 822–823. Career limits, not pension eligibility.
ARTICLE_98: tuple[StatutoryLimit, ...] = (
    StatutoryLimit("MDL", "Maréchal des Logis", 55, 37, SOURCE_ARTICLE_98),
    StatutoryLimit("MDC", "Maréchal des Logis Chef", 55, 37, SOURCE_ARTICLE_98),
    StatutoryLimit("ADJ", "Adjudant", 58, 40, SOURCE_ARTICLE_98),
    StatutoryLimit("ADC", "Adjudant-Chef", 58, 40, SOURCE_ARTICLE_98),
    StatutoryLimit("ADM", "Adjudant-Major", 58, 40, SOURCE_ARTICLE_98),
    StatutoryLimit("SLT", "Sous-Lieutenant", 60, 42, SOURCE_ARTICLE_98),
    StatutoryLimit("LTN", "Lieutenant", 60, 42, SOURCE_ARTICLE_98),
    StatutoryLimit("CNE", "Capitaine", 60, 42, SOURCE_ARTICLE_98),
    StatutoryLimit("CDT", "Commandant", 65, 47, SOURCE_ARTICLE_98),
    StatutoryLimit("LCL", "Lieutenant-Colonel", 65, 47, SOURCE_ARTICLE_98),
    StatutoryLimit("COL", "Colonel", 65, 47, SOURCE_ARTICLE_98),
    StatutoryLimit("CLM", "Colonel-Major", 65, 47, SOURCE_ARTICLE_98),
    StatutoryLimit("GDB", "Général de Brigade", 70, 52, SOURCE_ARTICLE_98),
    StatutoryLimit("GDD", "Général de Division", 70, 52, SOURCE_ARTICLE_98),
    StatutoryLimit("GCA", "Général de Corps d'Armée", 70, 52, SOURCE_ARTICLE_98),
    StatutoryLimit("GDA", "Général d'Armée", 70, 52, SOURCE_ARTICLE_98),
    StatutoryLimit("SDT", "Soldat", 50, 32, SOURCE_ARTICLE_98),
    StatutoryLimit("CPL", "Caporal", 50, 32, SOURCE_ARTICLE_98),
    StatutoryLimit("CCH", "Caporal-Chef", 50, 32, SOURCE_ARTICLE_98),
    # Keep existing distinct codes to avoid ambiguous duplicate aliases in
    # configurations that already contain MDC/SCH or ADM/MAJ side by side.
    StatutoryLimit("SCH", "Sergent-Chef", 55, 37, SOURCE_ARTICLE_98),
    StatutoryLimit("MAJ", "Major", 58, 40, SOURCE_ARTICLE_98),
)

# Barème historique des attestations pour les grades que l'art. 98 ne cite pas.
LEGACY_LIMITS: tuple[StatutoryLimit, ...] = (
    StatutoryLimit("GAD", "Gendarme Adjoint", 55, 35, SOURCE_LEGACY),
    StatutoryLimit("GND", "Gendarme", 55, 35, SOURCE_LEGACY),
    StatutoryLimit("BRG", "Brigadier", 55, 37, SOURCE_LEGACY),
    StatutoryLimit("BRC", "Brigadier-Chef", 55, 37, SOURCE_LEGACY),
    StatutoryLimit("ASP", "Aspirant", 60, 42, SOURCE_LEGACY),
)

_BY_CODE: dict[str, StatutoryLimit] = {row.code: row for row in (*ARTICLE_98, *LEGACY_LIMITS)}

# Plus long d'abord : « maréchal des logis chef » avant « maréchal des logis ».
_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("soldat", "SDT"), ("sdt", "SDT"),
            ("soldat de 1re classe", "SDT"), ("soldat de 2e classe", "SDT"),
            ("matelot", "SDT"), ("matelot de 1re classe", "SDT"), ("matelot de 2e classe", "SDT"),
            ("caporal", "CPL"), ("cpl", "CPL"),
            ("quartier maitre de 2e classe", "CPL"),
            ("caporal chef", "CCH"), ("cch", "CCH"),
            ("quartier maitre de 1re classe", "CCH"),
            ("sergent", "MDL"), ("sgt", "MDL"), ("second maitre", "MDL"),
            ("marechal de logis", "MDL"), ("marechal de logis chef", "MDC"),
            ("maitre", "MDC"), ("premier maitre", "ADJ"),
            ("maitre principal", "ADC"), ("maitre major", "ADM"),
            ("enseigne de vaisseau de 2e classe", "SLT"),
            ("enseigne de vaisseau de 1re classe", "LTN"),
            ("lieutenant de vaisseau", "CNE"), ("capitaine de corvette", "CDT"),
            ("capitaine de fregate", "LCL"), ("capitaine de vaisseau", "COL"),
            ("capitaine de vaisseau major", "CLM"), ("contre amiral", "GDB"),
            ("vice amiral", "GDD"), ("vice amiral d escadre", "GCA"), ("amiral", "GDA"),
            ("general de brigade aerienne", "GDB"), ("general de division aerienne", "GDD"),
            ("general de corps d armee aerienne", "GCA"), ("general d armee aerienne", "GDA"),
            ("marechal des logis chef", "MDC"),
            ("mdl chef", "MDC"),
            ("mdlchef", "MDC"),
            ("mdc", "MDC"),
            ("marechal des logis", "MDL"),
            ("mdl", "MDL"),
            ("adjudant major", "ADM"),
            ("adm", "ADM"),
            ("adjudant chef", "ADC"),
            ("adc", "ADC"),
            ("adjudant", "ADJ"),
            ("adj", "ADJ"),
            ("sous lieutenant", "SLT"),
            ("slt", "SLT"),
            ("lieutenant colonel", "LCL"),
            ("lcl", "LCL"),
            ("lieutenant", "LTN"),
            ("ltn", "LTN"),
            ("lt", "LTN"),
            ("capitaine", "CNE"),
            ("cne", "CNE"),
            ("cpt", "CNE"),
            ("commandant", "CDT"),
            ("cdt", "CDT"),
            ("colonel major", "CLM"),
            ("clm", "CLM"),
            ("colonel", "COL"),
            ("col", "COL"),
            ("general de brigade", "GDB"),
            ("gdb", "GDB"),
            ("general de division", "GDD"),
            ("gdd", "GDD"),
            ("general de corps d armee", "GCA"),
            ("gca", "GCA"),
            ("general d armee", "GDA"),
            ("gda", "GDA"),
            ("gendarme adjoint", "GAD"),
            ("gad", "GAD"),
            ("gendarme", "GND"),
            ("gnd", "GND"),
            ("brigadier chef", "BRC"),
            ("brc", "BRC"),
            ("brigadier", "BRG"),
            ("brg", "BRG"),
            ("sergent chef", "SCH"),
            ("sch", "SCH"),
            ("major", "MAJ"),
            ("maj", "MAJ"),
            ("aspirant", "ASP"),
            ("asp", "ASP"),
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

LADDER: tuple[str, ...] = tuple(row.code for row in ARTICLE_98)


def canonical_grade(raw: str | None) -> str | None:
    """Code court (MDC, ADJ, SLT…) à partir d'un code ou d'un libellé."""
    nk = normalize_grade_key(raw or "")
    if not nk:
        return None
    for alias, code in _ALIASES:
        if nk == alias or nk.startswith(alias + " "):
            return code
    return None


def exact_canonical_grade(raw: str | None) -> str | None:
    """Alias connu entier ; aucune correspondance par préfixe ou sous-chaîne."""
    key = normalize_grade_key(raw or "")
    return next((code for alias,code in _ALIASES if key == alias),None)


def limit_row(raw: str | None) -> StatutoryLimit | None:
    code = canonical_grade(raw)
    if code is None:
        return None
    return _BY_CODE.get(code)


def article98_limits(raw: str | None) -> tuple[int, int] | None:
    """Âge et services de l'article 98, ou None si le grade n'y figure pas."""
    row = limit_row(raw)
    if row is None or row.source != SOURCE_ARTICLE_98:
        return None
    return (row.limite_age, row.limite_service)


def limits_for_grade(raw: str | None) -> tuple[int, int] | None:
    """Barème connu (article 98, puis grades historiques hors décret)."""
    row = limit_row(raw)
    if row is None:
        return None
    return (row.limite_age, row.limite_service)


def default_grade_rows() -> list[dict[str, int | str]]:
    """Lignes initiales du module Limite d'âge (mêmes chiffres que l'avancement)."""
    rows: list[dict[str, int | str]] = []
    for item in (*ARTICLE_98, *LEGACY_LIMITS):
        rows.append(
            {
                "grade": item.libelle,
                "limite_age": item.limite_age,
                "limite_service": item.limite_service,
            }
        )
    return rows


def grade_libelle(code: str) -> str:
    row = _BY_CODE.get(code)
    return row.libelle if row else code
