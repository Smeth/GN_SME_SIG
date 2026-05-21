"""Indices de la QStackedWidget principale — doivent rester alignés sur ShellWindow.register_page."""

from __future__ import annotations

# Ordre strict = ordre d’appel à register_page dans shell_window.py
PAGE_DASHBOARD = 0
PAGE_LISTE_GENERALE = 1
PAGE_STRUCTURES_EFFECTIFS = 2
PAGE_STRUCTURES_HORS_DETACHE = 3
PAGE_LAYOUT_EDITOR = 4
PAGE_LISTE_PRESENTS = 5
PAGE_DETACHES = 6
PAGE_STRUCTURES = 7
PAGE_GRADES = 8
PAGE_FONCTIONS = 9
PAGE_DETACHES_CONFIG = 10
PAGE_NUMERIQUE = 11
PAGE_ARCHIVES_CLOSURE = 12
PAGE_SETTINGS = 13
PAGE_GAIN_PERTE = 14
PAGE_MALADE_PUNIS = 15
PAGE_LIMITE_AGE = 16
PAGE_SOIT_TRANSMIS = 17
PAGE_CSV_IMPORT = 18
PAGE_DOC_GEN_LOG = 19
PAGE_EDITION_SME = 20
PAGE_PADMIN = 21

# Aliases pour la compatibilité avec l’ancien code à trois pages séparées
PAGE_NUMERIQUE_MATRICE = PAGE_NUMERIQUE
PAGE_NUMERIQUE_INDISPO = PAGE_NUMERIQUE
PAGE_NUMERIQUE_SATELLITE = PAGE_NUMERIQUE

PAGE_COUNT = 22

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
    "PAGE_GAIN_PERTE",
    "PAGE_GRADES",
    "PAGE_LAYOUT_EDITOR",
    "PAGE_LIMITE_AGE",
    "PAGE_LISTE_GENERALE",
    "PAGE_LISTE_PRESENTS",
    "PAGE_MALADE_PUNIS",
    "PAGE_NUMERIQUE",
    "PAGE_NUMERIQUE_INDISPO",
    "PAGE_NUMERIQUE_MATRICE",
    "PAGE_NUMERIQUE_SATELLITE",
    "PAGE_PADMIN",
    "PAGE_SETTINGS",
    "PAGE_SOIT_TRANSMIS",
    "PAGE_STRUCTURES",
    "PAGE_STRUCTURES_EFFECTIFS",
    "PAGE_STRUCTURES_HORS_DETACHE",
]
