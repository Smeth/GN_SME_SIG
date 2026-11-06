# Algorithme complet — Génération de la liste des présents

> **Document de référence** pour implémenter la génération automatique de la liste des présents
> (structures, subdivisions, personnel) à partir de la base Access `smegend.mdb`.
>
> Source : projet `DATABASE` — scripts Python `generate_present_mai.py`, `sync_structures_ordre.py`,
> `parse_structures_ordre.py`, `rg_template.py`, `insert_rg_commandement.py`.

---

## 1. Vue d'ensemble

La génération repose sur **deux phases** enchaînées :

```
smegend.mdb  ──┐
               ├──►  Phase 1 : structures_ordre.txt  ──►  Phase 2 : liste Excel (.xlsx)
present.xlsx ──┘
```

| Phase | Script source | Entrées | Sortie |
|-------|---------------|---------|--------|
| **1** | `sync_structures_ordre.py` | MDB + `present.xlsx` (template) | `structures_ordre.txt` |
| **2** | `generate_present_mai.py` | MDB + `structures_ordre.txt` | `present_mai.xlsx` |

**Commandes d'exécution actuelles :**

```bash
python sync_structures_ordre.py    # Reconstruire l'ordre structures/subdivisions
python generate_present_mai.py     # Générer la liste Excel
python generate_officiel.py        # Version officielle dans OFFICIEL/
```

---

## 2. Sources de données

### 2.1 Base Access `smegend.mdb`

**Table `ListeGenerale`** — personnel :

| Champ | Colonne Excel | Description |
|-------|---------------|-------------|
| `Id` | — | Identifiant unique (clé primaire) |
| `noms` | C — Noms et prénoms | |
| `DateN` | D — Date de naissance | Format `dd/mm/yyyy` à l'export |
| `LieuN` | E — Lieu de naissance | |
| `Grade` | F — Grade | |
| `DateGrade` | G — Date nomination grade | |
| `DateService` | H — Date entrée en service | |
| `Ndecision` | I — N° décision engagement | |
| `Matmilitaire` | J — Matricule militaire | `"-"` si vide |
| `Matsolde` | K — Matricule solde | |
| `Padmin` | L — Position administrative | Filtrer sur `'Présent'` (avec accent) |
| `Fonction` | M — Fonction | |
| `SubArme` | N — Subdivision d'arme | |
| `Diplome` | O — Diplôme | |
| `DateObtention` | P — Date et lieu d'obtention | |
| `Structure` | Q — Structure (code) | Ex: `COMGEND`, `RG-BZV` |
| `Obs` | R — Observations | |
| `OderTri` | — | Ordre de tri interne | |
| `presseance` | — | Préséance (tri secondaire) | |

**Requête principale — personnel présent :**

```sql
SELECT Id, noms, DateN, LieuN, Grade, DateGrade, DateService, Ndecision,
       Matmilitaire, Matsolde, Padmin, Fonction, SubArme, Diplome,
       DateObtention, Structure, Obs, OderTri, presseance
FROM ListeGenerale
WHERE Padmin = 'Présent'
ORDER BY OderTri, presseance, noms
```

**Table `TabStructure`** — structures :

```sql
SELECT code, designation, OderTri FROM TabStructure ORDER BY OderTri
```

**Requête effectifs par subdivision :**

```sql
SELECT Structure, SubArme, COUNT(*)
FROM ListeGenerale
WHERE Padmin = 'Présent'
GROUP BY Structure, SubArme
```

### 2.2 Fichier template `present.xlsx`

- Feuille : `Liste`
- Sert de **référence d'ordre** pour les structures globales et les subdivisions des régions (`RG-XXX`)
- Colonne C (3) = noms, colonne N (14) = SubArme, colonne Q (17) = Structure
- Les en-têtes structure/subdivision sont des **cellules fusionnées** en colonne A

### 2.3 Fichier d'ordre `structures_ordre.txt`

Fichier texte UTF-8 décrivant l'ordre exact des structures et subdivisions.
Généré par la Phase 1, consommé par la Phase 2.

---

## 3. Phase 1 — Construction de `structures_ordre.txt`

### 3.1 Ordre global des structures

