**Audit global de S.G.I DPF — 6 novembre 2026**

Sources examinées : application Python/PySide6/MySQL, version déclarée 0.1.27, avec les modifications présentes dans l’espace de travail. Cet audit porte sur les sources actuelles ; il ne certifie pas que les exécutables déjà distribués contiennent ces modifications.

**Avis général**

L’application possède une couverture fonctionnelle importante et de vraies protections : transactions, contrôles de droits dans de nombreux services, versions de concurrence, journaux centraux, sauvegardes préalables et restauration préparée. Elle n’est cependant pas cohérente dans tous ses parcours. Les contrôles récemment ajoutés à certains écrans ne sont pas systématiquement appliqués aux autres chemins qui réalisent la même opération.

Les défauts les plus sérieux concernent la gestion du temps, les retours de mission/congé, certains chemins d’autorisation, l’avancement et les modifications concurrentes de stages. Plusieurs boutons peuvent annoncer un résultat différent de celui enregistré. La clôture est utile au métier, mais son implémentation rassemble trop de responsabilités et ne constitue pas actuellement une publication immuable de la SME.

Je recommande de conserver une validation mensuelle, de la transformer progressivement en publication versionnée et de sécuriser les anomalies prioritaires avant d’ajouter de nouvelles fonctions. Une réécriture intégrale de l’interface n’est pas le premier chantier à entreprendre.

**Méthode et portée**

- Inventaire : 446 fichiers Python applicatifs, environ 117 600 lignes ; 35 indices de navigation, comprenant un ancien emplacement CSV retiré et un alias de pilotage.
- Revue croisée de l’architecture temporelle, des modules métier, de l’administration, des exports, de la restauration et des comportements d’interface.
- Exécution des 2 315 tests unitaires, en deux lots isolés : **2 305 réussites et 10 échecs**.
- Sept scénarios d’anomalie reproduits sur une vraie instance MySQL jetable, contrôlée par son port 33329 et son répertoire sous `sme_python/build`.
- Six diagnostics isolés supplémentaires pour les droits, le contenu des sauvegardes, le CSV et l’interface.
- Analyse syntaxique de tous les fichiers Python : aucune erreur de syntaxe détectée.
- Les anciennes notes d’audit ont servi de pistes ; leurs défauts n’ont pas été reconduits sans examen du code actuel.
- Aucun code applicatif, compte ou contenu de production modifié. Les scripts de diagnostic et ce rapport sont les seuls nouveaux éléments créés pendant l’audit.

Les diagnostics peuvent réussir en reproduisant volontairement un comportement incorrect : leur réussite n’indique pas que l’anomalie a été corrigée. Aucun correctif décrit ci-dessous n’a été appliqué pendant cet audit.

Ce travail ne constitue pas une inspection exhaustive de chaque ligne, une recette réelle sur les quatre postes, une mesure de performance du serveur de production, un audit réglementaire des barèmes ou une certification de sécurité. Il couvre les modules et leurs principaux chemins, avec une profondeur variable indiquée dans les constats.

**Registre des anomalies démontrées**

Priorités : P1 = correction prioritaire pour l’intégrité ou les autorisations ; P2 = incohérence opérationnelle, risque de récupération ou résultat trompeur ; P3 = robustesse ou lisibilité. La priorité indique l’importance du scénario, pas une preuve que vos données de production ont déjà été touchées.

