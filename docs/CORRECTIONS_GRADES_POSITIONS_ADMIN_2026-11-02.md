# Corrections Grades et Positions administratives — 2 novembre 2026

Cette intervention traite les constats G1–G6 et P1–P7 de l'[audit initial](AUDIT_GRADES_POSITIONS_ADMIN_2026-11-02.md). Les changements sont dans le code de la V2 ; aucun nouvel exécutable ni déploiement Git n'est revendiqué par ce document.

## Grades

- Les champs Code et Libellé ont des limites cohérentes avec MySQL. Préséance et Effectif renseigné acceptent un entier positif ou nul, ou une valeur vide. Les erreurs sont indiquées avant fermeture du dialogue, sans perdre la saisie. La même validation protège le dépôt SQL.
- Les égalités de préséance sont autorisées et départagées par le code. Cette règle est expliquée dans le formulaire.
- Choisir un insigne conserve une sélection en attente ; Annuler ne publie aucune image.
- Les insignes enregistrés sont associés au code stable du grade. L'objet image porte une identité de contenu et ne remplace jamais une ancienne version. L'association dans `app_config`, les données du grade et les traces d'audit sont validées dans la même transaction.
- Si l'audit ou SQL échoue, l'ancien grade et son ancienne association restent visibles. Un objet copié mais non référencé peut subsister sur disque après un échec ; il ne devient pas l'insigne du grade et n'écrase aucune image existante. Les anciennes images ne sont pas supprimées automatiquement.
- Le renommage conserve l'association au code. Un ancien insigne nommé d'après le libellé est repris lors du premier renommage effectué par le service.
- La version de formulaire tient aussi compte de l'association à l'insigne : deux postes ne peuvent pas remplacer silencieusement l'image de l'autre avec un ancien formulaire.
- Le chemin de suppression réellement utilisé par la vue produit un audit métier atomique et retire l'association d'image. Les agents des mois passés et l'historique des passations sont contrôlés avant suppression.
- Le chargement, le comptage des agents et la lecture des images passent en arrière-plan. Les comptages sont groupés ; le modèle d'affichage réutilise des miniatures en mémoire. Changer de ligne ou repeindre le tableau ne relit plus MySQL ou les fichiers d'image.
- La sélection est conservée à l'actualisation et au filtrage lorsqu'elle reste visible. La fiche latérale est accessible par défilement ; elle distingue « Effectif renseigné » et « Agents (archives comprises) ». Une suppression connue comme impossible est désactivée avec une explication.

## Positions administratives

- Le référentiel utilise des instantanés versionnés, avec l'identifiant existant inclus dans leur signature. Modifier ou supprimer un état devenu périmé est refusé ; les données de l'autre poste restent conservées.
- La création n'utilise plus d'upsert : un doublon est signalé et ne modifie pas la position existante.
- Les positions système possèdent un code métier stable dans un module commun. Leurs libellés ne sont ni renommables ni supprimables depuis le référentiel. Les anciens libellés de base « En mission », « Congé maladie » et « Stage extérieur » sont reconnus comme des alias métier, sans réécrire leurs valeurs historiques.
- Les règles de présence imposées sont affichées comme telles et ne peuvent plus être contredites par la case du dialogue. Pour une position personnalisée, la case reste modifiable.
- Les prédicats Python utilisés par les états et exports consultent le même index de présence que la liste. L'index est partagé et invalidé lors d'un changement de référentiel ; une boucle d'export ne lance pas une requête par agent.
- Le filtre SQL prend aussi en compte les anciennes situations stockées uniquement avec un libellé. Les comparaisons de libellés personnalisés tolèrent les différences d'accents et les espaces internes supplémentaires, comme l'index Python.
- Le renommage d'une position personnalisée conserve son identifiant. Il met à jour les situations liées et les anciennes situations textuelles sur tous les mois, ainsi que les événements de position concernés. Les traces d'audit déjà enregistrées restent intactes. La confirmation explique explicitement cette portée globale.
- Une position utilisée par une fiche, une archive ou un événement de dossier ne peut pas être supprimée. L'écran indique cette protection, et le service la vérifie encore sous transaction. Il ne promet plus une suppression incompatible avec les clés étrangères.
- L'audit de modification et de suppression conserve les anciennes valeurs, les nouvelles valeurs quand il y en a et l'auteur.
- L'ordre de tri invalide n'est plus remplacé silencieusement par une valeur vide. La validation s'effectue dans le dialogue et dans le dépôt.
- Le chargement, les comptages et les écritures passent hors du fil graphique. Le tableau donne la place au libellé, dimensionne les autres colonnes, affiche le nombre de résultats et les agents distincts sur tous les mois. Les actions dépendent de la sélection ; les erreurs conservent les données précédemment affichées.

