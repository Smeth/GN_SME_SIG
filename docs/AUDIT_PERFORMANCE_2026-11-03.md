# Audit de performance et des tests — 3 novembre 2026

## Conclusion sur la fiche complète

Le ralentissement est surtout expliqué par le travail effectué **avant l'affichage**, dans le fil de l'interface : recalcul global des alertes de carrière, lecture de tous les historiques et relectures de la fiche. La journalisation n'est pas le premier poste dans le scénario synthétique mesuré.

Aucun code applicatif, réglage utilisateur ou schéma de la base opérationnelle n'a été modifié par cet audit. Les scripts de mesure et leurs fichiers de résultats sont séparés dans `sme_python/build/` et à la racine du dépôt. Les modifications déjà présentes dans le dépôt ont été conservées.

## Mesures de la fiche

Scénario : MySQL 8.0.40 local jetable, données fictives, PySide6 hors écran, fiche en consultation seule, neuf situations mensuelles par agent. Deux ouvertures par population. Le temps ci-dessous est celui de la construction du dialogue ; la relecture initiale et la trace d'ouverture sont mesurées séparément.

| Population | Situations de carrière | Construction de la fiche | Dont alertes globales |
|---|---:|---:|---:|
| 2 agents, deuxième ouverture | 18 | 0,064 s | 0,004 s |
| 1 000 agents | 9 000 | 0,178–0,214 s | 0,117–0,152 s |
| 5 000 agents | 45 000 | 0,876–0,927 s | 0,770–0,800 s |

Une deuxième passe instrumentée confirme le résultat : environ 0,83–0,92 s à 5 000 agents, avec le calcul global des alertes toujours dominant. Les variations entre passes ne constituent pas une comparaison avant/après : aucune optimisation n'a été appliquée.

Après initialisation, une ouverture complète mesurée — relecture initiale, construction, trace d'ouverture — émet **90 commandes SQL**, dont certaines servent à la coordination des accès. La toute première ouverture émet 199 commandes dans ce scénario, initialisations comprises.

Répartition observée lors de la deuxième passe, hors relecture initiale et trace d'ouverture :

| Étape | Commandes SQL |
|---|---:|
| En-tête et médias | 3 |
| Alertes | 6, avec lecture globale des historiques |
| Deuxième relecture de la fiche | 17 |
| Synthèse | 2 |
| Historiques du dossier | 22 |
| Historique des modifications | 13 |

La trace centrale d'ouverture prend environ 3–6 ms dans les passes mesurées. Le serveur de test utilise une synchronisation disque assouplie pour accélérer les tests fonctionnels : ce chiffre ne mesure donc pas le coût de durabilité d'un serveur de production. Il ne justifie pas de désactiver le journal.

Limites : agents synthétiques homogènes, historiques d'événements et de modifications peu remplis, serveur local, pas de mesure de réseau distant, pas de chronométrage du rendu visible sur le poste utilisateur ni du mode édition. Le serveur de test sert aussi aux tests d'intégration pendant la deuxième passe instrumentée. Aucun gain futur n'est garanti à partir de ces mesures seules.

## Optimisations proposées, par priorité

1. **Alertes de carrière : éviter le recalcul global à chaque ouverture.** `PersonnelDossierDialog._refresh_alerts_banner()` appelle `alerts_for_agent()`, qui exécute `scan_all_career_alerts()` pour tous les agents avant de filtrer. Réutiliser un résultat calculé hors du fil UI, indexé par agent, mois et révision des données. Préserver impérativement les contrôles de doublons entre agents et les acquittements ; filtrer naïvement toutes les données à un seul agent ferait perdre ces contrôles.

2. **Afficher la fiche sans attendre les historiques cachés.** Le constructeur appelle `_refresh_all()` avant `exec()`. Les diplômes, distinctions, passations, affectations, missions, stages, maladies, désertions, congés, positions et historique des modifications sont chargés immédiatement. Préparer les données dans un worker, construire les widgets dans le fil UI, puis charger les onglets à la demande. Les droits et le contexte mensuel doivent être vérifiés à nouveau lors de l'affichage et des écritures. Un mécanisme de préparation en arrière-plan existe déjà pour certaines consultations depuis les alertes ; il peut servir de base.