| ID | Priorité | Domaine | Résultat de l’audit | Preuve |
|---|---|---|---|---|
| A01 | P1 | Périodes et permissions | Un chemin permet d’activer un mois fermé et de modifier l’état des autres mois sans les droits de changement de mois/réouverture. | MySQL natif + code du calendrier |
| A02 | P1 | Paramètres et permissions | Fichier → Configuration et le clic sur MySQL ouvrent un formulaire enregistrable sans le droit `system.settings`, contrairement à la page Paramètres. | Diagnostic isolé + code |
| A03 | P1 | Missions | Terminer une mission commencée après le premier jour peut annoncer un succès alors que l’agent reste en Mission et que l’événement n’est pas fermé. | MySQL natif |
| A04 | P1 | Congés | Remettre à Présent peut annoncer un succès alors que l’agent reste en congé. | MySQL natif |
| A05 | P1 | Stages / concurrence | Une modification portant seulement sur Réf/note ou Session ne change pas le jeton de révision et peut être écrasée par un autre poste. | MySQL natif |
| A06 | P1 | Avancement | Des diplômes et passations futurs entrent dans les données de calcul d’un mois antérieur. | MySQL natif |
| A07 | P2 | Clôture | La sauvegarde liée à une clôture peut précéder la dernière modification contenue dans le mois finalement fermé. | MySQL natif |
| A08 | P2 | Sauvegarde complète | Le SQL et la copie JSON d’`app_config` peuvent représenter deux instants différents, puis être combinés à la restauration. | Diagnostic isolé de deux états successifs |
| A09 | P2 | Reprise mensuelle | L’aperçu et le résultat peuvent différer quand les activités ne sont pas reprises mais que leur historique central s’applique au mois cible. | MySQL natif |
| A10 | P2 | Archives | Un fichier Access ancien peut être copié et présenté comme archive du mois sans correspondre aux données MySQL de ce mois. | Code |
| A11 | P2 | Restauration | La préparation du choix des mois déclenche une maintenance globale avant confirmation de la restauration. | Code |
| A12 | P2 | Restauration / disque | Une préparation annulée peut laisser schémas et fichiers de staging sans politique explicite de rétention. | Code |
| A13 | P2 | Actualisation F5 | F5 recharge Édition SME avec le comportement qui réinitialise les filtres rapides de structure, grade, position et subdivision, contrairement à une recharge préservant l’interface. | Diagnostic isolé + code |
| A14 | P2 | Tableau de bord | Une erreur de rendu peut laisser la vue marquée à jour et son sous-titre actualisé, sans effacer les anciens indicateurs. | Diagnostic isolé |
| A15 | P2 | Historique documentaire / CSV | Les textes commençant par une formule ne sont pas neutralisés dans le CSV, contrairement au journal global. | Diagnostic isolé |
| A16 | P3 | Stages | La confirmation annonce toujours une fin au dernier jour du mois alors que le dialogue demande une date réelle. | Code |
| A17 | P3 | Validation des périodes | Certains normalisateurs acceptent `2026-99` avant un rejet ultérieur par une autre couche. | Diagnostic sans base + code |
| A18 | P3 | Bandeau de contexte | Sur une largeur de 960 px, certains éléments sont tronqués par la ligne unique. | Mesure Qt native |

**Détail des défauts prioritaires**

**A01 — Le changement global de mois et la réouverture doivent être contrôlés dans le service.**

Le calendrier est accessible depuis la barre latérale. Son action « Définir comme mois de travail » appelle `save_sme_context(...force_month_change=True)`, puis `SmeWorkspaceService.set_active_mois`, sans contrôle central des droits `workflow.set_active_month` et `workflow.reopen_month`.

Reproduction : septembre ouvert, octobre fermé, fonctions de permission forcées à refuser. L’activation d’octobre réussit tout de même, ouvre octobre et ferme septembre. Les contrôles de version et de transaction fonctionnent ; ils ne remplacent pas une autorisation.

À l’inverse, les flèches de consultation demandent le droit de modifier le mois actif. La consultation est donc plus restrictive dans un chemin que la mutation globale dans un autre.

Références : `ui/shell_window.py:1465`, `:1589`, `:1769`, `:1933`; `config/sme_context.py:340`; `services/sme_workspace_service.py:193`.

Correction : séparer consultation locale et activation globale. Vérifier les droits au point de mutation, pas uniquement sur un bouton. Une cible déjà fermée doit passer par une réouverture autorisée et motivée ; autoriser un changement de mois ne doit pas autoriser implicitement cette réouverture.

**A02 — Le formulaire de configuration possède plusieurs chemins d’accès incohérents.**

La page Paramètres vérifie `system.settings`, mais `_open_paths_config` ne vérifie que l’absence de brouillons. Le formulaire peut ensuite écrire hôte, base, utilisateur et mot de passe, et éventuellement changer le stockage partagé des médias. Un profil de lecture peut atteindre ce parcours en session active.

Références : `ui/shell_window.py:655`, `:2096`, `:2246`; `ui/config_paths_dialog.py:35`; `ui/paths_config_form.py:343`, `:445`, `:464`; `services/shared_media.py:257`.

Correction : garder un parcours explicite de configuration initiale avant connexion, distinct du parcours accessible pendant une session. Ce dernier doit vérifier le droit à l’ouverture et à l’enregistrement, y compris pour les effets sur les médias partagés.

