# Migration progressive — point de départ du 6 novembre 2026

Le point de retour est **Version finale avant migration — 2026-11-06 — 0.1.28**. La branche d'archive et le tag GitHub sont `archive/version-finale-avant-migration-2026-11-06` et `version-finale-avant-migration-2026-11-06`, commit `ea218d0`. Le dossier portable construit avant toute correction de migration se trouve dans `sme_python/dist/releases/20261106T125724140591Z/SME_Gendarmerie/`, sans ZIP.

La sauvegarde Git privée locale conserve l'historique antérieur, les fichiers suivis modifiés, les sources non suivies et les modèles originaux. Le contrôle de l'empreinte des sources correspond exactement au `BUILD_INFO.json` de l'exécutable. La copie publique exclut les données opérationnelles, les modèles contenant des données réelles et masque le mot de passe initial. Elle ne remplace pas une sauvegarde de la base MySQL de production.

La branche de travail est `migration/historique-publications-2026-11-06`, version de développement `0.2.0.dev0`. L'exécutable 0.1.28 ne contient pas les corrections suivantes. Aucun changement de schéma ni remplacement de données n'est lancé sur la production pendant ce travail.

## Première tranche

- La consultation d'un mois reste locale au poste. L'activation partagée, la clôture, l'import, la réouverture et les paramètres sont contrôlés dans les services. Un mois clôturé doit être réouvert avec un motif avant activation. La réparation de connexion au démarrage se limite à la connexion locale.
- Les fins de mission, congé et les retours de désertion utilisent une date réelle et une transaction commune : registre, position administrative et audit par agent. La situation affichée est revérifiée avant écriture. Les erreurs restent visibles et les agents en échec restent cochés. Une autre date ne remplace pas silencieusement un retour déjà validé.
- Les versions de stages et désertions incluent les références et sessions pour détecter les modifications concurrentes. La restauration mensuelle remappe les marqueurs de retour, de fin de stage et de position. Un vidage ou réimport qui laisserait un lien orphelin dans un autre mois est refusé ; un remplacement incompatible est annulé.
- L'avancement utilise une même date de référence pour les diplômes, promotions, échéances, filtres et affichage : fin du mois consulté, ou date explicitement demandée par un appelant. Les diplômes et promotions postérieurs sont exclus. Les données anciennes sans date restent des informations non datées, sans date inventée.
- F5 n'effectue plus un premier rechargement qui réinitialise les filtres avant le rechargement explicite. Un rendu de tableau de bord échoué reste signalé comme périmé et peut être relancé. Réappliquer le même zoom ne recalcule plus inutilement le style de toutes les pages.
- Les exports CSV des historiques neutralisent les formules. L'historique documentaire conserve un encodage réversible et accepte les anciens fichiers.
- L'export du classeur SME capture des données MySQL fraîches dans une lecture cohérente, conserve le modèle, les données typées et les octets exacts du classeur dans une nouvelle version. Voir `MIGRATION_PUBLICATIONS_SME_2026-11-06.md`.
- La sauvegarde complète inclut les versions des classeurs par une option distincte. La restauration vérifie leur contenu et ne remplace pas une version différente, y compris en cas de création concurrente. L'historique documentaire permet de récupérer le classeur exact sans relire les effectifs actuels. La restauration de mois sélectionnés reste une opération distincte et ne réimporte aucun document d'un autre mois.

Les tests utilisent uniquement des profils synthétiques et une instance MySQL jetable sur un port distinct (33329), avec contrôle du répertoire de données avant accès.

## Recette de cette tranche