1. Extraire l'ordre depuis `present.xlsx` : parcourir les lignes ≥ 4, colonne Q (Structure), garder la **première occurrence** de chaque code.
2. Ajouter les codes présents dans `structures_ordre.txt` actuel mais absents du template.
3. Ajouter les codes trouvés en MDB mais absents des deux sources précédentes.

**Algorithme :**

```
global_order = []
seen = set()

pour chaque ligne r >= 4 de present.xlsx avec un nom (col C):
    code = valeur colonne Q (Structure), trim
    si code non vide et code not in seen:
        seen.add(code)
        global_order.append(code)

pour chaque structure dans structures_ordre.txt actuel:
    si structure.code not in global_order:
        global_order.append(structure.code)

pour chaque code dans MDB (Structure distinctes):
    si code not in global_order:
        global_order.append(code)
```

### 3.2 Ordre des subdivisions — structures normales (hors RG)

Pour chaque structure **ne commençant pas par `RG-`** :

```
user_subs = subdivisions listées dans structures_ordre.txt actuel (si existant)
mdb_subs  = subdivisions trouvées en MDB pour ce code

si user_subs non vide:
    subs = user_subs + [s for s in mdb_subs if s not in user_subs]
sinon:
    subs = mdb_subs
```

### 3.3 Ordre des subdivisions — Régions (RG-XXX)

Pour chaque structure `RG-XXX` :

1. Extraire l'ordre template depuis `present.xlsx` (voir § 3.4)
2. Fusionner avec subdivisions MDB :

```
function merged_rg_subdivisions(code, template_subs, mdb_subs):
    result = copie(template_subs)
    covered = set()

    pour chaque ts in template_subs:
        pour chaque ms in mdb_subs:
            si template_sub_matches_mdb(code, ts, ms):
                covered.add(ms)

    pour chaque ms in mdb_subs:
        si ms not in covered:
            result.append(ms)   // nouvelles subdivisions MDB en fin de bloc

    return result
```

### 3.4 Extraction ordre régions depuis `present.xlsx`

```
rg_order = []
rg_data = {}   // code -> { designation, subs[], sub_keys set }
current_rg = null
pending_headers = []

pour r de 4 à max_row:
    si ligne r est un en-tête fusionné (col A):
        title = valeur
        si "REGION" in title AND "GENDARMERIE" in title AND len(title) > 20:
            // C'est un en-tête de région
            code = premier code RG-XXX trouvé dans les 50 lignes suivantes (col Q)
            si code not in rg_data:
                rg_order.append(code)
                rg_data[code] = { designation: title, subs: [], sub_keys: {} }
            current_rg = code
            pending_headers = []
        sinon si current_rg:
            pending_headers.append(title)

    sinon si ligne r a un nom (col C) et current_rg:
        code = col Q, sub = col N (SubArme)
        si code ne commence pas par RG-: continuer
        label = sub si sub non vide sinon pending_headers[-1]
        si label not in rg_data[current_rg].sub_keys:
            rg_data[current_rg].sub_keys.add(label)
            rg_data[current_rg].subs.append(label ou "(sans subdivision)")
        pending_headers = []
```

### 3.5 Correspondance subdivisions MDB ↔ template (régions)

Fonction centrale : **`template_sub_matches_mdb(code, template_sub, mdb_sub)`**

```
function normalize(text):
    text = uppercase, trim
    remplacer accents : É→E, È→E, Ê→E, À→A, Ô→O
    compresser espaces multiples

function template_sub_matches_mdb(code, template_sub, mdb_sub):
    si template_sub vide: return mdb_sub vide
    si mdb_sub vide: return false

    t = normalize(template_sub)
    m = normalize(mdb_sub)

    // 1. Égalité exacte
    si t == m: return true

    // 2. Alias explicites
    si (code, template_sub) in ALIASES:
        si normalize(ALIASES[(code, template_sub)]) == m: return true

    // 3. Variantes orthographiques MDB
    pour extra in EXTRA_MDB.get((code, template_sub), []):
        si normalize(extra) == m: return true

    // 4. Retrait suffixe code région
    base = strip_code_suffix(template_sub, code)  // retire "RG-XXX" du libellé
    si base non vide et base == m: return true

    // 5. RÈGLE ANTI-CROISEMENT compagnie ↔ brigade
    si is_brigade_recherche(t) != is_brigade_recherche(m): return false
    si is_compagnie_territoriale(t) et is_brigade_recherche(m): return false
    si is_brigade_recherche(t) et is_compagnie_territoriale(m): return false

    // 6. Inclusion partielle
    si m in t ou t in m: return true

    return false

function is_brigade_recherche(label):
    return "BRIGADE DE RECHERCHE" in label OR "BRIGADE DE RECHERCHES" in label

function is_compagnie_territoriale(label):
    return label.startswith("COMPAGNIE") AND "TERRITORIALE" in label

function strip_code_suffix(template_sub, code):
    t = normalize(template_sub)
    c = normalize(code)
    si t.endswith(c): return t[:-len(c)].strip()
    t = t.replace(c, "").strip()
    t = t.replace(c.replace("RG-", ""), "").strip()
    return t
```