**A03 / A04 — Les retours de mission et de congé ne doivent pas être de simples changements de cellule.**

Mission ouverte depuis le 10 septembre : le bouton de fin écrit « Présent » via le chemin générique. Ce chemin enregistre son effet au 1er septembre. L’événement Mission du 10 septembre reste plus récent et gagne lors de la projection. La relecture donne encore Mission ; `agent_mission.date_fin` reste NULL. Le message annonce pourtant une ligne mise à jour.

Le même scénario a été reproduit avec CongéAdmin. Les deux handlers avalent en outre les exceptions individuelles avec `except Exception: pass`.

Références : `ui/views/missions_view.py:756`; `ui/views/conges_view.py:730`; `services/agent_dossier_sync.py:191`; `services/temporal_events.py:134` et logique de sélection vers `:63`.

Correction : un service commun de fin d’activité, avec date réelle de retour, clôture de l’événement concerné, événement Présent à la bonne date, mise à jour de la projection et audit dans la même transaction. Le bilan doit reposer sur le résultat confirmé et détailler les refus. Le module Stages dispose déjà d’une partie de ce fonctionnement, mais les autres modules ne l’utilisent pas.

**A05 — Le jeton de concurrence des stages omet deux champs récents.**

`FIELDS['parcours_stage']` n’inclut ni `reference_dossier` ni `session`. Une modification isolée de ces champs ne change donc pas l’empreinte comparée par `checked_target`.

Reproduction contrôlée : A charge NOTE-A/SESSION-A ; B enregistre NOTE-B/SESSION-B ; A modifie Nature avec son ancien contenu. L’écriture est acceptée et rétablit NOTE-A/SESSION-A, perdant la modification de B. Le premier essai avait aussi modifié une représentation de date et avait correctement déclenché un conflit ; la preuve finale a isolé uniquement les deux champs omis.

Références : `repositories/monthly_write_guard.py:6–19`, `:49–66`; `repositories/mysql_numerique_repository.py:893`.

Correction : intégrer chaque champ métier mutable dans la révision, ou utiliser une version explicite de ligne avancée à chaque écriture. Tester des modifications concurrentes portant sur un seul champ. Revoir le même mécanisme lors de chaque ajout de colonne.

**A06 — L’avancement mélange le mois consulté et des événements futurs.**

Pour un mois consulté septembre 2026, un diplôme obtenu en janvier 2027 est inclus dans les données d’avancement. Une passation future au même grade peut remplacer la date de grade du snapshot par celle de 2027. Cela peut anticiper une qualification ou fausser l’ancienneté.

Références : `repositories/mysql_avancement_repository.py:117–203`, requêtes vers `:136–168`, sélection vers `:175–185`; `ui/views/avancement_view.py:377` utilise la date du jour dans un autre niveau du traitement.

Correction : choisir et afficher une date de référence unique, puis l’appliquer à tous les événements lus. Distinguer un calcul historique d’un calcul prévisionnel. La conformité des barèmes réglementaires n’a pas été validée dans cet audit.

**Cohérence de la clôture, des sauvegardes et des restaurations**

**A07 : sauvegarde préalable et état publié sont deux objets différents.** `sme_month_closure.py:315` sauvegarde avant d’entrer dans la transition vers `:325`. Le jeton de `active_month_guard.py` couvre les périodes, pas l’ensemble des écritures RH. Un vrai dump « BEFORE BACKUP », suivi d’une écriture concurrente « AFTER BACKUP », a été accepté par la clôture. La base fermée contient la nouvelle valeur ; la sauvegarde associée contient l’ancienne. Aucune perte de la donnée vivante n’a été démontrée. Le problème est d’utiliser une sauvegarde préalable comme si elle représentait exactement l’état final fermé.

**A08 : deux copies de configuration à des instants différents.** `backup_restore_service.py:630` produit le SQL ; vers `:651`, `app_config` est relu pour le JSON. La restauration SQL peut ensuite être suivie par l’application de ce JSON (`mysql_restore_staging.py:166–179`). Le diagnostic fait apparaître une configuration OLD dans le SQL et NEW dans le JSON. Le dump SQL lui-même est cohérent : c’est l’assemblage du paquet qui manque d’un instant commun. Ce scénario a été démontré avec deux lectures simulées, pas par un stress concurrent réel.

