# Corrections de la fiche et de la maintenance — 3 novembre 2026

Ce document complète l'[audit initial](AUDIT_PERFORMANCE_2026-11-03.md). Les changements sont appliqués aux sources du projet ; les données et réglages de la base opérationnelle n'ont pas été modifiés.

## Fiche complète

- Les lectures, la vérification du contexte et la trace d'ouverture sont préparées dans un worker, avec une progression discrète si le chargement dure. Les widgets restent construits dans le fil Qt.
- La fiche n'attend plus le calcul global des alertes avant de s'afficher. Le bandeau est complété en arrière-plan, y compris après une modification du dossier.
- Le résultat complet du calcul des alertes est réutilisé par mois et par révision partagée. Les contrôles entre agents, les doublons et les acquittements sont conservés. Une révision qui change pendant le calcul provoque une reprise.
- Une projection initiale évite la seconde lecture de la même fiche. Les tableaux d'historique sont remplis à l'ouverture de leur onglet à partir des données préparées ; les services normaux et les jetons de conflit restent utilisés pour les modifications.
- Les droits du point d'entrée et le contexte sont revérifiés après la préparation. Un droit d'édition retiré pendant le chargement ne reste pas acquis. Une fenêtre détruite ne reçoit pas le nouveau dialogue.
- Les SELECT de la liste et de la fiche utilisent le canal de lecture, sans s'enregistrer inutilement comme écrivains.
- Les quatre lectures du dossier résolvent une seule fois l'identité de l'agent. Les missions, maladies et stages de la synthèse peuvent être chargés pour une personne, en conservant le périmètre mensuel, le repli historique et les jetons d'écriture.
- Une consultation seule ne recharge plus toute la liste générale à sa fermeture. Après une modification, le rechargement s'effectue en arrière-plan ; le mécanisme existant de révision partagée reste actif entre postes.

## Sauvegarde, restauration et maintenance

- Le nettoyage des anciennes tables utilise désormais le propriétaire de maintenance centralisé. Ses autres connexions ne tentent plus d'acquérir une seconde fois le verrou exclusif déjà détenu par l'opération.
- La sauvegarde préalable et sa vérification restent obligatoires. Les connexions de l'opération sont distinguées des autres clients, qui continuent de bloquer une suppression exclusive.
- Une suppression partielle ou un résultat DDL incertain conserve la maintenance jusqu'à vérification de la récupération.
- La restauration de configuration partage le même propriétaire de maintenance avec la coordination des médias. Son journal conserve le jeton permettant de reprendre exactement la maintenance interrompue correspondante ; un autre jeton ou une autre opération ne peut pas la reprendre.
- Les fichiers modifiés entre-temps restent protégés contre l'écrasement par une récupération. Une bascule des médias est refusée pendant la restauration, puis peut être relancée après sa fin.

## Tests remis à jour

Les jeux synthétiques ont été alignés sur le schéma courant : statut des périodes, alias d'identité et journal central. Les appels de référentiels utilisent les jetons de version existants ; aucun contrôle de conflit n'a été supprimé pour faire passer les tests.

Les attentes obsolètes ont été harmonisées avec les règles déjà appliquées : position administrative vide comptée comme présente, libellé issu de la référence liée prioritaire sur un ancien texte de repli, renommage d'une position du catalogue propagé à tous les mois.

De nouveaux tests vérifient la réactivité réelle de la boucle Qt pendant le chargement, les changements de révision du cache, le retrait de droits, la construction d'une fiche préparée sans SQL dans le fil graphique et l'équivalence des lectures ciblées avec les projections collectives.

Le lanceur isolé dispose d'options explicites pour répartir les cas en lots, simuler les ACL dans son seul profil jetable et autoriser uniquement le client `mysqldump` attendu vers le port et le schéma de test vérifiés. Ces options ne changent pas les protections de l'application.

## Validation et mesures

**2 959 cas distincts ont été vérifiés : 2 075 hors intégration et 884 avec MySQL. Aucun échec ne reste après les reprises ciblées.**