### 3.6 Table des alias (extrait — voir `rg_template.py` pour la liste complète)

Les alias mappent un libellé **template** vers un libellé **MDB** :

| Code | Template (present.xlsx) | MDB (SubArme) |
|------|-------------------------|---------------|
| RG-BZV | COMMANDEMENT RG-BZV | COMMANDEMENT |
| RG-BZV | ETAT-MAJOR RG-BZV | ETAT-MAJOR |
| RG-BZV | ONU | DETACHEMENT ONU |
| RG-BZV | SECTION DE RECHERCHES JUDICIAIRE RG-BZV | SECTION DE RECHERCHES JUDICIAIRES |
| RG-BZV | BRIGADE TERRITORIALE DE MBAMOU | BRIGADE TERRITORIALE MBAMOU |
| RG-BZV | PELOTON ... BZV-1 | PELOTON ... BRAZZAVILLE 1 |
| RG-BZV | PELOTON ... BZV-2 | PELOTON ... BRAZZAVILLE 2 |
| RG-PNR | DET. CA-PNR | DETACHEMENT COUR D'APPEL DE POINTE-NOIRE |
| RG-PNR | TRIBUNAL DE GRANDE INSTANCE DE POINTE NOIRE | DETACHEMENT TRIBUNAL DE GRANDE INSTANCE DE POINTE NOIRE |
| RG-POO | ESSIG RG-POO | ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE |
| RG-LEK | ESCADRON DE GENDARMERIE MOBILE RG-LEK | ESCADRON DE GENDARMERIE MOBILE |
| RG-CVO | PSIG KELLE | PELOTON DE SURVEILLANCE ET D'INTERVENTION DE KELLE |
| *(toutes RG)* | COMMANDEMENT RG-XXX | COMMANDEMENT |
| *(toutes RG)* | ETAT-MAJOR RG-XXX | ETAT-MAJOR |
| *(toutes RG)* | SECTION DE RECHERCHES JUDICIAIRES RG-XXX | SECTION DE RECHERCHES JUDICIAIRES |
| *(toutes RG)* | ESSIG RG-XXX | ESCADRON SPECIALISE DE SECURITE ET D'INTERVENTION DE LA GENDARMERIE |

**Variantes orthographiques MDB (`EXTRA_MDB`) :**

| Code | Template | Variante MDB acceptée |
|------|----------|----------------------|
| RG-BZV | PELOTON ... KINTELE | PELOTON DE SURVEILLENCE ET D'INTERVENTION ... KINTELE |

> **Important :** porter la table `ALIASES` complète (98 entrées) depuis `rg_template.py`.

### 3.7 Format du fichier `structures_ordre.txt`

```
# ==============================================================================
# ORDRE DES STRUCTURES ET SUBDIVISIONS
# Source : smegend.mdb + ordre regions aligne sur present.xlsx
# ==============================================================================
#
# RESUME
#   Nombre de structures    : 42
#   Nombre de subdivisions  : 440
#   Personnel present       : 3726
#
# ORDRE GLOBAL : present.xlsx (structures + regions intercalees avec GGM)
# SUBDIVISIONS RG-XXX : ordre present.xlsx + nouvelles subdivisions MDB en fin de bloc
#
# FORMAT (1 bloc = 1 structure, sans repetition)
#   [STR NNN/TTT] CODE | DESIGNATION
#   Effectif structure : XXX
#   Subdivisions (N) :
#     [NNN.SS] Nom de la subdivision  (effectif: XX)
#
# ==============================================================================

# ------------------------------------------------------------------------------
[STR 001/042] COMGEND | COMMANDEMENT DE LA GENDARMERIE
Effectif structure : 10
Subdivisions (2) :
  [001.01] Commandement Gendarmerie  (effectif: 2)
  [001.02] Direction  (effectif: 8)

# ------------------------------------------------------------------------------
[STR 002/042] CAB-COMGEND | CABINET DU COMMANDANT DE LA GENDARMERIE
Effectif structure : 89
Subdivisions (9) :
  [002.01] CONSEILLER  (effectif: 3)
  ...
```

