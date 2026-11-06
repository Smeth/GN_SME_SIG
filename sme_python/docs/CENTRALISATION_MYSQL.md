# Centralisation MySQL — état des lieux

**Convergence SoT :** voir [`CHECKLIST_CONVERGENCE_SOT.md`](CHECKLIST_CONVERGENCE_SOT.md) (priorisation P0→P3, métriques SQL, flux édition → export).

## Données métier (100 % MySQL)

- Personnel, carrière, périodes SME, référentiels, numérique, gains/pertes
- Configuration partagée : `app_config` (layout présents, détachés, limite d'âge, soit transmis, workspace SME, auth)
- Journal : `audit_journal` (modifications personnel, activité, génération documents)

## Fichiers locaux conservés (par poste)

| Fichier | Rôle |
|---------|------|
| `paths.json` | Connexion MySQL, chemin Access **import**, gabarits Excel |
| `sme_ui_prefs.json` | Thème, colonnes masquées (UI locale) |
| `view_state.json` | Miroir des filtres UI (source = MySQL `ui_view_state`) |

## Access (.mdb)

Uniquement pour **importer** des archives mensuelles vers MySQL.

## Sauvegarde

- **Sauvegarde rapide** : `%LocalAppData%\SME_Gendarmerie\backups\`
- Contenu ZIP : dump SQL + `app_config` + config locale
- Rétention automatique : 15 archives (sauvegarde rapide)

## Variables d'environnement

- `SME_AUDIT_JSON_MIRROR=1` : réactive l'écriture des fichiers `.jsonl` en plus de MySQL

## Migration au démarrage

`ensure_central_config_ready()` importe les anciens JSON de `data/` et `view_state.json` vers MySQL (une fois).