- La passe finale MySQL comporte **884 réussites**, sans échec ni cas ignoré, réparties en huit lots sans omission ni doublon.
- La passe hors intégration comporte **2 074 réussites et un échec de faux connecteur**, qui n'acceptait pas encore `read_only=True`. Ce faux connecteur a été corrigé pour vérifier explicitement le mode de lecture ; les **six tests de son module réussissent** lors de la reprise ciblée. Les 2 075 cas sont donc validés par la passe globale complétée de cette reprise.
- Des reprises supplémentaires vérifient le dernier état du chargement et des droits (**6 cas avec les lectures MySQL ciblées**, et **41 cas UI/édition/avancement**) ainsi que la conservation de la maintenance après DDL partiel (**12 cas legacy**). Elles réussissent toutes.
- Les **20 cas ciblés** sur la restauration de configuration et les positions ont également réussi après correction des fixtures et de la coordination.

Sur 5 000 agents fictifs et 45 000 situations mensuelles, deuxième ouverture avec cache disponible :

| Mesure | Avant | Après |
|---|---:|---:|
| Relecture + préparation/construction + trace d'ouverture | 0,955 s | 0,119 s |
| Commandes SQL émises | 90 | 58 |
| Construction des widgets après préparation | Chargements SQL et calculs inclus dans le constructeur | 0,014 s |

Cela représente environ **8 fois moins de temps dans le parcours synthétique à chaud**, et **36 % de commandes SQL en moins**. Le benchmark inclut encore la projection complète des alertes pour permettre la comparaison ; dans le parcours applicatif initial, elles sont désormais chargées séparément après l'affichage de la fiche. Les données du benchmark ont peu d'événements individuels et peu de traces historiques ; ces chiffres ne garantissent pas le même temps sur une base réelle ou un réseau distant.

Le scénario sans résultat d'alertes réutilisable a aussi été mesuré : environ 0,69 s pour la projection complète, dont 0,013 s de construction des widgets. Ce calcul complet n'est plus une condition préalable à l'affichage dans l'application.

Les preuves sont conservées dans `corrections-final-summary-20261103.json`, `corrections-performance-metrics-20261103.json`, les journaux `corrections-final-*`, `corrections-dossier-benchmark-20261103.txt` et leurs rapports XML sous `sme_python/build/isolated_tests_*`. Le JSON distingue les résultats de la grande passe de ceux des reprises ciblées.

Les tests utilisent des données fictives, MySQL local jetable et Qt hors écran. Les réglages de durabilité du serveur jetable sont assouplis pour les tests fonctionnels ; ils ne sont pas appliqués à la base réelle. Les permissions Windows effectives et la résistance à une panne physique du serveur ne sont pas certifiées par cette exécution.

Pour accélérer les créations de tables de la fin des tests, les nouvelles tables de l'instance jetable ont temporairement été créées dans l'espace partagé InnoDB. Le mode par table et le redo ont été rétablis avant les mesures comparatives. Les réglages normaux sont rétablis et le serveur jetable est arrêté à la fin de l'intervention.

## Conservation et utilisation

Les modifications qui existaient avant cette intervention ont été conservées. Une copie des sources, tests et scripts avant correction est disponible dans `sme_python/build/before_corrections_20261103.zip`.

Les corrections prennent effet au prochain lancement depuis les sources du projet. Un exécutable portable déjà construit doit être reconstruit pour les intégrer.

## Correctif complémentaire — enregistrement du formatage des structures

Le bouton de cet onglet passait par la sauvegarde globale des attestations. Un grade vide ou des alias de grade en double dans une configuration existante pouvaient ainsi bloquer un simple renommage de libellé de structure.

L'onglet utilise désormais une sauvegarde limitée à `structure_format`, en conservant les autres données stockées, y compris les grades et les options. Le jeton de version reste contrôlé : un brouillon ancien ne remplace pas une modification d'un autre poste. La validation des grades reste appliquée à leur propre enregistrement.

Validation : **140 tests ciblés réussis**, dont trois tests MySQL reproduisant le blocage avec un grade vide ou des alias en double, vérifiant la conservation des autres sections et le refus d'un éditeur périmé. Preuve : `corrections-structure-format-20261103.txt`.
