Corrections des sessions et des diplômes — 0.1.16

Une connexion sur un autre compte ne doit pas déconnecter un utilisateur déjà
connecté. Le registre des sessions utilise désormais l'heure UTC du serveur
MySQL pour les expirations et le nettoyage. Auparavant, l'horloge du poste qui
se connectait pouvait entraîner le nettoyage de sessions d'autres comptes.
Ce défaut a été reproduit avec deux comptes et des décalages de ±48 heures.
La capture seule ne permet pas de confirmer le décalage réel des postes.

Une nouvelle connexion ne supprime plus un jeton existant du même profil.
L'identifiant du profil est lié à l'ordinateur afin qu'une copie de profil ne
confonde pas deux postes. Les limites de connexions simultanées restent
appliquées au compte concerné ; atteindre une limite refuse la nouvelle
connexion sans fermer les anciennes. Les révocations administratives et les
expirations réelles restent actives.

L'ajout d'un nouveau diplôme est autorisé aux comptes ayant les droits
d'édition SME ou du personnel, dans le module Stages, Édition SME et la fiche
complète. Modifier ou supprimer les anciens historiques reste réservé aux
administrateurs. L'opération contrôle les droits côté service également.

Les droits d'ajout sont vérifiés avant la fin de stage lorsqu'un diplôme est
demandé. La fin de stage, la position et le diplôme partagent une transaction
par agent : un échec annule l'ensemble pour cet agent. Le compte rendu indique
explicitement qu'aucune modification n'a été enregistrée pour les agents en
échec. Les succès des autres agents sont comptés séparément.

Les diplômes refusés avant ce correctif ne sont pas créés rétroactivement :
leur libellé et leurs dates doivent être saisis et enregistrés. L'ajout d'un
diplôme pour un agent déjà revenu à Présent est couvert par les tests.

Validation : tests unitaires de droits et d'interface ; tests MySQL sur bases
synthétiques jetables ; contrôles intégrés de l'exécutable. Les profils et les
données d'exploitation ne sont pas modifiés par ces essais.

Livraison : dossier portable complet, sans ZIP de distribution. Garder
l'exécutable avec ses bibliothèques et ressources.
