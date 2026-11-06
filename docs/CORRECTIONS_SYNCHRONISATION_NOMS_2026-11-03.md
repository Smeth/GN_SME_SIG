# Synchronisation entre postes et présentation des noms — V2 0.1.6

## Synchronisation

La lecture du numéro de révision était effectuée toutes les dix secondes.
Une révision différente déclenchait un changement de source mensuelle global,
la remise en état des vues puis un rechargement explicite de la page courante.
Une écriture sur un poste pouvait donc déclencher des traitements lourds sur
tous les autres, même pendant une consultation ou une recherche.

Le contrôle de révision se fait maintenant en arrière-plan toutes les trente
secondes. Les changements sont regroupés en une notification persistante
« Synchronisation : mise à jour disponible ». Le contrôle ne recharge aucune
page, ne rebinde aucun service et ne vide pas les caches de la liste.

Le bouton Actualiser, F5 et le menu d'actualisation permettent de charger les
changements à la demande. Une saisie non enregistrée ou un dialogue ouvert
diffère cette action. Les contrôles de session et les protections d'écriture
concurrente restent distincts de cette actualisation visuelle.

Conséquence : l'écran peut présenter la version précédente jusqu'à une
actualisation volontaire. La notification l'indique. Le premier contrôle au
démarrage établit une référence sans provoquer un second chargement global.

## Noms et prénoms dans les limites d'âge

Le module affichait directement le champ historique unique contenant le nom
et les prénoms, souvent entièrement en majuscules. Les tableaux, impressions
et attestations Word/PDF appliquent désormais une présentation cohérente :
nom en majuscules, initiales des prénoms en majuscules, accents et traits
d'union conservés.

Les identités déjà présentées avec un nom composé en majuscules sont
respectées. Pour les archives entièrement en majuscules, la frontière entre
nom et prénoms n'est pas toujours déductible. Le bouton « Distinguer nom et
prénoms… » dans la fiche des attestations permet de confirmer cette frontière,
par exemple MBOUNGOU MAKITA / Jean Paul.

Ce choix est conservé dans la configuration MySQL partagée du module, avec
contrôle de version et publication d'une notification aux autres postes.
Il ne renomme pas l'agent : seuls les mots déjà présents peuvent être répartis.
La règle est liée à l'identifiant et au texte de l'identité ; elle est ignorée
si cette identité change. Les configurations et réimports ne dépendent pas
de la casse brute du nom pour conserver le réglage.

Sans précision explicite, la convention du dossier reste le premier mot comme
nom, avec reconnaissance des particules initiales courantes. Les noms composés
ambigus doivent être confirmés ; aucun dictionnaire de prénoms ne les devine.

Déployer la même V2 0.1.6 sur tous les postes. Les anciennes versions continuent
à appliquer leur ancien comportement d'actualisation et ne connaissent pas
le nouveau réglage des noms composés.

## Validation et livraison

- Suite générale : 2 025 tests réussis sur profil isolé. Les cas ajoutés
  ensuite pour le dialogue et l'attestation de nom composé ont également réussi.
- MySQL jetable : deux clients partagent la présentation du nom composé ;
  une écriture avec une ancienne version est refusée et la notification est publiée.
- Vérification visuelle du dialogue sur identité fictive et contrôles des
  attestations Word/HTML utilisées pour les PDF.
- Exécutable compilé : six autotests réussis, version et module embarqué vérifiés,
  empreinte du code source contrôlée. Aucun ZIP créé.

Livraison complète :
`sme_python/dist/releases/20261103T100636827501Z/SME_Gendarmerie`.

Capture : [nom et prénoms](assets/sync_names_20261103/nom_prenoms.png).
