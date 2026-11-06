# Bilan approfondi de S.G.I DPF — 2 novembre 2026

> **Mise à jour après corrections :** ce document conserve les constats de
> l'audit initial. Les défauts C01–C09 ont fait l'objet de corrections et de
> validations ultérieures ; leur description ci-dessous n'est plus une liste
> de travaux à effectuer. Consulter `FINALISATION_LIVRAISON_2026-11-02.md`
> pour l'état vérifié, les preuves et les limites de la livraison.
> Les autres recommandations de cet audit ne sont pas automatiquement clôturées.
>
> **Complément 0.1.2 :** maintenance centralisée, conservation de l'audit,
> clarification des retraits historiques et traçabilité des consultations sont
> décrites dans `MAINTENANCE_AUDIT_HISTORIQUE_2026-11-02.md`.

## Lecture rapide

L'application possède un socle fonctionnel conséquent et de vraies protections : transactions MySQL partagées, contrôles de concurrence, sauvegardes préalables, journaux centraux, caches invalidables, chargements de fond et nombreux tests. Une réécriture générale ne serait pas le premier investissement à faire.

Les priorités sont la **fiabilité de la traçabilité**, la **révocation des sessions**, la **conservation des brouillons**, la **clarté de certaines règles de présence** et la **fluidité des accès MySQL depuis l'interface**. Des défauts réels subsistent dans ces domaines, y compris dans des chemins voisins de ceux récemment corrigés.

Ce rapport est un audit du code, des configurations de construction et de scénarios synthétiques. Il ne certifie ni les données de production, ni tous les postes de travail, ni la conformité réglementaire des barèmes. Aucun comportement applicatif n'a été corrigé pendant cet audit : les constats sont soumis à décision.

### Repères de lecture

- **Reproduit** : un scénario synthétique exécute le comportement et confirme le constat.
- **Confirmé par le code** : le chemin concerné est identifiable ; ses effets dépendent du contexte décrit.
- **Risque à mesurer / valider** : le mécanisme existe, mais aucune fréquence ni gravité réelle en production n'est déduite sans mesure.
- **Amélioration / choix métier** : décision souhaitable, sans prétendre qu'une règle actuellement choisie est une panne.

Priorités : **P1** = sécurité des opérations, données ou confiance utilisateur ; **P2** = robustesse, performances ou exploitation ; **P3** = ergonomie et maintenance.

Quelques termes utilisés : une **transaction** valide ou annule un ensemble d'écritures ; un **cache** est une copie temporaire en mémoire ; un **snapshot** est un état capturé à un moment donné ; le **thread graphique** est le traitement qui maintient la fenêtre réactive ; un **alias** conserve un ancien identifiant après une fusion de dossiers.

## 1. Périmètre et méthode

### Inventaire

| Élément | Résultat |
|---|---:|
| Fichiers Python applicatifs | 410 |
| Lignes physiques, commentaires compris | 111 455 |
| Fichiers de tests Python recensés | 250 |
| Fonctions de test recensées, avant paramétrage | 1 744 |
| Cas hors intégration collectés | 1 909 |
| Erreurs de syntaxe détectées | 0 |
| Imports internes manquants détectés par l'analyse statique | 0 |
| Définitions de méthodes/classes écrasant une définition homonyme dans la même portée | 0 détectée |

L'analyse du graphe d'import ne suffit pas à déclarer un module mort : certains sont des outils en ligne de commande, des exports publics ou des imports dynamiques. Les candidats proposés plus loin ont été recoupés avec les appels du code, des scripts et des tests.

### Vérifications réalisées

- Lecture de l'architecture et des flux importants : démarrage, navigation, contextes mensuels, cache, synchronisation, comptes, imports, dossiers, exports, sauvegarde/restauration et construction portable.
- Relecture des audits antérieurs et des comptes rendus de correction. Les anciens constats corrigés ne sont pas repris automatiquement comme défauts actuels.
- Exécution des tests hors intégration dans des processus et profils jetables séparés ; relances ciblées pour les chemins dépendant du répertoire de lancement et les cas de redimensionnement.
- Reproductions supplémentaires de défauts absents de la suite existante.
- Essais MySQL exclusivement sur l'instance jetable vérifiée sous `build`, avec refus du port de production et schémas de test distincts.
- Aucun import réel, aucune suppression de mois réel, aucune restauration de production, aucun changement de droits ni de données métier.

Les chiffres définitifs et les limites des essais sont regroupés dans l'annexe de validation.

## 2. Défauts prioritaires et traçabilité

### C01 — Le vidage du mois peut réussir alors que sa trace finale échoue — P1, reproduit

**Déclencheur :** la suppression SQL se termine, puis l'écriture de la trace `sme_month_emptied` échoue.

**Comportement actuel :** la transaction de suppression est déjà validée. Les fichiers d'archive peuvent déjà avoir été retirés. Une exception de journalisation remonte ensuite. L'utilisateur peut donc recevoir une erreur alors que le mois a réellement été vidé, et la trace centrale attendue n'est pas garantie.

**Preuve :** la reproduction observe l'ordre `begin → purge → commit → audit`, puis l'échec de l'audit. Une sauvegarde préalable existe : cela atténue les conséquences, sans rendre l'annonce de résultat correcte.

**À améliorer :** écrire la trace métier de suppression dans la même transaction que les données. Traiter ensuite le résultat de nettoyage des fichiers comme une étape distincte, avec état durable et message explicite : « données vidées, nettoyage d'archives à reprendre » si nécessaire.

**Sources :** `services/empty_sme_month.py:58–100`, `services/audit_log.py:260`.

### C02 — Le filtre « mois » du journal global peut masquer des imports réellement tracés — P1, reproduit

L'import Access appelle `append_audit_event(..., sme_mois=ym)`. Cette fonction attend `sme_mois_context` pour renseigner la colonne mensuelle du journal. `sme_mois` finit dans le contenu JSON, tandis que la colonne interrogée par le filtre reste vide.

**Conséquence :** l'opération peut être trouvée sans filtre, ou par recherche libre, puis disparaître quand on sélectionne précisément le mois de cet import. Il ne s'agit pas d'une absence d'import ni d'une limite de 500 lignes.

