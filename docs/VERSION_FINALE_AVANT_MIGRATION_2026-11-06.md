**Version finale avant migration — 6 novembre 2026 — 0.1.28**

Point de retour demandé avant la refonte progressive de l'architecture. Cette appellation identifie la fin de l'ancienne architecture ; elle ne signifie pas absence d'anomalies.

État conservé : interface compacte, filtres avancés, zoom, navigation latérale, déconnexion, sessions actives, références/session des stages, création complète U/D/T, fins de stage datées, ajout groupé de diplômes et contrôles associés.

L'audit global du 6 novembre décrit les défauts encore présents, notamment droits de période/configuration, fins de mission/congé, concurrence de stages, avancement et cohérence des sauvegardes. Voir `AUDIT_GLOBAL_2026-11-06.md`.

Les sources et ressources publiables sont conservées sur une branche d'archive et un tag datés. Le dépôt distant étant public, profils de connexion, secrets, journaux, exports et archives nominatives ne font pas partie de la sauvegarde publiée. Une sauvegarde Git locale privée conserve l'historique et les changements suivis avant le nettoyage de l'index public.

L'exécutable est construit avant les corrections de migration, dans un nouveau dossier portable, sans ZIP et sans remplacer une livraison précédente. Le dossier complet doit être conservé avec l'exécutable et son `BUILD_INFO.json`.

La migration commence sur une branche distincte. Aucun changement de schéma ou remplacement de données n'est exécuté sur la production par cette étape.

Les modèles Excel/Word et Access présents localement sont exclus du dépôt public, car ils peuvent contenir des données réelles. Pour reconstruire une livraison depuis GitHub, remettre les modèles privés dans `sme_python/modeles/` et le modèle officiel `SME_GEND_model.xlsx` à la racine. Ils sont conservés avec l'historique Git original dans le bundle local privé `sme_python/build/backups/2026-11-06-before-migration/`. Ce bundle ne doit jamais être envoyé au dépôt public. Cette sauvegarde du projet ne remplace pas une sauvegarde de la base MySQL.

La copie publique masque également la valeur du mot de passe initial dans `config/auth_config.py`. La copie locale privée conserve la source exacte correspondant à l’exécutable 0.1.28. Pour construire depuis la copie publique, définir une valeur initiale privée avant utilisation ; le marqueur de remplacement n’est pas un identifiant de production. L’empreinte source du binaire concerne la copie locale intégrale.