## Politique commune

Grades et Positions administratives sont des référentiels globaux, indépendants du mois actuellement consulté. Les écrans et confirmations annoncent cette portée. Les droits `referentiels.grades` et `referentiels.padmin` sont aussi vérifiés par le service avant modification ; les protections existantes sur les changements de situation individuelle restent applicables.

Stage interne reste distinct de la présence ordinaire et continue de dépendre du mode de liste choisi (`present`, `stage_interne` ou `les_deux`). Les règles d'absence des positions système sont conservées, avec une présentation qui ne permet plus d'enregistrer une case contradictoire.

## Vérification

Les essais utilisent des profils de test séparés, bloquent l'accès réseau hors de l'instance MySQL jetable vérifiée et ne modifient pas les données utilisées par l'application.

- Nouveaux tests Qt : validation sans perte de brouillon, annulation de l'insigne, chargements et écritures dans des workers, absence d'accès fichier pendant le rendu, conservation des sélections et captures à 960 × 720 et 1366 × 768.
- Nouveaux tests MySQL : doublons, formulaires périmés, renommage global avec conservation de l'identifiant, refus de suppression d'une position utilisée, audits complets, renommage avec insigne ancien ou nouveau, annulation lors d'un échec d'audit, détection d'une modification de l'image seule et accord SQL/Python sur une position personnalisée.
- Suite unitaire finale complète : **1 978 tests réussis**, aucun échec, aucun test ignoré. Les 33 avertissements concernent une dépréciation Qt déjà présente dans la navigation.

Résultats natifs : **17 tests MySQL réussis**, puis **3 vérifications finales réussies** après harmonisation des espaces et accents dans les anciennes situations. Le serveur jetable a été arrêté proprement après contrôle de son datadir.

Contrôles des anciens tests de formulaires : les tests de concurrence ont été adaptés à l'exécution en arrière-plan, au nouveau point d'entrée de sauvegarde du grade et à la politique de référentiel global ; le test de filtre utilise maintenant les vrais widgets. Les **93 tests** des trois modules concernés passent, avec conservation des vérifications de brouillon, de version et d'autorisation.

Captures inspectées avec le thème réel : [Grades à 960 pixels](assets/grades_positions_20261102/grades_960.png), [Positions à 960 pixels](assets/grades_positions_20261102/positions_960.png). Les versions à 1366 pixels se trouvent dans le même dossier.

Rapports XML locaux : `sme_python/build/isolated_tests_29f89994aa2e4ff78be02660776bde5a/results.xml` (suite générale), `isolated_tests_2e7ecbf5ad8b4022bfd920f57c609801/results.xml` (17 cas natifs), `isolated_tests_4454c47500e74299af248ede92c3c418/results.xml` (3 vérifications finales). Les trois derniers cas répètent une partie des cas natifs après le dernier ajustement ; ils ne représentent pas trois scénarios supplémentaires distincts.

Ces contrôles ne constituent pas une mesure de latence sur le partage réseau et les deux postes de production. Le code portable actuellement distribué n'est pas modifié par l'édition des sources.
