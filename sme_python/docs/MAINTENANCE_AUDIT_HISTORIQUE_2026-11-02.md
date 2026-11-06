# Maintenance, audit, historique et consultations — version 0.1.2

> Historique de la version 0.1.2. Le verrou global décrit ici a provoqué une
> régression de concurrence, corrigée en V2 0.1.3. Voir
> `CORRECTION_LENTEURS_CONCURRENCE_V2_2026-11-02.md` pour le mécanisme actuel.

## Maintenance centralisée

Une restauration SQL ou une restauration de configurations partagées annonce
la maintenance dans MySQL, attend la fin des écritures déjà engagées puis
refuse les nouvelles écritures des autres clients pendant l'opération.
Les transactions d'écriture passent par un verrou commun à cette base,
indépendant du mois affiché et de l'alias utilisé pour joindre le serveur.
Le verrou reste détenu jusqu'au véritable commit/rollback, même si un dépôt
effectue un commit explicite. Les copies/bascules de médias partagés,
l'initialisation du schéma et les opérations SQL dédiées participent au contrôle.

L'état durable `_sme_maintenance_state` reste dans la base active : il n'est
ni sauvegardé ni remplacé par une archive. Une interruption dont la bascule
n'est pas résolue laisse les écritures bloquées. Une connexion dédiée au
traitement conserve aussi un verrou de travail, pour qu'une perte de la
connexion de coordination ne permette pas une reprise pendant un traitement
encore actif. Les étapes de préparation et de publication vérifient leur verrou.

Dans Sauvegarde & restauration, « État de maintenance » consulte cet état.
« Lever une maintenance interrompue » exige une confirmation de vérification
du journal de récupération et refuse de lever une opération encore active
ou dont l'identifiant a changé. La reprise est journalisée avant réouverture.
Ne pas utiliser ce bouton pour contourner un incident non résolu.

Les sessions valides restent contrôlées en lecture seule pendant la maintenance,
sans prolonger leur validité ni ignorer une révocation. Le test de disponibilité
MySQL et les lectures de configuration n'exigent pas un droit d'écriture.

**Déploiement : mettre tous les postes à jour avant de s'appuyer sur ce contrôle.**
Il coordonne les clients de l'application à jour ; une ancienne version ou un
outil SQL externe ne participe pas à ce protocole. Les écritures sont sérialisées
à la frontière des transactions. Une opération occupée plus de cinq secondes
peut refuser une autre écriture avec un message explicite ; la saisie doit être
conservée et relancée après la fin de l'opération. La maintenance attend au plus
quinze secondes les écritures déjà engagées avant d'annuler son démarrage.

La création initiale de la table de contrôle exige les droits de création de
schéma habituels de l'application. Une fois créée, les clients disposant des
droits de lecture/écriture nécessaires n'ont pas besoin de la recréer.

## Audit après restauration

Le journal courant est conservé par défaut dans l'API et les restaurations
complètes/partielles de l'interface. Il concerne toute la base, donc tous ses
postes. Les miroirs locaux sensibles et leurs files de livraison en attente
sont conservés avec lui. Une restauration SQL prépare sa propre trace
`database_restored` dans les tables qui seront publiées par la bascule atomique.
Les restaurations de configurations partagées écrivent `configuration_restored`
dans leur transaction. Une reprise de maintenance écrit `maintenance_recovered`.

Remplacer volontairement l'audit reste possible avec une sélection explicite.
L'interface demande alors une seconde confirmation spécifique à l'effacement
des traces postérieures à l'archive, pour tous les postes. Les copies de
récupération existantes restent disponibles. Aucune purge automatique de l'audit
n'a été ajoutée.

## Historique métier

Vider un diplôme ou sa date depuis la liste mensuelle est refusé avec un message
orientant vers Dossier agent → Diplômes. Cette règle évite de laisser croire
qu'un événement historique a été supprimé alors qu'une projection peut le
réafficher. Les ajouts depuis la liste restent possibles.

Les corrections et retraits explicites du dossier enregistrent la version
antérieure complète et les nouvelles valeurs dans l'audit de la même transaction.
Le retrait d'un diplôme précise qu'il touche l'historique et peut affecter les
projections mensuelles ; ce n'est pas un masquage limité à un mois. Les contrôles
de droits et de version du dossier restent appliqués.

## Consultations

Le journal global couvre désormais les sections effectivement ouvertes,
les onglets visibles des dossiers et les résultats de recherche globale affichés,
en plus des ouvertures de dossiers et exports déjà tracés. Le préchargement des
pages, un rafraîchissement de la même section et une navigation refusée ne
produisent pas une consultation supplémentaire.

L'identité et le mois de vue sont capturés en mémoire avant l'envoi en tâche
de fond. Sans session applicative, le compte Windows est utilisé et identifié
comme tel. Un mois non disponible reste vide plutôt que d'être inventé.
Les frappes clavier et le texte libre des recherches ne sont pas enregistrés.
Un échec d'enregistrement est signalé dans l'écran ; il n'est pas présenté comme
une trace confirmée. Ces traces documentent l'ouverture d'un écran ou d'une
rubrique, pas la lecture humaine de chaque ligne.

## Recette sur site

Vérifier avec deux postes mis à jour : une restauration bloque les modifications
de l'autre poste, les brouillons restent disponibles, les sessions valides ne
sont pas fermées à cause de la maintenance, puis les écritures reprennent après
succès. Vérifier aussi la procédure d'incident sur une copie de test. Les essais
automatisés sur une instance MySQL jetable ne remplacent pas cette recette réseau.
