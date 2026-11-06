# Validation du correctif V2 0.1.3 — 2 novembre 2026

Le signalement concernait deux postes utilisant la version 0.1.2, sans
restauration lancée : lenteurs de recherche et modification refusée avec un
message de maintenance. Le verrou global des opérations ordinaires a été
remplacé par des enregistrements indépendants des écritures actives. Les
principales lectures ont été séparées des écritures (68 contextes dans 24 fichiers).

## Résultats

- **1 942 cas unitaires uniques validés**, après reprise complète des six modules
  dont les doubles SQL n'acceptaient pas encore l'option `read_only`. Leurs
  assertions métier ont été conservées. Le résultat consolidé correspond
  exactement à l'inventaire collecté, sans échec ni test ignoré.
- **63 tests MySQL ciblés réussis**, dont deux écritures parallèles sur des agents
  différents, une recherche et les référentiels pendant une modification ouverte,
  la fermeture des admissions pour une vraie maintenance, l'attente de la fin
  d'une écriture et le nettoyage des enregistrements de sessions interrompues.
- Les recherches testées n'entrent pas dans la coordination des écritures.
  Les deux transactions de modification ont pu entrer avant que l'une termine.
- Les protections d'audit, d'historique, de révocation des sessions et de
  restauration, dont la restauration ZIP, restent validées par la campagne ciblée.
- Autotest du portable réussi, empreintes des sources et de l'exécutable vérifiées,
  et contrôle CRC de tous les membres du ZIP réussi.

Les preuves consolidées sont dans `build/performance_hotfix_20261102/validation.json`.
La campagne MySQL est dans `build/release_checks_bc365b0609ed4c928a3742fc3522f008/`.
Les serveurs jetables sont arrêtés proprement après vérification de leur datadir.
Aucune intervention sur la base réelle de l'utilisateur n'a été exécutée.

## Livrable

- `dist/releases/20261102T185103927026Z/SME_Gendarmerie_portable.zip`
- Taille : **118 657 087 octets**.
- SHA-256 : `9156b0eca4b423a7ff53f578009ab94bbe9a174ccfc0e3175dc046bf59d41bdf`.
- Sources : `b27b8bc2f1c833af841e42aea93d321ab5cb676a3730b6b91df5a18dc45d9646`.

La version doit être remplacée puis relancée sur les deux postes. Les essais
ont été effectués avec plusieurs connexions sur MySQL jetable ; ils ne mesurent
pas les délais du vrai réseau ni le comportement de tous les postes physiques.
Vérifier sur place la recherche et les modifications simultanées après mise à jour.
