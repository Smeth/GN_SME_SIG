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

## Révision de Sauvegarde et restauration

Le module est organisé en deux onglets : **Sauvegarde et restauration** et **Suppression et réinitialisation**. La sauvegarde/restauration partielle reste disponible dans des volets dépliables. Sur écran large, les actions de sauvegarde et de restauration occupent deux colonnes ; elles passent en une colonne sur un écran plus étroit. Les boutons de vidage mensuel et de réinitialisation sont visibles dès l’ouverture du second onglet.

Les droits de suppression sont uniformisés entre cette page, le menu Fichier et les Archives : administrateur avec `system.restore`, également vérifié dans les services. Une réinitialisation nécessite toujours les choix de conservation, la saisie exacte de la base cible et une sauvegarde préalable. Le vidage mensuel passe désormais sous maintenance avant une sauvegarde fraîche vérifiée, puis sa transaction de suppression. Le verrou de schéma précède la maintenance pour les deux actions.

Un mois dépendant de mouvements réellement datés ne peut pas être vidé indépendamment de ces faits globaux : l’opération est refusée avant sauvegarde et vérifiée à nouveau sous maintenance. Il faut corriger explicitement les faits concernés, ou utiliser la réinitialisation complète pour tout effacer. Cela évite qu’une suppression du cache d’appartenance fasse réapparaître un agent parti.

Vérifications supplémentaires réussies : **128 tests unitaires ciblés** d’interface, permissions, confirmations et sauvegarde/restauration ; **15 tests MySQL** de réinitialisation, vidage mensuel et audit atomique. Les captures vérifient des écrans de 1 280 × 800 et 760 × 700 sans défilement horizontal ni boutons destructifs cachés, y compris lorsque les options partielles sont dépliées. Annuler une confirmation n’appelle pas le service de suppression ; un échec de sauvegarde n’annonce pas de succès et ne lance pas la suppression d’anciennes archives. Aucune donnée de production n’a été effacée.

## Correction du message de réinitialisation `(0, '')`

Le défaut a été reproduit avec une véritable expiration de requête sur MySQL jetable : PyMySQL ferme la connexion avec l’erreur 2013, puis le nettoyage `FOREIGN_KEY_CHECKS` utilisait cette connexion fermée et remplaçait la cause initiale par `InterfaceError(0, '')`. Les nettoyages de sauvegarde, de verrou de schéma et de maintenance ont été vérifiés pour la même classe de masquage.

La suppression se fait désormais par lots de 1 000 lignes dans **une transaction unique**, sans validation intermédiaire. L’erreur initiale conserve son code et son étape ; les connexions défectueuses sont retirées, sans reconnexion ni répétition de l’effacement. Le message utilisateur identifie le délai ou la coupure et la sauvegarde de sécurité. Les journaux techniques contiennent l’étape, le type, le code et les lignes de pile, sans paramètres SQL ni données de personnel.

Une perte de réponse au moment du COMMIT est traitée comme un résultat incertain : maintenance et fichiers de récupération sont conservés pour vérification explicite. Un nettoyage de verrou échouant après une réinitialisation déjà confirmée produit un avertissement et n’invite pas à recommencer. Une erreur de récupération des fichiers est signalée avec sa sauvegarde et maintient le blocage.

Vérifications : **142 tests unitaires ciblés réussis** ; **19 scénarios MySQL réussis** en réunissant les régressions reset/vidage/snapshot et les cinq nouveaux scénarios de coupure, lenteur par lots, accusé de COMMIT perdu, récupération de fichiers impossible et nettoyage FK depuis un appelant déjà dans un `except`. La reproduction avant correction donnait bien `(0, '')` ; après correction elle conserve l’erreur 2013 et annule les modifications. L’intervention sur la base utilisateur est restée strictement en lecture seule : aucune réinitialisation n’a été relancée.

## Progression mesurée et bilan de réinitialisation

Les opérations du module de sauvegarde/restauration, le vidage mensuel et la réinitialisation disposent maintenant d’une fenêtre avec pourcentage, compteur de travail et liste des étapes déclarées dès le démarrage. Chaque étape est à venir, en cours, terminée ou interrompue. Les détails indiquent notamment la table et les lignes traitées, les fichiers ou les mois concernés. Les opérations imbriquées contribuent à leur étape parente et les mois sélectionnés partagent la progression globale sans remise à zéro.

