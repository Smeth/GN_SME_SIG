# Analyse des grades et des positions administratives — 2 novembre 2026

> État historique avant correction. Les constats ci-dessous sont traités dans [CORRECTIONS_GRADES_POSITIONS_ADMIN_2026-11-02.md](CORRECTIONS_GRADES_POSITIONS_ADMIN_2026-11-02.md), qui décrit le comportement corrigé et sa validation.

Périmètre : référentiels Grades et Positions administratives, effets sur les fiches et les états, validation des formulaires, concurrence, audit et UI/UX. Analyse du code, quatre sondes synthétiques et captures Qt avec le thème réel à 960 × 720 et 1366 × 768. Aucune correction de ces deux modules ni écriture dans les données de production pendant cette analyse. Les sondes vérifient des défauts observés ; leur réussite ne signifie pas que les modules sont sans défaut.

## Grades

### G1 — Validation numérique défaillante — priorité haute

`GradeEditDialog.values()` convertit directement les champs Préséance et Effectif en entiers. Les appels dans `_on_add` et `_on_edit` sont placés avant le bloc de gestion des erreurs. Une saisie `abc` provoque donc une exception au lieu d'une validation dans le formulaire. Des valeurs négatives sont également retournées sans contrôle, y compris pour l'effectif.

Reproduction synthétique confirmée : `Préséance=abc` lève `ValueError` ; `Préséance=-10, Effectif=-12` retourne ces valeurs. Le service transmet les valeurs au dépôt et le schéma utilise des entiers signés.

Correction proposée : validation avant fermeture du dialogue, champs numériques adaptés, effectif positif ou nul, règle explicite pour les bornes et doublons de préséance. Conserver la saisie et indiquer le champ invalide.

Source : `sme_python/src/sme_gend/ui/views/grade_referentiel_view.py:276`, `:674`, `:700` ; `services/referentiel_service.py:366`.

### G2 — L'insigne est enregistré avant la validation du grade — priorité haute

Le bouton de choix d'insigne appelle immédiatement `save_grade_insignia()`. Annuler le dialogue ou échouer ensuite sur la validation du grade n'annule pas la copie de l'image. Un nouveau grade jamais créé peut donc laisser une image enregistrée ; la modification d'un grade peut persister malgré Annuler.

Correction proposée : garder l'image sélectionnée comme brouillon et la publier lors de l'enregistrement confirmé, avec un résultat cohérent entre données et média.

Source : `ui/views/grade_referentiel_view.py:254`.

### G3 — Renommer le grade peut faire disparaître son insigne — priorité haute

La clé du fichier d'insigne est dérivée du libellé. La mise à jour du grade modifie seulement les champs SQL ; elle ne migre pas le fichier de l'ancien libellé. La recherche de l'insigne avec le nouveau libellé peut alors échouer. La transaction SQL et les verrous de fichier existants ne corrigent pas cette divergence d'identité.

Correction proposée : identifier l'image par un identifiant stable de grade et migrer les anciens noms, avec traitement des collisions et des erreurs de copie.

Sources : `services/personnel_media.py:158`, `:261` ; `repositories/mysql_bundle_repositories.py:126`.

### G4 — Accès MySQL et images sur le fil graphique — priorité haute pour la fluidité

Le chargement et les écritures de la vue sont synchrones. Sélectionner un grade appelle `_grade_usage()`, qui construit un dépôt et effectue des comptages MySQL. La sonde Qt confirme l'exécution dans le fil graphique. Le modèle recharge et redimensionne aussi les images dans `data(DecorationRole)`, donc pendant les rafraîchissements d'affichage, sans cache dédié.

Conséquence : latence à chaque sélection, défilement ou chargement si MySQL ou le partage des insignes répond lentement. Il s'agit d'un chemin bloquant confirmé, pas d'une mesure du délai sur les postes réels.

Correction proposée : lectures et écritures en arrière-plan, comptages groupés, cache des miniatures invalidé lors d'un changement de média et rejet des résultats de sélection devenus périmés.

Sources : `ui/views/grade_referentiel_view.py:74`, `:617`, `:659`.

### G5 — Le chemin de suppression de la vue ne produit pas l'audit métier du dépôt — priorité haute

