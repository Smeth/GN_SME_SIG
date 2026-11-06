# Audit des dates : congés, maladies et positions administratives

État vérifié dans les sources correspondant au portable 0.1.14. Cet audit ne contient pas de correctif applicatif.

## Résultats confirmés

| Parcours | Constat | Conséquence |
|---|---|---|
| Congé futur saisi depuis septembre | Un congé du 1er au 15 octobre reste lié à septembre | Le lecteur du registre de septembre le retrouve ; celui d’octobre ne le retrouve pas automatiquement |
| Congé rétroactif | Un congé d’août saisi en septembre reste lié à septembre | La date ne détermine pas le mois de rattachement |
| Congé couvrant septembre et octobre | Une seule période SME est enregistrée | Il n’existe pas de reprise automatique de la fiche dans le lecteur d’octobre |
| Maladie ajoutée manuellement dans le dossier | La ligne est enregistrée avec `sme_periode_id = NULL` | L’historique du dossier la retrouve ; les lecteurs mensuels stricts l’excluent, y compris pendant les mois couverts par ses dates |
| Position administrative datée | Le changement est appliqué immédiatement au mois affiché | Une position future peut modifier septembre avant sa date de début ; une saisie rétroactive ne recalcule pas les autres mois |
| Action groupée de mission future | Les deux agents testés passent en Mission en septembre pour un départ le 1er octobre | L’enregistrement est réel et le compteur de réussite est correct ; le rattachement temporel est incohérent |
| Ajout de congé depuis la fiche complète | La boîte reçoit le mois actif global plutôt que le mois de la fiche consultée | Avec septembre consulté et novembre actif, le congé est préparé avec novembre comme période SME |

Les événements examinés sont conservés dans les registres ou historiques. Les défauts portent sur le rattachement mensuel, la sélection des fiches et la projection des positions selon les dates.

## Vérification

11 scénarios de diagnostic ont confirmé les comportements actuels : 10 scénarios utilisant une instance MySQL isolée et 1 scénario de routage de la boîte de saisie. Les dates futures, rétroactives et les intervalles couvrant deux mois ont été examinés pour les trois familles. Les assertions réussissent parce qu’elles reproduisent les anomalies ; ce ne sont pas des validations de corrections.

- Preuve : `audit-absences-positions-dates-20261104.txt`.
- Script : `sme_python/build/audit_absences_positions_dates_20261104.py`.
- Données fictives uniquement ; aucun compte, paramètre ou enregistrement de production modifié.
- ACL simulées et droits hérités dans les profils de test uniquement ; ces essais ne certifient pas les protections Windows.

## Périmètre

Les constats précédents sur les diplômes, affectations, missions, désertions et le repli des fiches de stage restent distincts et non corrigés dans 0.1.14. La généralisation de l’enregistrement selon les dates demeure à réaliser pour ces parcours. Ce contrôle ciblé ne certifie pas l’ensemble des fonctions de l’application.
