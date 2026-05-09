"""Indices de la QStackedWidget principale — doivent rester alignés sur ShellWindow.register_page."""

from __future__ import annotations

# Ordre strict = ordre d’appel à register_page dans shell_window.py
PAGE_DASHBOARD = 0
PAGE_LISTE_GENERALE = 1
PAGE_STRUCTURES_EFFECTIFS = 2
PAGE_LAYOUT_EDITOR = 3
PAGE_LISTE_PRESENTS = 4
PAGE_DETACHES = 5
PAGE_STRUCTURES = 6
PAGE_GRADES = 7
PAGE_FONCTIONS = 8
PAGE_DETACHES_CONFIG = 9
PAGE_NUMERIQUE = 10
PAGE_ARCHIVES_CLOSURE = 11
PAGE_SETTINGS = 12
PAGE_GAIN_PERTE = 13
PAGE_LIMITE_AGE    = 14
PAGE_SOIT_TRANSMIS = 15
PAGE_CSV_IMPORT    = 16
PAGE_DOC_GEN_LOG   = 17
PAGE_EDITION_SME   = 18
PAGE_PADMIN        = 19

# Aliases pour la compatibilité avec l’ancien code à trois pages séparées
PAGE_NUMERIQUE_MATRICE = PAGE_NUMERIQUE
PAGE_NUMERIQUE_INDISPO = PAGE_NUMERIQUE
PAGE_NUMERIQUE_SATELLITE = PAGE_NUMERIQUE

PAGE_COUNT = 20

# Séparateurs du menu Navigation (avant ces entrées) — alignés sur la sidebar restructurée
NAV_MENU_SEPARATOR_BEFORE: frozenset[int] = frozenset(
    {
        PAGE_DETACHES,         # après le groupe Effectifs
        PAGE_STRUCTURES,       # après le groupe Détachés
        PAGE_ARCHIVES_CLOSURE, # après le groupe Référentiels + numérique
    }
)

__all__ = [
    "NAV_MENU_SEPARATOR_BEFORE",
    "PAGE_ARCHIVES_CLOSURE",
    "PAGE_COUNT",
    "PAGE_CSV_IMPORT",
    "PAGE_DASHBOARD",
    "PAGE_DETACHES",
    "PAGE_DETACHES_CONFIG",
    "PAGE_DOC_GEN_LOG",
    "PAGE_EDITION_SME",
    "PAGE_FONCTIONS",
    "PAGE_PADMIN",
    "PAGE_GAIN_PERTE",
    "PAGE_GRADES",
    "PAGE_LAYOUT_EDITOR",
    "PAGE_LIMITE_AGE",
    "PAGE_LISTE_GENERALE",
    "PAGE_LISTE_PRESENTS",
    "PAGE_NUMERIQUE",
    "PAGE_NUMERIQUE_INDISPO",
    "PAGE_NUMERIQUE_MATRICE",
    "PAGE_NUMERIQUE_SATELLITE",
    "PAGE_SETTINGS",
    "PAGE_SOIT_TRANSMIS",
    "PAGE_STRUCTURES",
    "PAGE_STRUCTURES_EFFECTIFS",
]
