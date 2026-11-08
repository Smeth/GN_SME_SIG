# Vérification du lot de migration — 8 novembre 2026

Version de développement : `0.2.0.dev1`. Branche : `migration/cycle-publications-2026-11-08`.

Les essais ont utilisé des profils synthétiques distincts et une instance MySQL jetable sur le port 33329. Les fixtures contrôlent le port et le dossier de données avant de créer leurs bases temporaires ; elles refusent le port de production. L’instance a été arrêtée après vérification de son dossier et de l’absence de transaction active.

## Scénarios MySQL vérifiés

| Domaine | Vérifications |
|---|---|
| Migrations | Plan sans écriture, sauvegarde préalable liée au serveur et à la base, contrats physiques, checksum, application idempotente, interruption et reprise explicite |
| Publications | Numérotation concurrente, brouillon/validation/remplacement, contrôle de version, fraîcheur des données, empreintes des fragments, récupération exacte depuis un autre profil, conservation des anciennes versions |
| Cycle mensuel | Validation requise, refus de document obsolète, confirmation de mise à jour des postes, préparation du mois suivant, refus d’écraser une archive, contrôle des droits et de la concurrence |
| Mouvements réels | Saisie de 2025 sans archive créée, GAIN/PERTE, Réf/note facultative, correction/suppression avec CAS, opposition le même jour refusée, rollback du fait et du journal si la projection échoue |
| Effectifs | Trois formats de mois historiques, liste/avancement/organigramme/exports cohérents, départ masqué sans destruction, retour à l’observation initiale, GAIN sans carrière source, import strict sans résurrection d’un GAIN annulé |
| Retours et diplômes | Corrections historiques tracées, date minimale d’invalidation, retours et positions cohérents, conservation des diplômes, champs ordinaires verrouillés dans les fiches historiques |
| Sauvegarde | SQL et configuration dans le même instantané malgré une écriture concurrente, inventaire des fichiers, altération détectée, noms Windows gérés |
| Restauration | Remplacement des mois choisis et remappage des identités, conservation des autres mois, refus de contradiction avec un fait corrigé/supprimé, rollback groupé, contrôle du registre et des projections avant bascule |
| Restauration partielle | Conservation des clés opérationnelles et de leur absence selon la collation MySQL, refus de restaurer isolément ledger/barrière/journal/file/registre, refus des mélanges incompatibles avec les faits datés |
| Rapports d’intervalle | Regroupement des observations identiques, valeurs contradictoires signalées, dates absentes conservées, distinction entre archive absente et mois observé vide, protection contre les formules dans les textes exportés |

Les lots ciblés natifs couvrent notamment `test_publication_registry_mysql`, `test_document_month_cycle_mysql`, `test_temporal_projection_migration_mysql`, `test_membership_scope_mysql`, `test_dated_effectif_movement_mysql`, `test_dated_month_backup_mysql`, `test_partial_restore_operational_state_mysql`, `test_backup_snapshot_consistency_mysql` et `test_period_activity_report_mysql`. Les essais historiques de restauration et de sauvegarde mensuelle ont aussi été rejoués.

## Performance synthétique

L’essai crée 6 000 agents, puis prépare douze mois successifs. Il vérifie l’empreinte inchangée du mois source et le nombre inchangé d’événements sources. Le nombre de requêtes est contrôlé pour éviter un traitement individuel par agent.

| Données | Douze projections | Temps par projection | Requêtes par projection |
|---|---:|---:|---:|
| 6 000 agents, promotion datée | 6,285 s | 0,442 à 0,603 s | 105 à 107 |
| 6 000 agents, 6 000 mouvements datés et promotion | 13,624 s | 0,778 à 1,971 s | 105 à 106 |

Ces mesures portent sur la projection SQL locale et excluent l’installation du schéma, le chargement des données synthétiques, le réseau et le rendu du classeur. Elles ne prédisent pas le temps exact sur le serveur et les quatre postes réels.

## Vérification des sources et limites

Le dernier passage global comprend **2 537 tests hors intégration réussis**, répartis en deux lots de 1 269 et 1 268 tests. Les rapports JUnit indiquent zéro échec, zéro erreur et zéro test ignoré. Il comprend des contrôles UI et de permissions. Les écarts de mocks révélés par les nouveaux verrous et les nouvelles gardes ont été corrigés dans leurs tests, sans désactiver les protections de production. Les avertissements restants concernent des API Qt et le moteur de lecture Excel historiques dépréciés.

L’analyse des sources préparées pour Git vérifie leur syntaxe et les formats de secrets connus. Les seules chaînes ressemblant à des identifiants détectées dans ce lot sont des constantes synthétiques de tests. Le fichier local d’authentification, les données réelles, les modèles remplis, les journaux privés et les sauvegardes ne font pas partie du commit public.

Les profils de test utilisent des ACL héritées pour leurs dossiers temporaires : ces essais ne certifient pas le durcissement Windows des postes de production. Aucun essai ne constitue une garantie absolue d’absence de bug. La recette sur une copie des données réelles et sur les quatre postes reste nécessaire avant activation. Aucun exécutable n’est généré dans ce lot ; aucune base de production n’est migrée.
