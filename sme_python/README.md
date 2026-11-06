# S.G.I DPF (client Python)

**Système de Gestion de l'Information** — Direction du Personnel et de la Formation.

Application PySide6 : **liste générale**, **référentiels**, **liste des présents**, **cycle mensuel SME**, navigation par **menu latéral**.

## Prérequis

- Python 3.10+
- **MySQL** (base `smegend` ou équivalent) — source opérationnelle
- Pilote ODBC **Microsoft Access** — uniquement pour **import** depuis l’ancien SME (`.mdb` / `.accdb`)
- Dépendances : `PySide6`, `pymysql`, `pyodbc`, `openpyxl`, `python-docx`

## Installation

```powershell
cd sme_python
pip install -e .
```

## Arborescence

| Dossier | Contenu |
|---------|---------|
| **`modeles/`** | Gabarits Excel/Word (`resultat.xlsx`, `numerique.xlsx`, `DETACHES.xlsx`, …) |
| **`data/`** | Schémas SQL, config JSON de repli, journaux |
| **`data/schema/`** | Scripts SQL MySQL |
| **`src/sme_gend/`** | Code applicatif |

Voir `modeles/LISEZ-MOI.md` pour la liste des gabarits.

## Fichiers de données

| Fichier | Emplacement | Rôle |
|---------|-------------|------|
| **MySQL** | Paramètres applicatifs | Données opérationnelles (liste, carrière, référentiels, périodes SME) |
| **smegend.mdb** (optionnel) | `modeles/` ou `data/` | Import bundle Access → MySQL |
| **resultat.xlsx** | **`modeles/`** | Maquette export liste des présents |
| **numerique.xlsx** | **`modeles/`** | Situation numérique |

Ordre de résolution des chemins : variables d’environnement → `paths.json` → `modeles/<fichier>` → emplacements historiques.

## Lancer

```powershell
python -m sme_gend
```

ou `sme-gend` si le dossier Scripts est dans le `PATH`.

Build portable : `scripts\build_portable.cmd`

## Fonctionnalités principales

| Domaine | Description |
|---------|-------------|
| **Tableau de bord** | KPI effectifs (présents selon `ref_position_admin`) |
| **Pilotage du mois** | Alertes, checklist, clôture / réouverture, santé données ETL |
| **Liste générale** | Grille, filtres, dossier agent, sync dossier après édition |
| **Édition SME** | Saisie immédiate par cellule (mois ouvert) |
| **Liste des présents** | Aperçu HTML + export Excel (même périmètre que le référentiel padmin) |
| **Attestations** | Limite d’âge, soit transmis |
| **Référentiels** | Structures, grades, fonctions, positions admin |
| **Clôture** | `sme_periode` (Fermé/Ouvert), archives JSON mise en page, mois actif |

## Présents — règle métier

Une personne est **présente** si sa position administrative est marquée **« compte comme présent »** dans le référentiel (`ref_position_admin`).  
Si le libellé n’est pas dans le référentiel, repli : le mot **« présent »** dans le libellé (comportement historique Access).

## Tests

```powershell
pip install -e ".[dev]"
python -m pytest tests/
```

## Documentation technique

- `docs/CHECKLIST_CONVERGENCE_SOT.md` — convergence données (SoT)
- `docs/PLAN_SME.md` — historique produit et feuille de route