**À améliorer :** normaliser le contrat de journalisation et contrôler les appels existants. Prévoir une reprise ciblée des anciennes traces lorsque leur mois est déterminable dans le contenu ; conserver un marqueur de cette reprise.

**Sources :** `services/mysql_bundle_import.py:547`, `services/audit_log.py:260–293`, `repositories/app_config_repository.py`, méthode `search_audit_journal`.

### C03 — La révocation distante n'est pas contrôlée dans le mode de connexions « off » — P1, reproduit

`sync_session_from_config` ne contrôle le registre central de la session que lorsque la politique de connexions simultanées n'est pas `off`. Dans l'autre mode, une hausse de la révision utilisateur peut reconstruire la session locale avec la nouvelle révision au lieu de la fermer.

**Conséquence :** « révoquer les sessions » ou certaines modifications de compte peuvent ne pas interrompre un poste déjà connecté comme l'administrateur l'attend. Le contrôle du nombre de postes et la validité d'une session sont deux besoins différents.

**À améliorer :** vérifier la révocation indépendamment du plafond de connexions ; distinguer changement de rôle actualisable et révision imposant une réauthentification. Tester le mode sans plafond autant que les modes limité et unique.

**Sources :** `services/auth_service.py:290–335`, `:671–699`, `services/auth_session_registry.py:240`.

### C04 — Le registre de sessions indisponible peut laisser passer une politique de session unique — P1, reproduit

Lorsque `_table_ready` renvoie faux, `try_register_session` produit un jeton et ne renvoie pas d'erreur, même avec la politique `single`. `heartbeat_session` considère également le contrôle réussi si la table n'est pas disponible.

**Limite précise :** cela ne permet pas de se connecter sans mot de passe. C'est la politique de contrôle des sessions qui n'est plus garantie.

**À améliorer :** annoncer explicitement l'indisponibilité et définir le comportement de secours. Pour une politique imposée de session unique, refuser une nouvelle connexion non contrôlable serait cohérent ; le choix d'exploitation doit être assumé et documenté.

**Source :** `services/auth_session_registry.py:155–161`, `:252–253`.

### C05 — L'état du registre de sessions est mémorisé pour toutes les bases à la fois — P1, reproduit

`_table_ready_cached` est un unique booléen de module. Après une première réponse pour une base A, une vérification pour B peut reprendre cette réponse sans examiner B. Un premier échec peut également rester mémorisé après rétablissement de l'accès.

**Conséquence :** comportement erratique après changement de profil/base, activation de la gestion des sessions ou panne transitoire ; un redémarrage peut sembler « résoudre » le problème.

**À améliorer :** cache par identité de connexion/base, invalidation au changement de profil et nouvelle tentative contrôlée après erreur. Les autres caches de l'application utilisent déjà des identités de base plus précises : ce registre doit suivre la même règle.

**Source :** `services/auth_session_registry.py:18`, `:54–68`.

### C06 — L'éditeur avancé peut perdre un titre lors d'un changement rapide de section — P1, reproduit

Modifier le titre de A puis sélectionner B avant les 1,5 seconde d'auto-enregistrement recharge les champs de B sans avoir synchronisé le titre de A. En revenant à A, l'ancien titre réapparaît.

Le panneau rapide a été corrigé, mais l'éditeur avancé possède son propre chemin de saisie et conserve le défaut.

**À améliorer :** synchroniser les brouillons immédiatement, ou enregistrer le contenu de la section précédente avant le changement de sélection. Ajouter une vérification sur les deux éditeurs.

**Source :** `ui/views/layout_editor_view.py:466–510`, `:589`, méthode `_on_select`.

### C07 — Un brouillon du panneau rapide peut être effacé par une actualisation de contexte — P1, reproduit

La fenêtre principale ne reconnaît comme écrans à saisie non enregistrée que certains modules, notamment les attestations et la configuration des détachés. Le panneau rapide de mise en page n'est pas inclus.

Une actualisation distante ou de configuration peut donc appeler son rechargement, même si un titre vient d'être saisi ou si un conflit avait laissé un brouillon. Le chargement remplace alors ce brouillon.

**À améliorer :** contrat commun `has_unsaved_changes`, suspension de l'actualisation concernée, conservation du brouillon en cas de conflit et choix explicite pour recharger. La fermeture et le changement de mois doivent utiliser le même contrat.

**Sources :** `ui/shell_window.py:1224–1258`, `:2638`, `ui/views/liste_present_view.py`, méthodes `apply_runtime_after_config_change`, `_safe_reload`.

### C08 — La recherche du journal par agent ne couvre pas les alias d'identité fusionnés — P1, reproduit sur MySQL jetable

Les fusions conservent les anciens identifiants dans `agent_identity_alias`. La résolution du journal global récupère les numéros de carrière et l'identifiant principal de l'agent, sans récupérer ces alias.

**Conséquence :** une trace ancienne est bien en base, mais reste absente lorsqu'on recherche le dossier canonique après fusion. La recherche libre ou l'ancien identifiant peuvent encore la retrouver.

Certaines traces collectives/fusions placent également les cibles dans des formes JSON particulières, sans lien d'agent standardisé. La recherche par agent ne peut donc pas être présentée comme exhaustive pour tous les formats historiques.

**À améliorer :** intégrer les alias et normaliser les cibles des opérations. À terme, une table de liaison `événement d'audit ↔ agent(s)` serait plus précise et mieux indexable qu'une multiplication de recherches JSON.

**Sources :** `repositories/app_config_repository.py`, méthodes `audit_personnel_keys`, `search_audit_journal` ; `services/personnel_merge_service.py:223–238`.

### C09 — Le réglage « compte comme présent » n'est pas l'unique règle réellement appliquée — P1, reproduit

L'écran annonce que sa colonne détermine la présence. Le moteur applique cependant des exceptions et replis :

- « Présent » et « StageInterne » peuvent rester comptés présents avec une entrée du référentiel marquée fausse ;
- une valeur hors référentiel comme **« Non présent »** est interprétée présente par le repli textuel contenant « présent » ;
- les positions vides sont comptées présentes, selon le choix conservé explicitement par l'utilisateur.

Le dernier point est une règle décidée, et doit rester signalé. Les deux premiers méritent une correction ou une explication métier explicite : la case affichée ne représente pas fidèlement toute la décision du moteur.

