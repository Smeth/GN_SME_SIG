# Corrections Utilisateurs et rôles — 2 novembre 2026

Corrections dans le code de la V2, sur la branche de travail actuelle. Aucun nouvel exécutable n'a été généré et aucun commit n'a été poussé pour cette intervention. Les fichiers de données utilisateur déjà modifiés avant l'intervention sont restés hors du périmètre.

## Autorisations et cohérence

- Le basculement de l'authentification active est réservé au super administrateur. Un gestionnaire délégué des paramètres peut encore modifier les délais et les règles de connexion, sans ouvrir l'accès libre. L'interface explique l'effet immédiat d'une désactivation sur le poste et la prise en compte au prochain démarrage des autres postes.
- La normalisation et la sauvegarde ne promeuvent plus un compte parce qu'il se nomme `admin`. L'ancien compte initial peut être migré seulement lors de la lecture d'une configuration ancienne non versionnée, sans super administrateur existant. Les configurations sauvegardées portent un marqueur de politique empêchant de rejouer cette migration.
- Les révocations de sessions individuelles et globales vérifient la même hiérarchie que les modifications de comptes. La révocation individuelle retrouve le compte cible avant d'agir et refuse la session locale courante. Sa trace indique le compte et le poste au lieu d'un préfixe de jeton opaque.
- L'interface ne permet plus à un gestionnaire délégué de modifier un Administrateur, de lui assigner ce rôle ou de déconnecter ses sessions. Les refus restent aussi appliqués par le service.
- `system.backup` signifie sauvegarder ; `system.restore` autorise restaurer. La restauration est réservée aux administrateurs, contrôlée dans la vue et dans le service avant toute écriture. La levée d'une maintenance interrompue exige aussi ce droit. Le rôle Opérateur n'obtient plus la sauvegarde par défaut ; le droit de sauvegarde peut être accordé explicitement sans donner celui de restaurer. Une configuration ancienne retire l'ancien droit groupé de l'Opérateur à la migration et corrige son libellé.
- Les textes distinguent maintenant les rôles non supprimables et les permissions fixes : Administrateur et Super administrateur restent fixes ; Opérateur et Lecteur restent personnalisables.

## Saisies et concurrence

- L'application détecte les modifications de fiche compte, rôle, options de connexion et formulaire de création. Cette détection participe au contrôle global des saisies non enregistrées.
- Changer de compte ou de rôle demande confirmation avant d'abandonner une saisie. Refuser restaure la sélection et conserve les champs. Une simple réapplication des droits ne recharge plus les valeurs du rôle.
- Une actualisation automatique n'écrase pas un brouillon. Une actualisation explicite ou après une action demande confirmation si d'autres modifications sont encore présentes.
- Les sauvegardes de profil et de rôle fournissent leur état attendu au service. Un formulaire devenu périmé à cause d'un autre poste est refusé avec conservation de la saisie.
- Le champ Nom affiché est réactivé au chargement d'une fiche après une sélection vide.

## Fluidité et interface

- Les écritures de comptes et rôles, le calcul des mots de passe et l'audit s'exécutent dans un worker, avec un dialogue de progression. Les valeurs Qt sont lues avant le lancement. Le service du worker est une copie isolée ; son résultat est adopté sur le fil graphique.
- Une déconnexion survenue pendant une écriture n'est pas annulée par l'adoption de son résultat.
- La vérification et la lecture du registre des sessions sont asynchrones, avec vérification des permissions avant affichage du résultat. La détection du mot de passe initial se fait aussi pendant le chargement en arrière-plan.
- Les actions secondaires et destructives sont regroupées dans « Actions du compte… ». Enregistrer reste directement accessible.
- Le formulaire « Nouveau compte… » s'ouvre à la demande pour donner la priorité à la liste et à la fiche. Les panneaux restent accessibles par défilement lorsqu'il est ouvert.
- La fiche dispose de champs sur toute sa largeur. Les filtres du journal n'imposent plus une largeur excessive à tous les onglets ; Comptes et Journal disposent d'un défilement adapté aux petites surfaces.

## Validation

- Suite unitaire complète dans un profil isolé, avec protections Windows natives : **1 956 réussites**, 33 avertissements de dépréciation Qt déjà présents, aucun échec.
- Test complémentaire de lecture asynchrone des sessions et contrôle visuel répété après ajout de ce test : **2 réussites**. Le test asynchrone est un cas supplémentaire par rapport à la collecte de la suite complète.
- Sous-ensemble sauvegarde/restauration, workers et caches : **70 réussites** avec protections Windows natives. Un premier essai en environnement restreint avait échoué sur les ACL natives ; le passage natif a validé ces mêmes cas.
- Tests Authentification ciblés avant le dernier test supplémentaire : **72 réussites**, dont rendu du thème réel, conservation des brouillons, conflits entre postes, exécution hors fil Qt et non-résurrection d'une session révoquée.
- Validation MySQL native ciblée : **44 réussites, 4 cas de mouvements exclus**. Le passage élargi initial a donné **44 réussites et 4 échecs** dans les scénarios de mouvements `gain_insert`, `gain_update`, `gain_delete` : schémas de fixtures incomplets (notamment `agent_identity_alias` absent), sans modification du code métier des mouvements pendant cette intervention. Ces échecs ne sont pas comptés comme une validation réussie.
- Captures avec le thème réel vérifiées à **960 × 720** et **1366 × 768**, avec données synthétiques.
- `git diff --check` sur les sources et tests : aucun défaut d'espacement.

Les tests isolés bloquent l'accès à une base externe et les écritures dans les données réelles. Les essais MySQL utilisent uniquement une instance sous `sme_python/build`, un port distinct de 3306 et des bases synthétiques créées puis supprimées par les fixtures.

Le serveur de test a été arrêté par `SHUTDOWN` après vérification de son datadir. [Capture de la fiche compte corrigée à 960 pixels](../sme_python/build/users_roles_20261102/screenshots/utilisateurs_comptes_960.png).

L'analyse initiale des modules Grades et Positions administratives est conservée dans [AUDIT_GRADES_POSITIONS_ADMIN_2026-11-02.md](AUDIT_GRADES_POSITIONS_ADMIN_2026-11-02.md). Les corrections réalisées ensuite sont décrites dans [CORRECTIONS_GRADES_POSITIONS_ADMIN_2026-11-02.md](CORRECTIONS_GRADES_POSITIONS_ADMIN_2026-11-02.md).