**Règles de parsing (`parse_structures_ordre`) :**

- Ignorer lignes vides et lignes commençant par `#`
- Structure : regex `^\[STR\s+\d+/\d+\]\s+(.+)$` → split `"CODE | DESIGNATION"` sur `" | "`
- Subdivision : regex `^\s*\[([\d.]+)\]\s+(.+?)\s+\(effectif:\s*\d+\)\s*$`
- Ignorer subdivisions avec id se terminant par `--` (aucune subdivision avec personnel)
- `(sans subdivision)` → label vide `""`

---

## 4. Phase 2 — Génération de la liste Excel

### 4.1 Point d'entrée

```
function generate_from_ordre(output_path, use_template=false):
    structures = parse_structures_ordre("structures_ordre.txt")
    struct_info, personnel = load_mdb_data()
    groups = group_personnel(personnel)

    si use_template:
        charger present.xlsx, vider données à partir ligne 4
    sinon:
        créer classeur neuf

    setup_sheet_header(ws)
    global_num, missing = populate_liste(ws, structures, groups, struct_info)
    setup_print_layout(ws)
    sauvegarder(output_path)
    return saved_path
```

### 4.2 Groupement du personnel

```
function group_personnel(personnel):
    groups = defaultdict(list)
    pour chaque personne p:
        code = p.Structure.strip()
        sub  = p.SubArme.strip()
        groups[(code, sub)].append(p)
    return groups
```

### 4.3 Clé unique d'une personne

Empêche les doublons dans la liste générée :

```
function person_key(person):
    si person.Id non null et non vide: return "id:" + person.Id
    si person.Matsolde non vide:       return "mat:" + person.Matsolde
    return "nom:" + person.noms.strip()
```

### 4.4 Algorithme principal `populate_liste()`

```
function populate_liste(ws, structures, groups, struct_info):
    row = 4
    global_num = 0
    assigned_keys = set()
    missing = []

    // --- BOUCLE PRINCIPALE : structures dans l'ordre ---
    pour chaque structure in structures:
        code = structure.code
        designation = structure.designation
        subs = structure.subdivisions

        si subs vide: continuer

        write_structure_header(ws, row, designation)
        row += 1

        // CAS SPÉCIAL : CS-DPF — fusionner toutes subdivisions
        si code in NO_SUBDIVISION_STRUCTURES:  // {"CS-DPF"}
            people = []
            pour chaque sub in subs:
                pour chaque p in find_people(groups, code, sub):
                    si person_key(p) not in assigned_keys:
                        people.append(p)
            people.sort(key=grade_sort_key)
            pour i, person in enumerate(people):
                assigned_keys.add(person_key(person))
                global_num += 1
                write_person_row(ws, row, global_num, i+1, person)
                row += 1
            continuer

        // CAS NORMAL : une subdivision à la fois
        pour chaque sub in subs:
            sub_label = sub si sub else "(sans subdivision)"
            write_subdivision_header(ws, row, sub_label)
            row += 1

            // Régions : insérer "COMMANDEMENT" sous les compagnies territoriales
            si code.startswith("RG-") et is_rg_compagnie(sub):
                write_subdivision_header(ws, row, "COMMANDEMENT")
                row += 1

            people = [p for p in find_people(groups, code, sub)
                      if person_key(p) not in assigned_keys]

            // Tri par fonction si override défini
            fonction_order = FONCTION_ORDER_OVERRIDES.get((code, sub))
            si fonction_order:
                people.sort(key=fonction_sort_key(fonction_order))
            // sinon : ordre MDB (OderTri, presseance, noms) — déjà trié à la requête

            pour i, person in enumerate(people):
                assigned_keys.add(person_key(person))
                global_num += 1
                write_person_row(ws, row, global_num, i+1, person)
                row += 1

    // --- FIN DE FICHIER : personnel non prévu dans structures_ordre ---
    pour (code, sub), people in sorted(groups.items()):
        remaining = [p for p in people if person_key(p) not in assigned_keys]
        si remaining vide: continuer

        missing.append((code, sub, len(remaining)))
        designation = struct_info.get(code, (code, 0))[0]
        write_structure_header(ws, row, designation)
        row += 1
        write_subdivision_header(ws, row, sub ou "(sans subdivision)")
        row += 1
        pour i, person in enumerate(remaining):
            assigned_keys.add(person_key(person))
            global_num += 1
            write_person_row(ws, row, global_num, i+1, person)
            row += 1

    return global_num, missing
```