**À améliorer :** une règle centrale partagée entre SQL, interface et exports ; traitement explicite des libellés négatifs/inconnus ; présentation lisible des exceptions au référentiel. Faire arbitrer les cas ambigus avant de changer leurs totaux.

**Sources :** `padmin_present.py:76–95`, `:192–232`, `repositories/mysql_agent_repository.py`, fonction `_padmin_where_clause` ; `ui/views/referentiel_views.py:250`.

### C10 — Certains écrans peuvent présenter une lecture impossible comme une absence de données — P2, confirmé par le code

Exemples actuels :

- l'échec de lecture de diplômes dans le dossier est remplacé par une liste vide ;
- l'indicateur des congés de longue durée peut disparaître si sa lecture échoue ;
- des désignations, subdivisions ou choix de référentiel peuvent être remplacés par un résultat vide ou une autre source après une exception.

Toutes les interceptions d'exception ne sont pas mauvaises : les nettoyages de fichiers et options facultatives ont besoin de reprises. Le problème est le résultat métier lorsque « inconnu / indisponible » ressemble à « aucun ».

**À améliorer :** états visuels distincts : résultat vide confirmé, données indisponibles et résultat partiel. Garder le détail technique dans les journaux, avec une action simple « Réessayer ».

**Sources :** `ui/personnel_dossier_dialog.py:1327`, `services/dashboard_service.py:302–320`, `services/referentiel_service.py:257–289`, `services/detaches_preview_service.py:34–48`.

### C11 — Un scan d'alertes interrompu peut laisser un état « en cours » sans signal de fin — P2, reproduit

Le nettoyage du scan partagé intercepte les exceptions ordinaires, mais pas une interruption comme `KeyboardInterrupt`. Dans cette situation, le cache peut conserver `scanning=True` et un événement de fin non déclenché. Un autre appel sur la même génération attend cet événement sans limite.

Ce scénario d'interruption est moins courant que les erreurs MySQL habituelles, déjà interceptées ; il n'est pas présenté comme un blocage quotidien observé. Il fragilise toutefois l'annulation et la reprise d'un scan.

**À améliorer :** garantir la libération de l'état partagé dans un `finally`, y compris lors d'une interruption, et prévoir une attente bornée/réévaluation de génération pour les appelants.

**Source :** `services/career_alerts_cache.py:70–112`.

## 3. Performances et fluidité

### P01 — Des appels MySQL restent exécutés dans le thread graphique — P1/P2, confirmé par le code

Le contrôle de session, déclenché toutes les 15 secondes, recharge la configuration et peut interroger le registre directement depuis le callback Qt. L'ouverture habituelle d'un dossier construit aussi son interface et lit plusieurs rubriques avant d'afficher le dialogue. Certains changements de filtres demandent encore des référentiels synchrones.

**Effet attendu en réseau lent :** clics et déplacement de fenêtre qui attendent la réponse MySQL. Un calcul CPU rapide ne résout pas cette attente. Le pool prévoit des délais de connexion/lecture de plusieurs secondes ; leur présence ne rend pas un appel graphique non bloquant.

**À améliorer :** capture du contexte, lecture en tâche de fond, retour graphique vérifié par génération et chargement différé des rubriques. Priorité aux contrôles périodiques, aux dossiers et aux filtres utilisés quotidiennement.

**Sources :** `ui/shell_window.py:732`, `:760`, `services/auth_service.py:290`, `ui/personnel_dossier_open.py:75–87`, `ui/personnel_dossier_dialog.py:1435`, `ui/views/personnel_list_view.py:765`.

### P02 — Le rendu final PDF peut encore immobiliser l'interface — P2, confirmé par le code

La préparation HTML de certaines listes est déportée, mais la construction du document Qt et son impression PDF restent synchrones. Cela concerne en particulier les grandes listes détaillées. Le correctif d'exhaustivité du PDF des présents résout le contenu, sans garantir la fluidité de l'étape d'impression finale.

**À améliorer :** mesurer séparément lecture, HTML, mise en page Qt et publication ; afficher un état de progression cohérent ; évaluer une production dédiée pour les très gros documents. Ne pas déplacer simplement des widgets Qt dans un thread non graphique.

**Sources :** `ui/views/liste_present_view.py`, méthode `_export_pdf` ; `ui/views/personnel_list_view.py:1628–1731` ; `services/document_pdf_output.py`.

### P03 — Sauvegarde/restauration : plusieurs copies du SQL complet en mémoire — P2, confirmé par le code

Le dump de bas niveau sait écrire progressivement dans un fichier. Le service de sauvegarde relit ensuite ce fichier entier en chaîne, l'encode de nouveau pour le ZIP, et le chemin de restauration relit/découpe lui aussi le SQL entier.

**Conséquence à grande taille :** pic mémoire et durée de traitement qui augmentent avec tout l'historique mensuel, même si les écrans courants restent rapides. Le seuil gênant dépend de la taille réelle de la base et de la mémoire des postes ; il n'a pas été déterminé sur les données de production.

**À améliorer :** conserver le traitement en flux jusqu'au ZIP et à la préparation de restauration, avec limites, contrôles d'intégrité et suivi de progression.

**Sources :** `services/backup_restore_service.py:502–517`, `:623–656`, `:873–876`.

### P04 — Le journal global aura besoin d'une stratégie d'indexation avec la croissance — P2, risque identifié

Les recherches par noms, contenu JSON et listes d'agents utilisent des expressions non directement couvertes par les index actuels. Le mois utilise `LEFT(...)`. Le catalogue des opérations lit les couples distincts du journal. La limite de résultat borne les lignes affichées, pas forcément le travail de recherche.

Les schémas livrés disposent surtout d'index simples sur date, action et auteur. Cela peut suffire au volume actuel ; aucune lenteur de production n'est affirmée sans plan d'exécution et mesures sur un volume représentatif.

**À améliorer :** cibles d'audit normalisées, filtres mensuels indexables, index composés justifiés par `EXPLAIN`, puis pagination si nécessaire. Éviter l'ajout d'index au hasard.

**Sources :** `repositories/app_config_repository.py`, méthode `search_audit_journal` ; `data/schema/mysql_app_config.sql:32–34`.

### P05 — Imports et maintenances comportent des traitements proportionnels à tout le lot — P2, à mesurer

