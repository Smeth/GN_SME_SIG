Corrections rétroactives dans les mois clôturés — 0.1.19

Une correction datée enregistrée depuis le mois de travail recalcule désormais
toutes les situations mensuelles existantes concernées, y compris celles des
mois clôturés. Il n'est plus nécessaire de rouvrir chaque ancien mois pour
propager cette correction. Les mois conservent leur statut de clôture.

Exemple : en décembre 2026, une nomination prenant effet en juillet corrige les
situations de juillet à décembre. Si une autre nomination prend effet en
octobre, le grade de juillet s'applique jusqu'en septembre et le grade
d'octobre reste applicable ensuite. Les mois antérieurs à la prise d'effet et
les autres agents ne sont pas modifiés par cette propagation.

La règle s'applique aux grades, aux diplômes et aux affectations de l'historique,
ainsi qu'aux positions et absences datées. Les registres des mois clôturés
affichent également les saisies manuelles dont les dates couvrent ces mois.
La position mensuelle correspond à la situation à la fin du mois, ou à la fin
du segment individuel s'il se termine avant.

Le diplôme affiché demeure le diplôme militaire obtenu le plus récemment à la
date du mois consulté ; une saisie rétroactive ne masque pas un diplôme obtenu
plus tard. À date d'obtention identique, le dernier diplôme ajouté est retenu.

Le journal central enregistre les mois effectivement corrigés, leur statut de
clôture et les valeurs avant/après. L'historique, les projections mensuelles et
leur trace partagent une transaction : un échec annule l'ensemble. Les droits
de modification et les contrôles des modifications concurrentes sont conservés.

Les corrections n'inventent pas de liste complète pour un mois absent.
Les documents déjà exportés restent des fichiers statiques ; les nouveaux
exports doivent être produits pour disposer des états corrigés.

Les essais MySQL utilisent exclusivement des bases synthétiques jetables.
Ils couvrent notamment juillet à décembre avec les anciens mois clôturés,
les événements postérieurs, la conservation des statuts, les absences sur
plusieurs mois et l'annulation en cas d'échec du journal.

Livraison : conserver le dossier portable complet. Aucune archive ZIP de
distribution n'est générée.
