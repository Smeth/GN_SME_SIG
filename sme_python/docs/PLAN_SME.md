# Plan SME — application Python (liste des présents & référentiels)

## Objectifs produit

1. **Liste des présents** : alignée sur la maquette `resultat.xlsx` (liste par **structure**), **aperçu** dans l’application, **export Excel** (mise à jour / génération du fichier).
2. **Référentiels** : consultation (puis édition) des **structures**, **subdivisions** (hiérarchie Secun → Secdeux → Sectrois), **grades**, **fonctions** (distinctes ou table dédiée selon la base).
3. **Navigation** : **sidebar** (zones fonctionnelles claires) + **barre d’onglets** ou **pile de vues** (éviter la navigation uniquement par barre d’outils).

## Architecture technique (déjà en place ou cible)

| Couche | Rôle |
|--------|------|
| `config/` | Chemins `smegend.mdb`, maquette `resultat.xlsx` (`SME_TEMPLATE_PATH` ou `data/resultat.xlsx`) |
| `models/` | Dataclasses tables Access |
| `repositories/` | SQL lecture (puis écriture pour référentiels) |
| `services/` | Règles métier : présents, groupement par structure, HTML aperçu, export Excel |
| `ui/` | `ShellWindow` (sidebar + `QStackedWidget`), une vue par écran |

## Modules écran (sidebar)

| Vue | Contenu |
|-----|---------|
| **Accueil** | Raccourcis, état du mois SME (à brancher sur `TabSme`), chemins base / maquette |
| **Liste générale** | Grille existante (tout / présents / filtre) |
| **Structures** | Onglet unique : `TabStructure`, couples (Structure, SubArme) liste générale, hiérarchie `TabSecun` / `TabSecdeux` / `TabSectrois` |
| **Grades** | Table `TabGrade` (ordre de préséance) |
| **Fonctions** | Valeurs distinctes `ListeGenerale.Fonction` (en attendant une table dédiée) |
| **Liste des présents** | Aperçu HTML par structure + export Excel |

## Données Access (rappel)

- **Liste générale** : `ListeGenerale` (cœur).
- **Structures** : `TabStructure` (`code`, `designation`, `OderTri`).
- **Subdivisions** : `TabSecun` → `TabSecdeux` (`CodeU`) → `TabSectrois` (`CodeD`).
- **Grades** : `TabGrade` (`CodeG`, `Grade`, `PrefOrd`).
- **SME mensuel** : `TabSme` (`Codsme`, `mois`, `Statut`) — écran dédié à venir.

## Liste des présents — export Excel

- **Maquette** : `resultat.xlsx` (feuille `Liste`, lignes 1–3 = en-tête document, lignes 4–6 = styles de section / sous-section / données).
- **Configuration** : `data/liste_present_layout.json` — ordre des sections, codes structure, titres personnalisés, regroupement par subdivision. Éditeur intégré (**Mise en page liste**) avec **glisser-déposer**.
- **Export fidèle** : `excel_template_exporter` recrée une feuille `Liste_SME` en copiant **styles** depuis les lignes modèle de la maquette (pas d’écriture dans la feuille `Liste` d’origine).

## Aperçu dans l’application

- **HTML** (`QTextBrowser`) : **18 colonnes** comme la feuille `Liste` de `resultat.xlsx`, en-tête de tableau, lignes de **section** (structure : titre principal + sous-titre), puis **sous-section** par **subdivision d’arme**, puis lignes personnel (même ordre que l’export Excel).
- Ce n’est pas une image du fichier Excel : c’est un **rendu recréé** (CSS type Calibri / bordures / bandes) pour coller à la maquette sans intégrer Excel dans l’UI.
- Option ultérieure : remplissage **direct** des cellules de la feuille modèle pour **fidélité 1:1** (fusions, largeurs exactes).

## Référentiels — édition

- **Phase actuelle** : **lecture seule** (grilles + actualiser).  
- **Phase suivante** : formulaires `INSERT`/`UPDATE`/`DELETE` avec contraintes (intégrité subdivisions ↔ structure).

## Fichiers de configuration

| Variable | Usage |
|----------|--------|
| `SMEDB_PATH` | Fichier `.mdb` / `.accdb` |
| `SME_TEMPLATE_PATH` | Maquette `resultat.xlsx` |
| Dossier `data/` | `smegend.mdb`, `resultat.xlsx` (recommandé) |

## Prochaines itérations suggérées

1. Onglet **SME** : liste `TabSme`, mois ouvert, verrouillage.  
2. Édition référentiels + validation.  
3. Export Excel **strictement** calqué sur la maquette (lignes vides, fusions).  
4. Migration MySQL : mêmes services, autre `repository` d’implémentation.
