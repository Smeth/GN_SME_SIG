Première tranche : versions des exports SME — 6 novembre 2026

L'export « Situation numérique → Exporter le classeur Excel complet » conserve désormais chaque résultat dans un nouveau dossier. La clôture, l'import Access et le format du modèle officiel restent des mécanismes distincts. Générer un export ne valide pas officiellement un mois.

Le dossier est situé sous le répertoire d'archives configuré, dans `publications_sme/<identité de base>/<mois>/<identifiant UTC et UUID>/`. Il contient :

- `classeur.xlsx` : les octets exacts du fichier exporté ;
- `modele.xlsx` : le modèle exact utilisé, incluant ses feuilles masquées ;
- `donnees.json` : les données typées utilisées pour générer le document, y compris détails, mouvements, références et récapitulatif précédent ;
- `manifest.json` : auteur, application, mois, instant de capture UTC, provenance des imports mensuels disponibles, empreintes SHA-256 et tailles.

La capture lit directement MySQL, sans le cache de l'interface. Toutes ses lectures partagent une transaction InnoDB en lecture seule avec un instantané cohérent et une isolation REPEATABLE READ explicite. Cette transaction est terminée avant le calcul et l'écriture du classeur. Les écritures des autres postes restent possibles. Des tables non transactionnelles entraînent un refus explicite de l'export cohérent.

Les détachés proviennent des mêmes lignes capturées que le reste des effectifs. Les mouvements doivent provenir de la même base. Les fichiers Access historiques ne sont ni réécrits ni renommés par ce parcours. La provenance décrit les lots d'import connus ; elle ne prétend pas reconstituer des dates d'événements absentes des anciennes observations mensuelles.

Chaque dossier utilise un identifiant unique ; une nouvelle génération n'écrase jamais un dossier existant. Le modèle source et les destinations situées dans le répertoire de versions sont protégés contre le remplacement. Le fichier choisi par l'utilisateur reste une copie de travail remplaçable. En cas d'échec de cette copie après création de la version, l'erreur indique le classeur conservé.

Le message de réussite affiche l'identifiant et le dossier conservé. L'historique documentaire central enregistre l'identifiant et les empreintes de la version. Un double-clic sur son entrée affiche le reçu complet et propose « Récupérer le classeur archivé… » aux comptes ayant le droit d'exporter les effectifs. Cette récupération compare les empreintes au journal central, vérifie les fichiers puis produit une copie atomique du classeur exact, sans recalcul depuis la base courante.

Le service `sme_export_revision.verify_export_revision` contrôle les fichiers ; `read_export_dataset` relit le jeu de données typé ; `retrieve_export_revision` récupère le classeur exact. La consultation/récupération d'une version ne restaure aucune donnée opérationnelle. Si le classeur et sa version sont enregistrés mais que le journal échoue, l'interface annonce explicitement un export enregistré avec historique à vérifier ; elle demande de conserver le résultat et précise qu'une nouvelle génération n'est pas nécessaire. Une erreur de miroir après commit est distinguée d'un journal central non confirmé.

Limites assumées de cette tranche :

- Par défaut, le répertoire d'archives appartient au profil Windows du poste. Pour rendre les fichiers accessibles aux quatre postes, il faut un même dossier partagé et des droits de fichiers adaptés. Une entrée dans le journal MySQL ne rend pas accessible un chemin local d'un autre poste.
- L'identifiant est unique, sans numérotation centrale séquentielle. Il n'y a pas encore d'écran de validation officielle, de signature ni de registre SQL de publication partagé.
- L'application ne modifie pas ses anciennes versions. Un administrateur du disque peut toujours modifier ou supprimer les fichiers ; les empreintes détectent une altération des fichiers par rapport au manifeste. Ce mécanisme n'est ni une signature cryptographique ni une sauvegarde de toute la base.
- Les versions contiennent des données de personnel : elles suivent les protections et sauvegardes du dossier d'archives. Elles ne doivent pas être ajoutées au dépôt Git.
- Cette tranche ne transforme pas encore toute la carrière en événements datés et ne modifie pas la logique d'import des mois anciens. Cette transformation doit être préparée sur une copie de la base puis rapprochée avec les documents historiques.

Aucune nouvelle table ni migration DDL n'est exécutée par cet export. Les prochaines étapes sont un registre partagé et une validation explicite des publications, puis la séparation complète des événements opérationnels, projections mensuelles et documents publiés.

Les sauvegardes complètes incluent les versions présentes dans le dossier d'archives de ce poste ou dans son dossier partagé. L'option distincte « Versions archivées des classeurs SME » permet de les inclure ou de les restaurer sans restaurer les anciens JSON locaux ; elle est cochée par défaut. Les anciennes sauvegardes restent lisibles. L'inventaire et chaque groupe modèle/données/classeur/manifeste sont vérifiés avant application. Une version existante identique est conservée ; un même identifiant avec des octets différents bloque la restauration. Les nouveaux fichiers utilisent une publication exclusive avec journal de retour arrière. Les versions importées gardent leur provenance d'origine, et une sauvegarde suivante les conserve même après changement de serveur/base. L'historique sait retrouver leur nouvel emplacement local.

La restauration de mois sélectionnés applique uniquement les données des mois choisis. Elle ne restaure pas automatiquement les documents publiés présents dans la sauvegarde : la récupération des versions documentaires reste une opération distincte dans la restauration par composants. Aucune restauration de ces fichiers ne valide un mois ni ne modifie son état ouvert/fermé.

Référence technique : [documentation officielle MySQL, START TRANSACTION](https://dev.mysql.com/doc/refman/8.0/en/commit.html).
