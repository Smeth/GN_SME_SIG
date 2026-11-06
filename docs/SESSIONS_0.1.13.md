# Reprise des sessions après indisponibilité — 0.1.13

Deux comportements pouvaient provoquer des reconnexions inutiles : une erreur pendant le contrôle périodique supprimait la session locale ; un échec de lecture de la configuration MySQL pouvait également renvoyer une configuration vide, interprétée comme la disparition du compte.

## Correction

- Le contrôle d’une session authentifiée utilise une lecture stricte de la configuration. Une erreur SQL n’est plus remplacée par une liste de comptes vide.
- Lorsqu’une vérification échoue, les saisies et le jeton restent en mémoire. L’interface masque les données et bloque les interactions ; les permissions et les actions dépendant d’une session vérifiée sont suspendues.
- Le contrôle périodique reste actif. Après rétablissement de la connexion, le compte, ses révisions et le jeton existant sont revérifiés. La session reprend automatiquement si ces contrôles réussissent, sans créer de nouveau jeton.
- Une expiration réelle, une révocation, un compte désactivé ou un changement de révision exigent toujours une reconnexion. L’attente d’une connexion ne prolonge pas le délai d’inactivité.
- Un contrôle plus ancien ne peut plus raccourcir une expiration déjà prolongée dans le registre MySQL. La ligne est verrouillée pendant sa vérification et sa mise à jour.
- Les vraies demandes de reconnexion utilisent le titre « Session verrouillée ». Une panne temporaire affiche « Connexion temporairement indisponible » et annonce la nouvelle tentative automatique.
- Le premier échec d’un épisode d’indisponibilité est journalisé avec sa cause technique, sans répéter ce message à chaque contrôle.

Le délai d’inactivité configuré et la politique de sessions simultanées ne sont pas modifiés.

## Vérification

91 tests ciblés hors base et 18 essais MySQL réussis. Les 8 tests de reprise ont été rejoués après les derniers ajustements d’interface. Les scénarios couvrent la panne de lecture des comptes, la panne du registre, la reprise avec le même jeton, la révocation pendant une panne, les changements de compte, l’expiration réelle et la conservation de l’expiration lors d’un contrôle obsolète.

Les essais utilisent une instance MySQL et des profils temporaires isolés. Ils ne modifient aucun compte ni paramètre de production. Les restrictions ACL sont simulées dans ces seuls profils ; ces essais ne certifient pas les protections Windows.

## Installation

Fermer l’application, extraire tout le ZIP et lancer SME_Gendarmerie.exe. Cette version inclut les corrections précédentes des enregistrements, stages et actualisations.