La vue passe par `ReferentielService.grade_delete()`, puis `StructureOrganigramService.delete_grade()` et `MySQLOrganigramRepository.safe_delete_grade()`. Ce dernier supprime et publie une révision, mais n'appelle pas `record_business_action()`. Le dépôt Grades possède bien un audit de suppression, mais ce n'est pas le chemin utilisé par cette vue.

Correction proposée : enregistrer une trace métier atomique dans le chemin réellement exécuté, avec l'état précédent, l'auteur et le code. Une notification de révision ne remplace pas cette trace.

Sources : `services/referentiel_service.py:385` ; `services/structure_organigram_service.py:239` ; `repositories/mysql_organigram_repository.py:494`.

### G6 — Ergonomie et vocabulaire — priorité moyenne

- « Effectif » et « Agents » apparaissent côte à côte : le premier est une valeur saisie, le second un comptage. Leur sens doit être explicité, par exemple « Effectif théorique » si c'est bien la règle métier retenue.
- La fiche latérale n'a pas de défilement propre ; une hauteur faible, un grand facteur d'échelle ou des libellés longs peuvent réduire l'espace utile.
- La sélection revient au premier grade après une actualisation. Une recherche réinitialise le modèle sans restaurer explicitement le grade sélectionné.
- Plusieurs validations interviennent après fermeture du dialogue, ce qui oblige à recommencer la saisie en cas d'erreur.

La capture à 960 × 720 reste globalement lisible : aucune refonte visuelle complète n'est justifiée par ce seul essai.

### Protections déjà présentes

Le référentiel Grades possède un jeton de version vérifié sous verrou pour les modifications et suppressions. La suppression par le service contrôle l'utilisation du grade. Il faut conserver ces protections ; le problème n'est pas une absence générale de gestion de concurrence.

## Positions administratives

### P1 — La case « compte comme présent » n'est pas la règle réellement appliquée partout — priorité haute

La fonction de présence applique d'abord des exceptions métier : « Présent » reste présent même si la case est décochée ; « Mission » reste absent même si la case est cochée. Cela peut être volontaire, mais le référentiel laisse enregistrer la valeur opposée et l'écran annonce que cette colonne détermine la liste des présents et le numérique.

Autre divergence confirmée : un libellé personnalisé « Service spécial » coché présent est reconnu avec un `PadminPresenceIndex`, mais `is_present_strict()` ne reçoit ni index ni paramètres de base. Les chemins utilisant ce prédicat, notamment la construction de matrice numérique et certains exports, ne peuvent pas tenir compte de ce paramétrage personnalisé.

Correction proposée : une règle partagée par SQL, tableaux et exports, et une UI qui explique ou empêche les exceptions non paramétrables. Ajouter des cas comparant le même agent dans chaque état.

Sources : `padmin_present.py:165` ; `services/numerique_padmin.py:15` ; `services/numerique_stats.py:150` ; `ui/views/referentiel_views.py:245`.

### P2 — Création susceptible d'écraser une entrée existante ; absence de version du formulaire — priorité haute

`MySQLPadminRepository.insert()` utilise `ON DUPLICATE KEY UPDATE`. « Nouvelle position » peut donc modifier silencieusement une position déjà présente, notamment son indicateur de présence et son ordre. Les méthodes update/delete ne prennent aucun état attendu ni jeton de version. Deux formulaires ouverts sur des postes différents peuvent se remplacer, même si leurs transactions sont sérialisées.

Correction proposée : refuser le doublon lors de la création, utiliser un identifiant stable et contrôler une version pour la modification et la suppression, comme dans Grades.

Source : `repositories/mysql_bundle_repositories.py:913` ; modèle `models/padmin_ref.py`.

### P3 — Des libellés modifiables servent aussi de codes métier — priorité haute

Les rubriques numériques et certains parcours complémentaires reconnaissent des chaînes fixes : Mission, StageLocal, StageEx, Détaché, etc. Le référentiel autorise pourtant leur renommage sans migration de ces correspondances. Une variante peut cesser d'alimenter sa rubrique tout en restant considérée comme une absence.