L'import des carrières reste une grosse boucle d'upserts et de contrôles par agent. Plusieurs caches de référentiels réduisent déjà son coût. Les plans de maintenance calculent des empreintes de tables et, lors de l'application, peuvent verrouiller et relire des ensembles importants.

**À améliorer :** compter les requêtes et chronométrer chaque phase sur des archives synthétiques de taille comparable au travail réel. Réduire ensuite les allers-retours et les verrouillages longs sans sacrifier les comparaisons de versions ni les sauvegardes.

**Sources :** `repositories/mysql_agent_repository.py:1316`, `services/automatic_maintenance.py:65–105`.

### Mesure disponible : calcul des indicateurs

Un benchmark CPU existant, exécuté pendant cette campagne, donne environ **24 ms pour 5 000 lignes** et **119 ms pour 20 000 lignes** pour le cœur de calcul optimisé des indicateurs. Il utilise une population synthétique et un prédicat de présence simulé ; SQL, chargement des fenêtres et impression ne sont pas mesurés par ces chiffres. Les essais ont été réalisés sur ce poste, sans en faire une garantie de délai en production.

Cela oriente l'effort vers les échanges MySQL, les rafraîchissements et les rendus lourds avant de réécrire le petit calcul de comptage déjà optimisé.

**Preuves :** `build/audit_complet_20261102/test_dashboard_kpi_benchmark/**/kpi_cpu_*.json`.

## 4. Synchronisation et modèle des données

### S01 — La synchronisation est périodique et peut être différée — P2, comportement à rendre visible

Le client vérifie une révision partagée toutes les 10 secondes. Les dialogues modaux et les saisies reconnues peuvent retarder son application. Une erreur de lecture de la révision est actuellement journalisée au niveau debug, sans état durable de désynchronisation visible dans ce chemin.

**À améliorer :** afficher dernière synchronisation réussie, attente pendant saisie, actualisation en cours et connexion/synchronisation indisponible. Un délai de 10 secondes n'est pas, en lui-même, une anomalie ; l'absence d'état clair rend difficile la distinction entre attente normale et panne.

**Sources :** `ui/shell_window.py:698–702`, `:1224–1270`.

### S02 — Les notions de mois et de situation actuelle demandent une discipline commune — P2, dette structurelle

Le code gère mois actif, mois consulté, période liée, dates de début/fin et indicateur `est_actuelle`. Ces représentations servent des besoins différents, mais tous les chemins ne reposent pas sur une expression identique du contexte.

Le module des présents a récemment été renforcé pour utiliser explicitement son mois, y compris lorsque des indicateurs « actuels » sont incohérents. Cela ne permet pas de déduire que toutes les autres lectures seraient fausses : les invariants sont normalement entretenus à l'import et au changement de mois.

**À améliorer :** contrat de contexte unique, invariants vérifiables après chaque écriture et tests croisés liste générale / présents / numérique / dossier / exports pour un même mois. Éviter une nouvelle migration temporelle massive avant d'avoir défini précisément les cas à conserver.

**Sources :** `config/liste_generale_scope.py:120–210`, `repositories/mysql_agent_repository.py`, `services/mysql_import_period.py`.

### S03 — Dossier et projection de carrière restent deux représentations à coordonner — P2, choix métier à préciser

Les diplômes, passations et affectations alimentent le dossier ; les situations mensuelles conservent leurs champs projetés. Les services synchronisent déjà ces données en transaction, ce qui est un point solide.

Un cas mérite une règle explicite : effacer le diplôme affiché dans la liste n'entraîne pas, par ce chemin, la suppression du diplôme historique. Une projection ultérieure peut donc le réafficher. Conserver l'historique est légitime, mais l'interface doit expliquer ce que signifie « vider ce champ ».

**À décider :** correction du libellé, retrait d'un diplôme erroné, masquage sur une situation mensuelle ou conservation comme qualification acquise. Ces actions ne doivent pas partager un même geste ambigu.

**Source :** `services/agent_dossier_sync.py:88–145`.

### S04 — La recette sur les vrais postes et le partage réseau reste nécessaire — P1 d'exploitation, validation manquante

Le kit de validation SMB et les protections de publication/verrouillage sont présents. Le compte rendu disponible indique que les essais sur les deux vrais postes et le partage de travail n'ont pas été exécutés. Les essais locaux de cette campagne ne les remplacent pas.

**À valider sur place :** droits de lecture/remplacement, lettres de lecteurs différentes, déconnexion du partage, photos/insignes vus des deux postes, modifications simultanées et rétablissement de connexion.

**Source :** `docs/VALIDATION_SMB_POSTES_20261030.txt`.

## 5. Sauvegarde, restauration, droits et confidentialité

### E01 — La maintenance de restauration dépend encore d'une consigne opérateur — P1 conditionnelle

La restauration impose une confirmation de fermeture des autres postes et utilise un verrou MySQL pour protéger la restauration. Les écritures métier ordinaires ne participent pas toutes à ce même verrou de maintenance.

**Risque :** si un autre poste continue à écrire pendant la préparation/bascule, l'acquittement de la consigne ne constitue pas une protection technique globale contre la perte de ces écritures.

**À améliorer :** mode maintenance partagé, refus des nouvelles écritures, vérification des sessions actives et procédure de sortie/reprise. En attendant, la consigne de fermeture des autres postes doit être réellement exécutée.

**Sources :** `services/mysql_restore_staging.py`, `services/shared_reference_write.py:57`, `ui/views/backup_restore_view.py:526`.

### E02 — Le journal peut revenir à un état antérieur lors d'une restauration — P1 à arbitrer

L'option de conservation de l'audit est décochée par défaut. Une restauration peut donc remplacer le journal par celui de la sauvegarde. C'est compatible avec une restauration globale d'état, mais différent d'une traçabilité continue des opérations effectuées depuis.

L'interface parle en outre de journal/comptes **« de ce poste »**, alors que leur contenu central MySQL peut concerner tous les postes de la base sélectionnée.

**À améliorer :** formulation « de cette base / de tous les postes concernés », conséquence explicite sur l'historique et choix d'une politique de conservation. Conserver une trace séparée des opérations de restauration.

**Sources :** `ui/views/backup_restore_view.py:121–124`, `:169–172`, `:526–543`, `services/backup_restore_service.py`, `RestoreScope`.

