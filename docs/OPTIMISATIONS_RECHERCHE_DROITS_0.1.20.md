Liste générale, Édition SME et droits opérateur — 0.1.20

Mesures sur données synthétiques locales

Sur 20 000 agents et 35 grades, le filtrage médian après le premier passage
passe de 4,40 s à 0,19 s pour la Liste générale, et de 4,26 s à 0,19 s pour
Édition SME. Le premier filtrage de la Liste générale, qui prépare les textes,
prend environ 0,90 s. Ces mesures portent sur le filtrage, pas sur la latence
d'un réseau d'exploitation. Les mêmes recherches conservent leurs résultats.

Le chargement MySQL de 20 001 situations et 40 000 diplômes prend environ
0,80 s sur l'instance locale de test. La requête précédente dépassait le délai
de lecture d'une minute dans ce scénario. Les recherches de diplôme utilisent
maintenant l'index par agent même si les statistiques MySQL sont défavorables.
L'index est ajouté automatiquement s'il manque, sans supprimer les données.

Changements

Les correspondances des grades et les textes de recherche sont réutilisés.
Le cache couvre un gros effectif et reste indexé par les valeurs des champs :
une modification ne réutilise pas l'ancien texte. Les critères de la Liste
générale sont publiés ensemble au lieu de refiltrer trois fois la liste.
Édition SME et Rechercher/Remplacer attendent une courte pause de saisie avant
de lancer une recherche ; Entrée déclenche la recherche immédiatement.
Les compteurs ne sont plus recalculés pour chaque groupe de lignes masquées.
Un export et une action groupée appliquent la recherche en attente avant
de déterminer leurs lignes, afin de ne pas utiliser les anciens résultats.

Droits et enregistrements

Les opérateurs ayant les droits d'édition peuvent ajouter un diplôme, une
mission, un stage, une maladie, un congé, une désertion ou une fiche de position.
Les permissions sont contrôlées côté service, avec un message explicite en cas
de refus. Les transactions position + fiche complémentaire sont conservées.
Un compte en lecture seule est refusé avant écriture. La correction et la
suppression des anciens historiques restent soumises aux règles administrateur.

Les essais utilisent une vraie session applicative opérateur et vérifient les
enregistrements MySQL, la fin de stage avec diplôme, ainsi que les corrections
rétroactives dans les mois clôturés. Les dates, références et événements
postérieurs conservent leurs règles de sélection.

Sur demande explicite de l'utilisateur, le rôle « custom » de la base
configurée a reçu les permissions effectifs.liste, effectifs.edition,
effectifs.sme_dpf et situation.stages. Ses autres permissions, son nom et les
comptes associés sont conservés. Aucun droit d'administration n'est ajouté.
La modification de configuration est journalisée et versionnée pour les autres
postes ; elle ne constitue pas une règle imposée aux rôles des autres bases.

Base de production et livraison

Il n'est pas nécessaire de vider la base ni de réimporter les effectifs.
Les données existantes sont conservées. Les essais de performance et
d'enregistrement utilisent exclusivement des bases synthétiques jetables.
Seule la modification de droits demandée ci-dessus a été appliquée à la base
configurée. Aucune fiche de personnel n'a été modifiée par ces essais.

La livraison est un dossier portable complet, sans ZIP de distribution.
Conserver l'exécutable avec les bibliothèques et ressources de son dossier.
