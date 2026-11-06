# Plan SME — S.G.I DPF (liste des présents & référentiels)

> **État 2026-05** : application **S.G.I DPF** (`sme_gend`), exploitation **MySQL** (`sme_periode`, `carriere_situation`, `ref_*`, `app_config`).  
> L’import Access (`smegend.mdb`) reste une **source d’alimentation** ponctuelle, pas le moteur courant.  
> Voir `README.md` et `docs/CHECKLIST_CONVERGENCE_SOT.md`.

## Objectifs produit

1. **Liste des présents** : alignée sur la maquette Excel (`modeles/`), aperçu HTML, export Excel.
2. **Effectifs** : **liste & édition SME** (onglets Consultation / Édition) — entrée unique dans la sidebar.
3. **Référentiels** : structures, subdivisions U→D→T, SubArme par structure, grades, fonctions, positions admin.
4. **SoT dossier ↔ carrière** : dossier agent maître (`agent_diplome`, `agent_passation_grade`, `agent_affectation`) projeté vers `carriere_situation` ; bandeaux et filtres si écart ou FK non résolue.
5. **Navigation** : sidebar par phases du mois SME + pilotage (cycle, clôture, archives).

## Architecture

| Couche | Rôle |
|--------|------|
| `config/` | Chemins, branding S.G.I DPF, contexte SME (`sme_context.json`) |
| `models/` | Dataclasses métier |
| `repositories/` | Accès MySQL |
| `services/` | Règles métier, ETL référentiels, SoT, exports |
| `ui/` | `ShellWindow` + vues lazy ; configs JSON métier dans `app_config` MySQL |

## Écrans principaux (sidebar)

| Vue | Contenu |
|-----|---------|
| **Tableau de bord** | Synthèse effectifs, raccourcis |
| **Liste & édition SME** | Onglet consultation (filtres, dossier) + onglet édition rapide inline |
| **Liste des présents** | Aperçu HTML + export ; mise en page dans `app_config` (`liste_present_layout`) |
| **Pilotage du mois** | Alertes workflow, bandeau SoT, checklist, cycle SME / clôture |
| **Référentiels** | Organigramme, grades, fonctions, hiérarchie subdivisions |

## Données MySQL (rappel)

- **Carrière affichée** : `carriere_situation` (mois actif : `est_actuelle = 1` ; mois fermé : filtre `sme_periode.annee_mois`).
- **Dossier agent** : tables `agent_*` ; projection via `sync_carriere_from_dossier`.
- **Référentiels** : `ref_structure`, `ref_subdivision_arme`, `ref_subdivision_noeud`, `ref_grade`, `ref_fonction`, etc.
- **Config UI** : table `app_config` (plus de lecture directe des JSON locaux en runtime).

## Clôture mensuelle

- Mois actif : `sme_context.json` + `sme_periode`.
- Archives : `%LOCALAPPDATA%\SGI_DPF\sme_archives\`.
- Scripts maintenance : `scripts/run_referentiel_maintenance.py`, `scripts/run_sot_metrics.py`.

## Liste des présents — export

- Maquette : `modeles/liste_des_presents.xlsx` (alignée sur l’effectif du mois ; rubriques liées aux structures / subdivisions RSA).
- **Configuration** : clé MySQL `liste_present_layout` — éditeur intégré « Mise en page (présents) ».
- Export : services `excel_template_exporter` / `liste_present_service`.

## Prochaines itérations (hors P0)

1. ~~Export présents fidélité 1:1 maquette~~ — fait (`liste_des_presents.xlsx`, rubriques strictes RSA).
2. ~~Fonctions filtrées par structure~~ — fait (`ref_fonction_structure`).
3. Fusion navigation « Situation numérique » / satellites (P1 UI).