Le pourcentage décrit des étapes et des unités réellement accomplies, sans prédire la durée restante. Une requête ou un export natif bloquant conserve la dernière valeur et affiche l’attente du serveur. La vérification de l’archive, la publication atomique et la confirmation du COMMIT sont des étapes distinctes. L’interface réserve 100 % à la fin réelle du worker ; une erreur conserve l’étape interrompue et son message jusqu’à acquittement. Fermer la fenêtre ou appuyer sur Échap pendant l’opération ne peut pas annoncer un faux succès. Les dialogues déterminés déjà utilisés par les imports disposent également du pourcentage visible et de la liste des étapes annoncées par leur traitement.

La révocation d’une session pendant une restauration ou une réinitialisation reste immédiate. Seule la présentation de la fenêtre de connexion attend la fin de l’opération et la lecture du bilan. La fenêtre principale demeure non interactive pendant cet intervalle. Après une réinitialisation réussie, l’utilisateur lit le bilan, puis l’application se ferme ; il doit la relancer pour se reconnecter. Après une restauration ou un échec, la session est vérifiée à nouveau avant de rendre la fenêtre principale accessible.

Vérifications complémentaires : **24 scénarios MySQL de réinitialisation/vidage** et **22 de sauvegarde/restauration** réussis sur l’instance jetable. Ils couvrent les choix de conservation, les remplacements de mois, les sauvegardes cohérentes, les échecs de transaction et les fins de progression après publication/COMMIT. Les deux lots globaux et le recontrôle final de 48 cas couvrent les **2 623 tests hors intégration** de la collecte finale, sans cas actuel manquant, en erreur ou ignoré. Le premier lot a révélé un ancien mock d’autorisation visant l’alias UI au lieu de la garde centralisée : il a été corrigé et les trois combinaisons administrateur/permission sont vérifiées. Le rendu de la fenêtre de progression a été inspecté. L’instance de test a été arrêtée après contrôle de son dossier et de l’absence de transaction active. Aucune opération destructive n’a été rejouée sur les données utilisateur et aucun exécutable n’a été généré.

## Généralisation aux autres traitements

Le runner commun `run_with_progress` utilise désormais le même affichage déterminé pour tous ses points d’entrée : pourcentage, étapes nommées, compteur disponible et erreur conservée jusqu’à acquittement. Les lectures rapides avec affichage différé n’ouvrent pas de fenêtre si elles se terminent avant le délai. Les services instrumentés développent leurs sous-étapes dans le plan du traitement principal. Un résultat métier `False` ou une enveloppe `(False, erreur)` conserve sa valeur pour son appelant et n’affiche pas de faux succès à 100 %.

Les exports Excel, Word, HTML et PDF, dont le classeur SME complet et ses feuilles masquées, suivent leurs écritures de lignes, feuilles, fichiers et journaux. L’export CSV depuis un modèle Qt conserve la lecture du modèle sur le thread graphique et confirme la fin après fermeture du fichier. Le rendu PDF reste également sur le thread graphique ; Qt n’offrant pas de compteur de pages pendant `print_`, la valeur reste stable durant cette étape et les événements ne sont pas pompés à l’intérieur du rendu. Les retours groupés affichent les agents traités et les nombres confirmés/en échec, avec conservation de leur bilan par agent.

Les transferts mensuels, reprises depuis un autre mois, imports Access, contrôles avant clôture, cycles documentaires, clôtures et projections disposent de phases métier et de compteurs par table, lot ou agent. Les réconciliations, reprises des dossiers, mouvements datés et rapports d’intervalle utilisent le même mécanisme. L’empreinte de maintenance indique les tables et lignes déjà lues sans requête SQL supplémentaire. Lorsqu’un service rejoint une transaction extérieure, sa dernière étape attend le callback de COMMIT ; un rollback ne la termine pas. Les callbacks différés restent rattachés à leur étape parente initiale et ne modifient pas les compteurs de l’étape suivante.

Un défaut de réconciliation découvert pendant cette instrumentation a été corrigé : les situations carrière sont désormais lues et conservées avant la seconde requête sur les grades. La seconde requête remplaçait le résultat du curseur avant le recalcul d’ordre. Le correctif est couvert par un curseur synthétique réaliste et un essai MySQL.

