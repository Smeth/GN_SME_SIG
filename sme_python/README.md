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

Ordre **base** : `SMEDB_PATH` → `data/smegend.mdb` → `Impression/smegend.mdb`.

Ordre **maquette Excel** : `SME_TEMPLATE_PATH` → `data/resultat.xlsx` → `Impression/resultat.xlsx`.

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

Voir **`docs/PLAN_SME.md`** pour la feuille de route.

## Structure du projet

| Élément | Rôle |
|--------|------|
| `data/` | `smegend.mdb`, `resultat.xlsx` (optionnel) |
| `docs/PLAN_SME.md` | Plan produit / technique |
| `src/sme_gend/config/` | Paramètres |
| `src/sme_gend/repositories/` | Accès ODBC |
| `src/sme_gend/services/` | Métier + export liste présents |
| `src/sme_gend/ui/` | `ShellWindow` (sidebar), vues dans `ui/views/` |
