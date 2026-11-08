# Cycle RH et publications SME — 8 novembre 2026

Cette version prépare le remplacement du cycle fondé sur la copie des archives Access par un cycle fondé sur des situations mensuelles matérialisées et des publications SME validées. Le mode historique reste le mode par défaut d’une base existante. Aucun changement de mode ni de schéma de production n’est déclenché par le simple lancement de l’application.

## Ce qui change pour le travail quotidien

Dans **Situation numérique**, un export fidèle au modèle devient une version centrale numérotée du mois. Le registre permet de vérifier, télécharger, valider et remplacer cette version. Les anciennes versions restent conservées avec leurs documents, données, modèle et empreintes. Un autre poste peut les récupérer depuis MySQL sans accéder au disque du poste qui a produit le document.

Une publication validée et encore conforme aux données du mois permet ensuite de fermer ce mois et de préparer le suivant. Cette préparation copie la situation mensuelle utile, puis applique les changements réellement datés. Elle ne duplique pas les missions, stages, diplômes et autres historiques. Elle refuse d’écraser un mois existant contenant des données ou une archive importée. Les contrôles de concurrence utilisent la version du mois et de la publication attendue ; une modification concurrente impose un rechargement.

La création du classeur Excel se fait après libération du verrou de capture. Les écritures RH ordinaires peuvent se dérouler en parallèle entre postes ; seules les captures, validations et transitions nécessitant une vue commune prennent une barrière exclusive.

## Dates réelles et archives Access

Un fichier Access mensuel apporte une **observation du mois**. L’application conserve sa provenance. Une date de référence de gain/perte provenant de l’ancien import pouvait être le premier ou le dernier jour fabriqué du mois : elle ne devient pas une date réelle de départ ou d’arrivée.

Le registre **Mouvements datés**, accessible dans Gains & pertes, demande un agent, GAIN/PERTE, une nature et une date réelle. Réf/note et précisions sont facultatives. La date ne peut pas être future. Une saisie hors du mois de travail exige un administrateur autorisé et un motif. Corriger ou supprimer exige également ces droits et un motif. Deux mouvements pour le même agent le même jour sont refusés afin de ne pas inventer leur ordre.

Dès que de vrais mouvements datés existent, l’ancienne reprise et l’ancienne clôture demandent le passage au cycle documentaire : elles ne peuvent pas recopier un agent parti en ignorant sa projection. Une fois le cycle documentaire actif, la préparation du mois suivant passe par sa clôture contrôlée. L’import d’une ancienne archive Access sans clôture reste possible.

Un fait daté de 2025 peut être enregistré même sans archive de 2025. Il corrige les projections des mois connus concernés ; il ne crée pas d’archive de 2025. Les identifiants agents sont canoniques, distincts des anciens numéros Access. Un départ masque la situation dans les effectifs concernés sans supprimer sa ligne, son dossier ou sa provenance. La projection conserve l’observation initiale pour permettre l’annulation d’une correction.

Les mutations conservent leur date d’effet, leur date d’enregistrement, leur auteur, leur motif et leurs états avant/après. La file d’invalidation cible les agents et les composantes concernés. Les dates absentes restent absentes. Une correction peut rendre une publication déjà validée obsolète : la publication antérieure reste consultable, mais une nouvelle version doit être produite et validée avant la clôture.

Les retours de stage, mission, maladie ou congé peuvent être rectifiés depuis la fiche complète avec une date réelle et un motif. Ces corrections ne suppriment pas le diplôme déjà obtenu. Les règles de priorité du diplôme affiché dans la SME restent celles du dossier : enregistrer un diplôme ne signifie pas remplacer systématiquement un diplôme militaire plus récent.

## Rapports sur un intervalle

**Situation numérique → Mouvements sur une période** produit un classeur distinct du document SME mensuel officiel. Il contient les événements effectivement datés, les observations identiques regroupées et les registres présentant des dates absentes. Les observations mensuelles Access de gain/perte sont comptées séparément, sans date réelle inventée.

L’effectif de référence est celui du dernier mois observé dont la fin précède ou correspond à la fin de l’intervalle. Il est daté explicitement. Le rapport n’additionne pas des effectifs mensuels et ne prétend pas reconstituer un effectif quotidien exact lorsque les sources ne le permettent pas. Deux valeurs différentes pour un agent, un registre et une date sont signalées pour vérification. Au-delà de 50 000 observations, une période plus courte est demandée, sans troncature silencieuse.

