Registre partagé des publications SME — migration du 8 novembre 2026

L’export courant « Situation numérique → Exporter le classeur Excel complet » est indépendant de ce registre. Il demande le mois et le fichier de destination, remplit le modèle officiel `SME_GEND_model.xlsx` et journalise le document. Le droit `effectifs.export` suffit ; aucune validation, clôture ou migration 0001–0003 n’est nécessaire à cet export. Le bouton de versions et validations a été retiré de cet écran. Les mécanismes décrits ci-dessous restent réservés au circuit documentaire avancé, activé explicitement ; ils ne sont pas un préalable à la production du classeur.

Le registre conserve dans MySQL les quatre composants exacts de chaque version : classeur, modèle, données typées et manifeste. Ils sont découpés en fragments de 1 Mo, avec taille et empreinte SHA-256. Un poste peut vérifier ou récupérer un document même si le dossier local du poste producteur est absent. Les copies locales du 6 novembre restent conservées et importables ; leur import crée un brouillon sans inventer une validation historique.

Chaque mois possède une numérotation centrale indépendante. Deux postes créant simultanément une version obtiennent deux numéros distincts. Réimporter la même version est idempotent ; le même identifiant avec un contenu différent est refusé.

Le parcours distingue trois états :

- `brouillon` : export enregistré, sans validation officielle ;
- `valide` : révision courante explicitement validée par un administrateur ;
- `remplace` : ancienne révision validée, conservée intégralement et reliée à sa remplaçante.

La première validation exige le droit dédié, la version de ligne chargée et l'absence de publication courante concurrente. Un remplacement exige un motif et l'identifiant de la publication courante attendue. État précédent, nouvelle validation, auteur, date, motif et audit sont enregistrés dans la même transaction. Les fichiers antérieurs sont conservés. Une erreur de miroir après un commit confirmé produit un avertissement de confirmation, sans inviter à recommencer la validation.

La validation et la clôture contrôlent une empreinte logique du mois. Sous une courte barrière exclusive, les projections connues sont rafraîchies, puis les sources mensuelles et le modèle archivé sont comparés aux données du document. Les dates de capture, chemins locaux, hôtes et révisions globales sans effet sur le mois ne font pas partie de cette empreinte. Une modification pertinente impose un nouvel export. Les écritures ordinaires prennent une barrière partagée et restent parallèles ; la génération Excel et la copie des fichiers ont lieu après libération de la barrière exclusive.

Les droits sont distincts : consultation, création, vérification, téléchargement, validation et remplacement. Créer ou télécharger exige également le droit d'export des effectifs. Valider ou remplacer exige un rôle administrateur. Les rôles existants doivent être examinés lors du déploiement : une nouvelle permission ne doit pas être confondue avec une autorisation déjà attribuée.

Application explicite du schéma

Les contrôles de capacité et l'affichage du plan ne créent aucune table. Les migrations additives sont `0001` (registre et barrière), `0002` (métadonnées temporelles, file de projections et index) et `0003` (mouvements réellement datés et projection d'appartenance mensuelle). Les anciennes dates de référence fabriquées lors d'import Access ne sont pas promues en dates effectives. Leur checksum couvre le SQL normalisé, les index déclarés et les contrats de colonnes/clés. Le journal `_sme_schema_migration` conserve leur état, auteur, sauvegarde préalable et résultat. Une migration interrompue nécessite une reprise explicite ; une définition déjà enregistrée dont le checksum a changé est refusée.

Avant application, la sauvegarde doit être complète, posséder un inventaire vérifié et une preuve SQL/configuration dans le même instantané, provenir du même UUID de serveur et de la même base, et contenir toutes les tables actuelles. Les tables préexistantes incompatibles ne sont pas adoptées. Colonnes, types, nullabilité, tailles, valeurs par défaut, clés primaires, unicités, index et relations attendus sont contrôlés avant la marque « appliquée ». Une restauration ou une altération de schéma est détectée lors du contrôle suivant ; le cache des capacités ne dure que le temps d'une transaction.

