CONSTRUCTION ET LIVRAISON — S.G.I DPF V2 — correctif 0.1.4

Environnement de construction validé : Windows x64, Python 3.13.
Depuis sme_python : scripts\build_portable.cmd

Le script utilise un environnement .venv-build et installe les versions exactes
de requirements-win-py313.lock. L'installation initiale nécessite un accès au
dépôt de paquets. Il n'installe rien dans l'environnement Python de l'utilisateur.

Si l'environnement courant correspond déjà exactement au verrou :
scripts\build_portable.cmd --use-current-environment

Pour livrer le dossier complet sans produire de ZIP :
scripts\build_portable.cmd --use-current-environment --no-zip

La recette, le hook Qt et qt_onedir.conf sont conservés dans packaging/.
Le dossier build/ contient uniquement les produits de construction et diagnostics.
Une modification des sources pendant la compilation empêche la publication.

Chaque construction produit un nouveau dossier dist/releases/<horodatage UTC>/.
Le portable installé ou une précédente livraison ne sont jamais remplacés.
Le ZIP n'est créé qu'après réussite des contrôles : imports, ressources, interface
utilisateurs, boucle de connexion, journal, graphiques et génération PDF.
BUILD_INFO.json identifie la version, les sources, les dépendances et l'exécutable.

Les photos des agents et les médias de l'ancien portable ne sont pas copiés.
Seuls les insignes de grade sont repris dans personnel_media/grades.
Les bases Access livrées sont des gabarits vidés et vérifiés. Les sources restent
inchangées. La préparation de ces gabarits exige DAO/Access Database Engine sur
le poste de construction ; le SQL de restauration exige les outils MySQL adaptés.
Les outils administratifs optionnels hors application utilisent l'extra [tools].

Déployer le dossier portable complet, pas l'exécutable seul. Conserver les profils
de connexion propres aux postes et leurs chemins de médias partagés.
La recette entre plusieurs vrais postes et les essais de coupure réseau restent
à effectuer dans l'environnement d'exploitation avant une diffusion générale.
La procédure détaillée est dans docs/RECETTE_LIVRAISON.md et dans le portable.
Les contrôles automatisés de livraison sont relançables depuis la racine :
python sme_python/scripts/run_release_checks.py --mysql-server "C:/Program Files/MySQL/MySQL Server 8.0/bin/mysqld.exe"
