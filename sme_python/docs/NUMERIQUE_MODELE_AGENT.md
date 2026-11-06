# Situation numérique — modèle agent-centré (v2)

## Principes

| Entité | Règle |
|--------|--------|
| **Agent** | Identité durable (`agent`) |
| **Carrière** | Snapshot par période SME (`carriere_situation` + FK `ref_*`) |
| **Stages** | Plusieurs par agent, dates (`parcours_stage`, type LOCAL / EXTERIEUR) |
| **Missions** | Plusieurs par agent, dates (`agent_mission`) |
| **Maladie** | Plusieurs épisodes (`agent_malade`, `date_debut` / `date_fin`) |
| **Désertion** | Événement daté (`agent_desertion`) |
| **Mouvements d’effectif** | Par période SME (`mouvement_effectif`) : **GAIN** (entrée), **PERTE** (sortie, ex. **DECES**) |

## Tables (remplacement des `tab_*` satellites)

- `agent_mission` — remplace `tab_mission`
- `agent_malade` — remplace `tab_malade`
- `agent_deserteur` → `agent_desertion`
- `parcours_stage` — remplace `tab_stage_local` / `tab_stage_ex`
- `mouvement_effectif` — remplace `tab_gain_perte` (+ `categorie_mouvement`, `motif`)

Les `tab_*` référentiels (structure, grade, …) ne sont plus alimentés à l’import : passage direct Access → `ref_*` + `sme_periode`.

## Import Access

`mysql_bundle_import.copy_referentials_and_satellites_to_mysql` :

1. `import_referentials_from_access` → `ref_*`
2. `import_stages_from_access` → `parcours_stage`
3. Missions / malades / déserteurs → `agent_*` (import opérationnel complet)
4. `import_mouvements_from_access` → `mouvement_effectif`

## Scripts

```powershell
python scripts\backfill_mysql_ref_etl.py
python scripts\migrate_tab_to_agent_numerique.py
```