**A09 : reprise mensuelle.** Avec une mission datée déjà ouverte, toutes les activités décochées, l’aperçu annonce « À vérifier » et aucune mission copiée ; après application, la projection centrale remet l’agent en Mission. Les deux règles ont chacune une logique, mais le résultat n’est pas celui annoncé. Références : `month_fill.py:192–195`, `:297–299`; `temporal_events.py:232`. L’aperçu doit utiliser le même calcul final que l’enregistrement et expliquer la différence entre une fiche copiée et un événement déjà applicable.

**A10 : archive Access trompeuse.** `sme_month_closure.py:286–300` copie simplement le fichier Access configuré sous un nom contenant le mois clôturé. Le fichier n’est pas reconstruit depuis MySQL et son mois n’est pas vérifié. Le navigateur retrouve ensuite cette « archive » par son nom (`:503–518`). Un ancien fichier d’import peut donc être présenté comme archive du mois. La sauvegarde SQL séparée demeure utile, mais ces deux objets ne doivent pas être confondus.

**A11 : maintenance trop tôt.** `backup_month_selection.py:58` appelle la préparation de restauration ; `mysql_restore_staging.py:307–309` entre en maintenance de la base cible. Les écritures des autres postes peuvent être suspendues alors que l’utilisateur ne fait encore que lire la sauvegarde et choisir ses mois. L’inspection et le staging doivent précéder le gel de la cible.

**A12 : conservation du staging après annulation.** Les schémas préparés et le workspace sont volontairement conservés pour récupération. La branche d’annulation du choix de mois (`backup_restore_view.py:681–683`) ne les nettoie pas. Il faut distinguer une simple préparation abandonnée d’une opération incertaine nécessitant récupération, avec un registre et une politique de rétention. Il ne faut pas supprimer automatiquement les éléments d’une restauration dont le résultat demeure incertain.

**La clôture actuelle est-elle optimale ?**

Non. Elle est adaptée à l’existence d’une SME mensuelle, mais le système mélange :

1. le mois que l’utilisateur regarde ;
2. le mois de travail partagé ;
3. les états Ouvert/Fermé des périodes ;
4. les événements RH avec dates d’effet ;
5. les situations de personnel recopiées ou projetées par mois ;
6. la sauvegarde de récupération de toute la base ;
7. les fichiers Access et les documents remis comme archives.

La complexité vient surtout des interactions entre ces couches. Un correctif de diplôme ou d’affectation peut recalculer des mois fermés (`mysql_agent_dossier_repository.py:969–1017`), et les projections de positions font également évoluer des mois fermés (`temporal_events.py:63–94`). « Fermé » signifie donc principalement saisie ordinaire bloquée, pas conservation immuable du document officiel publié.

Ce choix peut convenir à une situation RH corrigée à date. Il est insuffisant si l’on veut retrouver strictement la version remise à une autorité lors de la clôture. Les noms et d’autres attributs partagés de l’agent peuvent également évoluer dans les vues historiques.

Les coûts augmentent avec l’historique : sauvegarde de toute la base à chaque clôture ; préparation d’une base complète pour choisir des mois à restaurer ; relectures des autres mois avant/après chaque restauration pour vérifier leur intégrité (`month_backup.py:337`, `:424` et suite). Ce sont des causes plausibles de lenteur, sans mesure chiffrée des temps sur votre serveur.

**Modèle cible recommandé**

Conserver MySQL et l’interface actuelle au départ. Séparer les concepts derrière les services existants :

| Élément | Fonction proposée |
|---|---|
| Mois consulté | Préférence locale de navigation ; lecture selon les droits, sans mutation du calendrier partagé. |
| Événements RH | Source de référence datée : stage, mission, congé, retour, diplôme, grade, affectation. |
| Projection mensuelle | Vue calculée ou matérialisée des événements applicables ; sert aux listes et exports rapides. |
| Mois en préparation | État de travail avec contrôles de cohérence et responsables identifiés. |
| Publication mensuelle | Version figée de la SME avec auteur, date, révision, empreinte et documents associés. |
| Correction ultérieure | Modification de l’historique avec audit et nouvelle version publiée, en conservant la précédente. |
| Sauvegarde technique | Mécanisme centralisé de récupération, planifié et indépendant de la consultation. |

Parcours proposé : **saisie continue → contrôles → aperçu final → publication d’une version**. Les lectures restent disponibles. Le verrou nécessaire à la publication doit porter sur une fenêtre courte et explicite ; la préparation lourde se fait avant cette fenêtre, avec vérification de révision à la bascule.

