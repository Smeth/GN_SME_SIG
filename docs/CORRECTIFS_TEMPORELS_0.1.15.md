Correctifs des enregistrements datés — version 0.1.15

Les diplômes et affectations saisis dans la fiche complète sont projetés dans
les situations mensuelles existantes à partir de leur date d'effet. Modifier
leur date ou supprimer un événement recalcule également les mois concernés.
Les autres rubriques de la carrière ne sont pas réécrites par cette projection.

Modifier la date d'un diplôme depuis la liste conserve sa catégorie, son lieu,
sa mention, son établissement et ses observations. Deux diplômes portant le
même libellé dans des catégories différentes restent distincts.

Les missions, maladies, désertions, stages et congés saisis sont rattachés au
mois de début réel. Leurs lectures mensuelles utilisent leurs dates : une
absence couvrant plusieurs mois apparaît dans chaque mois concerné et un
stage futur n'alimente plus la fiche d'un mois antérieur. Les versions des
événements restent contrôlées lors des modifications concurrentes.

La position administrative est projetée selon la situation à la fin du mois
consulté, ou à la fin de la situation individuelle si elle se termine avant.
Les dates saisies dans les fiches complémentaires et les actions groupées
déterminent donc les mois effectivement modifiés. Une absence terminée peut
figurer dans le registre du mois sans être la position à la fin de ce mois.
La suppression d'une fiche supprime aussi sa projection de position.

La reprise d'un mois applique les historiques déjà connus, y compris une
nomination future précédemment enregistrée. Les anciens libellés de mois
comme 202609 et les dates de fin de situation bornent correctement la lecture.
La fin de stage, le retour à Présent et le diplôme sont enregistrés dans la
même transaction : un échec annule l'ensemble pour l'agent concerné.

Les nouvelles affectations utilisent leur mois d'effet comme métadonnée.
Le dialogue de congé et le nom du PDF utilisent le mois consulté.

Les mois clôturés restent protégés. Corriger un événement dont le mois de
prise d'effet est clôturé exige de rouvrir ce mois. Une saisie future conserve
l'événement sans inventer une liste mensuelle complète lorsqu'elle n'existe
pas encore. La création normale du mois reprendra cet historique.

Les vérifications MySQL utilisent des schémas synthétiques jetables sur une
instance locale isolée. Elles ne modifient pas les données d'exploitation.
Les essais de plusieurs postes et des accès Windows réels restent distincts
de ces vérifications automatisées.

Livraison : dossier portable complet, sans archive ZIP. L'exécutable doit
rester avec les bibliothèques et ressources de son dossier.