### 4.5 Recherche de personnes `find_people(code, sub)`

```
function find_people(groups, code, sub):
    // Régions : matching flou via alias
    si code.startswith("RG-"):
        result = []
        pour (c, mdb_sub), people in groups.items():
            si c == code et template_sub_matches_mdb(code, sub, mdb_sub):
                result.extend(people)
        return result

    // Structures normales : matching exact normalisé
    sub_norm = normalize(sub)
    pour (c, s), people in groups.items():
        si c == code et normalize(s) == sub_norm:
            return people
    return []
```

### 4.6 Règles de tri du personnel

**Ordre des grades (`GRADE_ORDER`) :**

```
GDB, COL, LCL, CDT, CNE, LTN, SLT,
ADC, ADJ, SCH, MDC, SGT, MDL, SDT,
ADM, CLM, CIV
```

```
function grade_sort_key(person):
    grade = person.Grade.strip().upper()
    rank = index de grade dans GRADE_ORDER, ou len(GRADE_ORDER) si inconnu
    return (rank, person.noms.strip().upper())
```

**Tri par fonction — override pour `COMGEND / Direction` :**

```
FONCTION_ORDER_OVERRIDES = {
    ("COMGEND", "Direction"): [
        "INSPECTEUR IT",
        "DIRECTEUR DOE",
        "DIRECTEUR DPF",
        "DIRECTEUR DSF",
        "DIRECTEUR DTRJD",
        "DIRECTEUR DIRTRANS",
        "DIRECTEUR DIRMAT",
        "DIRECTEUR DDT",
    ]
}
```

```
function fonction_sort_key(order):
    rank = { f.upper(): i for i, f in enumerate(order) }
    return lambda person: (
        rank.get(person.Fonction.strip().upper(), len(order)),
        person.noms.strip().upper()
    )
```

**Détection compagnie territoriale RG (`is_rg_compagnie`) :**

```
function is_rg_compagnie(label):
    u = label.upper().strip()
    si not u.startswith("COMPAGNIE"): return false
    si "BRIGADE DE RECHERCHE" in u ou "BRIGADE DE RECHERCHES" in u: return false
    return true
```

Quand `is_rg_compagnie(sub)` est vrai, insérer un en-tête **"COMMANDEMENT"** entre l'en-tête de la compagnie et les lignes de personnel.

### 4.7 Constantes spéciales

```
NO_SUBDIVISION_STRUCTURES = {"CS-DPF"}
// CS-DPF : toutes subdivisions fusionnées en une seule liste, triée par grade
```

---

## 5. Structure du fichier Excel généré

### 5.1 Mise en page

| Élément | Valeur |
|---------|--------|
| Feuille | `Liste` |
| Ligne 1 | Titre fusionné A1:R1 — **LISTE DES PRESENTS** |
| Ligne 3 | 18 en-têtes de colonnes |
| Ligne 4+ | Données (structure → subdivision → personnel) |
| Orientation | Paysage A4 |
| Colonnes | 18, ajustées sur la largeur de page (`fitToWidth = 1`) |
| Marges | gauche/droite 0,25", haut/bas 0,4" |

### 5.2 Les 18 colonnes

| Col | En-tête | Source MDB |
|-----|---------|------------|
| A | N° | Numéro global séquentiel (1, 2, 3...) |
| B | N° interne | Numéro local dans la subdivision (1, 2, 3...) |
| C | Noms et prénoms | `noms` |
| D | Date de naissance | `DateN` → format `dd/mm/yyyy` |
| E | Lieu de naissance | `LieuN` |
| F | Grade | `Grade` |
| G | Date de nomination au grade | `DateGrade` → format `dd/mm/yyyy` |
| H | Date d'entrée en service | `DateService` |
| I | N° decision d'engagement | `Ndecision` |
| J | Matricule militaire | `Matmilitaire` ou `"-"` si vide |
| K | Matricule solde | `Matsolde` |
| L | Position administrative | `Padmin` |
| M | Fonction | `Fonction` |
| N | Subdivision d'arme | `SubArme` |
| O | Diplome | `Diplome` |
| P | Date et lieu d'obtention | `DateObtention` |
| Q | Structure | `Structure` |
| R | Obs | `Obs` |