Il faut pouvoir choisir clairement entre « situation corrigée pour ce mois » et « version officiellement publiée ». Une réouverture administrative devient une révision de publication motivée, sans perte de la version déjà remise.

**Comparaison des méthodes possibles**

| Méthode | Avantages | Coûts / limites | Avis |
|---|---|---|---|
| Garder le modèle actuel en corrigeant ses chemins | Migration limitée, continuité de travail | Complexité temporelle et doubles représentations conservées | Première étape nécessaire. |
| Historique daté + projections + publications mensuelles versionnées | Moins de copies manuelles, historique explicable, corrections et archives distinctes | Refactorisation progressive des services et des formats d’archive | Cible conseillée. |
| Un fichier ou une base par mois | Compréhension initiale simple | Identités, droits, croisements et restauration multipliés ; synchronisation multi-poste difficile | Déconseillé pour cette application. |
| Une API centrale conservant les écrans PySide6 | Règles, droits, migrations et travaux longs exécutés côté serveur ; secret SQL non distribué | Déploiement et maintenance d’un service supplémentaire | Évolution pertinente pour fiabiliser plusieurs postes. |
| Refaire toute l’application en web | Déploiement client simplifié et accès central | Coût de réécriture de l’interface ; les règles temporelles restent à concevoir | Pas la priorité immédiate. |
| Event sourcing intégral avec infrastructure distribuée | Historique technique détaillé, reconstruction | Complexité élevée d’exploitation et de développement | Disproportionné à ce stade. |

La fluidité peut déjà progresser par la séparation consultation/activation, la suppression de la maintenance pendant la simple inspection d’une sauvegarde, des aperçus calculés en arrière-plan, la conservation des filtres au rafraîchissement et des opérations métier communes. Aucun gain de temps chiffré ne peut être promis sans profilage sur un volume et un réseau représentatifs.

**Revue module par module**

« Pas de nouveau défaut démontré » signifie qu’aucun cas autonome n’a été établi durant cette revue, pas que le module est exempt de bugs.

