# SME Gendarmerie (client Python)

Application PySide6 : **liste générale**, **référentiels** (structures, subdivisions, grades, fonctions), **liste des présents** (aperçu HTML + export Excel), navigation par **menu latéral**.

## Prérequis

- Python 3.10+
- Pilote ODBC **Microsoft Access**
- Dépendances : `PySide6`, `pyodbc`, `openpyxl`

## Installation

```powershell
cd sme_python
pip install -e .
```

## Fichiers de données

| Fichier | Emplacement conseillé | Variable optionnelle |
|---------|------------------------|----------------------|
| **smegend.mdb** | `sme_python/data/` | `SMEDB_PATH` |
| **resultat.xlsx** (maquette liste présents) | `data/` ou dossier **Impression** | `SME_TEMPLATE_PATH` |
| **numerique.xlsx** (situation numérique) | `data/templates/` ou **Impression** | `SME_NUMERIQUE_TEMPLATE_PATH` |
| **Bundle SQLite** (option) | `data/liste_generale.sqlite` (ou autre) | `SME_SQLITE_LISTE_PATH`, `SME_USE_SQLITE_LISTE` |

En **mode SQLite**, le fichier `.sqlite` regroupe la liste générale, les référentiels (structures, grades, subdivisions), `TabSme` et les tables satellites (mission, malade, …). Utilisez *Fichier → Chemins…* puis **Importer Access → SQLite (bundle complet)**. Un redémarrage est nécessaire après changement des chemins.

Ordre **base** : `SMEDB_PATH` → fichier enregistré dans le menu **Fichier → Chemins des fichiers de données…** (`%LOCALAPPDATA%\SME_Gendarmerie\paths.json`) → `data/smegend.mdb` → `Impression/smegend.mdb`.

Ordre **maquette Excel** : `SME_TEMPLATE_PATH` → même fichier `paths.json` → `data/resultat.xlsx` → `Impression/resultat.xlsx`.

Ordre **gabarit situation numérique** : `SME_NUMERIQUE_TEMPLATE_PATH` → `paths.json` → `data/templates/numerique.xlsx` → `Impression/numerique.xlsx` (ou `numérique.xlsx`).

## Lancer

```powershell
python -m sme_gend
```

ou `sme-gend` si le dossier Scripts est dans le `PATH`.

## Fonctionnalités

- **Tableau de bord** : indicateurs, graphiques, chemins base / maquette / mise en page
- **Liste générale** : grille, filtres, présents seulement
- **Mise en page liste** : ordre des **sections** (glisser-déposer), titres personnalisés, option *regroupement par subdivision* — fichier `data/liste_present_layout.json`
- **Structures** : `TabStructure`, couples (structure ↔ SubArme) liste générale, hiérarchie Secun→Secdeux→Sectrois ; **Grades**, **Fonctions**
- **Liste des présents** : aperçu HTML + export Excel **fidèle à la maquette** (styles copiés depuis `resultat.xlsx`, feuille `Liste_SME`)
- **Situation numérique** (menu latéral dédié) : aperçus HTML **Matrice** et **Indispo par structure** (données Access), export Excel complet (`numerique.xlsx`)
- **Clôture mensuelle SME (Piste A)** : sur l’accueil, *Clôturer ce mois et passer au suivant* enregistre des copies dans `%LOCALAPPDATA%\SME_Gendarmerie\sme_archives\` (fichier SQLite bundle et `liste_present_layout.json` si présents), met à jour **TabSme** (Fermé / Ouvert), `sme_context.json` et une ligne d’audit `sme_month_closure_piste_a`.

Voir **`docs/PLAN_SME.md`** pour la feuille de route.

## Tests (développement)

```powershell
pip install -e ".[dev]"
python -m pytest tests/
```

## Structure du projet

| Élément | Rôle |
|--------|------|
| `data/` | `smegend.mdb`, `resultat.xlsx` (optionnel) |
| `docs/PLAN_SME.md` | Plan produit / technique |
| `src/sme_gend/config/` | Paramètres |
| `src/sme_gend/repositories/` | Accès ODBC |
| `src/sme_gend/services/` | Métier + export liste présents |
| `src/sme_gend/ui/` | `ShellWindow` (sidebar), vues dans `ui/views/` |