### E03 — Une sauvegarde complète combine des éléments capturés à des instants différents — P2, risque identifié

Le dump SQL dispose d'un snapshot cohérent. La collecte de la configuration exportée séparément, des médias et des fichiers locaux intervient ensuite. Pendant des écritures concurrentes, cette archive globale n'est pas nécessairement un instantané unique de tous ces composants.

**À améliorer :** vérifier les révisions avant/après, définir la cohérence attendue des médias et de la configuration et, pour les opérations qui l'exigent, utiliser une fenêtre de maintenance. Ce constat ne signifie pas que chaque sauvegarde actuelle est invalide.

**Sources :** `services/backup_restore_service.py:623–685`, `services/mysql_backup_service.py`, `_python_sql_dump`.

### E04 — L'état « sauvegarde récente » et la rétention sont peu liés au profil de base — P2, confirmé par le code

Le choix de la sauvegarde récente repose sur les fichiers trouvés, leur nom et leur date. La rétention de 15 archives dans un même dossier ne distingue pas leur base d'origine.

**Risque en environnement multibases :** indicateur rassurant pour la mauvaise base, ou suppression progressive des sauvegardes d'un autre profil. Les opérations destructives importantes créent actuellement une nouvelle sauvegarde avec une ancienneté maximale de zéro : elles disposent donc d'une protection plus forte que le seul indicateur.

**À améliorer :** classement par serveur/base, lecture et contrôle du manifeste, rétention par périmètre, et séparation des sauvegardes usuelles des points de récupération de restauration.

**Sources :** `services/app_health_service.py:35–67`, `services/backup_restore_service.py:1001–1037`.

### E05 — Les paramètres de sécurité du poste méritent un durcissement — P1/P2 conditionnelles

- Le mot de passe MySQL est enregistré en clair dans le profil JSON lorsqu'il est sauvegardé. La protection dépend donc des droits Windows sur ce fichier.
- Les paramètres de connexion livrés utilisent par défaut un compte MySQL très privilégié et un mot de passe vide. Cela ne prouve pas que ton installation utilise ces valeurs.
- L'authentification applicative est désactivable, et la configuration initiale prévoit un mot de passe d'amorçage connu du code. L'état réel de production n'a pas été lu pendant cet audit.
- Le profil n'expose pas un réglage clair permettant d'exiger et de vérifier un certificat serveur MySQL. Le chiffrement effectif des connexions de travail n'a pas été mesuré ; l'absence d'option dans l'écran ne prouve pas à elle seule une connexion en clair.

**À améliorer :** compte MySQL dédié aux droits nécessaires, secret stocké via une protection Windows appropriée, politique d'authentification explicite et validation du transport réseau selon l'installation. Les droits de l'application ne doivent pas être confondus avec les droits d'un utilisateur disposant directement des identifiants MySQL.

**Points positifs :** mots de passe applicatifs dérivés avec sel et PBKDF2 ; comparaison constante ; identifiants du client natif MySQL placés dans un fichier temporaire protégé plutôt que dans la ligne de commande ; suppression du mot de passe MySQL du `paths.json` sauvegardé.

**Sources :** `config/user_paths.py:142–150`, `config/settings.py:194–210`, `config/auth_config.py:24–26`, `services/auth_service.py:40–74`, `services/mysql_private_options.py`, `services/backup_restore_service.py:666–671`.

### E06 — Les archives de sauvegarde et de récupération sont des données sensibles — P2 d'exploitation

Les ZIP usuels ne sont pas chiffrés par ce chemin. Ils peuvent contenir la base, l'historique sensible, des empreintes de mots de passe applicatifs et des médias. Les anciens schémas de récupération et dossiers de restauration sont volontairement conservés : il faut une politique de nettoyage après validation, sans supprimer aveuglément les moyens de reprise.

**À améliorer :** destination protégée, copie hors du poste selon la politique retenue, rétention documentée, exercice de restauration, inventaire et nettoyage guidé des récupérations validées. Aucune conclusion de conformité légale n'est formulée ici.

**Sources :** `services/backup_restore_service.py`, `services/mysql_restore_staging.py`, `services/restore_files.py`.

## 6. Code mort, duplication et maintenance

### D01 — Quelques candidats de nettoyage sont identifiés — P3

| Candidat | Constat actuel | Décision proposée |
|---|---|---|
| `repositories/structure_access_repository.py` | Aucun appel trouvé depuis l'application, les scripts ou les tests examinés | Retirer après confirmation de l'absence d'usage externe |
| `repositories/subdivision_access_repository.py` | Même constat | Même traitement |
| `ui/structure_fonctions_dialog.py` | Ancien dialogue non branché dans la navigation/les actions actuelles ; conservé dans des tests | Retirer le chemin obsolète ou le rebrancher si un besoin existe |
| `services/fonction_smart_link.py` | Suggestions de rattachement testées, sans appel applicatif trouvé | Décider entre exploitation de la fonction et retrait |
| `ui/view_lifecycle.py`, classe `CachedViewMixin` | Mixin sans utilisateur trouvé ; `ViewDataState` du même fichier est, lui, utilisé | Nettoyer la classe seule, pas tout le fichier |

Ces candidats représentent quelques centaines de lignes. Leur retrait allégerait surtout la maintenance ; il ne faut pas attendre d'eux une forte accélération du démarrage ni une baisse majeure de taille du portable.

**À conserver comme outils utiles :** import Excel mensuel par scripts, migration de schéma, synchronisation depuis les maquettes, nettoyage/maintenance et autres services appelés en ligne de commande. Leur absence du menu principal ne signifie pas qu'ils sont morts.

L'indice réservé de l'ancien import CSV retiré sert à préserver les autres indices de navigation ; il ne faut pas décaler toute la navigation pour récupérer cette position.

### D02 — La logique reste concentrée dans de très gros fichiers — P2 de maintenance

| Fichier | Lignes physiques |
|---|---:|
| `ui/views/edition_sme_view.py` | 4 243 |
| `ui/views/limite_age_view.py` | 2 803 |
| `ui/shell_window.py` | 2 646 |
| `ui/personnel_dossier_dialog.py` | 2 231 |
| `ui/views/users_auth_view.py` | 1 987 |
| `ui/views/personnel_list_view.py` | 1 860 |
| `ui/views/stages_view.py` | 1 826 |

