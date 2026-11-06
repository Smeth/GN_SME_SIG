# Audit étendu des incohérences d’enregistrement et de période

Version examinée : 0.1.14. Les essais ne modifient aucune source applicative ni donnée de production. Ce rapport recense des défauts reproduits, pas des correctifs livrés.

## Nouveaux cas confirmés

| Priorité | Parcours | Exemple reproduit | Conséquence |
|---|---|---|---|
| Haute | Modification de la date d’un diplôme depuis la fiche personnel | Changer uniquement la date d’obtention d’un diplôme renseigné | Le lieu, la mention et l’établissement sont vidés ; les observations deviennent « Saisie applicative » |
| Haute | Diplômes civil et militaire portant le même libellé | Modifier la date du diplôme militaire affiché, alors qu’un diplôme civil homonyme est plus récent | L’enregistrement modifie la ligne civile et la transforme en diplôme militaire ; la ligne militaire initiale reste inchangée |
| Haute | Reprise de septembre vers octobre | Une passation ADM au 01/10 existe déjà, puis octobre est rempli depuis septembre | La nouvelle situation d’octobre reprend LT de septembre au lieu de prendre en compte ADM. La passation reste pourtant dans l’historique |
| Haute | Modification d’une mission déjà enregistrée | Déplacer sa date de septembre au 01/10 | La date change, mais le rattachement SME reste septembre |
| Haute | Modification d’une désertion déjà enregistrée | Déplacer sa date de septembre au 01/10 | Même maintien du rattachement au mois précédent |
| Haute | Ancien format de mois | Période stockée sous la forme `202609`, puis ajout d’un diplôme au 01/10 | La borne de date n’est plus reconnue et la situation de septembre reçoit ce diplôme futur |
| Moyenne | Fin anticipée d’une situation de carrière | Situation d’octobre terminée le 10/10, nomination ADM au 20/10 saisie depuis septembre | Le recalcul inscrit ADM au 20/10 dans la situation terminée le 10/10 |
| Moyenne | Ajout d’une affectation depuis la fiche complète | Septembre consulté, novembre actif, affectation datée d’août | Le champ de provenance mensuelle reçoit novembre. Les mois affiché, actif et d’effet ne sont pas distingués correctement |
| Faible | Nom proposé pour le PDF du parcours | Septembre consulté et novembre actif | Le nom de fichier proposé contient novembre. Ce constat concerne le nom proposé ; le contenu du PDF n’a pas été déclaré incorrect sur cette seule preuve |

Ces neuf scénarios comprennent plusieurs variantes d’un même problème. Ils ne représentent pas nécessairement neuf causes techniques indépendantes.

## Constats précédents toujours applicables

Les sources sont identiques à celles du portable 0.1.14 ; les constats précédents restent présents :

- Diplômes et affectations datées : les autres situations mensuelles existantes ne sont pas automatiquement recalculées.
- Missions et désertions créées depuis un autre mois : rattachement au mois de saisie, indépendant de la date de l’événement.
- Fiches de stage : le repli peut reprendre une fiche future dans un mois antérieur.
- Congés : rattachement au mois fourni par l’écran, sans reprise automatique dans les autres mois couverts ; la fiche complète peut fournir le mois actif au lieu du mois consulté.
- Maladies manuelles : conservation dans le dossier avec une période NULL, puis exclusion des lecteurs mensuels stricts.
- Positions administratives, y compris en action groupée : application immédiate au mois affiché sans projection des autres périodes selon les dates.

Les audits précédents contiennent 18 scénarios reproduits. Avec les neuf nouveaux, 27 scénarios sont recensés ; il ne s’agit pas de 27 défauts indépendants.

## Vérifications et limites

- Nouveaux diagnostics : sept scénarios utilisant une base MySQL jetable et deux scénarios de routage d’interface.
- Preuves : `audit-etendu-incoherences-20261104.txt` pour huit cas, puis `audit-grades-bornes-et-controle-20261104.txt` pour le cas de borne de grade après correction de sa mise en place de test.
- Le premier montage du cas de grade tentait de créer deux situations du même agent dans un même mois. Le schéma a correctement refusé ce doublon. Le défaut de borne a ensuite été reproduit avec une seule situation valide terminée le 10/10, sans changer le schéma.
- 34 contrôles existants ont réussi sur les cas sélectionnés du tableau de bord en consultation d’archive, des gains/pertes et de la reprise mensuelle. Le contrôle natif de l’unicité agent/mois réussit également. Ces résultats ne certifient pas tous les parcours de ces modules.
- Scripts : `sme_python/build/audit_extended_inconsistencies_20261104.py`, et les scripts d’audit temporel précédents.
- Tous les enregistrements de test sont fictifs ; les schémas sont créés puis supprimés sur une instance locale isolée, vérifiée par son port et son répertoire de données. Les ACL sont simulées dans les profils temporaires ; ces essais ne certifient pas les protections Windows.

L’audit est ciblé et ne constitue pas une preuve d’absence d’autres défauts dans toute l’application.

## Ordre de correction proposé

1. Préserver les détails des diplômes et identifier précisément la ligne civile ou militaire à modifier.
2. Centraliser la projection des événements datés : mois d’effet, bornes des situations, mois clôturés et formats anciens.
3. Réutiliser cette projection lors de la création ou de la reprise d’un mois.
4. Déplacer correctement les événements lorsqu’une date est corrigée et harmoniser les lecteurs mensuels.
5. Aligner les informations de provenance et les libellés d’export sur leur contexte réel.
