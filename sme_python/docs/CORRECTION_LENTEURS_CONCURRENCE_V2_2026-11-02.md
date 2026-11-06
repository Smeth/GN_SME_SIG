# V2 — correctif 0.1.3 : lenteurs et faux messages de maintenance

## Régression corrigée

La version 0.1.2 conservait un verrou MySQL unique pendant les opérations
ordinaires. Plusieurs lectures empruntaient aussi ce chemin. Une modification,
une lecture longue ou une écriture du journal pouvait ainsi bloquer d'autres
postes et produire « base occupée ou en maintenance », sans restauration réelle.

Les écritures ordinaires possèdent désormais chacune un verrou de session
indépendant. Le verrou commun ne sert qu'à leur enregistrement bref. Deux
postes peuvent donc modifier des agents différents simultanément. Les conflits
réels sur un même dossier restent contrôlés par les versions et les transactions.

Les principales lectures de recherche, de référentiels, d'historiques, de mois
et de journaux utilisent explicitement les connexions en lecture seule. Elles
ne demandent pas d'admission comme écriture et ne modifient pas le registre.

## Protection des vraies restaurations

Une restauration annonce toujours la maintenance dans MySQL et ferme les
nouvelles admissions. Elle attend ensuite la fin des écritures déjà enregistrées
avant de préparer et publier les données. Une session interrompue libère son
verrou propre ; ses métadonnées périmées sont nettoyées lors de la vérification.

Le registre technique `_sme_active_writer`, comme `_sme_maintenance_state`, est
exclu des sauvegardes et des tables remplacées par une restauration. Une
maintenance réellement interrompue ne s'efface pas automatiquement : sa reprise
reste soumise au contrôle et à la confirmation de récupération existants.

Une contention d'admission ordinaire est distinguée d'une maintenance confirmée
dans les messages. Le correctif ne transforme pas un incident de restauration
en autorisation d'écriture.

## Installation

Fermer l'application sur les deux postes, décompresser le nouveau portable V2
0.1.3 dans un dossier neuf et relancer son exécutable sur chacun des postes.
Conserver les paramètres de connexion propres aux postes et le même partage.
Ne pas lancer de restauration tant que tous les postes n'ont pas été mis à jour.
Ne pas recopier uniquement l'exécutable : conserver le dossier complet.

Ce correctif n'efface aucun agent, journal, compte ou mois. Aucune intervention
automatique sur la base réelle de l'utilisateur n'est effectuée par la préparation
du portable. La validation réseau sur les deux postes de travail reste nécessaire.