### 5.3 Styles visuels

| Élément | Police | Taille | Gras | Fond | Alignement |
|---------|--------|--------|------|------|------------|
| Titre | Tahoma | 14 | oui | aucun | centre |
| En-têtes colonnes | Tahoma | 7 | oui | `#CCFFCC` (vert) | centre |
| En-tête structure | Tahoma | 9 | oui | `#D9D9D9` (gris) | centre, fusionné A:R |
| En-tête subdivision | Tahoma | 8 | oui | `#E8E8E8` (gris clair) | centre, fusionné A:R |
| Données | Tahoma | 7 | non | aucun | centre, bordure fine |

**Hauteurs de lignes :** titre 18, en-têtes colonnes 42, structure 22, subdivision 14, données 18.

**Largeurs colonnes :**

```
A:4.5  B:4.5  C:19   D:7.5  E:9.5  F:5    G:7.5  H:7.5  I:12.5
J:7.5  K:6    L:7    M:10.5 N:10.5 O:6.5  P:6.5  Q:7.5  R:4.5
```

### 5.4 Hiérarchie visuelle des lignes

```
┌─────────────────────────────────────────────────────────┐
│ LISTE DES PRESENTS                          (ligne 1)   │
├─────────────────────────────────────────────────────────┤
│ N° | N° interne | Noms | ... | Obs           (ligne 3)  │
├─────────────────────────────────────────────────────────┤
│ COMMANDEMENT DE LA GENDARMERIE              (structure) │
├─────────────────────────────────────────────────────────┤
│ Commandement Gendarmerie                    (subdivision)│
├────┬────┬──────────────┬─────┬─────┬ ... ─┬─────┬───────┤
│ 1  │ 1  │ DUPONT Jean  │ ... │ GDB │      │COMG │       │
│ 2  │ 2  │ MARTIN Paul  │ ... │ COL │      │COMG │       │
├─────────────────────────────────────────────────────────┤
│ Direction                                   (subdivision)│
├────┬────┬──────────────┬─────┬─────┬ ... ─┬─────┬───────┤
│ 3  │ 1  │ ...          │     │     │      │     │       │
└─────────────────────────────────────────────────────────┘
```

Pour les **régions**, sous une compagnie territoriale :

```
│ COMPAGNIE TERRITORIALE DE ...               (subdivision)│
│ COMMANDEMENT                                (sub-entête)  │
│ 1  │ 1  │ ...                                              │
```

---

## 6. Pseudocode complet (intégration)