La taille seule n'est pas un bug. Ici, les mêmes fichiers mêlent construction de fenêtres, contexte mensuel, traitements métier, dialogues, chargement et exports. La correction du panneau rapide sans correction identique de l'éditeur avancé illustre le coût de chemins proches maintenus séparément.

**À améliorer progressivement :** contrat commun de brouillon/chargement, orchestration de contexte, services d'export et composants de tableaux. Conserver le comportement testé pendant les extractions ; éviter de réécrire tous les écrans à la fois.

### D03 — La documentation et les libellés ne sont pas tous au même niveau — P3

Des textes évoquent encore ODBC/Access pour des lectures MySQL, des « fichiers/disque » pour une configuration partagée, `ListeGenerale` comme détail interne, ou des données « de ce poste » pour une base centrale. Certaines feuilles de route décrivent comme futures des améliorations déjà livrées.

**À améliorer :** un inventaire de référence des modules, règles, droits et périmètres ; un seul statut « livré / en cours / envisagé / abandonné » par chantier. Les anciens rapports doivent rester datés et explicitement historiques.

Les titres **Édition SME** et **SME DPF** sont conservés selon le choix de l'utilisateur ; leur nom n'est pas classé ici comme une anomalie.

## 7. Fonctions inachevées, besoins non couverts et décisions métier

Je n'ai pas trouvé de grande série d'écrans directement raccordés à des `TODO` ou à des méthodes non implémentées. L'unique `NotImplementedError` détecté est celui d'un mixin de base, sans appel courant identifié. Un message de liaison non prête ne signifie pas une fonction inachevée lorsqu'un callback est correctement branché par la fenêtre principale.

Les manques concrets sont plutôt les suivants :

1. **Journal global non exhaustif de toutes les interactions.** Il rassemble les événements existants. Les clics/consultations ne sont pas tous enregistrés ; des cibles et mois restent à normaliser. L'écran propose une limite et un affinage des filtres, pas une pagination générale ni un export global de tout le résultat.
2. **État de synchronisation trop peu explicite.** Pas de suivi complet de dernière réussite / attente / erreur dans le parcours courant.
3. **Protection des brouillons non uniforme.** Pas de contrat appliqué à tous les écrans et panneaux.
4. **Recette réseau réelle encore manquante.** Le kit existe, les preuves sur les vrais postes restent à produire.
5. **Alertes parcours : filtres de diagnostic et remise à zéro.** Les améliorations M01/M02 du précédent audit avaient été différées volontairement ; elles restent des améliorations d'ergonomie à reconsidérer, pas des corrections promises et oubliées.
6. **Suggestions automatiques de rattachement de fonctions.** Un moteur existe sans branchement applicatif trouvé ; il faut décider si cette aide apporte quelque chose au processus réel.
7. **Procédure de récupération/maintenance et nettoyage guidé.** Les mécanismes techniques existent, mais l'exploitation ne devrait pas dépendre uniquement de dossiers et de scripts connus du développeur.

### Questions à trancher avant des changements métier

- Une valeur inconnue de position administrative doit-elle bloquer un export, être exclue ou être présentée comme « à vérifier » ? Les positions vides restent conservées et signalées selon le choix déjà fait.
- La case du référentiel doit-elle primer sur les exceptions historiques de présence ?
- Effacer un diplôme de la liste signifie-t-il corriger une erreur, retirer une projection mensuelle ou supprimer un événement historique ?
- L'audit doit-il rester continu à travers une restauration, ou suivre l'état restauré par défaut ?
- Quels événements de consultation doivent réellement être journalisés, et pendant combien de temps ?
- Quels états doivent être figés à la date d'un mois passé : identité, âge affiché, critères d'avancement, paramètres réglementaires et modèles de documents ? Les dates de référence actuelles ne représentent pas toutes le même besoin.
- Une restauration doit-elle imposer techniquement un arrêt des écritures de tous les postes ?

Les barèmes de retraite et d'avancement sont des paramètres métiers sensibles. Leur validité réglementaire, les cas particuliers et les textes de référence nécessitent une validation métier distincte de cet audit informatique.

## 8. Construction, versionnement et déploiement

### L01 — Des fichiers nécessaires à la construction se trouvent dans `build`, non suivis — P1 de livraison

`scripts/build_portable.cmd` dépend de `build/sme_gendarmerie_portable.spec`, lequel dépend d'un hook dans ce même dossier. `build/` est ignoré par Git ; la vérification de suivi du `.spec` n'a renvoyé aucun fichier.

**Conséquence :** nettoyer `build` ou repartir d'une copie propre du dépôt peut retirer la recette indispensable à la reconstruction. Il faut distinguer fichiers de construction versionnés et résultats générés.

**À améliorer :** déplacer et suivre les spécifications/hooks utiles dans un dossier de construction contrôlé ; vérifier une construction depuis un environnement propre.

### L02 — Le portable collecte largement les dépendances — P2, opportunité d'allègement

Le `.spec` utilise `collect_submodules('sme_gend')` et `collect_all('PySide6')`, avec certaines exclusions. Cette stratégie est robuste pour éviter des imports manquants, mais peut embarquer des modules Qt et outils non nécessaires au client courant.

**À améliorer :** mesurer la composition réelle du portable et conserver explicitement les modules/plugins nécessaires. L'effet sur la taille doit être mesuré sur un build ; aucun gain chiffré de taille n'est promis ici.

### L03 — La construction peut inclure les photos présentes dans les ressources de données — P2 conditionnelle

Le dossier `data/personnel_media` est collecté en entier s'il existe. Une présence de photos réelles à cet endroit peut donc les inclure dans la distribution. Aucune photo réelle n'a été examinée pendant l'audit et aucune fuite effective n'est affirmée.

**À améliorer :** séparer les ressources publiques livrées, comme les insignes, des médias des agents ; fournir un mode de construction vierge contrôlé. Le contrôle des fichiers Access vides déjà présent est un bon exemple à prolonger aux autres données.

### L04 — L'environnement et les dépendances ne sont pas entièrement reproductibles — P2

Pas de fichier de verrouillage des versions identifié dans le périmètre examiné. La construction installe des versions autorisées par plages. La version de projet reste `0.1.0`, peu utile pour distinguer les livraisons successives.