| Module / écran | Constat actuel et suite recommandée |
|---|---|
| Tableau de bord | A14 : rendu en erreur marqué à jour ; conserver un état d’échec visible et avancer la révision seulement après rendu réussi. Les données complémentaires masquent aussi certaines erreurs par journalisation seule. |
| Liste générale | Historique maître et projections inspectés ; pas de nouveau défaut autonome démontré. Les identités partagées rendent une vue historique différente d’un document publié. |
| Effectifs par structure | Pas de défaut de calcul nouveau démontré. Impression désormais soumise au droit d’export. Un test de filtre doit être adapté au nouveau composant d’impression. |
| Édition SME / actions groupées | Ajout groupé de diplôme avec transactions, bilan et prévention de doublons présents. A13 : F5 réinitialise certains filtres rapides. Les changements de position doivent respecter les mêmes services datés que les modules spécialisés. |
| SME DPF | Le périmètre de COMGEND est déduit de la présence du texte « DPF » dans Fonction/Subdivision (`sme_dpf_scope.py:78` environ). Préférer un rattachement explicite ; aucune mauvaise inclusion précise n’a été démontrée sur vos données. |
| Fiche complète / Synthèse | Rechargement après ajout de diplôme présent. La synthèse applique la règle de sélection militaire ; le dossier conserve aussi civils et diplômes futurs. Une recette des champs partagés reste nécessaire avec un jeu réel anonymisé. |
| Diplômes / ajout groupé | Contrôles et atomicité couverts par les tests ciblés existants. Le diplôme conservé au dossier peut légitimement ne pas remplacer celui de la SME selon catégorie/date. Ne pas supprimer cette distinction. |
| Liste des présents | Présence fondée sur le référentiel ; cohérence conditionnée à la bonne définition des positions. La « présence » doit être expliquée de manière identique dans les listes, tableaux de bord et modèles officiels. Aucun nouveau défaut autonome reproduit ici. |
| Mise en page des présents | Configuration partagée versionnée et exports inspectés. Distinguer préférence locale et modification globale ; tester visuellement après tout changement de modèle. |
| Situation numérique | Respect du modèle officiel présent ; contraintes de colonnes pour les grades et validation incomplète des noms de feuilles à anticiper. Aucun temps de génération représentatif mesuré. |
| Détachés | Comparaison et export inspectés. Les fonctions libres pour Détachés et CS-DPF sont une règle spéciale volontaire. Pas de nouveau défaut autonome démontré ; règles de périmètre à documenter. |
| Configuration Excel détachés | Persistance partagée versionnée ; dépend de la protection de configuration et des sauvegardes cohérentes. Pas de défaut autonome supplémentaire reproduit. |
| Gains & pertes | Les mois vides sont exclus de certaines inférences pour éviter des fausses pertes. Il manque une distinction explicite entre mois absent, vide en préparation et réellement terminé vide. Limite de modèle, pas perte de données démontrée. |
| Missions | A03 : fin inefficace à partir du milieu du mois, événement non fermé et erreurs individuelles masquées. Priorité élevée. |
| Congés | A04 : retour Présent inefficace et événement non fermé ; bilan d’échec insuffisant. Priorité élevée. |
| Stages internes / locaux / extérieurs | Corrections récentes de fin datée et de doublon présentes. A05 : perte concurrente Réf/note/Session ; A16 : confirmation ancienne. |
| Malades | Suppression de fiche différente d’une fin de maladie. Pas de bouton de retour Présent équivalent reproduit ; clarifier le parcours de guérison/retour et sa date si cette fonction est attendue. |
| Punis / détention | Changement de position préparé et transactionnel avec erreurs explicites. Date réelle de levée non proposée dans ce parcours ; limite fonctionnelle à arbitrer. Pas de mauvais résultat natif démontré ici. |
| Déserteurs | Retour Présent par le même chemin mensuel que Missions/Congés. Risque fort de défaut temporel analogue ; reproduction native distincte non réalisée. Les erreurs sont affichées. |
| Limite d’âge / retraite | Contrôles de dates et grades ambigus présents. Possibilité de survalidation quand naissance et service sont tous deux exigés alors qu’un seul plafond est configuré. Arbitrage métier et vérification réglementaire externe nécessaires. |
| Soit transmis | Génération et droit d’export inspectés, sans nouveau défaut autonome démontré. Recette des modèles Word à conserver. |
| Avancement | A06 : filtrage à date de référence à corriger avant de considérer le tableau comme une aide fiable pour un mois historique. Les conditions manuelles restent à vérifier humainement. |
| Structures & subdivisions | Création U/D/T et rattachement atomiques présents. Nomenclature globale et libellés historiques ne représentent pas la même chose. Pas de nouveau défaut autonome démontré dans la création récente. |
| Fonctions / postes vacants | Rattachement au niveau structure, pas catalogue de fonctions autorisées par subdivision ; liens non datés. Permissions trop agrégées pour une délégation fine. |
| Grades | Catalogue plus extensible que les colonnes du modèle officiel : un nouveau grade accepté peut ensuite bloquer l’export. Prévisualiser cette incompatibilité à la création. |
| Positions administratives | Combinaison de codes fixes et référentiel configurable. Toute nouvelle position doit être contrôlée dans la présence, les listes détaillées et les exports ; pas de défaut autonome supplémentaire reproduit. |
| Médailles & décorations | CRUD et droits au niveau service examinés ; pas de nouveau défaut autonome démontré. Couverture visuelle et scénarios de reprise historique non exhaustifs. |
| Utilisateurs & droits | Droits spécifiques présents, mais contournements A01/A02 dans d’autres chemins. Le rôle opérateur par défaut conserve des droits d’administration métier larges malgré sa description. |
| Sessions actives | L’écran montre des jetons encore valides, pas nécessairement des PC actuellement en ligne. Après crash, le token peut rester visible jusqu’à expiration. Distinguer présence récente et session valide. |
| Historique des documents | A15 : CSV à neutraliser ; export limité à la page chargée, jusqu’à 2 000 entrées. Purge locale conserve le journal MySQL ; le libellé doit rester explicite. |
| Journal d’activité global | Contrôle de consultation et export présent ; journalisation non exhaustive de chaque clic ni chaque impression papier. Droit couplé à l’audit sensible. |
| Audit des données sensibles | Périmètre d’export désormais explicite ; consultation couplée au journal global. Les traces ne constituent pas à elles seules une archive immuable indépendante d’un administrateur SQL. |
| Paramètres & données | A02 ; identifiants SQL locaux et transport à renforcer. Configuration initiale et configuration en session doivent être séparées. |
| Sauvegarde & restauration | A07/A08/A11/A12 ; protections de chemins, transactions et récupération positives. Archive finale non chiffrée. Exercices de restauration sur copie anonymisée nécessaires. |
| Cycle mensuel / calendrier / fichiers d’archive | A01/A07/A09/A10/A17 ; distinguer consultation, activation, clôture, publication et récupération. |
| Personnel retiré / restauration de fiches | Droits spécifiques et reconstruction inspectés dans les tests existants ; pas de nouveau défaut autonome démontré. Revalider les dépendances d’historique après chaque évolution du schéma. |
| Alertes carrière | Pas de défaut nouveau démontré par le test global en échec : il attend un ancien format SQL de mois. La qualité des alertes dépend néanmoins de la cohérence des événements datés. |
| Ancien import CSV / alias de navigation | Un emplacement retiré et un alias ne sont pas deux modules métier manquants. Les anciens accès doivent rester refusés ou redirigés explicitement. |