```python
# ============================================================
# ALGORITHME COMPLET — LISTE DES PRÉSENTS
# À porter dans l'application cible
# ============================================================

# --- CONSTANTES ---
NO_SUBDIVISION_STRUCTURES = {"CS-DPF"}

GRADE_ORDER = [
    "GDB", "COL", "LCL", "CDT", "CNE", "LTN", "SLT",
    "ADC", "ADJ", "SCH", "MDC", "SGT", "MDL", "SDT",
    "ADM", "CLM", "CIV",
]

FONCTION_ORDER_OVERRIDES = {
    ("COMGEND", "Direction"): [
        "INSPECTEUR IT", "DIRECTEUR DOE", "DIRECTEUR DPF",
        "DIRECTEUR DSF", "DIRECTEUR DTRJD", "DIRECTEUR DIRTRANS",
        "DIRECTEUR DIRMAT", "DIRECTEUR DDT",
    ],
}

# ALIASES : dict[(code_structure, libellé_template)] -> libellé_mdb
# EXTRA_MDB : dict[(code_structure, libellé_template)] -> [variantes_mdb]
# → Copier intégralement depuis rg_template.py (98 alias + 1 extra)


def generer_liste_presents(mdb_path, ordre_path, output_path):
    """Génère la liste Excel des présents."""

    # ÉTAPE 1 : Parser l'ordre structures/subdivisions
    structures = parse_structures_ordre(ordre_path)

    # ÉTAPE 2 : Charger personnel et structures depuis MDB
    personnel = db_query(mdb_path, """
        SELECT Id, noms, DateN, LieuN, Grade, DateGrade, DateService,
               Ndecision, Matmilitaire, Matsolde, Padmin, Fonction,
               SubArme, Diplome, DateObtention, Structure, Obs,
               OderTri, presseance
        FROM ListeGenerale
        WHERE Padmin = 'Présent'
        ORDER BY OderTri, presseance, noms
    """)
    struct_info = db_query(mdb_path,
        "SELECT code, designation, OderTri FROM TabStructure ORDER BY OderTri")

    # ÉTAPE 3 : Grouper par (Structure, SubArme)
    groups = defaultdict(list)
    for p in personnel:
        groups[(p["Structure"].strip(), p["SubArme"].strip())].append(p)

    # ÉTAPE 4 : Créer classeur Excel avec en-têtes
    wb, ws = create_workbook_with_headers()

    # ÉTAPE 5 : Remplir la liste
    row = 4
    global_num = 0
    assigned = set()
    missing = []

    for structure in structures:
        code = structure["code"]
        subs = structure["subdivisions"]
        if not subs:
            continue

        write_structure_header(ws, row, structure["designation"])
        row += 1

        # CS-DPF : fusion toutes subdivisions, tri par grade
        if code in NO_SUBDIVISION_STRUCTURES:
            people = []
            for sub in subs:
                for p in find_people(groups, code, sub):
                    if person_key(p) not in assigned:
                        people.append(p)
            people.sort(key=grade_sort_key)
            for i, p in enumerate(people):
                assigned.add(person_key(p))
                global_num += 1
                write_person_row(ws, row, global_num, i + 1, p)
                row += 1
            continue

        for sub in subs:
            label = sub if sub else "(sans subdivision)"
            write_subdivision_header(ws, row, label)
            row += 1

            if code.startswith("RG-") and is_rg_compagnie(sub):
                write_subdivision_header(ws, row, "COMMANDEMENT")
                row += 1

            people = [p for p in find_people(groups, code, sub)
                      if person_key(p) not in assigned]

            func_order = FONCTION_ORDER_OVERRIDES.get((code, sub))
            if func_order:
                people.sort(key=fonction_sort_key(func_order))

            for i, p in enumerate(people):
                assigned.add(person_key(p))
                global_num += 1
                write_person_row(ws, row, global_num, i + 1, p)
                row += 1

    # ÉTAPE 6 : Personnel non prévu dans l'ordre → fin de fichier
    for (code, sub), people in sorted(groups.items()):
        remaining = [p for p in people if person_key(p) not in assigned]
        if not remaining:
            continue
        missing.append((code, sub, len(remaining)))
        desig = struct_info.get(code, (code, 0))[0]
        write_structure_header(ws, row, desig)
        row += 1
        write_subdivision_header(ws, row, sub or "(sans subdivision)")
        row += 1
        for i, p in enumerate(remaining):
            assigned.add(person_key(p))
            global_num += 1
            write_person_row(ws, row, global_num, i + 1, p)
            row += 1

    # ÉTAPE 7 : Finaliser et sauvegarder
    setup_print_layout(ws)
    wb.save(output_path)

    # Vérification
    assert global_num == len(personnel), \
        f"Écart : {len(personnel) - global_num} fiche(s) non exportée(s)"

    return {"lignes": global_num, "personnel": len(personnel), "missing": missing}


def reconstruire_ordre(mdb_path, template_path, output_txt_path):
    """Phase 1 : reconstruit structures_ordre.txt."""

    counts = db_query(mdb_path, """
        SELECT Structure, SubArme, COUNT(*)
        FROM ListeGenerale WHERE Padmin='Présent'
        GROUP BY Structure, SubArme
    """)
    designations = db_query(mdb_path,
        "SELECT code, designation FROM TabStructure")

    global_order = extract_global_order_from_template(template_path)
    rg_data = extract_rg_from_template(template_path)

    # Compléter l'ordre global
    by_code_mdb = group_subdivisions_by_structure(counts)
    for code in by_code_mdb:
        if code not in global_order:
            global_order.append(code)

    structures_out = []
    for code in global_order:
        if code.startswith("RG-"):
            rg = rg_data["regions"].get(code, {})
            desig = rg.get("designation") or designations.get(code, code)
            tpl_subs = rg.get("subs", [])
            mdb_subs = by_code_mdb.get(code, [])
            subs = merged_rg_subdivisions(code, tpl_subs, mdb_subs)
        else:
            desig = designations.get(code, code)
            mdb_subs = by_code_mdb.get(code, [])
            subs = mdb_subs

        structures_out.append({"code": code, "designation": desig, "subdivisions": subs})

    write_structures_ordre_txt(output_txt_path, structures_out, counts)
    return structures_out
```