## Sauvegarde et restauration

Une sauvegarde complète capture le SQL et la configuration partagée depuis la même transaction cohérente. L’identité du serveur et de la base est enregistrée. Les fichiers externes sont capturés séparément et chacun reçoit une taille et une empreinte SHA-256 ; leur capture ne constitue pas une transaction distribuée avec MySQL. Les documents centraux et leurs fragments font partie du SQL.

Une restauration complète est préparée et contrôlée avant la bascule, notamment pour le registre documentaire et la projection des effectifs. Une sélection SQL partielle ne peut pas mélanger des faits datés restaurés avec une projection retenue incohérente. Une ancienne sauvegarde complète ne contenant pas les extensions installées se restaure d’abord dans une copie compatible examinée, plutôt que de mélanger silencieusement deux architectures. Une restauration partielle de référentiels ou de préférences conserve les clés opérationnelles de la base courante, y compris leur absence : elle ne peut pas activer le nouveau cycle par accident.

Une restauration mensuelle remplace les mois sélectionnés, remappe les identités et vérifie les autres mois. Les vrais mouvements datés sont des faits durables partagés ; leur restauration refuse les contradictions avec une correction, une suppression journalée ou une chronologie déjà présente. Un conflit est expliqué avant validation et provoque l’annulation de l’opération. Les anciennes publications restent des preuves conservées ; elles ne sont pas une commande de remplacement des données RH.

## Déploiement ordonné

1. Fermer les quatre postes et conserver la version finale avant migration du 6 novembre ainsi qu’une sauvegarde complète privée vérifiée. Les anciens exécutables ne connaissent pas la nouvelle barrière d’écriture.
2. Restaurer une copie de travail, capturer les rapprochements des mois de référence, consulter le plan puis appliquer les migrations explicites `0001`, `0002` et `0003`. Suivre les commandes et conditions du [registre SME](MIGRATION_REGISTRE_SME_2026-11-08.md). Un DDL interrompu se reprend explicitement ; son rollback transactionnel n’est pas promis.
3. Comparer les effectifs, agents et champs avant/après. Examiner les contradictions des données historiques au lieu d’inventer des dates pour les résoudre. Vérifier un export avec le modèle local réel et un aller-retour de sauvegarde sur la copie.
4. Examiner les permissions des rôles existants. Les droits de publication, correction des retours et mouvements historiques, téléchargement et changement du cycle sont distincts. Validation, remplacement, changement du cycle et corrections historiques exigent un administrateur autorisé. Les rôles personnalisés ne reçoivent pas automatiquement les nouveaux droits.
5. Installer la même version compatible sur les quatre postes. Produire et valider la publication du mois courant, puis activer explicitement le cycle documentaire avec un motif et la confirmation de mise à jour de tous les postes.
6. Vérifier la fermeture et l’ouverture du mois suivant, le registre partagé et les droits d’un opérateur sur les postes clients avant la reprise générale.

La fonction d’activation refuse l’absence de confirmation des postes. Le retour à l’ancien système nécessite un plan de retour coordonné du code **et** des données ; remettre un ancien exécutable sur une base déjà utilisée par le nouveau cycle ne constitue pas un retour contrôlé. Les accès SQL directs d’un administrateur restent hors des garanties de concurrence applicatives.

## Rapprochement en lecture seule

Le script ne modifie pas les données ; les fichiers produits contiennent des données de personnel et doivent rester privés.

```powershell
.\.venv\Scripts\python.exe sme_python\scripts\reconcile_migration.py --mois 2026-11 --database copie_sme --capture "C:\Sauvegardes\avant.reconciliation.json" --report "C:\Sauvegardes\capture.json"
.\.venv\Scripts\python.exe sme_python\scripts\reconcile_migration.py --mois 2026-11 --database copie_sme --compare "C:\Sauvegardes\avant.reconciliation.json" --report "C:\Sauvegardes\rapport.json"
```

Les résultats de test sur données synthétiques sont consignés séparément. Ils ne remplacent pas la recette sur les données réelles, les quatre postes et leurs droits Windows. Cette étape de développement ne génère pas d’exécutable et ne migre aucune base de production.