Sur le poste d'audit : Python 3.13.1, PySide6 6.9.1, PyMySQL 1.2.0, openpyxl 3.1.5, python-docx 1.2.0 et Pillow 12.1.1. Ce dernier ne correspond pas à la contrainte Pillow du manifeste pour Python 3.13 ; l'outil optionnel `python-calamine` manque aussi pour les tests concernés. Cela ne constitue pas une déclaration de vulnérabilité de ces versions.

**À améliorer :** environnement de construction maîtrisé, version de livraison visible, manifeste des versions et contrôle des dépendances. Une amélioration de la source Python n'est pas automatiquement présente dans un ancien exécutable portable : il faut une reconstruction et une recette du portable.

### L05 — La suite de tests est riche mais son lancement global manque de fiabilité — P2

Des tests supposent deux répertoires de lancement différents. D'autres dépendent de protections Windows indisponibles ici, d'un moteur Excel optionnel ou de doubles qui ne correspondent plus aux contrats actuels. Lancer le seul sous-dossier d'intégration provoquait aussi un problème d'import d'un module de tests parent ; un chemin de tests explicite a été nécessaire pour la campagne.

Les tests de redimensionnement réussissent individuellement, mais leur suite accumulée a dépassé la limite fixée dans le processus d'audit, pendant un changement de thème Qt. Ce résultat ne suffit pas à affirmer que le changement de thème de l'application réelle est cassé.

**À améliorer :** un lanceur de référence, chemins de ressources indépendants du dossier courant, dépendances de test déclarées, nettoyage des objets Qt, tests Windows de protections sur un poste adapté et contrôle automatique à chaque livraison. Aucun pipeline CI de référence n'a été identifié dans le périmètre examiné.

### L06 — Les prérequis de restauration sont plus précis que le simple mot « MySQL » — P2

La bascule de restauration accepte les versions MySQL compatibles prévues par le code, exige InnoDB et refuse notamment MariaDB, les objets SQL avancés et certaines références entre bases. Le dump interne impose également son périmètre d'objets pris en charge.

Ces refus protègent les données. Leur périmètre doit néanmoins figurer dans la documentation d'installation et dans un contrôle préalable, pour éviter de découvrir au moment d'une reprise qu'une installation personnalisée sort du cadre prévu.

**Sources :** `services/mysql_restore_staging.py:107–124`, `services/mysql_backup_service.py:66–87`, `README.md`.

## 9. Bilan par ensemble fonctionnel

| Ensemble | Appréciation | Travaux principaux |
|---|---|---|
| Tableau de bord / statistiques | Calculs déjà optimisés ; beaucoup de scénarios synthétiques couverts | Signaler les données indisponibles, mesurer la chaîne SQL + affichage |
| Liste générale / Édition SME / SME DPF | Cœur métier développé, filtres et écritures protégés par de nombreux tests | Contexte mensuel uniforme, clarification des corrections d'identité et des projections |
| Dossier agent | Historique, regroupements et contrôles de concurrence présents | Chargement plus fluide et absence de données distinguée d'une erreur |
| Liste des présents / mise en page | Correctifs récents utiles, export détaillé renforcé | Éditeur avancé, brouillons face à la synchronisation et cohérence de présence |
| Détachés | Comparaison mensuelle et exports dédiés, configuration partagée | Visibilité des erreurs de référentiel et recette des configurations sur plusieurs postes |
| Stages / missions / congés / maladies / punitions / désertions | Plusieurs chemins historiques et mensuels testés ; protections déjà en place | Garder les mêmes règles de dates, d'origine et de contexte entre dossier, liste et export |
| Gains et pertes | Écritures et versions testées ; séparation des calculs et mouvements conservée | Vérifier sur données métier les rapprochements de mois, entrées et sorties |
| Retraite / attestations / soit transmis | Fonctions développées ; modèles et redimensionnements testés | Validation métier des barèmes et recette Word/PDF réelle ; fluidité des rendus |
| Avancement | Moteur et règles configurables, cas manuels représentés | Validation métier des règles, dates de référence et cas particuliers |
| Structures / fonctions / grades / subdivisions / organigrammes | Contrôles de concurrence et rattachements présents | Réduire chemins historiques inutilisés, clarifier effet global/mensuel des opérations |
| Import Access / remplacement / reprise / transfert | Sauvegardes, plans et contrôles importants existent | Mois de journalisation, mesure des gros lots et recette de vraies archives anonymisées |
| Gestion des mois / archives / clôture | Processus développé, protections transactionnelles | Trace atomique du vidage, uniformité du contexte et procédure réseau |
| Comptes / droits / sessions | Base solide de mots de passe et permissions | Révocation, comportement du registre indisponible et cache par base |
| Journaux | Vue globale plus deux vues spécialisées opérationnelles | Exhaustivité des cibles/mois, indexation, politique de restauration et conservation |
| Sauvegarde / restauration | Préparation, validation, récupération et protections existantes | Mémoire, maintenance réellement partagée, cohérence des composants et recette Windows |
| Paramètres / médias / portable | Profils et ressources organisés ; contrôle des Access embarqués | Secrets, médias de distribution, recette de build reproductible et validation SMB |

« Testé » signifie couvert par les scénarios indiqués, pas validé sur toutes les données de production ni sur tous les périphériques.

## 10. Ordre de travail proposé

### Lot A — Fermer les défauts qui affectent la confiance

1. Révocation/registre des sessions et cache par base : C03–C05.
2. Trace atomique du vidage et mois normalisé des imports : C01–C02.
3. Recherche d'audit après fusion : C08.
4. Conservation des brouillons dans les deux éditeurs et lors des actualisations : C06–C07.
5. Arbitrage et application cohérente des règles de présence : C09.

Critère de réussite : les reproductions jointes deviennent des tests de non-régression du comportement corrigé, sans effet sur les autres mois, utilisateurs ou journaux.

### Lot B — Rendre les traitements fluides et les erreurs compréhensibles

- Supprimer les requêtes réseau du contrôle de session graphique et du chargement initial des dossiers.
- Différencier « vide » et « indisponible ».
- Afficher l'état de synchronisation et préserver les saisies pendant une actualisation distante.
- Mesurer les gros imports, l'impression PDF et le journal avant d'optimiser leurs parties coûteuses.

### Lot C — Sécuriser l'exploitation réelle

