# Enregistrements SME et fin de stage — 0.1.12

Cette version inclut les corrections précédentes d’actualisation et d’enregistrement des grades.

## Diplômes dans Édition SME

L’ajout depuis la cellule Diplôme écrit dans l’historique du dossier puis relit la situation en base. Il recalcule uniquement le diplôme affiché, en conservant le grade et l’affectation.

Un diplôme enregistré peut ne pas devenir celui affiché pour le mois : les diplômes militaires sont prioritaires, puis la date et le mois consulté déterminent le diplôme retenu. Un diplôme civil ou postérieur au mois reste bien présent dans l’historique.

## Fin de stage

Pour chaque agent, le changement de position, la clôture du stage et le diplôme demandé partagent une transaction. Si le diplôme échoue, la clôture et les autres changements de cet agent sont annulés. Les compteurs de réussite sont incrémentés après validation de la transaction.

Un agent déjà Présent n’est plus ignoré lorsque l’ajout d’un diplôme a été demandé. Sa date de fin existante n’est pas réécrite pour ajouter le diplôme. Les agents déjà Présents sont indiqués séparément dans le bilan.

## Actions groupées

Chaque agent est enregistré dans sa propre transaction. Les succès restent enregistrés et les échecs sont listés ; un traitement groupé n’est pas une transaction unique pour toute la sélection.

Le contrôle des grades de 0.1.11 s’applique aux actions groupées. Un contrôle supplémentaire empêche d’annoncer une structure, une fonction ou une subdivision comme enregistrée lorsque l’historique d’affectation la remplace. L’agent concerné est signalé en erreur, sans écriture partielle.

Le recalcul porte sur les rubriques réellement modifiées. Changer une affectation ne recalcule plus les diplômes et les grades sans rapport avec cette saisie.

## Utilisation

Vérification : 75 tests ciblés hors base et 14 essais MySQL réussis sur des données fictives et une instance locale isolée. Les essais MySQL vérifient les valeurs et historiques relus après écriture, les échecs partiels des actions groupées et l’annulation complète de la fin de stage si le diplôme échoue. Aucun accès à une base de production. Les tests utilisent des ACL simulées et des droits hérités dans leurs seuls répertoires jetables ; ils ne certifient pas les protections Windows.

Après installation, vérifier le bilan des actions groupées : le nombre d’agents modifiés et les éventuelles erreurs. Actualiser permet de relire les valeurs enregistrées.

Les correctifs ne recréent pas automatiquement un diplôme manquant à la suite d’un ancien échec de fin de stage ; consulter le dossier de l’agent et l’ajouter si nécessaire.