**Risques et choix de conception à traiter séparément des bugs reproduits**

1. **Secrets SQL sur les postes.** `config/user_paths.py:142` écrit le mot de passe MySQL dans le profil ; `config/settings.py:204` propose `root` par défaut. Cela ne prouve pas que vos quatre postes utilisent encore root. Avec un compte privilégié distribué, un accès SQL direct peut contourner les rôles de l’interface. Utiliser un compte applicatif limité et une protection Windows des secrets ; envisager un service central pour une vraie frontière d’autorisation.
2. **TLS non garanti par le client.** `mysql_connection.py:163–178` et `mysql_pool.py:53–65` ne définissent pas de paramètres de certificat/vérification TLS. La configuration du serveur et du réseau n’a pas été inspectée : aucun constat de capture réseau réelle n’est formulé. Le client doit pouvoir exiger un transport chiffré vérifié.
3. **Sauvegardes non chiffrées.** Le ZIP n’est pas chiffré. Le mot de passe du profil est bien expurgé, mais personnes, médias et comptes/hachages restent sensibles. Prévoir un stockage protégé, une rétention et un chiffrement adapté ; un CRC n’est pas une preuve d’authenticité.
4. **Permissions agrégées.** Consultation et mutations des référentiels restent réunies. Le journal global et l’audit sensible partagent un droit. Le droit d’historique documentaire complet inclut aussi import/purge. C’est une granularité limitée, pas automatiquement un contournement.
5. **Fichiers de modèles.** Les grades, feuilles, formules et fusions sont des contrats. La validation initiale ne vérifie pas tous les noms utilisés ensuite dans `numerique_model_export.py:217–276`. Tester le modèle avant une production finale et afficher précisément les incompatibilités.
6. **Maintenabilité.** Édition SME dépasse 4 400 lignes ; le shell dépasse 2 800 ; plusieurs écrans mêlent validation, transactions, règles métier et présentation. Les différences Mission/Congé/Stage en sont une manifestation concrète. Extraire des commandes métier communes plutôt que multiplier les correctifs dans chaque bouton.
7. **Dépendances et migrations.** Les clients peuvent déclencher l’initialisation/migration du schéma (`repositories/mysql_common.py:82` et suite). Distinguer installation/migration administrateur et exploitation ordinaire aiderait à limiter les droits SQL et les surprises au démarrage. Aucun échec de migration en production n’a été reproduit.

**Les dix échecs de la suite générale**

| Tests concernés | Nombre | Qualification actuelle |
|---|---:|---|
| Dialogue désertion | 1 | Ancienne attente ne contenant pas le nouveau champ facultatif `reference_dossier`. |
| Réponse satellite invalidée / Missions | 1 | Ancienne attente de vidage/reconstruction du filtre pendant F5 ; la réponse périmée reste refusée. |
| Opérations centralisées / diplôme et date | 3 | Objets simulés incomplets, dépourvus de `grade`, contrairement au vrai DTO. |
| Export intégrité du classeur | 1 | Ancien modèle et anciennes attentes d’absence de formules/fusions, incompatibles avec le modèle fidèle demandé. |
| Filtre Effectifs par structure | 1 | Objet simulé ne possédant pas `_sync_print_state` ajouté avec l’impression. |
| Requête alertes carrière | 1 | Attente d’un ancien format de période dans les paramètres SQL. |
| Routage du menu Remplir un mois | 1 | Faux `QMainWindow` ne possédant pas la méthode du menu Session. |
| Écriture historique / mois sélectionné | 1 | `MagicMock` interprète la méthode métier `assert_carriere_affectation` comme une assertion Mock non déclarée. |

