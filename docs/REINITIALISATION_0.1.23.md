# Reinitialisation complete - 0.1.23

Sauvegarde et restauration > Reinitialisation complete > Reinitialiser l'application.

Deux choix independants : conserver les utilisateurs/roles/mots de passe ; conserver l'historique des documents. Si les utilisateurs sont supprimes, un nouvel identifiant de super administrateur et un mot de passe confirme sont obligatoires.

La confirmation indique le serveur, le port et la base exacte. La maintenance bloque les ecritures sur tous les postes avant une nouvelle sauvegarde complete validee. Toutes les tables metier, referentiels et reglages partages sont vides ; les schemas et mecanismes techniques restent fonctionnels. Les identites, les periodes, les stages, les diplomes, les grades et les taches sont supprimes. Les sessions sont invalidees. Une nouvelle trace de reinitialisation et les marqueurs techniques indispensables sont conserves. Les anciens fichiers locaux ne sont pas reimportes automatiquement.

Les archives mensuelles, photos/insignes et journaux du poste sont retires avec copies de recuperation privees et manifeste a cote de la sauvegarde. Les fichiers propres aux autres postes ne sont pas effaces a distance. Les documents deja exportes et les sauvegardes restent disponibles. L'option historique concerne le journal central des documents, pas la suppression de ces documents externes.

Les suppressions SQL sont transactionnelles. Un echec restaure la base et les fichiers retires. Si la recuperation des fichiers echoue, la maintenance reste bloquee et le dossier de recuperation est conserve. Aucune reinitialisation de production n'a ete executee pour developper ou verifier cette option.