3. **Réduire les relectures et les transactions inutiles.** `resolve_dossier_row()` appelle `get_by_id()`, puis `_refresh_all()` provoque un deuxième `get_by_id()`. Des SELECT utilisent `_connect()` sans `read_only=True`, ce qui active la coordination des écritures et ajoute des commandes de verrouillage. Examiner ces accès un par un en préservant les garanties de maintenance et de cohérence. Résoudre une fois l'identifiant canonique de l'agent pendant un chargement et réutiliser les mêmes référentiels pendant cette opération.

4. **Ne recharger la liste générale qu'en cas de changement.** `PersonnelListView._execute_pending_dossier_open()` recharge actuellement toute la liste après la fermeture, même après une simple consultation. Exploiter le résultat `liste_generale_changed`, tout en tenant compte d'une modification provenant d'un autre poste via la révision partagée.

5. **Limiter les lectures collectives pour la synthèse d'un seul agent.** `personnel_situation_context.py` appelle notamment `load_missions().get(join_key)`, les chargeurs de stages et `load_malades().get(join_key)`. Examiner des lectures ciblées ou la réutilisation d'un lot déjà chargé. Le coût dépend de la position administrative et du volume ; il n'a pas été mesuré ici pour toutes les positions.

6. **Mesurer avant d'ajouter des index ou de réduire l'audit.** Ajouter un chronométrage ciblé des étapes et un compteur de requêtes, sans données personnelles ni journal par ligne. Examiner ensuite les plans SQL avec une volumétrie représentative. Les tables d'événements possèdent déjà plusieurs index par agent et date ; un index supplémentaire ne résout pas le recalcul global côté Python ni le blocage du fil UI.

## Tests et limites de validation

La suite existante contient 2 953 cas collectés : 2 071 hors intégration et 882 d'intégration MySQL.