Ces qualifications évitent de confondre des montages de test anciens avec des défauts de production. Elles ne rendent pas la suite verte : il faut mettre les tests à jour selon les contrats actuels et conserver des cas d’intégration réalistes. Les erreurs métier A01–A18 ont été trouvées en dehors de plusieurs chemins déjà largement couverts.

**Plan d’action proposé**

1. **Intégrité et autorisations.** Corriger A01/A02/A03/A04/A05/A06 ; reproduire le retour de désertion ; supprimer les erreurs silencieuses. Ajouter des tests d’autorisation au niveau service et de retour daté par activité.
2. **Récupération et publication.** Unifier l’instant de sauvegarde/configuration ; distinguer sauvegarde préalable et version publiée ; sortir la préparation des restaurations de la maintenance ; traiter explicitement les préparations annulées.
3. **Expérience quotidienne.** Préserver filtres/position lors de F5 ; ne jamais marquer un écran à jour après un échec de rendu ; corriger les messages de dates ; restituer les libellés du bandeau à toutes les tailles.
4. **Convergence temporelle.** Utiliser un service commun pour événements datés et projections. Faire calculer aperçu et résultat par la même logique. Définir les règles de correction d’un mois fermé et les versions officielles.
5. **Exploitation multi-poste.** Séparer migrations et usage courant, limiter les identifiants SQL, protéger le transport et les sauvegardes. Une API centrale peut être introduite progressivement sans jeter les écrans existants.
6. **Recette finale.** Mettre les dix tests obsolètes à jour ; exécuter une recette sur copie anonymisée avec volumes proches du réel et deux à quatre clients simultanés ; mesurer temps de chargement, blocage, clôture et restauration. Comparer les exports à des résultats métier attendus, pas seulement à l’absence d’exception.

Les critères de réussite doivent être concrets : un retour Mission/Congé change réellement la position à la date choisie ; un lecteur ne peut modifier aucune configuration par un chemin parallèle ; une modification de Session seule est protégée contre écrasement ; aperçu et mois rempli correspondent ; version publiée et empreinte archivée restent retrouvables après correction ultérieure.

**Références techniques externes**

Les recommandations techniques ont été confrontées à des sources primaires ; elles ne prouvent pas la configuration du serveur réel :

- [MySQL — modèle transactionnel InnoDB](https://dev.mysql.com/doc/refman/8.0/en/innodb-transaction-model.html) : transactions et verrous pour coordonner les écritures ; un jeton de calendrier ne protège pas automatiquement toutes les données métier.
- [MySQL — mysqldump et single-transaction](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html) : snapshot cohérent des tables transactionnelles et précautions concernant les changements de schéma pendant le dump. Cela ne synchronise pas une seconde lecture JSON effectuée après le dump.
- [MySQL — récupération à un instant donné](https://dev.mysql.com/doc/refman/8.0/en/point-in-time-recovery.html) : sauvegarde complète et journaux binaires pour une stratégie de récupération indépendante des documents mensuels.
- [Qt — objets et threads](https://doc.qt.io/qtforpython-6.10/overviews/qtdoc-threads-qobject.html) : traitements longs hors du thread d’interface et restitution des résultats par les mécanismes Qt adaptés.

**Éléments de preuve conservés**

- `sme_python/build/audit_global_20261106/unit_collection.txt` : inventaire des 2 315 tests.
- `sme_python/build/audit_global_20261106/unit_shard_0.txt` : 1 152 réussites et 6 échecs.
- `sme_python/build/audit_global_20261106/unit_shard_1.txt` : 1 153 réussites et 4 échecs.
- `sme_python/build/audit_month_architecture_20261106.py` : trois reproductions natives cycle mensuel.
- `sme_python/build/audit_personnel20261106.py` : quatre reproductions natives métier.
- `sme_python/build/audit_global_20261106/admin_probes/test_readonly_findings.py` : trois diagnostics administration/export.
- `sme_python/build/audit_global_20261106/test_ui_findings.py` et `ui_probes.txt` : trois diagnostics interface.

Les fichiers de preuve contiennent des données synthétiques. Aucun mot de passe réel ni contenu de la base de production n’a été nécessaire à cet audit. Aucun exécutable n’a été généré.