- Recette avec deux postes et le vrai partage.
- Procédure/mode maintenance de restauration.
- Politique d'audit après restauration, de sauvegarde par base et de récupération.
- Profils MySQL, secrets et authentification conformes aux règles retenues pour les postes.

### Lot D — Alléger la maintenance et les livraisons

- Recette de construction suivie hors des résultats `build`.
- Versions maîtrisées, tests reproductibles et manifeste de livraison.
- Nettoyage des candidats morts après validation des usages.
- Extraction progressive des composants partagés des gros écrans.

Le retrait massif de modules métier ou une réécriture de l'application ne seraient pas les premières actions proposées : les gains les plus directs viennent des quelques chemins prioritaires ci-dessus.

## Annexe — Validation, preuves et limites

### Tests hors intégration

Campagne par module : **229 modules**, dont 217 réussis directement, 11 avec échecs/erreurs et un arrêté à la limite de temps. Les XML disponibles comptabilisent 1 890 cas : 1 847 réussites, 37 échecs et six erreurs de préparation. Le module interrompu représente 19 cas non comptabilisés par ces XML.

Relances sans modification du code applicatif :

- **68/68** pour les deux fichiers dépendant du dossier de lancement, après lancement depuis la racine attendue : six erreurs et un échec de chemin résolus.
- **19/19** cas de redimensionnement dans des processus séparés.

Bilan hors intégration après ces relances : **1 873 réussites sur 1 909 cas collectés** ; 36 cas restent non validés dans cet environnement :

- 30 liés aux protections/répertoires privés Windows, dont un test de client natif ne parvenant pas à créer son fichier protégé ;
- trois liés au moteur Excel optionnel `python-calamine` absent ;
- deux anciens doubles de test d'export structure sans la méthode `_require_fresh` ;
- un test de branche moderne Qt simulée, alors que l'API `Direction` n'existe pas dans le Qt installé. Le modèle Qt réel de cette version utilise le chemin de compatibilité.

Ces 36 cas ne sont pas présentés comme 36 pannes métier confirmées. Ils restent à remettre en état ou à valider dans l'environnement adéquat. Aucune neutralisation des protections Windows n'a été utilisée pour annoncer leur réussite.

### Reproductions supplémentaires

**Neuf reproductions synthétiques réussies** confirment les comportements défectueux décrits : perte de titre avancée, brouillon effacé par rechargement, cache de registre entre bases, politique de session non garantie sans registre, révision distante acceptée en mode sans plafond, mois stocké hors de la colonne filtrée, exceptions de présence, audit du vidage après commit et scan interrompu laissant un événement de fin non déclenché.

**Une reproduction MySQL supplémentaire** confirme l'alias conservé mais absent de la recherche globale par dossier canonique. Ces tests affirment le défaut actuel : leur succès n'indique pas que le défaut est corrigé.

### Intégration MySQL

La campagne principale, hors grand fichier historique, a exécuté **275 cas** : 259 réussites, neuf échecs et sept erreurs de préparation. Les sept erreurs concernaient le répertoire de lancement du transfert mensuel. Après relance depuis le répertoire attendu et autorisation strictement bornée du client `mysqldump` vers le serveur et le schéma jetables, les huit cas de ce fichier réussissent. Sept de ces réussites remplacent les erreurs de la campagne principale.

Le grand fichier historique `test_restore_mysql.py` contient 558 cas dans la collecte examinée. **35 ont été exécutés avant l'arrêt configuré à 20 échecs** : 15 réussites et 20 échecs. La plupart des échecs précoces surviennent avant le scénario utile, sur les protections du miroir local sensible indisponibles ici. Une relance ciblée de 19 cas avec les miroirs JSON optionnels désactivés par leur réglage prévu donne **18 réussites** ; le cas restant utilise un ancien double de dialogue sans le contrat de regroupement désormais requis. Cette relance valide les chemins SQL concernés, pas les miroirs Windows.

Sur les **310 cas natifs distincts effectivement lancés**, **299 sont ainsi validés dans les conditions précisées**. Les 11 cas non validés se répartissent en :

- six cas bloqués par les protections Windows des sauvegardes/miroirs ;
- quatre cas de gains/pertes utilisant un ancien schéma de test sans table d'alias ou colonne de statut ;
- un ancien double de dialogue de dossier incomplet.

Les 523 autres cas du grand fichier historique n'ont pas été exécutés dans cette campagne. Ce bilan ne prétend donc pas que toute la suite native de l'application est verte. Il montre aussi pourquoi les fixtures et le lancement de référence doivent être remis en cohérence.

Les schémas jetables ont des noms uniques ; l'instance, son port distinct de la production et son répertoire de données sous `build` sont vérifiés. Aucune requête métier n'a été exécutée sur la base de production. Le client natif n'a été autorisé que pour une commande de dump exacte visant cette instance et un nom de schéma de test contrôlé ; les autres processus externes restent bloqués.

### Limites restantes

- Pas d'audit des enregistrements personnels de production, de leurs doublons réels ni de leurs totaux.
- Pas de validation réglementaire des limites d'âge et conditions d'avancement.
- Pas d'essai sur les vrais postes distants/SMB, ni sur leur réseau et leurs comptes Windows.
- Pas d'impression papier, de vérification de toutes les variantes Word installées ni de recette d'un nouvel exécutable portable.
- Les résultats des tests avec collaborateurs simulés et des benchmarks CPU ne remplacent pas une mesure de bout en bout.
- L'absence de `TODO`, d'import manquant ou d'erreur de syntaxe ne prouve pas l'absence de défaut fonctionnel.

### Fichiers de preuve

Sous `sme_python/build/audit_complet_20261102/` :

- `inventory.json` et `inventory.py` : inventaire/contrôles statiques ;
- `matrix_results.json`, `matrix_output.txt`, sous-dossiers par module et XML : campagne hors intégration ;
- `workspace_rerun.txt`, `responsive_results.json` : relances contextualisées ;
- `test_findings.py`, `reproductions.txt` : reproductions synthétiques ;
- `test_journal_alias.py`, `alias_repro.txt` : reproduction native de l'alias ;
- sorties et XML d'intégration ;
- traces de benchmark synthétique.

Les artefacts d'audit ne modifient pas le code métier et ne constituent pas une nouvelle version portable.