- Première passe complète exploitable : **2 040 réussites, 30 échecs, 882 ignorés faute de serveur jetable fourni, un cas écarté après blocage dans la suite**. Durée : environ 193 s.
- Le cas écarté, `test_available_operations_include_codes_outside_the_result_limit`, réussit seul : **1/1**. Le blocage groupé reste un problème d'isolation de tests à examiner ; il ne démontre pas à lui seul un blocage de l'application utilisée normalement.
- Les 30 échecs initiaux sont liés à l'impossibilité de poser les permissions Windows dans l'environnement restreint. Un second passage des six fichiers concernés donne **113/113 réussites**, avec une émulation des permissions limitée aux dossiers synthétiques. **Les protections Windows réelles ne sont pas certifiées par ce passage.** Aucun mécanisme de protection applicatif n'a été modifié.
- Les deux mesures instrumentées de la fiche réussissent sur données fictives. Les timings ne certifient pas toutes les actions interactives.
- Méthode des tests MySQL : serveur neuf lié uniquement à `127.0.0.1:33319`, répertoire vérifié sous `sme_python/build/mysql_audit_20261103/`. Synchronisation disque assouplie ; le redo InnoDB a ensuite été désactivé sur **cette seule instance jetable**, après les mesures de performance, pour accélérer les créations de tables. Cette possibilité est documentée pour une instance neuve dans le [manuel MySQL](https://dev.mysql.com/doc/refman/8.0/en/innodb-redo-log.html). Les tests ne certifient donc pas la durabilité après panne du serveur. Ces réglages ne sont pas une proposition pour la base utilisée par l'application.
- **Résultat MySQL final : 817 réussites, 65 échecs, aucun cas MySQL ignoré.** Les cas sont répartis en lots de processus distincts, chacun utilisant ses propres schémas. Les 180 réussites déjà acquises ont été conservées, puis les 702 cas restants, y compris les échecs à reproduire, ont été exécutés en huit lots. La vérification d'ensemble couvre **882 identifiants de test distincts**, sans doublon ni omission. Les tests de concurrence au sein d'un cas conservent leur comportement normal.
- Bilan de couverture des **2 953 cas distincts** : 2 071 hors intégration ont réussi lors des passages décrits ci-dessus, puis 817 cas MySQL ont réussi et 65 ont échoué. Ce n'est pas une suite entièrement verte : certaines réussites ont exigé une émulation ACL et le cas UI bloquant a été testé séparément. Ce bilan ne remplace pas un parcours manuel complet de l'application, ni une validation avec la base opérationnelle.

Répartition préliminaire des 65 échecs MySQL :

| Famille | Nombre | Interprétation |
|---|---:|---|
| Schémas synthétiques incomplets | 32 | 22 cas sans colonne `statut`, 10 sans table `agent_identity_alias` |
| Appels de référentiel sans jeton de version | 9 | Tests utilisant une ancienne convention d'appel, dont un échec secondaire d'assertion |
| Double de dialogue incomplet | 1 | Objet fictif sans `_pick_group_source` |
| Attente sur un texte de subdivision vidé, avec clé étrangère conservée | 2 | Le code privilégie désormais le libellé de la référence liée ; le test n'a vidé que le texte de repli |
| Texte exact d'erreur attendu différent | 1 | Le refus existe, mais son libellé a changé |
| Règle de présence contradictoire | 1 | Position administrative vide, voir ci-dessous |
| Exécution d'un programme externe interdite par le harness | 1 | Test natif `mysqldump` non validé dans ce passage isolé |
| Coordination maintenance/restauration à examiner | 18 | Refus, attentes et blocages ; ne pas les assimiler globalement à des tests obsolètes |

Le classement décrit les causes immédiates des échecs. Il ne permet pas de déclarer toutes les fonctions concernées correctes. Les détails, traces et identifiants figurent dans `audit-summary-20261103.json` et `audit-failure-groups-20261103.json`.

## Écarts déjà reproduits

**Règle de présence : contradiction entre tests.** Le test MySQL `test_present_read_explicit_month_ignores_stale_current_flags` attend uniquement la ligne 1 et obtient les lignes 2 et 1. La ligne 2 a une position administrative vide. Or `test_empty_padmin_is_present_au_corps` exige explicitement qu'une position vide soit comptée parmi les présents, ce que fait la requête actuelle. Il faut harmoniser les attentes sur la règle métier avant de qualifier ce résultat de bogue et de modifier le calcul.

**Jeux de données MySQL anciens incomplets.** Le cas `test_movements_and_workflow_have_one_trace_after_each_success[gain_insert]` échoue aussi lorsqu'il est exécuté seul : sa base simplifiée ne contient pas `agent_identity_alias`, table désormais consultée par la résolution des identités. Cet échec ne prouve pas une panne sur une base réelle correctement migrée. Les autres échecs de ce type doivent être classés de la même manière.

**Blocage reproductible du nettoyage legacy avant suppression.** `test_legacy_lot31_verified_backup_drop_and_actual_recovery` échoue dans la suite et lors d'un passage ciblé. La sauvegarde préalable appelle `_python_sql_dump()`, qui ouvre une connexion via le circuit d'écriture alors qu'un verrou de maintenance est déjà détenu par le nettoyage. Le refus de coordination survient avant le DROP dans ce scénario. Ce point mérite une correction fonctionnelle distincte des optimisations de la fiche : harmoniser la possession de la maintenance et le chemin de lecture de la sauvegarde, puis revérifier la sauvegarde, les refus et la récupération. Ne pas contourner la sauvegarde préalable.

**Restauration et maintenance : 18 échecs à analyser plus finement.** Ils comprennent ce blocage legacy, des refus de restauration déjà en cours, des attentes de coordination et des tests de verrouillage. Certains peuvent se propager après une opération interrompue ou dépendre de l'isolation des tests. Les reproductions ciblées ne suffisent pas à départager tous ces cas ; aucune correction n'a été tentée pendant cet audit.

## Fichiers de preuve

- `audit-isolated-20261103-v2.txt` : grande passe initiale et durées.
- `audit-rechecks-20261103.txt` : répétition fonctionnelle des six fichiers avec émulation ACL.
- `audit-hang-20261103.txt` : cas initialement bloquant exécuté seul.
- `audit-dossier-benchmark-20261103.txt` : mesures initiales de la fiche.
- `audit-dossier-queries-20261103.txt` : mesures et répartition des commandes SQL.
- `audit-presence-recheck-20261103.txt`, `audit-gain-recheck-20261103.txt` : reproductions ciblées.
- `audit-mysql-balanced-0-20261103.txt` à `audit-mysql-balanced-3-20261103.txt` et `audit-completed-20261103.json` : preuve des 180 réussites conservées avant répartition du travail restant.
- `audit-mysql-remaining-0-20261103.txt` à `audit-mysql-remaining-7-20261103.txt` : les 702 cas MySQL restants, y compris les échecs reproduits.
- `audit-summary-20261103.json`, `audit-failure-groups-20261103.json` : couverture, compteurs, traces détaillées et classement.
- `audit-reference-recheck-20261103.txt`, `audit-functional-rechecks-20261103.txt` : reproductions ciblées complémentaires.
- Chaque passage isolé produit également `results.xml`, `collected.json` et `pytest.log` dans son dossier `sme_python/build/isolated_tests_*`.