Correction proposée : séparer code métier stable, libellé affiché et synonymes ; protéger les codes système et montrer l'impact d'un renommage avant enregistrement.

Sources : `services/numerique_padmin.py:29`, `:47` ; `services/position_admin_events.py:4` ; `ui/views/referentiel_views.py:373`.

### P4 — La portée annoncée du renommage ne correspond pas à l'écriture des libellés — priorité haute

La confirmation annonce la mise à jour de toutes les fiches portant l'ancien libellé. Le SQL de propagation utilise pourtant `carriere_write_scope_for_settings()`, limité au périmètre carrière courant. Le référentiel est, lui, partagé. Les lignes liées par identifiant et les anciennes lignes conservant seulement un texte peuvent alors évoluer différemment. Les registres complémentaires ne sont pas migrés dans cette méthode.

Correction proposée : définir une politique globale explicite, identifier les lignes touchées et les archives, puis migrer les correspondances de façon contrôlée. Éviter une promesse « toutes les fiches » si le périmètre est limité.

Sources : `repositories/mysql_bundle_repositories.py:945` ; `config/liste_generale_scope.py:321`.

### P5 — Suppression : promesse incompatible avec les clés étrangères — priorité haute

L'écran indique que les fiches conserveront leur valeur mais que celle-ci ne sera plus référencée. Le dépôt exécute directement un DELETE ; une situation liée par `position_admin_id` est protégée par une clé étrangère sans suppression automatique. Dans ce cas, MySQL refusera l'opération. Aucun contrôle préalable d'utilisation ne prépare une explication métier.

Correction proposée : préférer un statut inactif pour conserver l'historique, ou bloquer explicitement une position utilisée avec un comptage et une explication. Ne pas annoncer une suppression possible puis présenter une erreur SQL.

Sources : `ui/views/referentiel_views.py:403` ; `repositories/mysql_bundle_repositories.py` (fin de `MySQLPadminRepository`) ; `data/schema/mysql_smegend_unified.sql:275`.

### P6 — Validation silencieuse et audit incomplet — priorité moyenne à haute

- `Ordre=abc` est accepté par le dialogue et transformé en `None`, sans signalement. Reproduit avec le vrai dialogue Qt.
- L'audit de modification transmet `before=None` ; l'audit de suppression transmet `before=None, after=None`. Il ne permet pas de retrouver les anciennes valeurs de présence et d'ordre.
- La vue ne reprend pas le contrôle de période utilisé par Grades. Comme les deux référentiels sont globaux, il faut clarifier une politique cohérente plutôt que copier arbitrairement un verrou de mois.

Sources : `ui/views/referentiel_views.py:231`, `:349`, `:373` ; `repositories/mysql_bundle_repositories.py:913`.

### P7 — Présentation et performances — priorité moyenne

Capture réelle Qt à 960 × 720 : les titres des colonnes « Position administrative » et « Compte comme présent » sont tronqués ; la dernière colonne Ordre occupe presque tout l'espace. Les largeurs ne sont pas adaptées au contenu. Les boutons Modifier/Supprimer restent proposés sans sélection. Il n'y a pas de compteur de résultats ou d'utilisation, contrairement à Grades. Chargement et écritures sont synchrones sur le fil graphique.

Correction proposée : colonnes redimensionnées avec le libellé extensible, actions dépendant de la sélection, compteur, état vide, indicateur de portée et traitement de fond.

## Ordre de traitement proposé

1. Unifier la présence et protéger les codes métier des positions.
2. Ajouter les conflits de versions et supprimer la création avec écrasement implicite.
3. Sécuriser renommages et suppressions, avec audit complet.
4. Corriger les validations des deux formulaires et la sauvegarde des insignes.
5. Sortir SQL et médias du fil graphique, puis harmoniser l'ergonomie.

Les droits de consultation, modification et suppression des référentiels ne sont pas distincts dans le catalogue actuel. Une délégation plus fine peut être ajoutée avec la politique métier correspondante.

## Captures synthétiques inspectées

- [Grades à 960 pixels](../sme_python/build/users_roles_20261102/screenshots/grades_960.png).
- [Positions administratives à 960 pixels](../sme_python/build/users_roles_20261102/screenshots/positions_administratives_960.png).
