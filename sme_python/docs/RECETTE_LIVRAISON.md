# Recette de livraison S.G.I DPF

Les essais ci-dessous utilisent une copie de test de la base et des agents
synthétiques. Consigner la version et le `source_sha256` de `BUILD_INFO.json`,
les postes, la version MySQL, le résultat et les éventuelles anomalies.

## Contrôles automatisés reproductibles

Depuis la racine du dépôt, sous Windows x64 avec Python 3.13 et les dépendances
du projet installées :

```powershell
python sme_python/scripts/run_release_checks.py --mysql-server 'C:/Program Files/MySQL/MySQL Server 8.0/bin/mysqld.exe'
```

Le serveur est créé sous `sme_python/build/release_checks_*/mysql`, écoute
uniquement sur `127.0.0.1` avec un port distinct de 3306, et est arrêté à la fin.
Chaque fixture vérifie son répertoire avant de créer ses propres bases
`impression_test_*`. Les journaux et `validation.json` sont conservés sous build.
Le lanceur isolé bloque les connexions externes et les écritures hors profil.

Pour la suite unitaire complète :

```powershell
python sme_python/scripts/run_isolated_tests.py tests --ignore=tests/integration -q
```

Installer l'extra `dev` dans l'environnement de test. Si le moteur Excel est
installé séparément sous `sme_python/build/test_dependencies`, passer aussi
`--test-dependencies sme_python/build/test_dependencies` au lanceur. Ce moteur
sert aux outils d'import et aux tests ; il n'est pas ajouté au portable.

L'option `--inherited-test-acls` sert uniquement aux répertoires synthétiques
dans un environnement restreint. Elle ne valide pas les protections Windows.
Pour une recette native des protections, exécuter sans cette option sur un
poste autorisant les opérations ACL. Un échec ne doit pas être présenté comme
une réussite ni corrigé en désactivant la protection des données.

## Construction

Depuis `sme_python`, exécuter `scripts\build_portable.cmd`. Si les dépendances
installées correspondent au verrou, ajouter `--use-current-environment`.
Ne pas éditer les sources pendant cette étape. Retenir uniquement une livraison
ayant un ZIP, `BUILD_INFO.json` et un `SELF_TEST.json` réussi, comprenant les
imports, les ressources, le journal et graphiques, le PDF et la connexion.
Copier le dossier complet extrait du ZIP sur chaque poste.

## Deux postes et partage réel — à exécuter sur place

Le kit `Test-ImpressionSmb.ps1` est inclus dans le portable. Sur A :

```powershell
powershell.exe -NoProfile -File .\Test-ImpressionSmb.ps1 -Role Start -Path 'Z:\Photos'
```

Sur B, utiliser le `SESSION_FOLDER` affiché par A :

```powershell
powershell.exe -NoProfile -File .\Test-ImpressionSmb.ps1 -Role Join -SessionFolder '\\serveur\partage\Photos\impression_smb_validation_UUID'
```

Consulter `a.result.json`. Les résultats locaux et le mode
`-AllowLocalSelfTest` ne valident pas deux postes réels. Si la politique Windows
refuse l'exécution, utiliser un script signé ou la procédure de l'administrateur.

1. Configurer les deux postes sur la même base de test et le même chemin UNC
   de médias, avec les comptes Windows habituels. Tester l'accès avec ces comptes.
2. Créer un agent synthétique sur A. Vérifier sa visibilité sur B après le cycle
   d'actualisation, puis modifier sa fiche sur B et contrôler la mise à jour sur A.
3. Ouvrir la même fiche sur A et B. Enregistrer deux modifications concurrentes :
   une version périmée doit produire un conflit explicite, sans écraser l'autre.
4. Ajouter une photo synthétique sur A et l'afficher sur B. Vérifier aussi deux
   lettres de lecteur différentes pointant vers le même partage UNC.
5. Saisir un titre de mise en page sans l'enregistrer. Déclencher une modification
   distante ; vérifier que le brouillon reste disponible et que la synchronisation
   différée est signalée. Tester également l'éditeur avancé.
6. Révoquer une session de test depuis l'autre poste. Vérifier la reconnexion
   requise, y compris lorsque le nombre de connexions simultanées est illimité.
7. Interrompre temporairement l'accès réseau du poste de test. Vérifier le message
   d'indisponibilité, l'absence d'écrasement et la reprise après rétablissement.

## Sauvegarde et restauration — base de test exclusivement

1. Noter les effectifs de deux mois, une fiche, ses événements et ses médias.
2. Créer une sauvegarde depuis l'application ; vérifier son contenu et conserver
   les empreintes des fichiers. Effectuer une modification synthétique identifiable.
3. Placer tous les clients de cette base de test en maintenance et fermer les
   autres sessions avant de restaurer. Le verrou SQL ne remplace pas cet arrêt.
4. Restaurer ; comparer les mois, fiches, événements, configurations et médias.
   Contrôler le journal de restauration et la procédure de récupération conservée.
5. Vérifier le refus d'une archive corrompue, d'un chemin hors archive et d'une
   restauration SQL sans maintenance, sans altérer l'état courant.
6. Vérifier les droits d'accès aux sauvegardes et fichiers temporaires avec un
   second compte Windows sans autorisation. Confirmer qu'aucun secret ne figure
   dans les arguments des processus, les journaux ou le portable distribué.

## Word, Excel et impression

Exporter un mois de test contenant accents, noms longs et plusieurs pages.
Contrôler les effectifs et rubriques de la liste des présents et de la situation
numérique. Ouvrir les documents Word avec la version installée sur les postes,
contrôler les sauts de page, en-têtes et signatures, puis imprimer une page de
chaque modèle utilisé. Le PDF synthétique automatique ne remplace pas cette recette.

## Fiche de résultat à remplir

| Vérification | Résultat / preuve | Poste / date |
|---|---|---|
| ZIP, empreinte, autotest du portable | À consigner | |
| Synchronisation entre deux postes | Non exécutée sur les postes réels | |
| Conflits et conservation des brouillons | Non exécutés sur les postes réels | |
| Médias UNC et coupure réseau | Non exécutés sur le partage réel | |
| Révocation des sessions | Non exécutée sur les postes réels | |
| Sauvegarde / restauration de la copie de test | À consigner | |
| ACL avec un second compte Windows | À consigner | |
| Word / Excel / papier | À consigner | |

Une anomalie bloquante doit être corrigée et son scénario rejoué avant la
diffusion générale. Les essais locaux automatisés et la recette sur place sont
deux preuves distinctes.