- Exécutable 0.1.28 : les 12 contrôles du binaire figé passent. Les empreintes du binaire et des sources privées archivées correspondent à `BUILD_INFO.json`. Le bundle Git privé a été vérifié et les références distantes de l'archive publique relues.
- Suite unitaire : 2 422 cas répartis en deux lots. Les deux seuls échecs des lots finaux étaient la même ancienne fixture de congés, sans méthode de clé de sélection, dans deux contextes. Fixture actualisée ; son module complet repasse à 32/32. Les 2 420 autres cas passent dans les lots. La recette finale couvre ainsi tous les cas collectés, avec relance ciblée des fixtures corrigées.
- UI/actualisation/CSV/avancement : 101 tests ciblés, puis 22 tests UI après optimisation du zoom, réussis. Trois tests MySQL d'avancement couvrent notamment diplôme et promotion futurs, y compris le diplôme déjà présent dans un ancien snapshot. Le lot MySQL précédent F5/historique documentaire/avancement passe à 22/22.
- Droits : 40 tests MySQL de permissions, concurrence, rollback, réouverture et import réussis. Les fichiers unitaires de droits passent également.
- Activités : 27 tests MySQL de retours, concurrence, ambiguïtés et rollback réussis. Les 19 tests MySQL de sauvegarde/purge comprennent six nouveaux cas de liens et remappage ; 45 régressions Stage/temporal passent également.
- Exports : trois tests MySQL de capture cohérente, concurrence, cache périmé et export réel réussis. Un quatrième scénario sauvegarde puis restaure réellement les données et les versions, avec comparaison des octets. Le lot final de 122 tests unitaires publications/sauvegarde/restauration/configuration/réinitialisation/récupération de clôture passe.
- Relecture indépendante : parcours de congé concurrent confirmé corrigé ; création concurrente de fichier conservée ; chemins adverses rejetés. Les lots se recouvrent : leurs nombres ne doivent pas être additionnés pour calculer un total global.

Les derniers lots de fichiers ont été exécutés hors du sandbox pour permettre les contrôles Windows métier. Les profils temporaires ont des ACL héritées ; cette recette ne certifie pas le durcissement des ACL du profil Windows de déploiement. Des premières exécutions sous sandbox ont été interrompues ou ont refusé les ACL, puis les domaines concernés ont été revérifiés dans l'environnement adapté. Il n'y a eu aucun essai sur la production ni de recette de charge sur son réseau à quatre postes.

## Architecture cible et prochaines tranches

1. **Sécuriser l'existant et figer les documents produits** : première tranche ci-dessus. La validation officielle d'une publication reste une étape distincte d'un export.
2. **Mettre en place le registre partagé des publications** : mois ou intervalle, numéro de révision, auteur, validation, modèle, empreintes, emplacement partagé et récupération depuis tous les postes. Définir les états brouillon, validé et remplacé, en gardant les anciennes versions.
3. **Normaliser l'historique métier** : identifiant stable par agent, événements avec date d'effet et date de saisie, correction tracée d'une date de retour, et liens explicites aux sources. Préparer les nouvelles tables dans des migrations versionnées, à tester sur une copie. Conserver les observations Access mensuelles avec leur provenance ; ne pas déduire des dates précises absentes des sources.
4. **Recalculer uniquement les agents et périodes concernés** : les événements deviennent la source métier, les situations mensuelles des projections contrôlables. La clôture devient une validation de publication et ne copie plus indistinctement toute la carrière. Distinguer effectif à une date et mouvements sur un intervalle.
5. **Rapprocher avant bascule** : comparer les résultats par mois, structure, grade et position avec les archives et le modèle officiel, sur les mois importés et les corrections rétroactives. Mesurer temps d'ouverture, mise à jour, clôture, export et restauration sur un volume représentatif.

Les problèmes de cohérence des anciennes sauvegardes SQL/configuration et de la sauvegarde prise avant clôture restent à traiter dans une tranche dédiée. Il faut aussi éprouver l'interruption/reprise des imports et restaurations sur une copie représentative avant de changer leur architecture. La présente tranche ne prétend pas avoir terminé la migration ni garantir l'absence de tout défaut en production.

## Retour au point de départ

L'exécutable 0.1.28, son dossier portable et les modèles locaux sont conservés séparément de la branche de migration. Le bundle privé est sous `sme_python/build/backups/2026-11-06-before-migration/`. Il ne doit jamais être poussé vers un dépôt public. Le manifeste local identifie le commit original, le commit public, le SHA-256 du bundle, celui des sources et celui du binaire. Restaurer une version du code ne restaure pas les données MySQL : une sauvegarde de données distincte est indispensable avant toute future bascule sur la production.