Depuis le dépôt, le plan peut être consulté sans mutation :

```powershell
.\.venv\Scripts\python.exe sme_python\scripts\migrate_schema.py --plan
```

L'application exige un compte applicatif administrateur autorisé et demande le mot de passe sans l'afficher :

```powershell
.\.venv\Scripts\python.exe sme_python\scripts\migrate_schema.py --apply --expect-database smegend --safety-backup "C:\Sauvegardes\avant_migration.zip" --maintenance-confirmed --username admin
```

Le nom de base et le fichier doivent correspondre au poste réellement déployé. `--resume` est réservé à une migration interrompue examinée, avec une nouvelle sauvegarde complète de son état courant. Les modifications DDL ne disposent pas d'un rollback transactionnel : les fichiers et le journal de reprise sont conservés en cas d'erreur. Les migrations ne suppriment ni table métier ni fichier Access.

Le verrou de métadonnées `schema_metadata_lock` empêche une initialisation ou une migration DDL de croiser un dump cohérent. Il est distinct de la barrière des écritures RH. Les sauvegardes complètes contiennent les tables du registre et les fragments des documents ; la restauration vérifie leurs empreintes, leurs relations, leur numérotation et leur révision courante avant publication.

Une restauration SQL partielle conserve l'état opérationnel actuel, y compris l'absence des clés de configuration : le mode de cycle et les métadonnées de transition ne peuvent pas être injectés par un ancien JSON ou par le SQL de `app_config`. Le contrôle utilise les collations MySQL des clés et recopie les lignes actives avec leurs métadonnées. Une sauvegarde complète antérieure aux extensions installées est refusée avant bascule si elle omet leurs tables ; elle peut être restaurée dans une base séparée pour consultation ou servir à une reprise explicite de référentiels. Une archive actuelle complète restaure ensemble les sources, les faits datés et le registre documentaire.

Le journal `_sme_schema_migration`, la barrière SQL, le registre central des publications et les métadonnées temporelles (`sme_temporal_change`, `sme_projection_dirty`) ne peuvent pas être restaurés seuls : leur remplacement exige une restauration cohérente de tout le schéma. Une restauration des seules tables documentaires pourrait supprimer les versions centrales récentes et réutiliser leur numérotation tout en conservant des données RH plus récentes ; elle est donc refusée. Les anciennes versions locales peuvent être réimportées par ajout comme brouillons. Le vrai retour à une sauvegarde complète conserve une sauvegarde préalable de l'état remplacé. Les deux tables des mouvements datés et de l'appartenance mensuelle doivent être restaurées ensemble, puis leurs contraintes et la cohérence de leur projection sont vérifiées avant bascule.

Un échec d'une migration strictement additive enregistre son état `failed` (ou conserve `running` si la connexion est perdue), sans maintenir une maintenance permanente après la sortie du traitement. Les fonctions nouvelles exigent l'état `applied` et refusent de travailler sur ce schéma partiel. Le fonctionnement antérieur peut continuer ; la reprise nécessite une nouvelle sauvegarde et `--resume` après examen de l'erreur.

Conditions de déploiement

Tous les anciens exécutables doivent être fermés et remplacés avant d'activer ce protocole sur les quatre postes. Un ancien client ne connaît pas la nouvelle barrière ; les écritures SQL directes d'un administrateur contournent également les contrôles applicatifs. Une empreinte n'est pas une signature indépendante face à un administrateur pouvant modifier simultanément la base et ses preuves. Cette migration ne doit donc pas être présentée comme une protection contre un DBA privilégié.

Ce registre est distinct de la sauvegarde et du mois de travail. Un téléchargement ou l'import d'une ancienne version ne ferme, ne réouvre et n'active aucun mois. La restauration de fichiers locaux et la restauration des données mensuelles restent des choix explicites.
