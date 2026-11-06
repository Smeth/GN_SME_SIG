# Validation de S.G.I DPF 0.1.2 — 2 novembre 2026

Les quatre évolutions sont décrites dans
`MAINTENANCE_AUDIT_HISTORIQUE_2026-11-02.md` : maintenance centralisée,
conservation du journal lors d'une restauration, clarification des retraits
historiques et traçabilité des consultations.

## Résultats finaux

- **1 942 tests unitaires réussis** dans une campagne complète, sans échec ni
  cas ignoré. Les identifiants du XML correspondent exactement à l'inventaire
  collecté. Les 33 avertissements proviennent d'une API d'alignement Qt dépréciée.
- **59 cas MySQL natifs distincts réussis** : journal, sessions, présence,
  purge, restauration transactionnelle, contrôle de maintenance et restauration ZIP.
  Les relances qui recouvrent ces cas ne s'ajoutent pas à leur nombre.
- **459 fichiers Python applicatifs et scripts valides** au contrôle syntaxique.
- `git diff --check` réussi sur le périmètre modifié.
- Portable compilé : six contrôles d'autotest réussis, dont journal/graphiques
  et PDF. Contrôle CRC de tous les membres du ZIP réussi. Empreintes de
  l'exécutable et des sources courantes concordantes avec le manifeste.

Les nouveaux tests vérifient notamment le refus d'écriture d'un autre client,
la lecture seule pendant la maintenance, le blocage durable après une bascule
interrompue, le refus d'une reprise tant qu'une maintenance est active, la
vérification de l'identifiant de reprise, le maintien des sessions valides sans
accepter une révision révoquée et l'absence de commit implicite d'une transaction
préexistante lors de l'initialisation du contrôle.

Un essai de bout en bout crée une archive ZIP, enregistre ensuite un événement
et de nouvelles données, puis restaure cette archive. Les données reviennent à
l'état sauvegardé ; l'événement postérieur et la trace de restauration restent
dans le journal courant. Les tests couvrent aussi la conservation des files de
miroirs d'audit, l'instantané d'un diplôme retiré et l'identité capturée avant
le traitement d'une consultation en arrière-plan.

## Livraison

- Archive : `dist/releases/20261102T175926997827Z/SME_Gendarmerie_portable.zip`.
- Taille : **118 654 499 octets**.
- SHA-256 : `0e8b8e3a2fe824d1c2d3741f50ca4fa79b86a42fb786bc5cbb37b5bf6069d4fc`.
- Sources Python : `ae5c08aa734db2244f6b4309172bdac940370c771d2c905a8a8154f5c2721176`.

La construction n'a pas remplacé les anciennes livraisons. Elle contient les
notes de version, la procédure de recette et le kit réseau. Les gabarits Access
sont vidés, compactés et vérifiés ; les photos des agents ne sont pas embarquées.

## Preuves et limites

Les résultats consolidés sont dans `build/maintenance_final_20261102/validation.json`.
La campagne unitaire est dans `build/isolated_tests_0564cce1b5224292a2aa3cebb6122421/`.
Les campagnes MySQL sont dans `build/release_checks_d20d5bd6e7374c7c8da6a9e46c87896a/`
et `build/release_checks_b0d7cf9664eb42aab85d1061d62d4c4b/`.

Les profils et bases sont jetables, les ports distincts de 3306 et les datadirs
vérifiés avant les opérations. Les serveurs d'essai sont arrêtés par une commande
MySQL SHUTDOWN vérifiée, y compris leurs processus de surveillance Windows.
Aucune modification de la base de production n'a été exécutée.

Les 59 cas natifs constituent une sélection ciblée, pas toute la suite historique
de restauration. La recette avec deux postes physiques, le vrai partage et les
comptes Windows de l'exploitation reste à effectuer. **Déployer la version 0.1.2
sur tous les postes avant de s'appuyer sur le contrôle de maintenance.**
