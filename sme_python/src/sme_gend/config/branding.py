"""Identité produit — S.G.I DPF (Système de Gestion de l'Information)."""

from __future__ import annotations

# Nom court affiché (barre titre, splash, connexion)
APP_NAME = "S.G.I DPF"

# Sous-titre institutionnel
APP_SUBTITLE = "Direction du Personnel et de la Formation"

# Nom complet pour boîtes « À propos »
APP_NAME_FULL = f"{APP_NAME} — {APP_SUBTITLE}"

# Acronyme métier conservé pour le cycle mensuel (Situation Mensuelle des Effectifs)
SME_LABEL = "SME"
SME_FULL = "Situation mensuelle des effectifs"

# Titre fenêtre principale
#WINDOW_TITLE = f"{APP_NAME} — {SME_FULL}"

WINDOW_TITLE = f"{APP_NAME} — {SME_FULL}"
# Organisation QSettings (préférences utilisateur locales)
QSETTINGS_ORG = "SGI-DPF"
QSETTINGS_ORG_LEGACY = "SME-Gendarmerie"

# Dossier utilisateur local (%LOCALAPPDATA%)
USER_DATA_DIR_NAME = "SGI_DPF"
LEGACY_USER_DATA_DIR_NAME = "SME_Gendarmerie"