Validation finale : **2 658 tests hors intégration réussis**, en deux lots de **1 329**, et **69 scénarios MySQL uniques réussis** (26 de transferts/cycles/projections/imports et 43 de maintenance/mouvements/rapports/recalculs). Les rapports JUnit globaux ne contiennent ni échec, ni erreur, ni test ignoré. Un ancien test d’erreur Word a été adapté pour acquitter le nouveau dialogue et vérifier son message et son pourcentage inférieur à 100 %. Deux fixtures MySQL de diplômes sans catégorie ont été explicitées : les cas militaires sont projetés et les catégories NULL/civiles conservent la règle existante, sans modification du choix de diplôme métier. Les avertissements globaux restants sont les dépréciations Qt et moteur Excel déjà connues.

La recherche dans les sources de l’application ne retrouve plus de `QProgressDialog` ni de `setRange(0, 0)`/`setMaximum(0)` pour ses traitements. Le rendu synthétique du plan imbriqué a été inspecté. Les sources préparées pour Git passent l’analyse syntaxique, le contrôle d’espacement et la recherche de formats de secrets. L’instance MySQL jetable a été arrêtée après contrôle de son dossier et de l’absence de transaction active. Aucune base réelle n’a été modifiée et aucun exécutable n’a été généré.

## Audit fonctionnel de la Liste des présents

Le module sélectionne la présence administrative du mois consulté et une seule situation par agent. Il relit la base sans le cache de la liste générale et respecte l’appartenance mensuelle lorsqu’elle est activée. Les stages internes restent distincts des présents, sauf dans le mode explicitement combiné. Les positions absentes restent comptées présentes au corps par convention historique ; l’aperçu et la feuille de contrôles de l’export les signalent. Ce document ne constitue pas un pointage quotidien.

L’audit a reproduit puis corrigé des exclusions dues aux tabulations, retours à la ligne et espaces insécables autour des positions importées. La normalisation SQL et les compteurs de l’éditeur utilisent maintenant les mêmes règles que la liste, y compris les positions personnalisées marquées présentes dans le référentiel. Les rubriques répétées ne dupliquent plus un agent. Les structures sans regroupement par subdivision et CS-DPF restent fusionnées même si certaines subdivisions ne figurent pas dans la configuration ; les agents hors configuration ne disparaissent pas.

Le premier chargement d’une mise en page absente peut être consulté sans droit d’écriture et sans enregistrement implicite. La capture de l’aperçu fige le mois, puis lit personnel, configuration, structures, subdivisions et grades dans une seule transaction MySQL en lecture seule. Le rendu se fait après libération de cette transaction. Une modification concurrente des noms et références conserve ainsi un document cohérent ; le prochain chargement voit les nouvelles valeurs. Une erreur de lecture des grades n’est plus cachée dans un document partiel. Un instantané d’une autre base, d’un autre mois ou d’autres filtres est refusé avant écriture d’export.

Le deuxième F5 ne réutilise plus un wrapper de thread Qt détruit. Le worker conserve son service lié au contexte de départ ; résultats, erreurs et recherches devenus obsolètes sont abandonnés. Le succès et 100 % attendent la fin réelle du thread. Les droits de consultation et d’export sont revérifiés après les traitements et les dialogues de destination. Les PDF demandent une confirmation d’écrasement. Le zoom agit sur les tailles CSS réelles de l’aperçu et la barre d’outils reste accessible à 760 et 1 280 pixels.

L’aperçu, le PDF et l’Excel utilisent les mêmes personnes capturées. Le mois est visible dans le document et dans les métadonnées d’export. Le PDF dispose de colonnes adaptées au paysage et d’en-têtes répétés ; un document réel synthétique de 60 agents a été rendu, contrôlé par texte et inspecté en images. L’Excel fidèle reprend les paramètres d’impression et en-têtes/pieds du modèle, borne la zone imprimable aux lignes produites et répète ses titres. Les valeurs de page absentes dans le modèle restent absentes ; l’export de secours sans modèle utilise A4 paysage.

Validation finale de ce lot : **2 703 tests hors intégration réussis**, répartis en 1 352 et 1 351, et **16 tests MySQL ciblés réussis** (normalisation/présence 11, cohérence d’instantané/export 3, chargement concurrent/échec de mise en page 2). Les captures UI à 760/1 280 pixels et les premières/dernières pages du PDF synthétique ont été inspectées. L’instance MySQL jetable a été arrêtée après vérification de son dossier et de l’absence de transaction active. La base utilisateur et les modèles remplis n’ont pas été modifiés ; aucun exécutable n’a été généré. La recette sur les données réelles et les postes clients reste distincte de ces essais synthétiques.