---

## 7. Fichiers sources du projet

| Fichier | Rôle | Priorité |
|---------|------|----------|
| `generate_present_mai.py` | **Cœur** — génération Excel, tri, styles, mise en page | Essentiel |
| `parse_structures_ordre.py` | Parser `structures_ordre.txt` → liste de structures | Essentiel |
| `rg_template.py` | Correspondance subdivisions RG, alias, extraction template | Essentiel |
| `insert_rg_commandement.py` | Détection compagnies territoriales RG | Essentiel |
| `sync_structures_ordre.py` | Reconstruction complète de `structures_ordre.txt` | Phase 1 |
| `structures_ordre.txt` | Fichier d'ordre (42 structures, ~440 subdivisions) | Données |
| `present.xlsx` | Template de référence pour l'ordre | Données |
| `rg_template_order.json` | Cache JSON ordre régions extrait du template | Cache |
| `append_missing_subdivisions.py` | Ajout chirurgical de nouvelles subdivisions | Maintenance |
| `build_structures_ordre.py` | Variante Phase 1 basée sur TabStructure.OderTri | Alternative |

---

## 8. Points d'attention pour l'implémentation

1. **Encodage SQL** : le filtre est `Padmin = 'Présent'` avec accent sur le « é ».
2. **Anti-doublons** : toujours utiliser `person_key()` + ensemble `assigned_keys`.
3. **Régions RG** : le matching flou (alias + inclusion partielle) est **indispensable** — sans la table `ALIASES`, de nombreuses personnes ne seront pas placées.
4. **CS-DPF** : cas spécial — fusionner toutes subdivisions, trier uniquement par grade.
5. **Compagnies RG** : insérer l'en-tête « COMMANDEMENT » (pas de personnel propre, c'est un séparateur visuel).
6. **Personnel manquant** : les personnes dont la subdivision n'est pas dans `structures_ordre.txt` sont ajoutées en **fin de fichier** ; l'outil `append_missing_subdivisions.py` permet de les intégrer proprement dans l'ordre.
7. **Dates** : formater en `dd/mm/yyyy` ; retourner chaîne vide si null.
8. **Matricule militaire** : afficher `"-"` si le champ est vide.
9. **Normalisation texte** : uppercase, suppression accents, compression espaces — utilisée partout pour les comparaisons.
10. **Ordre par défaut du personnel** : celui de la requête SQL (`OderTri, presseance, noms`) sauf overrides explicites (grade pour CS-DPF, fonction pour COMGEND/Direction).

---

## 9. Flux de maintenance

```
Nouvelle donnée en MDB
        │
        ▼
sync_structures_ordre.py  ──►  structures_ordre.txt mis à jour
        │
        ▼
generate_present_mai.py   ──►  present_mai.xlsx
        │
        ▼ (si subdivisions non prévues détectées)
append_missing_subdivisions.py  ──►  structures_ordre.txt corrigé
        │
        ▼
Regénérer present_mai.xlsx
```

**Vérification qualité** : `audit_complet.py` compare le résultat avec la liste officielle et signale les écarts.

---

## 10. Dépendances techniques (implémentation Python actuelle)

```
openpyxl    # Génération/manipulation Excel
pyodbc      # Connexion Microsoft Access (driver *.mdb, *.accdb)
```

Pour une autre technologie, remplacer par l'équivalent :
- **Excel** : toute librairie supportant styles, fusion cellules, mise en page (EPPlus, ClosedXML, Apache POI, etc.)
- **Access MDB** : ODBC, OLE DB, ou export préalable en SQLite/CSV

---

*Document généré le 20/08/2026 — Projet DATABASE, dossier Mai 2026.*
